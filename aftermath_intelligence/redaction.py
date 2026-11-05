from __future__ import annotations

import re
from typing import Any

_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_PROVIDER_KEY = re.compile(r"\b(?:gsk_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9._-]{20,})\b")
_SECRET_FIELD = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|client_secret|authorization)\b[\"']?\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&}]+)"
)


def redact_sensitive_text(value: str) -> str:
    """Redact common credentials before evidence leaves the local process."""
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _PROVIDER_KEY.sub("[REDACTED_KEY]", value)
    def replace_field(match: re.Match[str]) -> str:
        original = match.group(2)
        if original.startswith('"'):
            redacted = '"[REDACTED]"'
        elif original.startswith("'"):
            redacted = "'[REDACTED]'"
        else:
            redacted = "[REDACTED]"
        return match.group(1) + redacted

    return _SECRET_FIELD.sub(replace_field, value)


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            if re.search(r"(?i)(api[_-]?key|password|passwd|secret|authorization|access[_-]?token|refresh[_-]?token)", str(key)):
                continue
            output[str(key)] = redact_sensitive_data(item)
        return output
    return value
