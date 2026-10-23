from __future__ import annotations

from .models import *
from .safe_text import generic_public_label, safe_gap_code
from .closed_codes import PublicReasonCode, PublicWarningCode, to_public_reason, MisconceptionCode, is_gap_code


def _band(value: float) -> ScoreBand:
    if value <= 0:
        return ScoreBand.NO_EVIDENCE
    if value < 0.4:
        return ScoreBand.LOW
    if value < 0.75:
        return ScoreBand.MEDIUM
    return ScoreBand.HIGH


def build_public_view(
    evaluation_id: str,
    revision: int,
    request: LearningEvaluationRequest,
    node_assessments: list[NodeAssessment],
    ledger: list[EvidenceLedgerEntry],
    warnings: list[str],
    actions: list[RecommendedEvaluationAction],
) -> PublicLearningEvaluation:
    """Build a positive-whitelist Public View.

    Internal ObjectiveAssessment, requirement models, contract digests, evidence IDs,
    canonical identities and raw objective text are never reused or dumped here.
    """
    nodes: list[PublicNodeAssessment] = []
    for node in node_assessments:
        public_objectives = []
        for index, objective in enumerate(node.objective_assessments):
            gap_codes = []
            if not objective.assessed:
                gap_codes.append("OBJECTIVE_UNASSESSED")
            elif not objective.achieved:
                gap_codes.append("OBJECTIVE_NOT_ACHIEVED")
            if objective.counter_evidence_ids:
                gap_codes.append("OBJECTIVE_COUNTER_EVIDENCE")
            public_objectives.append(
                PublicObjectiveAssessment(
                    objective_id=objective.objective_id or f"objective-{index+1}",
                    public_label=generic_public_label(index),
                    achievement_status=objective.achievement_status,
                    achieved=objective.achieved,
                    achievement_score_band=_band(objective.achievement_score),
                    coverage_confidence_band=_band(objective.coverage_confidence),
                    evidence_count=len(set(objective.supporting_evidence_ids)),
                    counter_evidence_count=len(set(objective.counter_evidence_ids)),
                    safe_gap_codes=[to_public_reason(x) for x in gap_codes],
                )
            )
        public_requirements = [
            PublicMasteryRequirementAssessment(
                requirement_id=item.requirement_id,
                requirement_type=item.requirement_type,
                satisfied=item.satisfied,
                reason_code=item.reason_code,
            )
            for item in node.mastery_requirement_assessments
        ]
        nodes.append(
            PublicNodeAssessment(
                node_id=node.node_id,
                assessment_status=node.assessment_status,
                mastery_signal=node.mastery_signal,
                confidence=node.confidence,
                evidence_sufficiency=node.evidence_sufficiency,
                dimensions=[
                    PublicDimensionAssessment(
                        dimension=d.dimension,
                        score_band=d.score_band,
                        confidence=d.confidence,
                        missing_evidence=d.missing_evidence,
                    )
                    for d in node.dimension_assessments
                ],
                objective_coverage=public_objectives,
                mastery_requirement_coverage=public_requirements,
                misconceptions=[
                    PublicMisconception(
                        code=m.code if str(m.code) in {x.value for x in MisconceptionCode} else MisconceptionCode.UNCLASSIFIED_PRIVATE_SIGNAL.value,
                        summary="检测到需要复核的错误模式",
                        confidence=m.confidence,
                        severity=m.severity,
                        resolved=m.resolved,
                    )
                    for m in node.misconceptions
                ],
                evidence_gaps=[to_public_reason(gap) for gap in node.evidence_gaps],
                recommended_evaluation_actions=[
                    PublicRecommendedEvaluationAction(
                        action_type=a.action_type, target_node_id=a.target_node_id,
                        reason_codes=[to_public_reason(x) for x in a.reason_codes],
                        required_evidence_dimension=a.required_evidence_dimension, priority=a.priority,
                    ) for a in node.recommended_evaluation_actions
                ],
                completion_eligibility_signal=node.completion_eligibility_signal,
                needs_review=node.needs_review,
            )
        )
    evidence = [
        PublicEvidenceSummary(
            evidence_type=e.evidence_type,
            occurred_at=e.occurred_at,
            state=e.state,
            trust_level=e.trust_level,
            recency_band=e.recency_band,
        )
        for e in ledger
    ]
    return PublicLearningEvaluation(
        evaluation_id=evaluation_id,
        evaluation_revision=revision,
        graph_id=request.graph_ref.graph_id,
        graph_revision=request.graph_ref.graph_revision,
        evaluated_at=request.evaluated_at,
        node_assessments=nodes,
        evidence_summaries=evidence,
        global_warnings=[PublicWarningCode.REVIEW_REQUIRED for _ in warnings],
        next_evaluation_actions=[
            PublicRecommendedEvaluationAction(
                action_type=a.action_type, target_node_id=a.target_node_id,
                reason_codes=[to_public_reason(x) for x in a.reason_codes],
                required_evidence_dimension=a.required_evidence_dimension, priority=a.priority,
            ) for a in actions
        ],
    )
