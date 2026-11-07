"""Written incident reports (#40): the prose reader and the healthcare / logistics / support / safety packs."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aftermath_intelligence.ingestion import ingest_text
from aftermath_intelligence.investigation import IncidentCase, InvestigationEngine
from aftermath_intelligence.models import Severity
from aftermath_intelligence.packs import all_packs
from aftermath_intelligence.presentation import suggestion_fields
from aftermath_intelligence.remediation import RemediationKind
from aftermath_intelligence.reports import looks_like_report, parse_report
from aftermath_intelligence.signatures import match_signatures

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = json.loads((ROOT / "data/eval_scenarios.json").read_text())["scenarios"]
REPORTS = {s["id"]: s for s in SCENARIOS if s["id"].startswith("report-")}
LOG_SCENARIOS = [s for s in SCENARIOS if not s["id"].startswith("report-")]
REPORT_PACKS = {"healthcare", "logistics", "support", "safety"}


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- detection


@pytest.mark.parametrize("scenario", REPORTS.values(), ids=lambda s: s["id"])
def test_written_reports_are_detected(scenario):
    assert looks_like_report(scenario["logs"])
    assert "report" in ingest_text(scenario["logs"]).detected_formats


def test_machine_logs_are_never_read_as_reports():
    demo = json.loads((ROOT / "data/demo_alerts.json").read_text())["alerts"]
    seed = json.loads((ROOT / "data/seed_incidents.json").read_text())["incidents"]
    for text in [s["logs"] for s in LOG_SCENARIOS] + [a["logs"] for a in demo] + [s["logs"] for s in seed]:
        assert not looks_like_report(text), text[:80]
    assert not looks_like_report("")
    assert not looks_like_report("one short line")


# ---------------------------------------------------------------- parsing


def test_report_sections_times_and_severity():
    events = parse_report(REPORTS["report-hospital-ehr"]["logs"], default_service="ward-4b")
    by_text = {e.message[:30]: e for e in events}
    onset = next(e for e in events if e.message.startswith("At 02:10"))
    assert onset.timestamp == datetime(2026, 11, 7, 2, 10, tzinfo=timezone.utc)
    assert onset.severity == Severity.ERROR and onset.metadata["section"] == "summary"
    timeline = [e for e in events if e.metadata["section"] == "timeline"]
    assert [e.timestamp.strftime("%H:%M") for e in timeline] == ["02:05", "02:10", "02:20", "02:25", "03:40"]
    action = next(e for e in events if "Restarting the ward workstations" in e.message)
    assert action.event_type == "action" and action.severity == Severity.INFO and action.metadata["attempt_failed"]
    assert action.timestamp is None                       # action times are not failure times
    harm = next(e for e in events if e.message.startswith("Fourteen"))
    assert harm.severity == Severity.WARNING              # "no patient harm" is not harm; "late" is a warning
    assert not any(e.message.startswith(("Incident report", "Date:", "Reported by")) for e in events)
    assert all(e.service == "ward-4b" for e in events) and len(by_text) == len(events)


@pytest.mark.parametrize("text, expected", [
    ("Date: 2026-11-07", "2026-11-07"), ("Date: 7 November 2026", "2026-11-07"), ("Date: Nov 7, 2026", "2026-11-07"),
    ("Date: 7th Nov 2026", "2026-11-07"),
])
def test_report_dates(text, expected):
    report = f"Incident report\n{text}\n\nSummary\nAt 2:15 pm the warehouse management system went down for all pickers on site."
    event = next(e for e in parse_report(report) if e.timestamp)
    assert event.timestamp.isoformat().startswith(f"{expected}T14:15")


def test_report_without_a_date_uses_the_reference_or_stays_untimed():
    text = "Summary\nAt 23:50 the dispatch system went offline for every depot. At 00:20 it was still offline."
    assert all(e.timestamp is None for e in parse_report(text))
    events = parse_report(text, reference=datetime(2026, 11, 7, 23, 0, tzinfo=timezone.utc))
    assert [e.timestamp.isoformat() for e in events] == ["2026-11-07T23:50:00+00:00", "2026-11-08T00:20:00+00:00"]


def test_named_fix_is_recognised_but_failed_attempts_are_not():
    events = parse_report(REPORTS["report-airline-baggage"]["logs"])
    fixed = next(e for e in events if "worn drive belt" in e.message)
    assert fixed.metadata.get("named_fix")
    failed = next(e for e in events if "Rebooting the sorter controller" in e.message)
    assert failed.metadata.get("attempt_failed") and not failed.metadata.get("named_fix")


# ---------------------------------------------------------------- packs and end to end


@pytest.mark.parametrize("scenario", REPORTS.values(), ids=lambda s: s["id"])
def test_reports_get_the_right_cause_and_a_safe_plan(scenario):
    report = run(InvestigationEngine().run(IncidentCase(
        incident_id=scenario["id"], title=scenario["title"], service=scenario["service"], severity=scenario["severity"],
        alert_text=scenario["alert_text"], logs=scenario["logs"])))
    expected = scenario["expected"]
    assert report.hypotheses[0].signature_id in {expected["root_signature"], *expected.get("also_accept", ())}
    assert report.hypotheses[0].category.value == expected["category"]
    for step in report.remediation.steps:
        if step.kind in {RemediationKind.MITIGATION, RemediationKind.ROOT_CAUSE_FIX}:
            assert step.requires_approval, step.description
    fields = suggestion_fields(report)
    text = " ".join(fields["steps"] + fields["warnings"])
    assert "error rate and latency" not in text and "user-facing errors" not in text
    assert not any(w.startswith("Avoid:") for w in fields["warnings"])


def test_the_report_that_names_its_fix_ranks_that_cause_first():
    s = REPORTS["report-airline-baggage"]
    report = run(InvestigationEngine().run(IncidentCase(incident_id="R", title=s["title"], service=s["service"],
                                                        severity="SEV2", alert_text=s["alert_text"], logs=s["logs"])))
    leader = report.hypotheses[0]
    assert leader.signature_id == "mechanical_wear"
    assert any(b.factor == "named_as_fix" for b in leader.score_breakdown)


def test_report_packs_never_fire_on_machine_log_scenarios():
    """The new packs must not disturb log analysis: none of their signatures match any log scenario."""
    catalog = tuple(sig for pack in all_packs() if pack.id in REPORT_PACKS for sig in pack.signatures)
    for s in LOG_SCENARIOS:
        events = ingest_text(s["logs"], default_service=s["service"]).events
        hits = match_signatures(events, extra_text={"alert": f"{s['title']}\n{s['alert_text']}"}, catalog=catalog)
        assert not hits, (s["id"], [m.id for m in hits])


def test_healthcare_guidance_stays_operational():
    """Healthcare steps are about systems and procedures, never treatment or dosing decisions."""
    healthcare = next(p for p in all_packs() if p.id == "healthcare")
    advice = " ".join(t for sig in healthcare.signatures for t in (*sig.checks, *sig.mitigations, *sig.fixes, *sig.avoid))
    for word in ("mg", "dosage", "administer ", "prescribe", "increase the dose", "reduce the dose"):
        assert word not in advice.lower(), word
