from __future__ import annotations

import json
from typing import Any


class NarrativeJSONError(ValueError):
    pass


def parse_json_object_strict(raw: str, finish_reason: str | None = None) -> dict[str, Any]:
    if finish_reason == "length":
        raise NarrativeJSONError("model output was truncated")
    value = (raw or "").strip()
    if not value:
        raise NarrativeJSONError("model output is empty")
    if not (value.startswith("{") and value.endswith("}")):
        raise NarrativeJSONError("model output must be exactly one JSON object")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise NarrativeJSONError(f"invalid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise NarrativeJSONError("top-level JSON must be an object")
    return parsed
