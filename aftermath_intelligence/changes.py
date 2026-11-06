"""Deployment / change correlation for data supplied by the GitHub (or CD) connector.

The connector owns fetching. This module only scores supplied `ChangeRecord`s against the
incident: timing, overlap between changed files and the stack trace's origin frames, the
affected service, and whether config/migration files were touched. A recent commit is never
blamed on recency alone; every score lists its reasons.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

_CONFIG_FILE = re.compile(r"(?:\.ya?ml|\.json|\.toml|\.ini|\.env|\.conf|\.properties|values[-\w]*\.ya?ml|Dockerfile|helm/|k8s/|terraform/|\.tf)$", re.I)
_MIGRATION_FILE = re.compile(r"(?:migrations?/|alembic/|flyway|liquibase|\.sql$)", re.I)


class ChangeRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str                                  # commit sha, PR number, deploy id, workflow run id
    kind: str = "commit"                     # commit | pull_request | deployment | workflow_run
    title: str = ""
    timestamp: datetime | None = None
    service: str | None = None
    author: str | None = None
    files: tuple[str, ...] = ()
    status: str | None = None                # e.g. workflow conclusion "failure"
    url: str | None = None


class ChangeSuspect(BaseModel):
    model_config = ConfigDict(frozen=True)

    change: ChangeRecord
    score: float = Field(ge=0, le=1)
    reasons: tuple[str, ...]
    matched_files: tuple[str, ...] = ()


def correlate_changes(changes: Iterable[ChangeRecord], *, first_failure: datetime | None, service: str,
                      origin_frames: Iterable[str] = (), window: timedelta = timedelta(hours=6)) -> tuple[ChangeSuspect, ...]:
    origin_tokens = {_stem(f) for frame in origin_frames for f in _frame_files(frame)}
    suspects: list[ChangeSuspect] = []
    for change in changes:
        score = 0.0
        reasons: list[str] = []
        if change.timestamp and first_failure:
            lag = first_failure - change.timestamp
            if lag < timedelta(0):
                if lag < -timedelta(minutes=2):
                    continue  # landed after the failure started: cannot have caused it
            elif lag <= window:
                closeness = 1 - lag / window
                score += 0.3 * closeness
                reasons.append(f"landed {_human(lag)} before the first failure")
            else:
                continue
        elif change.timestamp is None:
            reasons.append("no timestamp; timing could not be checked")
        if change.service and change.service == service:
            score += 0.15
            reasons.append(f"touches the affected service ({service})")
        matched = tuple(f for f in change.files if _stem(f) in origin_tokens)
        if matched:
            score += 0.4
            reasons.append("changed file(s) appear in the failing stack trace: " + ", ".join(matched[:3]))
        if any(_CONFIG_FILE.search(f) for f in change.files):
            score += 0.1
            reasons.append("modifies configuration")
        if any(_MIGRATION_FILE.search(f) for f in change.files):
            score += 0.1
            reasons.append("includes a database migration")
        if change.kind == "deployment":
            score += 0.05
        if change.kind == "workflow_run" and (change.status or "").lower() in {"failure", "failed", "cancelled"}:
            score += 0.05
            reasons.append(f"workflow concluded {change.status}")
        if score > 0:
            suspects.append(ChangeSuspect(change=change, score=round(min(score, 0.95), 3), reasons=tuple(reasons),
                                          matched_files=matched))
    return tuple(sorted(suspects, key=lambda s: -s.score))


def _frame_files(frame: str) -> list[str]:
    """'com.acme.Foo.bar (Foo.java:12)' -> ['Foo.java']; '/app/service.py:22' -> ['/app/service.py']."""
    return re.findall(r"([\w./-]+\.(?:java|kt|scala|py|js|ts|go|rb|cs))", frame)


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].lower()


def _human(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes} min" if minutes < 120 else f"{minutes // 60} h"
