"""Evidence ingestion: detect formats, normalize to `NormalizedEvent`, keep provenance.

Supported per line: JSON objects (incl. Docker's json-file `{"log": ...}`), logfmt,
nginx/Apache access logs, `docker compose` prefixed output and timestamped text logs.
Whole documents may also be a JSON object/array. Multi-line JVM, Python, Node and Go
stack traces are folded into the event that precedes them. Malformed lines are kept as
text events and reported, never silently dropped.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .models import NormalizedEvent, Severity, stable_event_id
from .stacktrace import parse_stack_trace

MAX_INPUT_BYTES = 5_000_000
MAX_LINE_CHARS = 20_000

_TIMESTAMP = re.compile(
    r"(?P<ts>\d{4}-\d\d-\d\d[T ][0-9:.,]+(?:Z|[+-]\d\d:?\d\d)?)"
)
_SYSLOG_TS = re.compile(r"^(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s\d\d:\d\d:\d\d)\b")
_ACCESS = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+)[^"]*" (?P<status>\d{3}) (?P<size>\S+)'
    r'(?: "[^"]*" "[^"]*")?(?: (?P<rt>[\d.]+))?')
_COMPOSE_PREFIX = re.compile(r"^(?P<name>[a-z0-9][\w.-]{0,63}?)(?:[-_]\d+)?\s+\|\s(?P<rest>.*)$")
_LOGFMT_PAIR = re.compile(r'(\w[\w.-]*)=("(?:[^"\\]|\\.)*"|\S*)')
_LEVEL = re.compile(r"\b(TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL|PANIC|SEVERE)\b", re.I)
_EXCEPTION = re.compile(r"\b([A-Za-z_][\w.$]*Exception|[A-Za-z_][\w.$]*Error)\b")
_ERROR_CODE = re.compile(r"\b(?:error|status|code)[ _:=#-]*(\d{3,6})\b", re.I)
_SERVICE = re.compile(r"\bservice[=: ]+([\w.-]+)", re.I)
_THREAD_LOGGER = re.compile(r"^\[(?P<thread>[^\]]{1,80})\]\s+(?:(?P<logger>[\w.$]{3,120})\s+-\s+)?")
_METRIC = re.compile(r"\b(?P<key>[a-z][a-z_]{1,30})=(?P<value>\d+(?:\.\d+)?)(?:/(?P<max>\d+))?\b", re.I)
_CONTINUATION = re.compile(
    r"^(?:\s+at\s|\s+\.\.\.\s*\d+\s+more|Caused by:|Suppressed:|\s+File \"|Traceback \(most recent call last\)"
    r"|During handling of the above exception|The above exception was the direct cause|goroutine \d+ \[|\s+/\S+\.go:\d+"
    r"|[a-z][\w.$]*(?:\.[A-Z][\w$]*)+(?:Exception|Error)(?::|$)|java\.lang\.\w+(?::|$))"
)
_CONTAINER_EVENT = re.compile(
    r"oomkilled|crashloopbackoff|back-off restarting|liveness probe|readiness probe|killing container|"
    r"started container|pulling image|container (?:died|exited|restarted)|evicted", re.I)
_DEPLOY_EVENT = re.compile(r"\b(?:deployed|deploying|rollout|rolled out|helm upgrade|release v?\d+\.\d+|"
                           r"new replicaset|image updated)\b", re.I)


@dataclass(frozen=True)
class IngestionResult:
    events: tuple[NormalizedEvent, ...]
    malformed_lines: tuple[int, ...]
    duplicate_count: int
    detected_formats: dict[str, int]
    truncated: bool = False


# --------------------------------------------------------------------------- field parsing

def _severity(value: Any) -> Severity:
    text = str(value or "").strip().lower()
    if text in {"warn", "warning"}:
        return Severity.WARNING
    if text in {"fatal", "critical", "panic", "crit", "emerg", "alert"}:
        return Severity.CRITICAL
    if text in {"severe", "err"}:
        return Severity.ERROR
    try:
        return Severity(text)
    except ValueError:
        return Severity.UNKNOWN


def _timestamp(raw: Any) -> tuple[datetime | None, str | None]:
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        seconds = raw / 1000 if raw > 1e11 else raw
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc), str(raw)
        except (OverflowError, OSError, ValueError):
            return None, str(raw)
    original = str(raw)
    candidate = original.strip().replace("Z", "+00:00").replace(",", ".")
    if re.fullmatch(r"\d{4}-\d\d-\d\d \d", candidate[:12]):
        candidate = candidate.replace(" ", "T", 1)
    candidate = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", candidate)
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in ("%d/%b/%Y:%H:%M:%S %z", "%b %d %H:%M:%S"):
            try:
                parsed = datetime.strptime(original.strip(), fmt)
                if fmt == "%b %d %H:%M:%S":  # syslog omits the year
                    parsed = parsed.replace(year=datetime.now(timezone.utc).year)
                break
            except ValueError:
                continue
    if parsed is None:
        return None, original
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), original


def _optional(value: Any) -> str | None:
    return str(value) if value is not None and value != "" else None


def _metrics(message: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for match in _METRIC.finditer(message):
        key = match.group("key").lower()
        found[key] = float(match.group("value"))
        if match.group("max"):
            found[f"{key}_max"] = float(match.group("max"))
    return found


def _event_type(message: str, default: str = "log") -> str:
    if _CONTAINER_EVENT.search(message):
        return "container_event"
    if _DEPLOY_EVENT.search(message):
        return "deployment"
    return default


# --------------------------------------------------------------------------- per-format parsers

_JSON_KNOWN = {"timestamp", "time", "ts", "@timestamp", "message", "msg", "log", "severity", "level", "log_level",
               "stack_trace", "stack", "traceback", "exception_type", "exception", "service", "environment",
               "error_code", "event_type", "stream", "container_name", "container"}


def _from_json(value: dict[str, Any], raw: str, incident_id: str, source: str) -> NormalizedEvent:
    message = str(value.get("message", value.get("msg", value.get("log", raw))))
    if "log" in value and "message" not in value and "msg" not in value:
        message = message.rstrip("\n")  # docker json-file format
    ts_raw = value.get("timestamp", value.get("time", value.get("ts", value.get("@timestamp"))))
    timestamp, original = _timestamp(ts_raw)
    level = value.get("severity", value.get("level", value.get("log_level")))
    if level is None and value.get("stream") == "stderr":
        level_match = _LEVEL.search(message)
        level = level_match.group(1) if level_match else None
    stack = value.get("stack_trace", value.get("stack", value.get("traceback")))
    exc = value.get("exception_type", value.get("exception"))
    if isinstance(exc, dict):
        exc = exc.get("type")
    if not exc:
        match = _EXCEPTION.search(str(stack or message))
        exc = match.group(1) if match else None
    metadata = {str(k): v for k, v in value.items() if k not in _JSON_KNOWN}
    metrics = _metrics(message)
    if metrics:
        metadata.setdefault("metrics", metrics)
    service_value = value.get("service", value.get("container_name", value.get("container")))
    if isinstance(service_value, dict):
        service_value = service_value.get("name")
    service_value = str(service_value).lstrip("/") if service_value is not None else None
    return NormalizedEvent(
        event_id=stable_event_id(source, raw, incident_id), incident_id=incident_id, timestamp=timestamp,
        original_timestamp=original, service=service_value,
        environment=_optional(value.get("environment")), severity=_severity(level),
        event_type=str(value.get("event_type") or _event_type(message)), message=message,
        exception_type=_optional(exc), error_code=_optional(value.get("error_code")),
        stack_trace=_optional(stack), source=source, metadata=metadata, raw=raw,
    )


def _from_logfmt(line: str, incident_id: str, source: str) -> NormalizedEvent | None:
    pairs = {k.lower(): v.strip('"') for k, v in _LOGFMT_PAIR.findall(line)}
    if len(pairs) < 2 or not ({"level", "lvl", "msg", "message"} & pairs.keys()):
        return None
    record: dict[str, Any] = dict(pairs)
    if "lvl" in record:
        record.setdefault("level", record.pop("lvl"))
    if "msg" not in record and "message" not in record:
        record["msg"] = line
    event = _from_json(record, line, incident_id, source)
    metrics = _metrics(line)
    if metrics:
        event = event.model_copy(update={"metadata": {**event.metadata, "metrics": metrics}})
    return event


def _from_access_log(line: str, incident_id: str, source: str) -> NormalizedEvent | None:
    match = _ACCESS.match(line)
    if not match:
        return None
    status = int(match.group("status"))
    timestamp, original = _timestamp(match.group("ts"))
    severity = Severity.ERROR if status >= 500 else Severity.WARNING if status >= 400 else Severity.INFO
    metadata: dict[str, Any] = {"method": match.group("method"), "path": match.group("path"), "status": status}
    if match.group("rt"):
        metadata["response_time_s"] = float(match.group("rt"))
    return NormalizedEvent(
        event_id=stable_event_id(source, line, incident_id), incident_id=incident_id, timestamp=timestamp,
        original_timestamp=original, severity=severity, event_type="access_log",
        message=f"{match.group('method')} {match.group('path')} -> {status}", error_code=str(status),
        source=source, metadata=metadata, raw=line,
    )


def _from_text(line: str, incident_id: str, source: str, service_hint: str | None = None) -> NormalizedEvent:
    ts_match = _TIMESTAMP.search(line)
    syslog = _SYSLOG_TS.match(line) if not ts_match else None
    if ts_match:
        timestamp, original = _timestamp(ts_match.group("ts"))
        message = line[ts_match.end():]
    elif syslog:
        timestamp, original = _timestamp(syslog.group("ts"))
        message = line[syslog.end():]
    else:
        timestamp, original, message = None, None, line
    level_match = _LEVEL.search(line)
    severity = _severity(level_match.group(1)) if level_match else Severity.UNKNOWN
    message = re.sub(r"^\s*(?:stdout|stderr)\s+F\s+", "", message, flags=re.I)
    message = re.sub(r"^\s*\[?(?:TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL|PANIC|SEVERE)\]?\s*[: -]*", "",
                     message, flags=re.I)
    message = re.sub(r"^\s*service[=: ]+[\w.-]+\s*", "", message, flags=re.I).strip()
    metadata: dict[str, Any] = {}
    thread = _THREAD_LOGGER.match(message)
    if thread:
        metadata["thread"] = thread.group("thread")
        if thread.group("logger"):
            metadata["logger"] = thread.group("logger")
        message = message[thread.end():].strip() or message
    metrics = _metrics(message)
    if metrics:
        metadata["metrics"] = metrics
    exception = _EXCEPTION.search(message)
    code = _ERROR_CODE.search(line)
    service = _SERVICE.search(line)
    return NormalizedEvent(
        event_id=stable_event_id(source, line, incident_id), incident_id=incident_id,
        timestamp=timestamp, original_timestamp=original,
        service=service.group(1) if service else service_hint, severity=severity,
        event_type=_event_type(message), message=message,
        exception_type=exception.group(1) if exception else None,
        error_code=code.group(1) if code else None,
        stack_trace=line if line[:1].isspace() or "Traceback (most recent call last)" in line else None,
        source=source, metadata=metadata, raw=line,
    )


# --------------------------------------------------------------------------- driver

def ingest_text(text: str, *, incident_id: str = "unknown", source: str = "text",
                default_service: str | None = None, max_bytes: int = MAX_INPUT_BYTES) -> IngestionResult:
    """Parse JSON, JSONL, logfmt, access logs or plain text while retaining provenance."""
    truncated = len(text.encode("utf-8", errors="replace")) > max_bytes
    if truncated:
        text = text.encode("utf-8", errors="replace")[:max_bytes].decode("utf-8", errors="ignore")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        document = None
    records: list[Any] | None = None
    fmt = ""
    if isinstance(document, dict):
        records = document.get("events") if isinstance(document.get("events"), list) else [document]
        fmt = "json"
    elif isinstance(document, list):
        records, fmt = document, "json-array"
    if records is not None and records and all(isinstance(r, dict) for r in records):
        events = [_from_json(r, json.dumps(r, sort_keys=True), incident_id, source) for r in records]
        unique, duplicates = _dedupe(events)
        unique = _finalize(unique, default_service)
        return IngestionResult(tuple(unique), (), duplicates, {fmt: len(unique)}, truncated)

    events: list[NormalizedEvent] = []
    malformed: list[int] = []
    formats: Counter[str] = Counter()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS]
            formats["oversized-line"] += 1
        service_hint = default_service
        compose = _COMPOSE_PREFIX.match(line)
        if compose and not _TIMESTAMP.match(line):
            service_hint, body = compose.group("name"), compose.group("rest")
            formats["docker-compose"] += 1
        else:
            body = line

        if events and _is_continuation(body, events[-1]):
            events[-1] = _append_to_stack(events[-1], body, source, incident_id)
            continue

        parsed: Any = None
        if body.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                malformed.append(number)
        if isinstance(parsed, dict):
            event = _from_json(parsed, line, incident_id, source)
            if service_hint and not event.service:
                event = event.model_copy(update={"service": service_hint})
            formats["jsonl"] += 1
        elif isinstance(parsed, list):
            formats["json-array"] += 1
            for item in parsed:
                if isinstance(item, dict):
                    events.append(_from_json(item, json.dumps(item, sort_keys=True), incident_id, source))
            continue
        elif (access := _from_access_log(body, incident_id, source)) is not None:
            event = access.model_copy(update={"service": service_hint}) if service_hint else access
            formats["access-log"] += 1
        elif (logfmt := _from_logfmt(body, incident_id, source)) is not None and not _TIMESTAMP.match(body):
            event = logfmt.model_copy(update={"service": logfmt.service or service_hint})
            formats["logfmt"] += 1
        else:
            event = _from_text(body, incident_id, source, service_hint)
            if body != line:
                event = event.model_copy(update={"raw": line,
                                                 "event_id": stable_event_id(source, line, incident_id)})
            formats["text"] += 1
        events.append(event)
    unique, duplicates = _dedupe(events)
    return IngestionResult(tuple(_finalize(unique, default_service)), tuple(malformed), duplicates,
                           dict(formats), truncated)


def _is_continuation(line: str, previous: NormalizedEvent) -> bool:
    if _TIMESTAMP.match(line.lstrip()) or line.lstrip().startswith("{"):
        return False
    if line.startswith("Traceback (most recent call last)"):
        # A fresh traceback continues only an error line that has no trace yet, or a chained
        # exception announced by "During handling ..." / "The above exception ...".
        last = (previous.stack_trace or "").rsplit("\n", 1)[-1]
        if previous.stack_trace is None:
            return previous.severity in {Severity.ERROR, Severity.CRITICAL}
        return last.startswith(("During handling", "The above exception"))
    if _CONTINUATION.match(line):
        return True
    # An exception headline straight after an error line ("Charge failed\norg.x.FooException: ...").
    headline = re.match(r"^(?:[\w$]+\.)+[A-Z][\w$]*(?:Exception|Error)\b", line)
    if headline and previous.severity in {Severity.ERROR, Severity.CRITICAL, Severity.WARNING}:
        return True
    # Final "ValueError: boom" line of a Python traceback.
    return bool(previous.stack_trace and "Traceback" in previous.stack_trace
                and re.match(r"^[A-Za-z_][\w.]*(?:Error|Exception)\b", line))


def _append_to_stack(previous: NormalizedEvent, line: str, source: str, incident_id: str) -> NormalizedEvent:
    stack = f"{previous.stack_trace}\n{line}" if previous.stack_trace else line
    raw = f"{previous.raw}\n{line}"
    update: dict[str, Any] = {"stack_trace": stack, "raw": raw,
                              "event_id": stable_event_id(source, raw, incident_id)}
    stripped = line.strip()
    exception = _EXCEPTION.search(stripped) if not stripped.startswith("at ") else None
    if exception and not previous.exception_type:
        update["exception_type"] = exception.group(1)
    # Python prints the outermost exception on the last line; promote it to the event message.
    if "Traceback" in stack and re.match(r"^[A-Za-z_][\w.]*(?:Error|Exception)\b", stripped):
        update.update({"message": stripped, "exception_type": exception.group(1) if exception else None,
                       "severity": Severity.ERROR if previous.severity in {Severity.UNKNOWN, Severity.INFO}
                       else previous.severity})
    return previous.model_copy(update=update)


def _dedupe(events: list[NormalizedEvent]) -> tuple[list[NormalizedEvent], int]:
    unique: dict[str, NormalizedEvent] = {}
    duplicates = 0
    for event in events:
        if event.event_id in unique:
            duplicates += 1
        else:
            unique[event.event_id] = event
    return list(unique.values()), duplicates


def _finalize(events: list[NormalizedEvent], default_service: str | None) -> list[NormalizedEvent]:
    """Attach parsed stack metadata and default service once multi-line folding is complete."""
    output: list[NormalizedEvent] = []
    for event in events:
        update: dict[str, Any] = {}
        if default_service and not event.service:
            update["service"] = default_service
        if event.stack_trace and event.stack_trace.count("\n") >= 1:
            trace = parse_stack_trace(f"{event.message}\n{event.stack_trace}")
            if trace.language != "unknown":
                meta = dict(event.metadata)
                meta["stack_language"] = trace.language
                if trace.root_exception:
                    meta["root_exception"] = trace.root_exception.type
                if trace.origin_frame:
                    meta["origin_frame"] = trace.origin_frame.location
                fingerprint = trace.fingerprint()
                if fingerprint:
                    meta["stack_fingerprint"] = fingerprint
                update["metadata"] = meta
                if not event.exception_type and trace.outermost:
                    update["exception_type"] = trace.outermost.type
        if update.get("exception_type") or event.exception_type:
            if event.severity == Severity.UNKNOWN:
                update["severity"] = Severity.ERROR
        output.append(event.model_copy(update=update) if update else event)
    return output
