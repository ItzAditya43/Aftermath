import inspect
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from sqlmodel import col, select
from sse_starlette.sse import EventSourceResponse

from app import services
from app.api.deps import AgentDep
from app.db import SessionDep
from app.errors import NotFoundError, error_responses
from app.models import Incident, IncidentStatus, MemoryReference
from app.schemas import (
    IncidentCreate,
    IncidentDetail,
    IncidentSummary,
    MemoryReferenceOut,
    PastIncidentBrief,
    ResolveRequest,
    ResolveResponse,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])

SSE_EXAMPLE = (
    "event: memory\n"
    'data: {"type":"memory","past_incident_id":"INC-1009","title":"payments-api connection pool exhausted '
    'during refund batch","service":"payments-api","summary":"Same HikariPool exhaustion; fixed by raising pool '
    'size and closing leaked connections in RefundService","relevance_score":0.93,...}\n\n'
    "event: token\n"
    'data: {"type":"token","text":"This "}\n\n'
    "event: suggestion\n"
    'data: {"type":"suggestion","likely_root_cause":"...","steps":["..."],"warnings":["..."],'
    '"confidence":0.86,"based_on":["INC-1009","INC-1001"]}\n\n'
    "event: done\n"
    'data: {"type":"done","incident_id":"INC-1021","memories_used":2}\n\n'
)


@router.post("", response_model=IncidentDetail, status_code=201, responses=error_responses(422))
def create_incident(data: IncidentCreate, session: SessionDep) -> IncidentDetail:
    """Open a new incident from a production alert. Also records an `alert` timeline event."""
    incident = services.create_incident(session, data)
    return services.incident_detail(session, incident)


@router.get("", response_model=list[IncidentSummary], responses=error_responses(422))
def list_incidents(
    session: SessionDep,
    status: Annotated[IncidentStatus | None, Query(description="Filter by status")] = None,
    service: Annotated[str | None, Query(description="Filter by service, e.g. payments-api")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Incident]:
    """List incidents, newest first."""
    return services.list_incidents(session, status, service, limit, offset)


@router.get("/{incident_id}", response_model=IncidentDetail, responses=error_responses(404))
def get_incident(incident_id: str, session: SessionDep) -> IncidentDetail:
    """An incident with its full timeline."""
    incident = services.get_incident_or_404(session, incident_id)
    return services.incident_detail(session, incident)


@router.get(
    "/{incident_id}/analyze",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": (
                "Server-Sent Events stream. Event types, in order: `memory` (0..n recalled past incidents), "
                "`token` (text chunks), `suggestion` (structured fix), then `done`. On failure an `error` event "
                "is sent instead and the stream ends. Every `data` payload is JSON with a `type` field."
            ),
            "content": {"text/event-stream": {"example": SSE_EXAMPLE}},
        },
        **error_responses(404),
    },
)
async def analyze_incident(incident_id: str, request: Request, session: SessionDep, agent: AgentDep):
    """Stream the agent's analysis. Use `EventSource` in the browser and close it on `done` or `error`.

    Saves the recalled memories (see `/memories`), the suggestion (`last_suggestion`) and timeline events.
    """
    incident = services.get_incident_or_404(session, incident_id)
    context = services.start_analysis(session, incident)
    return EventSourceResponse(services.stream_analysis(request.app.state.engine, agent, context))


@router.post(
    "/{incident_id}/resolve", response_model=ResolveResponse, responses=error_responses(404, 409, 422)
)
async def resolve_incident(
    incident_id: str, data: ResolveRequest, session: SessionDep, agent: AgentDep
) -> ResolveResponse:
    """Mark the incident resolved, compute time-to-resolve, and teach the agent what worked and what failed."""
    incident = services.get_incident_or_404(session, incident_id)
    incident, memory_saved = await services.resolve_incident(session, agent, incident, data)
    detail = services.incident_detail(session, incident)
    return ResolveResponse(**detail.model_dump(), memory_saved=memory_saved)


@router.get(
    "/{incident_id}/investigation",
    response_model=dict[str, Any],
    responses={
        200: {
            "description": (
                "The latest full investigation as JSON: `headline`, `chain` and causal `graph`, ranked `hypotheses` "
                "with their score breakdown, `evidence`, `anomalies`, `recalled_incidents` and `lessons`, the "
                "`remediation` plan, a step-by-step `replay`, plus `resolution_evaluation` (after resolve) and "
                "`calibration`. Evidence is already redacted."
            )
        },
        **error_responses(404),
    },
)
def get_investigation(incident_id: str, session: SessionDep, agent: AgentDep) -> dict[str, Any]:
    """The full investigation behind the suggestion (real agent only).

    `404` if the incident doesn't exist, hasn't been analysed since the server started, or the agent
    (e.g. the mock) doesn't produce full investigations. Reports are kept for the last 200 analysed incidents.
    """
    incident = services.get_incident_or_404(session, incident_id)
    getter = getattr(agent, "investigation", None)
    data = None
    if callable(getter):
        # Saved reports outlive the DB: ids restart at INC-1021 if it is recreated. Passing created_at lets the
        # agent refuse an older incident's report for a reused id (agents without that parameter still work).
        accepts_created_at = "created_at" in inspect.signature(getter).parameters
        data = getter(incident_id, created_at=incident.created_at) if accepts_created_at else getter(incident_id)
    if data is None:
        raise NotFoundError(
            f"No investigation for {incident_id} yet. Run GET /incidents/{incident_id}/analyze first "
            "(full investigations need AGENT_MODE=real)."
        )
    return data


@router.get("/{incident_id}/memories", response_model=list[MemoryReferenceOut], responses=error_responses(404))
def get_memories(incident_id: str, session: SessionDep) -> list[MemoryReferenceOut]:
    """Past incidents the agent recalled for this incident, most relevant first (the "memory panel")."""
    services.get_incident_or_404(session, incident_id)
    refs = session.exec(
        select(MemoryReference)
        .where(MemoryReference.incident_id == incident_id)
        .order_by(col(MemoryReference.relevance_score).desc())
    ).all()
    past = {
        i.id: i
        for i in session.exec(select(Incident).where(col(Incident.id).in_([r.past_incident_id for r in refs]))).all()
    }
    return [
        MemoryReferenceOut(
            past_incident_id=ref.past_incident_id,
            summary=ref.summary,
            relevance_score=ref.relevance_score,
            used_at=ref.used_at,
            past_incident=PastIncidentBrief.model_validate(past[ref.past_incident_id])
            if ref.past_incident_id in past
            else None,
        )
        for ref in refs
    ]
