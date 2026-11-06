"""Read-only GitHub change feed: recent commits (with changed files) and workflow runs.

Produces `ChangeRecord`s for `IncidentCase.changes`, so deploy/commit correlation runs on real
data, and registers one read-only diagnostic tool. Only GET requests to api.github.com; the
token (optional for public repos) is sent as a header and never logged or returned.

Service -> repository mapping comes from configuration (`repo_for_service`); the model can
never choose an arbitrary repository.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..changes import ChangeRecord
from ..tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
USER_AGENT = "aftermath-intelligence/0.2 (+https://github.com)"


class GitHubConnector:
    def __init__(self, *, token: str | None = None, default_repo: str | None = None,
                 service_repos: dict[str, str] | None = None, base_url: str = "https://api.github.com",
                 timeout: float = 10.0, max_commits: int = 20, transport: httpx.AsyncBaseTransport | None = None):
        for repo in [default_repo, *(service_repos or {}).values()]:
            if repo and not _REPO.match(repo):
                raise ValueError(f"invalid repository '{repo}' (expected owner/name)")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.default_repo = default_repo
        self.service_repos = dict(service_repos or {})
        self.max_commits = max_commits
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    def repo_for_service(self, service: str) -> str | None:
        return self.service_repos.get(service) or self.default_repo

    async def recent_changes(self, service: str, *, until: datetime | None = None,
                             hours: int = 24) -> tuple[ChangeRecord, ...]:
        """Commits and workflow runs in the window before `until`. Errors yield an empty tuple."""
        repo = self.repo_for_service(service)
        if repo is None:
            return ()
        until = until or datetime.now(timezone.utc)
        since = until - timedelta(hours=max(1, min(hours, 24 * 14)))
        try:
            commits, runs = await asyncio.gather(self._commits(repo, service, since, until),
                                                 self._workflow_runs(repo, service, since, until))
        except (httpx.HTTPError, ValueError):
            return ()
        return tuple(sorted(commits + runs, key=lambda c: c.timestamp or since, reverse=True))

    async def _commits(self, repo: str, service: str, since: datetime, until: datetime) -> list[ChangeRecord]:
        response = await self._client.get(f"/repos/{repo}/commits", params={
            "since": since.isoformat(), "until": until.isoformat(), "per_page": str(self.max_commits)})
        response.raise_for_status()
        listing = response.json()[: self.max_commits]
        details = await asyncio.gather(*(self._client.get(f"/repos/{repo}/commits/{c['sha']}") for c in listing),
                                       return_exceptions=True)
        records = []
        for item, detail in zip(listing, details, strict=True):
            files: tuple[str, ...] = ()
            if isinstance(detail, httpx.Response) and detail.status_code == 200:
                files = tuple(f["filename"] for f in (detail.json().get("files") or [])[:100])
            commit = item.get("commit") or {}
            records.append(ChangeRecord(
                id=item["sha"][:12], kind="commit", title=(commit.get("message") or "").splitlines()[0][:200],
                timestamp=_parse((commit.get("committer") or commit.get("author") or {}).get("date")),
                service=service, author=(item.get("author") or {}).get("login") or (commit.get("author") or {}).get("name"),
                files=files, url=item.get("html_url")))
        return records

    async def _workflow_runs(self, repo: str, service: str, since: datetime, until: datetime) -> list[ChangeRecord]:
        response = await self._client.get(f"/repos/{repo}/actions/runs", params={
            "created": f"{since.strftime('%Y-%m-%dT%H:%M:%SZ')}..{until.strftime('%Y-%m-%dT%H:%M:%SZ')}", "per_page": "20"})
        if response.status_code == 404:
            return []
        response.raise_for_status()
        records = []
        for run in response.json().get("workflow_runs", [])[:20]:
            name = run.get("name") or "workflow"
            kind = "deployment" if re.search(r"deploy|release|cd\b|publish", name, re.I) else "workflow_run"
            records.append(ChangeRecord(
                id=f"run-{run.get('id')}", kind=kind, title=f"{name}: {run.get('display_title') or ''}"[:200],
                timestamp=_parse(run.get("updated_at") or run.get("created_at")), service=service,
                author=(run.get("actor") or {}).get("login"), status=run.get("conclusion") or run.get("status"),
                url=run.get("html_url")))
        return records

    async def recent_commits_tool(self, service: str, since_minutes: int = 360) -> ToolResult:
        name = "github_recent_commits"
        repo = self.repo_for_service(service)
        if repo is None:
            return ToolResult(tool_name=name, success=False, summary=f"no repository configured for {service}",
                              error="not_configured")
        changes = await self.recent_changes(service, hours=max(1, since_minutes // 60))
        if not changes:
            return ToolResult(tool_name=name, success=True,
                              summary=f"github {repo}: no commits or deployments in the last {since_minutes}m")
        lines = "; ".join(f"{c.kind} {c.id} '{c.title[:60]}'" + (f" ({c.status})" if c.status else "")
                          for c in changes[:5])
        return ToolResult(tool_name=name, success=True, summary=f"github {repo}: {len(changes)} change(s): {lines}",
                          data={"repo": repo, "changes": len(changes),
                                "failed_runs": sum(1 for c in changes if c.status == "failure")})

    def register(self, registry: ToolRegistry) -> None:
        registry.register(ToolSpec(
            name="github_recent_commits",
            description="Recent github commits, deployments and workflow runs for the service repository",
            risk=RiskLevel.READ_ONLY, read_only=True, allowed_parameters=("service", "since_minutes"),
            required_parameters=("service",), parameter_types={"service": "string", "since_minutes": "integer"}),
            self.recent_commits_tool)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
