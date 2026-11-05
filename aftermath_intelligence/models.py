from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Severity(StrEnum):
    TRACE = "trace"
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class NormalizedEvent(BaseModel):
    """A normalized event that retains enough provenance to audit extraction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    incident_id: str = "unknown"
    timestamp: datetime | None = None
    original_timestamp: str | None = None
    service: str | None = None
    environment: str | None = None
    severity: Severity = Severity.UNKNOWN
    event_type: str = "log"
    message: str
    exception_type: str | None = None
    error_code: str | None = None
    stack_trace: str | None = None
    source: str = "text"
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: str

    @field_validator("timestamp")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def stable_event_id(source: str, raw: str, incident_id: str = "unknown") -> str:
    return sha256(f"{incident_id}\0{source}\0{raw}".encode("utf-8", errors="replace")).hexdigest()[:24]
