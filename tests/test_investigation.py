from datetime import timezone
from hashlib import sha256

from aftermath_intelligence.agent import InvestigationAgent, InvestigationStatus
from aftermath_intelligence.correlation import correlate_events
from aftermath_intelligence.engine import EvidenceSource, IncidentIntelligenceEngine
from aftermath_intelligence.evidence import EvidenceLedger
from aftermath_intelligence.evaluation import EvaluationCase, compare_before_after, evaluate_cases
from aftermath_intelligence.hypotheses import generate_deterministic_hypotheses, generate_llm_hypotheses
from aftermath_intelligence.ingestion import ingest_text
from aftermath_intelligence.planner import plan_with_llm
from aftermath_intelligence.postmortem import build_postmortem
from aftermath_intelligence.preventive import derive_preventive_signals
from aftermath_intelligence.providers import LLMResponse
from aftermath_intelligence.redaction import redact_sensitive_text
from aftermath_intelligence.resolution import ResolutionInput, ResolutionStatus, evaluate_resolution
from aftermath_intelligence.similarity import (HistoricalIncident, detect_historical_contradictions,
                                              find_similar_incidents, historical_record,
                                              cluster_historical_incidents)
from aftermath_intelligence.tools import RiskLevel, ToolResult, ToolSpec, validate_tool_arguments


class FakeRouter:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete_json(self, *args, **kwargs):
        self.calls += 1
        return self.response, LLMResponse(text="{}", provider="gemini", model="test-model")


def _incident_events():
    text = (
        '{"timestamp":"2026-11-05T09:00:00Z","severity":"INFO","event_type":"deployment","service":"checkout","message":"release v2","commit_sha":"x"}\n'
        '{"timestamp":"2026-11-05T09:02:00Z","severity":"ERROR","service":"checkout","message":"DatabaseError: connection pool exhausted"}\n'
        '{"timestamp":"2026-11-05T09:03:00Z","severity":"ERROR","service":"checkout","message":"DatabaseError: connection pool exhausted"}\n'
    )
    return ingest_text(text, incident_id="inc-test", source="api.jsonl").events


def test_pretty_json_and_python_traceback_normalization():
    pretty = ingest_text('''{
      "timestamp": "2026-11-05T10:00:00+05:30",
      "severity": "error", "message": "request failed", "service": "api"
    }''', incident_id="i")
    assert len(pretty.events) == 1
    assert pretty.events[0].timestamp.tzinfo == timezone.utc
    trace = ingest_text("Traceback (most recent call last):\n  File \"app.py\", line 10\nDatabaseError: pool exhausted")
    assert len(trace.events) == 1
    assert trace.events[0].exception_type == "DatabaseError"
    assert "pool exhausted" in trace.events[0].stack_trace
    repeated = ingest_text(
        "Traceback (most recent call last):\n  File \"a.py\", line 1\nValueError: first\n"
        "Traceback (most recent call last):\n  File \"b.py\", line 2\nValueError: second\n"
        "Traceback (most recent call last):\n  File \"a.py\", line 1\nValueError: first"
    )
    assert len(repeated.events) == 2
    assert repeated.duplicate_count == 1
    assert any('b.py' in event.stack_trace for event in repeated.events)
    connector = ingest_text('{"timestamp":"2026-11-05T10:00:00Z","container_name":"/checkout-1",'
                            '"workflow_run":{"id":42,"conclusion":"failure"},"message":"job failed"}')
    assert connector.events[0].service == "checkout-1"
    assert connector.events[0].metadata["workflow_run"]["id"] == 42


def test_temporal_deployment_correlation_and_evidence_links():
    events = _incident_events()
    correlations = correlate_events(events)
    assert any(edge.relation == "deployment_precedes_error" for edge in correlations.edges)
    ledger = EvidenceLedger()
    ledger.add_event_facts(events)
    ledger.add_correlations(correlations)
    hypotheses = generate_deterministic_hypotheses(ledger.items, correlations)
    deploy = next(h for h in hypotheses if h.hypothesis_id == sha256(b"deployment_regression").hexdigest()[:20])
    assert deploy.supporting_evidence_ids
    assert all(ledger.by_id(evidence_id) for evidence_id in deploy.supporting_evidence_ids)


def test_llm_hypotheses_cannot_cite_unknown_evidence():
    ledger = EvidenceLedger()
    ledger.add_event_facts(_incident_events())
    first = ledger.items[0]
    router = FakeRouter({"hypotheses": [{
        "title": "db issue", "explanation": "possible db issue", "confidence": 0.8,
        "supporting_evidence_ids": [first.evidence_id, "invented-id"],
        "contradicting_evidence_ids": [], "assumptions": [],
    }]})
    hypotheses, response = generate_llm_hypotheses(ledger.items, router)
    assert response.provider == "gemini"
    assert len(hypotheses) == 1
    assert hypotheses[0].supporting_evidence_ids == (first.evidence_id,)
    assert hypotheses[0].status.value == "unverified"


def test_llm_planner_uses_only_declared_read_only_tools_and_arguments():
    spec = ToolSpec(name="docker.inspect", description="Read container health", risk=RiskLevel.READ_ONLY,
                    read_only=True, allowed_parameters=("container_id",), required_parameters=("container_id",),
                    parameter_types={"container_id": "string"})
    router = FakeRouter({"requests": [
        {"tool_name": "docker.inspect", "arguments": {"container_id": "abc"},
         "purpose": "check health", "evidence_gap": "health unknown"},
        {"tool_name": "shell.exec", "arguments": {"command": "rm -rf /"},
         "purpose": "bad", "evidence_gap": "bad"},
        {"tool_name": "docker.inspect", "arguments": {"command": "docker ps"},
         "purpose": "bad", "evidence_gap": "bad"},
    ]})
    plan, _ = plan_with_llm([], [], [spec], router)
    assert len(plan.requests) == 1
    assert plan.requests[0].arguments == {"container_id": "abc"}
    try:
        validate_tool_arguments(spec, {"container_id": "abc", "command": "anything"})
    except ValueError as exc:
        assert "unsupported" in str(exc) or "shell" in str(exc)
    else:
        raise AssertionError("unsafe arguments should be rejected")


