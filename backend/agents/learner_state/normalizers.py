from __future__ import annotations

from copy import deepcopy

from .models import (
    BarrierCategory,
    DifficultyAction,
    EvidenceResult,
    HintLevel,
    LearnerStateBlueprint,
    LearnerStateRequest,
    ReasoningState,
    SupportLevel,
    TaskType,
    TopicMastery,
    TransferReadiness,
)


_MASTERY_ORDER = [
    TopicMastery.UNKNOWN,
    TopicMastery.EMERGING,
    TopicMastery.PARTIAL,
    TopicMastery.SECURE,
    TopicMastery.TRANSFERABLE,
]
_REASONING_ORDER = [
    ReasoningState.CANNOT_START,
    ReasoningState.CAN_RECALL,
    ReasoningState.CAN_APPLY_WITH_SCAFFOLD,
    ReasoningState.CAN_EXPLAIN,
    ReasoningState.CAN_SELF_CORRECT,
    ReasoningState.CAN_TRANSFER,
]
_SUPPORT_ORDER = [
    SupportLevel.WORKED_EXAMPLE,
    SupportLevel.STEP_BY_STEP,
    SupportLevel.STRONG_SCAFFOLD,
    SupportLevel.MINIMAL_SCAFFOLD,
    SupportLevel.INDEPENDENT,
]


def _at_least(value, floor, order):
    return floor if order.index(value) < order.index(floor) else value


def evidence_confidence_cap(request: LearnerStateRequest) -> float:
    current = [e for e in request.current_evidence if e.result != EvidenceResult.UNKNOWN]
    recent = [] if request.context_mode.value == "current_item_only" else [
        e for e in request.recent_related_evidence if e.result != EvidenceResult.UNKNOWN
    ]
    transfer = any(e.transfer_context or e.task_type == TaskType.TRANSFER for e in current)
    if not current:
        return 0.45 if request.explicit_confusion else 0.3
    cap = 0.62 + min(0.22, 0.05 * len(current)) + min(0.08, 0.02 * len(recent))
    if transfer:
        cap += 0.05
    return min(0.95, cap)


def _support_from_hint(level: HintLevel) -> SupportLevel:
    return {
        HintLevel.NONE: SupportLevel.MINIMAL_SCAFFOLD,
        HintLevel.MINIMAL: SupportLevel.MINIMAL_SCAFFOLD,
        HintLevel.STRONG: SupportLevel.STRONG_SCAFFOLD,
        HintLevel.STEP_BY_STEP: SupportLevel.STEP_BY_STEP,
        HintLevel.WORKED_EXAMPLE: SupportLevel.WORKED_EXAMPLE,
    }[level]


