"""Written incident reports: turn prose (shift reports, ops summaries, ticket digests, safety reports) into events.

Machine logs carry a timestamp and a level on every line. Reports from hospitals, airlines, warehouses, support
desks or safety officers don't: they have a date in the header, section headings ("Summary", "Timeline",
"Actions taken"), times written into sentences ("At 02:10 the EHR became unavailable") and severity carried by
the wording ("fractured ankle", "stopped sorting", "delayed"). This reader recovers those so the same pipeline
(signatures, failure chain, ranked hypotheses, memory, plan) works on them.

Only claims what the text says: a sentence without a time gets no timestamp, and severity comes from explicit
words, never from guessing.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from .models import NormalizedEvent, Severity, stable_event_id

_LOG_LINE = re.compile(
    r"^\s*(?:\d{4}-\d\d-\d\d[T ]\d\d:\d\d|[A-Z][a-z]{2}\s+\d{1,2}\s\d\d:\d\d:\d\d|[{\[]|"
    r"\[?(?:TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\]?[\s:]|\s+at\s|\s+File \")")
_HEADINGS = {
    "summary": "summary", "incident summary": "summary", "overview": "summary", "description": "summary",
    "what happened": "summary", "details": "summary", "incident description": "summary", "background": "summary",
    "timeline": "timeline", "sequence of events": "timeline", "chronology": "timeline",
    "actions taken": "actions", "immediate actions": "actions", "actions": "actions", "response": "actions",
    "what we tried": "actions", "mitigation": "actions", "resolution": "actions", "corrective actions": "actions",
    "impact": "impact", "business impact": "impact", "patient impact": "impact", "customer impact": "impact",
    "contributing factors": "contributing", "causes": "contributing", "root cause": "contributing",
    "findings": "contributing", "lessons learned": "lessons", "follow-up": "lessons", "next steps": "lessons",
}
_HEADER_FIELD = re.compile(r"^\s*(?P<key>date|reported by|prepared by|site|location|unit|ward|department|team|"
                           r"time|shift|reference|ref|author)\s*:\s*(?P<value>.+)$", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_DATE_PATTERNS = (
    re.compile(r"\b(?P<y>\d{4})-(?P<m>\d\d)-(?P<d>\d\d)\b"),
    re.compile(r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?P<mon>[A-Za-z]{3,9})\.?,?\s+(?P<y>\d{4})\b"),
    re.compile(r"\b(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>\d{4})\b"),
)
_TIME = re.compile(r"(?<![\d:.])(?P<h>[01]?\d|2[0-3])[:.](?P<m>[0-5]\d)(?:\s*(?P<ap>[ap])\.?m\.?\b)?(?![\d:])", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

# Severity from explicit wording only. Ordered strongest first.
_CRITICAL = re.compile(r"\b(?:fatal(?:ity)?|died|death|fractur\w*|amputat\w*|unconscious|cardiac arrest|"
                       r"serious(?:ly)? injur\w*|(?:taken|admitted|sent) to hospital|patient harm|"
                       r"(?:explosion|fire) (?:broke out|in the))", re.I)
_ERROR = re.compile(r"\b(?:unavailable|outage|down(?:time)?|offline|failed|failure|faulted|fault|stopped|crash\w*|"
                    r"jam(?:med)?|breach\w*|struck|collided|collision|spill\w*|leak\w*|injur\w*|not respond\w*|"
                    r"could not|unable to|cancel+ed|missed|wrong|error|disconnected|disabled|bypassed|"
                    r"evacuat\w*|charged twice|lost|bug|defect)\b", re.I)
_WARNING = re.compile(r"\b(?:slow\w*|delay\w*|late|backlog|near[- ]miss|degraded|intermittent\w*|warning|alarm\w*|"
                      r"queue\w*|wait(?:ing)? time|blocked|obstructed|worn|overdue|spike|surge|complain\w*|"
                      r"(?:\d+(?:\.\d+)?|six|five|four|three|ten) times (?:the )?normal)\b", re.I)
# Sentences in these sections say what people did or found later; their clock times are not failure times
# ("the worn belt was replaced at 08:20" is not when the belt started failing).
_UNTIMED_SECTIONS = {"actions", "contributing", "lessons"}
_FIXED = re.compile(r"\b(?:replaced|repaired|fixed|rolled back|reverted|restored|patched|re-?seated|cleared)\b", re.I)
_RECOVERED = re.compile(r"\b(?:resumed|restored|recovered|resolved|back to normal|working again|stable again|"
                        r"until (?:the|it) [\w ]{0,30}was (?:fixed|replaced|repaired))\b", re.I)
_DID_NOT_HELP = re.compile(r"\b(?:did not|didn't|does not|failed to) (?:help|resolve|fix|work|reduce)\b|"
                           r"\bagain within\b|\bcame back\b|\bno (?:effect|improvement)\b", re.I)


def looks_like_report(text: str) -> bool:
    """Prose report rather than machine logs: few log-shaped lines and either report headings or sentences."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    log_lines = sum(1 for line in lines if _LOG_LINE.match(line))
    if log_lines / len(lines) > 0.2:
        return False
    headings = sum(1 for line in lines if _heading(line) is not None)
    words = [len(line.split()) for line in lines]
    prose = sum(1 for w in words if w >= 8) / len(lines)
    sentences = sum(len(_SENTENCE.split(line)) for line in lines if len(line.split()) >= 8)
    return headings >= 1 and prose >= 0.25 or prose >= 0.5 and sentences >= 3


