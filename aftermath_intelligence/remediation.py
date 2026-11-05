from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .hypotheses import RootCauseHypothesis
from .tools import RiskLevel


class RecommendationStatus(StrEnum):
    RECOMMENDED = "recommended"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class RemediationRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recommendation_id: str
    title: str
    rationale: str
    steps: tuple[str, ...]
    risk: RiskLevel
    requires_approval: bool = True
    status: RecommendationStatus = RecommendationStatus.RECOMMENDED
    hypothesis_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    cautions: tuple[str, ...] = ()


def recommend_remediation(hypotheses: tuple[RootCauseHypothesis, ...] | list[RootCauseHypothesis],
                          *, max_recommendations: int = 5) -> tuple[RemediationRecommendation, ...]:
    """Produce advice only. This module never executes a remediation step."""
    output: list[RemediationRecommendation] = []
    for hypothesis in hypotheses:
        text = f"{hypothesis.title} {hypothesis.explanation}".lower()
        steps: tuple[str, ...]
        title: str
        if "deployment" in text or "release" in text:
            title = "Validate and, if confirmed, roll back the suspected release"
            steps = (
                "Compare the release timestamp and changed services with the first failing events.",
                "Check health and error rates for the affected service and its dependencies.",
                "If the regression is confirmed, request an approved rollback through the deployment owner.",
            )
        elif "database" in text or "sql" in text:
            title = "Check database health and connection-pool pressure"
            steps = (
                "Inspect connection counts, pool wait time, query latency, and database health.",
                "Check for locks or recent schema/query changes tied to the first error timestamp.",
                "Review any pool, query, or database configuration change before applying it.",
            )
        elif "resource" in text or "memory" in text or "capacity" in text:
            title = "Check resource pressure before changing capacity"
            steps = (
                "Inspect container and host memory, CPU, disk, and restart metrics for the incident window.",
                "Compare current limits with recent peak usage and neighboring services.",
                "Propose a limit or capacity change for review; apply only after approval.",
            )
        elif "dependency" in text or "network" in text or "upstream" in text:
            title = "Verify the dependency path and service health"
            steps = (
                "Check dependency health, request latency, timeout rate, and recent connectivity changes.",
                "Compare caller errors with dependency-side logs for the same UTC interval.",
                "Use an approved retry, failover, or configuration change only after confirming the failure mode.",
            )
        elif "authentication" in text or "configuration" in text:
            title = "Validate credentials and configuration provenance"
            steps = (
                "Compare the active configuration version with the last known healthy version.",
                "Check credential expiry and access scope without exposing secret values.",
                "Have the owning engineer review any credential or configuration replacement.",
            )
        else:
            title = f"Gather evidence for: {hypothesis.title}"
            steps = ("Run read-only diagnostics that test this hypothesis.",
                     "Compare the result with the supporting and contradicting evidence before changing state.")
        output.append(RemediationRecommendation(
            recommendation_id=f"remediate:{hypothesis.hypothesis_id}", title=title,
            rationale=hypothesis.explanation, steps=steps, risk=RiskLevel.MEDIUM,
            requires_approval=True, hypothesis_ids=(hypothesis.hypothesis_id,),
            evidence_ids=hypothesis.supporting_evidence_ids,
            cautions=("This is an unverified hypothesis; do not treat the recommendation as authorization to act.",),
        ))
        if len(output) >= max_recommendations:
            break
    return tuple(output)
