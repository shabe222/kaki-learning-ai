from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Domain(str, Enum):
    MATH = "math"
    PROGRAMMING = "programming"
    LANGUAGE = "language"
    SCIENCE = "science"
    HUMANITIES = "humanities"
    OPEN = "open"


class DifficultyLevel(str, Enum):
    FOUNDATIONAL = "foundational"
    STANDARD = "standard"
    STANDARD_PLUS = "standard_plus"
    CHALLENGING = "challenging"
    ADVANCED = "advanced"


class GoalKind(str, Enum):
    UNDERSTAND = "understand"
    DISTINGUISH = "distinguish"
    TRANSFER = "transfer"


class QuestionKind(str, Enum):
    """Legacy rendering shape supported by the existing eight-state adventure."""

    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"


class InteractionKind(str, Enum):
    EXPLAIN = "explain"
    ORDERING = "ordering"
    STEP_COMPLETION = "step_completion"
    COMPARE = "compare"
    FIND_ERROR = "find_error"
    COUNTEREXAMPLE = "counterexample"
    APPLICATION = "application"
    CODE_TASK = "code_task"
    REWRITE = "rewrite"
    OPEN_RESPONSE = "open_response"
    MULTIPLE_CHOICE = "multiple_choice"


class ContextMode(str, Enum):
    RELEVANT_RECENT = "relevant_recent"
    CURRENT_ITEM_ONLY = "current_item_only"


class ClarificationReason(str, Enum):
    CONFLICTING_TOPICS = "conflicting_topics"
    AMBIGUOUS_TERM = "ambiguous_term"
    GOAL_UNCLEAR = "goal_unclear"
    SOURCE_CONFLICT = "source_conflict"


class PlannerWarningCode(str, Enum):
    PROVIDER_UNCONFIGURED = "planner_provider_unconfigured"
    TIMEOUT = "planner_timeout"
    INVALID_JSON = "planner_invalid_json"
    SCHEMA_INVALID = "planner_schema_invalid"
    SEMANTIC_VALIDATION_FAILED = "planner_semantic_validation_failed"
    REPAIR_FAILED = "planner_repair_failed"
    PROVIDER_AUTH_FAILED = "planner_provider_auth_failed"
    PROVIDER_RATE_LIMITED = "planner_provider_rate_limited"
    PROVIDER_NETWORK_ERROR = "planner_provider_network_error"
    HINT_NORMALIZED = "planner_hint_normalized"
    MICROCOPY_NORMALIZED = "planner_microcopy_normalized"
    OUTPUT_TRUNCATED = "planner_output_truncated"
    EMPTY_OUTPUT = "planner_empty_output"
    INTERNAL_ERROR = "planner_internal_error"


class ScoreBandKind(str, Enum):
    CORRECT = "correct"
    PARTIAL = "partial"
    REMEDIATION = "remediation"


class ConversationTurn(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1800)
    related_to_topic: bool = True
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    recency_rank: int = Field(default=0, ge=0, le=100)
    turn_id: str | None = Field(default=None, max_length=100)


class SelectedSource(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    source_type: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=120)
    excerpt: str = Field(min_length=1, max_length=2400)
    authoritative: bool = True


class SourceConflict(StrictModel):
    conflict_id: str = Field(min_length=1, max_length=100)
    source_ids: list[str] = Field(min_length=2, max_length=4)
    issue: str = Field(min_length=6, max_length=300)
    resolved: bool = False


class ConfirmedFact(StrictModel):
    fact_id: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=4, max_length=400)
    source_ids: list[str] = Field(default_factory=list, max_length=4)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    locked: bool = True


class LearningEvidence(StrictModel):
    kind: Literal[
        "correct_answer",
        "incorrect_answer",
        "self_explanation",
        "transfer_success",
        "hint_used",
        "remediation_needed",
        "course_level",
        "user_preference",
        "explicit_confusion",
    ]
    summary: str = Field(min_length=1, max_length=500)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    difficulty_signal: float = Field(default=0.0, ge=-2.0, le=2.0)
    recency_rank: int = Field(default=0, ge=0, le=100)


