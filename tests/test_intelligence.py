from datetime import timezone

from aftermath_intelligence.analysis import analyze_events
from aftermath_intelligence.ingestion import ingest_text
from aftermath_intelligence.models import Severity
from aftermath_intelligence.providers import LLMResponse, ProviderRouter, _short_retry_delay


def test_ingests_jsonl_and_text_with_utc_and_provenance():
    result = ingest_text(
        '{"timestamp":"2025-01-02T03:04:05+05:30","level":"ERROR","message":"db timeout","service":"api"}\n'
        '2025-01-02T00:00:00Z WARN service=worker retries exhausted\n'
        '{bad json',
        incident_id="inc-1", source="app.log",
    )
    assert len(result.events) == 3
    assert result.events[0].timestamp.tzinfo == timezone.utc
    assert result.events[0].timestamp.hour == 21
    assert result.events[0].service == "api"
    assert result.events[1].severity == Severity.WARNING
    assert result.malformed_lines == (3,)
    assert result.events[0].raw.startswith("{")


def test_deduplicates_exact_events_and_groups_errors():
    result = ingest_text("ERROR DatabaseError code=503\nERROR DatabaseError code=503\n")
    report = analyze_events(result.events)
    assert result.duplicate_count == 1
    assert report.event_count == 1
    assert report.error_groups[0].count == 1
    assert report.error_groups[0].exception_type == "DatabaseError"


def test_provider_fallback_and_cache(tmp_path, monkeypatch):
    environ = {
        "AI_PROVIDER": "groq", "AI_FALLBACK_PROVIDER": "gemini",
        "GROQ_API_KEY": "test-groq", "GEMINI_API_KEY": "test-gemini",
        "AFTERMATH_LLM_CACHE": "1",
    }
    router = ProviderRouter(environ=environ, cache_dir=tmp_path)
    calls = []

    def fake_request(provider, model, api_key, prompt, system, max_output_tokens, temperature):
        calls.append(provider)
        if provider == "groq":
            from aftermath_intelligence.providers import ProviderError
            raise ProviderError("HTTP 429")
        return '{"finding":"database timeout"}'

    monkeypatch.setattr(router, "_request", fake_request)
    payload, response = router.complete_json("investigate", use_cache=True)
    assert payload == {"finding": "database timeout"}
    assert response.provider == "gemini"
    cached = router.complete("investigate", use_cache=True)
    assert cached.cached
    assert calls == ["groq", "gemini"]


def test_provider_order_rejects_invalid_provider():
    router = ProviderRouter(environ={"AI_PROVIDER": "unknown"})
    try:
        router.complete("hello", use_cache=False)
    except ValueError as exc:
        assert "AI_PROVIDER" in str(exc)
    else:
        raise AssertionError("expected invalid provider to be rejected")


def test_provider_retry_is_bounded_to_short_windows():
    assert _short_retry_delay("1.5") == 1.5
    assert _short_retry_delay("60") is None
    assert _short_retry_delay("not-a-number") is None


def test_analysis_detects_cooccurrence_bursts_spikes_and_repeated_sequences():
    lines = []
    for minute, count, message in ((0, 1, "ERROR DatabaseError pool exhausted"),
                                   (1, 1, "ERROR TimeoutError upstream timeout"),
                                   (5, 1, "ERROR DatabaseError pool exhausted"),
                                   (10, 10, "ERROR DatabaseError pool exhausted")):
        for second in range(count):
            lines.append(f"2026-11-05T09:{minute:02d}:{second:02d}Z service=checkout {message}")
    events = ingest_text("\n".join(lines), incident_id="analysis", source="events.log").events
    report = analyze_events(events)
    assert report.temporal_spikes
    assert report.error_bursts
    assert report.error_cooccurrences
    assert report.repeated_failure_sequences


def test_invalid_structured_output_uses_the_other_configured_provider(tmp_path, monkeypatch):
    router = ProviderRouter(environ={
        "AI_PROVIDER": "groq", "AI_FALLBACK_PROVIDER": "gemini",
        "GROQ_API_KEY": "g", "GEMINI_API_KEY": "m", "AFTERMATH_LLM_CACHE": "0",
    }, cache_dir=tmp_path)
    calls = []

    def fake_request(self, provider, model, api_key, prompt, system, max_output_tokens, temperature):
        calls.append(provider)
        return "not json" if provider == "groq" else '{"ok":true}'

    monkeypatch.setattr(ProviderRouter, "_request", fake_request)
    payload, response = router.complete_json("test")
    assert payload == {"ok": True}
    assert response.provider == "gemini"
    assert calls == ["groq", "gemini"]
