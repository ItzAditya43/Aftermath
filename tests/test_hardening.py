"""Regression tests for the issues found in the Nov 7 deep test (warnings, latency, headline, wording, OOM)."""
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from aftermath_intelligence.ingestion import ingest_text
from aftermath_intelligence.investigation import IncidentCase, InvestigationEngine, InvestigationLimits
from aftermath_intelligence.memory import ActionType, InProcessMemory, Lesson, build_memory_record, derive_lessons
from aftermath_intelligence.presentation import clean_headline, suggestion_fields
from aftermath_intelligence.rca import Hypothesis
from aftermath_intelligence.remediation import plan_remediation
from aftermath_intelligence.signatures import Category, match_signatures
from tests.llm_fakes import Recorder, groq_reply, router

ROOT = Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.run(coro)


def _seeded_memory() -> InProcessMemory:
    memory = InProcessMemory()
    seed = json.loads((ROOT / "data/seed_incidents.json").read_text())
    seed = seed.get("incidents", seed) if isinstance(seed, dict) else seed
    for item in seed:
        events = ingest_text(item.get("logs", ""), incident_id=item["id"], default_service=item["service"]).events
        run(memory.retain(build_memory_record(
            incident_id=item["id"], title=item["title"], service=item["service"], alert_text=item.get("alert_text", ""),
            events=events, root_cause=item["root_cause"], resolution=item["resolution"],
            failed_attempts=item["failed_attempts"])))
    return memory


def _demo(index: int) -> dict:
    alerts = json.loads((ROOT / "data/demo_alerts.json").read_text())
    alerts = alerts.get("alerts", alerts) if isinstance(alerts, dict) else alerts
    return alerts[index]


def _case(alert: dict) -> IncidentCase:
    return IncidentCase(incident_id="INC-T", title=alert["title"], service=alert["service"],
                        severity=alert.get("severity", "SEV2"), alert_text=alert["alert_text"], logs=alert.get("logs", ""))


# ---------------------------------------------------------------- C1: history warnings always surface

def test_failed_history_becomes_warnings_even_when_plan_never_proposed_that_action():
    """api-gateway deploy regression: INC-1002 says restart and scale-out failed; the plan never suggests them."""
    report = run(InvestigationEngine(memory=_seeded_memory()).run(_case(_demo(1))))
    assert any(r.record.incident_id == "INC-1002" for r in report.recalled_incidents)
    warnings = " | ".join(report.remediation.warnings)
    assert "Restarting only gave temporary relief in INC-1002" in warnings
    assert "Scaling out did not resolve it in INC-1002" in warnings
    assert "Drained gateway connection pools" in warnings                      # INC-1011, an unclassified attempt
    assert suggestion_fields(report)["warnings"][:3] == list(report.remediation.warnings[:3])


def test_history_warnings_rank_before_generic_catalog_advice():
    h = Hypothesis(id="H-1", title="Connection leak", description="d", category=Category.APPLICATION_BUG,
                   signature_id="connection_leak", score=0.8)
    lessons = (Lesson(kind="avoid_action", message="Scaling out did not resolve it in INC-9", incident_ids=("INC-9",),
                      action_type=ActionType.SCALE_OUT),)
    plan = plan_remediation([h], lessons=lessons, service="payments-api", severity="SEV1")
    assert plan.warnings[0].startswith("SEV1")
    history = next(i for i, w in enumerate(plan.warnings) if "INC-9" in w)
    generic = next(i for i, w in enumerate(plan.warnings) if "raise the pool size" in w)
    assert history < generic


def test_blocked_step_is_not_warned_twice():
    h = Hypothesis(id="H-1", title="Connection leak", description="d", category=Category.APPLICATION_BUG,
                   signature_id="connection_leak", score=0.8)
    lessons = (Lesson(kind="avoid_action", message="Restarting only gave temporary relief in INC-1",
                      incident_ids=("INC-1",), action_type=ActionType.RESTART),)
    plan = plan_remediation([h], lessons=lessons, service="payments-api")
    assert sum("INC-1" in w for w in plan.warnings) == 1 and plan.blocked_actions


# ---------------------------------------------------------------- C4: readable lesson wording

def test_unclassified_attempt_is_quoted_not_called_other():
    memory = _seeded_memory()
    record = next(r for r in memory.records if r.incident_id == "INC-1011")
    from aftermath_intelligence.memory import RecalledIncident
    lessons = derive_lessons([RecalledIncident(record=record, relevance=0.9, matching_points=())])
    avoid = [lesson.message for lesson in lessons if lesson.kind == "avoid_action"]
    assert avoid and not any(m.startswith("other ") for m in avoid)
    assert any(m.startswith('"Drained gateway connection pools') for m in avoid)


