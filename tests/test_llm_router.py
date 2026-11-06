import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from aftermath_intelligence.llm import LLMBudget, LLMError, LLMErrorKind, LLMRequest, LLMSettings
from aftermath_intelligence.llm.adapters import USER_AGENT
from tests.llm_fakes import Recorder, gemini_reply, gemini_stream, groq_reply, groq_stream, router


class Verdict(BaseModel):
    category: str
    why: str


REQ = LLMRequest.of("system text", "classify this", max_output_tokens=200)


def run(coro):
    return asyncio.run(coro)


def test_groq_request_has_user_agent_json_mode_and_reasoning_limits():
    rec = Recorder(groq=lambda r: groq_reply('{"category":"db","why":"pool"}'))
    obj, result = run(router(rec).complete_structured(REQ, Verdict))
    assert obj.category == "db" and result.provider == "groq"
    sent = rec.requests[0][1]
    body = json.loads(sent.content)
    # Groq's edge rejects default library user agents (the original 403 bug).
    assert sent.headers["user-agent"] == USER_AGENT
    assert sent.headers["authorization"].startswith("Bearer ")
    assert body["response_format"] == {"type": "json_object"}
    assert body["reasoning_effort"] == "low" and body["include_reasoning"] is False
    assert body["messages"][0] == {"role": "system", "content": "system text"}


def test_gemini_request_shape_uses_system_instruction_and_json_mime():
    rec = Recorder(gemini=lambda r: gemini_reply('{"category":"db","why":"x"}'))
    obj, result = run(router(rec, order="gemini").complete_structured(REQ, Verdict))
    body = json.loads(rec.requests[0][1].content)
    assert result.provider == "gemini"
    assert body["systemInstruction"]["parts"][0]["text"] == "system text"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert rec.requests[0][1].headers["x-goog-api-key"].startswith("AIza")


def test_rate_limit_fails_over_to_gemini_and_records_fallback():
    rec = Recorder(groq=lambda r: httpx.Response(429, headers={"retry-after": "30"},
                                                 json={"error": {"message": "TPM limit"}}),
                   gemini=lambda r: gemini_reply("hello"))
    result = run(router(rec).complete(REQ))
    assert result.provider == "gemini" and result.fallback_from == ("groq",)
    # Retry-After of 30s exceeds the short-wait window: fail over immediately, no retry.
    assert rec.providers == ["groq", "gemini"]


def test_transient_5xx_is_retried_with_bounded_attempts():
    calls = {"n": 0}

    def flaky(_):
        calls["n"] += 1
        return httpx.Response(503, json={"error": {"message": "busy"}}) if calls["n"] < 2 else groq_reply("ok")

    result = run(router(Recorder(groq=flaky)).complete(REQ))
    assert result.text == "ok" and result.attempts == 2


def test_auth_errors_are_not_retried_and_open_the_circuit():
    rec = Recorder(groq=lambda r: httpx.Response(401, json={"error": {"message": "invalid key gsk_test_key_000000000000000000000"}}),
                   gemini=lambda r: gemini_reply("fallback"))
    r = router(rec)
    assert run(r.complete(REQ, use_cache=False)).provider == "gemini"
    assert rec.providers == ["groq", "gemini"]
    run(r.complete(LLMRequest.of("", "second"), use_cache=False))
    assert rec.providers == ["groq", "gemini", "gemini"], "groq should be skipped while its circuit is open"


def test_all_providers_failing_raises_classified_error_without_leaking_keys():
    rec = Recorder(groq=lambda r: httpx.Response(401, json={"error": {"message": "bad gsk_test_key_000000000000000000000"}}),
                   gemini=lambda r: httpx.Response(500, json={"error": {"message": "down"}}))
    with pytest.raises(LLMError) as info:
        run(router(rec, AFTERMATH_LLM_MAX_RETRIES="0").complete(REQ))
    # The most actionable failure (a bad key) is reported, and every per-provider failure is kept.
    assert info.value.kind == LLMErrorKind.AUTH
    assert {f.kind for f in info.value.failures} == {LLMErrorKind.AUTH, LLMErrorKind.UNAVAILABLE}
    assert "gsk_test_key" not in str(info.value)


def test_invalid_structured_output_gets_one_repair_turn_then_falls_over():
    replies = iter(['not json', '{"category": 3}'])
    rec = Recorder(groq=lambda r: groq_reply(next(replies)), gemini=lambda r: gemini_reply('{"category":"db","why":"ok"}'))
    obj, result = run(router(rec).complete_structured(REQ, Verdict, repair_attempts=1))
    assert obj.why == "ok" and result.provider == "gemini"
    repair_body = json.loads(rec.requests[1][1].content)
    assert "not valid for the required JSON schema" in repair_body["messages"][-1]["content"]


def test_structured_output_tolerates_code_fences_and_prose():
    rec = Recorder(groq=lambda r: groq_reply('Sure!\n```json\n{"category":"net","why":"reset"}\n```'))
    obj, _ = run(router(rec).complete_structured(REQ, Verdict))
    assert obj.category == "net"


def test_truncated_empty_output_is_classified():
    rec = Recorder(groq=lambda r: httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}))
    with pytest.raises(LLMError) as info:
        run(router(rec, order="groq").complete(REQ))
    assert info.value.kind == LLMErrorKind.INVALID_OUTPUT


def test_streaming_both_providers_and_fallback_before_first_token():
    rec = Recorder(groq=lambda r: groq_stream(["Hel", "lo"]))
    chunks = run(_collect(router(rec).stream(REQ)))
    assert "".join(chunks) == "Hello"
    rec = Recorder(groq=lambda r: httpx.Response(503, json={}), gemini=lambda r: gemini_stream(["from ", "gemini"]))
    chunks = run(_collect(router(rec, AFTERMATH_LLM_MAX_RETRIES="0").stream(REQ)))
    assert "".join(chunks) == "from gemini"
    assert "streamGenerateContent" in str(rec.requests[-1][1].url)


def test_budget_caps_llm_calls():
    rec = Recorder(groq=lambda r: groq_reply("x"))
    budget = LLMBudget(max_calls=1)
    r = router(rec)
    run(r.complete(REQ, budget=budget, use_cache=False))
    with pytest.raises(LLMError) as info:
        run(r.complete(LLMRequest.of("", "again"), budget=budget, use_cache=False))
    assert info.value.kind == LLMErrorKind.BUDGET_EXHAUSTED


def test_cache_returns_marked_hit_without_a_second_request():
    rec = Recorder(groq=lambda r: groq_reply("cached answer"))
    r = router(rec, AFTERMATH_LLM_CACHE="1")
    run(r.complete(REQ))
    hit = run(r.complete(REQ))
    assert hit.cached and len(rec.requests) == 1


def test_settings_parse_provider_chain_and_reject_unknown():
    s = LLMSettings.from_env({"AI_PROVIDER": "groq", "AI_FALLBACK_PROVIDER": "gemini, ollama", "GROQ_API_KEY": "k"})
    assert s.order == ("groq", "gemini", "ollama")
    assert s.provider("ollama").configured  # local Ollama needs no key
    assert not s.provider("gemini").configured
    with pytest.raises(ValueError):
        LLMSettings.from_env({"AI_PROVIDER": "openai"})


def test_unconfigured_providers_report_not_configured():
    r = router(Recorder(), order="groq", GROQ_API_KEY="")
    assert not r.available
    with pytest.raises(LLMError) as info:
        run(r.complete(REQ))
    assert info.value.kind == LLMErrorKind.NOT_CONFIGURED


async def _collect(gen):
    return [chunk async for chunk in gen]