class LearnerStateSnapshot(StrictModel):
    """Optional, topic-scoped learning state consumed only inside planning."""

    topic_mastery: float = Field(default=0.5, ge=0.0, le=1.0)
    misconception: str | None = Field(default=None, max_length=300)
    reasoning_state: Literal["explore", "stuck", "refining", "confirmed"] = "explore"
    support_level: Literal["hint", "scaffold", "full", "independent"] = "hint"
    transfer_readiness: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)
    updated_at: str | None = Field(default=None, max_length=40)


class PlannerRequest(StrictModel):
    request_id: str | None = Field(default=None, max_length=80)
    topic_hint: str = Field(min_length=2, max_length=120)
    explicit_confusion: str | None = Field(default=None, max_length=600)
    focus_adjustment: str | None = Field(default=None, max_length=500)
    recent_turns: list[ConversationTurn] = Field(default_factory=list, max_length=12)
    older_context_summary: str | None = Field(default=None, max_length=1000)
    context_mode: ContextMode = ContextMode.RELEVANT_RECENT
    selected_sources: list[SelectedSource] = Field(default_factory=list, max_length=8)
    source_conflicts: list[SourceConflict] = Field(default_factory=list, max_length=3)
    confirmed_facts: list[ConfirmedFact] = Field(default_factory=list, max_length=12)
    learning_evidence: list[LearningEvidence] = Field(default_factory=list, max_length=12)
    learner_state: LearnerStateSnapshot | None = None
    previous_difficulty: DifficultyLevel | None = None
    draft_revision: int = Field(default=1, ge=1, le=3)
    adjustment_count: int = Field(default=0, ge=0, le=2)
    clarification_count: int = Field(default=0, ge=0, le=1)
    locale: str = Field(default="zh-CN", max_length=12)

    @model_validator(mode="after")
    def validate_revision(self):
        if self.adjustment_count > 0 and not self.focus_adjustment:
            raise ValueError("focus_adjustment is required when adjustment_count is greater than zero")
        if self.draft_revision != self.adjustment_count + 1:
            raise ValueError("draft_revision must equal adjustment_count + 1")
        return self


class PreviewGoal(StrictModel):
    kind: GoalKind
    text: str = Field(min_length=4, max_length=80)


class PlanPreview(StrictModel):
    theme: str = Field(min_length=2, max_length=64)
    goals: list[PreviewGoal] = Field(min_length=3, max_length=3)


class Clarification(StrictModel):
    reason: ClarificationReason
    question: str = Field(min_length=4, max_length=100)
    options: list[str] = Field(min_length=2, max_length=2)


class Prerequisite(StrictModel):
    name: str = Field(min_length=1, max_length=60)
    why_needed: str = Field(min_length=4, max_length=140)
    required: bool = True


class CoreConcept(StrictModel):
    name: str = Field(min_length=1, max_length=60)
    explanation: str = Field(min_length=8, max_length=300)
    applicability: str = Field(min_length=4, max_length=180)


class ConceptRelationship(StrictModel):
    from_concept: str = Field(min_length=1, max_length=60)
    relation: str = Field(min_length=1, max_length=80)
    to_concept: str = Field(min_length=1, max_length=60)


class Misconception(StrictModel):
    mistaken_claim: str = Field(min_length=4, max_length=180)
    correction: str = Field(min_length=8, max_length=260)
    diagnostic_cue: str = Field(min_length=4, max_length=180)


class WorkedExample(StrictModel):
    scenario: str = Field(min_length=6, max_length=260)
    steps: list[str] = Field(min_length=2, max_length=6)
    conclusion: str = Field(min_length=4, max_length=180)


class RubricBand(StrictModel):
    band: ScoreBandKind
    criteria: list[str] = Field(min_length=1, max_length=4)
    feedback_focus: str = Field(min_length=4, max_length=180)


class QuestionRubric(StrictModel):
    bands: list[RubricBand] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_band_order(self):
        expected = [ScoreBandKind.CORRECT, ScoreBandKind.PARTIAL, ScoreBandKind.REMEDIATION]
        if [item.band for item in self.bands] != expected:
            raise ValueError("rubric bands must be ordered correct, partial, remediation")
        return self


class ConfirmationTask(StrictModel):
    question: str = Field(min_length=6, max_length=220)
    accepted_groups: list[list[str]] = Field(min_length=1, max_length=4)
    academic_rationale: str = Field(min_length=6, max_length=220)


