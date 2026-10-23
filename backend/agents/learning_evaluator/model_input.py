from __future__ import annotations

from pydantic import Field

from .contracts import node_contract_values
from .enums import EvidenceType, EvaluationDimension, TrustLevel
from .mastery_requirements import normalize_mastery_requirements
from .models import LearningEvaluationRequest, NormalizedEvidence, StrictModel
from .objective_mapper import objective_allowed_dimensions, required_dimensions
from .safe_text import generic_public_label
from .closed_codes import EvidenceErrorCode, EvidenceWarningCode
from .source_identity import OpaqueId


class ModelObjectiveContract(StrictModel):
    objective_id: OpaqueId
    public_label: str
    required_dimensions: list[EvaluationDimension]


class ModelMasteryRequirement(StrictModel):
    requirement_id: OpaqueId
    requirement_type: str
    required_dimensions: list[EvaluationDimension]
    accepted_evidence_types: list[EvidenceType]
    minimum_independent_count: int
    minimum_trust_level: TrustLevel
    requires_transfer: bool
    requires_retention: bool
    requires_unassisted: bool
    minimum_score: float
    resolved: bool


class SafeModelNodeContract(StrictModel):
    node_key: OpaqueId
    node_type: str
    objectives: list[ModelObjectiveContract]
    mastery_requirements: list[ModelMasteryRequirement]
    required_dimensions: list[EvaluationDimension]


ModelNodeContract = SafeModelNodeContract


class ModelEvidenceView(StrictModel):
    evidence_key: OpaqueId
    node_key: OpaqueId
    evidence_type: EvidenceType
    occurred_at: str
    normalized_score: float
    trust_level: TrustLevel
    independent: bool
    dimensions: list[EvaluationDimension]
    hinted: bool
    transfer: bool
    error_codes: list[EvidenceErrorCode]
    negative_signal_codes: list[EvidenceErrorCode]
    quality_warning_codes: list[EvidenceWarningCode]


class ModelExistingEvaluationContext(StrictModel):
    evaluation_revision: int
    node_public_summaries: list[dict] = Field(default_factory=list)


class LearningEvaluationModelInput(StrictModel):
    evaluation_mode: str
    locale: str
    nodes: list[SafeModelNodeContract]
    evidence: list[ModelEvidenceView]
    existing_evaluation: ModelExistingEvaluationContext | None = None
    allowed_evidence_ids: list[OpaqueId]
    allowed_node_ids: list[OpaqueId]


def sanitize_excerpt(text: str | None, limit: int) -> str | None:
    # v0.1.5: caller-labelled safe text is never trusted. A future server-owned
    # No caller-attested free-text projection exists; raw excerpts remain out.
    return None


def sanitize_contract_text(text: str, limit: int = 500) -> str:
    # Kept as a compatibility helper only. Contract text is no longer sent to Provider.
    return "[STRUCTURED_CONTRACT_ONLY]"


def build_model_input(
    request: LearningEvaluationRequest,
    evidence: list[NormalizedEvidence],
    required_dimensions_by_node: dict[str, list[EvaluationDimension]],
) -> LearningEvaluationModelInput:
    nodes: list[SafeModelNodeContract] = []
    for node in request.node_contracts:
        _, _, raw_objectives = node_contract_values(
            node,
            required_dimensions_by_node[node.node_id],
            objective_allowed_dimensions,
        )
        objectives = [
            ModelObjectiveContract(
                objective_id=row["objective_id"],
                public_label=generic_public_label(index),
                required_dimensions=row["required_dimensions"],
            )
            for index, row in enumerate(raw_objectives)
        ]
        normalized, _ = normalize_mastery_requirements(node)
        requirements = [
            ModelMasteryRequirement(
                requirement_id=req.requirement_id,
                requirement_type=req.requirement_type.value,
                required_dimensions=req.required_dimensions,
                accepted_evidence_types=req.accepted_evidence_types,
                minimum_independent_count=req.minimum_independent_count,
                minimum_trust_level=req.minimum_trust_level,
                requires_transfer=req.requires_transfer,
                requires_retention=req.requires_retention,
                requires_unassisted=req.requires_unassisted,
                minimum_score=req.minimum_score,
                resolved=req.resolved,
            )
            for req in normalized
        ]
        nodes.append(
            SafeModelNodeContract(
                node_key=node.node_id,
                node_type=node.node_type.value,
                objectives=objectives,
                mastery_requirements=requirements,
                required_dimensions=required_dimensions_by_node[node.node_id],
            )
        )
    model_evidence = [
        ModelEvidenceView(
            evidence_key=e.evidence_id,
            node_key=e.node_id,
            evidence_type=e.evidence_type,
            occurred_at=e.occurred_at.isoformat(),
            normalized_score=e.normalized_score,
            trust_level=e.trust_level,
            independent=e.independent,
            dimensions=e.dimensions,
            hinted=e.hinted,
            transfer=e.transfer,
            error_codes=e.error_codes,
            negative_signal_codes=e.negative_signal_codes,
            quality_warning_codes=e.quality_warning_codes,
        )
        for e in evidence
        if e.state == e.state.VALID
    ]
    existing = None
    if request.existing_evaluation_summary:
        existing = ModelExistingEvaluationContext(
            evaluation_revision=request.existing_evaluation_summary.evaluation_revision,
            node_public_summaries=[
                {
                    "node_key": n.node_id,
                    "assessment_status": n.assessment_status.value,
                    "mastery_band": round(n.mastery_signal, 1),
                    "confidence_band": round(n.confidence, 1),
                }
                for n in request.existing_evaluation_summary.node_assessments
            ],
        )
    return LearningEvaluationModelInput(
        evaluation_mode=request.evaluation_mode.value,
        locale=request.locale,
        nodes=nodes,
        evidence=model_evidence,
        existing_evaluation=existing,
        allowed_evidence_ids=[e.evidence_id for e in evidence if e.state == e.state.VALID],
        allowed_node_ids=[n.node_id for n in request.node_contracts],
    )
