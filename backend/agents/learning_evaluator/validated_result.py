"""Deprecated v0.1.4 compatibility surface.

No Python-private seal is a security boundary.  The legacy helper can only create an
explicitly untrusted candidate; it can never be projected or replayed as an authentic
result.  Formal code must use EvaluationResultEnvelope plus an
EvaluationResultAuthenticityPort.
"""
from __future__ import annotations
import copy
from dataclasses import dataclass
from .models import LearningEvaluationResult
from .result_authenticity import EvaluationResultEnvelope

_SEAL = None

class ValidatedLearningEvaluationResult:
    def __init__(self, *args, **kwargs):
        del args, kwargs
        raise TypeError("CANNOT_BE_PUBLICLY_FORGED: RESULT_AUTHENTICITY_PORT_REQUIRED")

@dataclass(frozen=True)
class UntrustedLegacyResultCandidate:
    result: LearningEvaluationResult
    def __getattr__(self, name):
        return copy.deepcopy(getattr(self.result, name))
    def model_dump(self, *a, **kw):
        return self.result.model_dump(*a, **kw)
    def model_copy(self, *a, **kw):
        return self.result.model_copy(*a, **kw)

def seal_validated_result(result: LearningEvaluationResult):
    del result
    raise TypeError("LEGACY_SEAL_REMOVED: RESULT_AUTHENTICITY_PORT_REQUIRED")

def unwrap_validated_result(value):
    del value
    raise TypeError("RESULT_AUTHENTICITY_PORT_REQUIRED")

__all__ = ["EvaluationResultEnvelope"]