def normalize_blueprint(
    blueprint: LearnerStateBlueprint,
    request: LearnerStateRequest,
) -> tuple[LearnerStateBlueprint, list[str]]:
    value = deepcopy(blueprint)
    warnings: list[str] = []
    allowed = request.allowed_evidence_refs()

    def clean_refs(refs: list[str]) -> list[str]:
        seen: set[str] = set()
        clean: list[str] = []
        for ref in refs:
            if ref in allowed and ref not in seen:
                clean.append(ref)
                seen.add(ref)
        return clean

    cleaned = clean_refs(value.evidence_refs)
    barrier_cleaned = clean_refs(value.primary_barrier.evidence_refs)
    if cleaned != value.evidence_refs or barrier_cleaned != value.primary_barrier.evidence_refs:
        warnings.append("learner_state_evidence_refs_normalized")
    value.evidence_refs = cleaned
    value.primary_barrier.evidence_refs = barrier_cleaned

    cap = evidence_confidence_cap(request)
    if value.confidence > cap:
        value.confidence = round(cap, 3)
        warnings.append("learner_state_confidence_capped")

    current = list(request.current_evidence)
    all_evidence = current + ([] if request.context_mode.value == "current_item_only" else list(request.recent_related_evidence))
    has_transfer_success = any(
        (e.transfer_context or e.task_type == TaskType.TRANSFER) and e.result == EvidenceResult.CORRECT
        for e in current
    )
    has_independent_transfer_success = any(
        (e.transfer_context or e.task_type == TaskType.TRANSFER)
        and e.result == EvidenceResult.CORRECT
        and e.hint_level_used == HintLevel.NONE
        and e.reasoning_present
        for e in current
    )
    has_transfer_failure = any(
        (e.transfer_context or e.task_type == TaskType.TRANSFER)
        and e.result in {EvidenceResult.PARTIALLY_CORRECT, EvidenceResult.NEEDS_REMEDIATION}
        for e in current
    )
    has_transfer_evidence = any(e.transfer_context or e.task_type == TaskType.TRANSFER for e in current)
    has_self_correction = any(
        e.self_corrected
        and e.result == EvidenceResult.CORRECT
        and e.hint_level_used == HintLevel.NONE
        for e in current
    )
    supported_corrections = [
        e for e in current
        if e.corrected_with_support and e.result == EvidenceResult.CORRECT
    ]
    has_supported_correction = bool(supported_corrections)
    has_independent_base_success = any(
        not (e.transfer_context or e.task_type == TaskType.TRANSFER)
        and e.result == EvidenceResult.CORRECT
        and e.hint_level_used == HintLevel.NONE
        and e.reasoning_present
        for e in current
    )
    has_performance = any(e.result != EvidenceResult.UNKNOWN for e in all_evidence)
    repeated_failure = sum(1 for e in current if e.result == EvidenceResult.NEEDS_REMEDIATION) >= 2 or any(
        e.result == EvidenceResult.NEEDS_REMEDIATION and e.attempts >= 2 for e in current
    )
    has_non_transfer_failure = any(
        not (e.transfer_context or e.task_type == TaskType.TRANSFER)
        and e.result in {EvidenceResult.PARTIALLY_CORRECT, EvidenceResult.NEEDS_REMEDIATION}
        for e in current
    )
    has_distinction_failure = any(
        e.task_type == TaskType.DISTINCTION
        and e.result in {EvidenceResult.PARTIALLY_CORRECT, EvidenceResult.NEEDS_REMEDIATION}
        for e in current
    )

    if not has_performance:
        changed = (
            value.topic_mastery != TopicMastery.UNKNOWN
            or value.reasoning_state != ReasoningState.CANNOT_START
            or value.support_level != SupportLevel.WORKED_EXAMPLE
            or value.transfer_readiness != TransferReadiness.NOT_READY
            or value.difficulty_action != DifficultyAction.HOLD
        )
        value.topic_mastery = TopicMastery.UNKNOWN
        value.reasoning_state = ReasoningState.CANNOT_START
        value.support_level = SupportLevel.WORKED_EXAMPLE
        value.transfer_readiness = TransferReadiness.NOT_READY
        value.difficulty_action = DifficultyAction.HOLD
        value.primary_barrier.category = BarrierCategory.INSUFFICIENT_EVIDENCE
        if changed:
            warnings.append("learner_state_insufficient_evidence_normalized")

    # Evidence-backed calibration floors. These do not infer ability; they prevent the model
    # from ignoring direct, current performance evidence that has deterministic meaning.
    if has_independent_base_success and value.topic_mastery in {TopicMastery.UNKNOWN, TopicMastery.EMERGING}:
        value.topic_mastery = TopicMastery.PARTIAL
        warnings.append("learner_state_independent_success_floor_applied")

    if has_self_correction:
        changed = False
        new_mastery = _at_least(value.topic_mastery, TopicMastery.PARTIAL, _MASTERY_ORDER)
        if new_mastery != value.topic_mastery:
            value.topic_mastery = new_mastery
            changed = True
        if value.reasoning_state != ReasoningState.CAN_SELF_CORRECT:
            value.reasoning_state = ReasoningState.CAN_SELF_CORRECT
            changed = True
        new_support = _at_least(value.support_level, SupportLevel.MINIMAL_SCAFFOLD, _SUPPORT_ORDER)
        if new_support != value.support_level:
            value.support_level = new_support
            changed = True
        if changed:
            warnings.append("learner_state_self_correction_calibrated")

    if has_supported_correction:
        changed = False
        new_mastery = _at_least(value.topic_mastery, TopicMastery.PARTIAL, _MASTERY_ORDER)
        if new_mastery != value.topic_mastery:
            value.topic_mastery = new_mastery
            changed = True
        if value.reasoning_state in {ReasoningState.CANNOT_START, ReasoningState.CAN_RECALL}:
            value.reasoning_state = ReasoningState.CAN_APPLY_WITH_SCAFFOLD
            changed = True
        strongest_needed = max(
            (_support_from_hint(e.hint_level_used) for e in supported_corrections),
            key=_SUPPORT_ORDER.index,
        )
        # Supported success cannot justify independence, but it should not be treated as
        # inability to start when the student completed the task with a known scaffold.
        if value.support_level == SupportLevel.INDEPENDENT or _SUPPORT_ORDER.index(value.support_level) < _SUPPORT_ORDER.index(strongest_needed):
            value.support_level = strongest_needed
            changed = True
        if changed:
            warnings.append("learner_state_supported_success_calibrated")

    # Barrier categories are evidence-gated. A transfer gap cannot be inferred from a
    # retry, hint use, or remediation success when no current transfer task exists.
    if value.primary_barrier.category == BarrierCategory.TRANSFER_GAP and not has_transfer_evidence:
        if has_distinction_failure:
            value.primary_barrier.category = BarrierCategory.CONCEPT_CONFUSION
        elif has_supported_correction or has_non_transfer_failure:
            value.primary_barrier.category = BarrierCategory.MECHANISM_GAP
        else:
            value.primary_barrier.category = (
                BarrierCategory.INSUFFICIENT_EVIDENCE
                if not has_performance
                else BarrierCategory.MECHANISM_GAP
            )
        value.primary_barrier.description = (
            "当前证据只显示学生在当前任务中仍需提示或补救，尚无迁移任务证据"
        )
        value.next_teaching_focus = (
            "围绕当前任务补足关键机制说明，并在相近任务中逐步撤去提示"
        )
        warnings.append("learner_state_transfer_barrier_evidence_guard_applied")

    if has_independent_base_success and has_transfer_failure:
        changed = False
        new_mastery = _at_least(value.topic_mastery, TopicMastery.PARTIAL, _MASTERY_ORDER)
        if new_mastery != value.topic_mastery:
            value.topic_mastery = new_mastery
            changed = True
        if value.transfer_readiness != TransferReadiness.NOT_READY:
            value.transfer_readiness = TransferReadiness.NOT_READY
            changed = True
        if value.primary_barrier.category == BarrierCategory.NONE_OBSERVED:
            value.primary_barrier.category = BarrierCategory.TRANSFER_GAP
            changed = True
        if value.difficulty_action != DifficultyAction.HOLD:
            value.difficulty_action = DifficultyAction.HOLD
            changed = True
        if changed:
            warnings.append("learner_state_transfer_gap_calibrated")

    if has_independent_transfer_success:
        changed = False
        if value.topic_mastery != TopicMastery.TRANSFERABLE:
            value.topic_mastery = TopicMastery.TRANSFERABLE
            changed = True
        if value.reasoning_state != ReasoningState.CAN_TRANSFER:
            value.reasoning_state = ReasoningState.CAN_TRANSFER
            changed = True
        if value.support_level != SupportLevel.INDEPENDENT:
            value.support_level = SupportLevel.INDEPENDENT
            changed = True
        if value.transfer_readiness != TransferReadiness.DEMONSTRATED:
            value.transfer_readiness = TransferReadiness.DEMONSTRATED
            changed = True
        if value.primary_barrier.category != BarrierCategory.NONE_OBSERVED:
            value.primary_barrier.category = BarrierCategory.NONE_OBSERVED
            changed = True
        if changed:
            warnings.append("learner_state_transfer_success_calibrated")

    if value.topic_mastery == TopicMastery.TRANSFERABLE and not has_transfer_success:
        value.topic_mastery = TopicMastery.SECURE if has_performance else TopicMastery.UNKNOWN
        warnings.append("learner_state_transfer_claim_downgraded")
    if value.transfer_readiness == TransferReadiness.DEMONSTRATED and not has_transfer_success:
        value.transfer_readiness = TransferReadiness.READY if has_transfer_evidence else TransferReadiness.NEAR_READY
        warnings.append("learner_state_transfer_readiness_downgraded")
    if value.reasoning_state == ReasoningState.CAN_TRANSFER and not has_transfer_success:
        value.reasoning_state = ReasoningState.CAN_EXPLAIN if has_performance else ReasoningState.CANNOT_START
        warnings.append("learner_state_reasoning_transfer_downgraded")
    if value.reasoning_state == ReasoningState.CAN_SELF_CORRECT and not has_self_correction:
        value.reasoning_state = ReasoningState.CAN_APPLY_WITH_SCAFFOLD if has_performance else ReasoningState.CANNOT_START
        warnings.append("learner_state_self_correction_downgraded")

    if value.support_level == SupportLevel.INDEPENDENT and value.topic_mastery in {
        TopicMastery.UNKNOWN,
        TopicMastery.EMERGING,
    }:
        value.support_level = SupportLevel.WORKED_EXAMPLE if value.topic_mastery == TopicMastery.UNKNOWN else SupportLevel.STEP_BY_STEP
        warnings.append("learner_state_support_normalized")

    if value.difficulty_action == DifficultyAction.INCREASE_HALF_STEP and (
        value.topic_mastery not in {TopicMastery.SECURE, TopicMastery.TRANSFERABLE}
        or value.confidence < 0.65
    ):
        value.difficulty_action = DifficultyAction.HOLD
        warnings.append("learner_state_difficulty_action_normalized")
    if repeated_failure and value.difficulty_action == DifficultyAction.INCREASE_HALF_STEP:
        value.difficulty_action = DifficultyAction.HOLD
        warnings.append("learner_state_failure_guard_applied")

    if value.primary_barrier.category == BarrierCategory.NONE_OBSERVED and not has_performance:
        value.primary_barrier.category = BarrierCategory.INSUFFICIENT_EVIDENCE
        warnings.append("learner_state_barrier_normalized")

    return value, list(dict.fromkeys(warnings))
