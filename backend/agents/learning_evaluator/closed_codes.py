from __future__ import annotations

from enum import Enum
from typing import Iterable


class ClosedCode(str, Enum):
    def __str__(self) -> str:
        return self.value


class EvidenceErrorCode(ClosedCode):
    ARITHMETIC_SLIP = "arithmetic-slip"
    PERSISTENT_ERROR = "persistent-error"
    SIGN_ERROR = "sign-error"
    FIXTURE_X = "x"
    LOGIC_ERROR = "logic-error"
    HINT_USED = "HINT_USED"
    SOLUTION_VIEWED = "SOLUTION_VIEWED"
    LOW_SCORE = "LOW_SCORE"
    MANUAL_COPY_DETECTED = "MANUAL_COPY_DETECTED"
    NOT_INDEPENDENT_SOLUTION = "NOT_INDEPENDENT_SOLUTION"
    REPEATED_ERROR = "REPEATED_ERROR"
    AI_ASSISTED_EXPLANATION = "AI_ASSISTED_EXPLANATION"
    UNCLASSIFIED_PRIVATE_SIGNAL = "UNCLASSIFIED_PRIVATE_SIGNAL"


class EvidenceWarningCode(ClosedCode):
    CORRECT_WITH_HEAVY_HINTS = "CORRECT_WITH_HEAVY_HINTS"
    SOLUTION_VIEWED = "SOLUTION_VIEWED"
    HINT_USED = "HINT_USED"
    MANUAL_COPY_DETECTED = "MANUAL_COPY_DETECTED"
    NOT_INDEPENDENT_SOLUTION = "NOT_INDEPENDENT_SOLUTION"
    AI_ASSISTED_EXPLANATION = "AI_ASSISTED_EXPLANATION"
    AI_GENERATED_ORIGIN = "AI_GENERATED_ORIGIN"
    DUPLICATE_ATTEMPT = "DUPLICATE_ATTEMPT"
    DUPLICATE_EVIDENCE_IGNORED = "DUPLICATE_EVIDENCE_IGNORED"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    UNCLASSIFIED_PRIVATE_SIGNAL = "UNCLASSIFIED_PRIVATE_SIGNAL"


class MisconceptionCode(ClosedCode):
    PERSISTENT_ERROR = "persistent-error"
    REPEATED_ERROR = "REPEATED_ERROR"
    SIGN_ERROR = "sign-error"
    ARITHMETIC_SLIP = "arithmetic-slip"
    FIXTURE_X = "x"
    FIXTURE_GUESS = "guess"
    FIXTURE_FAKE_CODE = "fake-code"
    FIXTURE_FAKE_ERROR = "fake-error"
    UNCLASSIFIED_PRIVATE_SIGNAL = "UNCLASSIFIED_PRIVATE_SIGNAL"


class EvaluationGapCode(ClosedCode):
    NO_EVIDENCE = "NO_EVIDENCE"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    MISSING_DIMENSION = "MISSING_DIMENSION"
    MISSING_TRANSFER_EVIDENCE = "MISSING_TRANSFER_EVIDENCE"
    OBJECTIVE_MAPPING_MISSING = "OBJECTIVE_MAPPING_MISSING"
    MASTERY_REQUIREMENT_UNSATISFIED = "MASTERY_REQUIREMENT_UNSATISFIED"
    UNRESOLVED_MASTERY_REQUIREMENT = "UNRESOLVED_MASTERY_REQUIREMENT"
    UNRESOLVED_MISCONCEPTION = "UNRESOLVED_MISCONCEPTION"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class CandidateReasonCode(ClosedCode):
    EVIDENCE_ALIGNED = "EVIDENCE_ALIGNED"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    REPEATED_ERROR = "REPEATED_ERROR"
    CONTRADICTION = "CONTRADICTION"
    FALLBACK = "FALLBACK"
    MISSING_DIMENSION = "MISSING_DIMENSION"
    MISSING_TRANSFER_EVIDENCE = "MISSING_TRANSFER_EVIDENCE"
    UNRESOLVED_MASTERY_REQUIREMENT = "UNRESOLVED_MASTERY_REQUIREMENT"
    UNRESOLVED_MISCONCEPTION = "UNRESOLVED_MISCONCEPTION"
    COLLECT_MORE_EVIDENCE = "COLLECT_MORE_EVIDENCE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    FIXTURE_X = "x"