# ---------------------------------------------------------------- C3: no doubled hedge in the headline

@pytest.mark.parametrize("raw, expected", [
    ("The most likely root cause is a regression introduced by v2.58.0.", "A regression introduced by v2.58.0."),
    ("Likely: A database connection leak exhausted the pool.", "A database connection leak exhausted the pool."),
    ("A connection leak in payments-api is the most likely root cause, exhausting the pool.",
     "A connection leak in payments-api, exhausting the pool."),
    ("Most likely the batch job loaded all rows.", "The batch job loaded all rows."),
    ("Disk exhaustion on es-node-2 prevented relocation.", "Disk exhaustion on es-node-2 prevented relocation."),
    ("The root cause remains undetermined.", "The root cause remains undetermined."),
])
def test_clean_headline(raw, expected):
    assert clean_headline(raw) == expected


def test_suggestion_root_cause_never_doubles_label():
    report = run(InvestigationEngine().run(_case(_demo(0))))
    report = report.model_copy(update={"headline": "The most likely root cause is a connection leak."})
    root = suggestion_fields(report)["likely_root_cause"]
    assert not root.startswith(("Likely", "Possibly"))
    assert "most likely" not in root.lower() and root.startswith("A connection leak")


def test_tentative_root_cause_is_flagged_in_warnings_not_in_the_cause():
    from aftermath_intelligence.rca import HypothesisState

    report = run(InvestigationEngine().run(_case(_demo(0))))
    leader = report.hypotheses[0]
    tentative = report.model_copy(update={"hypotheses": (leader.model_copy(update={"status": HypothesisState.PROPOSED}),
                                                         *report.hypotheses[1:])})
    fields = suggestion_fields(tentative)
    assert not fields["likely_root_cause"].startswith(("Likely", "Possibly"))
    # The SEV1 status-update reminder stays first; the certainty flag comes right after it.
    assert fields["warnings"][0].startswith("SEV1") and fields["warnings"][1].startswith("Low certainty")
    likely = suggestion_fields(report.model_copy(update={"hypotheses": (
        leader.model_copy(update={"status": HypothesisState.LIKELY}), *report.hypotheses[1:])}))
    assert not any(w.startswith("Low certainty") for w in likely["warnings"])


# ---------------------------------------------------------------- C5: heap OOM is not an OOM kill

def test_heap_out_of_memory_is_not_reported_as_a_kill():
    heap = ingest_text("2026-11-07T10:00:00Z ERROR orders-api java.lang.OutOfMemoryError: Java heap space",
                       default_service="orders-api").events
    kill = ingest_text("2026-11-07T10:00:00Z WARN kubelet orders-api container OOMKilled exit code 137",
                       default_service="orders-api").events
    assert {m.id for m in match_signatures(heap)} >= {"heap_exhausted"}
    assert "oom_killed" not in {m.id for m in match_signatures(heap)}
    assert "oom_killed" in {m.id for m in match_signatures(kill)}
    assert "heap_exhausted" not in {m.id for m in match_signatures(kill)}


# ---------------------------------------------------------------- C2: bounded latency

def test_slow_ai_cannot_stretch_the_investigation_past_its_deadline():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return groq_reply("{}")

    engine = InvestigationEngine(router=router(Recorder(groq=slow), order="groq"),
                                 limits=InvestigationLimits(max_seconds=2.0))
    started = time.monotonic()
    report = run(engine.run(_case(_demo(0))))
    assert time.monotonic() - started < 6
    assert report.hypotheses[0].signature_id == "connection_leak"                 # deterministic result stands
    assert any("time limit" in d or "unavailable" in d for d in report.degraded)


