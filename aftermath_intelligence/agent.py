from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .analysis import AnalysisReport, analyze_events
from .correlation import CorrelationReport, correlate_events
from .evidence import Evidence, EvidenceKind, EvidenceLedger
from .history import HistoricalContextAdapter
from .hypotheses import (RootCauseHypothesis, generate_deterministic_hypotheses,
                         generate_llm_hypotheses)
from .planner import DiagnosticRequest, InvestigationPlan, plan_diagnostics, plan_with_llm
from .providers import ProviderError, ProviderRouter
from .tools import ToolAdapter, ToolResult, validate_tool_arguments
from .models import NormalizedEvent
from .redaction import redact_sensitive_data, redact_sensitive_text
from .similarity import SimilarIncident, find_similar_incidents


class InvestigationStatus(StrEnum):
    COMPLETED = "completed"
    NEEDS_EVIDENCE = "needs_evidence"
    AWAITING_APPROVAL = "awaiting_approval"
    PARTIAL = "partial"


class ReplayEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int
    kind: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class InvestigationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: str
    status: InvestigationStatus
    analysis: AnalysisReport
    correlations: CorrelationReport
    evidence: tuple[Evidence, ...]
    hypotheses: tuple[RootCauseHypothesis, ...]
    plan: InvestigationPlan
    tool_results: tuple[ToolResult, ...]
    replay: tuple[ReplayEvent, ...]
    provider: str | None = None
    provider_model: str | None = None
    provider_error: str | None = None
    similar_incidents: tuple[SimilarIncident, ...] = ()
    history_error: str | None = None
    tool_error: str | None = None


