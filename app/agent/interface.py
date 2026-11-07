"""Contract between the backend and the AI agent.

The backend owns the database and HTTP layer; the agent owns reasoning and memory
(Groq + Hindsight in the real implementation). The agent only ever sees the plain
Pydantic models below, never database rows, so either side can change internals
without touching the other.

Implementing an agent
---------------------
Write a class with these three methods (no need to inherit from anything):

    class MyAgent:
        name = "my-agent"

        async def analyze(self, incident: IncidentContext) -> AsyncIterator[AgentEvent]:
            yield MemoryEvent(...)      # 0..n recalled past incidents, first
            yield TokenEvent(text="...")  # 0..n text chunks, streamed to the UI as they arrive
            yield SuggestionEvent(...)  # exactly one structured suggestion, last

        async def learn_from_resolution(self, incident: IncidentContext, resolution: ResolutionContext) -> None:
            ...  # e.g. Hindsight retain

        async def ingest_history(self, incidents: list[IncidentContext]) -> None:
            ...  # bulk-load resolved past incidents into memory (called by POST /admin/seed)

Rules for `analyze` (an async generator, i.e. `async def` containing `yield`):
- You don't need to yield DoneEvent; the backend sends "done" when your generator finishes.
  If you do yield one, the backend treats it as the end of the stream.
- To report a failure, either raise (the backend sends an "error" event with code "agent_error")
  or yield ErrorEvent(code=..., message=...) and return.
- Yielding plain dicts like {"type": "token", "text": "..."} also works; they are validated
  against these models.

Optional hook: `async def reset(self) -> None` is called by POST /admin/reset if present.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Literal, Protocol, Union, runtime_checkable

from pydantic import BaseModel, Field, TypeAdapter


# ---------------------------------------------------------------------------
# Inputs the agent receives
# ---------------------------------------------------------------------------


class IncidentContext(BaseModel):
    """Everything the agent knows about an incident. For resolved incidents the
    resolution fields are filled in."""

    id: str
    title: str
    service: str
    severity: Literal["SEV1", "SEV2", "SEV3", "SEV4"]
    status: Literal["open", "investigating", "resolved"]
    alert_text: str
    logs: str = ""
    created_at: datetime
    resolved_at: datetime | None = None
    root_cause: str | None = None
    resolution: str | None = None
    failed_attempts: list[str] = Field(default_factory=list)
    time_to_resolve_minutes: int | None = None
    notes: str | None = None


class ResolutionContext(BaseModel):
    """What the on-call engineer reported when resolving the incident."""

    root_cause: str
    fix_that_worked: str
    what_failed: list[str] = Field(default_factory=list)
    notes: str | None = None


# ---------------------------------------------------------------------------
# Events the agent streams back from analyze()
# ---------------------------------------------------------------------------


WARNING_KINDS = ("severity", "certainty", "history", "unsafe_history", "contradiction", "catalog", "approval", "memory")


class WarningDetail(BaseModel):
    """One warning with its category, so the UI can group them. Same text as the matching `warnings` entry."""

    text: str
    kind: str = Field(description=f"One of: {', '.join(WARNING_KINDS)} (a new kind from the engine is passed through)")


class Suggestion(BaseModel):
    likely_root_cause: str
    steps: list[str] = Field(default_factory=list, description="Ordered remediation steps")
    warnings: list[str] = Field(default_factory=list, description="Things to avoid, e.g. fixes that failed before")
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_calibrated: bool = Field(
        default=False,
        description="True when `confidence` is a calibrated probability; False when it is only a ranking score",
    )
    based_on: list[str] = Field(default_factory=list, description="IDs of past incidents this is based on")
    origin_frame: str | None = Field(
        default=None, description='Stack frame where the failure started, e.g. "ChargebackSyncJob.sync (ChargebackSyncJob.java:92)"',
    )
    warning_details: list[WarningDetail] = Field(
        default_factory=list, description="`warnings` with a category each, in the same order",
    )


class MemoryEvent(BaseModel):
    """A past incident recalled from memory."""

    type: Literal["memory"] = "memory"
    past_incident_id: str
    title: str
    service: str
    summary: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    root_cause: str | None = None
    resolution: str | None = None
    failed_attempts: list[str] = Field(default_factory=list)
    time_to_resolve_minutes: int | None = None


class TokenEvent(BaseModel):
    """A chunk of the agent's streamed explanation."""

    type: Literal["token"] = "token"
    text: str


class SuggestionEvent(Suggestion):
    type: Literal["suggestion"] = "suggestion"


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    incident_id: str | None = None
    memories_used: int = 0


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


AgentEvent = Annotated[
    Union[MemoryEvent, TokenEvent, SuggestionEvent, DoneEvent, ErrorEvent],
    Field(discriminator="type"),
]

agent_event_adapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)


# ---------------------------------------------------------------------------
# The service itself
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentService(Protocol):
    name: str

    def analyze(self, incident: IncidentContext) -> AsyncIterator[AgentEvent]:
        """Stream memory -> token* -> suggestion events. Implement as `async def` with `yield`."""
        ...

    async def learn_from_resolution(self, incident: IncidentContext, resolution: ResolutionContext) -> None:
        """Store what worked / what failed so future analyses can recall it."""
        ...

    async def ingest_history(self, incidents: list[IncidentContext]) -> None:
        """Bulk-load resolved historical incidents into memory."""
        ...
