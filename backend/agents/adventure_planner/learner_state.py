from __future__ import annotations

from .models import LearnerStateSnapshot, PlannerRequest


_STUCK_KINDS = {"incorrect_answer", "hint_used", "remediation_needed", "explicit_confusion"}
_POSITIVE_KINDS = {"correct_answer", "self_explanation", "transfer_success", "course_level"}


def resolve_learner_state(request: PlannerRequest) -> LearnerStateSnapshot:
    """Return an explicit state or a conservative topic-scoped temporary state.

    The temporary state is derived only from current planning evidence. It is not a
    durable learner profile and must never be exposed in the student-facing preview.
    """

    if request.learner_state is not None:
        return request.learner_state.model_copy(deep=True)

    positive = 0.0
    stuck = 0.0
    evidence_refs: list[str] = []
    misconception = request.explicit_confusion

    for index, item in enumerate(request.learning_evidence):
        weight = max(0.0, item.confidence)
        if item.kind in _POSITIVE_KINDS:
            positive += weight * max(0.25, item.difficulty_signal + 0.5)
        if item.kind in _STUCK_KINDS:
            stuck += weight * max(0.25, 0.5 - item.difficulty_signal)
        evidence_refs.append(f"learning_evidence:{index}:{item.kind}")
        if item.kind == "explicit_confusion" and item.summary:
            misconception = item.summary

    denominator = max(1.0, positive + stuck)
    mastery = min(0.9, max(0.1, 0.5 + (positive - stuck) / (2.5 * denominator)))
    transfer = min(0.9, max(0.1, mastery + (0.12 if any(
        item.kind == "transfer_success" for item in request.learning_evidence
    ) else -0.08)))

    if stuck >= 1.0:
        reasoning_state = "stuck"
        support_level = "scaffold"
    elif positive >= 1.2 and mastery >= 0.65:
        reasoning_state = "refining"
        support_level = "independent"
    else:
        reasoning_state = "explore"
        support_level = "hint"

    confidence = min(0.9, 0.25 + 0.1 * len(request.learning_evidence))
    return LearnerStateSnapshot(
        topic_mastery=round(mastery, 3),
        misconception=misconception,
        reasoning_state=reasoning_state,
        support_level=support_level,
        transfer_readiness=round(transfer, 3),
        confidence=round(confidence, 3),
        evidence_refs=evidence_refs[:12],
        updated_at=None,
    )