class RemediationLadder(StrictModel):
    first_attempt_hint: str = Field(min_length=4, max_length=180)
    second_attempt_explanation: str = Field(min_length=8, max_length=300)
    second_attempt_scaffold: list[str] = Field(min_length=2, max_length=4)
    third_attempt_full_solution: str = Field(min_length=12, max_length=600)
    third_attempt_confirmation: ConfirmationTask


class Question(StrictModel):
    kind: QuestionKind
    interaction_kind: InteractionKind | None = None
    question: str = Field(min_length=6, max_length=300)
    hint: str = Field(min_length=4, max_length=180)
    options: list[str] | None = Field(default=None, min_length=3, max_length=5)
    correct_option: int | None = Field(default=None, ge=0, le=4)
    accepted_groups: list[list[str]] | None = Field(default=None, min_length=1, max_length=4)
    academic_rationale: str = Field(min_length=6, max_length=260)
    verification_method: str | None = Field(default=None, max_length=260)
    answer_boundary: str | None = Field(default=None, max_length=260)
    rubric: QuestionRubric | None = None
    remediation: RemediationLadder | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.interaction_kind is None:
            self.interaction_kind = (
                InteractionKind.MULTIPLE_CHOICE
                if self.kind == QuestionKind.MULTIPLE_CHOICE
                else InteractionKind.EXPLAIN
            )
        if self.kind == QuestionKind.MULTIPLE_CHOICE:
            if self.options is None or self.correct_option is None:
                raise ValueError("multiple_choice requires options and correct_option")
            if self.correct_option >= len(self.options):
                raise ValueError("correct_option is outside options")
            if self.accepted_groups is not None:
                raise ValueError("multiple_choice must not define accepted_groups")
        else:
            if not self.accepted_groups:
                raise ValueError("short_answer requires accepted_groups")
            if self.options is not None or self.correct_option is not None:
                raise ValueError("short_answer must not define options/correct_option")
        return self


class BossStage(StrictModel):
    kind: GoalKind
    label: Literal["解释", "辨析", "迁移"]
    task: Question


class ChallengeSet(StrictModel):
    understanding_trial: Question
    practice_trial: Question
    remedy: Question
    understanding_interleave: Question
    practice_interleave: Question
    boss: list[BossStage] = Field(min_length=3, max_length=3)


class KnowledgePlan(StrictModel):
    topic: str = Field(min_length=2, max_length=120)
    specific_confusion: str = Field(min_length=2, max_length=500)
    domain: Domain
    difficulty: DifficultyLevel
    difficulty_reason: str = Field(min_length=6, max_length=240)
    preview: PlanPreview
    source_grounding: list[str] = Field(default_factory=list, max_length=8)
    source_note: str = Field(min_length=4, max_length=180)
    source_conflict_note: str | None = Field(default=None, max_length=240)
    confirmed_fact_ids: list[str] = Field(default_factory=list, max_length=12)
    prerequisites: list[Prerequisite] = Field(default_factory=list, max_length=5)
    core_concepts: list[CoreConcept] = Field(min_length=1, max_length=5)
    relationships: list[ConceptRelationship] = Field(default_factory=list, max_length=6)
    misconceptions: list[Misconception] = Field(min_length=1, max_length=4)
    teaching_strategy: list[str] = Field(min_length=2, max_length=6)
    worked_example: WorkedExample
    challenges: ChallengeSet
    completion_summary: str | None = Field(default=None, max_length=240)
    completion_criteria: list[str] = Field(min_length=3, max_length=5)
    next_step: str = Field(min_length=4, max_length=180)


class BlueprintGoals(StrictModel):
    understand: str = Field(min_length=4, max_length=80)
    distinguish: str = Field(min_length=4, max_length=80)
    transfer: str = Field(min_length=4, max_length=80)


