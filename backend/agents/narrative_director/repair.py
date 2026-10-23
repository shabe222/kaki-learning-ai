from __future__ import annotations

from .enums import RepairMode, Severity
from .models import ValidationIssue


_CLEAN_CODES = {
    "PRIVATE_SCENE_ID_LEAK",
    "STUDENT_LABEL",
    "HUMILIATION",
    "VIOLENCE",
    "PRIVATE_DATA_LEAK",
    "ANSWER_LEAK",
    "PROMPT_INJECTION_FOLLOWED",
    "ACADEMIC_NO_OVERLAP",
}


def choose_repair_mode(issues: list[ValidationIssue], parse_error: str | None = None) -> RepairMode:
    if any(issue.severity == Severity.ERROR and issue.code in _CLEAN_CODES for issue in issues):
        return RepairMode.CLEAN
    if parse_error or any(issue.severity == Severity.ERROR for issue in issues):
        return RepairMode.STRUCTURAL
    return RepairMode.NONE
