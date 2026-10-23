from __future__ import annotations

from .models import LearnerStateSnapshot, ReasoningState, SupportLevel, TopicMastery, TransferReadiness


_MASTERY_SCORE = {
    TopicMastery.UNKNOWN: 0.2,
    TopicMastery.EMERGING: 0.35,
    TopicMastery.PARTIAL: 0.55,
    TopicMastery.SECURE: 0.75,
    TopicMastery.TRANSFERABLE: 0.9,
}
_REASONING = {
    ReasoningState.CANNOT_START: "stuck",
    ReasoningState.CAN_RECALL: "explore",
    ReasoningState.CAN_APPLY_WITH_SCAFFOLD: "explore",
    ReasoningState.CAN_EXPLAIN: "refining",
    ReasoningState.CAN_SELF_CORRECT: "refining",
    ReasoningState.CAN_TRANSFER: "confirmed",
}
_SUPPORT = {
    SupportLevel.WORKED_EXAMPLE: "full",
    SupportLevel.STEP_BY_STEP: "full",
    SupportLevel.STRONG_SCAFFOLD: "scaffold",
    SupportLevel.MINIMAL_SCAFFOLD: "hint",
    SupportLevel.INDEPENDENT: "independent",
}
_TRANSFER_SCORE = {
    TransferReadiness.NOT_READY: 0.2,
    TransferReadiness.NEAR_READY: 0.45,
    TransferReadiness.READY: 0.7,
    TransferReadiness.DEMONSTRATED: 0.9,
}


def to_adventure_planner_learner_state(snapshot: LearnerStateSnapshot) -> dict:
    """Map the v0.1 state to AGENT-ADVENTURE-01 v0.3.0's existing snapshot contract."""
    misconception = None
    if snapshot.primary_barrier.category.value not in {"none_observed", "insufficient_evidence"}:
        misconception = snapshot.primary_barrier.description
    return {
        "topic_mastery": _MASTERY_SCORE[snapshot.topic_mastery],
        "misconception": misconception,
        "reasoning_state": _REASONING[snapshot.reasoning_state],
        "support_level": _SUPPORT[snapshot.support_level],
        "transfer_readiness": _TRANSFER_SCORE[snapshot.transfer_readiness],
        "confidence": snapshot.confidence,
        "evidence_refs": snapshot.evidence_refs[:12],
        "updated_at": snapshot.updated_at,
    }


def private_storage_payload(snapshot: LearnerStateSnapshot) -> dict:
    """Server-side payload for WorkspaceAgentTask.slots['learner_state']; never return publicly."""
    return snapshot.model_dump(mode="json")
