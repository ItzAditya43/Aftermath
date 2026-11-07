"""Incident packs: selection, catalog integrity, and that every pack obeys the core safety contract."""
import asyncio

import pytest

from aftermath_intelligence.investigation import IncidentCase, InvestigationEngine
from aftermath_intelligence.packs import all_packs, enabled_signatures, selected_pack_ids
from aftermath_intelligence.remediation import RemediationKind
from aftermath_intelligence.signatures import BY_ID, CATALOG, CORE_CATALOG, Role

PACK_IDS = ("security", "network", "kubernetes", "data_pipeline", "industrial", "healthcare", "logistics", "support",
            "safety")


def test_all_packs_enabled_by_default_and_ids_unique():
    assert selected_pack_ids("all") == PACK_IDS
    assert len(CATALOG) == len(CORE_CATALOG) + sum(len(p.signatures) for p in all_packs())
    assert len(BY_ID) == len(CATALOG)


def test_pack_selection():
    assert selected_pack_ids("none") == ()
    assert selected_pack_ids("security, industrial") == ("security", "industrial")
    assert {s.id for s in enabled_signatures("network")} == {s.id for s in all_packs()[1].signatures}
    with pytest.raises(ValueError, match="unknown incident pack"):
        selected_pack_ids("security,astrology")


@pytest.mark.parametrize("pack", all_packs(), ids=lambda p: p.id)
def test_every_pack_signature_is_actionable_and_causes_resolve(pack):
    for sig in pack.signatures:
        assert sig.patterns and sig.checks, sig.id
        assert sig.role == Role.SYMPTOM or sig.mitigations or sig.fixes, sig.id
        assert all(c in BY_ID for c in sig.causes), (sig.id, sig.causes)


@pytest.mark.parametrize("logs, service, expected", [
    ("2026-11-07T03:10:01Z WARN sshd Failed password for root from 185.220.101.4 port 51122 ssh2\n" * 3, "auth-service",
     "brute_force_login"),
    ("2026-11-07T06:00:00Z WARN plc-3 vibration alarm motor M2 rms velocity 11.4 mm/s\n"
     "2026-11-07T06:42:01Z ERROR plc-3 motor M2 tripped", "press-line-3", "vibration_anomaly"),
    ("2026-11-07T15:00:00Z ERROR consumer failed to deserialize record at offset 1: schema mismatch", "events", "schema_drift"),
])
def test_pack_incidents_get_approval_gated_plans(logs, service, expected):
    report = asyncio.run(InvestigationEngine().run(IncidentCase(
        incident_id="INC-P", title="t", service=service, severity="SEV2", alert_text="alert", logs=logs)))
    assert report.hypotheses[0].signature_id == expected
    for step in report.remediation.steps:
        if step.kind in {RemediationKind.MITIGATION, RemediationKind.ROOT_CAUSE_FIX}:
            assert step.requires_approval, step.description
    # Catalog advice arrives as plain sentences (the UI adds its own "Warnings" heading).
    assert set(BY_ID[expected].avoid) <= set(report.remediation.warnings)
    assert not any(w.startswith("Avoid:") for w in report.remediation.warnings)
