from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import *
from .evidence_models import EvidencePayload, ObjectiveRubricScore
from .source_identity import OpaqueId
from .closed_codes import PublicReasonCode, PublicWarningCode, MisconceptionCode
from .execution_codes import EvaluationIssueCode


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationIssue(StrictModel):
    code: EvaluationIssueCode
    severity: Severity
    message: str
    path: str | None = None
    repairable: bool = False


class ValidationReport(StrictModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)

    @classmethod
    def from_issues(cls, issues: list[ValidationIssue]):
        return cls(valid=not any(i.severity == Severity.ERROR for i in issues), issues=issues)


class GraphEvaluationRef(StrictModel):
    graph_id: OpaqueId
    graph_revision: int = Field(ge=1)
    graph_continuity_key: OpaqueId


class NodeEvaluationContract(StrictModel):
    node_id: OpaqueId
    node_continuity_key: OpaqueId
    title: str = Field(min_length=1, max_length=300)
    node_type: NodeType
    learning_objectives: list[str] = Field(min_length=1, max_length=30)
    mastery_evidence_requirements: list[str] = Field(min_length=1, max_length=30)
    prerequisite_node_ids: list[OpaqueId] = Field(default_factory=list, max_length=50)
    difficulty_band: str = Field(default="medium", max_length=50)
    status: str = Field(default="planned", max_length=50)
    source_ref_ids: list[OpaqueId] = Field(default_factory=list, max_length=50)
    node_contract_revision: int = Field(default=1, ge=1)


class ObjectiveEvaluationContract(StrictModel):
    objective_id: OpaqueId
    objective_text: str = Field(min_length=1, max_length=500)
    objective_digest: OpaqueId
    objective_order: int = Field(ge=0)
    required_dimensions: list[EvaluationDimension] = Field(default_factory=list)
    node_contract_digest: OpaqueId

