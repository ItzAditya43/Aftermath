"""Business logic shared by the routers. Routers stay thin; this module owns DB writes."""

import logging
import math
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, delete, select

from app.agent.interface import (
    AgentService,
    DoneEvent,
    ErrorEvent,
    IncidentContext,
    MemoryEvent,
    ResolutionContext,
    SuggestionEvent,
    agent_event_adapter,
)
from app.errors import ConflictError, NotFoundError
from app.models import (
    Incident,
    IncidentStatus,
    MemoryReference,
    TimelineEvent,
    TimelineType,
    utcnow,
)
from app.schemas import IncidentCreate, IncidentDetail, ResolveRequest, TimelineEventOut

logger = logging.getLogger(__name__)

ID_PREFIX = "INC-"
# Seed incidents occupy INC-1001..INC-1020; live incidents are numbered after them so that
# seeding after creating incidents never collides.
FIRST_LIVE_ID_NUMBER = 1021


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _id_number(incident_id: str) -> int:
    try:
        return int(incident_id.removeprefix(ID_PREFIX))
    except ValueError:
        return 0


def next_incident_id(session: Session) -> str:
    ids = session.exec(select(Incident.id)).all()
    highest = max((_id_number(i) for i in ids), default=0)
    return f"{ID_PREFIX}{max(highest + 1, FIRST_LIVE_ID_NUMBER)}"


def get_incident_or_404(session: Session, incident_id: str) -> Incident:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise NotFoundError(f"Incident {incident_id} not found")
    return incident


def add_timeline(
    session: Session, incident_id: str, type_: TimelineType, message: str, timestamp: datetime | None = None
) -> TimelineEvent:
    event = TimelineEvent(incident_id=incident_id, type=type_, message=message, timestamp=timestamp or utcnow())
    session.add(event)
    return event


def to_context(incident: Incident) -> IncidentContext:
    return IncidentContext(
        id=incident.id,
        title=incident.title,
        service=incident.service,
        severity=incident.severity.value,
        status=incident.status.value,
        alert_text=incident.alert_text,
        logs=incident.logs,
        created_at=incident.created_at,
        resolved_at=incident.resolved_at,
        root_cause=incident.root_cause,
        resolution=incident.resolution,
        failed_attempts=list(incident.failed_attempts or []),
        time_to_resolve_minutes=incident.time_to_resolve_minutes,
        notes=incident.notes,
    )


def incident_detail(session: Session, incident: Incident) -> IncidentDetail:
    timeline = session.exec(
        select(TimelineEvent)
        .where(TimelineEvent.incident_id == incident.id)
        .order_by(col(TimelineEvent.timestamp), col(TimelineEvent.id))
    ).all()
    detail = IncidentDetail.model_validate(incident, from_attributes=True)
    detail.timeline = [TimelineEventOut.model_validate(t) for t in timeline]
    return detail


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


def create_incident(session: Session, data: IncidentCreate) -> Incident:
    incident = Incident(id=next_incident_id(session), **data.model_dump())
    session.add(incident)
    add_timeline(
        session, incident.id, TimelineType.alert, f"[{incident.severity.value}] Alert fired: {incident.alert_text}",
        timestamp=incident.created_at,
    )
    session.commit()
    session.refresh(incident)
    return incident


def list_incidents(
    session: Session, status: IncidentStatus | None, service: str | None, limit: int, offset: int
) -> list[Incident]:
    query = select(Incident)
    if status is not None:
        query = query.where(Incident.status == status)
    if service:
        query = query.where(Incident.service == service)
    query = query.order_by(col(Incident.created_at).desc(), col(Incident.id).desc()).offset(offset).limit(limit)
    return list(session.exec(query).all())


