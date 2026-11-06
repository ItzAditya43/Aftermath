"""Read-only diagnostic connectors (Docker Engine API, GitHub REST) and env-driven setup.

    AFTERMATH_DOCKER_TOOLS=1          enable Docker diagnostics (DOCKER_SOCKET, default /var/run/docker.sock)
    GITHUB_REPO=owner/name            default repository for change correlation
    AFTERMATH_SERVICE_REPOS=svc=o/r,svc2=o/r2   per-service repositories
    GITHUB_TOKEN=...                  optional for public repos; needs read-only contents/actions scope
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from ..tools import ToolRegistry
from .docker import DockerConnector, demux_docker_stream
from .github import GitHubConnector

logger = logging.getLogger("aftermath.connectors")


@dataclass
class Connectors:
    registry: ToolRegistry | None = None
    docker: DockerConnector | None = None
    github: GitHubConnector | None = None

    async def aclose(self) -> None:
        for connector in (self.docker, self.github):
            if connector is not None:
                await connector.aclose()


def parse_service_repos(raw: str) -> dict[str, str]:
    pairs = (item.split("=", 1) for item in raw.split(",") if "=" in item)
    return {service.strip(): repo.strip() for service, repo in pairs if service.strip() and repo.strip()}


def connectors_from_env(env: dict[str, str] | None = None) -> Connectors:
    env = dict(os.environ if env is None else env)
    out = Connectors()
    registry = ToolRegistry()
    if env.get("AFTERMATH_DOCKER_TOOLS", "0").lower() in {"1", "true", "yes"}:
        out.docker = DockerConnector(socket_path=env.get("DOCKER_SOCKET", "/var/run/docker.sock"))
        out.docker.register(registry)
    service_repos = parse_service_repos(env.get("AFTERMATH_SERVICE_REPOS", ""))
    if env.get("GITHUB_REPO") or service_repos:
        out.github = GitHubConnector(token=env.get("GITHUB_TOKEN") or None, default_repo=env.get("GITHUB_REPO") or None,
                                     service_repos=service_repos)
        out.github.register(registry)
    if registry.list_tools():
        out.registry = registry
    logger.info("connectors: docker=%s github=%s", out.docker is not None, out.github is not None)
    return out


__all__ = ["Connectors", "DockerConnector", "GitHubConnector", "connectors_from_env", "demux_docker_stream",
           "parse_service_repos"]
