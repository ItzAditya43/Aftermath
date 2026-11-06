import asyncio
from datetime import datetime, timezone

from aftermath_intelligence.anomalies import AnomalyKind, detect_anomalies
from aftermath_intelligence.causal import analyze_chain, blast_radius, build_graph
from aftermath_intelligence.ingestion import ingest_text
from aftermath_intelligence.memory import (ActionType, InProcessMemory, MemoryQuery, build_memory_record, classify_action,
                                           classify_failed_attempt, derive_lessons)
from aftermath_intelligence.models import Severity
from aftermath_intelligence.remediation import RemediationKind, plan_remediation
from aftermath_intelligence.resolution import ResolutionStatus
from aftermath_intelligence.signatures import BY_ID, match_signatures
from aftermath_intelligence.stacktrace import parse_stack_trace, stack_similarity

JAVA = """org.springframework.jdbc.CannotGetJdbcConnectionException: Failed to obtain JDBC Connection; nested exception is java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available
    at org.springframework.jdbc.datasource.DataSourceUtils.getConnection(DataSourceUtils.java:84)
    at com.acme.payments.repo.ChargeRepository.insert(ChargeRepository.java:52)
Caused by: java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available
    at com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:696)
    ... 12 more"""


# --------------------------------------------------------------------------- stack traces

def test_jvm_chain_root_exception_and_application_origin():
    trace = parse_stack_trace(JAVA)
    assert trace.language == "jvm"
    assert trace.outermost.short_type == "CannotGetJdbcConnectionException"
    assert trace.root_exception.type == "java.sql.SQLTransientConnectionException"
    assert trace.origin_frame.function == "com.acme.payments.repo.ChargeRepository.insert"
    assert trace.origin_frame.line == 52
    assert trace.raw_frame_count == 3 + 12
    assert trace.fingerprint() == parse_stack_trace(JAVA.replace(":52)", ":57)")).fingerprint(), "line drift must not change fingerprint"


def test_python_chained_traceback_is_outermost_first():
    text = """Traceback (most recent call last):
  File "/app/db.py", line 10, in connect
    raise ConnectionError("refused")
ConnectionError: refused

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/usr/lib/python3.12/site-packages/x/y.py", line 3, in call
  File "/app/service.py", line 22, in handle
RuntimeError: service unavailable"""
    trace = parse_stack_trace(text)
    assert trace.language == "python"
    assert [e.type for e in trace.exceptions] == ["RuntimeError", "ConnectionError"]
    # The origin is where the root exception (ConnectionError) was raised.
    assert trace.origin_frame.file == "/app/db.py"


def test_node_and_go_traces_and_unknown_is_not_guessed():
    node = parse_stack_trace("TypeError: Cannot read properties of undefined (reading 'id')\n    at handler (/srv/app/routes.js:41:12)\n    at Layer.handle (/srv/node_modules/express/lib/router/layer.js:95:5)")
    assert node.language == "node" and node.origin_frame.file == "/srv/app/routes.js"
    go = parse_stack_trace("panic: runtime error: invalid memory address or nil pointer dereference\n\ngoroutine 1 [running]:\nmain.handle(0x0)\n\t/src/app/main.go:42 +0x1d\nnet/http.HandlerFunc.ServeHTTP(...)\n\t/usr/local/go/src/net/http/server.go:2136 +0x29")
    assert go.language == "go" and go.exceptions[0].type == "panic" and go.origin_frame.function == "main.handle"
    assert parse_stack_trace("just a message").language == "unknown"
    assert stack_similarity(node, node) == 1.0


# --------------------------------------------------------------------------- ingestion formats

def test_jvm_trace_lines_fold_into_the_error_event_with_metadata():
    log = "2026-11-05T14:02:17.300Z ERROR [http-nio-8080-exec-41] c.a.payments.ChargeController - POST /v1/charges failed\n" + JAVA
    result = ingest_text(log, incident_id="i", source="logs", default_service="payments-api")
    assert len(result.events) == 1
    event = result.events[0]
    assert event.metadata["thread"] == "http-nio-8080-exec-41"
    assert event.metadata["logger"] == "c.a.payments.ChargeController"
    assert event.metadata["root_exception"] == "java.sql.SQLTransientConnectionException"
    assert "ChargeRepository.insert" in event.metadata["origin_frame"]
    assert event.service == "payments-api"


