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
import tempfile
from pathlib import Path

from pydantic import ValidationError

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

    def record(self, prediction: GradedPrediction) -> None:
        super().record(prediction)
        _atomic_write(self.path, [json.dumps(p.__dict__) for p in self._items.values()])

    def clear(self) -> None:
        self._items.clear()
        _atomic_write(self.path, [])


__all__ = ["FileMemory", "PersistentCalibration"]
