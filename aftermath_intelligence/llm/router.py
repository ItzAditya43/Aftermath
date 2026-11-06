"""Async provider router: bounded retries, provider fallback, circuit breaking, caching.

The investigation engine talks only to `LLMRouter`; no provider SDK or URL leaks past
this module. Every failure is classified (see `LLMErrorKind`) so the caller can decide
whether to degrade to deterministic output.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..redaction import redact_sensitive_text
from .adapters import ADAPTERS, classify_http_error
from .config import LLMSettings, ProviderConfig
from .structured import extract_json_object
from .types import ChatMessage, LLMError, LLMErrorKind, LLMRequest, LLMResult

logger = logging.getLogger("aftermath.llm")
T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMBudget:
    """Caps LLM calls for one investigation so a loop can never run away with quota."""

    max_calls: int = 6
    used: int = 0

    def take(self) -> None:
        if self.used >= self.max_calls:
            raise LLMError(LLMErrorKind.BUDGET_EXHAUSTED, f"LLM call budget of {self.max_calls} used")
        self.used += 1


@dataclass
class ProviderStats:
    calls: int = 0
    successes: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    total_latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        avg = round(self.total_latency_ms / self.successes) if self.successes else None
        return {"calls": self.calls, "successes": self.successes, "failures": dict(self.failures),
                "avg_latency_ms": avg, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass
class _Circuit:
    consecutive_failures: int = 0
    open_until: float = 0.0


class LLMRouter:
    def __init__(self, settings: LLMSettings, *, transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep, clock: Callable[[], float] = time.monotonic,
                 memory_cache_size: int = 256):
        self.settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._sleep = sleep
        self._clock = clock
        self._circuits: dict[str, _Circuit] = {p.name: _Circuit() for p in settings.providers}
        self._cache: OrderedDict[str, LLMResult] = OrderedDict()
        self._cache_size = memory_cache_size
        self.stats: dict[str, ProviderStats] = {p.name: ProviderStats() for p in settings.providers}

    # ------------------------------------------------------------------ lifecycle

    @property
    def available(self) -> bool:
        return any(p.configured for p in self.settings.providers)

    def describe(self) -> dict[str, Any]:
        """Safe-to-log provider summary (no keys)."""
        return {"order": list(self.settings.order),
                "models": {p.name: p.model for p in self.settings.providers},
                "configured": {p.name: p.configured for p in self.settings.providers}}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(transport=self._transport,
                                             timeout=httpx.Timeout(self.settings.timeout_seconds, connect=10.0))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    # ------------------------------------------------------------------ public API

    async def complete(self, request: LLMRequest, *, budget: LLMBudget | None = None,
                       use_cache: bool = True) -> LLMResult:
        key = self._cache_key(request)
        if use_cache and self.settings.cache_enabled:
            hit = self._cache_get(key)
            if hit:
                return hit
        if budget:
            budget.take()
        result = await self._with_fallback(lambda cfg: self._complete_once(cfg, request))
        if use_cache and self.settings.cache_enabled:
            self._cache_put(key, result)
        return result

    async def stream(self, request: LLMRequest, *, budget: LLMBudget | None = None) -> AsyncIterator[str]:
        """Yield text deltas. Falls over to the next provider only before the first token."""
        if budget:
            budget.take()
        failures: list[LLMError] = []
        for config in self._candidates(failures):
            emitted = False
            started = self._clock()
            stats = self.stats[config.name]
            stats.calls += 1
            try:
                async for delta in self._stream_once(config, request):
                    emitted = True
                    yield delta
                self._record_success(config.name, started)
                return
            except LLMError as exc:
                exc.provider = config.name
                self._record_failure(config.name, exc)
                if emitted:
                    raise
                failures.append(exc)
        raise _all_failed(failures)

    async def complete_structured(self, request: LLMRequest, schema: type[T], *,
                                  budget: LLMBudget | None = None,
                                  validate: Callable[[T], T] | None = None,
                                  repair_attempts: int = 1) -> tuple[T, LLMResult]:
        """JSON completion validated against `schema`, with a bounded repair turn per provider."""
        request = LLMRequest(request.messages, task=request.task, json_mode=True,
                             max_output_tokens=request.max_output_tokens, temperature=request.temperature)

        async def attempt(config: ProviderConfig) -> tuple[T, LLMResult]:
            current = request
            last_error = ""
            for turn in range(repair_attempts + 1):
                if budget:
                    budget.take()
                result = await self._complete_once(config, current)
                try:
                    parsed = schema.model_validate(extract_json_object(result.text))
                    return (validate(parsed) if validate else parsed), result
                except (ValueError, ValidationError) as exc:
                    last_error = _short_validation_error(exc)
                    logger.info("structured output from %s failed validation (turn %d): %s",
                                config.name, turn, last_error)
                    current = LLMRequest(
                        request.messages + (ChatMessage("assistant", result.text[:4000]),
                                            ChatMessage("user", "Your previous reply was not valid for the required "
                                                        f"JSON schema: {last_error}. Reply again with only the "
                                                        "corrected JSON object.")),
                        task=request.task, json_mode=True, max_output_tokens=request.max_output_tokens,
                        temperature=0.0)
            raise LLMError(LLMErrorKind.INVALID_OUTPUT, last_error or "invalid structured output")

        return await self._with_fallback(attempt)

    # ------------------------------------------------------------------ fallback + retries

    def _candidates(self, failures: list[LLMError]) -> list[ProviderConfig]:
        now = self._clock()
        usable = []
        for config in self.settings.providers:
            if not config.configured:
                failures.append(LLMError(LLMErrorKind.NOT_CONFIGURED, "API key not set", provider=config.name))
            elif self._circuits[config.name].open_until > now:
                failures.append(LLMError(LLMErrorKind.CIRCUIT_OPEN, "provider temporarily disabled after "
                                         "repeated failures", provider=config.name))
            else:
                usable.append(config)
        return usable

    async def _with_fallback(self, call: Callable[[ProviderConfig], Any]) -> Any:
        failures: list[LLMError] = []
        tried: list[str] = []
        for config in self._candidates(failures):
            started = self._clock()
            try:
                outcome = await call(config)
            except LLMError as exc:
                exc.provider = exc.provider or config.name
                self._record_failure(config.name, exc)
                failures.append(exc)
                tried.append(config.name)
                if exc.kind == LLMErrorKind.BUDGET_EXHAUSTED:
                    raise
                continue
            self._record_success(config.name, started)
            if tried:
                outcome = _with_fallback_marker(outcome, tuple(tried))
            return outcome
        raise _all_failed(failures)

    async def _complete_once(self, config: ProviderConfig, request: LLMRequest) -> LLMResult:
        adapter = ADAPTERS[config.name]
        call = adapter.build(config, request)
        stats = self.stats[config.name]
        attempts = 0
        while True:
            attempts += 1
            stats.calls += 1
            started = self._clock()
            try:
                response = await self._http().post(call.url, headers=call.headers, json=call.body)
            except httpx.TimeoutException:
                error = LLMError(LLMErrorKind.TIMEOUT, "request timed out")
            except httpx.HTTPError as exc:
                error = LLMError(LLMErrorKind.UNAVAILABLE, type(exc).__name__)
            else:
                if response.status_code < 400:
                    try:
                        text, usage = adapter.parse(response.json())
                    except ValueError:
                        raise LLMError(LLMErrorKind.MALFORMED, "response was not JSON") from None
                    stats.input_tokens += usage.input_tokens or 0
                    stats.output_tokens += usage.output_tokens or 0
                    return LLMResult(text=text, provider=config.name, model=config.model, usage=usage,
                                     latency_ms=int((self._clock() - started) * 1000), attempts=attempts)
                error = classify_http_error(response.status_code, response.text,
                                            response.headers.get("retry-after"), secret=config.api_key)
            delay = self._retry_delay(error, attempts)
            if delay is None:
                raise error
            stats.failures[error.kind.value] = stats.failures.get(error.kind.value, 0) + 1
            logger.warning("%s %s; retrying in %.1fs", config.name, error.kind.value, delay)
            await self._sleep(delay)

    async def _stream_once(self, config: ProviderConfig, request: LLMRequest) -> AsyncIterator[str]:
        adapter = ADAPTERS[config.name]
        call = adapter.build(config, request, stream=True)
        try:
            async with self._http().stream("POST", call.url, headers=call.headers, json=call.body) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise classify_http_error(response.status_code, body, response.headers.get("retry-after"),
                                              secret=config.api_key)
                async for line in response.aiter_lines():
                    delta = adapter.parse_stream_line(line)
                    if delta is None:
                        return
                    if delta:
                        yield delta
        except httpx.TimeoutException:
            raise LLMError(LLMErrorKind.TIMEOUT, "stream timed out") from None
        except httpx.HTTPError as exc:
            raise LLMError(LLMErrorKind.UNAVAILABLE, type(exc).__name__) from None

    def _retry_delay(self, error: LLMError, attempts: int) -> float | None:
        if not error.kind.retryable or attempts > self.settings.max_retries:
            return None
        if error.retry_after is not None:
            # A long free-tier cooldown should fail over now instead of stalling the investigation.
            return error.retry_after if error.retry_after <= self.settings.max_retry_delay_seconds else None
        backoff = min(self.settings.max_retry_delay_seconds, 0.5 * (2 ** (attempts - 1)))
        return backoff * (0.75 + random.random() / 2)  # noqa: S311 - retry jitter, not crypto

    # ------------------------------------------------------------------ bookkeeping

    def _record_success(self, provider: str, started: float) -> None:
        stats = self.stats[provider]
        stats.successes += 1
        stats.total_latency_ms += int((self._clock() - started) * 1000)
        self._circuits[provider] = _Circuit()

    def _record_failure(self, provider: str, error: LLMError) -> None:
        stats = self.stats[provider]
        stats.failures[error.kind.value] = stats.failures.get(error.kind.value, 0) + 1
        if error.kind in {LLMErrorKind.INVALID_OUTPUT, LLMErrorKind.BUDGET_EXHAUSTED, LLMErrorKind.BAD_REQUEST}:
            return  # the provider is up; do not trip the breaker
        circuit = self._circuits[provider]
        circuit.consecutive_failures += 1
        if error.kind == LLMErrorKind.RATE_LIMITED and error.retry_after:
            # Skip the provider exactly as long as it asked (capped), instead of a fixed cooldown.
            circuit.open_until = max(circuit.open_until, self._clock() + min(error.retry_after,
                                                                            self.settings.circuit_cooldown_seconds))
        elif error.kind == LLMErrorKind.AUTH or circuit.consecutive_failures >= self.settings.circuit_failure_threshold:
            circuit.open_until = self._clock() + self.settings.circuit_cooldown_seconds
        logger.warning("LLM provider failure: %s", redact_sensitive_text(str(error)))

    def _cache_key(self, request: LLMRequest) -> str:
        material = json.dumps([[m.role, m.content] for m in request.messages] +
                              [request.json_mode, request.max_output_tokens, request.temperature,
                               [(p.name, p.model) for p in self.settings.providers]], ensure_ascii=False)
        return hashlib.sha256(material.encode()).hexdigest()

    def _cache_get(self, key: str) -> LLMResult | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            hit = self._cache[key]
            return LLMResult(**{**hit.__dict__, "cached": True})
        path = self._disk_path(key)
        if path and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                result = LLMResult(text=data["text"], provider=data["provider"], model=data["model"], cached=True)
                self._cache[key] = result
                return result
            except (OSError, ValueError, KeyError):
                path.unlink(missing_ok=True)
        return None

    def _cache_put(self, key: str, result: LLMResult) -> None:
        self._cache[key] = result
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        path = self._disk_path(key)
        if path:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"text": result.text, "provider": result.provider,
                                            "model": result.model}), encoding="utf-8")
            except OSError:
                logger.warning("could not write LLM cache entry")

    def _disk_path(self, key: str) -> Path | None:
        return self.settings.cache_dir / f"{key}.json" if self.settings.cache_dir else None


def _all_failed(failures: list[LLMError]) -> LLMError:
    if not failures:
        return LLMError(LLMErrorKind.NOT_CONFIGURED, "no AI provider configured")
    summary = "; ".join(str(f) for f in failures)
    # Report the most actionable kind: a real outage beats "not configured".
    kind = next((f.kind for f in failures if f.kind != LLMErrorKind.NOT_CONFIGURED), failures[0].kind)
    return LLMError(kind, f"all providers failed: {summary}", failures=tuple(failures))


def _with_fallback_marker(outcome: Any, tried: tuple[str, ...]) -> Any:
    if isinstance(outcome, LLMResult):
        return LLMResult(**{**outcome.__dict__, "fallback_from": tried})
    if isinstance(outcome, tuple) and len(outcome) == 2 and isinstance(outcome[1], LLMResult):
        return outcome[0], LLMResult(**{**outcome[1].__dict__, "fallback_from": tried})
    return outcome


def _short_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
    return str(exc)[:200]
