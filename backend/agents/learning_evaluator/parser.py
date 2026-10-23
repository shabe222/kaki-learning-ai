from __future__ import annotations
import json
from pydantic import ValidationError
from .models import EvaluationCandidate

class CandidateParseError(ValueError):
    pass

def parse_candidate(value) -> EvaluationCandidate:
    try:
        if isinstance(value, str):
            value = json.loads(value)
        return EvaluationCandidate.model_validate(value)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise CandidateParseError(str(exc)) from exc
