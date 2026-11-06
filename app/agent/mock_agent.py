"""A realistic fake agent. No LLM and no network calls.

It keeps an in-memory "memory bank" of resolved incidents (filled by ingest_history and
learn_from_resolution, like Hindsight would be). It recalls by matching service, known error
signatures and keyword overlap, then streams a templated explanation token by token and
finishes with a structured suggestion built from what worked and what failed before.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.agent.interface import (
    AgentEvent,
    IncidentContext,
    MemoryEvent,
    ResolutionContext,
    SuggestionEvent,
    TokenEvent,
)

# label -> (human description, regexes). Matched against title + alert + logs (lowercased).
SIGNATURES: dict[str, tuple[str, list[str]]] = {
    "pool_exhausted": (
        "connection pool exhaustion",
        [r"connection pool (?:is )?exhausted", r"hikaripool-\d+ - connection is not available",
         r"queuepool limit", r"pool exhausted", r"cannotgetjdbcconnection"],
    ),
    "pg_too_many_clients": (
        "Postgres connection limit",
        [r"too many clients", r"too many connections", r"remaining connection slots are reserved"],
    ),
    "oom": ("out-of-memory kill", [r"oomkilled", r"out of memory", r"exit code:? 137", r"memoryerror"]),
    "bad_gateway": (
        "502 Bad Gateway",
        [r"\b502\b", r"bad gateway", r"upstream prematurely closed", r"no live upstreams"],
    ),
    "upstream_timeout": (
        "upstream timeout",
        [r"\b504\b", r"upstream timed out", r"readtimeout", r"context deadline exceeded"],
    ),
    "deploy": ("recent deploy", [r"\bdeploy(?:ed|ment)?\b", r"\brollout\b", r"\brelease v?\d"]),
    "lock_contention": (
        "lock contention",
        [r"deadlock detected", r"lock timeout", r"accessexclusivelock", r"canceling statement due to lock timeout"],
    ),
    "jwks": ("JWT signing-key mismatch", [r"\bjwks\b", r"no matching key", r"signature verification failed", r"\bkid\b"]),
    "redis": ("Redis latency", [r"\bredis\b", r"jedis", r"ioredis"]),
    "stale_assets": (
        "stale frontend assets",
        [r"chunkloaderror", r"loading chunk \d+ failed", r"failed to fetch dynamically imported module"],
    ),
    "tls_cert": ("TLS certificate expiry", [r"certificate (?:has )?expired", r"x509: certificate"]),
    "webhook_sig": ("webhook signature failure", [r"stripe-signature", r"webhook signature", r"no signatures found matching"]),
    "batch_job": ("batch job load", [r"\bbatch\b", r"\bdigest\b", r"cronjob"]),
    "autovacuum": ("table bloat / autovacuum", [r"autovacuum", r"dead tuples", r"\bbloat"]),
}

# Generic first steps when nothing in memory matches.
PLAYBOOK: dict[str, list[str]] = {
    "pool_exhausted": ["Check pool metrics (active/idle/pending) and DB connection count",
                       "Look for code paths that open connections without closing them"],
    "pg_too_many_clients": ["Run SELECT count(*), usename, application_name FROM pg_stat_activity GROUP BY 2,3",
                            "Identify the client holding the most connections and cap or restart it"],
    "oom": ["Compare pod memory usage with its limit (kubectl top pod / describe pod)",
            "Check what workload was running when the container was killed"],
    "bad_gateway": ["Check upstream health checks and recent gateway config changes",
                    "Roll back the latest gateway deploy if errors started right after it"],
    "upstream_timeout": ["Find which downstream dependency is slow (traces, p99 per dependency)"],
    "deploy": ["Diff the latest deploy against the previous release and roll back if correlated"],
    "lock_contention": ["Inspect pg_locks / pg_stat_activity for blocking queries", "Cancel the blocking session if safe"],
    "jwks": ["Compare the token's kid with the keys served by the JWKS endpoint", "Refresh the JWKS cache"],
    "redis": ["Check Redis latency, memory and evictions"],
    "stale_assets": ["Check that the CDN serves the asset manifest from the latest build", "Purge the CDN cache"],
    "tls_cert": ["Check certificate expiry dates on the affected endpoint", "Renew and redeploy the certificate"],
    "webhook_sig": ["Verify the webhook signing secret matches the provider dashboard"],
}

_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "was", "were", "are", "has", "have", "not", "after",
    "during", "error", "errors", "failed", "failing", "alert", "firing", "high", "rate", "on", "in", "of",
    "warn", "info", "true", "false", "null", "exec", "http", "nio",
}
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_-]{2,}")


def detect_signatures(text: str) -> dict[str, str]:
    """Return {label: first matching snippet} for every signature found in `text`."""
    lowered = text.lower()
    found: dict[str, str] = {}
    for label, (_, patterns) in SIGNATURES.items():
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                found[label] = text[match.start() : match.end()]
                break
    return found


def _keywords(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS}


@dataclass
class MemoryEntry:
    incident: IncidentContext
    signatures: dict[str, str] = field(default_factory=dict)
    keywords: set[str] = field(default_factory=set)

    @classmethod
    def from_context(cls, incident: IncidentContext) -> "MemoryEntry":
        text = f"{incident.title}\n{incident.alert_text}\n{incident.logs}"
        return cls(incident, detect_signatures(text), _keywords(f"{incident.title} {incident.alert_text}"))


@dataclass
class Match:
    entry: MemoryEntry
    score: float
    shared: list[str]


class MockAgent:
    name = "mock"

    def __init__(self, token_delay_ms: int = 30, max_memories: int = 3, threshold: float = 0.45):
        self.token_delay = max(token_delay_ms, 0) / 1000
        self.max_memories = max_memories
        self.threshold = threshold
        self.memory: dict[str, MemoryEntry] = {}

    # -- AgentService ---------------------------------------------------------

    async def ingest_history(self, incidents: list[IncidentContext]) -> None:
        for incident in incidents:
            if incident.status == "resolved" and incident.root_cause:
                self.memory[incident.id] = MemoryEntry.from_context(incident)

    async def learn_from_resolution(self, incident: IncidentContext, resolution: ResolutionContext) -> None:
        learned = incident.model_copy(
            update={
                "status": "resolved",
                "root_cause": resolution.root_cause,
                "resolution": resolution.fix_that_worked,
                "failed_attempts": list(resolution.what_failed),
                "notes": resolution.notes,
            }
        )
        self.memory[incident.id] = MemoryEntry.from_context(learned)

    async def reset(self) -> None:
        self.memory.clear()

    async def analyze(self, incident: IncidentContext) -> AsyncIterator[AgentEvent]:
        current = MemoryEntry.from_context(incident)
        matches = self.recall(current)

        for match in matches:
            await self._sleep(8)
            past = match.entry.incident
            yield MemoryEvent(
                past_incident_id=past.id,
                title=past.title,
                service=past.service,
                summary=f"Root cause: {_clean(past.root_cause)}. Fix: {_clean(past.resolution)}.",
                relevance_score=round(min(match.score, 0.99), 2),
                root_cause=past.root_cause,
                resolution=past.resolution,
                failed_attempts=list(past.failed_attempts),
                time_to_resolve_minutes=past.time_to_resolve_minutes,
            )

        await self._sleep(5)
        text = self._narrative(incident, current, matches)
        for token in re.findall(r"\S+\s*", text):
            yield TokenEvent(text=token)
            await self._sleep(1)

        await self._sleep(5)
        yield self._suggestion(incident, current, matches)

    # -- recall ---------------------------------------------------------------

    def recall(self, current: MemoryEntry) -> list[Match]:
        matches: list[Match] = []
        for entry in self.memory.values():
            if entry.incident.id == current.incident.id:
                continue
            shared = [label for label in current.signatures if label in entry.signatures]
            if not shared:
                continue
            sig_score = len(shared) / len(current.signatures)
            union = current.keywords | entry.keywords
            kw_score = len(current.keywords & entry.keywords) / len(union) if union else 0.0
            service_score = 1.0 if entry.incident.service == current.incident.service else 0.0
            score = 0.30 * service_score + 0.40 * sig_score + 0.30 * kw_score
            if score >= self.threshold:
                matches.append(Match(entry, score, shared))
        matches.sort(key=lambda m: (m.score, m.entry.incident.created_at), reverse=True)
        return matches[: self.max_memories]

    # -- output ---------------------------------------------------------------

    def _narrative(self, incident: IncidentContext, current: MemoryEntry, matches: list[Match]) -> str:
        if not matches:
            labels = [SIGNATURES[s][0] for s in current.signatures]
            seen = f" The logs point to {_join(labels)}." if labels else ""
            return (
                f"I couldn't find a similar incident in memory for {incident.service}, so this is based on the "
                f"alert and logs alone.{seen} Treat the plan below as a starting point and record what works "
                f"when you resolve this, so the next on-call gets a better answer."
            )

        best = matches[0]
        past = best.entry.incident
        n = len(matches)
        parts = [
            f"I found {n} similar incident{'s' if n > 1 else ''} in memory. ",
            f"The closest match is {past.id} ({past.title}, {past.created_at:%b %d}), which shows the same "
            f"{_join([SIGNATURES[s][0] for s in best.shared])} signature",
            f"{' on the same service' if past.service == incident.service else ''}. ",
            f"Root cause then: {_clean(past.root_cause)}. What fixed it: {_clean(past.resolution)}.\n\n",
        ]

        failed = [(m.entry.incident.id, a) for m in matches for a in m.entry.incident.failed_attempts]
        if failed:
            listed = "; ".join(f"{_clean(a)} ({pid})" for pid, a in failed[:3])
            parts.append(f"What did NOT work before: {listed}. I'd skip those and go straight to the known fix.\n\n")

        history = sorted(
            (m.entry.incident for m in matches if m.entry.incident.time_to_resolve_minutes),
            key=lambda i: i.created_at,
        )
        if len(history) >= 2:
            parts.append(
                f"This pattern has come up {len(history)} times before. Time to resolve dropped from "
                f"{history[0].time_to_resolve_minutes} min ({history[0].id}) to "
                f"{history[-1].time_to_resolve_minutes} min ({history[-1].id}) once the fix was known.\n\n"
            )

        for other in matches[1:]:
            o = other.entry.incident
            parts.append(f"{o.id} is also relevant ({o.service}): {_clean(o.root_cause)}.\n\n")

        parts.append("Here is the plan I'd follow:")
        return "".join(parts)

    def _suggestion(self, incident: IncidentContext, current: MemoryEntry, matches: list[Match]) -> SuggestionEvent:
        if not matches:
            steps = [step for label in current.signatures for step in PLAYBOOK.get(label, [])][:4] or [
                f"Check {incident.service} dashboards: error rate, latency, saturation",
                "Check deploys and config changes from the last 2 hours and roll back if correlated",
                "Pull logs around the alert start time and look for the first error",
            ]
            steps.append("Watch error rate for 15 minutes before resolving")
            return SuggestionEvent(
                likely_root_cause=(
                    f"Unknown: no past incident matches. Signals point to {_join([SIGNATURES[s][0] for s in current.signatures])}."
                    if current.signatures
                    else "Unknown: no past incident matches and no known error signature in the logs."
                ),
                steps=steps,
                warnings=["No similar incident in memory; verify each step before acting."],
                confidence=0.35 if current.signatures else 0.2,
                based_on=[],
            )

        best = matches[0]
        past = best.entry.incident
        evidence = best.entry.signatures.get(best.shared[0]) and current.signatures.get(best.shared[0])
        steps = []
        if evidence:
            steps.append(f'Confirm the signature: look for "{evidence}" in {incident.service} logs')
        steps += _split_steps(past.resolution or "")
        steps.append("Watch error rate and latency for 15 minutes before resolving")

        warnings = [f"Avoid: {_clean(a)}. It did not work in {m.entry.incident.id}"
                    for m in matches for a in m.entry.incident.failed_attempts][:4]
        if incident.severity == "SEV1":
            warnings.append("SEV1: post a status update before applying changes")

        confidence = min(0.95, 0.4 + 0.5 * best.score + 0.05 * (len(matches) - 1))
        return SuggestionEvent(
            likely_root_cause=f"Likely a repeat of {past.id}: {_clean(past.root_cause)}",
            steps=steps,
            warnings=warnings,
            confidence=round(confidence, 2),
            based_on=[m.entry.incident.id for m in matches],
        )

    async def _sleep(self, units: int) -> None:
        if self.token_delay:
            await asyncio.sleep(self.token_delay * units)


def _clean(text: str | None) -> str:
    return (text or "").strip().rstrip(".")


def _join(items: list[str]) -> str:
    items = list(dict.fromkeys(items))
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# Resolutions are written in past tense ("Rolled back X"); steps read better as instructions ("Roll back X").
_IMPERATIVE = {
    "added": "Add", "cancelled": "Cancel", "changed": "Change", "deployed": "Deploy", "disabled": "Disable",
    "drained": "Drain", "enabled": "Enable", "fixed": "Fix", "forced": "Force", "hotfixed": "Hotfix",
    "increased": "Increase", "issued": "Issue", "kept": "Keep", "killed": "Kill", "lowered": "Lower",
    "manually": "Manually", "moved": "Move", "paused": "Pause", "pinned": "Pin", "purged": "Purge",
    "raised": "Raise", "re-ran": "Re-run", "redeployed": "Redeploy", "renamed": "Rename", "replaced": "Replace",
    "replayed": "Replay", "reset": "Reset", "rescheduled": "Reschedule", "restarted": "Restart",
    "restored": "Restore", "resumed": "Resume", "reverted": "Revert", "rolled": "Roll", "rotated": "Rotate",
    "routed": "Route", "scaled": "Scale", "set": "Set", "switched": "Switch", "terminated": "Terminate",
    "updated": "Update", "uploaded": "Upload",
}


def _imperative(step: str) -> str:
    first, _, rest = step.partition(" ")
    verb = _IMPERATIVE.get(first.lower())
    if verb is None:
        return step[0].upper() + step[1:]
    if verb == "Manually":
        inner = _imperative(rest)
        return "Manually " + inner[0].lower() + inner[1:]
    # "... and restarted the job" -> "... and restart the job"
    rest = re.sub(
        r"\band ([a-z-]+)\b",
        lambda m: "and " + _IMPERATIVE.get(m.group(1), m.group(1)).lower(),
        rest,
    )
    return f"{verb} {rest}".strip()


def _split_steps(resolution: str) -> list[str]:
    parts = re.split(r";\s+|\.\s+(?=[A-Z])", resolution)
    return [_imperative(p) for p in (_clean(p) for p in parts) if p][:5]
