"""Database tables."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.db import UTCDateTime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Severity(str, Enum):
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


class IncidentStatus(str, Enum):
    open = "open"
    investigating = "investigating"
    resolved = "resolved"


class TimelineType(str, Enum):
    alert = "alert"
    analysis = "analysis"
    action = "action"
    resolution = "resolution"


class Incident(SQLModel, table=True):
    id: str = Field(primary_key=True, description='Human-readable ID, e.g. "INC-1001"')
    title: str
    service: str = Field(index=True)
    severity: Severity
    status: IncidentStatus = Field(default=IncidentStatus.open, index=True)
    alert_text: str
    logs: str = ""
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime, index=True)
    resolved_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    root_cause: str | None = None
    resolution: str | None = None
    failed_attempts: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    time_to_resolve_minutes: int | None = None
    notes: str | None = None
    # Latest structured suggestion from the agent, so the UI can show it again after a reload.
    last_suggestion: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class TimelineEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    incident_id: str = Field(foreign_key="incident.id", index=True)
    timestamp: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    type: TimelineType
    message: str


class MemoryReference(SQLModel, table=True):
    """A past incident the agent recalled while analyzing `incident_id`."""

    id: int | None = Field(default=None, primary_key=True)
    incident_id: str = Field(foreign_key="incident.id", index=True)
    past_incident_id: str = Field(index=True)
    summary: str
    relevance_score: float
    used_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
