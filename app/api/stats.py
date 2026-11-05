from collections import Counter, defaultdict
from statistics import mean

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlmodel import col, select

from app.api.deps import AgentDep, SettingsDep
from app.db import SessionDep
from app.models import Incident, IncidentStatus, MemoryReference
from app.schemas import HealthOut, ResolutionPoint, StatsOut, TtrBucket

router = APIRouter(tags=["stats"])


def _avg(values: list[int]) -> float | None:
    return round(mean(values), 1) if values else None


@router.get("/stats", response_model=StatsOut)
def get_stats(session: SessionDep) -> StatsOut:
    """Totals plus time-to-resolve over time, so the demo can show resolution time dropping as memory grows."""
    incidents = session.exec(select(Incident).order_by(col(Incident.created_at))).all()
    assisted = set(session.exec(select(MemoryReference.incident_id).distinct()).all())

    status_counts = Counter(i.status for i in incidents)
    resolved = sorted(
        (i for i in incidents if i.status == IncidentStatus.resolved and i.time_to_resolve_minutes is not None),
        key=lambda i: i.resolved_at or i.created_at,
    )

    buckets: dict[str, list[int]] = defaultdict(list)
    for i in resolved:
        buckets[(i.resolved_at or i.created_at).strftime("%Y-%m")].append(i.time_to_resolve_minutes)

    return StatsOut(
        total_incidents=len(incidents),
        open=status_counts[IncidentStatus.open],
        investigating=status_counts[IncidentStatus.investigating],
        resolved=status_counts[IncidentStatus.resolved],
        avg_time_to_resolve_minutes=_avg([i.time_to_resolve_minutes for i in resolved]),
        avg_ttr_with_memory=_avg([i.time_to_resolve_minutes for i in resolved if i.id in assisted]),
        avg_ttr_without_memory=_avg([i.time_to_resolve_minutes for i in resolved if i.id not in assisted]),
        by_service=dict(sorted(Counter(i.service for i in incidents).items())),
        ttr_over_time=[
            TtrBucket(period=period, resolved=len(values), avg_ttr_minutes=_avg(values))
            for period, values in sorted(buckets.items())
        ],
        resolutions=[
            ResolutionPoint(
                id=i.id,
                title=i.title,
                service=i.service,
                resolved_at=i.resolved_at or i.created_at,
                time_to_resolve_minutes=i.time_to_resolve_minutes,
                memory_assisted=i.id in assisted,
            )
            for i in resolved
        ],
    )


@router.get("/health", response_model=HealthOut, tags=["health"])
def health(request: Request, session: SessionDep, settings: SettingsDep, agent: AgentDep) -> HealthOut:
    try:
        session.exec(text("SELECT 1"))
        database = "ok"
    except Exception:  # pragma: no cover
        database = "error"
    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        agent_mode=settings.agent_mode,
        agent=getattr(agent, "name", type(agent).__name__),
        database=database,
    )
