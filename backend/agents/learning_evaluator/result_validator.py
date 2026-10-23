from __future__ import annotations

import json
from collections import defaultdict

from .candidate_validator import explicit_objective_score, objective_contract_for
from .evidence_validator import issue
from .mastery_requirements import normalize_mastery_requirements
from .models import *
from .contracts import authority_context_digest, node_contract_set_digest, node_contract_values
from .objective_mapper import required_dimensions, objective_allowed_dimensions
from .correction_invariants import validate_correction_history

from .execution_codes import EvaluationIssueCode
FORBIDDEN_PUBLIC = (
    "raw evidence",
    "evidence_payload",
    "private_payload",
    "private_annotations",
    "password",
    "token",
    "C:/",
    "/home/",
    "provider",
    "model_id",
    "prompt",
    "private_comment",
    "authority_contract_version",
    "objective_digest",
    "node_contract_digest",
    "supporting_evidence_ids",
    "counter_evidence_ids",
    "coverage_rationale",
    "objective_text",
)


def validate_public_view(public: PublicLearningEvaluation) -> ValidationReport:
    payload = json.dumps(public.model_dump(mode="json"), ensure_ascii=False)
    issues: list[ValidationIssue] = []
    low = payload.lower()
    for term in FORBIDDEN_PUBLIC:
        if term.lower() in low:
            issues.append(issue(EvaluationIssueCode.PRIVATE_DATA_LEAK, f"forbidden public token: {term}"))
    return ValidationReport.from_issues(issues)


def _negative(evidence: EvidenceLedgerEntry) -> bool:
    return evidence.normalized_score < 0.6 or bool(evidence.error_codes) or bool(evidence.negative_signal_codes)


def _validate_cross_objective_result(node: NodeAssessment, evidence: dict[str, EvidenceLedgerEntry], contract: NodeEvaluationContract) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    by_evidence: dict[str, list[int]] = defaultdict(list)
    by_activity: dict[str, list[int]] = defaultdict(list)
    for objective in node.objective_assessments:
        if not objective.achieved:
            continue
        for evidence_id in objective.supporting_evidence_ids:
            by_evidence[evidence_id].append(objective.objective_index)
            if evidence_id in evidence and evidence[evidence_id].canonical_activity_key:
                by_activity[evidence[evidence_id].canonical_activity_key].append(objective.objective_index)
    for evidence_id, indices in by_evidence.items():
        distinct = sorted(set(indices))
        if len(distinct) <= 1:
            continue
        item = evidence[evidence_id]
        allowed = all(
            explicit_objective_score(item, contract, idx) is not None
            for idx in distinct
        )
        if not allowed:
            issues.append(issue(EvaluationIssueCode.OBJECTIVE_EVIDENCE_REUSED_ACROSS_OBJECTIVES, "same evidence achieved multiple objectives without objective-specific trusted score"))
    for activity_key, indices in by_activity.items():
        if len(set(indices)) > 1:
            involved = [evidence[eid] for objective in node.objective_assessments for eid in objective.supporting_evidence_ids if eid in evidence and evidence[eid].canonical_activity_key == activity_key]
            allowed = all(
                all(explicit_objective_score(item, contract, idx) is not None for idx in set(indices))
                for item in involved
            )
            if not allowed:
                issues.append(issue(EvaluationIssueCode.OBJECTIVE_ACTIVITY_REUSED_ACROSS_OBJECTIVES, "same canonical activity achieved multiple objectives"))
    return issues


