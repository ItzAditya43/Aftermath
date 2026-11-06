"""Catalog of known failure signatures and the operational knowledge attached to them.

A signature is deterministic: a set of regexes over normalized events. Matching one is an
*observation* ("the logs contain Hikari pool timeouts"), never a conclusion. The catalog also
encodes how signatures typically relate (a leak *can* exhaust a pool, which *can* surface as
504s) so the causal analysis can separate likely origins from downstream symptoms.

Add signatures by appending to `CATALOG`; nothing else needs to change.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .models import NormalizedEvent


class Category(StrEnum):
    APPLICATION_BUG = "application_bug"
    DATABASE = "database"
    CONNECTION_EXHAUSTION = "connection_exhaustion"
    NETWORK = "network"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    DEPLOYMENT_REGRESSION = "deployment_regression"
    CONFIGURATION = "configuration"
    DEPENDENCY_FAILURE = "dependency_failure"
    INFRASTRUCTURE = "infrastructure"
    AUTH = "authentication_authorization"
    EXTERNAL_SERVICE = "external_service"
    CAPACITY = "capacity"
    SECURITY = "security"
    DATA_PIPELINE = "data_pipeline"
    EQUIPMENT = "physical_equipment"
    UNKNOWN = "unknown"


class Role(StrEnum):
    """Where a signature usually sits in a failure chain. Evidence can override this."""

    ORIGIN = "origin"            # usually a root cause when present (leak, expired cert, bad migration)
    MECHANISM = "mechanism"      # the failing resource (pool exhausted, OOM) - explains symptoms, has a cause
    SYMPTOM = "symptom"          # user-visible effect (502, timeouts, crash loop)
    CONTRIBUTOR = "contributor"  # amplifies the failure (retry storm, batch load)
    CHANGE = "change"            # a change event (deploy, config, migration started)


@dataclass(frozen=True)
class Signature:
    id: str
    label: str
    category: Category
    role: Role
    patterns: tuple[str, ...]
    checks: tuple[str, ...] = ()          # read-only verification steps
    mitigations: tuple[str, ...] = ()     # temporary relief; never a root-cause fix
    fixes: tuple[str, ...] = ()           # permanent corrections
    avoid: tuple[str, ...] = ()           # common actions that do not address this failure
    causes: tuple[str, ...] = ()          # signature ids this one commonly leads to
    tool_hints: tuple[str, ...] = ()      # capability keywords for the diagnostic planner
    _compiled: tuple[re.Pattern[str], ...] = field(default=(), compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_compiled", tuple(re.compile(p, re.I) for p in self.patterns))

    def search(self, text: str) -> re.Match[str] | None:
        for pattern in self._compiled:
            match = pattern.search(text)
            if match:
                return match
        return None


CORE_CATALOG: tuple[Signature, ...] = (
    Signature(
        "connection_leak", "database connection leak", Category.APPLICATION_BUG, Role.ORIGIN,
        (r"connection leak", r"proxyleaktask", r"apparent connection leak", r"unclosed connection",
         r"connection (?:was )?not (?:returned|closed|released)", r"getconnection\(\) without close"),
        checks=("Find the code path named in the leak-detection stack trace and check it closes connections on every path (including errors/timeouts)",
                "Compare active vs idle connections over time: a leak shows active climbing while traffic is flat"),
        mitigations=("Restart the affected instances to release leaked connections (relief only; the leak will refill the pool)",),
        fixes=("Close connections in a finally / try-with-resources / context manager on every path",
               "Enable leak detection (e.g. Hikari leakDetectionThreshold) in production to catch regressions"),
        avoid=("Raising the pool size only delays exhaustion when connections are leaking",
               "Scaling out replicas multiplies leaked connections against the database limit"),
        causes=("connection_pool_exhausted", "db_too_many_connections"),
        tool_hints=("logs", "github", "commit", "code"),
    ),
    Signature(
        "connection_pool_exhausted", "connection pool exhaustion", Category.CONNECTION_EXHAUSTION, Role.MECHANISM,
        (r"connection pool (?:is )?exhausted", r"hikaripool-\d+ - connection is not available",
         r"queuepool limit", r"pool exhausted", r"cannotgetjdbcconnection", r"timeout waiting for (?:a )?connection",
         r"waiting=\d{2,}", r"pending=\d{2,}", r"unable to acquire (?:jdbc )?connection"),
        checks=("Read pool metrics (active / idle / pending) and the database's own connection count",
                "Identify which code paths or jobs hold connections longest (thread names in leak/slow logs)"),
        mitigations=("Restart instances to free held connections (temporary)",
                     "Pause the batch job or traffic source holding connections"),
        fixes=("Fix whatever holds connections too long (leak, long transaction, slow query)",
               "Size the pool from measured concurrency, below the database connection limit"),
        avoid=("Increasing pool size or replicas without finding the holder",),
        causes=("upstream_timeout", "bad_gateway", "liveness_probe_failed"),
        tool_hints=("database", "metrics", "logs", "container"),
    ),
    Signature(
        "long_transaction", "long-held transaction / slow I/O inside a transaction", Category.APPLICATION_BUG, Role.ORIGIN,
        (r"idle in transaction", r"long[- ]running transaction", r"held (?:a |the )?(?:db )?connection while",
         r"transaction (?:open|held) for \d+"),
        checks=("Query pg_stat_activity (or equivalent) for sessions idle in transaction and their age",),
        mitigations=("Terminate the long-running session after confirming it is safe",),
        fixes=("Move network or file I/O outside database transactions",
               "Set idle_in_transaction_session_timeout / statement timeouts"),
        causes=("connection_pool_exhausted", "db_lock_contention"),
        tool_hints=("database", "logs"),
    ),
    Signature(
        "db_too_many_connections", "database connection limit reached", Category.DATABASE, Role.MECHANISM,
        (r"too many clients", r"too many connections", r"remaining connection slots are reserved",
         r"max_connections"),
        checks=("Group database sessions by application/user to find the client holding the most connections",),
        mitigations=("Cap or restart the client holding the most connections",),
        fixes=("Put a connection pooler (e.g. PgBouncer) in front of the database",
               "Limit per-worker connections in the offending client"),
        avoid=("Raising max_connections without addressing the client (memory cost; needs a DB restart)",),
        causes=("connection_pool_exhausted", "upstream_timeout"),
        tool_hints=("database", "metrics"),
    ),
    Signature(
        "db_lock_contention", "database lock contention", Category.DATABASE, Role.MECHANISM,
        (r"deadlock detected", r"lock timeout", r"accessexclusivelock", r"canceling statement due to lock timeout",
         r"waiting for (?:\w+ )?lock", r"lock wait timeout exceeded"),
        checks=("Inspect pg_locks joined with pg_stat_activity for the blocking session and its query",),
        mitigations=("Cancel the blocking session if it is safe to do so",),
        fixes=("Run schema changes with non-blocking variants (CREATE INDEX CONCURRENTLY, expand/contract migrations)",
               "Set lock_timeout on migrations so they fail fast instead of blocking traffic"),
        avoid=("Restarting application pods: new queries queue behind the same lock",),
        causes=("upstream_timeout", "connection_pool_exhausted"),
        tool_hints=("database", "logs"),
    ),
    Signature(
        "db_migration", "schema migration", Category.DEPLOYMENT_REGRESSION, Role.CHANGE,
        (r"\bmigration \d+", r"running migrations?", r"alter table .* (?:type|add column)", r"alter column",
         r"create (?:unique )?index (?!concurrently)", r"flyway|liquibase|alembic upgrade"),
        checks=("Check which migration ran, whether it rewrites or locks a large table, and when it started",),
        mitigations=("Cancel the migration if it is blocking traffic and safe to abort",),
        fixes=("Rewrite the migration to be online (concurrent index, batched backfill)",),
        causes=("db_lock_contention",),
        tool_hints=("github", "deployment", "database"),
    ),
    Signature(
        "db_unavailable", "database unreachable", Category.DATABASE, Role.MECHANISM,
        (r"could not connect to server", r"connection refused.*(?:5432|3306|27017)", r"the database system is (?:starting up|shutting down)",
         r"server closed the connection unexpectedly", r"communications link failure", r"no pg_hba\.conf entry"),
        checks=("Check database health, failover events and network reachability from the application",),
        fixes=("Address the database outage or network path; add retry with backoff for transient failover",),
        causes=("upstream_timeout", "bad_gateway"),
        tool_hints=("database", "health", "network"),
    ),
    Signature(
        "oom_killed", "out-of-memory kill", Category.RESOURCE_EXHAUSTION, Role.MECHANISM,
        (r"oomkilled", r"oom[- ]?kill", r"out of memory: kill", r"exit code:? ?137", r"memory limit (?:exceeded|reached)",
         r"killed process \d+", r"memory cgroup out of memory"),
        checks=("Compare container memory usage with its limit over the incident window",
                "Identify what workload was running at the time of the kill (batch, large request, cache growth)"),
        mitigations=("Raise the memory limit temporarily while the cause is found",),
        fixes=("Stream or chunk large workloads instead of loading them into memory",
               "Fix unbounded caches / collections"),
        avoid=("Restarting or adding replicas when one job's memory use is the cause: each replica OOMs the same way",),
        causes=("crash_loop", "liveness_probe_failed", "bad_gateway"),
        tool_hints=("container", "metrics", "docker", "resource"),
    ),
    Signature(
        "heap_exhausted", "application out of memory (heap exhausted)", Category.RESOURCE_EXHAUSTION, Role.MECHANISM,
        (r"outofmemoryerror", r"java heap space", r"gc overhead limit exceeded", r"\bmemoryerror\b",
         r"javascript heap out of memory", r"fatal error: runtime: out of memory", r"cannot allocate memory",
         r"(?<!: )\bout of memory\b(?!: kill)"),
        checks=("Read heap / GC metrics for the process over the incident window",
                "Capture a heap dump or allocation profile from the failing instance, if safe"),
        mitigations=("Raise the heap / memory limit temporarily while the cause is found",),
        fixes=("Stream or chunk large workloads instead of loading them into memory",
               "Fix unbounded caches / collections"),
        avoid=("Restarting only resets the heap; the same workload fills it again",),
        causes=("oom_killed", "crash_loop", "upstream_timeout"),
        tool_hints=("metrics", "logs", "resource"),
    ),
    Signature(
        "cpu_saturation", "CPU saturation / throttling", Category.RESOURCE_EXHAUSTION, Role.MECHANISM,
        (r"cpu thrott", r"cpu (?:usage|utili[sz]ation)[^\d]{0,12}(?:9\d|100)(?:\.\d+)?%", r"load average: \d{2,}",
         r"high cpu"),
        checks=("Check CPU usage and throttling metrics per container against its limit",),
        mitigations=("Scale out or raise the CPU limit temporarily",),
        fixes=("Profile the hot path that consumes CPU",),
        causes=("upstream_timeout", "liveness_probe_failed"),
        tool_hints=("container", "metrics", "resource"),
    ),
    Signature(
        "disk_full", "disk full", Category.RESOURCE_EXHAUSTION, Role.MECHANISM,
        (r"no space left on device", r"disk (?:is )?full", r"enospc", r"disk usage (?:9\d|100)%"),
        checks=("Check filesystem usage and the largest recent writers (logs, temp files)",),
        mitigations=("Free space by rotating/removing safe-to-delete files",),
        fixes=("Add log rotation / retention and disk usage alerting",),
        causes=("crash_loop",),
        tool_hints=("container", "metrics", "resource"),
    ),
    Signature(
        "crash_loop", "container crash loop", Category.INFRASTRUCTURE, Role.SYMPTOM,
        (r"crashloop(?:backoff|ing)", r"back-off restarting failed container", r"restart(?:ed)? count[:= ]+\d{2,}",
         r"container exited with (?:code )?[1-9]"),
        checks=("Read the previous container's logs and exit code to find why it exits",),
        tool_hints=("container", "docker", "logs"),
    ),
    Signature(
        "liveness_probe_failed", "health probe failures", Category.INFRASTRUCTURE, Role.SYMPTOM,
        (r"liveness probe failed", r"readiness probe failed", r"health ?check failed", r"unhealthy"),
        checks=("Check what the health endpoint depends on (DB, cache) and its latency",),
        causes=("crash_loop",),
        tool_hints=("container", "health"),
    ),
    Signature(
        "bad_gateway", "502 Bad Gateway", Category.DEPENDENCY_FAILURE, Role.SYMPTOM,
        (r"(?<![\w.:/-])502(?![\w.:])", r"bad gateway", r"upstream prematurely closed", r"no live upstreams",
         r"upstream connect error"),
        checks=("Check upstream health and recent gateway/proxy configuration changes",),
        tool_hints=("logs", "health", "deployment"),
    ),
    Signature(
        "upstream_timeout", "upstream / request timeouts", Category.DEPENDENCY_FAILURE, Role.SYMPTOM,
        (r"(?<![\w.:/-])504(?![\w.:])", r"upstream timed out", r"readtimeout", r"read timed out", r"context deadline exceeded",
         r"sockettimeoutexception", r"request timed out", r"gateway timeout", r"timeouterror"),
        checks=("Find which dependency is slow using per-dependency latency or traces",),
        tool_hints=("logs", "metrics", "network"),
    ),
    Signature(
        "keepalive_mismatch", "proxy/upstream keep-alive timeout mismatch", Category.CONFIGURATION, Role.ORIGIN,
        (r"idle_timeout", r"keepalive_timeout", r"keep-alive timeout", r"upstream_idle", r"connection reset by peer.*upstream"),
        checks=("Compare the proxy's upstream idle timeout with the upstream server's keep-alive timeout (proxy must be lower)",),
        mitigations=("Roll back the configuration change that altered the timeout",),
        fixes=("Set proxy idle timeout below upstream keep-alive and add a config test that asserts it",),
        avoid=("Restarting gateway pods: the mismatch returns as soon as connections idle",),
        causes=("bad_gateway",),
        tool_hints=("github", "deployment", "config"),
    ),
    Signature(
        "network_failure", "network connectivity failure", Category.NETWORK, Role.MECHANISM,
        (r"connection reset", r"econnreset", r"econnrefused", r"no route to host", r"network is unreachable",
         r"name or service not known", r"enotfound", r"nxdomain", r"temporary failure in name resolution",
         r"i/o timeout"),
        checks=("Test connectivity and DNS resolution from the failing service to its dependency",),
        causes=("upstream_timeout", "bad_gateway"),
        tool_hints=("network", "health"),
    ),
    Signature(
        "tls_cert_expired", "TLS certificate problem", Category.CONFIGURATION, Role.ORIGIN,
        (r"certificate (?:has )?expired", r"x509: certificate", r"cert_has_expired", r"sslhandshakeexception",
         r"certificate verify failed", r"unable to get local issuer certificate"),
        checks=("Check certificate expiry dates on the endpoint and the renewal job's logs",),
        mitigations=("Manually issue and install a valid certificate",),
        fixes=("Fix automated renewal and alert on certificates expiring within 14 days",),
        avoid=("Restarting the renewal controller without fixing why renewal fails",),
        tool_hints=("config", "logs"),
    ),
    Signature(
        "jwt_key_mismatch", "JWT signing-key mismatch", Category.AUTH, Role.ORIGIN,
        (r"\bjwks\b", r"no matching key", r"signature verification failed", r"\bkid\b", r"invalid signature",
         r"jwt (?:signature|validation)"),
        checks=("Compare the token's kid with the keys currently served by the JWKS endpoint",),
        mitigations=("Refresh verifier JWKS caches", "Re-sign with the previous key until the new key is published"),
        fixes=("Publish new keys well before signing with them (pre-publication window)",),
        avoid=("Restarting verifiers if the new key is still not published",),
        causes=("auth_failure",),
        tool_hints=("config", "logs"),
    ),
    Signature(
        "auth_failure", "authentication/authorization failures", Category.AUTH, Role.SYMPTOM,
        (r"(?:status|http|code|returned)[ =:]*40[13](?![\w.:])", r"(?<![\w.:/-])40[13] (?:unauthori[sz]ed|forbidden)", r"unauthori[sz]ed", r"forbidden", r"invalid credentials", r"access denied",
         r"permission denied"),
        checks=("Check credential/secret versions and recent rotations without exposing values",),
        tool_hints=("config", "logs"),
    ),
    Signature(
        "webhook_signature", "webhook signature failure", Category.CONFIGURATION, Role.ORIGIN,
        (r"stripe-signature", r"webhook signature", r"no signatures found matching", r"signature mismatch"),
        checks=("Verify the configured signing secret matches the provider dashboard",),
        mitigations=("Update the signing secret and replay failed webhook deliveries",),
        fixes=("Manage provider secrets through the secret store with rotation runbooks",),
        avoid=("Redeploying with the same configuration",),
        tool_hints=("config",),
    ),
    Signature(
        "redis_latency", "Redis latency / blocking", Category.DEPENDENCY_FAILURE, Role.MECHANISM,
        (r"redis.*(?:timeout|timed out|latency)", r"jedis", r"ioredis", r"slowlog", r"busy redis",
         r"command timed out"),
        checks=("Check Redis SLOWLOG, latency, memory and connected clients",),
        causes=("upstream_timeout",),
        tool_hints=("metrics", "logs"),
    ),
    Signature(
        "blocking_command", "blocking O(N) command", Category.APPLICATION_BUG, Role.ORIGIN,
        (r"\bkeys \S*\*", r"\bflushall\b", r"\bsmembers\b.*large"),
        checks=("Find the caller issuing the blocking command (endpoint, job)",),
        mitigations=("Disable the endpoint/job issuing the command",),
        fixes=("Replace KEYS with SCAN / maintain an index set",),
        avoid=("Failing over to a replica: the same command blocks it too",),
        causes=("redis_latency",),
        tool_hints=("github", "code", "logs"),
    ),
    Signature(
        "stale_assets", "stale frontend assets", Category.DEPLOYMENT_REGRESSION, Role.SYMPTOM,
        (r"chunkloaderror", r"loading chunk \d+ failed", r"failed to fetch dynamically imported module",
         r"loading css chunk"),
        checks=("Check the CDN serves the asset manifest from the latest build and old chunks still exist",),
        mitigations=("Purge the CDN cache for HTML/manifest",),
        fixes=("Keep previous build assets for a grace period; give HTML a short Cache-Control",),
        tool_hints=("deployment", "config"),
    ),
    Signature(
        "retry_storm", "retry amplification", Category.CAPACITY, Role.CONTRIBUTOR,
        (r"\bretry(?:ing)?\b.*(?:attempt|\d+/\d+)", r"retries exhausted", r"retry storm", r"backoff",
         r"attempt \d+ of \d+"),
        checks=("Compare request rate to the dependency with user traffic: a multiplier indicates retries",),
        fixes=("Add jittered exponential backoff, retry budgets and circuit breakers",),
        causes=("connection_pool_exhausted", "cpu_saturation"),
        tool_hints=("metrics", "logs"),
    ),
    Signature(
        "batch_job", "batch / scheduled job load", Category.CAPACITY, Role.CONTRIBUTOR,
        (r"\bbatch (?:job|run|process)", r"starting batch\b", r"\bbatch\b(?! ?[-_]?\d)(?=.*\b(?:rows|accounts|recipients|records)=)",
         r"cronjob", r"\bdigest\b", r"nightly (?:job|export|batch)", r"\betl\b", r"export ?job", r"scheduled job"),
        checks=("Check which scheduled jobs started near the first error and their resource use",),
        mitigations=("Pause or reschedule the job",),
        fixes=("Chunk the job and bound its concurrency/connections",),
        causes=("oom_killed", "heap_exhausted", "connection_pool_exhausted", "db_too_many_connections", "cpu_saturation"),
        tool_hints=("container", "logs", "metrics"),
    ),
    Signature(
        "thread_pool_exhausted", "worker/thread pool exhaustion", Category.CAPACITY, Role.MECHANISM,
        (r"rejectedexecutionexception", r"thread pool (?:is )?(?:exhausted|full)", r"worker pool (?:is )?full",
         r"queue capacity"),
        checks=("Check thread-pool active/queued counts and what blocks worker threads",),
        causes=("upstream_timeout",),
        tool_hints=("metrics", "logs"),
    ),
    Signature(
        "rate_limited", "rate limited by a dependency", Category.EXTERNAL_SERVICE, Role.MECHANISM,
        (r"(?<![\w.:/-])429(?![\w.:])", r"too many requests", r"rate limit(?:ed| exceeded)", r"quota exceeded", r"throttl(?:ed|ing) by"),
        checks=("Check request volume to the dependency and its published limits",),
        fixes=("Add client-side rate limiting and backoff; request a higher quota",),
        causes=("upstream_timeout",),
        tool_hints=("metrics", "logs"),
    ),
    Signature(
        "external_dependency_error", "third-party service failure", Category.EXTERNAL_SERVICE, Role.MECHANISM,
        (r"(?:stripe|twilio|sendgrid|paypal|s3|aws|gcp|provider|third[- ]party).{0,40}(?:timed? ?out|unavailable|5\d\d|error)",),
        checks=("Check the provider's status page and per-provider error/latency metrics",),
        mitigations=("Fail over / degrade the feature that depends on the provider",),
        causes=("upstream_timeout",),
        tool_hints=("health", "logs"),
    ),
    Signature(
        "deploy_event", "recent deployment", Category.DEPLOYMENT_REGRESSION, Role.CHANGE,
        (r"\bdeploy(?:ed|ment|ing)\b", r"\brollout\b", r"\brelease v?\d", r"\bv\d+\.\d+\.\d+\b", r"helm upgrade",
         r"image (?:updated|changed)", r"new replicaset"),
        checks=("Diff the deployed release against the previous one and line its timestamp up with the first error",),
        mitigations=("Roll back to the previous release if errors started right after it",),
        tool_hints=("github", "deployment", "commit", "workflow"),
    ),
    Signature(
        "config_change", "configuration change", Category.CONFIGURATION, Role.CHANGE,
        (r"configmap", r"config(?:uration)? (?:change|update|reload)", r"feature flag", r"env(?:ironment)? var",
         r"_SIZE=\d+", r"setting .* to"),
        checks=("Diff the active configuration against the last known good version",),
        mitigations=("Revert the configuration change",),
        tool_hints=("github", "config", "deployment"),
    ),
    Signature(
        "null_reference", "application exception (null/undefined access)", Category.APPLICATION_BUG, Role.ORIGIN,
        (r"nullpointerexception", r"cannot read propert(?:y|ies) of (?:undefined|null)", r"attributeerror: 'nonetype'",
         r"nil pointer dereference", r"\bkeyerror\b", r"\btypeerror\b"),
        checks=("Locate the origin frame in the stack trace and check the change history of that file",),
        fixes=("Fix the code path and add a regression test",),
        tool_hints=("github", "commit", "code"),
    ),
)

# Domain packs (security, network, kubernetes, data pipelines, industrial equipment) extend the core
# catalog. They import `Signature` from this module, so they are loaded only after it is defined.
from .packs import enabled_signatures  # noqa: E402

CATALOG: tuple[Signature, ...] = CORE_CATALOG + enabled_signatures()
BY_ID: dict[str, Signature] = {s.id: s for s in CATALOG}
if len(BY_ID) != len(CATALOG):
    raise RuntimeError("duplicate signature id between the core catalog and a pack")


@dataclass(frozen=True)
class SignatureMatch:
    signature: Signature
    event_ids: tuple[str, ...]
    count: int
    first_seen: datetime | None
    last_seen: datetime | None
    services: tuple[str, ...]
    snippet: str

    @property
    def id(self) -> str:
        return self.signature.id


def match_signatures(events: Iterable[NormalizedEvent], *, extra_text: dict[str, str] | None = None,
                     catalog: tuple[Signature, ...] = CATALOG) -> tuple[SignatureMatch, ...]:
    """Match every signature against every event (message + stack + raw).

    `extra_text` maps a pseudo event id (e.g. "alert") to text such as the alert body, so
    signals that appear only in the alert are still counted, with honest provenance.
    """
    hits: dict[str, list[tuple[NormalizedEvent | None, str, str]]] = defaultdict(list)
    for event in events:
        text = "\n".join(filter(None, (event.message, event.stack_trace, event.raw if event.raw != event.message else None)))
        for signature in catalog:
            match = signature.search(text)
            if match:
                hits[signature.id].append((event, event.event_id, _snippet(text, match)))
    for pseudo_id, text in (extra_text or {}).items():
        for signature in catalog:
            match = signature.search(text)
            if match:
                hits[signature.id].append((None, pseudo_id, _snippet(text, match)))
    output: list[SignatureMatch] = []
    for sig_id, items in hits.items():
        stamps = [e.timestamp for e, _, _ in items if e is not None and e.timestamp]
        output.append(SignatureMatch(
            signature=BY_ID.get(sig_id) or next(s for s in catalog if s.id == sig_id),
            event_ids=tuple(dict.fromkeys(eid for _, eid, _ in items)),
            count=len(items),
            first_seen=min(stamps) if stamps else None,
            last_seen=max(stamps) if stamps else None,
            services=tuple(sorted({e.service for e, _, _ in items if e is not None and e.service})),
            snippet=items[0][2],
        ))
    return tuple(sorted(output, key=lambda m: (m.first_seen is None, m.first_seen or datetime.max, -m.count)))


def _snippet(text: str, match: re.Match[str], radius: int = 60) -> str:
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return re.sub(r"\s+", " ", text[start:end]).strip()[:200]
