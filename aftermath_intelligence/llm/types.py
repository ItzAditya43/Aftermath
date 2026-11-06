"""Provider-neutral request/response types and error classification."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class LLMErrorKind(StrEnum):
    NOT_CONFIGURED = "not_configured"
    AUTH = "auth"                    # 401/403: never retried, never cached as success
    BAD_REQUEST = "bad_request"      # 400/404/422: our fault, retrying will not help
    RATE_LIMITED = "rate_limited"    # 429: retry briefly, then fail over
    UNAVAILABLE = "unavailable"      # 5xx / connection errors: retry, then fail over
    TIMEOUT = "timeout"
    MALFORMED = "malformed"          # response body did not have the expected shape
    INVALID_OUTPUT = "invalid_output"  # content failed JSON / schema validation
    CIRCUIT_OPEN = "circuit_open"
    BUDGET_EXHAUSTED = "budget_exhausted"

    @property
    def retryable(self) -> bool:
        return self in {LLMErrorKind.RATE_LIMITED, LLMErrorKind.UNAVAILABLE, LLMErrorKind.TIMEOUT}

    @property
    def fail_over(self) -> bool:
        """Whether another provider might succeed where this one failed."""
        return self not in {LLMErrorKind.BUDGET_EXHAUSTED}


class LLMError(RuntimeError):
    def __init__(self, kind: LLMErrorKind, message: str, *, provider: str | None = None,
                 retry_after: float | None = None, failures: tuple["LLMError", ...] = ()):
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.retry_after = retry_after
        self.failures = failures

    def __str__(self) -> str:
        prefix = f"{self.provider}: " if self.provider else ""
        return f"{prefix}{self.kind.value}: {super().__str__()}"


Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class LLMRequest:
    messages: tuple[ChatMessage, ...]
    task: str = "general"
    json_mode: bool = False
    max_output_tokens: int = 1200
    temperature: float = 0.2

    @classmethod
    def of(cls, system: str, user: str, **kwargs) -> "LLMRequest":
        messages = ((ChatMessage("system", system),) if system else ()) + (ChatMessage("user", user),)
        return cls(messages=messages, **kwargs)


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    cached: bool = False
    attempts: int = 1
    fallback_from: tuple[str, ...] = ()
