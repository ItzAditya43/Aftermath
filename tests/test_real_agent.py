"""RealAgent through the real FastAPI app and SSE stream, with fake providers (no network)."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.real_agent import RealAgent
from app.config import Settings
from app.main import create_app
from tests.conftest import ALERT, parse_sse
from tests.llm_fakes import Recorder, groq_reply, groq_stream, router


@pytest.fixture(autouse=True)
def isolated_agent_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AFTERMATH_MEMORY_PATH", str(tmp_path / "memory.jsonl"))
    monkeypatch.setenv("AFTERMATH_CALIBRATION_PATH", str(tmp_path / "calibration.jsonl"))
    for name in ("AFTERMATH_DOCKER_TOOLS", "GITHUB_REPO", "AFTERMATH_SERVICE_REPOS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def real_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, agent_mode="real", database_url=f"sqlite:///{tmp_path / 'real.db'}",
                    auto_seed=False, mock_token_delay_ms=0)


def _client(settings: Settings, agent: RealAgent):
    app = create_app(settings)
    app.state.agent = agent
    return TestClient(app), app


def _analyze(client, incident_id):
    with client.stream("GET", f"/incidents/{incident_id}/analyze") as response:
        assert response.status_code == 200
        return parse_sse("".join(response.iter_text()))


def test_real_agent_streams_memory_tokens_then_one_suggestion_with_llm(real_settings):
    def groq(request: httpx.Request) -> httpx.Response:
        if b'"stream": true' in request.content or b'"stream":true' in request.content:
            return groq_stream(["The pool is ", "exhausted. ", "Here is the plan I'd follow:"])
        return groq_reply('{"assessments": [], "root_cause_summary": "A connection leak exhausted the Hikari pool."}')

    agent = RealAgent(real_settings, router=router(Recorder(groq=groq), order="groq"))
    client, _ = _client(real_settings, agent)
    with client:
        assert client.post("/admin/seed").json()["ingested"] == 20
        incident = client.post("/incidents", json=ALERT).json()
        events = _analyze(client, incident["id"])
    names = [name for name, _ in events]
    assert names[0] == "memory" and names[-1] == "done"
    assert names.count("suggestion") == 1 and names.index("suggestion") > max(i for i, n in enumerate(names) if n == "token")
    assert "".join(d["text"] for n, d in events if n == "token").startswith("The pool is exhausted.")
    suggestion = next(d for n, d in events if n == "suggestion")
    assert not suggestion["likely_root_cause"].startswith(("Likely", "Possibly"))
    assert "Hikari" in suggestion["likely_root_cause"]
    # 20 backtested seed incidents are below the default 30-sample threshold: a ranking score, not a probability.
    assert suggestion["confidence_calibrated"] is False
    assert set(suggestion["based_on"]) & {"INC-1001", "INC-1009", "INC-1017"}
    assert any("restart" in w.lower() for w in suggestion["warnings"])
    assert 0 < suggestion["confidence"] <= 0.95
    assert agent.reports[incident["id"]].memory_backend == "local_file"


def test_real_agent_works_with_every_provider_down(real_settings):
    dead = Recorder(groq=lambda r: httpx.Response(503, json={}), gemini=lambda r: httpx.Response(503, json={}))
    agent = RealAgent(real_settings, router=router(dead, AFTERMATH_LLM_MAX_RETRIES="0"))
    client, _ = _client(real_settings, agent)
    with client:
        client.post("/admin/seed")
        incident = client.post("/incidents", json=ALERT).json()
        events = _analyze(client, incident["id"])
    text = "".join(d["text"] for n, d in events if n == "token")
    assert "INC-" in text and text.rstrip().endswith("Here is the plan I'd follow:")
    assert [n for n, _ in events].count("suggestion") == 1
    assert not any(n == "error" for n, _ in events)


def test_resolution_is_learned_and_graded_then_recalled(real_settings):
    agent = RealAgent(real_settings, router=router(Recorder(), order="groq", GROQ_API_KEY=""))
    client, _ = _client(real_settings, agent)
    with client:
        incident = client.post("/incidents", json=ALERT).json()
        _analyze(client, incident["id"])
        resolved = client.post(f"/incidents/{incident['id']}/resolve", json={
            "root_cause": "Connection leak in RefundService exhausted the Hikari pool",
            "fix_that_worked": "Hotfixed the leak with try-with-resources",
            "what_failed": ["Rolling restart of payments-api pods (pool exhausted again within 6 minutes)"],
        })
        assert resolved.status_code == 200 and resolved.json()["memory_saved"] is True
        evaluation = agent.evaluations[incident["id"]]
        assert evaluation.memory_record.resolution_status.value == "verified"
        assert agent.calibration.report().samples == 1

        follow_up = client.post("/incidents", json={**ALERT, "title": "payments-api: pool exhausted again"}).json()
        events = _analyze(client, follow_up["id"])
    memories = [d for n, d in events if n == "memory"]
    assert memories and memories[0]["past_incident_id"] == incident["id"]
    assert any("Rolling restart" in f for f in memories[0]["failed_attempts"])


def test_reset_clears_agent_memory(real_settings):
    agent = RealAgent(real_settings, router=router(Recorder(), order="groq", GROQ_API_KEY=""))
    client, _ = _client(real_settings, agent)
    with client:
        client.post("/admin/seed")
        assert len(agent.memory) == 20
        client.post("/admin/reset")
    assert len(agent.memory) == 0 and not agent.reports


def test_seed_backtests_history_into_calibration(real_settings):
    agent = RealAgent(real_settings, router=router(Recorder(), order="groq", GROQ_API_KEY=""))
    client, _ = _client(real_settings, agent)
    with client:
        client.post("/admin/seed")
    report = agent.calibration.report()
    assert report.samples == 20 and report.graded > 0
    assert not report.calibrated  # default threshold is 30


def test_backtest_never_overwrites_a_live_grade(real_settings):
    import asyncio

    import json
    from pathlib import Path

    from aftermath_intelligence.calibration import GradedPrediction
    from app.agent.interface import IncidentContext

    agent = RealAgent(real_settings, router=router(Recorder(), order="groq", GROQ_API_KEY=""))
    data = json.loads((Path(__file__).parent.parent / "data" / "seed_incidents.json").read_text(encoding="utf-8"))
    items = data["incidents"] if isinstance(data, dict) else data
    contexts = [IncidentContext(**{k: v for k, v in i.items() if k in IncidentContext.model_fields}) for i in items]
    live = GradedPrediction(contexts[0].id, 0.42, "correct", 1)
    agent.calibration.record(live)
    asyncio.run(agent.ingest_history(contexts))
    assert agent.calibration._items[contexts[0].id] == live
    assert agent.calibration.report().samples == len(contexts)


def test_calibrated_confidence_reaches_the_stream_and_saved_suggestion(real_settings, monkeypatch):
    monkeypatch.setenv("AFTERMATH_CALIBRATION_MIN_SAMPLES", "15")
    agent = RealAgent(real_settings, router=router(Recorder(), order="groq", GROQ_API_KEY=""))
    client, _ = _client(real_settings, agent)
    with client:
        client.post("/admin/seed")
        incident = client.post("/incidents", json=ALERT).json()
        events = _analyze(client, incident["id"])
        detail = client.get(f"/incidents/{incident['id']}").json()
    saved = detail["last_suggestion"]
    suggestion = next(d for n, d in events if n == "suggestion")
    assert suggestion["confidence_calibrated"] is True and 0.05 <= suggestion["confidence"] <= 0.95
    assert saved["confidence_calibrated"] is True
    analysis = [e["message"] for e in detail["timeline"] if e["type"] == "analysis"][-1]
    assert f"(confidence {round(suggestion['confidence'] * 100)}%)" in analysis


def test_empty_llm_stream_falls_back_to_deterministic_narrative(real_settings):
    def groq(request: httpx.Request) -> httpx.Response:
        if b'"stream"' in request.content:
            return groq_stream([])
        return groq_reply('{"assessments": []}')

    agent = RealAgent(real_settings, router=router(Recorder(groq=groq), order="groq"))
    client, _ = _client(real_settings, agent)
    with client:
        incident = client.post("/incidents", json=ALERT).json()
        events = _analyze(client, incident["id"])
    text = "".join(d["text"] for n, d in events if n == "token")
    assert text.rstrip().endswith("Here is the plan I'd follow:")


def test_real_agent_memory_and_calibration_survive_restart(real_settings, tmp_path):
    import asyncio

    from aftermath_intelligence.calibration import GradedPrediction
    from tests.test_connectors_and_persistence import _record

    first = RealAgent(real_settings, router=router(Recorder(), order="groq", GROQ_API_KEY=""))
    assert first.memory.backend == "local_file"
    asyncio.run(first.memory.retain(_record("INC-OLD")))
    for i in range(30):
        first.calibration.record(GradedPrediction(f"I{i}", i / 30, "correct" if i >= 15 else "incorrect", 1))

    second = RealAgent(real_settings, router=router(Recorder(), order="groq", GROQ_API_KEY=""))
    assert [r.incident_id for r in second.memory.records] == ["INC-OLD"]
    assert second.calibration.report().calibrated
    client, _ = _client(real_settings, second)
    with client:
        incident = client.post("/incidents", json=ALERT).json()
        events = _analyze(client, incident["id"])
    assert any(n == "memory" and d["past_incident_id"] == "INC-OLD" for n, d in events)
    suggestion = next(d for n, d in events if n == "suggestion")
    assert suggestion["confidence"] == second.calibration.calibrate(second.reports[incident["id"]].top_score)
