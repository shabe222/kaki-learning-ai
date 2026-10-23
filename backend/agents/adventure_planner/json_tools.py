from __future__ import annotations

import json
from typing import Any


class JSONExtractionError(ValueError):
    pass


def _strip_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        first_break = value.find("\n")
        if first_break >= 0:
            value = value[first_break + 1 :]
        if value.rstrip().endswith("```"):
            value = value.rstrip()[:-3]
    return value.strip()


def _balanced_object(text: str) -> str | None:
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
    if not raw or not raw.strip():
        raise JSONExtractionError("LLM output is empty")
    candidates = [raw.strip(), _strip_fence(raw)]
    balanced = _balanced_object(raw)
    if balanced:
        candidates.append(balanced)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if not isinstance(parsed, dict):
                raise JSONExtractionError("top-level JSON must be an object")
            return parsed
        except (json.JSONDecodeError, JSONExtractionError) as error:
            last_error = error
    raise JSONExtractionError(f"unable to extract JSON object: {last_error}")
