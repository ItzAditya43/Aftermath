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
