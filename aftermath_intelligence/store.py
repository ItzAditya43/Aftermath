"""Persistent local stores: incident memory and calibration grades on disk.

`FileMemory` implements `IncidentMemory` with a JSON-lines file so lessons survive restarts.
It reports ``backend="local_file"``; it is not Hindsight and never claims to be. When the
Hindsight adapter lands it replaces this through the same `recall` / `retain` contract.

Writes are atomic (temp file + rename) and serialized with an asyncio lock, so a crash
mid-write leaves the previous file intact. Corrupt lines are skipped and counted, never fatal.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from .calibration import CalibrationTracker, GradedPrediction
from .memory import MemoryQuery, MemoryRecord, RecalledIncident, score_candidates

logger = logging.getLogger("aftermath.store")


def _atomic_write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(line + "\n" for line in lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class FileMemory:
    """Durable local memory. One `MemoryRecord` per line, keyed (upserted) by incident id."""

    backend = "local_file"

    def __init__(self, path: str | os.PathLike[str], *, threshold: float = 0.35):
        self.path = Path(path)
        self.threshold = threshold
        self.skipped_lines = 0
        self._lock = asyncio.Lock()
        self._records: dict[str, MemoryRecord] = {}
        for line in _read_lines(self.path):
            try:
                record = MemoryRecord.model_validate_json(line)
            except ValidationError:
                self.skipped_lines += 1
                continue
            self._records[record.incident_id] = record
        if self.skipped_lines:
            logger.warning("memory file %s: skipped %d unreadable line(s)", self.path, self.skipped_lines)

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return tuple(self._records.values())

    async def retain(self, record: MemoryRecord) -> None:
        async with self._lock:
            self._records[record.incident_id] = record
            await asyncio.to_thread(self._flush)

    async def retain_many(self, records) -> None:
        """Upsert several records with a single file write (history ingest at startup)."""
        async with self._lock:
            for record in records:
                self._records[record.incident_id] = record
            await asyncio.to_thread(self._flush)

    def get(self, incident_id: str) -> MemoryRecord | None:
        return self._records.get(incident_id)

    async def clear(self) -> None:
        async with self._lock:
            self._records.clear()
            await asyncio.to_thread(self._flush)

    async def recall(self, query: MemoryQuery) -> list[RecalledIncident]:
        return score_candidates(query, self._records.values(), threshold=self.threshold)[: query.limit]

    def _flush(self) -> None:
        _atomic_write(self.path, [r.model_dump_json() for r in self._records.values()])


class PersistentCalibration(CalibrationTracker):
    """`CalibrationTracker` whose graded predictions are saved to disk after every record."""

    def __init__(self, path: str | os.PathLike[str], *, min_samples: int = 30):
        super().__init__(min_samples=min_samples)
        self.path = Path(path)
        for line in _read_lines(self.path):
            try:
                data = json.loads(line)
                super().record(GradedPrediction(str(data["incident_id"]), float(data["score"]), str(data["verdict"]),
                                                data.get("rank_of_actual")))
            except (ValueError, KeyError, TypeError):
                continue

    _deferred = False

    def record(self, prediction: GradedPrediction) -> None:
        super().record(prediction)
        if not self._deferred:
            self._save()

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Record many grades with one file write at the end (history backtest), not one fsync per grade."""
        self._deferred = True
        try:
            yield
        finally:
            self._deferred = False
            self._save()

    def _save(self) -> None:
        _atomic_write(self.path, [json.dumps(p.__dict__) for p in self._items.values()])

    def clear(self) -> None:
        self._items.clear()
        _atomic_write(self.path, [])


M = TypeVar("M", bound=BaseModel)
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")


class ReportStore(Generic[M]):
    """Bounded store for large per-incident results (full investigation reports, resolution evaluations).

    Keeps the most recent `cache_size` objects in RAM and, when `directory` is set, every object as one JSON
    file so the data survives restarts. The directory is pruned to the newest `max_files`. A report is about
    40 KB of JSON (several times that as Python objects), so RAM holds only the hot set.
    """

    def __init__(self, model: type[M], directory: str | os.PathLike[str] | None, *, suffix: str,
                 cache_size: int = 50, max_files: int = 500):
        self.model = model
        self.directory = Path(directory) if directory else None
        self.suffix = suffix
        self.cache_size = max(1, cache_size)
        self.max_files = max(1, max_files)
        self._cache: OrderedDict[str, M] = OrderedDict()
        self._lock = threading.RLock()   # put() runs in a worker thread while get() runs on the event loop

    def _path(self, key: str) -> Path:
        # Incident ids come from the API: never let one name a path outside the directory.
        safe = _UNSAFE_NAME.sub("_", key)[:120] or "_"
        return self.directory / f"{safe}.{self.suffix}.json"  # type: ignore[operator]

    def put(self, key: str, value: M) -> None:
        with self._lock:
            self._put(key, value)

    def _put(self, key: str, value: M) -> None:
        self._remember(key, value)
        if self.directory is None:
            return
        try:
            _atomic_write(self._path(key), [value.model_dump_json()])
            self._prune()
        except OSError:
            logger.warning("could not persist %s for %s", self.suffix, key, exc_info=True)

    def get(self, key: str) -> M | None:
        with self._lock:
            return self._get(key)

    def _get(self, key: str) -> M | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if self.directory is None:
            return None
        path = self._path(key)
        try:
            value = self.model.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValidationError, ValueError):
            logger.warning("unreadable %s file %s; ignoring", self.suffix, path)
            return None
        self._remember(key, value)
        return value

    def __getitem__(self, key: str) -> M:
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self.get(key) is not None

    def __len__(self) -> int:
        if self.directory is None:
            return len(self._cache)
        return len(list(self.directory.glob(f"*.{self.suffix}.json"))) if self.directory.exists() else 0

    def clear(self) -> None:
        with self._lock:
            self._clear()

    def _clear(self) -> None:
        self._cache.clear()
        if self.directory is not None and self.directory.exists():
            for path in self.directory.glob(f"*.{self.suffix}.json"):
                path.unlink(missing_ok=True)

    def _remember(self, key: str, value: M) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)

    def _prune(self) -> None:
        files = sorted(self.directory.glob(f"*.{self.suffix}.json"), key=lambda p: p.stat().st_mtime)  # type: ignore[union-attr]
        for path in files[: max(0, len(files) - self.max_files)]:
            path.unlink(missing_ok=True)


__all__ = ["FileMemory", "PersistentCalibration", "ReportStore"]
