import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aftermath_intelligence.calibration import CalibrationTracker, GradedPrediction
from aftermath_intelligence.ingestion import ingest_text
from aftermath_intelligence.insights import cluster_incidents, preventive_insights, what_if_skipped
from aftermath_intelligence.investigation import IncidentCase, InvestigationEngine, Phase
from aftermath_intelligence.learning import evaluate_resolution_report, grade_prediction
from aftermath_intelligence.memory import ActionType, InProcessMemory, build_memory_record
from aftermath_intelligence.postmortem import render_postmortem_markdown
from aftermath_intelligence.remediation import RemediationKind
from aftermath_intelligence.resolution import ResolutionStatus

SCENARIOS = json.loads(Path("data/eval_scenarios.json").read_text())["scenarios"]


def run(coro):
    return asyncio.run(coro)


def _case(s) -> IncidentCase:
    return IncidentCase(incident_id=s["id"], title=s["title"], service=s["service"], severity=s["severity"],
                        alert_text=s["alert_text"], logs=s["logs"])


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_scenario_root_cause_and_safety(scenario):
    report = run(InvestigationEngine().run(_case(scenario)))
    expected = scenario["expected"]
    leader = report.hypotheses[0]
    assert (leader.signature_id or "unknown") in {expected["root_signature"], *expected.get("also_accept", ())}
    assert leader.category.value == expected["category"]
    for symptom in expected["symptoms"]:
        assert symptom in report.chain.symptoms
    if expected["root_signature"] == "unknown":
        assert report.status == Phase.AWAITING_EVIDENCE and leader.score < 0.2
    for step in report.remediation.steps:
        if step.kind in {RemediationKind.MITIGATION, RemediationKind.ROOT_CAUSE_FIX}:
            assert step.requires_approval
    # every hypothesis points at evidence that actually exists
    ids = {e.evidence_id for e in report.evidence}
    for h in report.hypotheses:
        assert set(h.supporting_evidence_ids) <= ids


# --------------------------------------------------------------------------- memory learning scenario

POOL_LOGS_1 = "\n".join([
    "2026-09-01T09:00:00Z ERROR HikariPool-1 - Connection is not available, request timed out after 30000ms.",
    "2026-09-01T09:00:05Z WARN Connection leak detection triggered (ProxyLeakTask) on thread refund-3",
    "2026-09-01T09:00:06Z WARN Pool stats (total=40, active=40, idle=0, waiting=150)",
])
POOL_LOGS_2 = "\n".join([
    "2026-11-01T09:00:00Z ERROR HikariPool-1 - Connection is not available, request timed out after 30000ms.",
    "2026-11-01T09:00:03Z WARN session idle in transaction for 95s (PayoutExportJob)",
    "2026-11-01T09:00:06Z WARN Pool stats (total=40, active=40, idle=0, waiting=180)",
])


def test_two_incident_learning_scenario_avoids_temporary_fix_and_follows_current_evidence():
    memory = InProcessMemory()
    engine = InvestigationEngine(memory=memory)
    first = IncidentCase(incident_id="INC-A", title="payments-api pool exhausted", service="payments-api", severity="SEV1",
                         alert_text="5xx on payments-api", logs=POOL_LOGS_1,
                         created_at=datetime(2026, 9, 1, 9, tzinfo=timezone.utc))
    report_1 = run(engine.run(first))
    evaluation = evaluate_resolution_report(
        incident_id="INC-A", title=first.title, service=first.service, alert_text=first.alert_text,
        events=ingest_text(POOL_LOGS_1).events, root_cause="Connection leak in RefundService under retries",
        fix_that_worked="Hotfixed RefundService to use try-with-resources",
        what_failed=["Rolling restart of payments-api (pool exhausted again within 6 minutes)"],
        hypotheses=report_1.hypotheses, recommended_steps=[s.description for s in report_1.remediation.steps],
        created_at=first.created_at, resolved_at=first.created_at + timedelta(minutes=95))
    assert evaluation.outcome == ResolutionStatus.VERIFIED
    assert evaluation.prediction.verdict == "correct"
    restart = next(a for a in evaluation.attempts if a.action_type == ActionType.RESTART)
    assert restart.outcome == ResolutionStatus.TEMPORARY, "a restart that bought minutes is not a fix"
    run(memory.retain(evaluation.memory_record))

    second = IncidentCase(incident_id="INC-B", title="payments-api pool exhausted again", service="payments-api",
                          severity="SEV1", alert_text="5xx on payments-api", logs=POOL_LOGS_2)
    report_2 = run(engine.run(second))
    assert report_2.recalled_incidents[0].record.incident_id == "INC-A"
    assert any(lesson.kind == "avoid_action" and lesson.action_type == ActionType.RESTART for lesson in report_2.lessons)
    assert not any("restart" in s.description.lower() for s in report_2.remediation.steps
                   if s.kind == RemediationKind.MITIGATION)
    # Current evidence (a long transaction) leads, not a blind "repeat of INC-A".
    assert report_2.hypotheses[0].signature_id == "long_transaction"
    assert report_2.remediation.steps[0].kind == RemediationKind.VERIFY