def parse_report(text: str, *, incident_id: str = "unknown", source: str = "report",
                 default_service: str | None = None, reference: datetime | None = None) -> list[NormalizedEvent]:
    """One event per sentence or timeline entry, with section, time (when stated) and wording-based severity."""
    lines = text.splitlines()
    report_date = _report_date(lines) or (reference.date() if reference else None)
    section = "summary"
    events: list[NormalizedEvent] = []
    last_time: datetime | None = None
    for raw_line in lines:
        line = raw_line.strip(" \t•*-–—")
        if not line.strip():
            continue
        heading = _heading(line)
        if heading is not None:
            section = heading
            continue
        if _HEADER_FIELD.match(line) or _is_title(line, events):
            continue
        for sentence in _sentences(line):
            when = _time_of(sentence, report_date) if section not in _UNTIMED_SECTIONS else None
            if when and last_time and when < last_time - timedelta(hours=12):
                when += timedelta(days=1)              # the report crossed midnight
            last_time = when or last_time
            severity = _severity(sentence, section)
            metadata = {"section": section, "report": True}
            if section == "actions" and _DID_NOT_HELP.search(sentence):
                metadata["attempt_failed"] = True
            elif _FIXED.search(sentence) and _RECOVERED.search(sentence) and not _DID_NOT_HELP.search(sentence):
                metadata["named_fix"] = True
            events.append(NormalizedEvent(
                event_id=stable_event_id(source, f"{len(events)}\0{sentence}", incident_id), incident_id=incident_id,
                timestamp=when, original_timestamp=_TIME.search(sentence).group(0) if when else None,
                service=default_service, severity=severity, event_type="action" if section == "actions" else "report",
                message=sentence, source=source, metadata=metadata, raw=sentence))
    return events


def _heading(line: str) -> str | None:
    return _HEADINGS.get(line.strip().strip(":#*").strip().lower())


def _is_title(line: str, events: list[NormalizedEvent]) -> bool:
    """The report's own title ("Incident report: Ward 4B ...") carries no fact of its own."""
    return not events and bool(re.match(r"(?i)^(?:[\w ]+ )?(?:incident|operations|safety|support)\b[\w ]*(?:report|summary)\b", line))


def _sentences(line: str) -> list[str]:
    # Timeline entries ("02:05 - Staff reported ...") stay one event each.
    parts = [p.strip() for p in _SENTENCE.split(line) if p.strip()]
    return [p for p in parts if len(p) > 3]


def _report_date(lines: list[str]) -> date | None:
    for line in lines[:15]:
        field = _HEADER_FIELD.match(line.strip())
        candidates = [field.group("value")] if field and field.group("key").lower() in {"date", "time"} else []
        candidates.append(line)
        for text in candidates:
            for pattern in _DATE_PATTERNS:
                match = pattern.search(text)
                if match:
                    parsed = _to_date(match)
                    if parsed:
                        return parsed
    return None


def _to_date(match: re.Match[str]) -> date | None:
    groups = match.groupdict()
    try:
        month = int(groups["m"]) if groups.get("m") else _MONTHS.get(groups["mon"][:3].lower())
        if not month:
            return None
        return date(int(groups["y"]), month, int(groups["d"]))
    except (ValueError, KeyError, TypeError):
        return None


def _time_of(sentence: str, report_date: date | None) -> datetime | None:
    if report_date is None:
        return None
    match = _TIME.search(sentence)
    if not match:
        return None
    hour, minute = int(match.group("h")), int(match.group("m"))
    marker = (match.group("ap") or "").lower()
    if marker == "p" and hour < 12:
        hour += 12
    elif marker == "a" and hour == 12:
        hour = 0
    if not marker and match.group(0).count(".") and hour > 12:
        return None                                   # "14.5" is a number, not a time
    # Local wall-clock time in the report; recorded as UTC because the report gives no zone.
    return datetime(report_date.year, report_date.month, report_date.day, hour, minute, tzinfo=timezone.utc)


_NEGATED = re.compile(r"(?i)\b(?:no|not|nor|without|never|zero)\s+(?:\w+\s+){0,2}?"
                      r"(?:patient harm|harm|injur\w*|fatalit\w*|outage|impact|data loss|delay\w*)")


def _severity(sentence: str, section: str) -> Severity:
    if section in {"actions", "lessons"}:
        return Severity.INFO                          # what people did is context, not a new failure
    sentence = _NEGATED.sub("", sentence)             # "no patient harm was identified" is not harm
    if _CRITICAL.search(sentence):
        return Severity.CRITICAL
    if _ERROR.search(sentence):
        return Severity.ERROR
    if _WARNING.search(sentence):
        return Severity.WARNING
    return Severity.INFO


__all__ = ["looks_like_report", "parse_report"]
