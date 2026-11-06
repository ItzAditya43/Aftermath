"""Track ranking scores against confirmed outcomes.

Scores are not probabilities until enough graded outcomes show they behave like ones.
`CalibrationReport.calibrated` stays False below `min_samples`, and callers must not
display scores as percentages until it flips.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True)
class GradedPrediction:
    incident_id: str
    score: float
    verdict: str                 # correct | partially_correct | incorrect | unknown
    rank_of_actual: int | None


class CalibrationBin(BaseModel):
    model_config = ConfigDict(frozen=True)

    low: float
    high: float
    count: int
    mean_score: float | None
    accuracy: float | None


class CalibrationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    samples: int
    graded: int
    top1_accuracy: float | None
    top3_accuracy: float | None
    brier_score: float | None
    unresolved: int
    overconfident_misses: tuple[str, ...]
    bins: tuple[CalibrationBin, ...]
    calibrated: bool
    note: str


class CalibrationTracker:
    def __init__(self, *, min_samples: int = 30):
        self.min_samples = min_samples
        self._items: dict[str, GradedPrediction] = {}

    def record(self, prediction: GradedPrediction) -> None:
        self._items[prediction.incident_id] = prediction

    def extend(self, predictions: Iterable[GradedPrediction]) -> None:
        for p in predictions:
            self.record(p)

    def clear(self) -> None:
        self._items.clear()

    def calibrate(self, score: float) -> float | None:
        """Map a ranking score to observed accuracy, or None while there are too few graded outcomes.

        Isotonic regression (pool-adjacent-violators) over graded predictions: monotone, so a higher
        score never maps to a lower probability, and it makes no parametric assumption.
        Values are clamped to [0.05, 0.95] so a small sample can never claim certainty.
        """
        graded = sorted((p for p in self._items.values() if p.verdict != "unknown"), key=lambda p: p.score)
        if len(graded) < self.min_samples:
            return None
        blocks: list[list[float]] = []          # [sum_outcome, count, max_score]
        for p in graded:
            blocks.append([1.0 if p.verdict == "correct" else 0.5 if p.verdict == "partially_correct" else 0.0,
                           1.0, p.score])
            while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
                tail = blocks.pop()
                blocks[-1] = [blocks[-1][0] + tail[0], blocks[-1][1] + tail[1], tail[2]]
        value = blocks[-1][0] / blocks[-1][1]
        for total, count, upper in blocks:
            if score <= upper:
                value = total / count
                break
        return round(min(0.95, max(0.05, value)), 3)

    def report(self) -> CalibrationReport:
        items = list(self._items.values())
        graded = [p for p in items if p.verdict != "unknown"]
        correct = [p for p in graded if p.verdict == "correct"]
        top3 = [p for p in graded if p.rank_of_actual is not None and p.rank_of_actual <= 3]
        brier = (sum((p.score - (1.0 if p.verdict == "correct" else 0.0)) ** 2 for p in graded) / len(graded)
                 if graded else None)
        bins = []
        for low in (0.0, 0.2, 0.4, 0.6, 0.8):
            high = low + 0.2
            members = [p for p in graded if low <= p.score < high or (high >= 1.0 and p.score == 1.0)]
            bins.append(CalibrationBin(
                low=low, high=round(high, 1), count=len(members),
                mean_score=round(sum(p.score for p in members) / len(members), 3) if members else None,
                accuracy=round(sum(p.verdict == "correct" for p in members) / len(members), 3) if members else None))
        calibrated = len(graded) >= self.min_samples
        return CalibrationReport(
            samples=len(items), graded=len(graded),
            top1_accuracy=round(len(correct) / len(graded), 3) if graded else None,
            top3_accuracy=round(len(top3) / len(graded), 3) if graded else None,
            brier_score=round(brier, 4) if brier is not None else None,
            unresolved=len(items) - len(graded),
            overconfident_misses=tuple(p.incident_id for p in graded if p.score >= 0.7 and p.verdict == "incorrect"),
            bins=tuple(bins), calibrated=calibrated,
            note=("Enough graded outcomes to compare scores with observed accuracy." if calibrated else
                  f"Only {len(graded)} graded outcome(s); scores are rankings, not probabilities "
                  f"(need {self.min_samples})."))