def test_slow_narrative_falls_back_to_deterministic_text(tmp_path, monkeypatch):
    from app.agent.interface import IncidentContext
    from app.agent.real_agent import RealAgent
    from app.config import Settings

    monkeypatch.setenv("AFTERMATH_MEMORY_PATH", "")
    monkeypatch.setenv("AFTERMATH_CALIBRATION_PATH", "")
    monkeypatch.setenv("AFTERMATH_NARRATIVE_FIRST_TOKEN_SECONDS", "1")

    async def groq(request: httpx.Request) -> httpx.Response:
        if b'"stream": true' in request.content or b'"stream":true' in request.content:
            await asyncio.sleep(30)
        return groq_reply('{"assessments": [], "root_cause_summary": "A connection leak exhausted the pool."}')

    settings = Settings(_env_file=None, agent_mode="real", database_url=f"sqlite:///{tmp_path / 'x.db'}")
    agent = RealAgent(settings, router=router(Recorder(groq=groq), order="groq"))
    alert = _demo(0)
    incident = IncidentContext(id="INC-T", title=alert["title"], service=alert["service"], severity="SEV1",
                               alert_text=alert["alert_text"], logs=alert.get("logs", ""), status="open",
                               created_at=datetime.now(timezone.utc))

    async def collect():
        return [e async for e in agent.analyze(incident)]

    started = time.monotonic()
    events = run(collect())
    assert time.monotonic() - started < 8
    text = "".join(getattr(e, "text", "") for e in events if e.type == "token")
    assert "Here is the plan I'd follow:" in text
    assert [e.type for e in events].count("suggestion") == 1


def test_full_investigation_is_exposed_as_json(tmp_path, monkeypatch):
    from app.agent.interface import IncidentContext
    from app.agent.real_agent import RealAgent
    from app.config import Settings

    monkeypatch.setenv("AFTERMATH_MEMORY_PATH", "")
    monkeypatch.setenv("AFTERMATH_CALIBRATION_PATH", "")
    settings = Settings(_env_file=None, agent_mode="real", database_url=f"sqlite:///{tmp_path / 'x.db'}")
    agent = RealAgent(settings, router=router(Recorder(), order="groq", GROQ_API_KEY=""))
    alert = _demo(0)
    incident = IncidentContext(id="INC-J", title=alert["title"], service=alert["service"], severity="SEV1",
                               alert_text=alert["alert_text"] + " password=Hunter2Secret!", logs=alert.get("logs", ""),
                               status="open", created_at=datetime.now(timezone.utc))
    assert agent.investigation("INC-J") is None

    async def drain():
        return [e async for e in agent.analyze(incident)]

    run(drain())
    data = agent.investigation("INC-J")
    json.dumps(data)                                                    # JSON-safe
    assert data["hypotheses"][0]["score_breakdown"] and data["graph"]["nodes"] and data["replay"]
    assert data["calibration"]["calibrated"] is False
    assert "Hunter2Secret" not in json.dumps(data)


# ---------------------------------------------------------------- C6: secrets never reach an AI provider

SECRETS = ("Hunter2Secret!", "AKIAIOSFODNN7EXAMPLE", "ghp_abcdefghijklmnopqrstuvwxyz0123456789", "s3cr3tpw",
           "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "wJalrXUtnFEMI", "xoxb-1234567890-abc", "sk_live_abcdefghijklmnop1234")
SECRET_LOGS = (
    "2026-11-07T10:00:00Z ERROR payments-api HikariPool-1 - Connection is not available, request timed out after 30000ms "
    "password=Hunter2Secret! aws_key=AKIAIOSFODNN7EXAMPLE token=ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
    "2026-11-07T10:00:01Z ERROR payments-api cannot connect postgres://app:s3cr3tpw@db:5432/pay "
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijkl\n"
    '2026-11-07T10:00:02Z WARN payments-api config aws_secret_access_key="wJalrXUtnFEMI/K7MDENG" '
    "slack=xoxb-1234567890-abc stripe=sk_live_abcdefghijklmnop1234\n")


def test_redaction_covers_common_credential_formats():
    from aftermath_intelligence.redaction import redact_sensitive_data, redact_sensitive_text
    out = redact_sensitive_text(SECRET_LOGS)
    assert not [s for s in SECRETS if s in out]
    benign = "pool stats (total=40, active=40, idle=0, waiting=212); tokens=1200 max_tokens: 700; GET /api/token/refresh 200"
    assert redact_sensitive_text(benign) == benign
    assert redact_sensitive_data({"input_tokens": 5, "db_password": "x", "api_key_id": "y"}) == {"input_tokens": 5}


def test_no_secret_is_ever_sent_to_an_ai_provider():
    recorder = Recorder(groq=lambda request: groq_reply('{"assessments": [], "root_cause_summary": "leak"}'))
    engine = InvestigationEngine(router=router(recorder, order="groq"))
    run(engine.run(IncidentCase(incident_id="INC-S", title="pool exhausted", service="payments-api", severity="SEV1",
                                alert_text="5xx password=Hunter2Secret!", logs=SECRET_LOGS)))
    assert recorder.requests, "the engine should have called the AI"
    sent = " ".join(request.content.decode() for _, request in recorder.requests)
    assert not [s for s in SECRETS if s in sent]
