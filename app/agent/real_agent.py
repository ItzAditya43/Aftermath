"""Real agent (Groq + Hindsight). Owned by the AI agent workstream; replace this stub.

Selected when AGENT_MODE=real. The backend calls exactly the three methods below. See
app/agent/interface.py for the event types and rules; app/agent/mock_agent.py is a working
reference that emits the same events.

Suggested mapping:
  ingest_history(incidents)          -> Hindsight retain, one memory per resolved incident
                                        (title, service, root_cause, resolution, failed_attempts)
  analyze(incident)                  -> Hindsight recall on alert_text + logs -> yield MemoryEvent per hit,
                                        then Groq streaming completion -> yield TokenEvent per chunk,
                                        then a JSON/tool-call completion -> yield SuggestionEvent.
                                        aftermath_intelligence.ingest_text/analyze_events can pre-digest logs.
  learn_from_resolution(incident, r) -> Hindsight retain (+ optionally reflect)

Settings available on `settings`: groq_api_key, hindsight_api_key, hindsight_bank_id.
"""

from collections.abc import AsyncIterator

from app.agent.interface import AgentEvent, IncidentContext, ResolutionContext
from app.config import Settings


class RealAgent:
    name = "real"

    def __init__(self, settings: Settings):
        raise NotImplementedError(
            "RealAgent is not implemented yet. Set AGENT_MODE=mock, or implement app/agent/real_agent.py."
        )

    async def analyze(self, incident: IncidentContext) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError
        yield  # pragma: no cover  (makes this an async generator)

    async def learn_from_resolution(self, incident: IncidentContext, resolution: ResolutionContext) -> None:
        raise NotImplementedError

    async def ingest_history(self, incidents: list[IncidentContext]) -> None:
        raise NotImplementedError
