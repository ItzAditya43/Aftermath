from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResolutionStatus(StrEnum):
    UNRESOLVED = "unresolved"
    FAILED = "failed"
    TEMPORARY = "temporary"
    PARTIAL = "partial"
    VERIFIED = "verified"


class ResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: str
    action_taken: str | None = None
    errors_before: int = Field(ge=0)
    errors_after: int = Field(ge=0)
    stable_for_minutes: int = Field(default=0, ge=0)
    required_stable_minutes: int = Field(default=30, ge=0)
    regression_observed: bool = False
    human_verified: bool = False
    evidence_ids: tuple[str, ...] = ()
    notes: str = ""


class ResolutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: str
    status: ResolutionStatus
    action_taken: str | None
    errors_before: int
    errors_after: int
    improvement_ratio: float
    human_verified: bool
    evidence_ids: tuple[str, ...]
    notes: str

    def memory_ready_payload(self) -> dict[str, Any]:
        """Neutral payload for the team's future memory adapter; no Hindsight calls."""
        return self.model_dump(mode="json")


def evaluate_resolution(value: ResolutionInput) -> ResolutionRecord:
    before, after = value.errors_before, value.errors_after
    improvement = 0.0 if before == 0 else max(0.0, min(1.0, (before - after) / before))
    if not value.action_taken:
        status = ResolutionStatus.UNRESOLVED
    elif value.regression_observed:
        status = ResolutionStatus.TEMPORARY
    elif after >= before and before > 0:
        status = ResolutionStatus.FAILED
    elif value.human_verified:
        status = ResolutionStatus.VERIFIED
    elif improvement > 0 and value.stable_for_minutes >= value.required_stable_minutes:
        status = ResolutionStatus.VERIFIED
    elif improvement > 0 and after == 0:
        status = ResolutionStatus.TEMPORARY
    elif improvement > 0:
        status = ResolutionStatus.PARTIAL
    else:
        status = ResolutionStatus.UNRESOLVED
    return ResolutionRecord(incident_id=value.incident_id, status=status, action_taken=value.action_taken,
                            errors_before=before, errors_after=after, improvement_ratio=improvement,
                            human_verified=value.human_verified, evidence_ids=value.evidence_ids, notes=value.notes)
