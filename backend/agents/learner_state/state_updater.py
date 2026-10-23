from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .models import (
    EvidenceResult,
    LearnerStateBlueprint,
    LearnerStateRequest,
    LearnerStateSnapshot,
    SupportLevel,
    TaskType,
    TopicMastery,
    TransferReadiness,
)


_MASTERY = [
    TopicMastery.UNKNOWN,
    TopicMastery.EMERGING,
    TopicMastery.PARTIAL,
    TopicMastery.SECURE,
    TopicMastery.TRANSFERABLE,
]
_SUPPORT = [
    SupportLevel.WORKED_EXAMPLE,
    SupportLevel.STEP_BY_STEP,
    SupportLevel.STRONG_SCAFFOLD,
    SupportLevel.MINIMAL_SCAFFOLD,
    SupportLevel.INDEPENDENT,
]
_TRANSFER = [
    TransferReadiness.NOT_READY,
    TransferReadiness.NEAR_READY,
    TransferReadiness.READY,
    TransferReadiness.DEMONSTRATED,
]


def _bounded_transition(previous, proposed, order, *, allow_jump: bool = False):
    if allow_jump:
        return proposed
    old = order.index(previous)
    new = order.index(proposed)
    if new > old + 1:
        return order[old + 1]
    if new < old - 1:
        return order[old - 1]
    return proposed


def compile_snapshot(
    blueprint: LearnerStateBlueprint,
    request: LearnerStateRequest,
    *,
    source: str,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> LearnerStateSnapshot:
    previous = request.previous_topic_state
    current = list(request.current_evidence)
    transfer_success = any(
        (e.transfer_context or e.task_type == TaskType.TRANSFER) and e.result == EvidenceResult.CORRECT
        for e in current
    )
    repeated_failure = sum(1 for e in current if e.result == EvidenceResult.NEEDS_REMEDIATION) >= 2 or any(
        e.result == EvidenceResult.NEEDS_REMEDIATION and e.attempts >= 2 for e in current
    )

    mastery = blueprint.topic_mastery
    support = blueprint.support_level
    transfer = blueprint.transfer_readiness
    revision = 1
    if previous and previous.topic_key == request.topic_key:
        mastery = _bounded_transition(
            previous.topic_mastery,
            mastery,
            _MASTERY,
            allow_jump=transfer_success or repeated_failure,
        )
        support = _bounded_transition(
            previous.support_level,
            support,
            _SUPPORT,
            allow_jump=repeated_failure,
        )
        transfer = _bounded_transition(
            previous.transfer_readiness,
            transfer,
            _TRANSFER,
            allow_jump=transfer_success,
        )
        revision = previous.revision + 1

    updated_at = now or datetime.now(timezone.utc).isoformat()
    identity = f"{request.student_id}|{request.topic_key}|{revision}|{updated_at}"
    state_id = "lst_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]

    return LearnerStateSnapshot(
        state_id=state_id,
        student_id=request.student_id,
        topic_key=request.topic_key,
        topic_name=request.topic_name,
        topic_mastery=mastery,
        primary_barrier=blueprint.primary_barrier,
        reasoning_state=blueprint.reasoning_state,
        support_level=support,
        transfer_readiness=transfer,
        difficulty_action=blueprint.difficulty_action,
        next_teaching_focus=blueprint.next_teaching_focus,
        confidence=blueprint.confidence,
        evidence_refs=blueprint.evidence_refs,
        revision=revision,
        source="model" if source == "model" else "fallback",
        warnings=list(dict.fromkeys(warnings or []))[:12],
        updated_at=updated_at,
    )
