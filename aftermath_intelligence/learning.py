"""Resolution evaluation: turn what the engineer reported into structured learning data.

Critical rules enforced here:
* a failed attempt is never recorded as a solution;
* a temporary mitigation is never recorded as a verified root-cause fix;
* the prediction is graded against the *confirmed* cause, not against itself.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .memory import ActionType, AttemptRecord, MemoryRecord, build_memory_record, classify_action, classify_failed_attempt, keywords
from .models import NormalizedEvent
from .rca import Hypothesis
from .resolution import ResolutionStatus
from .signatures import BY_ID, Role, match_signatures

_PARTIAL = re.compile(r"\bpartial(?:ly)?\b|\bstill (?:seeing|elevated|some)\b|\breduced\b|\bmostly\b", re.I)
_MITIGATION_ONLY = {ActionType.RESTART, ActionType.SCALE_OUT, ActionType.RAISE_LIMIT, ActionType.FAILOVER,
                    ActionType.CACHE_PURGE, ActionType.WAIT}


class PredictionGrade(BaseModel):
    model_config = ConfigDict(frozen=True)

    verdict: str                    # correct | partially_correct | incorrect | unknown
    predicted: str | None
    predicted_signature: str | None
    predicted_score: float | None
    actual_signatures: tuple[str, ...]
    rank_of_actual: int | None      # 1-based position of the first hypothesis matching the confirmed cause
    explanation: str


class ResolutionEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    outcome: ResolutionStatus
    attempts: tuple[AttemptRecord, ...]
    prediction: PredictionGrade
    recommended_actions_tried: tuple[str, ...]
    recommended_actions_failed: tuple[str, ...]
    time_to_resolve_minutes: int | None
    summary: str
    memory_record: MemoryRecord

    def memory_payload(self) -> dict:
        """Neutral payload for the memory backend (the Hindsight adapter owns retention)."""
        return {"record": self.memory_record.model_dump(mode="json"), "text": self.memory_record.retain_text(),
                "evaluation": self.model_dump(mode="json", exclude={"memory_record"})}


def grade_prediction(hypotheses: Iterable[Hypothesis], confirmed_root_cause: str) -> PredictionGrade:
    hypotheses = tuple(hypotheses)
    actual = tuple(m.id for m in match_signatures((), extra_text={"root": confirmed_root_cause})
                   if m.signature.role in {Role.ORIGIN, Role.MECHANISM, Role.CHANGE})
    if not hypotheses:
        return PredictionGrade(verdict="unknown", predicted=None, predicted_signature=None, predicted_score=None,
                               actual_signatures=actual, rank_of_actual=None, explanation="no prediction was made")
    top = hypotheses[0]
    rank = next((i + 1 for i, h in enumerate(hypotheses) if h.signature_id and h.signature_id in actual), None)
    overlap_kw = _jaccard(keywords(f"{top.title} {top.description}"), keywords(confirmed_root_cause))
    if top.signature_id and top.signature_id in actual:
        verdict, why = "correct", f"predicted '{BY_ID[top.signature_id].label}', which the confirmed cause describes"
    elif top.category.value != "unknown" and any(BY_ID[s].category == top.category for s in actual if s in BY_ID):
        verdict, why = "partially_correct", f"right category ({top.category.value}) but a different specific mechanism"
    elif overlap_kw >= 0.2:
        verdict, why = "partially_correct", f"wording overlaps the confirmed cause ({overlap_kw:.0%} keyword overlap)"
    elif top.category.value == "unknown":
        verdict, why = "unknown", "the engine reported insufficient evidence"
    else:
        verdict, why = "incorrect", "the leading hypothesis does not match the confirmed cause"
    if rank and rank > 1:
        why += f"; the confirmed cause was ranked #{rank}"
    return PredictionGrade(verdict=verdict, predicted=top.title, predicted_signature=top.signature_id,
                           predicted_score=top.score, actual_signatures=actual, rank_of_actual=rank, explanation=why)


def evaluate_resolution_report(*, incident_id: str, title: str, service: str, alert_text: str,
                               events: Iterable[NormalizedEvent], root_cause: str, fix_that_worked: str,
                               what_failed: Iterable[str] = (), notes: str | None = None,
                               hypotheses: Iterable[Hypothesis] = (), recommended_steps: Iterable[str] = (),
                               severity: str | None = None, created_at: datetime | None = None,
                               resolved_at: datetime | None = None) -> ResolutionEvaluation:
    what_failed = [w for w in what_failed if w and w.strip()]
    fix_action = classify_action(fix_that_worked)
    # Classify the reported fix honestly: a restart/scale that "worked" is relief, not a root-cause fix.
    combined = f"{fix_that_worked} {notes or ''}"
    if classify_failed_attempt(combined) == ResolutionStatus.TEMPORARY:
        outcome = ResolutionStatus.TEMPORARY
    elif _PARTIAL.search(combined):
        outcome = ResolutionStatus.PARTIAL
    elif fix_action in _MITIGATION_ONLY and not re.search(r"\b(?:fix|hotfix|patch|revert|roll(?:ed)? back|chang)", fix_that_worked, re.I):
        outcome = ResolutionStatus.TEMPORARY
    else:
        outcome = ResolutionStatus.VERIFIED
    ttr = int((resolved_at - created_at).total_seconds() // 60) if created_at and resolved_at else None
    hypotheses = tuple(hypotheses)
    grade = grade_prediction(hypotheses, root_cause)
    tried, failed = _compare_recommendations(recommended_steps, what_failed, fix_that_worked)
    record = build_memory_record(
        incident_id=incident_id, title=title, service=service, alert_text=alert_text, events=events,
        root_cause=root_cause, resolution=fix_that_worked, failed_attempts=what_failed, severity=severity,
        created_at=created_at, time_to_resolve_minutes=ttr, notes=notes, resolution_status=outcome,
        predicted_root_cause=grade.predicted,
        prediction_correct=None if grade.verdict == "unknown" else grade.verdict == "correct")
    failed_n = sum(1 for a in record.attempts if a.outcome == ResolutionStatus.FAILED)
    temp_n = sum(1 for a in record.attempts if a.outcome == ResolutionStatus.TEMPORARY and a.description != fix_that_worked)
    summary = (f"Resolved ({outcome.value}) after {len(what_failed)} unsuccessful attempt(s) "
               f"({failed_n} failed, {temp_n} temporary). Prediction {grade.verdict.replace('_', ' ')}: {grade.explanation}."
               + (f" Recommended actions that failed: {', '.join(failed)}." if failed else ""))
    return ResolutionEvaluation(incident_id=incident_id, outcome=outcome, attempts=record.attempts, prediction=grade,
                                recommended_actions_tried=tuple(tried), recommended_actions_failed=tuple(failed),
                                time_to_resolve_minutes=ttr, summary=summary, memory_record=record)


def _compare_recommendations(recommended: Iterable[str], failed: list[str], fix: str) -> tuple[list[str], list[str]]:
    recommended_actions = {classify_action(step): step for step in recommended}
    failed_actions = {classify_action(f) for f in failed}
    tried = [step for action, step in recommended_actions.items()
             if action != ActionType.OTHER and (action in failed_actions or action == classify_action(fix))]
    failures = [step for action, step in recommended_actions.items() if action in failed_actions and action != ActionType.OTHER]
    return tried, failures


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0
