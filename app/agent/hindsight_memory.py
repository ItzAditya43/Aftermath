"""Hindsight-backed incident memory: the `IncidentMemory` adapter the real agent uses when Hindsight is configured.

How it fits:

- **retain**: each resolved incident is written to the local store (the structured record the engine needs:
  root cause, fix, failed attempts, signatures) and to a Hindsight memory bank, as natural language with
  ``document_id`` = the incident id. Re-retaining the same id replaces it in Hindsight, never duplicates it.
- **recall**: Hindsight searches the bank. Its facts are mapped back to incidents through ``document_id``, in
  Hindsight's order. Hindsight always returns *something* (its ``final`` score is relative to the query), so a
  recalled incident is kept only when the engine's explainable scorer agrees (relevance >= threshold) or
  Hindsight's semantic match is strong on its own. The fact Hindsight matched is added to the matching points,
  so the UI shows what Hindsight recalled.
- **Credits**: Hindsight runs an LLM over every retain. A small state file remembers what was sent (by content
  hash), so restarts and re-seeding only send new or changed incidents.
- **Failure**: if Hindsight is unreachable, recall falls back to the local store and `backend` says so
  ("local_file (Hindsight unavailable)"), so a report never claims Hindsight answered when it didn't. Writes that
  fail stay pending and are retried on the next write or startup.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from aftermath_intelligence.memory import MemoryQuery, MemoryRecord, RecalledIncident, score_candidates

logger = logging.getLogger("aftermath.hindsight")

CLOUD_BASE_URL = "https://api.hindsight.vectorize.io"
BACKEND = "hindsight"
FALLBACK_BACKEND = "local_file (Hindsight unavailable)"

# Real matches on the demo history score ~0.8 semantic, unrelated incidents ~0.6. Above this, Hindsight's own
# judgement is enough to recall an incident even when the keyword/signature scorer disagrees.
STRONG_SEMANTIC = 0.75


class HindsightMemory:
    """`IncidentMemory` on top of a Hindsight bank, with a local structured store beside it."""

    def __init__(self, client: Any, bank_id: str, local: Any, *, state_path: str | os.PathLike[str] | None = None,
                 threshold: float = 0.35, recall_timeout: float = 6.0, retain_timeout: float = 45.0,
                 concurrency: int = 4):
        self.client = client
        self.bank_id = bank_id
        self.local = local
        self.threshold = threshold
        self.recall_timeout = recall_timeout
        self.retain_timeout = retain_timeout
        self._concurrency = concurrency
        self._state_path = Path(state_path) if state_path else None
        self._sent: dict[str, str] = self._load_state()   # incident_id -> hash of the text Hindsight holds
        self._bank_ready = False
        self._last_source = BACKEND

    # ------------------------------------------------------------------ IncidentMemory

    @property
    def backend(self) -> str:
        """Which memory answered the last recall: "hindsight", or the local fallback when Hindsight was down."""
        return self._last_source

    async def retain(self, record: MemoryRecord) -> None:
        """Store a resolution. Hindsight is written synchronously, so the next analysis can recall it."""
        await self.local.retain(record)
        await self._push([record], background=False)
        await self._push(self._pending(exclude={record.incident_id}), background=True)   # retry earlier failures

    async def retain_many(self, records: Iterable[MemoryRecord]) -> None:
        """History ingest (seeding, every startup): store new records, then send everything Hindsight lacks.

        Sent in the background (Hindsight processes them asynchronously), so seeding stays fast; recall finds
        them once Hindsight has finished, usually within seconds.
        """
        records = list(records)
        retain_many = getattr(self.local, "retain_many", None)
        if callable(retain_many):
            await retain_many(records)
        else:
            for record in records:
                await self.local.retain(record)
        await self._push(self._pending(), background=True)

    async def recall(self, query: MemoryQuery) -> list[RecalledIncident]:
        try:
            await self._ensure_bank()
            response = await asyncio.wait_for(
                self.client.arecall(self.bank_id, _query_text(query), max_tokens=2048, budget="mid"),
                timeout=self.recall_timeout)
            results = list(getattr(response, "results", None) or [])
        except Exception as exc:  # noqa: BLE001 - an outage must not stop the analysis
            if _status(exc) == 404:   # the bank has no memories yet
                self._last_source = BACKEND
                return []
            logger.warning("Hindsight recall failed (%s); using local memory", _describe(exc))
            self._last_source = FALLBACK_BACKEND
            return await self.local.recall(query)
        self._last_source = BACKEND
        return self._rank(query, results)[: query.limit]

    # ------------------------------------------------------------------ the rest of the store interface

    def __len__(self) -> int:
        return len(self.local)

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return self.local.records

    def get(self, incident_id: str) -> MemoryRecord | None:
        return self.local.get(incident_id)

    async def clear(self) -> None:
        """Forget everything: the local store and the Hindsight bank (deleted, then recreated empty)."""
        await self.local.clear()
        self._sent.clear()
        self._save_state()
        try:
            await self.client.adelete_bank(self.bank_id)
        except Exception as exc:  # noqa: BLE001
            if _status(exc) != 404:
                logger.warning("could not delete Hindsight bank %s (%s)", self.bank_id, _describe(exc))
        self._bank_ready = False

    async def aclose(self) -> None:
        close = getattr(self.client, "aclose", None)
        if callable(close):
            await close()

    def status(self) -> dict[str, Any]:
        """For health checks and logs: how much of the local memory Hindsight already holds."""
        return {"backend": self.backend, "bank_id": self.bank_id, "records": len(self.local),
                "in_hindsight": sum(1 for r in self.local.records if self._sent.get(r.incident_id) == _digest(r)),
                "pending": len(self._pending())}

    # ------------------------------------------------------------------ internals

    def _rank(self, query: MemoryQuery, results: list[Any]) -> list[RecalledIncident]:
        best: OrderedDict[str, tuple[float, str]] = OrderedDict()   # incident id -> (semantic score, first fact)
        for result in results:   # Hindsight returns facts best-first
            incident_id = getattr(result, "document_id", None) or (getattr(result, "metadata", None) or {}).get("incident_id")
            if not incident_id or incident_id == query.incident_id or self.local.get(incident_id) is None:
                continue   # cross-document observations carry no document id; skip them
            semantic = float(getattr(getattr(result, "scores", None), "semantic", None) or 0.0)
            if incident_id in best:
                best[incident_id] = (max(best[incident_id][0], semantic), best[incident_id][1])
            else:
                best[incident_id] = (semantic, str(getattr(result, "text", "") or "").strip())

        recalled: list[RecalledIncident] = []
        for incident_id, (semantic, fact) in best.items():
            record = self.local.get(incident_id)
            local = score_candidates(query, [record], threshold=0.0)
            local_score = local[0].relevance if local else 0.0
            if local_score < self.threshold and semantic < STRONG_SEMANTIC:
                continue   # Hindsight's weak tail; the explainable scorer doesn't back it either
            points = list(local[0].matching_points) if local else []
            if fact:
                points.append(f"Hindsight recalled: {fact}")
            recalled.append(RecalledIncident(
                record=record, relevance=round(min(1.0, max(local_score, semantic)), 3),
                shared_signatures=local[0].shared_signatures if local else (),
                matching_points=tuple(points), differences=local[0].differences if local else ()))
        return recalled

    def _pending(self, exclude: set[str] = frozenset()) -> list[MemoryRecord]:
        return [r for r in self.local.records
                if r.incident_id not in exclude and self._sent.get(r.incident_id) != _digest(r)]

    async def _push(self, records: list[MemoryRecord], *, background: bool) -> None:
        if not records:
            return
        try:
            await self._ensure_bank()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Hindsight unavailable (%s); %d record(s) stay pending", _describe(exc), len(records))
            return
        slots = asyncio.Semaphore(self._concurrency)

        async def send(record: MemoryRecord) -> bool:
            async with slots:
                try:
                    await asyncio.wait_for(self.client.aretain(
                        self.bank_id, record.retain_text(), document_id=record.incident_id,
                        context="A resolved production incident: what broke, the confirmed root cause, the fix "
                                "that worked and the fixes that did not.",
                        timestamp=record.created_at, metadata={"incident_id": record.incident_id,
                                                               "service": record.service},
                        tags=[f"service:{record.service}"], retain_async=background), timeout=self.retain_timeout)
                except Exception as exc:  # noqa: BLE001 - stays pending; retried later
                    logger.warning("Hindsight retain of %s failed (%s); will retry", record.incident_id, _describe(exc))
                    return False
                self._sent[record.incident_id] = _digest(record)
                return True

        sent = sum(await asyncio.gather(*(send(r) for r in records)))
        self._save_state()
        if sent:
            logger.info("Hindsight: retained %d incident(s) in bank %s%s", sent, self.bank_id,
                        " (processing in the background)" if background else "")

    async def _ensure_bank(self) -> None:
        if not self._bank_ready:
            await self.client.acreate_bank(  # idempotent: creating an existing bank updates it
                self.bank_id, name="Aftermath incidents",
                mission="Remember how production incidents were resolved: root causes, fixes that worked, and "
                        "fixes that failed, so on-call engineers can reuse them on similar alerts.")
            self._bank_ready = True

    def _load_state(self) -> dict[str, str]:
        if not self._state_path or not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return dict(data.get(self.bank_id, {}))
        except (ValueError, OSError, AttributeError):
            logger.warning("unreadable Hindsight state %s; will resend memories", self._state_path)
            return {}

    def _save_state(self) -> None:
        if not self._state_path:
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8")) if self._state_path.exists() else {}
        except (ValueError, OSError):
            data = {}
        data[self.bank_id] = self._sent
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._state_path)


def hindsight_from_settings(api_key: str, base_url: str, bank_id: str, local: Any,
                            state_path: str | os.PathLike[str] | None) -> HindsightMemory | None:
    """A `HindsightMemory` when Hindsight is configured (a key for Hindsight Cloud, or a self-hosted base URL)."""
    if not api_key and not base_url:
        return None
    from hindsight_client import Hindsight   # imported only when Hindsight is used

    client = Hindsight(base_url=base_url or CLOUD_BASE_URL, api_key=api_key or None, timeout=60, max_attempts=2)
    return HindsightMemory(client, bank_id, local, state_path=state_path)


def _query_text(query: MemoryQuery) -> str:
    parts = [query.text.strip(), f"Service: {query.service}."]
    if query.signatures:
        parts.append("Signals: " + ", ".join(query.signatures) + ".")
    if query.exception_types:
        parts.append("Exceptions: " + ", ".join(query.exception_types) + ".")
    return " ".join(p for p in parts if p)[:2000]


def _digest(record: MemoryRecord) -> str:
    return hashlib.sha256(record.retain_text().encode("utf-8")).hexdigest()[:16]


def _status(exc: BaseException) -> int | None:
    status = getattr(exc, "status", None)
    return status if isinstance(status, int) else None


def _describe(exc: BaseException) -> str:
    status = _status(exc)
    return f"{type(exc).__name__}{f' {status}' if status else ''}"
