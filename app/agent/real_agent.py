"""Real agent: the Aftermath intelligence engine behind the backend's AgentService contract.

Selected when AGENT_MODE=real. Flow for `analyze`:

  1. Deterministic investigation (parse logs, signatures, anomalies, failure chain, memory recall,
     ranked hypotheses, remediation plan). Always succeeds, no network needed.
  2. Optional LLM assessment / diagnostic planning inside the engine (Groq -> Gemini -> Ollama fallback).
  3. Stream MemoryEvents, then a narrative (LLM-streamed, or deterministic if no provider answers),
     then exactly one SuggestionEvent.

Memory: `FileMemory` (JSON lines at AFTERMATH_MEMORY_PATH, default .aftermath/memory.jsonl) so lessons
survive restarts; set AFTERMATH_MEMORY_PATH="" for process-only memory. Both implement
`aftermath_intelligence.memory.IncidentMemory`, so the Hindsight adapter can be passed as `memory=`
without touching this file. Reports never claim Hindsight was used (`memory_backend` says what was).

Calibration: graded predictions persist at AFTERMATH_CALIBRATION_PATH. After AFTERMATH_CALIBRATION_MIN_SAMPLES
(default 30) graded outcomes the suggestion confidence becomes an isotonic-calibrated probability instead of a
raw ranking score. Grades come from live resolutions and from backtesting resolved history at ingest time.

Diagnostics: read-only Docker / GitHub connectors from env (see aftermath_intelligence.connectors).
Recent commits and workflow runs are fetched before each investigation for change correlation.

Configuration (env / .env): AI_PROVIDER, AI_FALLBACK_PROVIDER (comma list), GROQ_API_KEY, GROQ_MODEL,
GEMINI_API_KEY, GEMINI_MODEL, OLLAMA_BASE_URL, OLLAMA_MODEL, AFTERMATH_LLM_TIMEOUT_SECONDS,
AFTERMATH_MAX_LLM_CALLS, AFTERMATH_INVESTIGATION_SECONDS.
"""

import asyncio
import contextlib
import logging
import os
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

from aftermath_intelligence.calibration import CalibrationTracker, GradedPrediction
from aftermath_intelligence.connectors import Connectors, connectors_from_env
from aftermath_intelligence.ingestion import ingest_text
from aftermath_intelligence.investigation import IncidentCase, InvestigationEngine, InvestigationLimits, InvestigationReport
from aftermath_intelligence.learning import ResolutionEvaluation, evaluate_resolution_report, grade_prediction
from aftermath_intelligence.llm import LLMError, LLMRequest, LLMRouter, LLMSettings
from aftermath_intelligence.memory import IncidentMemory, InProcessMemory, build_memory_record
from aftermath_intelligence.presentation import deterministic_narrative, narrative_context, suggestion_fields
from aftermath_intelligence.prompts import NARRATIVE_SYSTEM, narrative_prompt
from aftermath_intelligence.resolution import ResolutionStatus
from aftermath_intelligence.store import FileMemory, PersistentCalibration, ReportStore
from aftermath_intelligence.tools import ToolAdapter
from app.agent.interface import AgentEvent, IncidentContext, MemoryEvent, ResolutionContext, SuggestionEvent, TokenEvent
from app.config import Settings

logger = logging.getLogger("aftermath.agent")



