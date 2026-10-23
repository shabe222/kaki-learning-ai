from __future__ import annotations

from .models import (
    BarrierCategory,
    DifficultyAction,
    EvidenceResult,
    HintLevel,
    LearnerStateBlueprint,
    LearnerStateRequest,
    PrimaryBarrier,
    ReasoningState,
    SupportLevel,
    TaskType,
    TopicMastery,
    TransferReadiness,
)


def _all_evidence(request: LearnerStateRequest):
    items = list(request.current_evidence)
    if request.context_mode.value != "current_item_only":
        items.extend(request.recent_related_evidence)
    return items


def build_fallback_blueprint(request: LearnerStateRequest) -> LearnerStateBlueprint:
    evidence = _all_evidence(request)
    refs = [item.evidence_id for item in evidence[:8]]
    if request.explicit_confusion:
        refs = ["explicit_confusion", *refs]

    performance = [
        item for item in evidence
        if item.result != EvidenceResult.UNKNOWN and item.task_type != TaskType.UNKNOWN
    ]
    correct = [item for item in performance if item.result == EvidenceResult.CORRECT]
    partial = [item for item in performance if item.result == EvidenceResult.PARTIALLY_CORRECT]
    failed = [item for item in performance if item.result == EvidenceResult.NEEDS_REMEDIATION]
    transfer = [item for item in performance if item.transfer_context or item.task_type == TaskType.TRANSFER]
    transfer_correct = [item for item in transfer if item.result == EvidenceResult.CORRECT]
    transfer_failed = [item for item in transfer if item.result != EvidenceResult.CORRECT]
    self_corrected = any(item.self_corrected for item in performance)
    corrected_with_support = any(item.corrected_with_support for item in performance)
    hinted = [item for item in performance if item.hint_level_used != HintLevel.NONE]
    repeated_failure = sum(1 for item in failed if item.attempts >= 2) >= 1 or len(failed) >= 2

    if not performance:
        mastery = TopicMastery.UNKNOWN
        reasoning = ReasoningState.CANNOT_START
        support = SupportLevel.WORKED_EXAMPLE
        transfer_readiness = TransferReadiness.NOT_READY
        difficulty = DifficultyAction.HOLD
        category = BarrierCategory.INSUFFICIENT_EVIDENCE
        description = request.explicit_confusion or "当前证据不足，暂不做具体误区判断"
        focus = "先通过一个短示例确认学生对核心概念的起点理解"
        confidence = 0.35 if request.explicit_confusion else 0.2
    elif transfer_correct and len(correct) >= 1:
        mastery = TopicMastery.TRANSFERABLE
        reasoning = ReasoningState.CAN_TRANSFER
        support = SupportLevel.INDEPENDENT
        transfer_readiness = TransferReadiness.DEMONSTRATED
        difficulty = DifficultyAction.INCREASE_HALF_STEP
        category = BarrierCategory.NONE_OBSERVED
        description = "当前证据未显示关键障碍，学生已在新情境中完成迁移"
        focus = "提供略高半级的新情境挑战，并要求说明适用边界"
        confidence = min(0.92, 0.62 + 0.06 * len(performance))
    elif transfer_failed and len(correct) >= 1:
        mastery = TopicMastery.SECURE if len(correct) >= 2 else TopicMastery.PARTIAL
        reasoning = ReasoningState.CAN_EXPLAIN if any(i.reasoning_present for i in correct) else ReasoningState.CAN_RECALL
        support = SupportLevel.MINIMAL_SCAFFOLD
        transfer_readiness = TransferReadiness.NOT_READY
        difficulty = DifficultyAction.HOLD
        category = BarrierCategory.TRANSFER_GAP
        description = "原情境中的理解已有基础，但换到新情境时尚未稳定迁移"
        focus = "对比原题与新情境中不变的规则，再进行一次近迁移练习"
        confidence = min(0.88, 0.58 + 0.05 * len(performance))
    elif self_corrected:
        mastery = TopicMastery.PARTIAL
        reasoning = ReasoningState.CAN_SELF_CORRECT
        support = SupportLevel.MINIMAL_SCAFFOLD
        transfer_readiness = TransferReadiness.NEAR_READY
        difficulty = DifficultyAction.HOLD
        category = BarrierCategory.MECHANISM_GAP
        description = "学生能够发现并修正先前错误，但关键机制仍需再稳定一次"
        focus = "让学生复述修正依据，再用一个相邻情境确认理解"
        confidence = min(0.86, 0.58 + 0.05 * len(performance))
    elif corrected_with_support:
        mastery = TopicMastery.PARTIAL
        reasoning = ReasoningState.CAN_APPLY_WITH_SCAFFOLD
        support = SupportLevel.STRONG_SCAFFOLD if len(hinted) >= 2 else SupportLevel.MINIMAL_SCAFFOLD
        transfer_readiness = TransferReadiness.NOT_READY
        difficulty = DifficultyAction.HOLD
        category = BarrierCategory.MECHANISM_GAP
        description = "学生在提示或补救后能够完成，但尚未证明可以独立解释和应用"
        focus = "逐步撤去提示，让学生独立补出关键因果步骤"
        confidence = min(0.84, 0.55 + 0.05 * len(performance))
    elif repeated_failure:
        mastery = TopicMastery.EMERGING
        reasoning = ReasoningState.CANNOT_START
        support = SupportLevel.WORKED_EXAMPLE
        transfer_readiness = TransferReadiness.NOT_READY
        difficulty = DifficultyAction.DECREASE_HALF_STEP
        category = BarrierCategory.CONCEPT_CONFUSION
        description = request.explicit_confusion or "多次作答仍未形成稳定起点，需要重新建立核心概念"
        focus = "先用完整示例建立概念，再拆成一个可完成的小步骤"
        confidence = min(0.9, 0.62 + 0.05 * len(performance))
    elif partial or failed:
        mastery = TopicMastery.PARTIAL if partial or correct else TopicMastery.EMERGING
        reasoning = ReasoningState.CAN_RECALL if partial or correct else ReasoningState.CANNOT_START
        support = SupportLevel.MINIMAL_SCAFFOLD if partial or correct else SupportLevel.STEP_BY_STEP
        transfer_readiness = TransferReadiness.NOT_READY
        difficulty = DifficultyAction.HOLD
        category = BarrierCategory.CONCEPT_CONFUSION
        description = request.explicit_confusion or "回答包含部分正确要点，但关键条件或机制仍未完整说明"
        focus = "指出已经正确的部分，再补上缺失的关键机制"
        confidence = min(0.82, 0.5 + 0.05 * len(performance))
    else:
        mastery = TopicMastery.SECURE
        reasoning = ReasoningState.CAN_EXPLAIN if any(i.reasoning_present for i in correct) else ReasoningState.CAN_RECALL
        support = SupportLevel.INDEPENDENT
        transfer_readiness = TransferReadiness.NEAR_READY
        difficulty = DifficultyAction.HOLD
        category = BarrierCategory.NONE_OBSERVED
        description = "当前范围内作答稳定，尚需用新情境确认迁移能力"
        focus = "减少重复讲解，安排一个新情境迁移任务"
        confidence = min(0.88, 0.58 + 0.06 * len(performance))

    return LearnerStateBlueprint(
        topic_mastery=mastery,
        primary_barrier=PrimaryBarrier(
            category=category,
            description=description[:220],
            evidence_refs=refs[:6],
        ),
        reasoning_state=reasoning,
        support_level=support,
        transfer_readiness=transfer_readiness,
        difficulty_action=difficulty,
        next_teaching_focus=focus,
        confidence=round(confidence, 3),
        evidence_refs=refs[:10],
    )
