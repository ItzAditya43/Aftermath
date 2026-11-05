"""Load data/seed_incidents.json into the database and the agent's memory."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import Session, delete, select

from app.agent.interface import AgentService
from app.models import Incident, IncidentStatus, MemoryReference, Severity, TimelineEvent, TimelineType, utcnow
from app.schemas import ResetResponse, SeedResponse
from app.services import to_context

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_incidents.json"
# After rebasing, the newest seed incident is between 5 and 12 days old.
NEWEST_SEED_AGE = timedelta(days=5)


def load_seed_file(path: Path = SEED_PATH) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)["incidents"]


_ISO_IN_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
_PG_IN_TEXT = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?) UTC")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _shift_text(text: str, shift: timedelta) -> str:
    """Shift timestamps embedded in log text so they line up with the rebased incident dates."""
    if not shift or not text:
        return text

    def iso(match: re.Match) -> str:
        raw = match.group(0)
        moved = _dt(raw) + shift
        fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in raw else "%Y-%m-%dT%H:%M:%S"
        out = moved.strftime(fmt)
        return (out[:-3] if "." in raw else out) + "Z"

    def pg(match: re.Match) -> str:
        moved = datetime.fromisoformat(match.group(1)) + shift
        return moved.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"

    return _PG_IN_TEXT.sub(pg, _ISO_IN_TEXT.sub(iso, text))


def rebase_shift(items: list[dict], now: datetime | None = None) -> timedelta:
    """Shift by whole weeks so that weekdays (e.g. "Friday deploy") and times of day are preserved."""
    newest = max(_dt(item["created_at"]) for item in items)
    target = (now or utcnow()) - NEWEST_SEED_AGE
    return timedelta(weeks=(target - newest) // timedelta(weeks=1))


async def seed_database(
    session: Session, agent: AgentService, *, rebase_dates: bool = True, path: Path = SEED_PATH
) -> SeedResponse:
    items = load_seed_file(path)
    shift = rebase_shift(items) if rebase_dates else timedelta(0)
    existing = set(session.exec(select(Incident.id)).all())

    inserted: list[Incident] = []
    for item in items:
        if item["id"] in existing:
            continue
        incident = Incident(
            id=item["id"],
            title=item["title"],
            service=item["service"],
            severity=Severity(item["severity"]),
            status=IncidentStatus(item["status"]),
            alert_text=item["alert_text"],
            logs=_shift_text(item["logs"], shift),
            created_at=_dt(item["created_at"]) + shift,
            resolved_at=_dt(item["resolved_at"]) + shift if item.get("resolved_at") else None,
            root_cause=item.get("root_cause"),
            resolution=item.get("resolution"),
            failed_attempts=item.get("failed_attempts", []),
            time_to_resolve_minutes=item.get("time_to_resolve_minutes"),
            notes=item.get("notes"),
        )
        session.add(incident)
        for event in item.get("timeline", []):
            session.add(
                TimelineEvent(
                    incident_id=incident.id,
                    timestamp=_dt(event["timestamp"]) + shift,
                    type=TimelineType(event["type"]),
                    message=event["message"],
                )
            )
        for ref in item.get("memories_used", []):
            session.add(
                MemoryReference(
                    incident_id=incident.id,
                    past_incident_id=ref["past_incident_id"],
                    summary=ref["summary"],
                    relevance_score=ref["relevance_score"],
                    used_at=_dt(ref["used_at"]) + shift,
                )
            )
        inserted.append(incident)
    session.commit()

    contexts = [to_context(i) for i in inserted]
    if contexts:
        await agent.ingest_history(contexts)
    return SeedResponse(seeded=len(inserted), skipped=len(items) - len(inserted), ingested=len(contexts))


async def reset_database(session: Session, agent: AgentService) -> ResetResponse:
    deleted = {}
    for name, model in (("memory_references", MemoryReference), ("timeline_events", TimelineEvent), ("incidents", Incident)):
        result = session.exec(delete(model))
        deleted[name] = result.rowcount or 0
    session.commit()
    reset = getattr(agent, "reset", None)
    if callable(reset):
        await reset()
    return ResetResponse(deleted=deleted)


async def warm_agent_from_db(session: Session, agent: AgentService) -> int:
    """Re-load resolved incidents into an agent whose memory doesn't survive restarts (the mock)."""
    resolved = session.exec(select(Incident).where(Incident.status == IncidentStatus.resolved)).all()
    await agent.ingest_history([to_context(i) for i in resolved])
    return len(resolved)