def test_logfmt_access_log_compose_prefix_and_docker_json():
    text = "\n".join([
        'ts=2026-11-05T10:00:00Z level=error service=cart msg="redis timeout after 2s" active=10/10',
        '10.0.0.1 - - [05/Nov/2026:10:00:01 +0000] "GET /api/cart HTTP/1.1" 502 157 "-" "curl/8"',
        "api-1  | 2026-11-05T10:00:02Z ERROR upstream timed out",
        '{"log":"2026-11-05T10:00:03Z WARN slow query\\n","stream":"stderr","time":"2026-11-05T10:00:03Z"}',
    ])
    result = ingest_text(text, source="mixed")
    by_format = result.detected_formats
    assert by_format["logfmt"] == 1 and by_format["access-log"] == 1 and by_format["docker-compose"] == 1
    logfmt, access, compose, docker = result.events
    assert logfmt.severity == Severity.ERROR and logfmt.service == "cart"
    assert logfmt.metadata["metrics"]["active_max"] == 10
    assert access.severity == Severity.ERROR and access.error_code == "502" and access.event_type == "access_log"
    assert access.timestamp == datetime(2026, 11, 5, 10, 0, 1, tzinfo=timezone.utc)
    assert compose.service == "api"
    assert docker.message == "2026-11-05T10:00:03Z WARN slow query"


def test_epoch_timestamps_and_size_limit():
    result = ingest_text('{"ts": 1762336800000, "level": "error", "msg": "boom"}\n{"ts": 1762336801, "level": "info", "msg": "ok"}')
    assert result.events[0].timestamp == datetime(2025, 11, 5, 10, 0, tzinfo=timezone.utc)
    assert result.events[1].timestamp.second == 1
    big = ingest_text("ERROR x\n" * 1000, max_bytes=100)
    assert big.truncated and len(big.events) <= 2


# --------------------------------------------------------------------------- signatures

def test_signatures_avoid_common_false_positives():
    assert not BY_ID["bad_gateway"].search("at com.zaxxer.hikari.pool.HikariPool.java:502")
    assert BY_ID["bad_gateway"].search("upstream returned 502")
    assert not BY_ID["auth_failure"].search("processed 401 records")
    assert BY_ID["auth_failure"].search("status=401 unauthorized")
    assert not BY_ID["batch_job"].search("/data/spool/batch-8812.tmp")
    assert BY_ID["batch_job"].search("starting batch run_id=x accounts=10")


def test_alert_only_signals_are_matched_with_alert_provenance():
    matches = match_signatures((), extra_text={"alert": "KubePodCrashLooping: last state OOMKilled"})
    assert {m.id for m in matches} >= {"oom_killed", "crash_loop"}
    assert all(m.event_ids == ("alert",) for m in matches)


# --------------------------------------------------------------------------- anomalies

def test_anomalies_detect_spike_saturation_restarts_and_onset():
    lines = [f"2026-11-05T10:{m:02d}:00Z ERROR service=api timeout" for m in range(6)]
    lines += [f"2026-11-05T10:06:{s:02d}Z ERROR service=api timeout" for s in range(0, 50, 2)]
    lines += ["2026-11-05T10:06:30Z WARN service=api pool stats active=40/40 idle=0 waiting=99"]
    lines += [f"2026-11-05T10:07:0{i}Z WARN service=api Back-off restarting failed container" for i in range(3)]
    events = ingest_text("\n".join(lines)).events
    kinds = {a.kind for a in detect_anomalies(events)}
    assert {AnomalyKind.ONSET, AnomalyKind.ERROR_RATE_SPIKE, AnomalyKind.RESOURCE_SATURATION,
            AnomalyKind.RESTART_FREQUENCY} <= kinds


