from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    DESTRUCTIVE = "destructive"


class ToolSpec(BaseModel):
    """A connector-owned capability declaration; intelligence never invents tools."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    risk: RiskLevel
    read_only: bool
    requires_approval: bool = False
    allowed_parameters: tuple[str, ...] = ()
    required_parameters: tuple[str, ...] = ()
    parameter_types: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,79}", value):
            raise ValueError("tool name must be an identifier, not a command")
        return value


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str
    success: bool
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    source_event_ids: tuple[str, ...] = ()


class ToolAdapter(Protocol):
    """Backend/connector integration contract. Implementations own real execution."""

    def list_tools(self) -> tuple[ToolSpec, ...]: ...

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult: ...


def validate_tool_arguments(spec: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any]:
    unknown = set(arguments) - set(spec.allowed_parameters)
    missing = set(spec.required_parameters) - set(arguments)
    forbidden = set(arguments) & {"command", "cmd", "shell", "script", "exec"}
    if unknown:
        raise ValueError(f"unsupported tool arguments: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"missing required tool arguments: {', '.join(sorted(missing))}")
    if forbidden:
        raise ValueError("model-generated shell or command arguments are not allowed")
    for name, expected in spec.parameter_types.items():
        if name not in arguments:
            continue
        value = arguments[name]
        valid = {
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
        }
        if expected not in valid:
            raise ValueError(f"unsupported parameter type for {name}")
        if not valid[expected](value):
            raise ValueError(f"invalid type for tool argument {name}")
    return arguments


class ToolRegistry:
    """Simple `ToolAdapter` for connector owners: register a function per declared capability.

    The registry re-validates arguments and refuses non-read-only tools unless the call carries an
    approval token issued by a human-facing flow (`approve`). The investigation engine never
    requests approval itself, so model output can never trigger a state-changing call.
    """

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, Any]] = {}
        self._approved: set[tuple[str, str]] = set()

    def register(self, spec: ToolSpec, handler: Any) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name} already registered")
        if spec.read_only and spec.risk != RiskLevel.READ_ONLY:
            raise ValueError("a read_only tool must declare risk=read_only")
        self._tools[spec.name] = (spec, handler)

    def list_tools(self) -> tuple[ToolSpec, ...]:
        return tuple(spec for spec, _ in self._tools.values())

    def approve(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Record a human approval for one exact call (tool + arguments)."""
        self._approved.add((tool_name, repr(sorted(arguments.items()))))

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        if tool_name not in self._tools:
            return ToolResult(tool_name=tool_name, success=False, summary="unknown tool", error="unknown_tool")
        spec, handler = self._tools[tool_name]
        arguments = validate_tool_arguments(spec, dict(arguments))
        needs_approval = spec.requires_approval or not spec.read_only or spec.risk != RiskLevel.READ_ONLY
        key = (tool_name, repr(sorted(arguments.items())))
        if needs_approval and key not in self._approved:
            return ToolResult(tool_name=tool_name, success=False, summary="approval required", error="approval_required")
        self._approved.discard(key)  # approvals are single-use
        result = handler(**arguments)
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, dict):
            return ToolResult(tool_name=tool_name, success=True, summary=str(result.get("summary", ""))[:400],
                              data={k: v for k, v in result.items() if k != "summary"})
        return ToolResult(tool_name=tool_name, success=True, summary=str(result)[:400])
