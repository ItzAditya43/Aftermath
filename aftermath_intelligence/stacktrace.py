"""Language-aware stack trace parsing.

Recognises JVM, Python, Node.js and Go traces. Each parser only claims what its format
actually encodes: when a trace cannot be identified it is returned as `language="unknown"`
with no frames rather than a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256

_JAVA_FRAME = re.compile(r"^\s*at\s+(?P<func>[\w$.<>/]+)\((?P<file>[^:()]+)?(?::(?P<line>\d+))?[^)]*\)")
_JAVA_EXC = re.compile(r"^(?:Caused by:\s*|Suppressed:\s*)?(?P<type>(?:[a-zA-Z_$][\w$]*\.)+[A-Z][\w$]*(?:Exception|Error|Throwable)|[A-Z]\w*(?:Exception|Error))(?::\s*(?P<msg>.*))?$")
_PY_FRAME = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>[\w<>.]+)')
_PY_EXC = re.compile(r"^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)|[A-Z][\w.]*Error)(?::\s*(?P<msg>.*))?$")
_NODE_FRAME = re.compile(r"^\s*at\s+(?:(?P<func>[^\s(]+)\s+)?\(?(?P<file>[^():\s]+\.(?:m?js|cjs|ts|tsx|jsx)):(?P<line>\d+)(?::\d+)?\)?")
_GO_FUNC = re.compile(r"^(?P<func>[\w./*()-]+\.[\w*()-]+)\(.*\)$")
_GO_LOC = re.compile(r"^\s+(?P<file>/?[\w./-]+\.go):(?P<line>\d+)")

_LIBRARY_HINTS = (
    "java.", "javax.", "jdk.", "sun.", "com.sun.", "org.springframework.", "org.apache.", "org.hibernate.",
    "com.zaxxer.", "io.netty.", "kotlin.", "scala.", "org.postgresql.", "com.mysql.", "reactor.", "io.grpc.",
    "site-packages", "dist-packages", "/lib/python", "<frozen", "node_modules", "node:internal", "internal/",
    "/usr/lib", "/usr/local/go/", "runtime/", "net/http.", "asyncio/", "starlette/", "uvicorn/",
)


@dataclass(frozen=True)
class StackFrame:
    function: str
    file: str | None = None
    line: int | None = None
    is_application: bool = True

    @property
    def location(self) -> str:
        where = f"{self.file}:{self.line}" if self.file and self.line else (self.file or "?")
        return f"{self.function} ({where})"


@dataclass(frozen=True)
class ExceptionLink:
    type: str
    message: str = ""

    @property
    def short_type(self) -> str:
        return self.type.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class StackTrace:
    language: str
    exceptions: tuple[ExceptionLink, ...] = ()
    frames: tuple[StackFrame, ...] = ()
    raw_frame_count: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def outermost(self) -> ExceptionLink | None:
        return self.exceptions[0] if self.exceptions else None

    @property
    def root_exception(self) -> ExceptionLink | None:
        """The innermost cause (JVM `Caused by` tail, Python `__cause__` head)."""
        return self.exceptions[-1] if self.exceptions else None

    @property
    def origin_frame(self) -> StackFrame | None:
        """First application (non-library) frame: the most useful 'where did this start'."""
        return next((f for f in self.frames if f.is_application), None)

    @property
    def application_frames(self) -> tuple[StackFrame, ...]:
        return tuple(f for f in self.frames if f.is_application)

    def fingerprint(self) -> str:
        """Stable across line-number drift and volatile messages: exception types + app functions."""
        parts = [e.short_type for e in self.exceptions] + [f.function for f in self.application_frames[:6]]
        if not parts:
            return ""
        return sha256("|".join(parts).encode()).hexdigest()[:16]


def _is_application(function: str, file: str | None) -> bool:
    text = f"{function} {file or ''}"
    return not any(hint in text for hint in _LIBRARY_HINTS)


def detect_language(text: str) -> str:
    if re.search(r"^\s*at\s+[\w$.<>/]+\([\w$]+\.(?:java|kt|scala)(?::\d+)?\)", text, re.M) or "Caused by:" in text:
        return "jvm"
    if "Traceback (most recent call last)" in text or _PY_FRAME.search(text):
        return "python"
    if re.search(r"^goroutine \d+ \[", text, re.M) or re.search(r"^panic:", text, re.M):
        return "go"
    if re.search(r"^\s*at\s+.*\.(?:m?js|cjs|ts):\d+", text, re.M):
        return "node"
    if re.search(r"^\s*at\s+[\w$.<>/]+\(", text, re.M):
        return "jvm"
    return "unknown"


def parse_stack_trace(text: str) -> StackTrace:
    language = detect_language(text)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if language == "jvm":
        return _parse_jvm(lines)
    if language == "python":
        return _parse_python(lines)
    if language == "node":
        return _parse_node(lines)
    if language == "go":
        return _parse_go(lines)
    return StackTrace(language="unknown")


def _parse_jvm(lines: list[str]) -> StackTrace:
    exceptions: list[ExceptionLink] = []
    frames: list[StackFrame] = []
    omitted = 0
    for line in lines:
        stripped = line.strip()
        frame = _JAVA_FRAME.match(line)
        if frame:
            file = frame.group("file")
            frames.append(StackFrame(frame.group("func"), file, _int(frame.group("line")),
                                     _is_application(frame.group("func"), file)))
            continue
        more = re.match(r"^\.\.\.\s*(\d+)\s+more", stripped)
        if more:
            omitted += int(more.group(1))
            continue
        # Exception headlines can appear mid-line ("... failed\norg.foo.BarException: msg").
        exc = _JAVA_EXC.match(stripped)
        if exc:
            exceptions.append(ExceptionLink(exc.group("type"), (exc.group("msg") or "").strip()[:300]))
            nested = re.search(r"nested exception is ((?:[\w$]+\.)+[A-Z][\w$]*(?:Exception|Error)):\s*(.*)$",
                               exc.group("msg") or "")
            if nested:
                exceptions.append(ExceptionLink(nested.group(1), nested.group(2).strip()[:300]))
    notes = (f"{omitted} frames elided by the JVM ('... N more')",) if omitted else ()
    return StackTrace("jvm", _dedupe_chain(exceptions), tuple(frames), len(frames) + omitted, notes)


def _parse_python(lines: list[str]) -> StackTrace:
    # Python prints the original exception first and the final (outermost) one last.
    # We normalise to outermost-first, matching the JVM ordering.
    blocks: list[tuple[list[StackFrame], ExceptionLink | None]] = []
    frames: list[StackFrame] = []
    for line in lines:
        frame = _PY_FRAME.match(line)
        if frame:
            frames.append(StackFrame(frame.group("func"), frame.group("file"), _int(frame.group("line")),
                                     _is_application(frame.group("func"), frame.group("file"))))
            continue
        if line.startswith("Traceback") or line.startswith(("During handling", "The above exception")):
            continue
        exc = _PY_EXC.match(line.strip()) if not line[:1].isspace() else None
        if exc:
            blocks.append((frames, ExceptionLink(exc.group("type"), (exc.group("msg") or "").strip()[:300])))
            frames = []
    if frames:
        blocks.append((frames, None))
    exceptions = tuple(e for _, e in reversed(blocks) if e)
    # Innermost-call-first frames of the root block are most specific; Python lists outer calls first.
    ordered = [f for block_frames, _ in blocks for f in reversed(block_frames)]
    return StackTrace("python", exceptions, tuple(ordered), len(ordered))


def _parse_node(lines: list[str]) -> StackTrace:
    exceptions: list[ExceptionLink] = []
    frames: list[StackFrame] = []
    for line in lines:
        frame = _NODE_FRAME.match(line)
        if frame:
            func = frame.group("func") or "<anonymous>"
            frames.append(StackFrame(func, frame.group("file"), _int(frame.group("line")),
                                     _is_application(func, frame.group("file"))))
            continue
        exc = re.match(r"^(?:Uncaught\s+)?(?P<type>[A-Z]\w*(?:Error|Exception)|Error)(?::\s*(?P<msg>.*))?$", line.strip())
        if exc:
            exceptions.append(ExceptionLink(exc.group("type"), (exc.group("msg") or "").strip()[:300]))
    return StackTrace("node", tuple(exceptions), tuple(frames), len(frames))


def _parse_go(lines: list[str]) -> StackTrace:
    exceptions: list[ExceptionLink] = []
    frames: list[StackFrame] = []
    pending: str | None = None
    for line in lines:
        if line.startswith("panic:"):
            exceptions.append(ExceptionLink("panic", line[6:].strip()[:300]))
            continue
        func = _GO_FUNC.match(line.strip()) if not line[:1].isspace() else None
        if func:
            pending = func.group("func")
            continue
        loc = _GO_LOC.match(line)
        if loc and pending:
            frames.append(StackFrame(pending, loc.group("file"), _int(loc.group("line")),
                                     _is_application(pending, loc.group("file"))))
            pending = None
    return StackTrace("go", tuple(exceptions), tuple(frames), len(frames))


def _dedupe_chain(chain: list[ExceptionLink]) -> tuple[ExceptionLink, ...]:
    out: list[ExceptionLink] = []
    for link in chain:
        if out and out[-1].type == link.type:
            continue
        out.append(link)
    return tuple(out)


def _int(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def stack_similarity(left: StackTrace, right: StackTrace) -> float:
    """Jaccard over exception types and application functions (0..1)."""
    a = {e.short_type for e in left.exceptions} | {f.function for f in left.application_frames}
    b = {e.short_type for e in right.exceptions} | {f.function for f in right.application_frames}
    return len(a & b) / len(a | b) if a | b else 0.0
