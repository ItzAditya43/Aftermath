"""Persistent memory/calibration and the read-only Docker / GitHub connectors (no real network)."""
import asyncio
import json
import struct
from datetime import datetime, timezone

import httpx
import pytest

from aftermath_intelligence.calibration import CalibrationTracker, GradedPrediction
from aftermath_intelligence.connectors import DockerConnector, GitHubConnector, connectors_from_env, demux_docker_stream
from aftermath_intelligence.ingestion import ingest_text
from aftermath_intelligence.investigation import IncidentCase, InvestigationEngine
from aftermath_intelligence.memory import MemoryQuery, build_memory_record
from aftermath_intelligence.store import FileMemory, PersistentCalibration
from aftermath_intelligence.tools import ToolRegistry
from tests.test_investigation_engine import CASE, LOGS


def run(coro):
    return asyncio.run(coro)


def _record(incident_id="INC-1"):
    events = ingest_text(LOGS, incident_id=incident_id, default_service="payments-api").events
    return build_memory_record(incident_id=incident_id, title="pool exhausted", service="payments-api",
                               alert_text="5xx on payments-api", events=events,
                               root_cause="Connection leak in chargeback sync", resolution="Close connections",
                               failed_attempts=["Restarted pods, came back in 20 minutes"])


# ---------------------------------------------------------------- persistence

def test_file_memory_survives_restart_and_upserts(tmp_path):
    path = tmp_path / "mem" / "memory.jsonl"
    memory = FileMemory(path)
    run(memory.retain(_record()))
    run(memory.retain(_record()))                     # same id: upsert, not duplicate
    reloaded = FileMemory(path)
    assert len(reloaded) == 1 and reloaded.backend == "local_file"
    query = MemoryQuery(incident_id="INC-NEW", service="payments-api", text="hikari pool exhausted connection leak",
                        signatures=tuple(reloaded.records[0].symptom_signatures))
    assert run(reloaded.recall(query))[0].record.incident_id == "INC-1"
    run(reloaded.clear())
    assert len(FileMemory(path)) == 0


def test_file_memory_skips_corrupt_lines(tmp_path):
    path = tmp_path / "memory.jsonl"
    path.write_text(_record().model_dump_json() + "\n{not json\n")
    memory = FileMemory(path)
    assert len(memory) == 1 and memory.skipped_lines == 1


def test_calibration_persists_and_isotonic_mapping_is_monotone(tmp_path):
    path = tmp_path / "cal.jsonl"
    cal = PersistentCalibration(path, min_samples=10)
    assert cal.calibrate(0.8) is None                 # too few outcomes: still a ranking
    for i in range(20):
        score = i / 20
        cal.record(GradedPrediction(f"I{i}", score, "correct" if score >= 0.5 else "incorrect", 1))
    reloaded = PersistentCalibration(path, min_samples=10)
    assert reloaded.report().graded == 20 and reloaded.report().calibrated
    low, high = reloaded.calibrate(0.1), reloaded.calibrate(0.9)
    assert low == 0.05 and high == 0.95               # clamped, never 0 or 1
    values = [reloaded.calibrate(s / 10) for s in range(11)]
    assert values == sorted(values)


def test_calibration_pools_violations():
    cal = CalibrationTracker(min_samples=4)
    for i, (score, verdict) in enumerate([(0.2, "correct"), (0.4, "incorrect"), (0.6, "correct"), (0.8, "correct")]):
        cal.record(GradedPrediction(f"I{i}", score, verdict, 1))
    assert cal.calibrate(0.3) == 0.5                  # 0.2/0.4 pooled to 1 of 2 correct


# ---------------------------------------------------------------- docker

def _frame(text: str, stream: int = 1) -> bytes:
    data = text.encode()
    return bytes([stream, 0, 0, 0]) + struct.pack(">I", len(data)) + data


