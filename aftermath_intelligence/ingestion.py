from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .models import NormalizedEvent, Severity, stable_event_id

_TIMESTAMP = re.compile(
    r"(?P<ts>\d{4}-\d\d-\d\d[T ][0-9:.,]+(?:Z|[+-]\d\d:?\d\d)?)"
)
_LEVEL = re.compile(r"\b(TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\b", re.I)
_EXCEPTION = re.compile(r"\b([A-Za-z_][\w.]*Exception|[A-Za-z_][\w.]*Error)\b")
_ERROR_CODE = re.compile(r"\b(?:error|status|code)[ _:=#-]*(\d{3,6})\b", re.I)
_SERVICE = re.compile(r"\bservice[=: ]+([\w.-]+)", re.I)


@dataclass(frozen=True)
class IngestionResult:
    events: tuple[NormalizedEvent, ...]
    malformed_lines: tuple[int, ...]
    duplicate_count: int
    detected_formats: dict[str, int]


def _severity(value: Any) -> Severity:
    text = str(value or "").strip().lower()
    if text in {"warn", "warning"}:
        return Severity.WARNING
    if text in {"fatal", "critical", "panic"}:
        return Severity.CRITICAL
    try:
        return Severity(text)
    except ValueError:
        return Severity.UNKNOWN


def _timestamp(raw: Any) -> tuple[datetime | None, str | None]:
    if raw is None:
        return None, None
    original = str(raw)
    candidate = original.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None, original
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), original


def _from_json(value: dict[str, Any], raw: str, incident_id: str, source: str) -> NormalizedEvent:
    message = str(value.get("message", value.get("msg", value.get("log", raw))))
    ts_raw = value.get("timestamp", value.get("time", value.get("ts")))
    timestamp, original = _timestamp(ts_raw)
    level = value.get("severity", value.get("level", value.get("log_level")))
    stack = value.get("stack_trace", value.get("stack", value.get("traceback")))
    exc = value.get("exception_type", value.get("exception"))
    if isinstance(exc, dict):
        exc = exc.get("type")
    if not exc:
        match = _EXCEPTION.search(str(stack or message))
        exc = match.group(1) if match else None
    known = {"timestamp", "time", "ts", "message", "msg", "log", "severity", "level", "log_level",
             "stack_trace", "stack", "traceback", "exception_type", "exception", "service", "environment",
             "error_code", "event_type"}
    metadata = {str(k): v for k, v in value.items() if k not in known}
    service_value = value.get("service", value.get("container_name", value.get("container")))
    if isinstance(service_value, dict):
        service_value = service_value.get("name")
    service_value = str(service_value).lstrip("/") if service_value is not None else None
    return NormalizedEvent(
        event_id=stable_event_id(source, raw, incident_id), incident_id=incident_id, timestamp=timestamp,
        original_timestamp=original, service=service_value,
        environment=_optional(value.get("environment")), severity=_severity(level),
        event_type=str(value.get("event_type", "log")), message=message,
        exception_type=_optional(exc), error_code=_optional(value.get("error_code")),
        stack_trace=_optional(stack), source=source, metadata=metadata, raw=raw,
    )


def _optional(value: Any) -> str | None:
    return str(value) if value is not None else None


def _from_text(line: str, incident_id: str, source: str) -> NormalizedEvent:
    ts_match = _TIMESTAMP.search(line)
    timestamp, original = _timestamp(ts_match.group("ts")) if ts_match else (None, None)
    level_match = _LEVEL.search(line)
    severity = _severity(level_match.group(1)) if level_match else Severity.UNKNOWN
    message = line[ts_match.end():] if ts_match else line
    message = re.sub(r"^\s*(?:stdout|stderr)\s+F\s+", "", message, flags=re.I)
    message = re.sub(r"^\s*\[?(?:TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\]?\s*[: -]*", "", message, flags=re.I)
    message = re.sub(r"^\s*service[=: ]+[\w.-]+\s*", "", message, flags=re.I).strip()
    exception = _EXCEPTION.search(message)
    code = _ERROR_CODE.search(line)
    service = _SERVICE.search(line)
    return NormalizedEvent(
        event_id=stable_event_id(source, line, incident_id), incident_id=incident_id,
        timestamp=timestamp, original_timestamp=original,
        service=service.group(1) if service else None, severity=severity,
        message=message, exception_type=exception.group(1) if exception else None,
        error_code=code.group(1) if code else None,
        stack_trace=line if line[:1].isspace() or "Traceback (most recent call last)" in line else None,
        source=source, raw=line,
    )


