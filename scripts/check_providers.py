"""Smoke-test the configured AI providers with your real keys (uses a few tokens).

    python scripts/check_providers.py            # every provider in AI_PROVIDER + AI_FALLBACK_PROVIDER
    python scripts/check_providers.py groq       # just one

Prints status, model, latency and token usage per provider. Never prints keys.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel  # noqa: E402

from aftermath_intelligence.llm import LLMError, LLMRequest, LLMRouter, LLMSettings  # noqa: E402


class Probe(BaseModel):
    category: str
    reason: str


async def check(name: str, base: LLMSettings) -> bool:
    config = base.provider(name)
    if config is None:
        print(f"{name:7} SKIP  not in the provider chain")
        return True
    settings = LLMSettings(providers=(config,), timeout_seconds=base.timeout_seconds, max_retries=1, cache_enabled=False)
    router = LLMRouter(settings)
    if not config.configured:
        print(f"{name:7} SKIP  {name.upper()}_API_KEY not set")
        return True
    try:
        obj, result = await router.complete_structured(
            LLMRequest.of("Reply only with JSON {category, reason}.",
                          "Classify: 'HikariPool-1 - Connection is not available, request timed out after 30000ms'",
                          max_output_tokens=300), Probe)
        chunks = [c async for c in router.stream(LLMRequest.of("", "Say 'ok' and nothing else.", max_output_tokens=300))]
        print(f"{name:7} OK    model={result.model} json={result.latency_ms}ms tokens={result.usage.input_tokens}/"
              f"{result.usage.output_tokens} stream_chunks={len(chunks)} category={obj.category!r}")
        return True
    except LLMError as exc:
        print(f"{name:7} FAIL  model={config.model} {exc.kind.value}: {exc}")
        return False
    finally:
        await router.aclose()


async def main() -> int:
    base = LLMSettings.from_env()
    names = sys.argv[1:] or list(base.order)
    print(f"provider chain: {' -> '.join(base.order)}")
    results = [await check(name, base) for name in names]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
