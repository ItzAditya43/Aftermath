"""Synchronous provider router kept for the offline engine and existing callers.

Shares wire formats with the async `llm` package (headers, JSON mode, reasoning limits);
the live backend agent uses `llm.LLMRouter` instead. Credentials come only from the environment.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .llm.adapters import ADAPTERS, classify_http_error
from .llm.config import DEFAULT_MODELS, ProviderConfig, read_dotenv
from .llm.structured import extract_json_object
from .llm.types import LLMError, LLMRequest
from .redaction import redact_sensitive_text


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    cached: bool = False


class ProviderRouter:
    """Route one request to a primary and fallback provider without hidden retries."""

    def __init__(self, *, environ: dict[str, str] | None = None, cache_dir: Path | None = None):
        self.environ = dict(environ) if environ is not None else _with_local_env(os.environ)
        self.cache_dir = cache_dir or Path(os.getenv("AFTERMATH_CACHE_DIR", ".cache/aftermath-llm"))
        self.timeout = float(self.environ.get("AFTERMATH_LLM_TIMEOUT_SECONDS", "30"))
        self._json_mode = False

    def complete(self, prompt: str, *, system: str = "", max_output_tokens: int = 1200,
                 temperature: float = 0.2, use_cache: bool | None = None) -> LLMResponse:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        prompt = redact_sensitive_text(prompt)
        system = redact_sensitive_text(system)
        cache_enabled = (self.environ.get("AFTERMATH_LLM_CACHE", "1") != "0") if use_cache is None else use_cache
        providers = self._provider_order()
        models = {name: self.environ.get(f"{name.upper()}_MODEL") or DEFAULT_MODELS[name] for name in providers}
        key = hashlib.sha256(json.dumps([system, prompt, max_output_tokens, temperature, providers, models],
                                       ensure_ascii=False).encode()).hexdigest()
        cache_path = self.cache_dir / f"{key}.json"
        if cache_enabled and cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                return LLMResponse(**cached, cached=True)
            except (OSError, ValueError, TypeError):
                cache_path.unlink(missing_ok=True)

        failures: list[str] = []
        for provider in providers:
            api_key = self.environ.get(f"{provider.upper()}_API_KEY", "")
            if not api_key and provider != "ollama":
                failures.append(f"{provider}: key is not configured")
                continue
            model = models[provider]
            try:
                response_text = self._request(provider, model, api_key, prompt, system,
                                              max_output_tokens, temperature)
                response = LLMResponse(response_text, provider, model)
                if cache_enabled:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps({"text": response.text, "provider": provider,
                                                      "model": model}, ensure_ascii=False), encoding="utf-8")
                return response
            except ProviderError as exc:
                failures.append(f"{provider}: {exc}")
        raise ProviderError("All configured providers failed: " + "; ".join(failures))

    def complete_json(self, prompt: str, *, system: str = "", **kwargs: Any) -> tuple[dict[str, Any], LLMResponse]:
        self._json_mode = True
        try:
            response = self.complete(prompt, system=system, **kwargs)
        finally:
            self._json_mode = False
        try:
            return _decode_json(response), response
        except ProviderError as first_error:
            order = self._provider_order()
            remaining = [name for name in order if name != response.provider]
            for provider in remaining:
                fallback_env = dict(self.environ)
                fallback_env["AI_PROVIDER"] = provider
                fallback_env["AI_FALLBACK_PROVIDER"] = ""
                fallback = ProviderRouter(environ=fallback_env, cache_dir=self.cache_dir)
                try:
                    fallback_kwargs = dict(kwargs)
                    fallback_kwargs["use_cache"] = False
                    alternate = fallback.complete(prompt, system=system, **fallback_kwargs)
                    return _decode_json(alternate), alternate
                except ProviderError:
                    continue
            raise first_error

    def _provider_order(self) -> tuple[str, ...]:
        primary = (self.environ.get("AI_PROVIDER") or "groq").strip().lower()
        fallbacks = [p.strip().lower() for p in self.environ.get("AI_FALLBACK_PROVIDER", "").split(",") if p.strip()]
        allowed = {"groq", "gemini", "ollama"}
        if primary not in allowed:
            raise ValueError("AI_PROVIDER must be 'groq', 'gemini' or 'ollama'")
        if any(p not in allowed for p in fallbacks):
            raise ValueError("AI_FALLBACK_PROVIDER must list 'groq', 'gemini' or 'ollama', or be empty")
        return tuple(dict.fromkeys([primary, *fallbacks]))

    def _request(self, provider: str, model: str, api_key: str, prompt: str, system: str,
                 max_output_tokens: int, temperature: float) -> str:
        config = ProviderConfig(name=provider, model=model, api_key=api_key,
                                base_url=self.environ.get(f"{provider.upper()}_BASE_URL") or None)
        request = LLMRequest.of(system, prompt, max_output_tokens=max_output_tokens, temperature=temperature,
                                json_mode=self._json_mode)
        adapter = ADAPTERS[provider]
        call = adapter.build(config, request)
        for attempt in range(2):
            try:
                response = httpx.post(call.url, headers=call.headers, json=call.body, timeout=self.timeout)
            except httpx.HTTPError as exc:
                raise ProviderError(type(exc).__name__) from None
            if response.status_code < 400:
                try:
                    return adapter.parse(response.json())[0]
                except (LLMError, ValueError) as exc:
                    raise ProviderError(f"malformed response ({exc})") from None
            # Honor only short retry windows; a long free-tier cooldown should
            # fail over immediately instead of tying up an investigation.
            retry_after = response.headers.get("retry-after", "")
            delay = _short_retry_delay(retry_after) if retry_after else None
            transient = response.status_code in {500, 502, 503, 504}
            should_retry = delay is not None if response.status_code == 429 else (
                transient and (not retry_after or delay is not None))
            if attempt == 0 and should_retry:
                time.sleep(delay if delay is not None else 1.0)
                continue
            raise ProviderError(str(classify_http_error(response.status_code, response.text, retry_after,
                                                        secret=api_key)))
        raise ProviderError("request failed")


def _with_local_env(process_env: dict[str, str]) -> dict[str, str]:
    """Overlay the process environment on the project .env (no shell evaluation)."""
    values = read_dotenv(Path(".env"))
    values.update(process_env)
    return values


def _short_retry_delay(value: str) -> float | None:
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return None
    return delay if 0 <= delay <= 2 else None


def _decode_json(response: LLMResponse) -> dict[str, Any]:
    try:
        return extract_json_object(response.text)
    except ValueError as exc:
        raise ProviderError(f"{response.provider} returned invalid JSON: {exc}") from exc
