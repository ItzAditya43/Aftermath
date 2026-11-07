"""Root-cause hypotheses with transparent, additive scoring.

The score is a *ranking score* in [0, 1], not a calibrated probability. Every point added or
removed is recorded in `score_breakdown` with the reason, so a reviewer can see exactly why
one explanation outranks another. Calibration against real outcomes lives in `calibration`.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from .causal import FailureChain
from .evidence import Evidence, EvidenceKind, EvidenceLedger
from .memory import RecalledIncident
from .resolution import ResolutionStatus
from .signatures import BY_ID, Category, Role, SignatureMatch

SCORE_FLOOR, SCORE_CEILING = 0.03, 0.95


class HypothesisState(StrEnum):
    PROPOSED = "proposed"
    LIKELY = "likely"
    UNRESOLVED = "unresolved"
    WEAKENED = "weakened"
    REJECTED = "rejected"
    VERIFIED = "verified"


class ScoreFactor(BaseModel):
    model_config = ConfigDict(frozen=True)

    factor: str
    delta: float
    reason: str


class StatusChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: HypothesisState
    reason: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Hypothesis(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    description: str
    category: Category
    affected_service: str | None = None
    signature_id: str | None = None
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    score: float = Field(ge=0, le=1)
    score_breakdown: tuple[ScoreFactor, ...] = ()
    reasoning: str = ""
    contributing_factors: tuple[str, ...] = ()
    explains_symptoms: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    verification_steps: tuple[str, ...] = ()
    status: HypothesisState = HypothesisState.PROPOSED
    history: tuple[StatusChange, ...] = ()
    generated_by: str = "deterministic"
    based_on_incidents: tuple[str, ...] = ()

    def with_status(self, status: HypothesisState, reason: str) -> "Hypothesis":
        if status == self.status:
            return self
        return self.model_copy(update={"status": status, "history": self.history + (StatusChange(status=status, reason=reason),)})

    def adjusted(self, factor: str, delta: float, reason: str) -> "Hypothesis":
        breakdown = self.score_breakdown + (ScoreFactor(factor=factor, delta=round(delta, 3), reason=reason),)
        return self.model_copy(update={"score": _clamp(sum(f.delta for f in breakdown)), "score_breakdown": breakdown})


def _clamp(value: float) -> float:
    return round(max(SCORE_FLOOR, min(SCORE_CEILING, value)), 3)


def hypothesis_id(key: str) -> str:
    return "H-" + sha256(key.encode()).hexdigest()[:8]


_BASE = {Role.ORIGIN: 0.35, Role.MECHANISM: 0.25, Role.CHANGE: 0.20, Role.CONTRIBUTOR: 0.10, Role.SYMPTOM: 0.08}


def generate_hypotheses(*, service: str, matches: Iterable[SignatureMatch], chain: FailureChain,
                        ledger: EvidenceLedger, signature_evidence: dict[str, str],
                        recalled: Iterable[RecalledIncident] = ()) -> list[Hypothesis]:
    """Competing, evidence-linked explanations derived from the failure chain and memory."""
    by_id = {m.id: m for m in matches}
    recalled = tuple(recalled)
    hypotheses: list[Hypothesis] = []
    # "Earliest failure" ignores changes and contributors (a batch job starting is not a failure).
    earliest = min((m.first_seen for m in by_id.values()
                    if m.first_seen and m.signature.role not in {Role.CHANGE, Role.CONTRIBUTOR}), default=None)

    candidates = list(chain.root_candidates)
    # A change that precedes the failure deserves its own competing explanation.
    candidates += [t for t in chain.triggers if t not in candidates]
    for sig_id in candidates:
        match = by_id[sig_id]
        signature = match.signature
        evidence_id = signature_evidence.get(sig_id)
        supporting = [evidence_id] if evidence_id else []
        explained = chain.explains.get(sig_id, ())
        supporting += [signature_evidence[e] for e in explained if e in signature_evidence]
        affected = match.services[0] if match.services else service
        if signature.role == Role.CHANGE:
            title = f"Regression introduced by a {signature.label}"
            introduced = _join([by_id[r].signature.label for r in chain.root_candidates if r in by_id])
            description = (f"A {signature.label} preceded the failure and may have introduced "
                           f"{introduced or 'it'}.")
        elif signature.role == Role.MECHANISM:
            title = f"{_upper_first(signature.label)} on {affected}"
            effects = _join([by_id[e].signature.label for e in explained])
            description = (f"{_upper_first(signature.label)} on {affected}"
                           + (f", causing {effects}" if effects else "")
                           + "; what drove it there is not identified yet.")
        else:
            title = f"{_upper_first(signature.label)} in {affected}"
            effects = _join([by_id[e].signature.label for e in explained])
            description = (f"{_upper_first(signature.label)} in {affected}"
                           + (f", which leads to {effects}" if effects else "") + ".")
        h = Hypothesis(id=hypothesis_id(f"sig:{sig_id}"), title=title, description=description,
                       category=signature.category, affected_service=affected, signature_id=sig_id,
                       supporting_evidence_ids=tuple(dict.fromkeys(supporting)), score=0.0,
                       contributing_factors=tuple(BY_ID[c].label for c in chain.contributing if c in BY_ID),
                       explains_symptoms=explained, verification_steps=signature.checks,
                       missing_evidence=_missing(signature.role, signature.tool_hints))
        h = h.adjusted("base", _BASE[signature.role], f"{signature.role.value} signature '{signature.label}'")
        h = h.adjusted("evidence_volume", min(0.12, 0.03 * max(0, len(match.event_ids) - 1)),
                       f"{len(match.event_ids)} matching events")
        if explained:
            h = h.adjusted("explains_symptoms", min(0.2, 0.07 * len(explained)),
                           f"explains {len(explained)} other observed signature(s)")
        if match.first_seen and earliest:
            lag = (match.first_seen - earliest).total_seconds()
            if lag <= 0:
                h = h.adjusted("temporal_precedence", 0.08, "earliest failure signal in the timeline")
            elif lag > 120 and explained:
                h = h.adjusted("temporal_precedence", -0.1, f"first seen {int(lag)}s after the earliest failure")
        if match.named_fix and signature.role in {Role.ORIGIN, Role.MECHANISM, Role.CONTRIBUTOR}:
            h = h.adjusted("named_as_fix", 0.12, "the report says fixing this resolved the incident")
        if signature.role == Role.ORIGIN and _has_app_origin_frame(ledger, match.event_ids):
            h = h.adjusted("stack_origin", 0.05, "stack trace points at an application frame")
        if signature.role == Role.CHANGE and not chain.root_candidates:
            h = h.adjusted("no_failure_mechanism", -0.08, "no failure mechanism observed to connect to the change")
        hypotheses.append(h)

    # Contributors that explain a candidate (batch job -> OOM) name the driver of a mechanism.
    for index, h in enumerate(hypotheses):
        drivers = [c for c in chain.contributing if h.signature_id in chain.explains.get(c, ())]
        if drivers:
            labels = _join([BY_ID[d].label for d in drivers])
            h = h.adjusted("driver_identified", 0.1, f"{labels} observed as the load driving it")
            hypotheses[index] = h.model_copy(update={"description": h.description.replace(
                "what drove it there is not yet identified.", f"likely driven by {labels}.")})

    # Memory: support current hypotheses whose signature recurred in verified past incidents,
    # and keep the most relevant concrete past causes as competing alternatives.
    alternatives = 0
    for item in recalled:
        record = item.record
        if not record.root_cause or record.resolution_status == ResolutionStatus.UNRESOLVED:
            continue
        history_evidence = tuple(e.evidence_id for e in ledger.by_kind(EvidenceKind.HISTORICAL)
                                 if e.attributes.get("incident_id") == record.incident_id)
        matched_any = False
        for index, h in enumerate(hypotheses):
            if not h.signature_id:
                continue
            as_cause = h.signature_id in record.root_cause_signatures
            as_symptom = h.signature_id in record.symptom_signatures
            if not (as_cause or as_symptom):
                continue
            matched_any = True
            weight = 0.2 if as_cause else 0.1
            reason = (f"{record.incident_id} (relevance {item.relevance:.2f}) had this verified cause" if as_cause else
                      f"{record.incident_id} (relevance {item.relevance:.2f}) showed the same signature")
            updated = h.adjusted("history_support", round(weight * item.relevance, 3), reason)
            hypotheses[index] = updated.model_copy(update={
                "based_on_incidents": tuple(dict.fromkeys(h.based_on_incidents + (record.incident_id,))),
                "supporting_evidence_ids": tuple(dict.fromkeys(h.supporting_evidence_ids + history_evidence))})
        if item.relevance >= 0.55 and alternatives < 2 and not (matched_any and record.root_cause_signatures
                                                                and set(record.root_cause_signatures) <= {h.signature_id for h in hypotheses}):
            alternatives += 1
            category = next((BY_ID[s].category for s in record.root_cause_signatures if s in BY_ID), Category.UNKNOWN)
            h = Hypothesis(id=hypothesis_id(f"hist:{record.incident_id}"),
                           title=f"Repeat of {record.incident_id}: {_first_sentence(record.root_cause)}",
                           description=f"Symptoms match {record.incident_id}, whose confirmed cause was: {record.root_cause}",
                           category=category, affected_service=record.service, supporting_evidence_ids=history_evidence,
                           score=0.0, generated_by="memory", based_on_incidents=(record.incident_id,),
                           missing_evidence=("Current evidence that the same component is involved (not just similar symptoms)",),
                           verification_steps=(f"Check whether the component blamed in {record.incident_id} is active now",))
            h = h.adjusted("base", 0.12, "historical analogue; similar symptoms do not prove the same cause")
            hypotheses.append(h.adjusted("history_support", round(0.2 * item.relevance, 3), f"relevance {item.relevance:.2f}"))

    if not hypotheses:
        signals = [m.signature.label for m in by_id.values()]
        h = Hypothesis(id=hypothesis_id(f"unknown:{service}"), title="Insufficient evidence for a root cause",
                       description=("No known failure mechanism was identified in the supplied evidence"
                                    + (f"; observed only {_join(signals)}" if signals else "") + "."),
                       category=Category.UNKNOWN, affected_service=service,
                       supporting_evidence_ids=tuple(signature_evidence.values())[:5], score=0.0,
                       missing_evidence=("Logs from the failing service around the first alert",
                                         "Recent deploys and configuration changes",
                                         "Resource metrics (CPU, memory, connections)"),
                       verification_steps=("Collect the missing evidence and re-run the analysis",))
        hypotheses.append(h.adjusted("base", 0.1, "placeholder until evidence identifies a mechanism"))
    return rank(hypotheses)


def apply_contradictions(hypotheses: list[Hypothesis], ledger: EvidenceLedger) -> list[Hypothesis]:
    """Tool results that report a component as healthy contradict hypotheses blaming it."""
    healthy_words = ("healthy", "normal", "no errors", "within limits", "nominal", "passed", "ready", "ok")
    output: list[Hypothesis] = []
    for h in hypotheses:
        signature = BY_ID.get(h.signature_id or "")
        related = [BY_ID[s] for s in (h.signature_id, *h.explains_symptoms) if s and s in BY_ID]
        hints = {hint for sig in related for hint in sig.tool_hints}
        against, support = [], []
        for item in ledger.by_kind(EvidenceKind.TOOL_RESULT):
            text = f"{item.source} {item.summary}".lower()
            if hints and not any(hint in text for hint in hints) and (h.affected_service or "") not in text:
                continue
            if signature and signature.search(item.summary):
                support.append(item.evidence_id)
            elif any(word in text for word in healthy_words):
                against.append(item.evidence_id)
        for eid in against:
            if eid not in h.contradicting_evidence_ids:
                h = h.adjusted("contradiction", -0.15, f"diagnostic {eid} reports the component healthy")
                h = h.model_copy(update={"contradicting_evidence_ids": h.contradicting_evidence_ids + (eid,)})
        for eid in support:
            if eid not in h.supporting_evidence_ids:
                h = h.adjusted("diagnostic_confirmation", 0.2, f"diagnostic {eid} shows the same signature")
                h = h.model_copy(update={"supporting_evidence_ids": h.supporting_evidence_ids + (eid,)})
        output.append(h)
    return rank(output)


def rank(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    ordered = sorted(hypotheses, key=lambda h: (-h.score, h.id))
    out: list[Hypothesis] = []
    for index, h in enumerate(ordered):
        if h.status in {HypothesisState.VERIFIED, HypothesisState.REJECTED}:
            out.append(h)
            continue
        runner_up = ordered[1].score if len(ordered) > 1 else 0.0
        if h.contradicting_evidence_ids and len(h.contradicting_evidence_ids) >= len(h.supporting_evidence_ids):
            out.append(h.with_status(HypothesisState.WEAKENED, "contradicting evidence outweighs support"))
        elif index == 0 and h.score >= 0.6 and h.score - runner_up >= 0.1:
            out.append(h.with_status(HypothesisState.LIKELY, f"leads the next explanation by {h.score - runner_up:.2f}"))
        elif h.category == Category.UNKNOWN and not h.based_on_incidents:
            out.append(h.with_status(HypothesisState.UNRESOLVED, "no mechanism identified"))
        else:
            out.append(h.with_status(HypothesisState.PROPOSED, "competing explanation") if h.status != HypothesisState.PROPOSED else h)
    return out


def compare_top(hypotheses: list[Hypothesis]) -> str | None:
    """One-line explanation of why the leader outranks the runner-up."""
    if len(hypotheses) < 2:
        return None
    a, b = hypotheses[0], hypotheses[1]
    a_factors = {f.factor: f.delta for f in a.score_breakdown}
    b_factors = {f.factor: f.delta for f in b.score_breakdown}
    gaps = sorted(((k, a_factors.get(k, 0) - b_factors.get(k, 0)) for k in a_factors | b_factors),
                  key=lambda kv: -kv[1])
    reasons = [k.replace("_", " ") for k, gap in gaps if gap > 0.02][:3]
    return (f"'{a.title}' ranks above '{b.title}' ({a.score:.2f} vs {b.score:.2f})"
            + (f" mainly on {', '.join(reasons)}" if reasons else "") + ".")


def _missing(role: Role, hints: tuple[str, ...]) -> tuple[str, ...]:
    asks = {
        "database": "Database connection / lock statistics from the incident window",
        "metrics": "Resource or pool metrics over time (not just a single sample)",
        "github": "Recent commits / PRs touching the affected component",
        "deployment": "Deployment timestamps for the affected services",
        "container": "Container restart counts, exit codes and memory usage",
        "config": "Diff of active configuration vs last known good",
        "network": "Connectivity / DNS checks between the services involved",
    }
    out = [asks[h] for h in hints if h in asks]
    if role == Role.MECHANISM:
        out.insert(0, "Evidence of what is holding / consuming the exhausted resource")
    return tuple(dict.fromkeys(out))[:4]


def _has_app_origin_frame(ledger: EvidenceLedger, event_ids: tuple[str, ...]) -> bool:
    for event_id in event_ids:
        for item in ledger.by_event(event_id):
            if item.attributes.get("origin_frame"):
                return True
    return False


def _upper_first(text: str) -> str:
    """Capitalise the first letter only: str.capitalize() would turn "EHR downtime" into "Ehr downtime"."""
    return text[:1].upper() + text[1:]


def _join(items: list[str]) -> str:
    items = list(dict.fromkeys(i for i in items if i))
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _first_sentence(text: str, limit: int = 110) -> str:
    sentence = text.split(". ")[0].strip().rstrip(".")
    return sentence if len(sentence) <= limit else sentence[: limit - 1] + "…"


def evidence_for(ledger: EvidenceLedger, ids: Iterable[str]) -> tuple[Evidence, ...]:
    return tuple(item for item in (ledger.by_id(i) for i in ids) if item)
