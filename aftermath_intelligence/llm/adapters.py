"""Wire formats for each provider. Pure functions: build a request, parse a response.

Keeping these free of I/O lets the async router, the sync compatibility router and the
tests share one implementation of every provider's quirks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import ProviderConfig
from .types import LLMError, LLMErrorKind, LLMRequest, Usage

# Groq sits behind Cloudflare, which rejects default library user agents (HTTP 403, error 1010).
USER_AGENT = "aftermath-intelligence/0.2 (+https://github.com/ItzAditya43)"


@dataclass(frozen=True)
class HttpCall:
    url: str
    headers: dict[str, str]
    body: dict[str, Any]


class OpenAICompatibleAdapter:
    """Groq and Ollama both speak the OpenAI chat-completions protocol."""

    default_base = {"groq": "https://api.groq.com/openai/v1", "ollama": "http://localhost:11434/v1"}

    def build(self, config: ProviderConfig, request: LLMRequest, *, stream: bool = False) -> HttpCall:
        base = (config.base_url or self.default_base[config.name]).rstrip("/")
        body: dict[str, Any] = {
            "model": config.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.json_mode:
            body["response_format"] = {"type": "json_object"}
        if stream:
            body["stream"] = True
        if config.name == "groq" and "gpt-oss" in config.model:
            # Reasoning tokens count against max_tokens and the 8k TPM free tier; keep them small and hidden.
            body["reasoning_effort"] = "low"
            body["include_reasoning"] = False
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        return HttpCall(f"{base}/chat/completions", headers, body)

    def parse(self, data: Any) -> tuple[str, Usage]:
        try:
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError(LLMErrorKind.MALFORMED, "response has no choices[0].message") from None
        if not text.strip() and choice.get("finish_reason") == "length":
            raise LLMError(LLMErrorKind.INVALID_OUTPUT, "output truncated before any content (raise max tokens)")
        usage = data.get("usage") or {}
        return text, Usage(usage.get("prompt_tokens"), usage.get("completion_tokens"))

    def parse_stream_line(self, line: str) -> str | None:
        """Return the text delta in one SSE line, '' for non-content lines, None at end of stream."""
        if not line.startswith("data:"):
            return ""
        payload = line[5:].strip()
        if payload == "[DONE]":
            return None
        try:
            delta = json.loads(payload)["choices"][0].get("delta", {})
        except (ValueError, KeyError, IndexError, TypeError):
            return ""
        return delta.get("content") or ""


class GeminiAdapter:
    base = "https://generativelanguage.googleapis.com/v1beta"

    def build(self, config: ProviderConfig, request: LLMRequest, *, stream: bool = False) -> HttpCall:
        base = (config.base_url or self.base).rstrip("/")
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        contents = [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
                    for m in request.messages if m.role != "system"]
        generation: dict[str, Any] = {"temperature": request.temperature,
                                      "maxOutputTokens": request.max_output_tokens}
        if request.json_mode:
            generation["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": contents, "generationConfig": generation}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "x-goog-api-key": config.api_key}
        return HttpCall(f"{base}/models/{config.model}:{method}", headers, body)

    def parse(self, data: Any) -> tuple[str, Usage]:
        try:
            candidate = data["candidates"][0]
        except (KeyError, IndexError, TypeError):
            reason = (data or {}).get("promptFeedback", {}).get("blockReason") if isinstance(data, dict) else None
            raise LLMError(LLMErrorKind.MALFORMED, f"no candidates (block reason: {reason})") from None
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        if not text.strip() and candidate.get("finishReason") == "MAX_TOKENS":
            raise LLMError(LLMErrorKind.INVALID_OUTPUT, "output truncated before any content (raise max tokens)")
        usage = data.get("usageMetadata") or {}
        return text, Usage(usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))

    def parse_stream_line(self, line: str) -> str | None:
        if not line.startswith("data:"):
            return ""
        try:
            data = json.loads(line[5:].strip())
            parts = data["candidates"][0]["content"]["parts"]
        except (ValueError, KeyError, IndexError, TypeError):
            return ""
        return "".join(part.get("text", "") for part in parts if not part.get("thought"))


ADAPTERS: dict[str, OpenAICompatibleAdapter | GeminiAdapter] = {
    "groq": OpenAICompatibleAdapter(),
    "ollama": OpenAICompatibleAdapter(),
    "gemini": GeminiAdapter(),
}


def classify_http_error(status: int, body: str, retry_after: str | None, *, secret: str = "") -> LLMError:
    """Map an HTTP failure to an error kind without leaking keys or request bodies."""
    message = f"HTTP {status}"
    try:
        detail = json.loads(body).get("error", {})
        text = detail.get("message") if isinstance(detail, dict) else None
        if isinstance(text, str):
            message += ": " + (text.replace(secret, "[redacted]") if secret else text)[:240]
    except (ValueError, AttributeError):
        if "1010" in body and status == 403:
            message += ": blocked by edge firewall (user agent)"
    delay: float | None = None
    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            delay = None
    if status == 429:
        kind = LLMErrorKind.RATE_LIMITED
    elif status in (401, 403):
        kind = LLMErrorKind.AUTH
    elif status in (408,):
        kind = LLMErrorKind.TIMEOUT
    elif status >= 500:
        kind = LLMErrorKind.UNAVAILABLE
    else:
        kind = LLMErrorKind.BAD_REQUEST
    return LLMError(kind, message, retry_after=delay)