def test_steady_errors_are_not_anomalous():
    events = ingest_text("\n".join(f"2026-11-05T10:{m:02d}:00Z ERROR service=api timeout" for m in range(10))).events
    assert not [a for a in detect_anomalies(events) if a.kind == AnomalyKind.ERROR_RATE_SPIKE]


def test_new_error_signature_against_baseline():
    baseline = ingest_text("2026-11-04T10:00:00Z ERROR service=api old error").events
    current = ingest_text("2026-11-05T10:00:00Z ERROR service=api brand new failure").events
    assert any(a.kind == AnomalyKind.NEW_ERROR_SIGNATURE for a in detect_anomalies(current, baseline_events=baseline))


# --------------------------------------------------------------------------- causal chain

def _matches(text: str, alert: str = ""):
    return match_signatures(ingest_text(text, default_service="payments-api").events, extra_text={"alert": alert})


def test_chain_separates_root_mechanism_contributor_and_symptom():
    matches = _matches("\n".join([
        "2026-11-05T10:00:00Z WARN Connection leak detection triggered (ProxyLeakTask)",
        "2026-11-05T10:00:05Z ERROR HikariPool-1 - Connection is not available",
        "2026-11-05T10:00:06Z WARN retrying charge attempt 3/5",
        "2026-11-05T10:00:20Z ERROR upstream timed out (504)",
    ]))
    chain = analyze_chain(matches)
    assert chain.root_candidates[0] == "connection_leak"
    assert "connection_pool_exhausted" in chain.mechanisms
    assert "retry_storm" in chain.contributing
    assert "upstream_timeout" in chain.symptoms
    graph = build_graph(matches, chain)
    inferred = {(e.source, e.target) for e in graph.edges if e.relation == "inferred_causes"}
    assert ("sig:connection_leak", "sig:connection_pool_exhausted") in inferred
    assert all(e.basis in {"observed", "inferred", "known"} for e in graph.edges)


def test_cause_seen_long_after_effect_is_not_inferred():
    matches = _matches("2026-11-05T10:00:00Z ERROR HikariPool-1 - Connection is not available\n"
                       "2026-11-05T10:30:00Z WARN Connection leak detection triggered")
    chain = analyze_chain(matches)
    assert "connection_pool_exhausted" in chain.root_candidates
    graph = build_graph(matches, chain)
    assert not [e for e in graph.edges if e.relation == "inferred_causes"]


def test_blast_radius_uses_declared_dependencies():
    radius = blast_radius("payments-api", _matches("2026-11-05T10:00:00Z ERROR HikariPool-1 - Connection is not available"),
                          {"checkout-web": ["payments-api"], "search": ["catalog"]})
    assert radius == {"directly_affected": ["payments-api"], "possibly_affected_downstream": ["checkout-web"]}


# --------------------------------------------------------------------------- memory & lessons

def test_action_classification_and_temporary_detection():
    assert classify_action("Rolling restart of payments-api pods") == ActionType.RESTART
    assert classify_action("Scaled payments-api from 6 to 10 replicas") == ActionType.SCALE_OUT
    assert classify_action("Raised maximumPoolSize from 40 to 60") == ActionType.RAISE_LIMIT
    assert classify_action("Rolled back api-gateway to v2.40.3") == ActionType.ROLLBACK
    assert classify_failed_attempt("restart (pool exhausted again within 6 minutes)") == ResolutionStatus.TEMPORARY
    assert classify_failed_attempt("scaled up (no effect)") == ResolutionStatus.FAILED


def _record(incident_id, root_cause, failed, service="payments-api", logs="2026-01-01T00:00:00Z ERROR HikariPool-1 - Connection is not available"):
    return build_memory_record(incident_id=incident_id, title="pool exhausted", service=service, alert_text="5xx",
                               events=ingest_text(logs, default_service=service).events, root_cause=root_cause,
                               resolution="Hotfixed the leak with try-with-resources", failed_attempts=failed)