class LiteralOrigin(str, Enum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    AI_GENERATED = "AI_GENERATED"
    HUMAN_OBSERVER = "HUMAN_OBSERVER"
    UNKNOWN = "UNKNOWN"


class EvidenceProvenance(StrictModel):
    trust_level: TrustLevel
    capture_method: str = Field(max_length=100)
    content_origin: LiteralOrigin = LiteralOrigin.USER
    private_annotations: dict[str, Any] = Field(default_factory=dict)


class LearningEvidenceRecord(StrictModel):
    evidence_id: OpaqueId
    event_id: OpaqueId
    user_ref: OpaqueId
    graph_id: OpaqueId
    node_id: OpaqueId
    evidence_type: EvidenceType
    source_system: OpaqueId
    source_record_id: OpaqueId
    occurred_at: datetime
    received_at: datetime
    attempt_group_id: OpaqueId | None = None
    activity_id: OpaqueId | None = None
    artifact_id: OpaqueId | None = None
    verified: bool = False
    independent: bool = False
    evidence_payload: EvidencePayload
    provenance: EvidenceProvenance

    @model_validator(mode="after")
    def payload_matches_type(self):
        kind = self.evidence_payload.payload_kind
        allowed = {
            EvidenceType.QUESTION_ATTEMPT: "question_attempt",
            EvidenceType.ASSESSMENT_RESULT: "assessment_result",
            EvidenceType.CODE_EXECUTION: "code_execution",
            EvidenceType.ADVENTURE_EXPLANATION: "adventure",
            EvidenceType.ADVENTURE_DISTINCTION: "adventure",
            EvidenceType.ADVENTURE_TRANSFER: "adventure",
            EvidenceType.RESOURCE_OPENED: "resource_interaction",
            EvidenceType.RESOURCE_COMPLETED: "resource_interaction",
            EvidenceType.NOTE_REFLECTION: "note_reflection",
            EvidenceType.DIALOGUE_EXPLANATION: "dialogue_explanation",
            EvidenceType.TEACH_BACK: "dialogue_explanation",
            EvidenceType.MANUAL_TEACHER_EVIDENCE: "manual",
            EvidenceType.SELF_REPORT: "self_report",
        }
        if allowed[self.evidence_type] != kind:
            raise ValueError("evidence_type does not match payload_kind")
        stage_map = {
            EvidenceType.ADVENTURE_EXPLANATION: "explanation",
            EvidenceType.ADVENTURE_DISTINCTION: {"distinction", "remedy"},
            EvidenceType.ADVENTURE_TRANSFER: "transfer",
        }
        if self.evidence_type in stage_map:
            expected = stage_map[self.evidence_type]
            actual = getattr(self.evidence_payload, "stage", None)
            if (isinstance(expected, set) and actual not in expected) or (
                not isinstance(expected, set) and actual != expected
            ):
                raise ValueError("adventure evidence type does not match stage")
        if self.evidence_type == EvidenceType.RESOURCE_OPENED and getattr(
            self.evidence_payload, "action", None
        ) not in {"opened", "viewed"}:
            raise ValueError("RESOURCE_OPENED requires opened/viewed action")
        if self.evidence_type == EvidenceType.RESOURCE_COMPLETED and getattr(
            self.evidence_payload, "action", None
        ) != "completed":
            raise ValueError("RESOURCE_COMPLETED requires completed action")
        return self


class EvidenceSourceAuthority(StrictModel):
    source_system: OpaqueId
    allowed_evidence_types: list[EvidenceType] = Field(min_length=1)
    maximum_trust_level: TrustLevel
    can_assert_verified: bool = False
    can_assert_independence: bool = False
    authority_contract_version: OpaqueId


class EvidenceAuthorityPolicy(StrictModel):
    authorities: list[EvidenceSourceAuthority] = Field(default_factory=list)
    unknown_source_maximum_trust: TrustLevel = TrustLevel.INFERRED


class TrustedEvaluationContext(StrictModel):
    """Server-owned authority context. It is mandatory and never accepted from a public request."""
    evidence_authority_policy: EvidenceAuthorityPolicy
    authenticated_subject_ref: OpaqueId
    adapter_contract_versions: dict[OpaqueId, OpaqueId]
    context_version: OpaqueId

class EvaluationPolicy(StrictModel):
    clock_skew_seconds: int = Field(default=300, ge=0, le=3600)
    repeated_activity_window_seconds: int = Field(default=86400, ge=60)
    spaced_recheck_days: int = Field(default=7, ge=1, le=365)
    secure_min_independent_evidence: int = Field(default=3, ge=2, le=20)
    secure_min_verified_evidence: int = Field(default=2, ge=1, le=20)
    stale_after_days: int = Field(default=90, ge=1, le=3650)
    weak_evidence_mastery_cap: float = Field(default=0.2, ge=0, le=0.4)
    single_evidence_mastery_cap: float = Field(default=0.5, ge=0, le=0.7)
    objective_achievement_threshold: float = Field(default=0.65, ge=0.5, le=0.95)
    text_excerpt_limit: int = Field(default=500, ge=50, le=1200)


class EvaluationConstraints(StrictModel):
    max_evidence_records: int = Field(default=5000, ge=1, le=20000)
    max_nodes: int = Field(default=200, ge=1, le=1000)
    allow_manual_evidence: bool = True


class EvidenceCorrectionIntent(StrictModel):
    evidence_id: OpaqueId
    requested_reason: CorrectionReason
    safe_user_summary: str | None = Field(default=None, max_length=300)
    idempotency_key: OpaqueId


class TrustedCorrectionContext(StrictModel):
    authenticated_actor_ref: OpaqueId
    actor_role: CorrectionActorRole
    corrected_at: datetime
    allowed_correction_reasons: list[CorrectionReason]
    correction_source: OpaqueId
    correction_contract_version: OpaqueId
    correction_event_ids: dict[OpaqueId, OpaqueId]
    supersedes_correction_event_ids: dict[OpaqueId, OpaqueId] = Field(default_factory=dict)


class EvidenceCorrectionAuditEntry(StrictModel):
    correction_event_id: OpaqueId
    reason: CorrectionReason
    actor_ref: OpaqueId
    actor_role: CorrectionActorRole
    corrected_at: datetime
    contract_version: OpaqueId
    source: OpaqueId
    idempotency_key: OpaqueId
    supersedes_correction_event_id: OpaqueId | None = None

class DimensionAssessment(StrictModel):
    dimension: EvaluationDimension
    score_band: ScoreBand
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    supporting_evidence_ids: list[OpaqueId] = Field(default_factory=list)
    counter_evidence_ids: list[OpaqueId] = Field(default_factory=list)
    missing_evidence: bool = False
    rationale_code: OpaqueId


class ObjectiveAssessment(StrictModel):
    objective_index: int = Field(default=0, ge=0)
    objective: str
    objective_id: OpaqueId | None = None
    objective_digest: OpaqueId | None = None
    node_contract_digest: OpaqueId | None = None
    covered: bool = False
    assessed: bool = False
    achieved: bool = False
    achievement_status: ObjectiveAchievementStatus = ObjectiveAchievementStatus.UNASSESSED
    achievement_score: float = Field(default=0, ge=0, le=1)
    evidence_ids: list[OpaqueId] = Field(default_factory=list)
    supporting_evidence_ids: list[OpaqueId] = Field(default_factory=list)
    counter_evidence_ids: list[OpaqueId] = Field(default_factory=list)
    coverage_rationale: str = Field(default="OBJECTIVE_MAPPING_MISSING", max_length=200)
    coverage_confidence: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def synchronize_legacy_covered(self):
        if self.assessed and not self.covered:
            self.covered = True
        elif self.covered and not self.assessed:
            self.assessed = True
        return self

class NormalizedMasteryRequirement(StrictModel):
    requirement_id: OpaqueId
    source_requirement_index: int = Field(default=0, ge=0)
    source_text: str = Field(min_length=1, max_length=500)
    requirement_type: MasteryRequirementType
    required_dimensions: list[EvaluationDimension] = Field(default_factory=list)
    accepted_evidence_types: list[EvidenceType] = Field(default_factory=list)
    minimum_independent_count: int = Field(default=1, ge=0, le=20)
    minimum_trust_level: TrustLevel = TrustLevel.SYSTEM_OBSERVED
    requires_transfer: bool = False
    requires_retention: bool = False
    requires_unassisted: bool = False
    minimum_score: float = Field(default=0.6, ge=0, le=1)
    resolved: bool = True


class MasteryRequirementParseResult(StrictModel):
    source_requirement_index: int = Field(ge=0)
    source_text: str
    parsed_requirements: list[NormalizedMasteryRequirement] = Field(default_factory=list)
    unparsed_fragments: list[str] = Field(default_factory=list)
    ambiguous: bool = False


class MasteryRequirementAssessment(StrictModel):
    requirement_id: OpaqueId
    requirement_type: MasteryRequirementType
    satisfied: bool
    evidence_ids: list[OpaqueId] = Field(default_factory=list)
    reason_code: OpaqueId


class Contradiction(StrictModel):
    contradiction_code: OpaqueId
    node_id: OpaqueId
    involved_evidence_ids: list[OpaqueId] = Field(min_length=2, max_length=50)
    severity: Severity
    resolution_needed: bool = True
    recommended_follow_up: ActionType


class EvidenceQualityWarning(StrictModel):
    warning_code: OpaqueId
    node_id: OpaqueId
    evidence_ids: list[OpaqueId] = Field(min_length=1, max_length=50)
    severity: Severity
    recommended_follow_up: ActionType


class Misconception(StrictModel):
    code: OpaqueId
    node_id: OpaqueId
    summary: str
    evidence_ids: list[OpaqueId]
    confidence: float = Field(ge=0, le=1)
    severity: MisconceptionSeverity = MisconceptionSeverity.LOW
    resolved: bool = False


class RecommendedEvaluationAction(StrictModel):
    action_type: ActionType
    target_node_id: OpaqueId
    reason_codes: list[OpaqueId]
    required_evidence_dimension: EvaluationDimension | None = None
    priority: int = Field(ge=1, le=5)


class NodeAssessment(StrictModel):
    node_id: OpaqueId
    node_continuity_key: OpaqueId
    node_contract_revision: int = Field(ge=1)
    node_contract_digest: OpaqueId
    mastery_requirement_digest: OpaqueId
    objective_contracts: list[ObjectiveEvaluationContract]
    assessment_status: AssessmentStatus
    mastery_signal: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_sufficiency: float = Field(ge=0, le=1)
    dimension_assessments: list[DimensionAssessment]
    objective_assessments: list[ObjectiveAssessment]
    mastery_requirement_assessments: list[MasteryRequirementAssessment] = Field(default_factory=list)
    supported_claims: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    misconceptions: list[Misconception] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    evidence_quality_warnings: list[EvidenceQualityWarning] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    completion_eligibility_signal: CompletionEligibility
    recommended_evaluation_actions: list[RecommendedEvaluationAction] = Field(default_factory=list)
    evidence_ids: list[OpaqueId] = Field(default_factory=list)
    needs_review: bool = False


class ExistingEvidenceSummary(StrictModel):
    evidence_id: OpaqueId
    event_id: OpaqueId
    node_id: OpaqueId
    evidence_type: EvidenceType
    occurred_at: datetime
    state: EvidenceState = EvidenceState.VALID
    normalized_score: float = Field(ge=0, le=1)
    effective_weight: float = Field(ge=0, le=1)
    dimensions: list[EvaluationDimension]
    independent: bool
    trust_level: TrustLevel
    activity_key: str
    source_fingerprint: str
    subject_ref: OpaqueId | None = None
    graph_id: OpaqueId | None = None
    graph_continuity_key: OpaqueId | None = None
    source_system: OpaqueId | None = None
    effective_verified: bool | None = None
    evidence_family_key: OpaqueId | None = None
    canonical_activity_key: OpaqueId | None = None
    canonical_item_key: OpaqueId | None = None
    attempt_group_key: OpaqueId | None = None
    weak: bool | None = None
    transfer: bool | None = None
    hinted: bool | None = None
    error_codes: list[OpaqueId] | None = None
    objective_scores: dict[str, float] = Field(default_factory=dict)
    objective_rubric_scores: list[ObjectiveRubricScore] = Field(default_factory=list)
    negative_signal_codes: list[OpaqueId] | None = None
    misconception_pattern_codes: list[OpaqueId] | None = None
    quality_warning_codes: list[OpaqueId] | None = None
    correction_history: list[EvidenceCorrectionAuditEntry] = Field(default_factory=list)

class ExistingNodeAssessmentSummary(StrictModel):
    node_id: OpaqueId
    assessment_status: AssessmentStatus
    mastery_signal: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_sufficiency: float = Field(default=0, ge=0, le=1)
    dimension_assessments: list[DimensionAssessment] = Field(default_factory=list)
    objective_assessments: list[ObjectiveAssessment] = Field(default_factory=list)
    mastery_requirement_assessments: list[MasteryRequirementAssessment] = Field(default_factory=list)
    supported_claims: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    completion_eligibility_signal: CompletionEligibility = CompletionEligibility.MORE_EVIDENCE_REQUIRED
    needs_review: bool = False
    node_contract_revision: int | None = Field(default=None, ge=1)
    node_contract_digest: OpaqueId | None = None
    mastery_requirement_digest: OpaqueId | None = None
    objective_contracts: list[ObjectiveEvaluationContract] = Field(default_factory=list)

class ExistingMisconceptionSummary(StrictModel):
    code: OpaqueId
    node_id: OpaqueId
    summary: str = ""
    evidence_ids: list[OpaqueId] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    severity: MisconceptionSeverity = MisconceptionSeverity.LOW
    resolved: bool = False


class ExistingContradictionSummary(StrictModel):
    contradiction_code: OpaqueId
    node_id: OpaqueId
    resolved: bool = False


class ProcessedEvaluationRequest(StrictModel):
    request_id: OpaqueId
    idempotency_key: OpaqueId
    request_digest: OpaqueId
    evaluation_mode: EvaluationMode
    base_revision: int = Field(ge=0)
    result_revision: int = Field(ge=1)
    result_ref: OpaqueId
    processed_at: datetime
    # DEPRECATED / UNTRUSTED MIGRATION METADATA. v0.1.5 never replays from this field.
    result_snapshot: dict[str, Any] | None = None


class ExistingEvaluationSummary(StrictModel):
    evaluation_id: OpaqueId
    evaluation_revision: int = Field(ge=1)
    graph_id: OpaqueId
    subject_ref: OpaqueId | None = None
    graph_revision: int | None = Field(default=None, ge=1)
    graph_continuity_key: OpaqueId | None = None
    trusted_context_version: OpaqueId | None = None
    authority_context_digest: OpaqueId | None = None
    node_contract_set_digest: OpaqueId | None = None
    evidence_ledger: list[ExistingEvidenceSummary] = Field(default_factory=list)
    node_assessments: list[ExistingNodeAssessmentSummary] = Field(default_factory=list)
    misconceptions: list[ExistingMisconceptionSummary] = Field(default_factory=list)
    contradictions: list[ExistingContradictionSummary] = Field(default_factory=list)
    evidence_quality_warnings: list[EvidenceQualityWarning] = Field(default_factory=list)
    # DEPRECATED display/migration metadata. It is rejected by preflight when non-empty.
    processed_requests: list[ProcessedEvaluationRequest] = Field(default_factory=list)

class LearningEvaluationRequest(StrictModel):
    request_id: OpaqueId
    idempotency_key: OpaqueId
    subject_ref: OpaqueId
    evaluation_mode: EvaluationMode
    graph_ref: GraphEvaluationRef
    node_contracts: list[NodeEvaluationContract] = Field(min_length=1)
    evidence_records: list[LearningEvidenceRecord] = Field(default_factory=list)
    existing_evaluation_summary: ExistingEvaluationSummary | None = None
    expected_existing_revision: int | None = Field(default=None, ge=0)
    evidence_corrections: list[EvidenceCorrectionIntent] = Field(default_factory=list)
    evaluation_policy: EvaluationPolicy = Field(default_factory=EvaluationPolicy)
    locale: str = Field(default="zh-CN", max_length=20)
    constraints: EvaluationConstraints = Field(default_factory=EvaluationConstraints)
    evaluated_at: datetime

    @model_validator(mode="after")
    def mode_contract(self):
        if self.evaluation_mode == EvaluationMode.INITIAL:
            if self.existing_evaluation_summary is not None:
                raise ValueError("INITIAL cannot include existing evaluation")
            # Internal callers receive a deterministic base revision. Public adapters
            # still require the caller to submit the field explicitly.
            if self.expected_existing_revision is None:
                self.expected_existing_revision = 0
            elif self.expected_existing_revision != 0:
                raise ValueError("INITIAL_EXPECTED_REVISION_MUST_BE_ZERO")
        elif self.existing_evaluation_summary is not None:
            if self.expected_existing_revision is None:
                if self.evaluation_mode == EvaluationMode.FULL_REASSESSMENT:
                    raise ValueError("FULL_REASSESSMENT_REQUIRES_EXPECTED_REVISION")
                self.expected_existing_revision = self.existing_evaluation_summary.evaluation_revision
            elif self.expected_existing_revision != self.existing_evaluation_summary.evaluation_revision:
                raise ValueError("EVALUATION_REVISION_CONFLICT")
        if self.evaluation_mode in {EvaluationMode.INCREMENTAL, EvaluationMode.EVIDENCE_CORRECTION, EvaluationMode.FULL_REASSESSMENT} and self.existing_evaluation_summary is None:
            raise ValueError("FULL_REASSESSMENT_REQUIRES_EXISTING_EVALUATION" if self.evaluation_mode == EvaluationMode.FULL_REASSESSMENT else "mode requires existing evaluation")
        if self.evaluation_mode == EvaluationMode.FULL_REASSESSMENT and self.expected_existing_revision is None:
            raise ValueError("FULL_REASSESSMENT_REQUIRES_EXPECTED_REVISION")
        if self.evaluation_mode == EvaluationMode.EVIDENCE_CORRECTION and not self.evidence_corrections:
            raise ValueError("EVIDENCE_CORRECTION requires corrections")
        if self.evaluation_mode != EvaluationMode.EVIDENCE_CORRECTION and self.evidence_corrections:
            raise ValueError("corrections only allowed in EVIDENCE_CORRECTION")
        if len(self.evidence_records) > self.constraints.max_evidence_records:
            raise ValueError("too many evidence records")
        if len(self.node_contracts) > self.constraints.max_nodes:
            raise ValueError("too many node contracts")
        return self

class NormalizedEvidence(StrictModel):
    evidence_id: OpaqueId
    event_id: OpaqueId
    node_id: OpaqueId
    evidence_type: EvidenceType
    occurred_at: datetime
    state: EvidenceState = EvidenceState.VALID
    normalized_score: float = Field(ge=0, le=1)
    effective_weight: float = Field(ge=0, le=1)
    dimensions: list[EvaluationDimension]
    independent: bool
    trust_level: TrustLevel
    effective_verified: bool = False
    subject_ref: OpaqueId
    graph_id: OpaqueId
    graph_continuity_key: OpaqueId
    source_system: OpaqueId | None = None
    activity_key: str
    source_fingerprint: str
    evidence_family_key: OpaqueId
    canonical_activity_key: OpaqueId
    canonical_item_key: OpaqueId
    attempt_group_key: OpaqueId
    weak: bool = False
    transfer: bool = False
    hinted: bool = False
    error_codes: list[OpaqueId] = Field(default_factory=list)
    objective_scores: dict[str, float] = Field(default_factory=dict)
    objective_rubric_scores: list[ObjectiveRubricScore] = Field(default_factory=list)
    negative_signal_codes: list[OpaqueId] = Field(default_factory=list)
    misconception_pattern_codes: list[OpaqueId] = Field(default_factory=list)
    quality_warning_codes: list[OpaqueId] = Field(default_factory=list)
    correction_history: list[EvidenceCorrectionAuditEntry] = Field(default_factory=list)
    safe_summary: str | None = Field(default=None, max_length=500)
    raw_private: dict[str, Any] = Field(default_factory=dict)

class CandidateMisconception(StrictModel):
    code: OpaqueId
    summary: str = Field(min_length=1, max_length=300)
    evidence_ids: list[OpaqueId] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)


