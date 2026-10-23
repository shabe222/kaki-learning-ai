from __future__ import annotations

import json
from typing import Any


def _strip_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return value


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
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
                return text[start : index + 1]
    return None


def parse_json_object(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise TypeError("model content must be a string")
    text = _strip_fence(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        candidate = _first_balanced_object(text)
        if candidate is None:
            raise
        parsed = json.loads(candidate)

    # Some providers occasionally return a JSON string containing the real object.
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    if not isinstance(parsed, dict):
        raise ValueError("top-level JSON value must be an object")
    return parsed