def validate_evaluation_result(
    result: LearningEvaluationResult,
    request: LearningEvaluationRequest,
    trusted_context: TrustedEvaluationContext | None = None,
) -> ValidationReport:
    if not isinstance(trusted_context, TrustedEvaluationContext):
        return ValidationReport.from_issues([issue(EvaluationIssueCode.MISSING_TRUSTED_EVALUATION_CONTEXT, "trusted evaluation context is mandatory")])
    context = trusted_context
    issues: list[ValidationIssue] = []
    expected_authority_digest = authority_context_digest(context)
    expected_contract_set_digest = node_contract_set_digest(request.node_contracts, required_dimensions, objective_allowed_dimensions)
    if result.subject_ref != context.authenticated_subject_ref or result.subject_ref != request.subject_ref:
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "result subject ownership mismatch"))
    if result.graph_id != request.graph_ref.graph_id or result.graph_continuity_key != request.graph_ref.graph_continuity_key:
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "result graph ownership mismatch"))
    if result.trusted_context_version != context.context_version:
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "trusted context version mismatch"))
    if result.authority_context_digest != expected_authority_digest:
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "authority context digest mismatch"))
    if result.node_contract_set_digest != expected_contract_set_digest:
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "node contract set digest mismatch"))
    evidence = {e.evidence_id: e for e in result.evidence_ledger}
    node_contracts = {n.node_id: n for n in request.node_contracts}
    correction_events: set[str] = set()
    correction_idempotency: dict[str, str] = {}
    corrected_ids: set[str] = set()
    for ledger in result.evidence_ledger:
        if ledger.subject_ref != result.subject_ref:
            issues.append(issue(EvaluationIssueCode.EVIDENCE_HISTORY_USER_MISMATCH, "ledger evidence owner mismatch", ledger.evidence_id))
        if ledger.graph_id != result.graph_id or ledger.graph_continuity_key != result.graph_continuity_key:
            issues.append(issue(EvaluationIssueCode.EVIDENCE_HISTORY_GRAPH_MISMATCH, "ledger evidence graph mismatch", ledger.evidence_id))
        issues.extend(
            validate_correction_history(
                list(ledger.correction_history),
                evidence_id=ledger.evidence_id,
                state=ledger.state,
                effective_weight=ledger.effective_weight,
                effective_verified=ledger.effective_verified,
                independent=ledger.independent,
                global_events=correction_events,
                global_idempotency=correction_idempotency,
            )
        )
        if ledger.correction_history:
            corrected_ids.add(ledger.evidence_id)
        for correction in ledger.correction_history:
            if correction.corrected_at < ledger.occurred_at:
                issues.append(issue(EvaluationIssueCode.CORRECTION_BEFORE_EVIDENCE, "correction predates evidence", ledger.evidence_id))
            if correction.corrected_at > result.evaluated_at:
                issues.append(issue(EvaluationIssueCode.CORRECTION_FROM_FUTURE, "correction is after evaluation", ledger.evidence_id))
    if context.authenticated_subject_ref != request.subject_ref:
        issues.append(issue(EvaluationIssueCode.UNTRUSTED_AUTHORITY_CONTEXT, "trusted authority context subject mismatch"))
    if not result.validation_report.valid:
        issues.append(issue(EvaluationIssueCode.INVALID_RESULT_REPORT, "successful result cannot be invalid"))
    for contradiction in result.contradictions:
        if len(contradiction.involved_evidence_ids) != len(set(contradiction.involved_evidence_ids)):
            issues.append(issue(EvaluationIssueCode.DUPLICATE_CONTRADICTION_EVIDENCE_ID, "contradiction evidence IDs must be distinct"))
        if len(set(contradiction.involved_evidence_ids)) < 2:
            issues.append(issue(EvaluationIssueCode.CONTRADICTION_REQUIRES_DISTINCT_EVIDENCE, "contradiction requires at least two different evidence records"))
        if not set(contradiction.involved_evidence_ids) <= set(evidence):
            issues.append(issue(EvaluationIssueCode.CONTRADICTION_WITHOUT_EVIDENCE, "contradiction references missing evidence"))
    for index, node in enumerate(result.node_assessments):
        contract = node_contracts[node.node_id]
        if corrected_ids.intersection(node.evidence_ids):
            issues.append(issue(EvaluationIssueCode.CORRECTED_EVIDENCE_USED_FOR_MASTERY, "corrected evidence cannot appear in node evidence IDs", f"node_assessments[{index}]"))
        for objective in node.objective_assessments:
            if corrected_ids.intersection(objective.evidence_ids) or corrected_ids.intersection(objective.supporting_evidence_ids) or corrected_ids.intersection(objective.counter_evidence_ids):
                issues.append(issue(EvaluationIssueCode.CORRECTED_EVIDENCE_USED_FOR_MASTERY, "corrected evidence cannot support objective assessment", f"node_assessments[{index}]"))
        for requirement in node.mastery_requirement_assessments:
            if corrected_ids.intersection(requirement.evidence_ids):
                issues.append(issue(EvaluationIssueCode.CORRECTED_EVIDENCE_USED_FOR_MASTERY, "corrected evidence cannot satisfy mastery requirement", f"node_assessments[{index}]"))
        for misconception in node.misconceptions:
            if corrected_ids.intersection(misconception.evidence_ids):
                issues.append(issue(EvaluationIssueCode.CORRECTED_EVIDENCE_USED_FOR_MASTERY, "corrected evidence cannot support misconception", f"node_assessments[{index}]"))
        current_digest, current_mastery_digest, raw_objectives = node_contract_values(contract, required_dimensions(contract.node_type), objective_allowed_dimensions)
        expected_objectives = [ObjectiveEvaluationContract(**x) for x in raw_objectives]
        if node.node_contract_revision != contract.node_contract_revision or node.node_contract_digest != current_digest:
            issues.append(issue(EvaluationIssueCode.NODE_CONTRACT_CHANGED, "node assessment contract provenance mismatch", f"node_assessments[{index}]"))
        if node.mastery_requirement_digest != current_mastery_digest:
            issues.append(issue(EvaluationIssueCode.MASTERY_REQUIREMENT_CONTRACT_CHANGED, "mastery requirement digest mismatch", f"node_assessments[{index}]"))
        if {o.objective_id: o.objective_digest for o in node.objective_contracts} != {o.objective_id: o.objective_digest for o in expected_objectives}:
            issues.append(issue(EvaluationIssueCode.OBJECTIVE_CONTRACT_CHANGED, "objective contract set mismatch", f"node_assessments[{index}]"))
        expected_by_id = {o.objective_id: o for o in expected_objectives}
        for objective in node.objective_assessments:
            expected = expected_by_id.get(objective.objective_id)
            if expected is None or objective.objective_digest != expected.objective_digest or objective.node_contract_digest != current_digest:
                issues.append(issue(EvaluationIssueCode.OBJECTIVE_SCORE_CONTRACT_MISMATCH, "objective assessment scope mismatch", f"node_assessments[{index}]"))
        valid_evidence = [evidence[x] for x in node.evidence_ids if x in evidence and evidence[x].state == EvidenceState.VALID]
        independent = len({e.canonical_activity_key for e in valid_evidence if e.independent})
        verified = sum(e.effective_verified for e in valid_evidence)
        issues.extend(_validate_cross_objective_result(node, evidence, contract))
        normalized_requirements, unresolved = normalize_mastery_requirements(contract)
        expected_requirement_ids = {r.requirement_id for r in normalized_requirements}
        actual_requirement_ids = {r.requirement_id for r in node.mastery_requirement_assessments}
        if expected_requirement_ids != actual_requirement_ids:
            issues.append(issue(EvaluationIssueCode.SECURE_WITH_INEXACT_REQUIREMENT, "result does not contain the exact normalized requirement set", f"node_assessments[{index}]"))
        if node.assessment_status == AssessmentStatus.SECURE:
            if not node.objective_assessments or not all(o.achieved for o in node.objective_assessments):
                issues.append(issue(EvaluationIssueCode.SECURE_WITH_UNACHIEVED_OBJECTIVE, "SECURE requires every mandatory objective to be achieved", f"node_assessments[{index}]"))
                issues.append(issue(EvaluationIssueCode.SECURE_WITH_UNCOVERED_OBJECTIVE, "legacy compatibility: SECURE objective coverage invalid", f"node_assessments[{index}]"))
            if any(o.achievement_status == ObjectiveAchievementStatus.COUNTER_EVIDENCE_ONLY for o in node.objective_assessments):
                issues.append(issue(EvaluationIssueCode.OBJECTIVE_SUPPORTED_ONLY_BY_COUNTER_EVIDENCE, "counter evidence cannot establish objective achievement", f"node_assessments[{index}]"))
            if unresolved or not node.mastery_requirement_assessments or not all(r.satisfied for r in node.mastery_requirement_assessments):
                issues.append(issue(EvaluationIssueCode.SECURE_WITH_UNSATISFIED_REQUIREMENT, "SECURE requires every exact mastery requirement", f"node_assessments[{index}]"))
                if unresolved:
                    issues.append(issue(EvaluationIssueCode.SECURE_WITH_INEXACT_REQUIREMENT, "SECURE cannot rely on unresolved mastery grammar", f"node_assessments[{index}]"))
            if independent < request.evaluation_policy.secure_min_independent_evidence:
                issues.append(issue(EvaluationIssueCode.SECURE_WITH_REPEATED_ACTIVITY, "SECURE lacks independent canonical activities", f"node_assessments[{index}]"))
            if verified < request.evaluation_policy.secure_min_verified_evidence:
                issues.append(issue(EvaluationIssueCode.SECURE_WITH_SPOOFED_TRUST, "SECURE lacks authority-verified evidence", f"node_assessments[{index}]"))
            if EvaluationDimension.TRANSFER in [d.dimension for d in node.dimension_assessments] and not any(
                d.dimension == EvaluationDimension.TRANSFER and d.score >= 0.65 for d in node.dimension_assessments
            ):
                issues.append(issue(EvaluationIssueCode.MISSING_TRANSFER_EVIDENCE, "SECURE lacks transfer evidence", f"node_assessments[{index}]"))
        unresolved_blocking = [m for m in node.misconceptions if not m.resolved and m.severity in {MisconceptionSeverity.MODERATE, MisconceptionSeverity.HIGH}]
        if node.completion_eligibility_signal == CompletionEligibility.ELIGIBLE:
            if node.assessment_status != AssessmentStatus.SECURE:
                issues.append(issue(EvaluationIssueCode.COMPLETION_SIGNAL_WITHOUT_EVIDENCE, "eligibility requires SECURE"))
            if not all(o.achieved for o in node.objective_assessments):
                issues.append(issue(EvaluationIssueCode.SECURE_WITH_UNACHIEVED_OBJECTIVE, "ELIGIBLE requires achieved objectives"))
            if not all(r.satisfied for r in node.mastery_requirement_assessments):
                issues.append(issue(EvaluationIssueCode.ELIGIBLE_WITH_UNSATISFIED_REQUIREMENT, "ELIGIBLE requires all mastery requirements"))
            if unresolved_blocking:
                issues.append(issue(EvaluationIssueCode.ELIGIBLE_WITH_UNRESOLVED_MISCONCEPTION, "unresolved moderate/high misconception blocks eligibility"))
        if result.fallback_used and (
            node.assessment_status == AssessmentStatus.SECURE or node.completion_eligibility_signal == CompletionEligibility.ELIGIBLE
        ):
            issues.append(issue(EvaluationIssueCode.FALLBACK_BOUNDARY_EXCEEDED, "fallback cannot emit SECURE or ELIGIBLE"))
        for misconception in node.misconceptions:
            if not set(misconception.evidence_ids) <= set(evidence):
                issues.append(issue(EvaluationIssueCode.MISCONCEPTION_WITHOUT_EVIDENCE, "misconception references missing evidence"))
                continue
            items = [evidence[x] for x in misconception.evidence_ids]
            activities = {(e.canonical_activity_key or e.evidence_id) for e in items if e.independent and _negative(e)}
            if len(activities) < 2 or len([e for e in items if _negative(e)]) < 2:
                issues.append(issue(EvaluationIssueCode.INVALID_MISCONCEPTION_SUPPORT, "misconception lacks independent negative support"))
    issues.extend(validate_public_view(result.public_view).issues)
    return ValidationReport.from_issues(issues)