class CandidateObjectiveMapping(StrictModel):
    node_id: OpaqueId
    objective_index: int = Field(ge=0)
    evidence_ids: list[OpaqueId] = Field(default_factory=list, max_length=50)
    explanation: str = Field(max_length=400)
    coverage_confidence: float = Field(default=0.5, ge=0, le=1)


class CandidateAction(StrictModel):
    action_type: ActionType
    target_node_id: OpaqueId
    reason_codes: list[OpaqueId] = Field(min_length=1, max_length=20)
    required_evidence_dimension: EvaluationDimension | None = None
    priority: int = Field(ge=1, le=5)


class EvaluationCandidate(StrictModel):
    objective_mappings: list[CandidateObjectiveMapping] = Field(default_factory=list)
    misconceptions: list[CandidateMisconception] = Field(default_factory=list)
    recommended_actions: list[CandidateAction] = Field(default_factory=list)
    global_explanation: str = Field(default="", max_length=800)
    fallback_used: bool = False


class EvidenceLedgerEntry(StrictModel):
    evidence_id: OpaqueId
    event_id: OpaqueId
    node_id: OpaqueId
    evidence_type: EvidenceType
    occurred_at: datetime
    state: EvidenceState
    normalized_score: float = Field(ge=0, le=1)
    effective_weight: float = Field(ge=0, le=1)
    dimensions: list[EvaluationDimension]
    independent: bool
    trust_level: TrustLevel
    effective_verified: bool = False
    subject_ref: OpaqueId
    graph_id: OpaqueId
    graph_continuity_key: OpaqueId
    source_system: OpaqueId | None = None
    recency_band: str
    staleness_warning: bool
    source_fingerprint: str
    evidence_family_key: OpaqueId | None = None
    canonical_activity_key: OpaqueId | None = None
    canonical_item_key: OpaqueId | None = None
    attempt_group_key: OpaqueId | None = None
    weak: bool = False
    transfer: bool = False
    hinted: bool = False
    error_codes: list[OpaqueId] = Field(default_factory=list)
    objective_scores: dict[str, float] = Field(default_factory=dict)
    objective_rubric_scores: list[ObjectiveRubricScore] = Field(default_factory=list)
    negative_signal_codes: list[OpaqueId] = Field(default_factory=list)
    misconception_pattern_codes: list[OpaqueId] = Field(default_factory=list)
    quality_warning_codes: list[OpaqueId] = Field(default_factory=list)
    correction_history: list[EvidenceCorrectionAuditEntry] = Field(default_factory=list)

