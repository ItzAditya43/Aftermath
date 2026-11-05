from __future__ import annotations

from dataclasses import dataclass

from .agent import InvestigationAgent, InvestigationResult
from .ingestion import IngestionResult, ingest_text
from .history import HistoricalContextAdapter
from .reporting import IncidentReport, build_report
from .providers import ProviderRouter
from .tools import ToolAdapter


@dataclass(frozen=True)
class EvidenceSource:
    name: str
    content: str


@dataclass(frozen=True)
class EngineResult:
    ingestion: tuple[IngestionResult, ...]
    investigation: InvestigationResult
    report: IncidentReport


class IncidentIntelligenceEngine:
    """Small facade suitable for a future FastAPI service or offline demo."""

    def __init__(self, *, router: ProviderRouter | None = None, max_steps: int = 4):
        self.agent = InvestigationAgent(router=router, max_steps=max_steps)

    def investigate(self, incident_id: str, sources: tuple[EvidenceSource, ...] | list[EvidenceSource], *,
                    tools: ToolAdapter | None = None, history: HistoricalContextAdapter | None = None,
                    use_llm: bool = True) -> EngineResult:
        ingested = tuple(ingest_text(source.content, incident_id=incident_id, source=source.name)
                         for source in sources)
        events = tuple(event for result in ingested for event in result.events)
        investigation = self.agent.run(incident_id, events, tools=tools, history=history, use_llm=use_llm)
        return EngineResult(ingestion=ingested, investigation=investigation,
                            report=build_report(investigation))