# --------------------------------------------------------------------------- learning details

def test_restart_reported_as_the_fix_is_recorded_as_temporary():
    evaluation = evaluate_resolution_report(incident_id="I", title="t", service="s", alert_text="a", events=(),
                                            root_cause="unknown", fix_that_worked="Restarted the pods")
    assert evaluation.outcome == ResolutionStatus.TEMPORARY
    assert evaluation.memory_record.resolution_status == ResolutionStatus.TEMPORARY


def test_partial_resolution_and_prediction_grades():
    evaluation = evaluate_resolution_report(incident_id="I", title="t", service="s", alert_text="a", events=(),
                                            root_cause="Disk full on /data", fix_that_worked="Rotated logs",
                                            notes="errors reduced but still seeing some")
    assert evaluation.outcome == ResolutionStatus.PARTIAL
    assert grade_prediction((), "x").verdict == "unknown"
    payload = evaluation.memory_payload()
    assert payload["record"]["incident_id"] == "I" and "Confirmed root cause" in payload["text"]


def test_calibration_refuses_to_claim_probabilities_with_little_data():
    tracker = CalibrationTracker(min_samples=5)
    tracker.extend([GradedPrediction("a", 0.9, "correct", 1), GradedPrediction("b", 0.8, "incorrect", 2),
                    GradedPrediction("c", 0.2, "unknown", None)])
    report = tracker.report()
    assert not report.calibrated and report.graded == 2 and report.unresolved == 1
    assert report.top1_accuracy == 0.5 and report.top3_accuracy == 1.0
    assert report.overconfident_misses == ("b",)
    tracker.extend(GradedPrediction(str(i), 0.7, "correct", 1) for i in range(5))
    assert tracker.report().calibrated


def _seed_records():
    data = json.loads(Path("data/seed_incidents.json").read_text())["incidents"]
    return [build_memory_record(incident_id=i["id"], title=i["title"], service=i["service"], alert_text=i["alert_text"],
                                events=ingest_text(i["logs"], default_service=i["service"]).events,
                                root_cause=i["root_cause"], resolution=i["resolution"], failed_attempts=i["failed_attempts"],
                                created_at=datetime.fromisoformat(i["created_at"].replace("Z", "+00:00")),
                                time_to_resolve_minutes=i.get("time_to_resolve_minutes")) for i in data]


def test_clusters_and_preventive_insights_from_seed_history():
    records = _seed_records()
    clusters = cluster_incidents(records)
    payments = next(c for c in clusters if "INC-1001" in c.incident_ids)
    assert {"INC-1009", "INC-1017"} <= set(payments.incident_ids)
    kinds = {i.kind for i in preventive_insights(records)}
    assert {"recurring_pattern", "repeated_failed_mitigation", "hot_service"} <= kinds
    assert all(i.label == "historical observation" for i in preventive_insights(records))


def test_counterfactual_is_labelled_hypothetical():
    record = _seed_records()[0]
    answer = what_if_skipped(record, ActionType.RESTART)
    assert "hypothetical" in answer.label and answer.basis


def test_postmortem_marks_unknowns_and_is_blameless():
    report = run(InvestigationEngine().run(_case(SCENARIOS[0])))
    markdown = render_postmortem_markdown(report)
    assert markdown.startswith("# Postmortem") and "Leading hypothesis (not verified)" in markdown
    assert "blameless" in markdown and "_Not yet resolved._" in markdown
