"""The investigation engine: a bounded, auditable, async investigation loop.

Pipeline (each step recorded as a replay event)::

    CREATED -> INGESTING -> ANALYZING -> PLANNING -> INVESTIGATING <-> EVALUATING -> COMPLETED
                                              \\-> AWAITING_APPROVAL / AWAITING_EVIDENCE / FAILED

Deterministic analysis always runs and always produces a result. The LLM (if configured)
only *assesses* deterministic hypotheses and plans diagnostics; its influence on the score
is capped and recorded. Tools run only if the connector declares them read-only.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .analysis import AnalysisReport, analyze_events
from .anomalies import Anomaly, detect_anomalies
from .changes import ChangeRecord, ChangeSuspect, correlate_changes
from .causal import CausalGraph, ChainSummary, analyze_chain, blast_radius, build_graph
from .correlation import correlate_events
from .debate import DebateOutcome, run_debate
from .evidence import Evidence, EvidenceKind, EvidenceLedger
from .ingestion import ingest_text
from .llm import LLMBudget, LLMError, LLMErrorKind, LLMRequest, LLMRouter
from .memory import IncidentMemory, Lesson, MemoryQuery, RecalledIncident, derive_lessons
from .models import NormalizedEvent, Severity
from .prompts import (ASSESS_SYSTEM, PLAN_SYSTEM, LLMAssessment, LLMPlan, assess_prompt, confidence_words, fence)
from .rca import (Hypothesis, apply_contradictions, compare_top, generate_hypotheses, hypothesis_id,
                  rank)
from .redaction import redact_sensitive_data, redact_sensitive_text
from .remediation import RemediationPlan, plan_remediation
from .signatures import BY_ID, Category, SignatureMatch, match_signatures
from .tools import RiskLevel, ToolAdapter, ToolResult, ToolSpec, validate_tool_arguments

logger = logging.getLogger("aftermath.investigation")


# --------------------------------------------------------------------------- inputs & limits

class IncidentCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    title: str
    service: str
    severity: str = "SEV3"
    alert_text: str = ""
    logs: str = ""
    created_at: datetime | None = None
    extra_sources: dict[str, str] = Field(default_factory=dict)      # name -> raw content
    baseline_logs: str = ""
    dependencies: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    changes: tuple[ChangeRecord, ...] = ()                            # from the GitHub / CD connector


class InvestigationLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_steps: int = Field(default=4, ge=1, le=12)
    max_tool_calls: int = Field(default=6, ge=0, le=30)
    max_llm_calls: int = Field(default=3, ge=0, le=12)
    max_seconds: float = Field(default=45.0, gt=0)
    max_evidence_for_llm: int = Field(default=20, ge=5, le=80)
    stop_score: float = Field(default=0.8, ge=0, le=1)


# --------------------------------------------------------------------------- state machine

class Phase(StrEnum):
    CREATED = "created"
    INGESTING = "ingesting"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_EVIDENCE = "awaiting_evidence"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"


_TRANSITIONS: dict[Phase, set[Phase]] = {
    Phase.CREATED: {Phase.INGESTING, Phase.FAILED},
    Phase.INGESTING: {Phase.ANALYZING, Phase.FAILED},
    Phase.ANALYZING: {Phase.PLANNING, Phase.FAILED},
    Phase.PLANNING: {Phase.INVESTIGATING, Phase.EVALUATING, Phase.FAILED},
    Phase.INVESTIGATING: {Phase.EVALUATING, Phase.FAILED},
    Phase.EVALUATING: {Phase.INVESTIGATING, Phase.COMPLETED, Phase.AWAITING_APPROVAL, Phase.AWAITING_EVIDENCE, Phase.FAILED},
    Phase.AWAITING_APPROVAL: {Phase.INVESTIGATING, Phase.COMPLETED},
    Phase.AWAITING_EVIDENCE: {Phase.INGESTING, Phase.COMPLETED},
    Phase.COMPLETED: set(),
    Phase.FAILED: set(),
}


class InvalidTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self) -> None:
        self.phase = Phase.CREATED
        self.history: list[Phase] = [Phase.CREATED]

    def to(self, phase: Phase) -> None:
        if phase not in _TRANSITIONS[self.phase]:
            raise InvalidTransition(f"{self.phase.value} -> {phase.value} is not allowed")
        self.phase = phase
        self.history.append(phase)


# --------------------------------------------------------------------------- outputs

class ReplayStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    at: datetime
    phase: Phase
    kind: str
    summary: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    provider: str | None = None
    model: str | None = None
    duration_ms: int = 0
    status: str = "ok"            # ok | degraded | failed | skipped


class TimelineEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: datetime | None
    service: str | None
    kind: str                     # first_error | signature | anomaly | change | alert
    summary: str
    evidence_id: str | None = None
    event_id: str | None = None


class SignatureFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    category: str
    role: str
    count: int
    services: tuple[str, ...]
    first_seen: datetime | None
    snippet: str
    evidence_id: str | None


class AnomalyFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    summary: str
    severity: str
    method: str
    service: str | None
    observed: float | None
    baseline: float | None
    evidence_id: str | None


class LogStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    events: int
    severity_counts: dict[str, int]
    service_error_counts: dict[str, int]
    top_error_groups: tuple[dict[str, Any], ...]
    formats: dict[str, int]
    malformed_lines: int
    duplicates: int
    first_event_at: datetime | None
    last_event_at: datetime | None


class DiagnosticCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: str
    arguments: dict[str, Any]
    purpose: str
    hypothesis_id: str | None
    risk: RiskLevel
    status: str                   # executed | failed | awaiting_approval | skipped
    result_summary: str | None = None
    evidence_id: str | None = None


class LLMUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    calls: int = 0
    providers: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    fallbacks: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class InvestigationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    service: str
    severity: str
    status: Phase
    started_at: datetime
    finished_at: datetime
    headline: str
    confidence_label: str
    top_score: float
    log_stats: LogStats
    timeline: tuple[TimelineEntry, ...]
    evidence: tuple[Evidence, ...]
    signatures: tuple[SignatureFinding, ...]
    anomalies: tuple[AnomalyFinding, ...]
    chain: ChainSummary
    graph: CausalGraph
    hypotheses: tuple[Hypothesis, ...]
    hypothesis_comparison: str | None
    llm_assessment: LLMAssessment | None
    debate: DebateOutcome | None
    diagnostics: tuple[DiagnosticCall, ...]
    remediation: RemediationPlan
    recalled_incidents: tuple[RecalledIncident, ...]
    lessons: tuple[Lesson, ...]
    blast_radius: dict[str, list[str]]
    change_suspects: tuple[ChangeSuspect, ...] = ()
    origin_frame: str | None = None        # application frame the leading hypothesis's stack trace points at
    current_version: str | None = None     # release running now (from the alert or recent deploys), if known
    memory_backend: str | None
    memory_consulted: bool
    llm: LLMUsage
    degraded: tuple[str, ...]
    replay: tuple[ReplayStep, ...]
    phases: tuple[Phase, ...]

    @property
    def leader(self) -> Hypothesis | None:
        return self.hypotheses[0] if self.hypotheses else None


class ProgressUpdate(BaseModel):
    """Streamed while the investigation runs; `report` is set on the final update."""

    model_config = ConfigDict(frozen=True)

    phase: Phase
    step: ReplayStep | None = None
    report: InvestigationReport | None = None


# --------------------------------------------------------------------------- engine

class InvestigationEngine:
    def __init__(self, *, router: LLMRouter | None = None, memory: IncidentMemory | None = None,
                 tools: ToolAdapter | None = None, limits: InvestigationLimits | None = None,
                 enable_debate: bool = True):
        self.router = router
        self.memory = memory
        self.tools = tools
        self.limits = limits or InvestigationLimits()
        self.enable_debate = enable_debate

    async def run(self, case: IncidentCase) -> InvestigationReport:
        report: InvestigationReport | None = None
        async for update in self.stream(case):
            if update.report is not None:
                report = update.report
        assert report is not None
        return report

    async def stream(self, case: IncidentCase) -> AsyncIterator[ProgressUpdate]:
        run = _Run(self, case)
        async for update in run.execute():
            yield update


class _Run:
    """State for a single investigation. Not reused across incidents."""

    def __init__(self, engine: InvestigationEngine, case: IncidentCase):
        self.engine = engine
        self.case = case
        self.limits = engine.limits
        self.sm = StateMachine()
        self.started = datetime.now(timezone.utc)
        self.deadline = time.monotonic() + self.limits.max_seconds
        self.replay: list[ReplayStep] = []
        self.ledger = EvidenceLedger()
        self.degraded: list[str] = []
        self.budget = LLMBudget(max_calls=self.limits.max_llm_calls)
        self.llm_providers: list[str] = []
        self.llm_models: list[str] = []
        self.llm_fallbacks: list[str] = []
        self.llm_errors: list[str] = []
        self.diagnostics: list[DiagnosticCall] = []
        self.tool_calls = 0
        self.assessment: LLMAssessment | None = None
        self.change_suspects: tuple[ChangeSuspect, ...] = ()
        self.origin_frame: str | None = None
        self.current_version: str | None = None
        self.change_evidence: dict[str, str] = {}
        self._pending: list[ProgressUpdate] = []

    # -- helpers -----------------------------------------------------------------

    def record(self, kind: str, summary: str, *, inputs=(), outputs=(), provider=None, model=None,
               started: float | None = None, status: str = "ok") -> ReplayStep:
        step = ReplayStep(sequence=len(self.replay) + 1, at=datetime.now(timezone.utc), phase=self.sm.phase,
                          kind=kind, summary=redact_sensitive_text(summary)[:400], inputs=tuple(inputs)[:20],
                          outputs=tuple(outputs)[:20], provider=provider, model=model,
                          duration_ms=int((time.monotonic() - started) * 1000) if started else 0, status=status)
        self.replay.append(step)
        self._pending.append(ProgressUpdate(phase=self.sm.phase, step=step))
        return step

    def drain(self) -> list[ProgressUpdate]:
        out, self._pending = self._pending, []
        return out

    def out_of_time(self) -> bool:
        return time.monotonic() > self.deadline

    async def within_deadline(self, coro):
        """Await an AI call, but never past the investigation deadline (one call may retry and fail over)."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0.5:
            coro.close()
            raise LLMError(LLMErrorKind.TIMEOUT, "investigation time limit reached")
        try:
            return await asyncio.wait_for(coro, timeout=remaining)
        except asyncio.TimeoutError:
            raise LLMError(LLMErrorKind.TIMEOUT, "investigation time limit reached") from None

    def to(self, phase: Phase) -> None:
        self.sm.to(phase)

    # -- pipeline ----------------------------------------------------------------

    async def execute(self) -> AsyncIterator[ProgressUpdate]:
        try:
            # Parsing and signature matching are pure CPU work (about a second on 200 KB of logs). They run in a
            # worker thread so a large analysis never stalls the server's event loop for other requests.
            self.to(Phase.INGESTING)
            events, ingest_stats = await asyncio.to_thread(self.ingest)
            for u in self.drain():
                yield u

            self.to(Phase.ANALYZING)
            analysis, matches, anomalies, chain, sig_evidence = await asyncio.to_thread(self.analyze, events)
            for u in self.drain():
                yield u
            recalled, lessons = await self.recall(events, matches)
            for u in self.drain():
                yield u

            self.to(Phase.PLANNING)
            hypotheses = generate_hypotheses(service=self.case.service, matches=matches, chain=chain,
                                             ledger=self.ledger, signature_evidence=sig_evidence, recalled=recalled)
            hypotheses = self.apply_change_suspects(hypotheses, chain)
            self.record("hypotheses_generated", f"{len(hypotheses)} competing hypotheses from the failure chain"
                        + (" and memory" if recalled else ""), outputs=[h.id for h in hypotheses])
            hypotheses = await self.assess(hypotheses, chain, lessons)
            for u in self.drain():
                yield u

            hypotheses, awaiting_approval = await self.investigate_loop(hypotheses, matches)
            for u in self.drain():
                yield u

            debate: DebateOutcome | None = None
            if self.engine.enable_debate and hypotheses:
                debate = run_debate(hypotheses, matches=matches, ledger=self.ledger, recalled=recalled, lessons=lessons)
                hypotheses = self.apply_debate(hypotheses, debate)
                self.record("perspectives_compared", f"{len(debate.stances)} perspective stances; "
                            f"{len(debate.disagreements)} disagreement(s)", outputs=list(debate.agreement))

            self.origin_frame = leader_origin_frame(hypotheses[0] if hypotheses else None, self.ledger.items)
            self.current_version = current_release(self.case, self.change_suspects)
            remediation = plan_remediation(hypotheses, lessons=lessons, service=self.case.service,
                                           severity=self.case.severity, origin_frame=self.origin_frame,
                                           current_version=self.current_version)
            self.record("remediation_planned", f"{len(remediation.steps)} steps, {len(remediation.blocked_actions)} "
                        "blocked by history", outputs=[s.id for s in remediation.steps])

            leader = hypotheses[0] if hypotheses else None
            if awaiting_approval:
                final = Phase.AWAITING_APPROVAL
            elif leader is None or leader.category == Category.UNKNOWN:
                final = Phase.AWAITING_EVIDENCE
            else:
                final = Phase.COMPLETED
            self.to(final)
            self.record("concluded", f"Investigation {final.value}; leading hypothesis: "
                        f"{leader.title if leader else 'none'}",
                        outputs=[leader.id] if leader else [])
            report = self.build_report(events, ingest_stats, analysis, matches, anomalies, chain, sig_evidence,
                                       hypotheses, debate, remediation, recalled, lessons, final)
            for u in self.drain():
                yield u
            yield ProgressUpdate(phase=final, report=report)
        except Exception as exc:  # the pipeline must report failures, not hang the stream
            logger.exception("investigation %s failed", self.case.incident_id)
            if Phase.FAILED in _TRANSITIONS[self.sm.phase]:
                self.to(Phase.FAILED)
            self.record("failed", f"Investigation failed: {type(exc).__name__}", status="failed")
            for u in self.drain():
                yield u
            raise

    # -- stages ------------------------------------------------------------------

    def ingest(self) -> tuple[tuple[NormalizedEvent, ...], dict[str, Any]]:
        started = time.monotonic()
        sources = {"logs": self.case.logs, **self.case.extra_sources}
        events: list[NormalizedEvent] = []
        formats: dict[str, int] = {}
        malformed = duplicates = 0
        for name, content in sources.items():
            if not content or not content.strip():
                continue
            result = ingest_text(content, incident_id=self.case.incident_id, source=name,
                                 default_service=self.case.service)
            events.extend(result.events)
            for key, value in result.detected_formats.items():
                formats[key] = formats.get(key, 0) + value
            malformed += len(result.malformed_lines)
            duplicates += result.duplicate_count
            if result.truncated:
                self.degraded.append(f"source '{name}' exceeded the size limit and was truncated")
        if self.case.alert_text:
            self.ledger.add_alert(alert_text=self.case.alert_text, service=self.case.service, severity=self.case.severity)
        self.record("evidence_ingested", f"Parsed {len(events)} events from {len([s for s in sources.values() if s])} "
                    f"source(s); formats {formats or {'none': 0}}; {malformed} malformed line(s) kept",
                    outputs=[e.event_id for e in events[:20]], started=started,
                    status="ok" if events or self.case.alert_text else "degraded")
        if not events:
            self.degraded.append("no log events supplied; analysis relies on the alert text only")
        return tuple(events), {"formats": formats, "malformed": malformed, "duplicates": duplicates}

    def analyze(self, events: tuple[NormalizedEvent, ...]):
        started = time.monotonic()
        analysis = analyze_events(events)
        correlations = correlate_events(events)
        self.ledger.add_event_facts(events)
        self.ledger.add_analysis(analysis)
        self.ledger.add_correlations(correlations)
        matches = match_signatures(events, extra_text={"alert": f"{self.case.title}\n{self.case.alert_text}"})
        sig_evidence: dict[str, str] = {}
        for m in matches:
            item = self.ledger.add_signature(signature_id=m.id, label=m.signature.label, category=m.signature.category.value,
                                             role=m.signature.role.value, count=m.count, event_ids=m.event_ids,
                                             snippet=m.snippet, services=m.services,
                                             first_seen=m.first_seen.isoformat() if m.first_seen else None)
            sig_evidence[m.id] = item.evidence_id
        baseline = ingest_text(self.case.baseline_logs, incident_id=f"{self.case.incident_id}:baseline",
                               source="baseline").events if self.case.baseline_logs else ()
        anomalies = detect_anomalies(events, baseline_events=baseline)
        for a in anomalies:
            self.ledger.add_anomaly(kind=a.kind.value, summary=a.summary, event_ids=a.event_ids,
                                    attributes={"service": a.service, "observed": a.observed, "baseline": a.baseline,
                                                "method": a.method, "severity": a.severity})
        chain = analyze_chain(matches)
        self.change_suspects = self.correlate_changes(events)
        self.record("signals_extracted",
                    f"{len(analysis.error_groups)} error groups, {len(matches)} known signatures "
                    f"({', '.join(m.id for m in matches[:6]) or 'none'}), {len(anomalies)} anomalies",
                    outputs=list(sig_evidence.values()), started=started)
        self.record("failure_chain", "roots: " + (", ".join(chain.root_candidates) or "none") +
                    "; symptoms: " + (", ".join(chain.symptoms) or "none") +
                    ("; triggers: " + ", ".join(chain.triggers) if chain.triggers else ""))
        return analysis, matches, anomalies, chain, sig_evidence

    def correlate_changes(self, events: tuple[NormalizedEvent, ...]) -> tuple[ChangeSuspect, ...]:
        if not self.case.changes:
            return ()
        failures = [e.timestamp for e in events if e.timestamp and e.severity in {Severity.ERROR, Severity.CRITICAL}]
        origins = [str(e.metadata["origin_frame"]) for e in events if "origin_frame" in e.metadata]
        suspects = correlate_changes(self.case.changes, first_failure=min(failures) if failures else self.case.created_at,
                                     service=self.case.service, origin_frames=origins)
        for suspect in suspects:
            c = suspect.change
            item = self.ledger.add_change(change_id=c.id, kind=c.kind, title=c.title, score=suspect.score,
                                          reasons=suspect.reasons, service=c.service,
                                          timestamp=c.timestamp.isoformat() if c.timestamp else None)
            self.change_evidence[c.id] = item.evidence_id
        self.record("changes_correlated", f"{len(self.case.changes)} change(s) supplied; {len(suspects)} plausible suspect(s)"
                    + (f"; top: {suspects[0].change.id} ({suspects[0].score:.2f})" if suspects else ""),
                    outputs=list(self.change_evidence.values()))
        return suspects

    def apply_change_suspects(self, hypotheses: list[Hypothesis], chain) -> list[Hypothesis]:
        strong = [s for s in self.change_suspects if s.score >= 0.4]
        if not strong:
            return hypotheses
        top = strong[0]
        evidence_id = self.change_evidence[top.change.id]
        why = "; ".join(top.reasons)
        out = []
        for h in hypotheses:
            if h.category in {Category.DEPLOYMENT_REGRESSION, Category.CONFIGURATION} or (
                    top.matched_files and h.category == Category.APPLICATION_BUG):
                h = h.adjusted("change_correlation", round(0.15 * top.score, 3), f"{top.change.kind} {top.change.id}: {why}")
                h = h.model_copy(update={"supporting_evidence_ids": h.supporting_evidence_ids + (evidence_id,)})
            out.append(h)
        if not any(h.category == Category.DEPLOYMENT_REGRESSION for h in out):
            new = Hypothesis(id=hypothesis_id(f"change:{top.change.id}"),
                             title=f"Regression from {top.change.kind} {top.change.id[:12]}: {top.change.title[:80]}",
                             description=f"The change is a plausible trigger: {why}.", category=Category.DEPLOYMENT_REGRESSION,
                             affected_service=top.change.service or self.case.service,
                             supporting_evidence_ids=(evidence_id,), score=0.0, generated_by="change_correlation",
                             missing_evidence=("A diff review showing how the change produces the observed failure",),
                             verification_steps=(f"Review the diff of {top.change.id} against the failing code path",
                                                 "Check whether the error rate changed exactly at the deploy time"))
            out.append(new.adjusted("base", 0.15, "change correlated with the failure")
                       .adjusted("change_correlation", round(0.35 * top.score, 3), why))
        return rank(out)

    async def recall(self, events: tuple[NormalizedEvent, ...], matches: tuple[SignatureMatch, ...]):
        memory = self.engine.memory
        if memory is None:
            self.record("memory_skipped", "No memory backend configured; historical incidents were not consulted",
                        status="skipped")
            return (), ()
        started = time.monotonic()
        query = MemoryQuery(
            incident_id=self.case.incident_id, service=self.case.service,
            text=f"{self.case.title}\n{self.case.alert_text}",
            signatures=tuple(m.id for m in matches),
            stack_fingerprints=tuple(sorted({str(e.metadata["stack_fingerprint"]) for e in events if "stack_fingerprint" in e.metadata})),
            exception_types=tuple(sorted({e.exception_type for e in events if e.exception_type})),
        )
        try:
            recalled = tuple(await asyncio.wait_for(memory.recall(query), timeout=10))
        except Exception as exc:
            self.degraded.append(f"memory recall failed ({type(exc).__name__}); continued without history")
            self.record("memory_failed", "Memory recall failed; continuing with current evidence only",
                        started=started, status="degraded")
            return (), ()
        for item in recalled:
            self.ledger.add_historical_match(incident_id=item.record.incident_id, similarity=item.relevance,
                                             shared_fingerprints=item.shared_signatures,
                                             contradicts=False)
        lessons = derive_lessons(recalled)
        self.record("memory_recalled", f"{len(recalled)} similar past incident(s) from {memory.backend} memory; "
                    f"{len(lessons)} lesson(s)", outputs=[r.record.incident_id for r in recalled], started=started)
        return recalled, lessons

    async def assess(self, hypotheses: list[Hypothesis], chain, lessons: tuple[Lesson, ...]) -> list[Hypothesis]:
        router = self.engine.router
        if router is None or not router.available or self.limits.max_llm_calls == 0:
            self.record("llm_skipped", "No AI provider configured; ranking is deterministic only", status="skipped")
            return hypotheses
        started = time.monotonic()
        aliases = self._aliases()
        evidence = [{"id": alias, "kind": item.kind.value, "summary": item.summary[:170]}
                    for alias, item in aliases.items()]
        reverse = {item.evidence_id: alias for alias, item in aliases.items()}
        payload_h = [{"id": h.id, "title": h.title, "category": h.category.value, "score": h.score,
                      "evidence": [reverse[e] for e in h.supporting_evidence_ids if e in reverse],
                      "explains": list(h.explains_symptoms), "based_on": list(h.based_on_incidents)}
                     for h in hypotheses[:6]]
        incident = {"id": self.case.incident_id, "title": self.case.title, "service": self.case.service,
                    "severity": self.case.severity, "alert": self.case.alert_text[:600]}
        request = LLMRequest.of(ASSESS_SYSTEM, assess_prompt(
            incident=incident, evidence=evidence, hypotheses=payload_h,
            chain={"roots": list(chain.root_candidates), "triggers": list(chain.triggers),
                   "symptoms": list(chain.symptoms), "contributing": list(chain.contributing)},
            lessons=[lesson.message for lesson in lessons][:6]),
            task="assess", max_output_tokens=1400, temperature=0.1)
        try:
            assessment, result = await self.within_deadline(
                router.complete_structured(request, LLMAssessment, budget=self.budget))
        except LLMError as exc:
            self.llm_errors.append(str(exc)[:300])
            self.degraded.append(f"AI assessment unavailable ({exc.kind.value}); deterministic ranking used")
            self.record("llm_failed", f"AI assessment failed ({exc.kind.value}); deterministic findings stand",
                        started=started, status="degraded")
            return hypotheses
        self._note_llm(result)
        valid_h = {h.id for h in hypotheses}
        rejected_refs = 0
        updated = list(hypotheses)
        for a in assessment.assessments:
            if a.hypothesis_id not in valid_h:
                rejected_refs += 1
                continue
            index = next(i for i, h in enumerate(updated) if h.id == a.hypothesis_id)
            h = updated[index]
            support = [aliases[x].evidence_id for x in a.supporting_evidence_ids if x in aliases]
            against = [aliases[x].evidence_id for x in a.contradicting_evidence_ids if x in aliases]
            rejected_refs += len(a.supporting_evidence_ids) + len(a.contradicting_evidence_ids) - len(support) - len(against)
            delta = {"leading": 0.1, "plausible": 0.0, "unlikely": -0.08}[a.verdict]
            if delta:
                h = h.adjusted("ai_assessment", delta, f"{result.provider} judged it {a.verdict}")
            h = h.model_copy(update={
                "reasoning": a.reasoning[:800],
                "supporting_evidence_ids": tuple(dict.fromkeys(h.supporting_evidence_ids + tuple(support))),
                "contradicting_evidence_ids": tuple(dict.fromkeys(h.contradicting_evidence_ids + tuple(against))),
                "missing_evidence": tuple(dict.fromkeys(h.missing_evidence + tuple(m[:200] for m in a.missing_evidence[:3]))),
            })
            updated[index] = h
        new = assessment.new_hypothesis
        if new is not None:
            support = [aliases[x].evidence_id for x in new.supporting_evidence_ids if x in aliases]
            if support:
                try:
                    category = Category(new.category)
                except ValueError:
                    category = Category.UNKNOWN
                h = Hypothesis(id=hypothesis_id(f"llm:{new.title.lower()}"), title=new.title[:140],
                               description=new.description[:800], category=category,
                               affected_service=self.case.service, supporting_evidence_ids=tuple(support), score=0.0,
                               generated_by=f"llm:{result.provider}",
                               missing_evidence=("Deterministic confirmation (no known signature matched this explanation)",))
                updated.append(h.adjusted("base", 0.2, "AI-proposed explanation backed by cited evidence")
                               .adjusted("evidence_volume", min(0.1, 0.03 * len(support)), f"{len(support)} cited evidence items"))
            else:
                rejected_refs += 1
        self.assessment = assessment
        self.record("llm_assessed", f"{result.provider}/{result.model} assessed {len(assessment.assessments)} hypotheses"
                    + (f"; rejected {rejected_refs} invented reference(s)" if rejected_refs else ""),
                    provider=result.provider, model=result.model, started=started)
        return rank(updated)

    async def investigate_loop(self, hypotheses: list[Hypothesis], matches) -> tuple[list[Hypothesis], bool]:
        tools = self.engine.tools
        awaiting = False
        if tools is None:
            self.record("diagnostics_skipped", "No diagnostic connectors available; using supplied evidence only",
                        status="skipped")
            self.to(Phase.EVALUATING)
            return apply_contradictions(hypotheses, self.ledger), False
        try:
            specs = tuple(await _maybe_await(tools.list_tools()))
        except Exception as exc:
            self.degraded.append(f"tool registry unavailable ({type(exc).__name__})")
            self.record("diagnostics_unavailable", "Connector registry could not be read", status="degraded")
            self.to(Phase.EVALUATING)
            return hypotheses, False
        executed: set[tuple[str, str]] = set()
        for _step in range(self.limits.max_steps):
            if self.out_of_time():
                self.degraded.append("investigation time limit reached")
                break
            self.to(Phase.INVESTIGATING)
            requests = await self.plan_diagnostics(hypotheses, specs, executed)
            if not requests:
                self.to(Phase.EVALUATING)
                break
            for spec, args, purpose, hid in requests:
                if self.tool_calls >= self.limits.max_tool_calls or self.out_of_time():
                    break
                key = (spec.name, repr(sorted(args.items())))
                executed.add(key)
                if not spec.read_only or spec.risk != RiskLevel.READ_ONLY or spec.requires_approval:
                    awaiting = True
                    self.diagnostics.append(DiagnosticCall(tool_name=spec.name, arguments=redact_sensitive_data(args),
                                                           purpose=purpose, hypothesis_id=hid, risk=spec.risk,
                                                           status="awaiting_approval"))
                    self.record("approval_required", f"{spec.name} is {spec.risk.value}; not run without human approval",
                                status="skipped")
                    continue
                await self.execute_tool(tools, spec, args, purpose, hid)
            self.to(Phase.EVALUATING)
            hypotheses = apply_contradictions(hypotheses, self.ledger)
            leader = hypotheses[0] if hypotheses else None
            runner = hypotheses[1].score if len(hypotheses) > 1 else 0.0
            if leader and leader.score >= self.limits.stop_score and leader.score - runner >= 0.15:
                self.record("stop_condition", f"Leader '{leader.title}' is clear ({leader.score:.2f}); stopping")
                break
        else:
            self.record("step_limit", f"Reached the {self.limits.max_steps}-step limit", status="degraded")
        if self.sm.phase != Phase.EVALUATING:
            self.to(Phase.EVALUATING)
        return hypotheses, awaiting

    async def plan_diagnostics(self, hypotheses: list[Hypothesis], specs: tuple[ToolSpec, ...],
                               executed: set[tuple[str, str]]) -> list[tuple[ToolSpec, dict[str, Any], str, str | None]]:
        """Pick tools by expected information gain: prefer tools that test the top hypotheses differently."""
        top = hypotheses[:3]
        scored: list[tuple[float, ToolSpec, str | None]] = []
        for spec in specs:
            if any(name == spec.name for name, _ in executed):
                continue
            text = f"{spec.name} {spec.description}".lower()
            matched = []
            for h in top:
                # A cause is often verified through what it explains (a leak shows up in pool metrics).
                related = [BY_ID[s] for s in (h.signature_id, *h.explains_symptoms) if s and s in BY_ID]
                hints = {hint for sig in related for hint in sig.tool_hints} or {"logs"}
                if any(hint in text for hint in hints):
                    matched.append(h)
            if not matched:
                continue
            # A tool that tests only one of two close leaders discriminates between them.
            discriminating = 0.15 if len(top) > 1 and len(matched) == 1 and abs(top[0].score - top[1].score) < 0.2 else 0.0
            cost = 0.0 if spec.risk == RiskLevel.READ_ONLY else 0.5
            scored.append((sum(h.score for h in matched) + discriminating - cost, spec, matched[0].id))
        scored.sort(key=lambda item: -item[0])
        plan = [(spec, _default_args(spec, self.case), f"Test hypothesis {hid}", hid) for _, spec, hid in scored[:2]]
        if self.engine.router and self.engine.router.available and specs and self.budget.used < self.budget.max_calls:
            plan = await self._llm_plan(hypotheses, specs, executed, plan)
        valid = []
        for spec, args, purpose, hid in plan:
            try:
                valid.append((spec, validate_tool_arguments(spec, args), purpose, hid))
            except ValueError as exc:
                self.record("tool_rejected", f"{spec.name}: {exc}", status="skipped")
        if valid:
            self.record("diagnostics_planned", "; ".join(f"{s.name} ({p})" for s, _, p, _ in valid)[:380],
                        outputs=[s.name for s, *_ in valid])
        return valid

    async def _llm_plan(self, hypotheses, specs, executed, fallback):
        started = time.monotonic()
        payload = {"hypotheses": [{"id": h.id, "title": h.title, "score": h.score} for h in hypotheses[:4]],
                   "tools": [{"name": s.name, "description": s.description, "parameters": list(s.allowed_parameters),
                              "required": list(s.required_parameters), "read_only": s.read_only} for s in specs
                             if not any(n == s.name for n, _ in executed)],
                   "service": self.case.service}
        try:
            plan, result = await self.within_deadline(self.engine.router.complete_structured(
                LLMRequest.of(PLAN_SYSTEM, fence(payload), task="plan", max_output_tokens=600, temperature=0.1),
                LLMPlan, budget=self.budget))
        except LLMError as exc:
            self.llm_errors.append(str(exc)[:300])
            self.record("llm_plan_failed", f"AI planning unavailable ({exc.kind.value}); heuristic plan used",
                        started=started, status="degraded")
            return fallback
        self._note_llm(result)
        by_name = {s.name: s for s in specs}
        chosen = []
        for call in plan.requests[:3]:
            spec = by_name.get(call.tool_name)
            if spec is None or not spec.read_only or spec.risk != RiskLevel.READ_ONLY:
                self.record("tool_rejected", f"AI requested unavailable or non-read-only tool '{call.tool_name[:60]}'",
                            status="skipped")
                continue
            chosen.append((spec, dict(call.arguments), call.purpose[:200] or "AI-planned check", call.hypothesis_id))
        self.record("llm_planned", f"{result.provider} proposed {len(chosen)} valid read-only check(s)",
                    provider=result.provider, model=result.model, started=started)
        return chosen or fallback

    async def execute_tool(self, tools: ToolAdapter, spec: ToolSpec, args: dict[str, Any], purpose: str,
                           hid: str | None) -> None:
        started = time.monotonic()
        self.tool_calls += 1
        try:
            result: ToolResult = await asyncio.wait_for(_maybe_await(tools.execute(spec.name, args)), timeout=20)
        except Exception as exc:
            self.diagnostics.append(DiagnosticCall(tool_name=spec.name, arguments=redact_sensitive_data(args),
                                                   purpose=purpose, hypothesis_id=hid, risk=spec.risk, status="failed",
                                                   result_summary=type(exc).__name__))
            self.record("tool_failed", f"{spec.name} failed: {type(exc).__name__}", started=started, status="failed")
            return
        summary = redact_sensitive_text(result.summary)[:400]
        evidence_id = None
        if result.success:
            data = redact_sensitive_data(result.data)
            item = self.ledger.add_tool_result(
                tool_name=spec.name, summary=summary, source_event_ids=result.source_event_ids,
                attributes={str(k): v for k, v in data.items() if isinstance(v, (str, int, float, bool)) or v is None})
            evidence_id = item.evidence_id
        self.diagnostics.append(DiagnosticCall(tool_name=spec.name, arguments=redact_sensitive_data(args), purpose=purpose,
                                               hypothesis_id=hid, risk=spec.risk,
                                               status="executed" if result.success else "failed",
                                               result_summary=summary, evidence_id=evidence_id))
        self.record("tool_succeeded" if result.success else "tool_failed", f"{spec.name}: {summary[:200]}",
                    outputs=[evidence_id] if evidence_id else [], started=started,
                    status="ok" if result.success else "failed")

    def apply_debate(self, hypotheses: list[Hypothesis], debate: DebateOutcome) -> list[Hypothesis]:
        out = []
        for h in hypotheses:
            net = debate.agreement.get(h.id, 0)
            if net >= 2:
                h = h.adjusted("perspective_agreement", min(0.08, 0.03 * net), f"{net} perspectives agree on balance")
            elif net < 0:
                h = h.adjusted("perspective_disagreement", 0.04 * net, f"{-net} more perspectives oppose than support")
            out.append(h)
        return rank(out)

    # -- report ------------------------------------------------------------------

    def _aliases(self) -> dict[str, Evidence]:
        return {f"E{i + 1}": item for i, item in enumerate(self.ledger.ranked(self.limits.max_evidence_for_llm))}

    def _note_llm(self, result) -> None:
        self.llm_providers.append(result.provider)
        self.llm_models.append(result.model)
        self.llm_fallbacks.extend(result.fallback_from)

    def build_report(self, events, ingest_stats, analysis: AnalysisReport, matches, anomalies: tuple[Anomaly, ...],
                     chain, sig_evidence, hypotheses, debate, remediation, recalled, lessons, final: Phase) -> InvestigationReport:
        evidence_by_kind: dict[tuple[str, str], str] = {}
        for item in self.ledger.by_kind(EvidenceKind.ANOMALY):
            evidence_by_kind[(str(item.attributes.get("anomaly")), item.summary)] = item.evidence_id
        anomaly_findings = tuple(AnomalyFinding(kind=a.kind.value, summary=redact_sensitive_text(a.summary), severity=a.severity, method=a.method,
                                                service=a.service, observed=a.observed, baseline=a.baseline,
                                                evidence_id=evidence_by_kind.get((a.kind.value, a.summary)))
                                 for a in anomalies)
        signature_findings = tuple(SignatureFinding(id=m.id, label=m.signature.label, category=m.signature.category.value,
                                                    role=m.signature.role.value, count=m.count, services=m.services,
                                                    first_seen=m.first_seen, snippet=m.snippet,
                                                    evidence_id=sig_evidence.get(m.id)) for m in matches)
        timeline: list[TimelineEntry] = []
        for m in matches:
            timeline.append(TimelineEntry(at=m.first_seen, service=m.services[0] if m.services else None,
                                          kind="change" if m.signature.role.value == "change" else "signature",
                                          summary=f"first {m.signature.label}: {m.snippet[:140]}",
                                          evidence_id=sig_evidence.get(m.id), event_id=m.event_ids[0]))
        for a in anomalies:
            if a.window_start:
                timeline.append(TimelineEntry(at=a.window_start, service=a.service, kind=a.kind.value, summary=redact_sensitive_text(a.summary),
                                              evidence_id=evidence_by_kind.get((a.kind.value, a.summary)),
                                              event_id=a.event_ids[0] if a.event_ids else None))
        for suspect in self.change_suspects[:3]:
            if suspect.change.timestamp:
                timeline.append(TimelineEntry(at=suspect.change.timestamp, service=suspect.change.service, kind="change",
                                              summary=f"{suspect.change.kind} {suspect.change.id[:12]}: {_clip(suspect.change.title, 120)}",
                                              evidence_id=self.change_evidence.get(suspect.change.id)))
        if self.case.created_at:
            timeline.append(TimelineEntry(at=self.case.created_at, service=self.case.service, kind="alert",
                                          summary=f"alert raised: {_clip(self.case.alert_text, 140)}"))
        timeline.sort(key=lambda t: (t.at is None, t.at or datetime.max.replace(tzinfo=timezone.utc)))
        stamps = [e.timestamp for e in events if e.timestamp]
        stats = LogStats(
            events=len(events), severity_counts=analysis.severity_counts, service_error_counts=analysis.service_error_counts,
            top_error_groups=tuple({"fingerprint": _clip(g.fingerprint, 160), "count": g.count, "services": list(g.services),
                                    "exception_type": g.exception_type, "event_ids": list(g.event_ids[:5])}
                                   for g in analysis.error_groups[:8]),
            formats=ingest_stats["formats"], malformed_lines=ingest_stats["malformed"],
            duplicates=ingest_stats["duplicates"], first_event_at=min(stamps) if stamps else None,
            last_event_at=max(stamps) if stamps else None)
        graph = build_graph(matches, chain, evidence_by_signature=sig_evidence, dependencies=self.case.dependencies)
        leader = hypotheses[0] if hypotheses else None
        headline = (self.assessment.root_cause_summary if self.assessment and self.assessment.root_cause_summary
                    else (leader.description if leader else "No explanation could be formed from the evidence."))
        memory = self.engine.memory
        return InvestigationReport(
            incident_id=self.case.incident_id, service=self.case.service, severity=self.case.severity, status=final,
            started_at=self.started, finished_at=datetime.now(timezone.utc), headline=redact_sensitive_text(headline)[:600],
            confidence_label=confidence_words(leader.score if leader else 0.0), top_score=leader.score if leader else 0.0,
            log_stats=stats, timeline=tuple(timeline), evidence=self.ledger.ranked(), signatures=signature_findings,
            anomalies=anomaly_findings, chain=ChainSummary.of(chain), graph=graph, hypotheses=tuple(hypotheses),
            hypothesis_comparison=compare_top(hypotheses), llm_assessment=self.assessment, debate=debate,
            diagnostics=tuple(self.diagnostics), remediation=remediation, recalled_incidents=tuple(recalled),
            lessons=tuple(lessons), blast_radius=blast_radius(self.case.service, matches, self.case.dependencies),
            change_suspects=self.change_suspects, origin_frame=self.origin_frame,
            current_version=self.current_version,
            memory_backend=memory.backend if memory else None, memory_consulted=memory is not None and not any(
                "memory recall failed" in d for d in self.degraded),
            llm=LLMUsage(calls=self.budget.used, providers=tuple(dict.fromkeys(self.llm_providers)),
                         models=tuple(dict.fromkeys(self.llm_models)), fallbacks=tuple(dict.fromkeys(self.llm_fallbacks)),
                         errors=tuple(self.llm_errors)),
            degraded=tuple(dict.fromkeys(self.degraded)), replay=tuple(self.replay), phases=tuple(self.sm.history))


