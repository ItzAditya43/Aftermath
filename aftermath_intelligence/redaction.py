"""Credential redaction applied before any evidence leaves the process (AI providers, reports, tools).

Covers: bearer tokens, provider/API keys with well-known prefixes (Groq, Google, OpenAI/Anthropic-style, AWS access
key ids, GitHub, GitLab, Slack, Stripe, SendGrid, Hugging Face, npm, PyPI, Twilio, DigitalOcean, Shopify), JWTs,
private key blocks, credentials embedded in URLs / connection strings (including an empty user, `redis://:pw@`),
AWS secret access keys on lines that mention AWS, payment card numbers (Luhn-checked), and `name=value` /
`"name": "value"` pairs whose name looks secret, including prefixed environment variables (`DB_PASSWORD`,
`PGPASSWORD`, `MYSQL_ROOT_PASSWORD`) and connection-string keys (`AccountKey`, `SharedAccessKey`, `sig`).

E-mail addresses are kept by default because they are often the key evidence (whose login, whose order); set
AFTERMATH_REDACT_EMAILS=1 to mask them as [REDACTED_EMAIL]. Hostnames and IPs are always kept.
"""
from __future__ import annotations

import os
import re
from typing import Any

_BEARER = re.compile(r"(?i)\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{8,}")
# Prefixes distinctive enough to match even when glued to a preceding word ("xghp_...", "token:sk-proj-...").
# Generic ones (plain "sk-", Stripe, Twilio) keep the word boundary so words like "disk-usage-..." survive.
_GLUED_KEY = re.compile(
    r"(?:sk-(?:ant|proj)-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|glpat-[A-Za-z0-9_-]{20,}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}|gsk_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|hf_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{30,}|pypi-[A-Za-z0-9_-]{50,}|dop_v1_[a-f0-9]{64})(?![A-Za-z0-9])")
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
    r"|SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"     # SendGrid
    r"|hf_[A-Za-z0-9]{30,}"                          # Hugging Face
    r"|npm_[A-Za-z0-9]{30,}"                         # npm
    r"|pypi-[A-Za-z0-9_-]{50,}"                      # PyPI
    r"|SK[0-9a-f]{32}"                               # Twilio API key
    r"|dop_v1_[a-f0-9]{64}"                          # DigitalOcean
    r"|shp(?:at|ss|ca|pa)_[a-fA-F0-9]{32}"           # Shopify
    r")\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}://[^\s:/@]*):([^\s@/]+)@")
# A 40-character AWS secret access key has no prefix; only redact it on lines that mention AWS, and require mixed
# case plus a digit so 40-character hex commit SHAs survive.
_AWS_SECRET = re.compile(r"(?<![A-Za-z0-9/+=])(?=[A-Za-z0-9/+]{0,39}[A-Z])(?=[A-Za-z0-9/+]{0,39}[a-z])"
                         r"(?=[A-Za-z0-9/+]{0,39}[0-9])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])")
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
_CARD = re.compile(r"(?<![\d-])(?:4\d{3}|5[1-5]\d{2}|2[2-7]\d{2}|3[47]\d{2}|6011)(?:[ -]?\d){9,12}(?![\d-])")
# A regex of secret *field names*, not a secret.
_SECRET_NAME = (r"(?:api[_-]?key|apikey|access[_-]?key(?:[_-]?id)?|secret[_-]?(?:access[_-]?)?key|aws[_-]?(?:secret|key)[\w-]*"  # noqa: S105
                r"|access[_-]?token|refresh[_-]?token|auth[_-]?token|id[_-]?token|session[_-]?token|token"
                r"|password|passwd|pwd|secret|client[_-]?secret|private[_-]?key|authorization|cookie|set-cookie"
                r"|account[_-]?key|shared[_-]?access[_-]?key|sig|credentials?)")
# The name may carry any prefix ("DB_PASSWORD", "PGPASSWORD", "spring.datasource.password") but must end the word,
# so "input_tokens" or "password_reset" are not treated as secrets.
_SECRET_FIELD = re.compile(
    r"(?i)([\"']?(?<![\w.-])[\w.-]*?" + _SECRET_NAME + r"(?![\w])[\"']?\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&}]+)"
)
# Dict keys: any prefix ("db_password", "pgpassword"); a suffix only as a whole segment ("api_key_id"), so
# "input_tokens" survives.
_SECRET_KEY_NAME = re.compile(r"(?i)^[\w-]*?" + _SECRET_NAME + r"(?:[_-][\w-]*)?$")


def redact_sensitive_text(value: str) -> str:
    """Redact common credentials before evidence leaves the local process."""
    value = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", value)
    value = _BEARER.sub(lambda m: f"{m.group(1)} [REDACTED]", value)
    value = _JWT.sub("[REDACTED_JWT]", value)
    value = _PROVIDER_KEY.sub("[REDACTED_KEY]", value)
    value = _GLUED_KEY.sub("[REDACTED_KEY]", value)
    value = _URL_CREDENTIALS.sub(r"\1:[REDACTED]@", value)
    if "aws" in value.lower():
        value = "\n".join(_AWS_SECRET.sub("[REDACTED_KEY]", line) if "aws" in line.lower() else line
                          for line in value.split("\n"))
    value = _CARD.sub(lambda m: "[REDACTED_CARD]" if _is_card(m, value) else m.group(0), value)
    if os.environ.get("AFTERMATH_REDACT_EMAILS", "").strip().lower() in {"1", "true", "yes"}:
        value = _EMAIL.sub("[REDACTED_EMAIL]", value)

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


_ID_LABEL = re.compile(r"(?i)id\s*[=:]\s*[\"']?$")


def _is_card(match: re.Match[str], text: str) -> bool:
    # Numeric trace / order / request ids can pass the Luhn check; a value labelled as an id is kept.
    return _luhn(match.group(0)) and not _ID_LABEL.search(text[max(0, match.start() - 16):match.start()])


def _luhn(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d > 4 else d * 2) for i, d in enumerate(reversed(digits)))
    return total % 10 == 0


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