def test_failed_attempts_never_become_solutions_and_lessons_warn():
    record = _record("INC-1", "Connection leak in RefundService", ["Rolling restart of pods (exhausted again within 6 minutes)"])
    outcomes = {a.description: a.outcome for a in record.attempts}
    assert outcomes["Rolling restart of pods (exhausted again within 6 minutes)"] == ResolutionStatus.TEMPORARY
    assert outcomes["Hotfixed the leak with try-with-resources"] == ResolutionStatus.VERIFIED
    assert "connection_leak" in record.root_cause_signatures
    memory = InProcessMemory()
    asyncio.run(memory.retain(record))
    recalled = asyncio.run(memory.recall(MemoryQuery(incident_id="NEW", service="payments-api", text="pool exhausted 5xx",
                                                     signatures=("connection_pool_exhausted",))))
    assert recalled and recalled[0].record.incident_id == "INC-1"
    lessons = derive_lessons(recalled)
    avoid = [lesson for lesson in lessons if lesson.kind == "avoid_action"]
    assert avoid and avoid[0].action_type == ActionType.RESTART and "temporary" in avoid[0].message


def test_contradiction_only_when_past_causes_are_disjoint():
    from aftermath_intelligence.memory import RecalledIncident
    a = _record("INC-A", "Connection leak in RefundService", [])
    b = _record("INC-B", "Postgres hit too many connections from the ETL job", [])
    c = _record("INC-C", "Connection leak after a deployment of v2", [])
    lessons = derive_lessons([RecalledIncident(record=a, relevance=0.8), RecalledIncident(record=b, relevance=0.8)])
    assert any(lesson.kind == "contradiction" for lesson in lessons)
    lessons = derive_lessons([RecalledIncident(record=a, relevance=0.8), RecalledIncident(record=c, relevance=0.8)])
    assert not any(lesson.kind == "contradiction" for lesson in lessons)


def test_memory_does_not_match_unrelated_services_without_shared_signatures():
    memory = InProcessMemory()
    asyncio.run(memory.retain(_record("INC-X", "leak", [], service="other", logs="2026-01-01T00:00:00Z ERROR disk full")))
    recalled = asyncio.run(memory.recall(MemoryQuery(incident_id="N", service="payments-api", text="x",
                                                     signatures=("connection_pool_exhausted",))))
    assert recalled == []


# --------------------------------------------------------------------------- remediation safety

def test_remediation_requires_approval_for_state_changes_and_blocks_failed_actions():
    from aftermath_intelligence.memory import Lesson
    from aftermath_intelligence.rca import Hypothesis
    from aftermath_intelligence.signatures import Category
    h = Hypothesis(id="H-1", title="Connection leak", description="d", category=Category.APPLICATION_BUG,
                   signature_id="connection_leak", score=0.8, supporting_evidence_ids=("e1",),
                   verification_steps=BY_ID["connection_leak"].checks)
    lessons = (Lesson(kind="avoid_action", message="restart only temporary relief in INC-1", incident_ids=("INC-1",),
                      action_type=ActionType.RESTART),)
    plan = plan_remediation([h], lessons=lessons, service="payments-api", severity="SEV1")
    assert plan.steps[0].kind == RemediationKind.VERIFY and not plan.steps[0].requires_approval
    assert all(s.requires_approval for s in plan.steps if s.kind in {RemediationKind.MITIGATION, RemediationKind.ROOT_CAUSE_FIX})
    assert not any("Restart" in s.description for s in plan.steps)
    assert any("restart" in b.lower() for b in plan.blocked_actions)
    assert any("SEV1" in w for w in plan.warnings)


def test_unknown_leader_recommends_investigation_not_action():
    from aftermath_intelligence.rca import Hypothesis
    from aftermath_intelligence.signatures import Category
    h = Hypothesis(id="H-0", title="Insufficient evidence", description="d", category=Category.UNKNOWN, score=0.1)
    plan = plan_remediation([h])
    assert plan.steps and all(s.kind in {RemediationKind.VERIFY, RemediationKind.INVESTIGATE} for s in plan.steps)
    assert not any(s.requires_approval for s in plan.steps)