class RealAgent:
    name = "real"

    def __init__(self, settings: Settings, *, router: LLMRouter | None = None, memory: IncidentMemory | None = None,
                 tools: ToolAdapter | None = None, limits: InvestigationLimits | None = None,
                 stream_narrative: bool = True):
        if router is None:
            llm_settings = LLMSettings.from_env(overrides={"GROQ_API_KEY": settings.groq_api_key})
            router = LLMRouter(llm_settings)
        self.router = router
        memory_path = os.getenv("AFTERMATH_MEMORY_PATH", ".aftermath/memory.jsonl")
        self.memory: IncidentMemory = memory or (FileMemory(memory_path) if memory_path else InProcessMemory())
        self.connectors = connectors_from_env() if tools is None else Connectors(registry=None)
        tools = tools if tools is not None else self.connectors.registry
        self.limits = limits or InvestigationLimits(
            max_llm_calls=_env_number("AFTERMATH_MAX_LLM_CALLS", 3, int, minimum=0),
            max_seconds=_env_number("AFTERMATH_INVESTIGATION_SECONDS", 20, float),
        )
        self.narrative_first_token_seconds = _env_number("AFTERMATH_NARRATIVE_FIRST_TOKEN_SECONDS", 8, float)
        self.narrative_total_seconds = _env_number("AFTERMATH_NARRATIVE_SECONDS", 25, float)
        self.engine = InvestigationEngine(router=router, memory=self.memory, tools=tools, limits=self.limits)
        self.stream_narrative = stream_narrative
        calibration_path = os.getenv("AFTERMATH_CALIBRATION_PATH", ".aftermath/calibration.jsonl")
        min_samples = _env_number("AFTERMATH_CALIBRATION_MIN_SAMPLES", 30, int)
        if min_samples < 30:
            logger.warning("AFTERMATH_CALIBRATION_MIN_SAMPLES=%d: calibrated percentages rest on fewer than 30 graded "
                           "outcomes and are illustrative only (fine for a demo, not for production)", min_samples)
        self.calibration: CalibrationTracker = (
            PersistentCalibration(calibration_path, min_samples=min_samples) if calibration_path
            else CalibrationTracker(min_samples=min_samples))
        # Latest structured report per incident, for richer API endpoints and for grading at resolve time.
        # Full reports back the investigation endpoint and grading at resolve time. The newest are kept in RAM,
        # all of them on disk (AFTERMATH_REPORTS_DIR; empty = RAM only) so they survive restarts.
        # Default: a "reports" folder next to the memory file, so state that belongs together moves together.
        default_dir = str(Path(memory_path).parent / "reports") if memory_path else ""
        reports_dir = os.getenv("AFTERMATH_REPORTS_DIR", default_dir) or None
        cache = _env_number("AFTERMATH_REPORT_CACHE", 50, int)
        keep = _env_number("AFTERMATH_REPORTS_MAX", 500, int)
        self.reports: ReportStore[InvestigationReport] = ReportStore(
            InvestigationReport, reports_dir, suffix="report", cache_size=cache, max_files=keep)
        self.evaluations: ReportStore[ResolutionEvaluation] = ReportStore(
            ResolutionEvaluation, reports_dir, suffix="evaluation", cache_size=cache, max_files=keep)
        # Each analysis takes CPU (parsing, matching) and AI quota; bound how many run at once. Extra
        # requests wait their turn instead of all competing for the same rate-limited provider.
        self._analysis_slots = asyncio.Semaphore(_env_number("AFTERMATH_MAX_CONCURRENT_ANALYSES", 4, int))
        logger.info("RealAgent ready: providers=%s memory=%s", router.describe(), self.memory.backend)

    # ------------------------------------------------------------------ AgentService

    async def analyze(self, incident: IncidentContext) -> AsyncIterator[AgentEvent]:
        changes = ()
        if self.connectors.github is not None:
            changes = await self.connectors.github.recent_changes(incident.service, until=incident.created_at)
        async with self._analysis_slots:
            report = await self.engine.run(_case(incident, changes))
        await asyncio.to_thread(self.reports.put, incident.id, report)   # JSON + fsync off the event loop

        for item in report.recalled_incidents:
            record = item.record
            failed = [a.description for a in record.attempts if a.outcome in {ResolutionStatus.FAILED, ResolutionStatus.TEMPORARY}]
            yield MemoryEvent(
                past_incident_id=record.incident_id, title=record.title, service=record.service,
                summary=_memory_summary(record.root_cause, record.resolution, item.matching_points),
                relevance_score=item.relevance, root_cause=record.root_cause, resolution=record.resolution,
                failed_attempts=failed, time_to_resolve_minutes=record.time_to_resolve_minutes,
            )

        async for chunk in self._narrate(report):
            yield TokenEvent(text=chunk)

        fields = suggestion_fields(report)
        # Forward-compatible: structured extras (warning_details, origin_frame) are sent only when the backend's
        # Suggestion model declares them; until then the plain fields carry the same information.
        fields = {k: v for k, v in fields.items() if k in SuggestionEvent.model_fields}
        calibrated = self.calibration.calibrate(report.top_score)
        if calibrated is not None:
            fields["confidence"] = calibrated
        # Lets the UI show a percentage (and consensus) only when the score is a real probability.
        fields["confidence_calibrated"] = calibrated is not None
        yield SuggestionEvent(**fields)

    async def learn_from_resolution(self, incident: IncidentContext, resolution: ResolutionContext) -> None:
        events = ingest_text(incident.logs, incident_id=incident.id, source="logs", default_service=incident.service,
                             reference=incident.created_at).events
        report = self._report_for(incident.id, incident.created_at)
        evaluation = evaluate_resolution_report(
            incident_id=incident.id, title=incident.title, service=incident.service, alert_text=incident.alert_text,
            events=events, root_cause=resolution.root_cause, fix_that_worked=resolution.fix_that_worked,
            what_failed=resolution.what_failed, notes=resolution.notes,
            hypotheses=report.hypotheses if report else (),
            recommended_steps=[s.description for s in report.remediation.steps] if report else (),
            severity=incident.severity, created_at=incident.created_at, resolved_at=incident.resolved_at,
        )
        await self.memory.retain(evaluation.memory_record)
        await asyncio.to_thread(self.evaluations.put, incident.id, evaluation)
        if report is not None:
            self.calibration.record(GradedPrediction(incident.id, report.top_score, evaluation.prediction.verdict,
                                                     evaluation.prediction.rank_of_actual))
        logger.info("learned from %s: outcome=%s prediction=%s", incident.id, evaluation.outcome.value,
                    evaluation.prediction.verdict)

    async def ingest_history(self, incidents: list[IncidentContext]) -> None:
        """Load resolved incidents into memory. Runs on seeding and on every server start.

        A record the memory already holds is kept as it is: one learned from a live resolution carries the
        engine's graded prediction and outcome analysis, which a rebuild from the database row would lose.
        New records are written in one batch (one file write instead of one per incident).
        """
        resolved = sorted((i for i in incidents if i.status == "resolved" and i.root_cause),
                          key=lambda i: _aware(i.created_at))
        get = getattr(self.memory, "get", None)
        records, new = [], []
        for incident in resolved:
            existing = get(incident.id) if callable(get) else None
            if existing is not None:
                records.append(existing)
                continue
            events = ingest_text(incident.logs, incident_id=incident.id, source="logs",
                                 default_service=incident.service, reference=incident.created_at).events
            record = build_memory_record(
                incident_id=incident.id, title=incident.title, service=incident.service,
                alert_text=incident.alert_text, events=events, root_cause=incident.root_cause,
                resolution=incident.resolution, failed_attempts=incident.failed_attempts, severity=incident.severity,
                created_at=incident.created_at, time_to_resolve_minutes=incident.time_to_resolve_minutes,
                notes=incident.notes,
            )
            records.append(record)
            new.append(record)
        retain_many = getattr(self.memory, "retain_many", None)
        if callable(retain_many):
            await retain_many(new)
        else:
            for record in new:
                await self.memory.retain(record)
        if new:
            logger.info("memory: %d new historical incident(s), %d already known", len(new), len(records) - len(new))
        await self._backtest(resolved, records)

    async def _backtest(self, incidents: list[IncidentContext], records: list) -> None:
        """Grade the engine on resolved history so confidence can be calibrated before live resolutions.

        Each incident is replayed (oldest first) against memory holding only the incidents before it, the
        way the engine would have met it live, and the top hypothesis is graded against the confirmed
        root cause. Deterministic only: no AI calls and no connectors, so seeding stays fast and offline.
        Incidents that already have a grade (e.g. from a live resolution) are left untouched.
        """
        earlier = InProcessMemory()
        engine = InvestigationEngine(memory=earlier, limits=InvestigationLimits(max_llm_calls=0,
                                                                                 max_seconds=self.limits.max_seconds))
        graded = 0
        batch = getattr(self.calibration, "batch", None)
        with batch() if callable(batch) else contextlib.nullcontext():   # one calibration write, not one per grade
            for incident, record in zip(incidents, records, strict=True):
                if incident.id not in self.calibration:
                    try:
                        report = await engine.run(_case(incident))
                    except Exception:  # noqa: BLE001 - one bad historical record must not abort seeding
                        logger.warning("backtest of %s failed; skipping", incident.id, exc_info=True)
                    else:
                        grade = grade_prediction(report.hypotheses, incident.root_cause)
                        self.calibration.record(GradedPrediction(incident.id, report.top_score, grade.verdict,
                                                                 grade.rank_of_actual))
                        graded += 1
                await earlier.retain(record)
        if graded:
            logger.info("backtested %d historical incident(s); %s", graded, self.calibration.report().note)

    async def reset(self) -> None:
        clear = getattr(self.memory, "clear", None)
        if clear is not None:
            await clear()
        self.reports.clear()
        self.evaluations.clear()
        self.calibration.clear()

    def _report_for(self, incident_id: str, created_at: datetime | None) -> InvestigationReport | None:
        """The saved report for this incident, if it was produced for *this* incident.

        Ids restart at INC-1021 with a fresh database while saved reports can outlive it; a report from before
        the incident existed belongs to an older incident with the same id and is never served or graded.
        """
        report = self.reports.get(incident_id)
        if report is not None and created_at is not None and _aware(report.started_at) < _aware(created_at):
            return None
        return report

    def investigation(self, incident_id: str, created_at: datetime | None = None) -> dict | None:
        """The latest full investigation for an incident as JSON-safe data, or None if not analysed yet.

        Contains the failure chain and causal graph, every ranked hypothesis with its score breakdown,
        evidence, anomalies, recalled incidents and lessons, the remediation plan and a step-by-step replay.
        Evidence is already redacted. Intended for a `GET /incidents/{id}/investigation` endpoint.
        Pass the incident's `created_at` so a saved report from an older incident with the same id is never
        returned (ids restart when the database is recreated).
        """
        report = self._report_for(incident_id, created_at)
        if report is None:
            return None
        data = report.model_dump(mode="json")
        evaluation = self.evaluations.get(incident_id)
        if evaluation is not None:
            data["resolution_evaluation"] = evaluation.model_dump(mode="json")
        data["calibration"] = self.calibration.report().model_dump(mode="json")
        return data

    async def aclose(self) -> None:
        await self.connectors.aclose()
        await self.router.aclose()

    # ------------------------------------------------------------------ helpers

    async def _narrate(self, report: InvestigationReport) -> AsyncIterator[str]:
        """Stream the AI explanation, bounded in time; fall back to the deterministic narrative.

        A judge or on-call engineer should never stare at a spinner: if no first token arrives within
        `narrative_first_token_seconds` (rate-limited or slow provider), the deterministic narrative is used.
        """
        if self.stream_narrative and self.router.available:
            emitted = False
            loop = asyncio.get_running_loop()
            ends_at = loop.time() + self.narrative_total_seconds
            request = LLMRequest.of(NARRATIVE_SYSTEM, narrative_prompt(narrative_context(report)),
                                    task="narrative", max_output_tokens=700, temperature=0.3)
            stream = self.router.stream(request).__aiter__()
            try:
                while True:
                    wait = self.narrative_first_token_seconds if not emitted else ends_at - loop.time()
                    if wait <= 0:
                        raise asyncio.TimeoutError
                    try:
                        chunk = await asyncio.wait_for(stream.__anext__(), timeout=wait)
                    except StopAsyncIteration:
                        break
                    emitted = True
                    yield chunk
                if emitted:
                    return
                # Reasoning models can spend the whole token budget thinking and stream nothing.
                logger.warning("narrative stream returned no content; using deterministic narrative")
            except asyncio.TimeoutError:
                logger.warning("narrative stream too slow (emitted=%s); using deterministic narrative", emitted)
                if emitted:
                    yield "\n\n(The AI explanation was cut short to keep the analysis fast. Here is the plan I'd follow:)"
                    return
            except LLMError as exc:
                logger.warning("narrative stream failed (%s); using deterministic narrative", exc.kind.value)
                if emitted:
                    yield "\n\n(The AI explanation was interrupted. Here is the plan I'd follow:)"
                    return
            finally:
                await _close(stream)
        for token in _tokens(deterministic_narrative(report)):
            yield token


