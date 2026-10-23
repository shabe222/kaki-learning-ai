from __future__ import annotations
import json, re
from typing import Any
from .models import CandidateBlueprint

class ParseError(ValueError): pass

def _extract_json(text: str) -> str:
    text=text.strip()
    fence=re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.S|re.I)
    if fence: return fence.group(1)
    start=text.find("{"); end=text.rfind("}")
    if start>=0 and end>start: return text[start:end+1]
    return text

def parse_candidate(value: str | dict[str, Any]) -> CandidateBlueprint:
    try:
        data=json.loads(_extract_json(value)) if isinstance(value,str) else value
    except Exception as e:
        raise ParseError(f"invalid JSON: {type(e).__name__}") from e
    try:
        return CandidateBlueprint.model_validate(data)
    except Exception as e:
        raise ParseError(str(e)) from e
