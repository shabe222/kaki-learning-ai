"""Shared extraction of a JSON object from free-form LLM text."""
from __future__ import annotations

import json
from typing import Any


def extract_json_object(text: Any) -> dict[str, Any] | None:
    """Return the first balanced JSON object found in ``text``.

    Handles the common LLM shapes: bare JSON, ```json fences, and prose around
    a single object.  Returns None when nothing parseable is present — callers
    degrade to their deterministic fallback instead of guessing.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    for prefix in ("```json", "```JSON", "```"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(raw[start:index + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
