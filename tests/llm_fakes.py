"""Deterministic fake LLM providers for tests. No network, no credits."""
from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from aftermath_intelligence.llm import LLMRouter, LLMSettings

Handler = Callable[[httpx.Request], httpx.Response]


def settings(order: str = "groq,gemini", **extra: str) -> LLMSettings:
    primary, *fallbacks = order.split(",")
    env = {"AI_PROVIDER": primary, "AI_FALLBACK_PROVIDER": ",".join(fallbacks),
           "GROQ_API_KEY": "gsk_test_key_000000000000000000000", "GEMINI_API_KEY": "AIza-test-key-0000000000000000000",
           "AFTERMATH_LLM_CACHE": "0", **extra}
    return LLMSettings.from_env(env)


def groq_reply(content: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


def gemini_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": content}]}, "finishReason": "STOP"}],
                                     "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})


def groq_stream(chunks: list[str]) -> httpx.Response:
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}" for c in chunks] + ["data: [DONE]"]
    return httpx.Response(200, content="\n\n".join(lines).encode(), headers={"content-type": "text/event-stream"})


def gemini_stream(chunks: list[str]) -> httpx.Response:
    lines = [f"data: {json.dumps({'candidates': [{'content': {'parts': [{'text': c}]}}]})}" for c in chunks]
    return httpx.Response(200, content="\n\n".join(lines).encode(), headers={"content-type": "text/event-stream"})


class Recorder:
    """Route requests to per-provider handlers and remember what was sent."""

    def __init__(self, groq: Handler | None = None, gemini: Handler | None = None, ollama: Handler | None = None):
        self.handlers = {"groq": groq, "gemini": gemini, "ollama": ollama}
        self.requests: list[tuple[str, httpx.Request]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        provider = "groq" if "groq" in host else "gemini" if "googleapis" in host else "ollama"
        self.requests.append((provider, request))
        handler = self.handlers[provider]
        if handler is None:
            return httpx.Response(500, json={"error": {"message": f"no fake for {provider}"}})
        return handler(request)

    @property
    def providers(self) -> list[str]:
        return [p for p, _ in self.requests]


async def _no_sleep(_: float) -> None:
    return None


def router(recorder: Recorder, order: str = "groq,gemini", **extra: str) -> LLMRouter:
    return LLMRouter(settings(order, **extra), transport=httpx.MockTransport(recorder), sleep=_no_sleep)
