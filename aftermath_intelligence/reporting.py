from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from .agent import InvestigationResult
from .evidence import Evidence
from .hypotheses import RootCauseHypothesis
from .remediation import RemediationRecommendation, recommend_remediation
from .similarity import SimilarIncident


class IncidentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str
    summary: str
    observed_facts: tuple[Evidence, ...]
    hypotheses: tuple[RootCauseHypothesis, ...]
    recommendations: tuple[RemediationRecommendation, ...]
    similar_incidents: tuple[SimilarIncident, ...]
    open_questions: tuple[str, ...]
    analysis_stats: dict[str, int]
    provenance: dict[str, str | None]
    limitations: tuple[str, ...]


def build_report(result: InvestigationResult) -> IncidentReport:
    primary = result.hypotheses[0] if result.hypotheses else None
    if primary:
        summary = f"Possible cause: {primary.title} (confidence {primary.confidence:.0%}; unverified)."
    elif result.analysis.event_count:
        summary = f"Analyzed {result.analysis.event_count} events; no supported root-cause hypothesis was produced."
    else:
        summary = "No events were available to analyze."
    facts = tuple(item for item in result.evidence if item.kind.value != "correlation")
    return IncidentReport(
        incident_id=result.incident_id, status=result.status.value, summary=summary,
        observed_facts=facts, hypotheses=result.hypotheses,
        recommendations=recommend_remediation(result.hypotheses),
        similar_incidents=result.similar_incidents,
        open_questions=result.plan.unresolved_questions,
        analysis_stats={"events": result.analysis.event_count,
                        "error_groups": len(result.analysis.error_groups),
                        "rare_errors": len(result.analysis.rare_errors),
                        "error_bursts": len(result.analysis.error_bursts),
                        "error_cooccurrences": len(result.analysis.error_cooccurrences),
                        "repeated_failure_sequences": len(result.analysis.repeated_failure_sequences),
                        "correlation_edges": len(result.correlations.edges),
                        "tool_results": len(result.tool_results)},
        provenance={"provider": result.provider, "model": result.provider_model,
                    "provider_error": result.provider_error, "history_error": result.history_error,
                    "tool_error": result.tool_error},
        limitations=("Hypotheses are not confirmed facts.",
                     "Temporal correlation does not prove causation.",
                     "No connector, database, or memory system is invoked by this standalone package."),
    )