class EvaluationChangeSet(StrictModel):
    added_evidence_ids: list[OpaqueId] = Field(default_factory=list)
    invalidated_evidence_ids: list[OpaqueId] = Field(default_factory=list)
    updated_node_assessments: list[OpaqueId] = Field(default_factory=list)
    new_misconceptions: list[OpaqueId] = Field(default_factory=list)
    resolved_misconceptions: list[OpaqueId] = Field(default_factory=list)
    new_contradictions: list[OpaqueId] = Field(default_factory=list)
    resolved_contradictions: list[OpaqueId] = Field(default_factory=list)
    mastery_signal_changes: dict[str, float] = Field(default_factory=dict)
    confidence_changes: dict[str, float] = Field(default_factory=dict)


class PublicEvidenceSummary(StrictModel):
    evidence_type: EvidenceType
    occurred_at: datetime
    state: EvidenceState
    trust_level: TrustLevel
    recency_band: str


class PublicDimensionAssessment(StrictModel):
    dimension: EvaluationDimension
    score_band: ScoreBand
    confidence: float
    missing_evidence: bool


class PublicObjectiveAssessment(StrictModel):
    objective_id: OpaqueId
    public_label: str = Field(min_length=1, max_length=120)
    achievement_status: ObjectiveAchievementStatus
    achieved: bool
    achievement_score_band: ScoreBand
    coverage_confidence_band: ScoreBand
    evidence_count: int = Field(ge=0)
    counter_evidence_count: int = Field(ge=0)
    safe_gap_codes: list[PublicReasonCode] = Field(default_factory=list)

    # Source-compatible read-only aliases. They are properties, never serialized,
    # so legacy probes cannot force internal objective text or evidence IDs public.
    @property
    def objective(self) -> str:
        return self.public_label

    @property
    def covered(self) -> bool:
        return self.achievement_status != ObjectiveAchievementStatus.UNASSESSED

    @property
    def assessed(self) -> bool:
        return self.covered


