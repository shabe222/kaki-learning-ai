from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TopicMastery(str, Enum):
    UNKNOWN = "unknown"
    EMERGING = "emerging"
    PARTIAL = "partial"
    SECURE = "secure"
    TRANSFERABLE = "transferable"


class ReasoningState(str, Enum):
    CANNOT_START = "cannot_start"
    CAN_RECALL = "can_recall"
    CAN_APPLY_WITH_SCAFFOLD = "can_apply_with_scaffold"
    CAN_EXPLAIN = "can_explain"
    CAN_SELF_CORRECT = "can_self_correct"
    CAN_TRANSFER = "can_transfer"


class SupportLevel(str, Enum):
    WORKED_EXAMPLE = "worked_example"
    STEP_BY_STEP = "step_by_step"
    STRONG_SCAFFOLD = "strong_scaffold"
    MINIMAL_SCAFFOLD = "minimal_scaffold"
    INDEPENDENT = "independent"


class TransferReadiness(str, Enum):
    NOT_READY = "not_ready"
    NEAR_READY = "near_ready"
    READY = "ready"
    DEMONSTRATED = "demonstrated"


class DifficultyAction(str, Enum):
    DECREASE_HALF_STEP = "decrease_half_step"
    HOLD = "hold"
    INCREASE_HALF_STEP = "increase_half_step"


class BarrierCategory(str, Enum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONCEPT_CONFUSION = "concept_confusion"
    MECHANISM_GAP = "mechanism_gap"
    CONDITION_OMISSION = "condition_omission"
    PROCEDURE_ERROR = "procedure_error"
    REPRESENTATION_GAP = "representation_gap"
    ROTE_APPLICATION = "rote_application"
    TRANSFER_GAP = "transfer_gap"
    NON_KNOWLEDGE_SLIP = "non_knowledge_slip"
    NONE_OBSERVED = "none_observed"


class EvidenceType(str, Enum):
    EXPLICIT_CONFUSION = "explicit_confusion"
    ANSWER = "answer"
    HINT_USE = "hint_use"
    REMEDIATION_RESULT = "remediation_result"
    TRANSFER_RESULT = "transfer_result"
    SELF_CORRECTION = "self_correction"
    HISTORICAL_SUMMARY = "historical_summary"


class TaskType(str, Enum):
    UNDERSTANDING = "understanding"
    DISTINCTION = "distinction"
    TRANSFER = "transfer"
    REMEDIATION = "remediation"
    UNKNOWN = "unknown"


class EvidenceResult(str, Enum):
    CORRECT = "correct"
    PARTIALLY_CORRECT = "partially_correct"
    NEEDS_REMEDIATION = "needs_remediation"
    UNKNOWN = "unknown"


class HintLevel(str, Enum):
    NONE = "none"
    MINIMAL = "minimal"
    STRONG = "strong"
    STEP_BY_STEP = "step_by_step"
    WORKED_EXAMPLE = "worked_example"


class ContextMode(str, Enum):
    RELEVANT_RECENT = "relevant_recent"
    CURRENT_ITEM_ONLY = "current_item_only"


class LearningEvidence(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=80)
    evidence_type: EvidenceType
    task_type: TaskType = TaskType.UNKNOWN
    response_summary: str | None = Field(default=None, max_length=180)
    result: EvidenceResult = EvidenceResult.UNKNOWN
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    attempts: int = Field(default=1, ge=1, le=20)
    reasoning_present: bool = False
    hint_level_used: HintLevel = HintLevel.NONE
    self_corrected: bool = False
    corrected_with_support: bool = False
    transfer_context: bool = False
    recency_rank: int = Field(default=0, ge=0, le=100)
    observed_at: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def validate_correction_flags(self):
        if self.self_corrected and self.corrected_with_support:
            raise ValueError("self_corrected and corrected_with_support cannot both be true")
        if self.self_corrected and self.hint_level_used != HintLevel.NONE:
            raise ValueError("self_corrected requires hint_level_used=none")
        if self.task_type == TaskType.TRANSFER:
            self.transfer_context = True
        return self


class LearnerStateRequest(StrictModel):
    request_id: str | None = Field(default=None, max_length=80)
    student_id: str = Field(min_length=1, max_length=80)
    topic_key: str = Field(min_length=2, max_length=160)
    topic_name: str = Field(min_length=2, max_length=160)
    explicit_confusion: str | None = Field(default=None, max_length=600)
    current_evidence: list[LearningEvidence] = Field(default_factory=list, max_length=12)
    recent_related_evidence: list[LearningEvidence] = Field(default_factory=list, max_length=8)
    previous_topic_state: "LearnerStateSnapshot | None" = None
    current_material_summary: str | None = Field(default=None, max_length=800)
    context_mode: ContextMode = ContextMode.RELEVANT_RECENT
    locale: str = Field(default="zh-CN", max_length=12)

    @model_validator(mode="after")
    def enforce_topic_and_context_isolation(self):
        if self.previous_topic_state and self.previous_topic_state.topic_key != self.topic_key:
            raise ValueError("previous_topic_state.topic_key must match request.topic_key")
        if self.context_mode == ContextMode.CURRENT_ITEM_ONLY and self.recent_related_evidence:
            raise ValueError("recent_related_evidence must be empty in current_item_only mode")
        return self

    def allowed_evidence_refs(self) -> set[str]:
        refs = {item.evidence_id for item in self.current_evidence}
        if self.context_mode != ContextMode.CURRENT_ITEM_ONLY:
            refs.update(item.evidence_id for item in self.recent_related_evidence)
        if self.explicit_confusion:
            refs.add("explicit_confusion")
        return refs


class PrimaryBarrier(StrictModel):
    category: BarrierCategory
    description: str = Field(min_length=4, max_length=220)
    evidence_refs: list[str] = Field(default_factory=list, max_length=6)


class LearnerStateBlueprint(StrictModel):
    topic_mastery: TopicMastery
    primary_barrier: PrimaryBarrier
    reasoning_state: ReasoningState
    support_level: SupportLevel
    transfer_readiness: TransferReadiness
    difficulty_action: DifficultyAction
    next_teaching_focus: str = Field(min_length=6, max_length=180)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list, max_length=10)


