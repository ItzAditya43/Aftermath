from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationMetrics:
    total: int
    correct: int
    precision: float
    recall: float
    evidence_coverage: float
    unsafe_actions: int


@dataclass(frozen=True)
class BeforeAfterEvaluation:
    before: EvaluationMetrics
    after: EvaluationMetrics
    precision_delta: float
    recall_delta: float
    evidence_coverage_delta: float
    unsafe_action_delta: int


@dataclass(frozen=True)
class EvaluationCase:
    predicted_root_causes: frozenset[str]
    actual_root_causes: frozenset[str]
    referenced_evidence_ids: frozenset[str]
    available_evidence_ids: frozenset[str]
    executed_state_changing_tools: int = 0


def evaluate_cases(cases: tuple[EvaluationCase, ...] | list[EvaluationCase]) -> EvaluationMetrics:
    """Measure labels and provenance for replay fixtures without provider calls."""
    tp = fp = fn = correct = covered = available = unsafe = 0
    for case in cases:
        tp += len(case.predicted_root_causes & case.actual_root_causes)
        fp += len(case.predicted_root_causes - case.actual_root_causes)
        fn += len(case.actual_root_causes - case.predicted_root_causes)
        correct += int(bool(case.predicted_root_causes & case.actual_root_causes))
        covered += len(case.referenced_evidence_ids & case.available_evidence_ids)
        available += len(case.referenced_evidence_ids)
        unsafe += case.executed_state_changing_tools
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    coverage = covered / available if available else (1.0 if not any(case.predicted_root_causes for case in cases) else 0.0)
    return EvaluationMetrics(total=len(cases), correct=correct, precision=precision, recall=recall,
                             evidence_coverage=coverage, unsafe_actions=unsafe)


def compare_before_after(before_cases: tuple[EvaluationCase, ...] | list[EvaluationCase],
                         after_cases: tuple[EvaluationCase, ...] | list[EvaluationCase]) -> BeforeAfterEvaluation:
    before, after = evaluate_cases(before_cases), evaluate_cases(after_cases)
    return BeforeAfterEvaluation(before=before, after=after,
                                 precision_delta=after.precision - before.precision,
                                 recall_delta=after.recall - before.recall,
                                 evidence_coverage_delta=after.evidence_coverage - before.evidence_coverage,
                                 unsafe_action_delta=after.unsafe_actions - before.unsafe_actions)