def docker_transport(*, oom=False, restarts=0, logs=LOGS, mem_usage=100, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if calls is not None:
            calls.append(request.url.path)
        path = request.url.path
        if path == "/containers/json":
            return httpx.Response(200, json=[{"Id": "abc123", "Names": ["/app-payments-api-1"],
                                              "Labels": {"com.docker.compose.service": "payments-api"}}])
        if path == "/containers/abc123/logs":
            return httpx.Response(200, content=b"".join(_frame(line + "\n") for line in logs.splitlines()))
        if path == "/containers/abc123/json":
            return httpx.Response(200, json={"RestartCount": restarts, "State": {
                "Status": "running", "OOMKilled": oom, "ExitCode": 0, "Health": {"Status": "healthy"}}})
        if path == "/containers/abc123/stats":
            return httpx.Response(200, json={
                "memory_stats": {"usage": mem_usage, "limit": 100},
                "cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 2000, "online_cpus": 2},
                "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000}})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_demux_handles_framed_and_tty_streams():
    assert demux_docker_stream(_frame("a\n") + _frame("b\n", 2)) == "a\nb\n"
    assert demux_docker_stream(b"plain tty output\n") == "plain tty output\n"


def test_docker_tools_summarize_logs_health_and_metrics():
    docker = DockerConnector(transport=docker_transport(oom=True, restarts=5, mem_usage=97))
    logs = run(docker.container_logs("payments-api"))
    assert logs.success and "Connection leak" in logs.summary or "leak" in logs.summary.lower()
    assert logs.data["errors"] >= 1 and "connection_leak" in logs.data["signatures"]
    health = run(docker.container_health("payments-api"))
    assert "OOMKilled=true" in health.summary and "restarted 5 times" in health.summary
    metrics = run(docker.container_metrics("payments-api"))
    assert metrics.data["memory_percent"] == 97.0 and "near exhaustion" in metrics.summary
    assert metrics.data["cpu_percent"] == 20.0


def test_docker_rejects_bad_names_and_reports_missing_container():
    docker = DockerConnector(transport=docker_transport())
    with pytest.raises(ValueError):
        run(docker.resolve("x; rm -rf /"))
    missing = run(docker.container_health("search-api"))
    assert not missing.success and missing.error == "not_found"


def test_docker_unavailable_is_a_failed_result_not_a_crash():
    def boom(request):
        raise httpx.ConnectError("no socket")
    result = run(DockerConnector(transport=httpx.MockTransport(boom)).container_logs("payments-api"))
    assert not result.success and result.error == "ConnectError"


def test_docker_diagnostics_confirm_hypothesis_inside_investigation():
    registry = ToolRegistry()
    calls: list[str] = []
    DockerConnector(transport=docker_transport(calls=calls)).register(registry)
    report = run(InvestigationEngine(tools=registry).run(CASE))
    executed = [d for d in report.diagnostics if d.status == "executed"]
    assert executed and all(d.tool_name.startswith("docker_") for d in executed)
    assert all(not p.endswith(("/start", "/stop", "/restart", "/exec", "/kill")) for p in calls)
    leader = report.hypotheses[0]
    assert leader.signature_id == "connection_leak"
    assert any(f.factor == "diagnostic_confirmation" for f in leader.score_breakdown)


# ---------------------------------------------------------------- github

def github_transport(seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path == "/repos/acme/payments/commits":
            return httpx.Response(200, json=[
                {"sha": "a1b2c3d4e5f6a7b8", "html_url": "https://github.com/acme/payments/commit/a1b2",
                 "author": {"login": "dev1"},
                 "commit": {"message": "Add chargeback sync job\n\nlong body",
                            "committer": {"date": "2026-11-05T13:10:00Z"}}}])
        if path == "/repos/acme/payments/commits/a1b2c3d4e5f6a7b8":
            return httpx.Response(200, json={"files": [
                {"filename": "src/main/java/com/acme/payments/service/ChargeService.java"}]})
        if path == "/repos/acme/payments/actions/runs":
            return httpx.Response(200, json={"workflow_runs": [
                {"id": 77, "name": "Deploy production", "display_title": "release 4.2", "conclusion": "success",
                 "updated_at": "2026-11-05T13:20:00Z", "actor": {"login": "ci"}}]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_github_changes_feed_correlation():
    seen: list[httpx.Request] = []
    gh = GitHubConnector(token="ghp_secret", default_repo="acme/payments", transport=github_transport(seen))
    until = datetime(2026, 11, 5, 14, 2, tzinfo=timezone.utc)
    changes = run(gh.recent_changes("payments-api", until=until))
    assert [c.kind for c in changes] == ["deployment", "commit"]
    commit = changes[1]
    assert commit.title == "Add chargeback sync job" and commit.files[0].endswith("ChargeService.java")
    assert seen[0].headers["Authorization"] == "Bearer ghp_secret"
    report = run(InvestigationEngine().run(CASE.model_copy(update={"changes": changes, "created_at": until})))
    assert report.change_suspects and report.change_suspects[0].change.id == commit.id
    assert any("ChargeService" in f for f in report.change_suspects[0].matched_files)


def test_github_repo_validation_and_unconfigured_service():
    with pytest.raises(ValueError):
        GitHubConnector(default_repo="../../etc")
    gh = GitHubConnector(service_repos={"payments-api": "acme/payments"}, transport=github_transport())
    assert run(gh.recent_changes("search-api")) == ()
    result = run(gh.recent_commits_tool("search-api"))
    assert not result.success and result.error == "not_configured"


def test_github_errors_yield_no_changes():
    gh = GitHubConnector(default_repo="acme/payments", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert run(gh.recent_changes("payments-api")) == ()


def test_connectors_from_env():
    empty = connectors_from_env({})
    assert empty.registry is None and empty.docker is None and empty.github is None
    full = connectors_from_env({"AFTERMATH_DOCKER_TOOLS": "1", "AFTERMATH_SERVICE_REPOS": "payments-api=acme/payments"})
    names = {s.name for s in full.registry.list_tools()}
    assert names == {"docker_container_logs", "docker_container_health", "docker_container_metrics",
                     "github_recent_commits"}
    assert all(s.read_only for s in full.registry.list_tools())
    run(full.aclose())


def test_case_model_accepts_changes_roundtrip():
    assert IncidentCase.model_validate(json.loads(CASE.model_dump_json())).changes == ()
