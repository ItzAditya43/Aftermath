"""Request and response models for the HTTP API."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.agent.interface import Suggestion
from app.models import IncidentStatus, Severity, TimelineType

# Field limits, shared with app.services so text that grows during redaction is cut back to them.
TITLE_MAX = 200
ALERT_TEXT_MAX = 10_000
LOGS_MAX = 200_000
CAUSE_MAX = 5_000
NOTES_MAX = 10_000

# Leading/trailing whitespace is stripped before length checks, so "   " is rejected as blank.
Stripped = Annotated[str, StringConstraints(strip_whitespace=True)]
NonBlankItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

EXAMPLE_ALERT = {
    "title": "payments-api: connection pool exhausted, checkout payments failing",
    "service": "payments-api",
    "severity": "SEV1",
    "alert_text": (
        "[FIRING] PaymentsApiHighErrorRate: 5xx rate 38.2% (threshold 5%) on payments-api for 5m. "
        "p99 latency 30.4s. HikariPool-1 active=40/40, pending=212."
    ),
    "logs": (
        "2026-11-05T14:02:11.204Z ERROR [http-nio-8080-exec-41] c.a.payments.ChargeController - Charge failed\n"
        "org.springframework.jdbc.CannotGetJdbcConnectionException: Failed to obtain JDBC Connection; "
        "nested exception is java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not "
        "available, request timed out after 30000ms.\n"
        "    at com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:696)\n"
        "    at com.aftermath.payments.refund.RefundService.reconcile(RefundService.java:118)\n"
        "2026-11-05T14:02:12.881Z WARN  [HikariPool-1 housekeeper] c.z.h.p.HikariPool - HikariPool-1 - "
        "Pool stats (total=40, active=40, idle=0, waiting=212)"
    ),
}


class IncidentCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [EXAMPLE_ALERT]})

    title: Stripped = Field(min_length=3, max_length=TITLE_MAX)
    service: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    severity: Severity
    alert_text: Stripped = Field(min_length=1, max_length=ALERT_TEXT_MAX)
    logs: str = Field(default="", max_length=LOGS_MAX)


class IncidentSummary(BaseModel):
    """List item: everything except the (potentially large) logs and the suggestion."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    service: str
    severity: Severity
    status: IncidentStatus
    alert_text: str
    created_at: datetime
    resolved_at: datetime | None = None
    root_cause: str | None = None
    time_to_resolve_minutes: int | None = None


class TimelineEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    type: TimelineType
    message: str


class IncidentDetail(IncidentSummary):
    logs: str
    resolution: str | None = None
    failed_attempts: list[str] = Field(default_factory=list)
    notes: str | None = None
    last_suggestion: Suggestion | None = None
    timeline: list[TimelineEventOut] = Field(default_factory=list)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "root_cause": "Connection leak in RefundService.reconcile() under retry storm exhausted the Hikari pool",
                    "fix_that_worked": "Hotfixed the leak (try-with-resources) and raised maximumPoolSize 50 -> 80",
                    "what_failed": ["Rolling restart of payments-api pods (pool exhausted again within 6 minutes)"],
                    "notes": "Same signature as INC-1001/INC-1009; agent suggestion was correct.",
                }
            ]
        }
    )

    root_cause: Stripped = Field(min_length=1, max_length=CAUSE_MAX)
    fix_that_worked: Stripped = Field(min_length=1, max_length=CAUSE_MAX)
    what_failed: list[NonBlankItem] = Field(default_factory=list, max_length=50)
    notes: Stripped | None = Field(default=None, max_length=NOTES_MAX)


class ResolveResponse(IncidentDetail):
    memory_saved: bool = Field(description="Whether the agent stored this resolution in memory")


class PastIncidentBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    service: str
    severity: Severity
    created_at: datetime
    root_cause: str | None = None
    resolution: str | None = None
    failed_attempts: list[str] = Field(default_factory=list)
    time_to_resolve_minutes: int | None = None


class MemoryReferenceOut(BaseModel):
    past_incident_id: str
    summary: str
    relevance_score: float
    used_at: datetime
    past_incident: PastIncidentBrief | None = Field(
        default=None, description="Null if the past incident is no longer in the database"
    )


class TtrBucket(BaseModel):
    period: str = Field(description='Month, "YYYY-MM"')
    resolved: int
    avg_ttr_minutes: float


class ResolutionPoint(BaseModel):
    id: str
    title: str
    service: str
    resolved_at: datetime
    time_to_resolve_minutes: int
    memory_assisted: bool


class StatsOut(BaseModel):
    total_incidents: int
    open: int
    investigating: int
    resolved: int
    avg_time_to_resolve_minutes: float | None
    avg_ttr_with_memory: float | None = Field(description="Avg TTR of resolved incidents where the agent recalled past incidents")
    avg_ttr_without_memory: float | None
    by_service: dict[str, int]
    ttr_over_time: list[TtrBucket]
    resolutions: list[ResolutionPoint] = Field(description="Every resolved incident, oldest first (for a line chart)")


class SeedResponse(BaseModel):
    seeded: int = Field(description="Incidents inserted (existing IDs are skipped)")
    skipped: int
    ingested: int = Field(description="Incidents passed to agent.ingest_history()")


class ResetResponse(BaseModel):
    deleted: dict[str, int]


class HealthOut(BaseModel):
    status: str
    agent_mode: str
    agent: str
    database: str
