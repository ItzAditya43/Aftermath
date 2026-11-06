"""Independent investigator perspectives and a coordinator.

Each perspective looks at a different slice of the evidence and takes a stance on each
hypothesis. They are deterministic (no extra LLM calls): the value is in the *disagreement*
they surface, e.g. "logs blame the pool, but the change investigator sees no deploy, and
history says this symptom had two different causes".
"""
from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from .evidence import EvidenceKind, EvidenceLedger
from .memory import Lesson, RecalledIncident
from .rca import Hypothesis
from .signatures import BY_ID, Category, Role, SignatureMatch


class Stance(BaseModel):
    model_config = ConfigDict(frozen=True)

    perspective: str
    hypothesis_id: str
    stance: str            # support | oppose | neutral
    reason: str
    evidence_ids: tuple[str, ...] = ()


class DebateOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    stances: tuple[Stance, ...]
    agreement: dict[str, int]          # hypothesis id -> supports minus opposes
    disagreements: tuple[str, ...]
    consensus_hypothesis_id: str | None = None


def _log_view(h: Hypothesis, matches: dict[str, SignatureMatch], ledger: EvidenceLedger) -> Stance:
    match = matches.get(h.signature_id or "")
    if match and match.signature.role in {Role.ORIGIN, Role.MECHANISM}:
        return Stance(perspective="logs", hypothesis_id=h.id, stance="support",
                      reason=f"{len(match.event_ids)} log events match '{match.signature.label}'",
                      evidence_ids=h.supporting_evidence_ids[:3])
    if h.generated_by == "memory" and not match:
        return Stance(perspective="logs", hypothesis_id=h.id, stance="neutral",
                      reason="no current log signature for the historical cause")
    return Stance(perspective="logs", hypothesis_id=h.id, stance="neutral", reason="no direct log signature")


def _change_view(h: Hypothesis, matches: dict[str, SignatureMatch], ledger: EvidenceLedger) -> Stance:
    changes = [m for m in matches.values() if m.signature.role == Role.CHANGE]
    deploy_evidence = ledger.by_kind(EvidenceKind.DEPLOYMENT)
    if h.category == Category.DEPLOYMENT_REGRESSION or (h.signature_id and BY_ID.get(h.signature_id) and BY_ID[h.signature_id].role == Role.CHANGE):
        if changes or deploy_evidence:
            return Stance(perspective="changes", hypothesis_id=h.id, stance="support",
                          reason="a deploy/config/migration change precedes the failure")
        return Stance(perspective="changes", hypothesis_id=h.id, stance="oppose", reason="no change event found in the evidence")
    if changes and h.category in {Category.APPLICATION_BUG, Category.CONFIGURATION}:
        return Stance(perspective="changes", hypothesis_id=h.id, stance="support",
                      reason=f"recent {changes[0].signature.label} could have introduced this defect")
    return Stance(perspective="changes", hypothesis_id=h.id, stance="neutral",
                  reason="no change data" if not changes else "change present but unrelated category")


def _infra_view(h: Hypothesis, matches: dict[str, SignatureMatch], ledger: EvidenceLedger) -> Stance:
    saturation = [e for e in ledger.by_kind(EvidenceKind.ANOMALY) if e.attributes.get("anomaly") == "resource_saturation"]
    restarts = [e for e in ledger.by_kind(EvidenceKind.ANOMALY) if e.attributes.get("anomaly") == "restart_frequency"]
    if h.category in {Category.RESOURCE_EXHAUSTION, Category.CONNECTION_EXHAUSTION, Category.CAPACITY}:
        if saturation:
            return Stance(perspective="infrastructure", hypothesis_id=h.id, stance="support",
                          reason=saturation[0].summary, evidence_ids=(saturation[0].evidence_id,))
        return Stance(perspective="infrastructure", hypothesis_id=h.id, stance="neutral", reason="no saturation metrics supplied")
    if h.category == Category.INFRASTRUCTURE and restarts:
        return Stance(perspective="infrastructure", hypothesis_id=h.id, stance="support", reason=restarts[0].summary,
                      evidence_ids=(restarts[0].evidence_id,))
    for tool in ledger.by_kind(EvidenceKind.TOOL_RESULT):
        if tool.evidence_id in h.contradicting_evidence_ids:
            return Stance(perspective="infrastructure", hypothesis_id=h.id, stance="oppose", reason=tool.summary[:160],
                          evidence_ids=(tool.evidence_id,))
    return Stance(perspective="infrastructure", hypothesis_id=h.id, stance="neutral", reason="no infrastructure signal either way")


def _history_view(h: Hypothesis, recalled: tuple[RecalledIncident, ...], lessons: tuple[Lesson, ...]) -> Stance:
    supporting = [r for r in recalled if h.signature_id and h.signature_id in r.record.root_cause_signatures]
    if h.based_on_incidents or supporting:
        ids = tuple(dict.fromkeys(h.based_on_incidents + tuple(r.record.incident_id for r in supporting)))
        return Stance(perspective="history", hypothesis_id=h.id, stance="support",
                      reason=f"verified cause in {', '.join(ids)}")
    contradicting = [r for r in recalled if r.relevance >= 0.5 and r.record.root_cause_signatures
                     and h.signature_id and h.signature_id not in r.record.root_cause_signatures
                     and h.signature_id in r.record.symptom_signatures]
    if contradicting:
        return Stance(perspective="history", hypothesis_id=h.id, stance="oppose",
                      reason=f"{contradicting[0].record.incident_id} showed this signature but the cause was different")
    return Stance(perspective="history", hypothesis_id=h.id, stance="neutral",
                  reason="no relevant past incidents" if not recalled else "past incidents do not address this cause")


def run_debate(hypotheses: Iterable[Hypothesis], *, matches: Iterable[SignatureMatch], ledger: EvidenceLedger,
               recalled: Iterable[RecalledIncident] = (), lessons: Iterable[Lesson] = ()) -> DebateOutcome:
    by_sig = {m.id: m for m in matches}
    recalled, lessons = tuple(recalled), tuple(lessons)
    stances: list[Stance] = []
    hypotheses = tuple(hypotheses)[:5]
    for h in hypotheses:
        stances += [_log_view(h, by_sig, ledger), _change_view(h, by_sig, ledger), _infra_view(h, by_sig, ledger),
                    _history_view(h, recalled, lessons)]
    agreement: dict[str, int] = {}
    disagreements: list[str] = []
    for h in hypotheses:
        own = [s for s in stances if s.hypothesis_id == h.id]
        support = [s for s in own if s.stance == "support"]
        oppose = [s for s in own if s.stance == "oppose"]
        agreement[h.id] = len(support) - len(oppose)
        if support and oppose:
            disagreements.append(f"'{h.title}': {', '.join(s.perspective for s in support)} support it, "
                                 f"{', '.join(s.perspective for s in oppose)} oppose ({oppose[0].reason})")
    consensus = max(agreement, key=lambda k: agreement[k]) if agreement and max(agreement.values()) >= 2 else None
    return DebateOutcome(stances=tuple(stances), agreement=agreement, disagreements=tuple(disagreements),
                         consensus_hypothesis_id=consensus)