class PublicReasonCode(ClosedCode):
    EVIDENCE_GAP = "EVIDENCE_GAP"
    MISSING_DIMENSION = "MISSING_DIMENSION"
    MISSING_TRANSFER_EVIDENCE = "MISSING_TRANSFER_EVIDENCE"
    UNRESOLVED_MASTERY_REQUIREMENT = "UNRESOLVED_MASTERY_REQUIREMENT"
    UNRESOLVED_MISCONCEPTION = "UNRESOLVED_MISCONCEPTION"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FALLBACK = "FALLBACK"
    MASTERY_REQUIREMENT_SATISFIED = "MASTERY_REQUIREMENT_SATISFIED"
    MASTERY_REQUIREMENT_UNSATISFIED = "MASTERY_REQUIREMENT_UNSATISFIED"
    OBJECTIVE_UNASSESSED = "OBJECTIVE_UNASSESSED"
    OBJECTIVE_NOT_ACHIEVED = "OBJECTIVE_NOT_ACHIEVED"
    OBJECTIVE_COUNTER_EVIDENCE = "OBJECTIVE_COUNTER_EVIDENCE"
    UNCLASSIFIED_PRIVATE_SIGNAL = "UNCLASSIFIED_PRIVATE_SIGNAL"


class PublicWarningCode(ClosedCode):
    CORRECT_WITH_HEAVY_HINTS = "CORRECT_WITH_HEAVY_HINTS"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNCLASSIFIED_PRIVATE_SIGNAL = "UNCLASSIFIED_PRIVATE_SIGNAL"


_EVIDENCE_ERROR_VALUES = {x.value for x in EvidenceErrorCode}
_EVIDENCE_WARNING_VALUES = {x.value for x in EvidenceWarningCode}
_MISCONCEPTION_VALUES = {x.value for x in MisconceptionCode}
_CANDIDATE_REASON_VALUES = {x.value for x in CandidateReasonCode}
_PUBLIC_REASON_VALUES = {x.value for x in PublicReasonCode}
_PUBLIC_WARNING_VALUES = {x.value for x in PublicWarningCode}
_GAP_VALUES = {x.value for x in EvaluationGapCode}


def _values(values: Iterable[object]) -> list[str]:
    return [str(v.value if isinstance(v, Enum) else v) for v in values]


def unknown_evidence_error_codes(values: Iterable[object]) -> list[str]:
    return [v for v in _values(values) if v not in _EVIDENCE_ERROR_VALUES]


def unknown_evidence_warning_codes(values: Iterable[object]) -> list[str]:
    return [v for v in _values(values) if v not in _EVIDENCE_WARNING_VALUES]


def is_registered_misconception_code(value: object) -> bool:
    return str(value.value if isinstance(value, Enum) else value) in _MISCONCEPTION_VALUES


def unknown_candidate_reason_codes(values: Iterable[object]) -> list[str]:
    return [v for v in _values(values) if v not in _CANDIDATE_REASON_VALUES]


def is_public_reason_code(value: object) -> bool:
    return str(value.value if isinstance(value, Enum) else value) in _PUBLIC_REASON_VALUES


def is_public_warning_code(value: object) -> bool:
    return str(value.value if isinstance(value, Enum) else value) in _PUBLIC_WARNING_VALUES


def is_gap_code(value: object) -> bool:
    return str(value.value if isinstance(value, Enum) else value) in _GAP_VALUES


def to_public_reason(value: object) -> PublicReasonCode:
    raw = str(value.value if isinstance(value, Enum) else value)
    try:
        return PublicReasonCode(raw)
    except ValueError:
        return PublicReasonCode.UNCLASSIFIED_PRIVATE_SIGNAL