def test_agent_executes_read_only_but_never_executes_state_changing_tools():
    class ReadOnlyTools:
        calls = 0
        def list_tools(self):
            return (ToolSpec(name="database.health", description="database connection pool health",
                             risk=RiskLevel.READ_ONLY, read_only=True),)
        def execute(self, tool_name, arguments):
            self.calls += 1
            return ToolResult(tool_name=tool_name, success=True, summary="database diagnostics completed")

    readonly = ReadOnlyTools()
    result = InvestigationAgent(max_steps=3).run("inc-db", _incident_events(), tools=readonly, use_llm=False)
    assert readonly.calls == 1
    assert result.tool_results[0].success
    assert any(event.kind == "tool_succeeded" for event in result.replay)

    class MutatingTools:
        calls = 0
        def list_tools(self):
            return (ToolSpec(name="deployment.rollback", description="rollback deployment release",
                             risk=RiskLevel.HIGH, read_only=False),)
        def execute(self, tool_name, arguments):
            self.calls += 1
            raise AssertionError("state changing call must not run")

    mutating = MutatingTools()
    result = InvestigationAgent(max_steps=3).run("inc-deploy", _incident_events(), tools=mutating, use_llm=False)
    assert mutating.calls == 0
    assert result.status == InvestigationStatus.AWAITING_APPROVAL


def test_resolution_feedback_similarity_and_evaluation_metrics():
    verified = evaluate_resolution(ResolutionInput(incident_id="i1", action_taken="fixed pool size",
                                                    errors_before=20, errors_after=0,
                                                    human_verified=True, evidence_ids=("e1",)))
    assert verified.status == ResolutionStatus.VERIFIED
    temporary = evaluate_resolution(ResolutionInput(incident_id="i2", action_taken="restart",
                                                     errors_before=20, errors_after=0,
                                                     regression_observed=True))
    assert temporary.status == ResolutionStatus.TEMPORARY
    assert verified.memory_ready_payload()["incident_id"] == "i1"
    events = _incident_events()
    history = [HistoricalIncident("old-a", frozenset({"db timeout"}), frozenset({"checkout"}),
                                   frozenset({"deployment_regression"}), ResolutionStatus.VERIFIED)]
    similar = find_similar_incidents(events, history, threshold=0)
    assert similar and similar[0].similarity > 0
    conflict = HistoricalIncident("old-b", frozenset({"db timeout"}), frozenset({"checkout"}),
                                  frozenset({"database_capacity"}), ResolutionStatus.VERIFIED)
    assert detect_historical_contradictions(history + [conflict], minimum_overlap=0)
    metrics = evaluate_cases([EvaluationCase(frozenset({"db"}), frozenset({"db"}),
                                              frozenset({"e1"}), frozenset({"e1"}), 0)])
    assert metrics.precision == metrics.recall == metrics.evidence_coverage == 1
    assert metrics.unsafe_actions == 0
    comparison = compare_before_after(
        [EvaluationCase(frozenset({"deployment_regression"}), frozenset({"database_capacity"}),
                        frozenset(), frozenset(), 1)],
        [EvaluationCase(frozenset({"database_capacity"}), frozenset({"database_capacity"}),
                        frozenset({"e1"}), frozenset({"e1"}), 0)],
    )
    assert comparison.precision_delta > 0
    assert comparison.evidence_coverage_delta > 0
    assert comparison.unsafe_action_delta == -1


def test_history_adapter_prevention_clusters_and_postmortem():
    events = _incident_events()
    old = historical_record("old", events, verified_root_causes=("database_capacity",))

    class History:
        def retrieve_incidents(self, events, *, limit=10):
            assert limit == 10
            return (old,)

    result = InvestigationAgent(max_steps=2).run("current", events, history=History(), use_llm=False)
    assert result.similar_incidents[0].incident_id == "old"
    assert any(item.kind.value == "historical" for item in result.evidence)
    assert cluster_historical_incidents((old, HistoricalIncident(
        "old-2", old.fingerprints, old.services, frozenset(), ResolutionStatus.UNRESOLVED))) == (("old", "old-2"),)
    assert derive_preventive_signals((old, HistoricalIncident(
        "old-2", old.fingerprints, old.services, frozenset(), ResolutionStatus.UNRESOLVED)))
    postmortem = build_postmortem(result)
    assert postmortem.incident_id == "current"
    assert postmortem.evidence_ids
    assert postmortem.root_cause_candidates


def test_full_engine_and_secret_redaction():
    engine = IncidentIntelligenceEngine()
    result = engine.investigate("demo", [EvidenceSource("log", '{"severity":"error","message":"api_key=hidden gsk_12345678901234567890"}')],
                                use_llm=False)
    assert result.report.incident_id == "demo"
    assert "hidden" not in result.report.observed_facts[0].summary
    assert "gsk_" not in result.report.observed_facts[0].summary
    assert "[REDACTED]" in redact_sensitive_text("password=hunter2")
    structured = redact_sensitive_text('{"api_key":"do-not-send", "password": "also private"}')
    assert '"api_key":"[REDACTED]"' in structured
    assert "do-not-send" not in structured and "also private" not in structured