class PublicMasteryRequirementAssessment(StrictModel):
    requirement_id: OpaqueId
    requirement_type: MasteryRequirementType
    satisfied: bool
    reason_code: PublicReasonCode


class PublicMisconception(StrictModel):
    code: MisconceptionCode
    summary: str
    confidence: float
    severity: MisconceptionSeverity = MisconceptionSeverity.LOW
    resolved: bool


class PublicRecommendedEvaluationAction(StrictModel):
    action_type: ActionType
    target_node_id: OpaqueId
    reason_codes: list[PublicReasonCode] = Field(min_length=1, max_length=20)
    required_evidence_dimension: EvaluationDimension | None = None
    priority: int = Field(ge=1, le=5)


class PublicNodeAssessment(StrictModel):
    node_id: OpaqueId
    assessment_status: AssessmentStatus
    mastery_signal: float
    confidence: float
    evidence_sufficiency: float
    dimensions: list[PublicDimensionAssessment]
    objective_coverage: list[PublicObjectiveAssessment]
    mastery_requirement_coverage: list[PublicMasteryRequirementAssessment] = Field(default_factory=list)
    misconceptions: list[PublicMisconception]
    evidence_gaps: list[PublicReasonCode]
    recommended_evaluation_actions: list[PublicRecommendedEvaluationAction]
    completion_eligibility_signal: CompletionEligibility
    needs_review: bool = False