def ingest_text(text: str, *, incident_id: str = "unknown", source: str = "text") -> IngestionResult:
    """Parse JSON, JSONL, or plain text while retaining malformed line provenance."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        document = None
    if isinstance(document, dict):
        records = document.get("events") if isinstance(document.get("events"), list) else [document]
        if all(isinstance(record, dict) for record in records):
            events = tuple(_from_json(record, json.dumps(record, sort_keys=True), incident_id, source)
                           for record in records)
            unique: dict[str, NormalizedEvent] = {}
            duplicates = 0
            for event in events:
                if event.event_id in unique:
                    duplicates += 1
                else:
                    unique[event.event_id] = event
            return IngestionResult(tuple(unique.values()), (), duplicates,
                                   {"json": len(unique)})
    if isinstance(document, list) and all(isinstance(record, dict) for record in document):
        unique = {}
        duplicates = 0
        for record in document:
            event = _from_json(record, json.dumps(record, sort_keys=True), incident_id, source)
            if event.event_id in unique:
                duplicates += 1
            else:
                unique[event.event_id] = event
        return IngestionResult(tuple(unique.values()), (), duplicates, {"json-array": len(unique)})

    lines = text.splitlines()
    events: list[NormalizedEvent] = []
    malformed: list[int] = []
    formats: Counter[str] = Counter()
    duplicates = 0
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parsed: Any = None
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            pass
        if isinstance(parsed, dict):
            event = _from_json(parsed, line, incident_id, source)
            formats["jsonl"] += 1
        elif isinstance(parsed, list):
            formats["json-array"] += 1
            for item in parsed:
                if isinstance(item, dict):
                    event = _from_json(item, json.dumps(item, sort_keys=True), incident_id, source)
                    events.append(event)
            continue
        else:
            # Multiline exception traces are preserved per line; indented frames are
            # joined to the preceding event so fingerprints retain the full trace.
            if line[:1].isspace() and events and events[-1].stack_trace is not None:
                previous = events[-1]
                joined = f"{previous.stack_trace}\n{line}"
                raw_joined = f"{previous.raw}\n{line}"
                events[-1] = previous.model_copy(update={"stack_trace": joined, "raw": raw_joined,
                                                          "event_id": stable_event_id(source, raw_joined, incident_id)})
                continue
            if ("Traceback (most recent call last)" in line and events
                    and events[-1].severity in {Severity.ERROR, Severity.CRITICAL}
                    and events[-1].stack_trace is None):
                previous = events[-1]
                raw_joined = f"{previous.raw}\n{line}"
                events[-1] = previous.model_copy(update={"stack_trace": line, "raw": raw_joined,
                                                          "event_id": stable_event_id(source, raw_joined, incident_id)})
                continue
            if events and events[-1].stack_trace and not _TIMESTAMP.search(line):
                exception = _EXCEPTION.search(line)
                if exception:
                    previous = events[-1]
                    raw_joined = f"{previous.raw}\n{line}"
                    events[-1] = previous.model_copy(update={
                        "message": line.strip(), "exception_type": exception.group(1),
                        "severity": Severity.ERROR, "stack_trace": f"{previous.stack_trace}\n{line}",
                        "raw": raw_joined, "event_id": stable_event_id(source, raw_joined, incident_id),
                    })
                    continue
            event = _from_text(line, incident_id, source)
            formats["text"] += 1
            if line.lstrip().startswith(("{", "[")):
                malformed.append(number)
        events.append(event)
    unique: dict[str, NormalizedEvent] = {}
    for event in events:
        if event.event_id in unique:
            duplicates += 1
            continue
        unique[event.event_id] = event
    return IngestionResult(tuple(unique.values()), tuple(malformed), duplicates, dict(formats))
