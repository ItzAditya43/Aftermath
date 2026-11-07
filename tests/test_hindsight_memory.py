"""The Hindsight adapter, against a fake client that behaves like Hindsight (no network, no key)."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from aftermath_intelligence.ingestion import ingest_text
from aftermath_intelligence.memory import InProcessMemory, MemoryQuery, build_memory_record
from app.agent.hindsight_memory import FALLBACK_BACKEND, HindsightMemory, hindsight_from_settings


class NotFound(Exception):
    status = 404


class FakeHindsight:
    """Stores retained text per bank and document id; recall returns facts best-first with scores."""

    def __init__(self):
        self.banks: dict[str, dict[str, str]] = {}
        self.retains: list[dict] = []
        self.semantic: dict[str, float] = {}   # document id -> semantic score the next recalls report
        self.fail_recall = False
        self.fail_retain = False
        self.closed = False

    async def acreate_bank(self, bank_id, **_):
        self.banks.setdefault(bank_id, {})

    async def adelete_bank(self, bank_id):
        if bank_id not in self.banks:
            raise NotFound()
        del self.banks[bank_id]

    async def aretain(self, bank_id, content, *, document_id=None, metadata=None, tags=None, retain_async=False, **_):
        if self.fail_retain:
            raise ConnectionError("hindsight down")
        self.retains.append({"document_id": document_id, "metadata": metadata, "tags": tags, "async": retain_async})
        self.banks[bank_id][document_id] = content   # same document id = replace, never duplicate

    async def arecall(self, bank_id, query, **_):
        if self.fail_recall:
            raise ConnectionError("hindsight down")
        if bank_id not in self.banks:
            raise NotFound()
        ranked = sorted(self.banks[bank_id], key=lambda d: -self.semantic.get(d, 0.5))
        results = [SimpleNamespace(document_id=d, metadata={"incident_id": d}, text=f"fact about {d}",
                                   scores=SimpleNamespace(final=1.0, semantic=self.semantic.get(d, 0.5)))
                   for d in ranked]
        results.insert(1, SimpleNamespace(document_id=None, metadata={}, text="an observation across documents",
                                          scores=SimpleNamespace(final=0.9, semantic=0.9)))
        return SimpleNamespace(results=results)

    async def aclose(self):
        self.closed = True


def run(coro):
    return asyncio.run(coro)


def record(incident_id, service="payments-api", title="payments-api: connection pool exhausted",
           logs="HikariPool-1 - Connection is not available, request timed out after 30000ms."):
    events = ingest_text(logs, incident_id=incident_id, source="logs", default_service=service).events
    return build_memory_record(incident_id=incident_id, title=title, service=service, alert_text=title,
                               events=events, root_cause="Connection leak in RefundService",
                               resolution="Closed the connection in a finally block",
                               failed_attempts=["Raised maximumPoolSize from 40 to 60 (drained again)"])


def query(incident_id="INC-NEW", service="payments-api", text="payments-api: connection pool exhausted"):
    return MemoryQuery(incident_id=incident_id, service=service, text=text, signatures=("db_pool_exhausted",))


@pytest.fixture
def fake():
    return FakeHindsight()


@pytest.fixture
def memory(fake, tmp_path):
    return HindsightMemory(fake, "test-bank", InProcessMemory(), state_path=tmp_path / "hindsight_state.json")


def test_retain_writes_locally_and_to_hindsight_with_the_incident_as_document(memory, fake):
    run(memory.retain(record("INC-1")))
    assert memory.get("INC-1") is not None
    sent = fake.retains[0]
    assert sent["document_id"] == "INC-1" and sent["metadata"]["incident_id"] == "INC-1"
    assert sent["tags"] == ["service:payments-api"] and sent["async"] is False   # live resolutions: recallable at once
    assert "Connection leak in RefundService" in fake.banks["test-bank"]["INC-1"]


def test_recall_maps_hindsight_facts_back_to_incidents_in_hindsights_order(memory, fake):
    run(memory.retain_many([record("INC-1"), record("INC-2"), record("INC-3")]))
    fake.semantic = {"INC-2": 0.85, "INC-1": 0.8, "INC-3": 0.78}
    recalled = run(memory.recall(query()))
    assert [r.record.incident_id for r in recalled] == ["INC-2", "INC-1", "INC-3"]
    assert memory.backend == "hindsight"
    assert any(p.startswith("Hindsight recalled: fact about INC-2") for p in recalled[0].matching_points)
    assert all(0 < r.relevance <= 1 for r in recalled)


def test_weak_hindsight_matches_the_scorer_rejects_are_dropped(memory, fake):
    run(memory.retain_many([record("INC-9", service="search-api", title="search-api: cluster red", logs="ClusterRed")]))
    fake.semantic = {"INC-9": 0.6}   # Hindsight's weak tail, different service, no shared signal
    assert run(memory.recall(query())) == []


def test_a_strong_semantic_match_is_kept_even_when_the_scorer_disagrees(memory, fake):
    run(memory.retain_many([record("INC-9", service="checkout-api", title="checkout-api: db pool drained", logs="")]))
    fake.semantic = {"INC-9": 0.82}
    recalled = run(memory.recall(query()))
    assert [r.record.incident_id for r in recalled] == ["INC-9"] and recalled[0].relevance >= 0.82


def test_recall_never_returns_the_incident_itself(memory, fake):
    run(memory.retain_many([record("INC-NEW"), record("INC-1")]))
    fake.semantic = {"INC-NEW": 0.99, "INC-1": 0.8}
    assert [r.record.incident_id for r in run(memory.recall(query()))] == ["INC-1"]


def test_outage_falls_back_to_local_memory_and_says_so(memory, fake):
    run(memory.retain_many([record("INC-1")]))
    fake.fail_recall = True
    recalled = run(memory.recall(query()))
    assert [r.record.incident_id for r in recalled] == ["INC-1"]   # local recall still works
    assert memory.backend == FALLBACK_BACKEND
    fake.fail_recall = False
    run(memory.recall(query()))
    assert memory.backend == "hindsight"


def test_empty_bank_recalls_nothing(fake, tmp_path):
    memory = HindsightMemory(fake, "fresh-bank", InProcessMemory())
    assert run(memory.recall(query())) == [] and memory.backend == "hindsight"


def test_restart_does_not_resend_what_hindsight_already_has(fake, tmp_path):
    state = tmp_path / "hindsight_state.json"
    first = HindsightMemory(fake, "bank", InProcessMemory(), state_path=state)
    run(first.retain_many([record("INC-1"), record("INC-2")]))
    assert len(fake.retains) == 2 and all(r["async"] for r in fake.retains)   # history goes in the background

    local = InProcessMemory()
    run(local.retain_many([record("INC-1"), record("INC-2")]))
    second = HindsightMemory(fake, "bank", local, state_path=state)
    run(second.retain_many([]))
    assert len(fake.retains) == 2   # nothing resent: no credits spent on restart
    assert second.status()["in_hindsight"] == 2 and second.status()["pending"] == 0


def test_existing_local_memory_is_backfilled_into_hindsight(fake, tmp_path):
    local = InProcessMemory()
    run(local.retain_many([record("INC-1"), record("INC-2")]))   # learned before Hindsight was configured
    memory = HindsightMemory(fake, "bank", local, state_path=tmp_path / "s.json")
    run(memory.retain_many([]))
    assert sorted(fake.banks["bank"]) == ["INC-1", "INC-2"]


def test_failed_writes_stay_pending_and_are_retried(memory, fake):
    fake.fail_retain = True
    run(memory.retain(record("INC-1")))   # does not raise: the resolution is still saved locally
    assert memory.get("INC-1") is not None and memory.status()["pending"] == 1
    fake.fail_retain = False
    run(memory.retain(record("INC-2")))
    assert sorted(fake.banks["test-bank"]) == ["INC-1", "INC-2"] and memory.status()["pending"] == 0


def test_a_changed_resolution_is_resent_and_replaces_the_old_one(memory, fake):
    run(memory.retain(record("INC-1")))
    changed = record("INC-1").model_copy(update={"root_cause": "A different, corrected root cause"})
    run(memory.retain(changed))
    assert len(fake.banks["test-bank"]) == 1 and "corrected root cause" in fake.banks["test-bank"]["INC-1"]


def test_clear_forgets_locally_and_deletes_the_bank(memory, fake, tmp_path):
    run(memory.retain(record("INC-1")))
    run(memory.clear())
    assert len(memory) == 0 and "test-bank" not in fake.banks
    saved = json.loads((tmp_path / "hindsight_state.json").read_text(encoding="utf-8"))
    assert saved["test-bank"] == {}
    run(memory.retain(record("INC-2")))   # the bank is recreated on the next write
    assert list(fake.banks["test-bank"]) == ["INC-2"]


def test_aclose_closes_the_client(memory, fake):
    run(memory.aclose())
    assert fake.closed


def test_not_configured_means_local_memory():
    assert hindsight_from_settings("", "", "bank", InProcessMemory(), None) is None


def test_real_agent_uses_hindsight_when_configured(tmp_path, monkeypatch):
    from app.agent.real_agent import RealAgent
    from app.config import Settings
    from tests.llm_fakes import Recorder, router

    monkeypatch.setenv("AFTERMATH_MEMORY_PATH", str(tmp_path / "memory.jsonl"))
    monkeypatch.setenv("AFTERMATH_CALIBRATION_PATH", str(tmp_path / "calibration.jsonl"))
    settings = Settings(_env_file=None, agent_mode="real", hindsight_api_key="test-key", hindsight_bank_id="b")
    agent = RealAgent(settings, router=router(Recorder(), order="groq", GROQ_API_KEY=""))
    assert isinstance(agent.memory, HindsightMemory) and agent.memory.backend == "hindsight"
    assert agent.memory.bank_id == "b"
    plain = RealAgent(Settings(_env_file=None, agent_mode="real"), router=router(Recorder(), order="groq", GROQ_API_KEY=""))
    assert plain.memory.backend == "local_file"


def test_full_flow_through_the_api_with_hindsight(tmp_path, monkeypatch):
    """Seed -> new incident -> analyze recalls from Hindsight -> resolve retains it -> it is recalled next time."""
    from fastapi.testclient import TestClient

    from app.agent.real_agent import RealAgent
    from app.config import Settings
    from app.main import create_app
    from tests.conftest import ALERT, LOCAL_CLIENT, parse_sse
    from tests.llm_fakes import Recorder, router

    monkeypatch.setenv("AFTERMATH_MEMORY_PATH", str(tmp_path / "memory.jsonl"))
    monkeypatch.setenv("AFTERMATH_CALIBRATION_PATH", str(tmp_path / "calibration.jsonl"))
    settings = Settings(_env_file=None, agent_mode="real", database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
                        auto_seed=False, mock_token_delay_ms=0)
    fake = FakeHindsight()
    agent = RealAgent(settings, router=router(Recorder(), order="groq", GROQ_API_KEY=""),
                      memory=HindsightMemory(fake, "bank", InProcessMemory(), state_path=tmp_path / "s.json"))
    app = create_app(settings)
    app.state.agent = agent

    def analyze(client, incident_id):
        with client.stream("GET", f"/incidents/{incident_id}/analyze") as response:
            return parse_sse("".join(response.iter_text()))

    with TestClient(app, client=LOCAL_CLIENT) as client:
        client.post("/admin/seed")
        assert len(fake.banks["bank"]) == 20   # the seeded history went to Hindsight
        fake.semantic = {"INC-1017": 0.86, "INC-1009": 0.84, "INC-1001": 0.83}
        first = client.post("/incidents", json=ALERT).json()
        events = analyze(client, first["id"])
        recalled = [d["past_incident_id"] for n, d in events if n == "memory"]
        assert recalled[:3] == ["INC-1017", "INC-1009", "INC-1001"]
        report = client.get(f"/incidents/{first['id']}/investigation").json()
        assert report["memory_backend"] == "hindsight"

        resolved = client.post(f"/incidents/{first['id']}/resolve", json={
            "root_cause": "Connection leak in RefundService exhausted the Hikari pool",
            "fix_that_worked": "Hotfixed the leak with try-with-resources",
            "what_failed": ["Rolling restart of payments-api pods (pool exhausted again within 6 minutes)"]})
        assert resolved.json()["memory_saved"] is True
        assert first["id"] in fake.banks["bank"]   # the new resolution is in Hindsight

        fake.semantic[first["id"]] = 0.95
        second = client.post("/incidents", json={**ALERT, "title": "payments-api: pool exhausted again"}).json()
        again = [d["past_incident_id"] for n, d in analyze(client, second["id"]) if n == "memory"]
        assert again[0] == first["id"]   # recalled from Hindsight on the next similar incident
