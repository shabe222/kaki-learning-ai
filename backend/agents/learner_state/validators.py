from __future__ import annotations

from dataclasses import dataclass

from .models import (
    BarrierCategory,
    DifficultyAction,
    EvidenceResult,
    LearnerStateBlueprint,
    LearnerStateRequest,
    ReasoningState,
    SupportLevel,
    TaskType,
    TopicMastery,
    TransferReadiness,
)


_PROHIBITED_LABELS = {
    "笨",
    "懒惰",
    "差生",
    "学困生",
    "低智商",
    "智力低",
    "视觉型学习者",
    "听觉型学习者",
    "kinesthetic learner",
    "lazy student",
    "stupid",
    "low intelligence",
}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


class LearnerStateValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("; ".join(f"{i.code}:{i.message}" for i in issues))


def validate_blueprint(blueprint: LearnerStateBlueprint, request: LearnerStateRequest) -> None:
    issues: list[ValidationIssue] = []
    allowed = request.allowed_evidence_refs()
    refs = set(blueprint.evidence_refs) | set(blueprint.primary_barrier.evidence_refs)
    unsupported = sorted(refs - allowed)
    if unsupported:
        issues.append(ValidationIssue("unsupported_evidence_ref", ",".join(unsupported)))

    text = f"{blueprint.primary_barrier.description} {blueprint.next_teaching_focus}".lower()
    for label in _PROHIBITED_LABELS:
        if label.lower() in text:
            issues.append(ValidationIssue("prohibited_learner_label", label))

    current = list(request.current_evidence)
    has_transfer_success = any(
        (e.transfer_context or e.task_type == TaskType.TRANSFER) and e.result == EvidenceResult.CORRECT
        for e in current
    )
    has_transfer_evidence = any(
        e.transfer_context or e.task_type == TaskType.TRANSFER
        for e in current
    )
    has_self_correction = any(e.self_corrected for e in current)
    has_performance = any(e.result != EvidenceResult.UNKNOWN for e in current)

    if blueprint.primary_barrier.category == BarrierCategory.TRANSFER_GAP and not has_transfer_evidence:
        issues.append(ValidationIssue(
            "unsupported_transfer_gap",
            "transfer_gap requires current transfer-task evidence",
        ))
    if blueprint.topic_mastery == TopicMastery.TRANSFERABLE and not has_transfer_success:
        issues.append(ValidationIssue("unsupported_transferable_state", "transferable requires current transfer success"))
    if blueprint.transfer_readiness == TransferReadiness.DEMONSTRATED and not has_transfer_success:
        issues.append(ValidationIssue("unsupported_transfer_demonstrated", "demonstrated requires transfer success"))
    if blueprint.reasoning_state == ReasoningState.CAN_SELF_CORRECT and not has_self_correction:
        issues.append(ValidationIssue("unsupported_self_correction", "can_self_correct requires self-correction evidence"))
    if blueprint.support_level == SupportLevel.INDEPENDENT and blueprint.topic_mastery in {
        TopicMastery.UNKNOWN,
        TopicMastery.EMERGING,
    }:
        issues.append(ValidationIssue("support_mastery_conflict", "independent conflicts with low mastery"))
    if blueprint.difficulty_action == DifficultyAction.INCREASE_HALF_STEP and blueprint.topic_mastery not in {
        TopicMastery.SECURE,
        TopicMastery.TRANSFERABLE,
    }:
        issues.append(ValidationIssue("unsupported_difficulty_increase", "increase requires secure or transferable mastery"))
    if not has_performance and blueprint.primary_barrier.category not in {
        BarrierCategory.INSUFFICIENT_EVIDENCE,
        BarrierCategory.CONCEPT_CONFUSION,
    }:
        issues.append(ValidationIssue("overconfident_barrier", "no performance evidence supports a precise barrier"))

    if request.context_mode.value == "current_item_only":
        recent_refs = {e.evidence_id for e in request.recent_related_evidence}
        leaked = sorted(refs & recent_refs)
        if leaked:
            issues.append(ValidationIssue("current_item_only_history_leak", ",".join(leaked)))

    if issues:
        raise LearnerStateValidationError(issues)
