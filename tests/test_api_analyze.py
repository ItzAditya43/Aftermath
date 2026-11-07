from app.agent.interface import MemoryEvent, SuggestionEvent, TokenEvent
from tests.conftest import ALERT


def test_analyze_streams_memory_tokens_suggestion_done(client, seeded, make_incident, analyze):
    incident = make_incident()
    events = analyze(incident["id"])
    names = [name for name, _ in events]

    # Order: memory* -> token+ -> suggestion -> done
    assert names[0] == "memory"
    assert names[-2:] == ["suggestion", "done"]
    first_token = names.index("token")
    assert set(names[:first_token]) == {"memory"}
    assert set(names[first_token:-2]) == {"token"}

    memories = [data for name, data in events if name == "memory"]
    assert 2 <= len(memories) <= 3
    assert all(m["service"] == "payments-api" for m in memories)
    assert {m["past_incident_id"] for m in memories} <= {"INC-1001", "INC-1009", "INC-1017"}
    for m in memories:
        MemoryEvent.model_validate(m)
        assert 0 < m["relevance_score"] <= 1

    text = "".join(data["text"] for name, data in events if name == "token")
    assert "INC-10" in text and "did NOT work" in text

    suggestion = SuggestionEvent.model_validate(events[-2][1])
    assert not suggestion.likely_root_cause.lower().startswith(("likely", "possibly", "repeat"))
    assert suggestion.likely_root_cause == memories[0]["root_cause"].strip().rstrip(".")
    assert suggestion.confidence_calibrated is (len(memories) >= 2)
    assert suggestion.steps and suggestion.warnings
    assert any("restart" in w.lower() for w in suggestion.warnings)
    assert 0.5 < suggestion.confidence <= 0.95
    assert suggestion.based_on == [m["past_incident_id"] for m in memories]

    assert events[-1][1] == {"type": "done", "incident_id": incident["id"], "memories_used": len(memories)}


def test_analyze_persists_memories_suggestion_and_timeline(client, seeded, make_incident, analyze):
    incident = make_incident()
    events = analyze(incident["id"])
    streamed_ids = [d["past_incident_id"] for n, d in events if n == "memory"]

    detail = client.get(f"/incidents/{incident['id']}").json()
    assert detail["status"] == "investigating"
    assert detail["last_suggestion"]["based_on"] == streamed_ids
    analysis = [e["message"] for e in detail["timeline"] if e["type"] == "analysis"]
    assert len(analysis) == 2
    assert "Suggested root cause" in analysis[1]

    memories = client.get(f"/incidents/{incident['id']}/memories").json()
    assert [m["past_incident_id"] for m in memories] == streamed_ids  # sorted by relevance, like the stream
    assert memories[0]["past_incident"]["service"] == "payments-api"
    assert memories[0]["past_incident"]["resolution"]


def test_reanalyze_replaces_memories(client, seeded, make_incident, analyze):
    incident = make_incident()
    analyze(incident["id"])
    analyze(incident["id"])
    memories = client.get(f"/incidents/{incident['id']}/memories").json()
    assert len(memories) == len({m["past_incident_id"] for m in memories})


def test_analyze_without_history_gives_low_confidence(client, make_incident, analyze):
    incident = make_incident(service="search-api", title="search-api: cluster red", alert_text="ES cluster RED", logs="")
    events = analyze(incident["id"])
    assert [n for n, _ in events if n == "memory"] == []
    suggestion = events[-2][1]
    assert suggestion["based_on"] == []
    assert suggestion["confidence"] < 0.5
    assert client.get(f"/incidents/{incident['id']}/memories").json() == []


def test_learning_loop_resolved_incident_is_recalled_next_time(client, make_incident, analyze):
    """No seed data: resolve one incident, then a similar one recalls it."""
    first = make_incident()
    client.post(
        f"/incidents/{first['id']}/resolve",
        json={"root_cause": "Leak in RefundService", "fix_that_worked": "Fixed the leak", "what_failed": ["Restarted pods"]},
    )
    second = make_incident()
    events = analyze(second["id"])
    memory_ids = [d["past_incident_id"] for n, d in events if n == "memory"]
    assert memory_ids == [first["id"]]
    assert "Avoid: Restarted pods" in events[-2][1]["warnings"][0]


def test_analyze_unknown_incident_404_json(client):
    response = client.get("/incidents/INC-0000/analyze")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_analyze_agent_exception_becomes_error_event(client, app, make_incident, analyze):
    async def exploding(incident):
        yield TokenEvent(text="Thinking ")
        raise RuntimeError("groq rate limited")

    app.state.agent.analyze = exploding
    incident = make_incident()
    events = analyze(incident["id"])
    assert [n for n, _ in events] == ["token", "error"]
    assert events[-1][1] == {"type": "error", "code": "agent_error", "message": "groq rate limited"}
    timeline = client.get(f"/incidents/{incident['id']}").json()["timeline"]
    assert "groq rate limited" in timeline[-1]["message"]


def test_interrupted_reanalysis_keeps_previous_analysis(client, app, seeded, make_incident, analyze):
    incident = make_incident()
    analyze(incident["id"])
    before = client.get(f"/incidents/{incident['id']}").json()
    memories_before = client.get(f"/incidents/{incident['id']}/memories").json()
    assert memories_before and before["last_suggestion"]

    async def cut_off(ctx):
        yield {"type": "memory", "past_incident_id": "INC-1002", "title": "t", "service": "api-gateway",
               "summary": "unrelated", "relevance_score": 0.5}
        yield TokenEvent(text="Thinking ")
        raise ConnectionError("stream dropped")

    app.state.agent.analyze = cut_off
    assert [n for n, _ in analyze(incident["id"])] == ["memory", "token", "error"]

    after = client.get(f"/incidents/{incident['id']}").json()
    assert client.get(f"/incidents/{incident['id']}/memories").json() == memories_before
    assert after["last_suggestion"] == before["last_suggestion"]


def test_analyze_accepts_dict_events_and_skips_self_reference(client, app, make_incident, analyze):
    incident = make_incident()

    async def dict_agent(ctx):
        yield {"type": "memory", "past_incident_id": ctx.id, "title": "self", "service": "x", "summary": "s", "relevance_score": 0.9}
        yield {"type": "token", "text": "hello"}
        yield {"type": "suggestion", "likely_root_cause": "rc", "confidence": 0.5}
        yield {"type": "done"}
        yield {"type": "token", "text": "never sent"}

    app.state.agent.analyze = dict_agent
    events = analyze(incident["id"])
    assert [n for n, _ in events] == ["token", "suggestion", "done"]
    assert events[-1][1]["memories_used"] == 0


def test_example_alert_matches_conftest_shape():
    # Guard: the OpenAPI example and the test alert both validate as IncidentCreate.
    from app.schemas import EXAMPLE_ALERT, IncidentCreate

    IncidentCreate.model_validate(EXAMPLE_ALERT)
    IncidentCreate.model_validate(ALERT)
