"""Backend-independent intelligence components for Aftermath."""

from .agent import InvestigationAgent, InvestigationResult, InvestigationStatus
from .analysis import AnalysisReport, analyze_events
from .correlation import CorrelationReport, correlate_events
from .evidence import Evidence, EvidenceLedger
from .evaluation import (BeforeAfterEvaluation, EvaluationCase, EvaluationMetrics,
                         compare_before_after, evaluate_cases)
from .engine import EvidenceSource, IncidentIntelligenceEngine
from .history import HistoricalContextAdapter
from .ingestion import IngestionResult, ingest_text
from .models import NormalizedEvent, Severity
from .postmortem import IncidentPostmortem, build_postmortem
from .remediation import RemediationRecommendation, recommend_remediation
from .reporting import IncidentReport
from .similarity import (HistoricalIncident, SimilarIncident, cluster_historical_incidents,
                         detect_historical_contradictions, find_similar_incidents)
from .resolution import ResolutionInput, ResolutionRecord, ResolutionStatus, evaluate_resolution

__all__ = [
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
]
