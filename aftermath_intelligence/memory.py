"""Historical incident memory: the contract, a local stand-in, recall scoring and lessons.

`IncidentMemory` is the seam for the memory owner's Hindsight adapter. Until that exists,
`InProcessMemory` keeps records in this process only. It reports itself as
``backend="in_process"`` so no output ever implies Hindsight was consulted.

The *lessons* computed here are the product's core promise: past failed or temporary
fixes become warnings, and similar symptoms with different verified causes become
contradiction alerts instead of a blind "do what we did last time".
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .resolution import ResolutionStatus
from .signatures import Role, match_signatures
from .models import NormalizedEvent

_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "was", "were", "are", "has", "have", "not", "after",
    "during", "error", "errors", "failed", "failing", "alert", "firing", "high", "rate", "on", "in", "of",
    "warn", "info", "true", "false", "null", "exec", "http", "nio", "threshold", "5m",
}
_TOKEN = re.compile(r"[a-z][a-z0-9_-]{2,}")


class ActionType(StrEnum):
    RESTART = "restart"
    SCALE_OUT = "scale_out"
    RAISE_LIMIT = "raise_limit"
    ROLLBACK = "rollback"
    REDEPLOY = "redeploy"
    CONFIG_CHANGE = "config_change"
    CODE_FIX = "code_fix"
    FAILOVER = "failover"
    CACHE_PURGE = "cache_purge"
    KILL_SESSION = "kill_session"
    PAUSE_JOB = "pause_job"
    WAIT = "wait"
    OTHER = "other"


_ACTION_PATTERNS: tuple[tuple[ActionType, str], ...] = (
    (ActionType.ROLLBACK, r"\broll(?:ed|ing)? ?back\b|\brevert(?:ed)?\b(?! the config)"),
    (ActionType.RESTART, r"\brestart(?:ed|ing)?\b|\brecycl(?:e|ed)\b|\bbounce(?:d)?\b|\breboot(?:ed)?\b"),
    (ActionType.SCALE_OUT, r"\bscal(?:e|ed|ing) (?:out|up)?\b|\breplicas?\b|\badd(?:ed)? (?:more )?(?:pods|nodes|instances)"),
    (ActionType.RAISE_LIMIT, r"\brais(?:e|ed|ing)\b|\bincreas(?:e|ed|ing)\b|\bbump(?:ed)?\b|max_connections|maximumpoolsize|memory limit"),
    (ActionType.REDEPLOY, r"\bredeploy(?:ed)?\b"),
    (ActionType.FAILOVER, r"\bfail(?:ed)? ?over\b"),
    (ActionType.CACHE_PURGE, r"\bpurg(?:e|ed)\b|\bflush(?:ed)? (?:the )?cache\b|\bcache\b.*\bclear"),
    (ActionType.KILL_SESSION, r"\bkill(?:ed)?\b|\bterminat(?:e|ed)\b|\bcancel(?:led|ed)?\b"),
    (ActionType.PAUSE_JOB, r"\bpaus(?:e|ed)\b|\bdisabl(?:e|ed) the (?:job|cron)|\breschedul"),
    (ActionType.WAIT, r"\bwait(?:ed)?\b"),
    (ActionType.CODE_FIX, r"\bhotfix(?:ed)?\b|\bfix(?:ed)? the\b|\bpatch(?:ed)?\b|try-with-resources|\bclose\(\)"),
    (ActionType.CONFIG_CHANGE, r"\bconfig\b|\bset\b|\bchang(?:e|ed)\b|\bupdat(?:e|ed)\b|\brotat(?:e|ed)\b"),
)

_TEMPORARY_HINTS = re.compile(
    r"came back|returned|again within|again after|recurred|temporar|briefly|for (?:a few|\d+) min|bought|"
    r"exhausted again|re-?occurred|only helped|short[- ]lived", re.I)


def classify_action(text: str) -> ActionType:
    lowered = text.lower()
    for action, pattern in _ACTION_PATTERNS:
        if re.search(pattern, lowered):
            return action
    return ActionType.OTHER


def classify_failed_attempt(text: str) -> ResolutionStatus:
    """A reported 'what failed' entry is either a pure failure or a temporary relief."""
    return ResolutionStatus.TEMPORARY if _TEMPORARY_HINTS.search(text) else ResolutionStatus.FAILED


def keywords(text: str) -> frozenset[str]:
    return frozenset(t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS)


class AttemptRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    action_type: ActionType
    outcome: ResolutionStatus


class MemoryRecord(BaseModel):
    """What one resolved incident teaches. This is the payload a memory backend retains."""

    model_config = ConfigDict(frozen=True)

    incident_id: str
    title: str
    service: str
    severity: str | None = None
    created_at: datetime | None = None
    symptom_signatures: tuple[str, ...] = ()
    root_cause_signatures: tuple[str, ...] = ()
    stack_fingerprints: tuple[str, ...] = ()
    exception_types: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    root_cause: str | None = None
    resolution: str | None = None
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    attempts: tuple[AttemptRecord, ...] = ()
    time_to_resolve_minutes: int | None = None
    notes: str | None = None
    predicted_root_cause: str | None = None
    prediction_correct: bool | None = None

    def retain_text(self) -> str:
        """Natural-language rendering for text-based memory backends (e.g. Hindsight retain)."""
        lines = [f"Incident {self.incident_id} on {self.service}: {self.title}."]
        if self.root_cause:
            lines.append(f"Confirmed root cause: {self.root_cause}.")
        if self.resolution:
            lines.append(f"Fix that worked ({self.resolution_status.value}): {self.resolution}.")
        for attempt in self.attempts:
            if attempt.outcome != ResolutionStatus.VERIFIED:
                lines.append(f"Attempt that did NOT resolve it ({attempt.outcome.value}): {attempt.description}.")
        if self.time_to_resolve_minutes is not None:
            lines.append(f"Time to resolve: {self.time_to_resolve_minutes} minutes.")
        if self.symptom_signatures:
            lines.append("Signatures: " + ", ".join(self.symptom_signatures) + ".")
        return " ".join(lines)


class MemoryQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    service: str
    text: str
    signatures: tuple[str, ...] = ()
    stack_fingerprints: tuple[str, ...] = ()
    exception_types: tuple[str, ...] = ()
    limit: int = 5


class RecalledIncident(BaseModel):
    model_config = ConfigDict(frozen=True)

    record: MemoryRecord
    relevance: float = Field(ge=0, le=1)
    shared_signatures: tuple[str, ...] = ()
    matching_points: tuple[str, ...] = ()
    differences: tuple[str, ...] = ()


@runtime_checkable
class IncidentMemory(Protocol):
    backend: str

    async def recall(self, query: MemoryQuery) -> list[RecalledIncident]: ...

    async def retain(self, record: MemoryRecord) -> None: ...


class InProcessMemory:
    """Process-local memory. Not persistent and not Hindsight; used until that adapter lands."""

    backend = "in_process"

    def __init__(self, *, threshold: float = 0.35):
        self.threshold = threshold
        self._records: dict[str, MemoryRecord] = {}

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return tuple(self._records.values())

    async def retain(self, record: MemoryRecord) -> None:
        self._records[record.incident_id] = record

    async def clear(self) -> None:
        self._records.clear()

    async def recall(self, query: MemoryQuery) -> list[RecalledIncident]:
        return score_candidates(query, self._records.values(), threshold=self.threshold)[: query.limit]


def score_candidates(query: MemoryQuery, records: Iterable[MemoryRecord], *,
                     threshold: float = 0.35) -> list[RecalledIncident]:
    """Explainable similarity: signatures 45%, service 20%, keywords 20%, stack/exception 15%."""
    query_sigs = {s for s in query.signatures if _role(s) != Role.CHANGE}
    query_kw = keywords(query.text)
    output: list[RecalledIncident] = []
    for record in records:
        if record.incident_id == query.incident_id:
            continue
        record_sigs = {s for s in record.symptom_signatures if _role(s) != Role.CHANGE}
        shared = query_sigs & record_sigs
        union = query_sigs | record_sigs
        sig_score = len(shared) / len(union) if union else 0.0
        if not shared and record.service != query.service:
            continue
        kw_union = query_kw | set(record.keywords)
        kw_score = len(query_kw & set(record.keywords)) / len(kw_union) if kw_union else 0.0
        service_score = 1.0 if record.service == query.service else 0.0
        stack_shared = set(query.stack_fingerprints) & set(record.stack_fingerprints)
        exc_shared = {e.rsplit(".", 1)[-1] for e in query.exception_types} & {e.rsplit(".", 1)[-1] for e in record.exception_types}
        stack_score = 1.0 if stack_shared else (0.6 if exc_shared else 0.0)
        score = 0.45 * sig_score + 0.20 * service_score + 0.20 * kw_score + 0.15 * stack_score
        if score < threshold:
            continue
        matching = []
        if shared:
            matching.append("shared signatures: " + ", ".join(sorted(shared)))
        if service_score:
            matching.append(f"same service ({record.service})")
        if stack_shared:
            matching.append("identical stack trace fingerprint")
        elif exc_shared:
            matching.append("same exception types: " + ", ".join(sorted(exc_shared)))
        differences = []
        if record.service != query.service:
            differences.append(f"different service ({record.service} vs {query.service})")
        only_now = sorted(query_sigs - record_sigs)
        only_then = sorted(record_sigs - query_sigs)
        if only_now:
            differences.append("only in current incident: " + ", ".join(only_now))
        if only_then:
            differences.append("only in past incident: " + ", ".join(only_then))
        output.append(RecalledIncident(record=record, relevance=round(min(score, 0.99), 3),
                                       shared_signatures=tuple(sorted(shared)), matching_points=tuple(matching),
                                       differences=tuple(differences)))
    output.sort(key=lambda r: (r.relevance, r.record.created_at or datetime.min), reverse=True)
    return output


def _role(signature_id: str) -> Role | None:
    from .signatures import BY_ID
    signature = BY_ID.get(signature_id)
    return signature.role if signature else None


# --------------------------------------------------------------------------- lessons

class Lesson(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str                 # avoid_action | proven_fix | contradiction | recurrence
    message: str
    incident_ids: tuple[str, ...]
    action_type: ActionType | None = None


_ACTION_LABELS = {
    ActionType.RESTART: "Restarting", ActionType.SCALE_OUT: "Scaling out", ActionType.RAISE_LIMIT: "Raising limits",
    ActionType.ROLLBACK: "Rolling back", ActionType.REDEPLOY: "Redeploying", ActionType.CONFIG_CHANGE: "Changing config",
    ActionType.CODE_FIX: "A code fix", ActionType.FAILOVER: "Failing over", ActionType.CACHE_PURGE: "Purging the cache",
    ActionType.KILL_SESSION: "Killing sessions", ActionType.PAUSE_JOB: "Pausing the job", ActionType.WAIT: "Waiting",
}


def derive_lessons(recalled: Iterable[RecalledIncident]) -> tuple[Lesson, ...]:
    recalled = tuple(recalled)
    lessons: list[Lesson] = []
    failed_by_action: dict[ActionType, list[tuple[str, AttemptRecord]]] = {}
    for item in recalled:
        for attempt in item.record.attempts:
            if attempt.outcome in {ResolutionStatus.FAILED, ResolutionStatus.TEMPORARY}:
                failed_by_action.setdefault(attempt.action_type, []).append((item.record.incident_id, attempt))
    for action, entries in failed_by_action.items():
        ids = tuple(dict.fromkeys(i for i, _ in entries))
        outcomes = {a.outcome for _, a in entries}
        kind = "only gave temporary relief" if outcomes == {ResolutionStatus.TEMPORARY} else "did not resolve it"
        example = entries[0][1].description.rstrip(".")
        if action == ActionType.OTHER:
            # No action family to name: quote what was actually done instead of "other ...".
            message = f"\"{example[:140]}\" {kind} in {', '.join(ids)}"
        else:
            label = _ACTION_LABELS.get(action, action.value.replace("_", " "))
            message = f"{label} {kind} in {', '.join(ids)} (e.g. \"{example[:140]}\")"
        lessons.append(Lesson(kind="avoid_action", action_type=action, incident_ids=ids, message=message))
    for item in recalled:
        record = item.record
        if record.resolution and record.resolution_status in {ResolutionStatus.VERIFIED, ResolutionStatus.PARTIAL}:
            lessons.append(Lesson(kind="proven_fix", incident_ids=(record.incident_id,),
                                  action_type=classify_action(record.resolution),
                                  message=f"{record.incident_id} was resolved by: {record.resolution.rstrip('.')[:240]}"))
    # Similar symptoms, different verified causes: the history itself disagrees.
    causes: dict[frozenset[str], list[str]] = {}
    for item in recalled:
        if item.record.root_cause_signatures and item.relevance >= 0.4:
            causes.setdefault(frozenset(item.record.root_cause_signatures), []).append(item.record.incident_id)
    disjoint = len(causes) > 1 and all(not (a & b) for a in causes for b in causes if a is not b)
    if disjoint:
        groups = "; ".join(f"{', '.join(sorted(sig))} ({', '.join(ids)})" for sig, ids in causes.items())
        lessons.append(Lesson(kind="contradiction", incident_ids=tuple(i for ids in causes.values() for i in ids),
                              message=f"Similar symptoms had different root causes before: {groups}. "
                                      "Confirm which applies before reusing a past fix."))
    if len(recalled) >= 2:
        ttrs = sorted((r.record for r in recalled if r.record.time_to_resolve_minutes and r.record.created_at),
                      key=lambda r: r.created_at)
        if len(ttrs) >= 2:
            lessons.append(Lesson(kind="recurrence", incident_ids=tuple(r.incident_id for r in ttrs),
                                  message=f"This pattern recurred {len(ttrs)} times; time to resolve went from "
                                          f"{ttrs[0].time_to_resolve_minutes} min ({ttrs[0].incident_id}) to "
                                          f"{ttrs[-1].time_to_resolve_minutes} min ({ttrs[-1].incident_id})."))
    return tuple(lessons)


# --------------------------------------------------------------------------- record construction

def build_memory_record(*, incident_id: str, title: str, service: str, alert_text: str,
                        events: Iterable[NormalizedEvent], root_cause: str | None, resolution: str | None,
                        failed_attempts: Iterable[str] = (), severity: str | None = None,
                        created_at: datetime | None = None, time_to_resolve_minutes: int | None = None,
                        notes: str | None = None, resolution_status: ResolutionStatus | None = None,
                        predicted_root_cause: str | None = None,
                        prediction_correct: bool | None = None) -> MemoryRecord:
    events = tuple(events)
    matches = match_signatures(events, extra_text={"alert": f"{title}\n{alert_text}"})
    root_matches = match_signatures((), extra_text={"root": root_cause or ""})
    attempts = [AttemptRecord(description=a, action_type=classify_action(a), outcome=classify_failed_attempt(a))
                for a in failed_attempts if a.strip()]
    if resolution:
        status = resolution_status or ResolutionStatus.VERIFIED
        attempts.append(AttemptRecord(description=resolution, action_type=classify_action(resolution), outcome=status))
    else:
        status = resolution_status or ResolutionStatus.UNRESOLVED
    return MemoryRecord(
        incident_id=incident_id, title=title, service=service, severity=severity, created_at=created_at,
        symptom_signatures=tuple(m.id for m in matches),
        root_cause_signatures=tuple(m.id for m in root_matches if m.signature.role in {Role.ORIGIN, Role.MECHANISM, Role.CHANGE}),
        stack_fingerprints=tuple(sorted({str(e.metadata["stack_fingerprint"]) for e in events if "stack_fingerprint" in e.metadata})),
        exception_types=tuple(sorted({e.exception_type for e in events if e.exception_type})),
        keywords=tuple(sorted(keywords(f"{title} {alert_text}")))[:60],
        root_cause=root_cause, resolution=resolution, resolution_status=status, attempts=tuple(attempts),
        time_to_resolve_minutes=time_to_resolve_minutes, notes=notes,
        predicted_root_cause=predicted_root_cause, prediction_correct=prediction_correct,
    )


__all__ = [
    "ActionType", "AttemptRecord", "IncidentMemory", "InProcessMemory", "Lesson", "MemoryQuery", "MemoryRecord",
    "RecalledIncident", "build_memory_record", "classify_action", "classify_failed_attempt", "derive_lessons",
    "keywords", "score_candidates",
]
