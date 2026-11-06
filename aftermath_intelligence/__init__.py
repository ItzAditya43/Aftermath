"""Backend-independent intelligence components for Aftermath.

Primary entry point: `InvestigationEngine` (async, bounded, auditable). The older synchronous
`InvestigationAgent` / `IncidentIntelligenceEngine` remain for offline use and compatibility.
"""

from .agent import InvestigationAgent, InvestigationResult, InvestigationStatus
from .analysis import AnalysisReport, analyze_events
from .correlation import CorrelationReport, correlate_events
from .evidence import Evidence, EvidenceLedger
from .evaluation import (BeforeAfterEvaluation, EvaluationCase, EvaluationMetrics,
                         compare_before_after, evaluate_cases)
from .engine import EvidenceSource, IncidentIntelligenceEngine
from .changes import ChangeRecord, ChangeSuspect, correlate_changes
from .history import HistoricalContextAdapter
from .investigation import (IncidentCase, InvestigationEngine, InvestigationLimits, InvestigationReport, Phase,
                            ProgressUpdate)
from .learning import ResolutionEvaluation, evaluate_resolution_report
from .memory import IncidentMemory, InProcessMemory, MemoryQuery, MemoryRecord, RecalledIncident
from .store import FileMemory, PersistentCalibration
from .presentation import deterministic_narrative, suggestion_fields
from .tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec
from .ingestion import IngestionResult, ingest_text
from .models import NormalizedEvent, Severity
from .postmortem import IncidentPostmortem, build_postmortem
from .remediation import RemediationRecommendation, recommend_remediation
from .reporting import IncidentReport
from .similarity import (HistoricalIncident, SimilarIncident, cluster_historical_incidents,
                         detect_historical_contradictions, find_similar_incidents)
from .resolution import ResolutionInput, ResolutionRecord, ResolutionStatus, evaluate_resolution

__all__ = [
    "ChangeRecord",
    "ChangeSuspect",
    "IncidentCase",
    "IncidentMemory",
    "InProcessMemory",
    "InvestigationEngine",
    "InvestigationLimits",
    "InvestigationReport",
    "MemoryQuery",
    "MemoryRecord",
    "Phase",
    "ProgressUpdate",
    "RecalledIncident",
    "ResolutionEvaluation",
    "RiskLevel",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "correlate_changes",
    "deterministic_narrative",
    "evaluate_resolution_report",
    "suggestion_fields",
    "AnalysisReport",
    "BeforeAfterEvaluation",
    "CorrelationReport",
    "EvaluationCase",
    "EvaluationMetrics",
    "Evidence",
    "EvidenceLedger",
    "EvidenceSource",
    "IncidentIntelligenceEngine",
    "IncidentReport",
    "IncidentPostmortem",
    "HistoricalContextAdapter",
    "HistoricalIncident",
    "InvestigationAgent",
    "InvestigationResult",
    "InvestigationStatus",
    "IngestionResult",
    "NormalizedEvent",
    "RemediationRecommendation",
    "ResolutionInput",
    "ResolutionRecord",
    "ResolutionStatus",
    "Severity",
    "SimilarIncident",
    "analyze_events",
    "cluster_historical_incidents",
    "compare_before_after",
    "correlate_events",
    "detect_historical_contradictions",
    "evaluate_cases",
    "find_similar_incidents",
    "build_postmortem",
    "evaluate_resolution",
    "ingest_text",
    "recommend_remediation",
    "FileMemory",
    "PersistentCalibration",
]