class LearnerStateSnapshot(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    state_id: str = Field(min_length=8, max_length=80)
    student_id: str = Field(min_length=1, max_length=80)
    topic_key: str = Field(min_length=2, max_length=160)
    topic_name: str = Field(min_length=2, max_length=160)
    topic_mastery: TopicMastery
    primary_barrier: PrimaryBarrier
    reasoning_state: ReasoningState
    support_level: SupportLevel
    transfer_readiness: TransferReadiness
    difficulty_action: DifficultyAction
    next_teaching_focus: str = Field(min_length=6, max_length=180)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list, max_length=10)
    revision: int = Field(default=1, ge=1, le=999)
    source: Literal["model", "fallback"] = "model"
    warnings: list[str] = Field(default_factory=list, max_length=12)
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AnalyzerMetadata(StrictModel):
    generation_mode: Literal["model", "fallback"]
    fallback_used: bool
    warnings: list[str] = Field(default_factory=list, max_length=20)
    raw_attempts: int = Field(default=0, ge=0, le=2)
    finish_reason: str | None = Field(default=None, max_length=40)
    configured_model_id: str | None = Field(default=None, max_length=100)
    provider_returned_model_id: str | None = Field(default=None, max_length=100)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    content_length: int | None = Field(default=None, ge=0)


class LearnerStateResult(StrictModel):
    status: Literal["ok"] = "ok"
    state: LearnerStateSnapshot
    metadata: AnalyzerMetadata


LearnerStateRequest.model_rebuild()
