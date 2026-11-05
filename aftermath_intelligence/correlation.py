from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import NormalizedEvent, Severity


@dataclass(frozen=True)
class CorrelationEdge:
    source_event_id: str
    target_event_id: str
    relation: str
    confidence: float
    time_delta_seconds: float | None
    rationale: str


@dataclass(frozen=True)
class EventSequence:
    service: str
    event_ids: tuple[str, ...]
    start: datetime | None
    end: datetime | None


@dataclass(frozen=True)
class CorrelationReport:
    edges: tuple[CorrelationEdge, ...]
    sequences: tuple[EventSequence, ...]


def correlate_events(events: list[NormalizedEvent] | tuple[NormalizedEvent, ...], *,
                     window_seconds: int = 300) -> CorrelationReport:
    """Find close-in-time cross-service errors and deployment-to-error links.

    These are temporal associations, not proof of causation. Every edge carries
    that limitation in its relation and rationale for downstream reporting.
    """
    ordered = sorted(events, key=lambda e: (e.timestamp is None, e.timestamp, e.event_id))
    edges: list[CorrelationEdge] = []
    errors = [e for e in ordered if e.severity in {Severity.ERROR, Severity.CRITICAL} and e.timestamp]
    deployments = [e for e in ordered if _is_deployment(e)]
    for index, left in enumerate(errors):
        for right in errors[index + 1:]:
            delta = (right.timestamp - left.timestamp).total_seconds()
            if delta > window_seconds:
                break
            if left.service == right.service:
                continue
            edges.append(CorrelationEdge(
                source_event_id=left.event_id, target_event_id=right.event_id,
                relation="temporal_co_occurrence", confidence=max(0.25, 1 - delta / (window_seconds * 1.5)),
                time_delta_seconds=delta,
                rationale="Errors in different services occurred within the configured time window; this is association, not causal proof.",
            ))
    for deploy in deployments:
        if not deploy.timestamp:
            continue
        deploy_service = deploy.service
        for error in errors:
            if error.timestamp < deploy.timestamp:
                continue
            delta = (error.timestamp - deploy.timestamp).total_seconds()
            if delta > window_seconds * 12:
                break
            same_service = deploy_service is not None and deploy_service == error.service
            if delta <= window_seconds * 12 and (same_service or delta <= window_seconds):
                edges.append(CorrelationEdge(
                    source_event_id=deploy.event_id, target_event_id=error.event_id,
                    relation="deployment_precedes_error", confidence=0.55 if same_service else 0.35,
                    time_delta_seconds=delta,
                    rationale="A deployment event preceded this error; timing alone does not establish causation.",
                ))
    by_service: dict[str, list[NormalizedEvent]] = {}
    for event in ordered:
        if event.service:
            by_service.setdefault(event.service, []).append(event)
    sequences = tuple(EventSequence(
        service=service, event_ids=tuple(e.event_id for e in service_events),
        start=next((e.timestamp for e in service_events if e.timestamp), None),
        end=next((e.timestamp for e in reversed(service_events) if e.timestamp), None),
    ) for service, service_events in sorted(by_service.items()) if len(service_events) > 1)
    return CorrelationReport(tuple(edges), sequences)


def _is_deployment(event: NormalizedEvent) -> bool:
    kind = event.event_type.lower()
    metadata_keys = {key.lower() for key in event.metadata}
    return kind in {"deployment", "deploy", "release"} or bool(
        metadata_keys & {"commit", "commit_sha", "deployment_id", "release_id", "image_digest"}
    )