class InvestigationAgent:
    """Bounded investigation loop that never executes state-changing tools."""

    def __init__(self, *, router: ProviderRouter | None = None, max_steps: int = 4,
                 confidence_stop: float = 0.85, max_evidence_for_llm: int = 50):
        if max_steps < 1 or max_steps > 12:
            raise ValueError("max_steps must be between 1 and 12")
        self.router = router
        self.max_steps = max_steps
        self.confidence_stop = confidence_stop
        self.max_evidence_for_llm = max_evidence_for_llm

    def run(self, incident_id: str, events: tuple[NormalizedEvent, ...] | list[NormalizedEvent], *,
            tools: ToolAdapter | None = None, history: HistoricalContextAdapter | None = None,
            use_llm: bool = True) -> InvestigationResult:
        bounded_events = tuple(events)
        analysis = analyze_events(bounded_events)
        correlations = correlate_events(bounded_events)
        ledger = EvidenceLedger()
        ledger.add_event_facts(bounded_events)
        ledger.add_analysis(analysis)
        ledger.add_correlations(correlations)
        replay: list[ReplayEvent] = []
        similar_incidents: tuple[SimilarIncident, ...] = ()
        history_error: str | None = None
        if history:
            try:
                history_cases = history.retrieve_incidents(bounded_events, limit=10)
                similar_incidents = find_similar_incidents(bounded_events, history_cases)
                for match in similar_incidents:
                    ledger.add_historical_match(incident_id=match.incident_id, similarity=match.similarity,
                                                shared_fingerprints=match.shared_fingerprints,
                                                contradicts=match.contradicts_verified_conclusion)
                _record(replay, "historical_context", f"Retrieved {len(similar_incidents)} similar prior incidents.", {})
            except Exception as exc:
                history_error = type(exc).__name__
                _record(replay, "historical_context_failed", "Historical context was unavailable; current evidence analysis continued.", {})
        hypotheses = list(generate_deterministic_hypotheses(ledger.items, correlations))
        _record(replay, "analysis", f"Analyzed {len(bounded_events)} normalized events.", {
            "error_groups": len(analysis.error_groups), "correlation_edges": len(correlations.edges),
            "input_event_count": len(bounded_events),
            "input_event_digest": sha256("\0".join(event.event_id for event in bounded_events).encode()).hexdigest(),
            "sources": ",".join(sorted({event.source for event in bounded_events})),
        })
        _record(replay, "hypotheses_generated", f"Produced {len(hypotheses)} deterministic hypotheses.", {
            "hypothesis_ids": ",".join(item.hypothesis_id for item in hypotheses),
        })
        provider_name: str | None = None
        provider_model: str | None = None
        provider_error: str | None = None
        tool_error: str | None = None

        if use_llm and self.router and ledger.items:
            try:
                ai_hypotheses, response = generate_llm_hypotheses(
                    _compact_evidence(ledger.items, self.max_evidence_for_llm), self.router, max_hypotheses=4)
                hypotheses.extend(ai_hypotheses)
                provider_name, provider_model = response.provider, response.model
                _record(replay, "llm_hypotheses", f"Received {len(ai_hypotheses)} evidence-linked hypotheses.", {
                    "provider": response.provider, "model": response.model, "cached": response.cached,
                })
            except (ProviderError, ValueError) as exc:
                provider_error = str(exc)[:300]
                _record(replay, "llm_unavailable", "LLM reasoning failed; deterministic findings remain available.", {})

        try:
            specs = tools.list_tools() if tools else ()
        except Exception as exc:
            specs = ()
            tool_error = f"tool registry unavailable: {type(exc).__name__}"
            _record(replay, "tool_registry_failed", "Connector registry could not be read; no diagnostics were run.", {})
        executed: set[str] = set()
        results: list[ToolResult] = []
        pending_approval: list[DiagnosticRequest] = []
        plan = plan_diagnostics(hypotheses, specs)
        if use_llm and self.router and specs and hypotheses:
            try:
                llm_plan, response = plan_with_llm(hypotheses, _compact_evidence(ledger.items, 40), specs, self.router)
                merged = {request.tool_name: request for request in plan.requests}
                merged.update({request.tool_name: request for request in llm_plan.requests})
                plan = InvestigationPlan(requests=tuple(merged.values()),
                                         unresolved_questions=plan.unresolved_questions + llm_plan.unresolved_questions)
                provider_name, provider_model = response.provider, response.model
                _record(replay, "llm_plan", f"Validated {len(llm_plan.requests)} read-only diagnostic requests.", {
                    "provider": response.provider, "model": response.model, "cached": response.cached,
                })
            except (ProviderError, ValueError) as exc:
                provider_error = provider_error or str(exc)[:300]
                _record(replay, "llm_plan_unavailable", "Deterministic planning remains available.", {})
        steps = 0
        while tools and steps < self.max_steps and plan.requests:
            available_requests = [request for request in plan.requests if request.tool_name not in executed]
            if not available_requests:
                break
            for request in available_requests:
                if steps >= self.max_steps:
                    break
                steps += 1
                spec = next((item for item in specs if item.name == request.tool_name), None)
                if spec is None:
                    _record(replay, "tool_missing", "Planned tool is not present in the connector registry.", {"tool": request.tool_name})
                    executed.add(request.tool_name)
                    continue
                _record(replay, "tool_requested", request.purpose, {
                    "tool": request.tool_name, "risk": request.risk.value,
                    "arguments": str(redact_sensitive_data(request.arguments))[:300],
                })
                if request.requires_approval or not spec.read_only or spec.risk.value != "read_only":
                    pending_approval.append(request)
                    executed.add(request.tool_name)
                    _record(replay, "approval_required", "Tool was not run because it is not declared read-only.", {
                        "tool": spec.name, "risk": spec.risk.value,
                    })
                    continue
                try:
                    arguments = validate_tool_arguments(spec, dict(request.arguments))
                    tool_result = tools.execute(spec.name, arguments)
                    tool_result = _sanitize_tool_result(tool_result)
                    results.append(tool_result)
                    if tool_result.success:
                        ledger.add_tool_result(tool_name=tool_result.tool_name, summary=tool_result.summary,
                                               source_event_ids=tool_result.source_event_ids,
                                               attributes=_safe_attributes(tool_result.data))
                        hypotheses.extend(generate_deterministic_hypotheses(ledger.items, correlations))
                        kind = "tool_succeeded"
                    else:
                        kind = "tool_failed"
                    _record(replay, kind, tool_result.summary[:300], {"tool": spec.name})
                except Exception as exc:  # adapter failures are retained in the replay, not swallowed
                    tool_result = ToolResult(tool_name=spec.name, success=False, summary="Diagnostic tool failed.",
                                             error=type(exc).__name__)
                    results.append(tool_result)
                    _record(replay, "tool_failed", tool_result.error or "Tool failed.", {"tool": spec.name})
                executed.add(spec.name)
            plan = plan_diagnostics(hypotheses, specs, excluded_tools=executed)
            if any(h.confidence >= self.confidence_stop for h in hypotheses) and not plan.requests:
                break

        if tools is None:
            plan = plan_diagnostics(hypotheses, (),)
        remaining_requests = tuple(request for request in plan.requests if request.tool_name not in executed)
        final_plan = InvestigationPlan(requests=tuple(pending_approval) + remaining_requests,
                                       unresolved_questions=plan.unresolved_questions)
        if pending_approval:
            status = InvestigationStatus.AWAITING_APPROVAL
        elif final_plan.unresolved_questions:
            status = InvestigationStatus.NEEDS_EVIDENCE
        elif steps >= self.max_steps and plan.requests:
            status = InvestigationStatus.PARTIAL
        else:
            status = InvestigationStatus.COMPLETED
        _record(replay, "stopped", f"Investigation stopped with status {status.value}.", {
            "steps": steps, "hypotheses": len(hypotheses), "pending_approval": len(pending_approval),
            "status": status.value,
            "final_hypothesis_ids": ",".join(sorted({item.hypothesis_id for item in hypotheses})),
            "evidence_count": len(ledger.items),
        })
        unique_hypotheses = {h.hypothesis_id: h for h in hypotheses}
        return InvestigationResult(
            incident_id=incident_id, status=status, analysis=analysis, correlations=correlations,
            evidence=ledger.items, hypotheses=tuple(sorted(unique_hypotheses.values(), key=lambda h: -h.confidence)),
            plan=final_plan, tool_results=tuple(results), replay=tuple(replay),
            provider=provider_name, provider_model=provider_model, provider_error=provider_error,
            similar_incidents=similar_incidents, history_error=history_error,
            tool_error=tool_error,
        )


