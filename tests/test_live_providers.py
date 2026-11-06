"""Live provider tests. Skipped unless AFTERMATH_LIVE_TESTS=1 (they spend real tokens)."""
import asyncio
import os

import pytest
from pydantic import BaseModel

from aftermath_intelligence.llm import LLMRequest, LLMRouter, LLMSettings

pytestmark = pytest.mark.skipif(os.getenv("AFTERMATH_LIVE_TESTS") != "1", reason="set AFTERMATH_LIVE_TESTS=1 to run")


class Probe(BaseModel):
    category: str


@pytest.mark.parametrize("provider", ["groq", "gemini"])
def test_live_structured_and_stream(provider):
    base = LLMSettings.from_env(overrides={"AI_PROVIDER": provider, "AI_FALLBACK_PROVIDER": ""})
    if not base.providers[0].configured:
        pytest.skip(f"{provider} key not configured")
    router = LLMRouter(LLMSettings(providers=base.providers, cache_enabled=False))

    async def go():
        obj, result = await router.complete_structured(
            LLMRequest.of("Reply with JSON {category}.", "Classify: connection pool exhausted", max_output_tokens=200), Probe)
        chunks = [c async for c in router.stream(LLMRequest.of("", "Say ok.", max_output_tokens=300))]
        await router.aclose()
        return obj, result, chunks

    obj, result, chunks = asyncio.run(go())
    assert obj.category and result.provider == provider and chunks
