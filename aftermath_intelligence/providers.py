"""Small provider adapter with per-provider fallback and prompt-hash caching.

Uses the providers' HTTP APIs directly so this layer can be installed before the
team chooses SDK versions. Credentials are read only from the process environment.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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

    def complete(self, prompt: str, *, system: str = "", max_output_tokens: int = 1200,
                 temperature: float = 0.2, use_cache: bool | None = None) -> LLMResponse:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        prompt = redact_sensitive_text(prompt)
        system = redact_sensitive_text(system)
        cache_enabled = (self.environ.get("AFTERMATH_LLM_CACHE", "1") != "0") if use_cache is None else use_cache
        providers = self._provider_order()
        models = {"groq": self.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
                  "gemini": self.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")}
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
            api_key = self.environ.get("GROQ_API_KEY" if provider == "groq" else "GEMINI_API_KEY", "")
            if not api_key:
                failures.append(f"{provider}: key is not configured")
                continue
            model = self.environ.get("GROQ_MODEL" if provider == "groq" else "GEMINI_MODEL")
            model = model or ("openai/gpt-oss-20b" if provider == "groq" else "gemini-3.5-flash-lite")
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
        response = self.complete(prompt, system=system, **kwargs)
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
        primary = self.environ.get("AI_PROVIDER", "gemini").strip().lower()
        fallback = self.environ.get("AI_FALLBACK_PROVIDER", "").strip().lower()
        allowed = {"groq", "gemini"}
        if primary not in allowed:
            raise ValueError("AI_PROVIDER must be 'groq' or 'gemini'")
        if fallback and fallback not in allowed:
            raise ValueError("AI_FALLBACK_PROVIDER must be 'groq', 'gemini', or empty")
        return (primary,) if not fallback or fallback == primary else (primary, fallback)

    def _request(self, provider: str, model: str, api_key: str, prompt: str, system: str,
                 max_output_tokens: int, temperature: float) -> str:
        if provider == "groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            payload: dict[str, Any] = {
                "model": model,
                "messages": ([{"role": "system", "content": system}] if system else []) +
                            [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_output_tokens,
            }
            headers = {"Authorization": f"Bearer {api_key}"}
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            content = (f"{system}\n\n" if system else "") + prompt
            payload = {"contents": [{"role": "user", "parts": [{"text": content}]}],
                       "generationConfig": {"temperature": temperature, "maxOutputTokens": max_output_tokens}}
            headers = {"x-goog-api-key": api_key}
        request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers})
        for attempt in range(2):
            try:
                with urlopen(request, timeout=self.timeout) as result:
                    data = json.loads(result.read())
                break
            except HTTPError as exc:
                # Honor only short retry windows; a long free-tier cooldown should
                # fail over immediately instead of tying up an investigation.
                retry_after = exc.headers.get("Retry-After", "")
                transient_server_error = exc.code in {500, 502, 503, 504}
                delay = _short_retry_delay(retry_after) if retry_after else None
                should_retry = delay is not None if exc.code == 429 else (
                    transient_server_error and (not retry_after or delay is not None)
                )
                if attempt == 0 and should_retry:
                    time.sleep(delay if delay is not None else 1.0)
                    continue
                detail = f"HTTP {exc.code}"
                if retry_after:
                    detail += f" (retry after {retry_after}s)"
                try:
                    body = json.loads(exc.read().decode("utf-8", errors="replace"))
                    message = body.get("error", {}).get("message") if isinstance(body, dict) else None
                    if isinstance(message, str):
                        detail += ": " + message.replace(api_key, "[redacted]")[:240]
                except (ValueError, OSError):
                    pass
                raise ProviderError(detail) from None
            except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                raise ProviderError(type(exc).__name__) from None
        try:
            if provider == "groq":
                return str(data["choices"][0]["message"]["content"])
            return "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError) as exc:
            # Provider error bodies may include user request context; omit them.
            raise ProviderError(f"malformed response ({type(exc).__name__})") from None


def _with_local_env(process_env: dict[str, str]) -> dict[str, str]:
    """Read simple KEY=value lines from the project .env without shell evaluation."""
    values: dict[str, str] = {}
    env_path = Path(".env")
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    values.update(process_env)
    return values


def _short_retry_delay(value: str) -> float | None:
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return None
    return delay if 0 <= delay <= 2 else None


def _decode_json(response: LLMResponse) -> dict[str, Any]:
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"{response.provider} returned invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ProviderError(f"{response.provider} JSON response must be an object")
    return value