def _record(replay: list[ReplayEvent], kind: str, summary: str,
            details: dict[str, str | int | float | bool | None]) -> None:
    replay.append(ReplayEvent(sequence=len(replay) + 1, kind=kind, summary=summary, details=details))


def _safe_attributes(data: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    data = redact_sensitive_data(data)
    return {str(k): v for k, v in data.items() if isinstance(v, (str, int, float, bool)) or v is None}


def _compact_evidence(items: tuple[Evidence, ...], limit: int) -> tuple[Evidence, ...]:
    priority = {
        EvidenceKind.ERROR_PATTERN: 0,
        EvidenceKind.TEMPORAL_SPIKE: 0,
        EvidenceKind.CORRELATION: 1,
        EvidenceKind.TOOL_RESULT: 1,
        EvidenceKind.HISTORICAL: 2,
        EvidenceKind.OBSERVATION: 3,
        EvidenceKind.DEPLOYMENT: 1,
    }
    return tuple(sorted(items, key=lambda item: (priority.get(item.kind, 1), item.evidence_id))[:max(0, limit)])


def _sanitize_tool_result(result: ToolResult) -> ToolResult:
    return result.model_copy(update={
        "summary": redact_sensitive_text(result.summary),
        "error": redact_sensitive_text(result.error) if result.error else None,
        "data": redact_sensitive_data(result.data),
    })