_VERSION = r"v\d+(?:\.\d+){1,3}(?:-[\w.]+)?"
_DEPLOYED_VERSION = re.compile(r"(?i)\b(?:deploy(?:ed|ment|ing)?|release[sd]?|rollout|rolled out|upgraded? to|version)\b"
                               r"[^\n]{0,40}?\b(" + _VERSION + r")\b")
_ANY_VERSION = re.compile(r"\b(" + _VERSION + r")\b")


def current_release(case: IncidentCase, suspects=()) -> str | None:
    """The release running now: named next to a deploy word in the alert, else in a suspected change, else logs."""
    head = f"{case.title}\n{case.alert_text}"
    match = _DEPLOYED_VERSION.search(head) or _ANY_VERSION.search(head)
    if match:
        return match.group(1)
    for suspect in suspects:
        match = _ANY_VERSION.search(suspect.change.title or "")
        if match:
            return match.group(1)
    match = _DEPLOYED_VERSION.search(case.logs[:200_000])
    return match.group(1) if match else None


def leader_origin_frame(leader: Hypothesis | None, items) -> str | None:
    """The first application stack frame in the evidence behind the leading hypothesis's signature.

    Only frames from the events that matched the leader's own signature count. A connection-leak report
    must point at the class holding the connection, not at the request that timed out waiting for it.
    """
    if leader is None or not leader.signature_id:
        return None
    signature_events = {event_id for item in items if item.kind == EvidenceKind.SIGNATURE
                        and item.attributes.get("signature") == leader.signature_id for event_id in item.event_ids}
    for item in items:
        frame = item.attributes.get("origin_frame")
        if frame and signature_events.intersection(item.event_ids):
            return str(frame)
    return None


def _clip(text: str, limit: int) -> str:
    """Redact, then truncate: truncating first can cut a credential into a fragment redaction won't recognise."""
    return redact_sensitive_text(text or "")[:limit]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _default_args(spec: ToolSpec, case: IncidentCase) -> dict[str, Any]:
    """Fill only well-known parameters from incident context; never invent values."""
    args: dict[str, Any] = {}
    for name in spec.allowed_parameters:
        if name in {"service", "service_name", "container", "container_name"}:
            args[name] = case.service
        elif name in {"since_minutes", "minutes"}:
            args[name] = 60
    return args


__all__ = [
    "IncidentCase", "InvestigationEngine", "InvestigationLimits", "InvestigationReport", "InvalidTransition", "Phase",
    "ProgressUpdate", "ReplayStep", "StateMachine",
]

