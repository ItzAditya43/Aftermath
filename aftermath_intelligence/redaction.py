"""Credential redaction applied before any evidence leaves the process (AI providers, reports, tools).

Covers: bearer tokens, provider/API keys with well-known prefixes (Groq, Google, OpenAI/Anthropic-style, AWS access
key ids, GitHub, GitLab, Slack, Stripe), JWTs, private key blocks, credentials embedded in URLs / connection
strings, and `name=value` / `"name": "value"` pairs whose name looks secret (password, token, api_key, aws_key,
secret_key, client_secret, authorization, cookie, ...).
"""
from __future__ import annotations

import re
from typing import Any

_BEARER = re.compile(r"(?i)\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{8,}")
_PROVIDER_KEY = re.compile(
    r"\b(?:"
    r"gsk_[A-Za-z0-9]{20,}"                          # Groq
    r"|AIza[A-Za-z0-9_-]{20,}"                       # Google
    r"|AQ\.[A-Za-z0-9._-]{20,}"                      # Google OAuth-style
    r"|sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"         # OpenAI / Anthropic style
    r"|(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[A-Z0-9]{16}"  # AWS access key ids
    r"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"  # GitHub
    r"|glpat-[A-Za-z0-9_-]{20,}"                     # GitLab
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"                # Slack
    r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"     # Stripe
    r")\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}://[^\s:/@]+):([^\s@/]+)@")
# A regex of secret *field names*, not a secret.
_SECRET_NAME = (r"(?:api[_-]?key|apikey|access[_-]?key(?:[_-]?id)?|secret[_-]?(?:access[_-]?)?key|aws[_-]?(?:secret|key)[\w-]*"  # noqa: S105
                r"|access[_-]?token|refresh[_-]?token|auth[_-]?token|id[_-]?token|session[_-]?token|token"
                r"|password|passwd|pwd|secret|client[_-]?secret|private[_-]?key|authorization|cookie|set-cookie)")
_SECRET_FIELD = re.compile(
    r"(?i)([\"']?\b" + _SECRET_NAME + r"\b[\"']?\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&}]+)"
)
# Dict keys: the secret name must be a whole segment ("db_password", "api_key_id"), so "input_tokens" survives.
_SECRET_KEY_NAME = re.compile(r"(?i)^(?:[\w-]*[_-])?" + _SECRET_NAME + r"(?:[_-][\w-]*)?$")


def redact_sensitive_text(value: str) -> str:
    """Redact common credentials before evidence leaves the local process."""
    value = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", value)
    value = _BEARER.sub(lambda m: f"{m.group(1)} [REDACTED]", value)
    value = _JWT.sub("[REDACTED_JWT]", value)
    value = _PROVIDER_KEY.sub("[REDACTED_KEY]", value)
    value = _URL_CREDENTIALS.sub(r"\1:[REDACTED]@", value)

    def replace_field(match: re.Match[str]) -> str:
        original = match.group(2)
        if original.startswith("[REDACTED"):
            return match.group(0)
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
            if _SECRET_KEY_NAME.match(str(key)):
                continue
            output[str(key)] = redact_sensitive_data(item)
        return output
    return value