def _env_number(name: str, default, kind=int, *, minimum=1):
    """Numeric setting from the environment. A typo or a value below `minimum` logs a warning and uses the
    default, instead of crashing startup with a bare ValueError or silently disabling a limit."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = kind(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    if value < minimum:
        logger.warning("%s=%r must be at least %s; using %s", name, raw, minimum, default)
        return default
    return value


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes and seeds are UTC-aware; sorting a mix of both raises TypeError."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _case(incident: IncidentContext, changes=()) -> IncidentCase:
    return IncidentCase(incident_id=incident.id, title=incident.title, service=incident.service,
                        severity=incident.severity, alert_text=incident.alert_text, logs=incident.logs,
                        created_at=incident.created_at, changes=tuple(changes))


def _memory_summary(root_cause: str | None, resolution: str | None, matching: tuple[str, ...]) -> str:
    parts = []
    if root_cause:
        parts.append(f"Root cause: {root_cause.strip().rstrip('.')}.")
    if resolution:
        parts.append(f"Fix: {resolution.strip().rstrip('.')}.")
    if matching:
        parts.append(f"Matched on {'; '.join(matching)}.")
    return " ".join(parts) or "Similar past incident."


async def _close(stream) -> None:
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception:  # noqa: BLE001 - closing an abandoned provider stream must never fail the analysis
            logger.debug("closing narrative stream failed", exc_info=True)


def _tokens(text: str) -> list[str]:
    return re.findall(r"\S+\s*", text)