class BlueprintTask(StrictModel):
    kind: QuestionKind
    interaction_kind: InteractionKind
    question: str = Field(min_length=6, max_length=300)
    hint: str = Field(min_length=2, max_length=180)
    options: list[str] | None = Field(default=None, min_length=3, max_length=5)
    correct_option: int | None = Field(default=None, ge=0, le=4)
    accepted_groups: list[list[str]] | None = Field(default=None, min_length=1, max_length=4)
    rationale: str = Field(min_length=6, max_length=320)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.kind == QuestionKind.MULTIPLE_CHOICE:
            if self.options is None or self.correct_option is None:
                raise ValueError("multiple_choice blueprint requires options and correct_option")
            if self.correct_option >= len(self.options):
                raise ValueError("correct_option is outside blueprint options")
            if self.accepted_groups is not None:
                raise ValueError("multiple_choice blueprint must not define accepted_groups")
        else:
            if not self.accepted_groups:
                raise ValueError("short_answer blueprint requires accepted_groups")
            if self.options is not None or self.correct_option is not None:
                raise ValueError("short_answer blueprint must not define options/correct_option")
        return self


class BlueprintTasks(StrictModel):
    understanding_trial: BlueprintTask
    distinction_trial: BlueprintTask
    remedy: BlueprintTask
    transfer_interleave: BlueprintTask
    boss_understand: BlueprintTask
    boss_distinguish: BlueprintTask
    boss_transfer: BlueprintTask


class LearningBlueprint(StrictModel):
    blueprint_version: Literal["0.3"] = "0.3"
    topic: str = Field(min_length=2, max_length=120)
    specific_confusion: str = Field(min_length=2, max_length=500)
    domain: Domain
    preview_theme: str = Field(min_length=2, max_length=64)
    goals: BlueprintGoals
    source_grounding: list[str] = Field(default_factory=list, max_length=8)
    core_concept: CoreConcept
    misconception: Misconception
    worked_example: WorkedExample
    tasks: BlueprintTasks
    follow_up_recommendation: str = Field(min_length=4, max_length=180)


class BlueprintEnvelope(StrictModel):
    status: Literal["ready", "needs_clarification"]
    clarification: Clarification | None = None
    blueprint: LearningBlueprint | None = None

    @model_validator(mode="after")
    def validate_status_payload(self):
        if self.status == "ready" and self.blueprint is None:
            raise ValueError("ready result requires blueprint")
        if self.status == "needs_clarification" and self.clarification is None:
            raise ValueError("needs_clarification result requires clarification")
        if self.status == "ready" and self.clarification is not None:
            raise ValueError("ready result must not include clarification")
        if self.status == "needs_clarification" and self.blueprint is not None:
            raise ValueError("clarification result must not include blueprint")
        return self


class LLMPlanEnvelope(StrictModel):
    status: Literal["ready", "needs_clarification"]
    clarification: Clarification | None = None
    plan: KnowledgePlan | None = None

    @model_validator(mode="after")
    def validate_status_payload(self):
        if self.status == "ready" and self.plan is None:
            raise ValueError("ready result requires plan")
        if self.status == "needs_clarification" and self.clarification is None:
            raise ValueError("needs_clarification result requires clarification")
        if self.status == "ready" and self.clarification is not None:
            raise ValueError("ready result must not include clarification")
        if self.status == "needs_clarification" and self.plan is not None:
            raise ValueError("clarification result must not include plan")
        return self


class PlannerMetadata(StrictModel):
    generation_mode: Literal["model", "repair", "fallback", "deterministic"]
    request_fingerprint: str = Field(min_length=12, max_length=64)
    content_fingerprint: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=100)
    tokens_used: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=12)
    diagnostics: list[str] = Field(default_factory=list, max_length=12)
    difficulty_trace: list[str] = Field(default_factory=list, max_length=12)
    raw_attempts: int = Field(default=0, ge=0, le=2)
    draft_revision: int = Field(default=1, ge=1, le=3)
    adjustment_count: int = Field(default=0, ge=0, le=2)
    plan_state: Literal["draft"] = "draft"


class PlannerResult(StrictModel):
    schema_version: Literal["1.0", "1.1"] = "1.1"
    status: Literal["ready", "needs_clarification"]
    clarification: Clarification | None = None
    plan: KnowledgePlan | None = None
    metadata: PlannerMetadata

    @model_validator(mode="after")
    def validate_result(self):
        LLMPlanEnvelope(status=self.status, clarification=self.clarification, plan=self.plan)
        return self


JSONValue = dict[str, Any] | list[Any] | str | int | float | bool | None
