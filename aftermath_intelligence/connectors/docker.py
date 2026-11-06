"""Read-only Docker diagnostics through the Docker Engine HTTP API (unix socket).

No shell, no `docker` CLI, no exec: every call is a fixed GET endpoint with validated
parameters, so model output cannot turn into a command. Containers are resolved by the
incident's service name (container name or docker-compose service label).

Tools (all `read_only`):
  docker_container_logs    recent logs, summarized through the signature catalog
  docker_container_health  state, restart count, OOM kill flag, health-check status
  docker_container_metrics one-shot CPU / memory usage against limits
"""
from __future__ import annotations

import json
import re
import struct
import time
from typing import Any

import httpx

from ..ingestion import ingest_text
from ..models import Severity
from ..signatures import match_signatures
from ..tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec

_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
MAX_LOG_LINES = 2000


class DockerConnector:
    def __init__(self, *, socket_path: str = "/var/run/docker.sock", base_url: str = "http://docker",
                 timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout,
            transport=transport or httpx.AsyncHTTPTransport(uds=socket_path))

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ lookup

    async def resolve(self, service: str) -> dict[str, Any] | None:
        if not _NAME.match(service):
            raise ValueError("invalid service/container name")
        response = await self._client.get("/containers/json", params={"all": "true"})
        response.raise_for_status()
        for item in response.json():
            names = [n.lstrip("/") for n in item.get("Names", [])]
            labels = item.get("Labels") or {}
            if service in names or labels.get("com.docker.compose.service") == service:
                return item
        return None  # exact name or compose service only; never guess a different container

    async def _container(self, service: str) -> tuple[dict[str, Any] | None, ToolResult | None]:
        try:
            item = await self.resolve(service)
        except (httpx.HTTPError, OSError) as exc:
            return None, ToolResult(tool_name="docker", success=False, summary="Docker API unavailable",
                                    error=type(exc).__name__)
        if item is None:
            return None, ToolResult(tool_name="docker", success=False, summary=f"no container found for {service}",
                                    error="not_found")
        return item, None

    # ------------------------------------------------------------------ tools

    async def container_logs(self, service: str, since_minutes: int = 60) -> ToolResult:
        name = "docker_container_logs"
        item, failure = await self._container(service)
        if failure:
            return failure.model_copy(update={"tool_name": name})
        since = int(time.time()) - max(1, min(int(since_minutes), 24 * 60)) * 60
        response = await self._client.get(f"/containers/{item['Id']}/logs", params={
            "stdout": "true", "stderr": "true", "timestamps": "true", "since": str(since), "tail": str(MAX_LOG_LINES)})
        if response.status_code >= 400:
            return ToolResult(tool_name=name, success=False, summary="log fetch failed", error=str(response.status_code))
        text = demux_docker_stream(response.content)
        events = ingest_text(text, incident_id="diagnostic", source="docker", default_service=service).events
        errors = [e for e in events if e.severity in {Severity.ERROR, Severity.CRITICAL}]
        matches = match_signatures(events)
        if matches:
            top = "; ".join(f"{m.signature.label} x{m.count}: \"{m.snippet[:110]}\"" for m in matches[:3])
            summary = f"{service} container logs (last {since_minutes}m): {len(errors)} error(s); {top}"
        elif errors:
            summary = f"{service} container logs (last {since_minutes}m): {len(errors)} error(s), no known signature; " \
                      f"latest: \"{errors[-1].message[:160]}\""
        else:
            summary = f"{service} container logs (last {since_minutes}m): {len(events)} lines, no errors"
        return ToolResult(tool_name=name, success=True, summary=summary, data={
            "lines": len(events), "errors": len(errors), "signatures": ",".join(m.id for m in matches[:8])})

    async def container_health(self, service: str) -> ToolResult:
        name = "docker_container_health"
        item, failure = await self._container(service)
        if failure:
            return failure.model_copy(update={"tool_name": name})
        response = await self._client.get(f"/containers/{item['Id']}/json")
        if response.status_code >= 400:
            return ToolResult(tool_name=name, success=False, summary="inspect failed", error=str(response.status_code))
        info = response.json()
        state = info.get("State") or {}
        health = (state.get("Health") or {}).get("Status")
        restarts = int(info.get("RestartCount") or 0)
        oom = bool(state.get("OOMKilled"))
        problems = []
        if oom:
            problems.append("OOMKilled=true (container was killed for exceeding its memory limit)")
        if restarts >= 3:
            problems.append(f"restarted {restarts} times (crash loop)")
        if state.get("Status") not in {"running", None}:
            problems.append(f"status {state.get('Status')} exit code {state.get('ExitCode')}")
        if health and health != "healthy":
            problems.append(f"health check {health}")
        summary = (f"{service} container: " + "; ".join(problems)) if problems else \
            f"{service} container healthy: running, {restarts} restart(s), health {health or 'n/a'}"
        return ToolResult(tool_name=name, success=True, summary=summary, data={
            "status": state.get("Status"), "restart_count": restarts, "oom_killed": oom, "health": health,
            "exit_code": state.get("ExitCode"), "started_at": state.get("StartedAt")})

    async def container_metrics(self, service: str) -> ToolResult:
        name = "docker_container_metrics"
        item, failure = await self._container(service)
        if failure:
            return failure.model_copy(update={"tool_name": name})
        response = await self._client.get(f"/containers/{item['Id']}/stats", params={"stream": "false"})
        if response.status_code >= 400:
            return ToolResult(tool_name=name, success=False, summary="stats failed", error=str(response.status_code))
        stats = response.json()
        memory = stats.get("memory_stats") or {}
        usage, limit = memory.get("usage") or 0, memory.get("limit") or 0
        mem_pct = round(100 * usage / limit, 1) if limit else None
        cpu_pct = _cpu_percent(stats)
        flags = []
        if mem_pct is not None and mem_pct >= 90:
            flags.append(f"memory {mem_pct}% of limit, resource near exhaustion")
        if cpu_pct is not None and cpu_pct >= 90:
            flags.append(f"cpu {cpu_pct}%, resource saturated")
        summary = f"{service} container metrics: " + ("; ".join(flags) if flags else
                                                      f"within limits (memory {mem_pct}%, cpu {cpu_pct}%)")
        return ToolResult(tool_name=name, success=True, summary=summary, data={
            "memory_percent": mem_pct, "memory_usage_bytes": usage, "memory_limit_bytes": limit, "cpu_percent": cpu_pct})

    # ------------------------------------------------------------------ registration

    def register(self, registry: ToolRegistry) -> None:
        common = {"risk": RiskLevel.READ_ONLY, "read_only": True, "required_parameters": ("service",)}
        registry.register(ToolSpec(
            name="docker_container_logs", description="Fetch recent docker container logs and scan them for known errors",
            allowed_parameters=("service", "since_minutes"), parameter_types={"service": "string", "since_minutes": "integer"},
            **common), self.container_logs)
        registry.register(ToolSpec(
            name="docker_container_health",
            description="Docker container health: state, restart count, OOM kill, health check",
            allowed_parameters=("service",), parameter_types={"service": "string"}, **common), self.container_health)
        registry.register(ToolSpec(
            name="docker_container_metrics", description="Docker container resource metrics: memory and cpu against limits",
            allowed_parameters=("service",), parameter_types={"service": "string"}, **common), self.container_metrics)


def demux_docker_stream(payload: bytes) -> str:
    """Docker multiplexes stdout/stderr with an 8-byte frame header unless the container has a TTY."""
    if len(payload) < 8 or payload[0] not in (0, 1, 2) or payload[1:4] != b"\x00\x00\x00":
        return payload.decode("utf-8", errors="replace")
    out, offset = [], 0
    while offset + 8 <= len(payload):
        size = struct.unpack(">I", payload[offset + 4:offset + 8])[0]
        out.append(payload[offset + 8:offset + 8 + size])
        offset += 8 + size
    return b"".join(out).decode("utf-8", errors="replace")


def _cpu_percent(stats: dict[str, Any]) -> float | None:
    try:
        cpu, pre = stats["cpu_stats"], stats["precpu_stats"]
        delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        system = cpu["system_cpu_usage"] - pre.get("system_cpu_usage", 0)
        cpus = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or [1])
        return round(100.0 * delta / system * cpus, 1) if system > 0 and delta >= 0 else None
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
