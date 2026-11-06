"""Tolerant extraction of a JSON object from model output."""
from __future__ import annotations

import json
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    """Return the first JSON object in `text`, tolerating code fences and leading prose.

    Raises ValueError when no object can be decoded; callers treat that as invalid output.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        value = None
        decoder = json.JSONDecoder()
        for index, char in enumerate(stripped):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(stripped[index:])
                break
            except json.JSONDecodeError:
                continue
    if not isinstance(value, dict):
        raise ValueError("no JSON object found in model output")
    return value