class PublicLearningEvaluation(StrictModel):
    evaluation_id: OpaqueId
    evaluation_revision: int
    graph_id: OpaqueId
    graph_revision: int
    evaluated_at: datetime
    node_assessments: list[PublicNodeAssessment]
    evidence_summaries: list[PublicEvidenceSummary]
    global_warnings: list[PublicWarningCode]
    next_evaluation_actions: list[PublicRecommendedEvaluationAction]


class LearningEvaluationResult(StrictModel):
    evaluation_id: OpaqueId
    evaluation_revision: int = Field(ge=1)
    request_id: OpaqueId
    subject_ref: OpaqueId
    graph_id: OpaqueId
    graph_revision: int = Field(ge=1)
    graph_continuity_key: OpaqueId
    trusted_context_version: OpaqueId
    authority_context_digest: OpaqueId
    node_contract_set_digest: OpaqueId
    evaluated_at: datetime
    evaluation_mode: EvaluationMode
    node_assessments: list[NodeAssessment]
    evidence_ledger: list[EvidenceLedgerEntry]
    contradictions: list[Contradiction]
    evidence_quality_warnings: list[EvidenceQualityWarning] = Field(default_factory=list)
    global_warnings: list[str]
    next_evaluation_actions: list[RecommendedEvaluationAction]
    change_set: EvaluationChangeSet
    validation_report: ValidationReport
    public_view: PublicLearningEvaluation
    private_payload: dict[str, Any]
    provenance: dict[str, Any]
    # Deprecated compatibility field; always empty in newly issued results.
    processed_requests: list[ProcessedEvaluationRequest] = Field(default_factory=list)
    fallback_used: bool = False