async def resolve_incident(
    session: Session, agent: AgentService, incident: Incident, data: ResolveRequest
) -> tuple[Incident, bool]:
    if incident.status == IncidentStatus.resolved:
        raise ConflictError(f"Incident {incident.id} is already resolved")

    now = utcnow()
    incident.status = IncidentStatus.resolved
    incident.resolved_at = now
    incident.root_cause = data.root_cause
    incident.resolution = data.fix_that_worked
    incident.failed_attempts = list(data.what_failed)
    incident.notes = data.notes
    incident.time_to_resolve_minutes = max(1, math.ceil((now - incident.created_at).total_seconds() / 60))
    for attempt in data.what_failed:
        add_timeline(session, incident.id, TimelineType.action, f"Tried (did not work): {attempt}", timestamp=now)
    add_timeline(
        session, incident.id, TimelineType.resolution,
        f"Resolved in {incident.time_to_resolve_minutes} min. Root cause: {data.root_cause}. Fix: {data.fix_that_worked}",
        timestamp=now,
    )
    session.add(incident)
    session.commit()
    session.refresh(incident)

    resolution = ResolutionContext(**data.model_dump())
    try:
        await agent.learn_from_resolution(to_context(incident), resolution)
        memory_saved = True
    except Exception as exc:  # the incident is resolved either way; memory is best-effort
        logger.exception("agent.learn_from_resolution failed for %s", incident.id)
        add_timeline(session, incident.id, TimelineType.action, f"Could not save resolution to agent memory: {exc}")
        session.commit()
        memory_saved = False
    return incident, memory_saved


# ---------------------------------------------------------------------------
# Analysis (SSE)
# ---------------------------------------------------------------------------


def start_analysis(session: Session, incident: Incident) -> IncidentContext:
    """Mark the incident as being investigated and clear memories from any previous run."""
    if incident.status == IncidentStatus.open:
        incident.status = IncidentStatus.investigating
        session.add(incident)
    session.exec(delete(MemoryReference).where(MemoryReference.incident_id == incident.id))
    add_timeline(session, incident.id, TimelineType.analysis, "Agent started analysis: recalling similar past incidents")
    session.commit()
    session.refresh(incident)
    return to_context(incident)


def _sse(event) -> dict:
    return {"event": event.type, "data": event.model_dump_json()}


async def stream_analysis(engine: Engine, agent: AgentService, incident: IncidentContext) -> AsyncIterator[dict]:
    """Run the agent and translate its events into SSE messages, persisting as we go.

    Uses its own DB sessions because the request-scoped session may be closed while streaming.
    """
    recalled: list[str] = []
    try:
        async for raw in agent.analyze(incident):
            event = raw if not isinstance(raw, dict) else agent_event_adapter.validate_python(raw)

            if isinstance(event, MemoryEvent):
                if event.past_incident_id == incident.id or event.past_incident_id in recalled:
                    continue
                recalled.append(event.past_incident_id)
                with Session(engine) as session:
                    session.add(
                        MemoryReference(
                            incident_id=incident.id,
                            past_incident_id=event.past_incident_id,
                            summary=event.summary,
                            relevance_score=event.relevance_score,
                        )
                    )
                    session.commit()

            elif isinstance(event, SuggestionEvent):
                with Session(engine) as session:
                    row = session.get(Incident, incident.id)
                    if row is not None:
                        row.last_suggestion = event.model_dump(exclude={"type"})
                        session.add(row)
                        recalled_text = f"Recalled {', '.join(recalled)}. " if recalled else "No similar past incidents found. "
                        add_timeline(
                            session, incident.id, TimelineType.analysis,
                            f"{recalled_text}Suggested root cause: {event.likely_root_cause} "
                            f"(confidence {round(event.confidence * 100)}%)",
                        )
                        session.commit()

            elif isinstance(event, ErrorEvent):
                yield _sse(event)
                return

            elif isinstance(event, DoneEvent):
                break

            yield _sse(event)
    except Exception as exc:
        logger.exception("Agent analysis failed for %s", incident.id)
        with Session(engine) as session:
            add_timeline(session, incident.id, TimelineType.analysis, f"Agent analysis failed: {exc}")
            session.commit()
        yield _sse(ErrorEvent(code="agent_error", message=str(exc) or exc.__class__.__name__))
        return

    yield _sse(DoneEvent(incident_id=incident.id, memories_used=len(recalled)))
