from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from .agent import InvestigationResult
from .remediation import recommend_remediation
from .resolution import ResolutionRecord


class TimelineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime | None
    description: str
    event_id: str | None = None


class IncidentPostmortem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str
    timeline: tuple[TimelineEntry, ...]
    root_cause_candidates: tuple[str, ...]
    resolution: ResolutionRecord | None
    follow_up_actions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]


def build_postmortem(result: InvestigationResult, *, resolution: ResolutionRecord | None = None) -> IncidentPostmortem:
    event_timeline: dict[str, TimelineEntry] = {}
    aggregate_timeline: list[TimelineEntry] = []
    for evidence in result.evidence:
        timestamp = evidence.observed_at
        source_timestamp = evidence.attributes.get("timestamp")
        if isinstance(source_timestamp, str):
            try:
                timestamp = datetime.fromisoformat(source_timestamp.replace("Z", "+00:00"))
            except ValueError:
                pass
        if evidence.event_ids:
            for event_id in evidence.event_ids:
                event_timeline.setdefault(event_id, TimelineEntry(timestamp=timestamp,
                                                                   description=evidence.summary,
                                                                   event_id=event_id))
        else:
            aggregate_timeline.append(TimelineEntry(timestamp=evidence.observed_at, description=evidence.summary))
    events = sorted((*event_timeline.values(), *aggregate_timeline),
                    key=lambda item: (item.timestamp is None, item.timestamp, item.event_id or ""))
    if result.hypotheses:
        summary = f"Investigation produced {len(result.hypotheses)} unverified root-cause candidates."
    else:
        summary = "Investigation did not produce a supported root-cause candidate."
    followups = tuple(step for recommendation in recommend_remediation(result.hypotheses)
                      for step in recommendation.steps)
    return IncidentPostmortem(
        incident_id=result.incident_id, summary=summary, timeline=tuple(events),
        root_cause_candidates=tuple(item.title for item in result.hypotheses), resolution=resolution,
        follow_up_actions=followups,
        evidence_ids=tuple(item.evidence_id for item in result.evidence),
        limitations=("Root-cause candidates are not confirmed without verification.",
                     "The timeline includes evidence extraction time when source timestamps are unavailable."),
    )
