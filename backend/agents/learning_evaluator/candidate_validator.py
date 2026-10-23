from __future__ import annotations

from collections import defaultdict

from .evidence_validator import issue
from .models import *
from .objective_mapper import required_dimensions, objective_allowed_dimensions
from .contracts import node_contract_values
from .closed_codes import is_registered_misconception_code, unknown_candidate_reason_codes


from .execution_codes import EvaluationIssueCode
def objective_contract_for(node: NodeEvaluationContract, objective_index: int) -> ObjectiveEvaluationContract:
    digest, _, raw = node_contract_values(node, required_dimensions(node.node_type), objective_allowed_dimensions)
    return ObjectiveEvaluationContract(**raw[objective_index])


def explicit_objective_score(evidence: NormalizedEvidence, node: NodeEvaluationContract, objective_index: int) -> float | None:
    contract = objective_contract_for(node, objective_index)
    matches = [
        score for score in evidence.objective_rubric_scores
        if score.objective_id == contract.objective_id
        and score.node_contract_digest == contract.node_contract_digest
        and score.objective_digest == contract.objective_digest
        and score.rubric_version
        and score.assessment_contract_ref
    ]
    if len(matches) > 1:
        return None
    return matches[0].score if matches else None


def validate_cross_objective_evidence_allocation(request: LearningEvaluationRequest, candidate: EvaluationCandidate, evidence: list[NormalizedEvidence]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    nodes = {n.node_id: n for n in request.node_contracts}
    ev = {e.evidence_id: e for e in evidence}
    evidence_allocations: dict[tuple[str, str], list[int]] = defaultdict(list)
    activity_allocations: dict[tuple[str, str], list[int]] = defaultdict(list)
    for mapping in candidate.objective_mappings:
        node = nodes.get(mapping.node_id)
        if node is None or mapping.objective_index >= len(node.learning_objectives):
            continue
        objective = node.learning_objectives[mapping.objective_index]
        for evidence_id in dict.fromkeys(mapping.evidence_ids):
            item = ev.get(evidence_id)
            if item is None:
                continue
            evidence_allocations[(mapping.node_id, evidence_id)].append(mapping.objective_index)
            activity_allocations[(mapping.node_id, item.canonical_activity_key)].append(mapping.objective_index)
    for (node_id, evidence_id), indices in evidence_allocations.items():
        distinct = sorted(set(indices))
        if len(distinct) <= 1:
            continue
        item = ev[evidence_id]
        node = nodes[node_id]
        explicitly_scoped = all(explicit_objective_score(item, node, idx) is not None for idx in distinct)
        if not explicitly_scoped:
            issues.append(issue(EvaluationIssueCode.OBJECTIVE_EVIDENCE_REUSED_ACROSS_OBJECTIVES, "the same evidence cannot be primary support for multiple objectives without trusted objective-specific scores", evidence_id, True))
    for (node_id, activity_key), indices in activity_allocations.items():
        distinct = sorted(set(indices))
        if len(distinct) <= 1:
            continue
        mapped_items = [ev[eid] for m in candidate.objective_mappings if m.node_id == node_id for eid in m.evidence_ids if eid in ev and ev[eid].canonical_activity_key == activity_key]
        node = nodes[node_id]
        explicitly_scoped = mapped_items and all(
            all(explicit_objective_score(item, node, idx) is not None for idx in distinct)
            for item in mapped_items
        )
        if not explicitly_scoped:
            issues.append(issue(EvaluationIssueCode.OBJECTIVE_ACTIVITY_REUSED_ACROSS_OBJECTIVES, "the same canonical activity cannot independently achieve multiple objectives", activity_key, True))
    return issues


def validate_objective_mapping(request: LearningEvaluationRequest, candidate: EvaluationCandidate, evidence: list[NormalizedEvidence]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    nodes = {n.node_id: n for n in request.node_contracts}
    ev = {e.evidence_id: e for e in evidence}
    seen: set[tuple[str, int]] = set()
    for index, mapping in enumerate(candidate.objective_mappings):
        path = f"objective_mappings[{index}]"
        key = (mapping.node_id, mapping.objective_index)
        if key in seen:
            issues.append(issue(EvaluationIssueCode.OBJECTIVE_MAPPING_DUPLICATE_EVIDENCE, "duplicate objective mapping", path, True))
        seen.add(key)
        if mapping.node_id not in nodes:
            issues.append(issue(EvaluationIssueCode.UNKNOWN_NODE_REFERENCE, "objective mapping node missing", path, True))
            continue
        node = nodes[mapping.node_id]
        if mapping.objective_index >= len(node.learning_objectives):
            issues.append(issue(EvaluationIssueCode.INVALID_OBJECTIVE_INDEX, "objective index outside contract", path, True))
            continue
        ids = list(dict.fromkeys(mapping.evidence_ids))
        if len(ids) != len(mapping.evidence_ids):
            issues.append(issue(EvaluationIssueCode.OBJECTIVE_MAPPING_DUPLICATE_EVIDENCE, "mapping repeats evidence ID", path, True))
        if not ids:
            issues.append(issue(EvaluationIssueCode.OBJECTIVE_MAPPING_MISSING, "objective mapping requires evidence", path, True))
            continue
        objective = node.learning_objectives[mapping.objective_index]
        allowed = objective_allowed_dimensions(node, objective)
        activities: set[str] = set()
        for evidence_id in ids:
            if evidence_id not in ev:
                issues.append(issue(EvaluationIssueCode.UNKNOWN_EVIDENCE_REFERENCE, "objective mapping evidence missing", path, True))
                continue
            item = ev[evidence_id]
            if item.node_id != mapping.node_id:
                issues.append(issue(EvaluationIssueCode.EVIDENCE_NODE_MISMATCH, "objective mapping crosses node", path, False))
                continue
            if item.state != EvidenceState.VALID or item.weak or item.effective_weight < 0.25:
                issues.append(issue(EvaluationIssueCode.OBJECTIVE_MAPPING_WEAK_EVIDENCE, "objective mapping uses weak or invalid evidence", path, True))
            explicit_score = explicit_objective_score(item, node, mapping.objective_index)
            if item.objective_rubric_scores and explicit_score is None:
                issues.append(issue(EvaluationIssueCode.OBJECTIVE_SCORE_CONTRACT_MISMATCH, "objective-specific rubric score does not match current objective contract", path, True))
                continue
            if explicit_score is None and not set(item.dimensions).intersection(allowed):
                issues.append(issue(EvaluationIssueCode.OBJECTIVE_MAPPING_WRONG_DIMENSION, "evidence dimensions do not support objective", path, True))
            if item.trust_level in {TrustLevel.INVALID, TrustLevel.INFERRED, TrustLevel.SELF_REPORTED}:
                issues.append(issue(EvaluationIssueCode.OBJECTIVE_MAPPING_UNTRUSTED_EVIDENCE, "evidence authority too low", path, True))
            if item.canonical_activity_key in activities:
                issues.append(issue(EvaluationIssueCode.OBJECTIVE_MAPPING_REPEATED_ACTIVITY, "mapping repeats canonical activity", path, True))
            activities.add(item.canonical_activity_key)
    issues.extend(validate_cross_objective_evidence_allocation(request, candidate, evidence))
    return issues


def _negative_support(evidence: NormalizedEvidence) -> bool:
    return evidence.state == EvidenceState.VALID and (
        evidence.normalized_score < 0.6
        or bool(evidence.error_codes)
        or bool(evidence.negative_signal_codes)
        or (
            evidence.safe_summary is not None
            and any(k in evidence.safe_summary.lower() for k in ("错误", "混淆", "遗漏", "不正确", "failed", "wrong"))
        )
    )


def validate_candidate_assessment(request: LearningEvaluationRequest, candidate: EvaluationCandidate, evidence: list[NormalizedEvidence]) -> ValidationReport:
    issues: list[ValidationIssue] = []
    nodes = {n.node_id: n for n in request.node_contracts}
    ev = {e.evidence_id: e for e in evidence}
    seen_mis: set[str] = set()
    for index, misconception in enumerate(candidate.misconceptions):
        path = f"misconceptions[{index}]"
        if not is_registered_misconception_code(misconception.code):
            issues.append(issue(EvaluationIssueCode.UNKNOWN_MISCONCEPTION_CODE, "provider misconception code is not registered", path, True))
        if misconception.code in seen_mis:
            issues.append(issue(EvaluationIssueCode.DUPLICATE_MISCONCEPTION, "duplicate misconception", path, True))
        seen_mis.add(misconception.code)
        ids = list(dict.fromkeys(misconception.evidence_ids))
        if len(ids) < 2:
            issues.append(issue(EvaluationIssueCode.MISCONCEPTION_WITHOUT_EVIDENCE, "misconception requires two distinct evidence records", path, True))
        missing = [x for x in ids if x not in ev]
        if missing:
            issues.append(issue(EvaluationIssueCode.UNSUPPORTED_MISCONCEPTION, "misconception references unknown evidence", path, True))
            continue
        items = [ev[x] for x in ids]
        if len({x.node_id for x in items}) > 1:
            issues.append(issue(EvaluationIssueCode.UNSUPPORTED_MISCONCEPTION, "misconception evidence crosses nodes", path, True))
        negative = [x for x in items if _negative_support(x)]
        if len(negative) < 2:
            issues.append(issue(EvaluationIssueCode.MISCONCEPTION_SUPPORTED_ONLY_BY_POSITIVE_EVIDENCE, "misconception requires negative evidence", path, True))
        activities = {x.canonical_activity_key for x in negative if x.independent}
        if len(activities) < 2:
            issues.append(issue(EvaluationIssueCode.UNSUPPORTED_MISCONCEPTION, "misconception requires two independent negative activities", path, True))
        trusted = any(x.trust_level in {TrustLevel.VERIFIED, TrustLevel.SYSTEM_OBSERVED, TrustLevel.STRUCTURED_HUMAN} for x in negative)
        if misconception.confidence > 0.35 and (len(activities) < 2 or not trusted):
            issues.append(issue(EvaluationIssueCode.UNSUPPORTED_MISCONCEPTION, "high-confidence misconception lacks trusted independent negative support", path, True))
    issues.extend(validate_objective_mapping(request, candidate, evidence))
    for index, action in enumerate(candidate.recommended_actions):
        path = f"recommended_actions[{index}]"
        if action.target_node_id not in nodes:
            issues.append(issue(EvaluationIssueCode.UNKNOWN_NODE_REFERENCE, "action node missing", path, True))
        if unknown_candidate_reason_codes(action.reason_codes):
            issues.append(issue(EvaluationIssueCode.UNKNOWN_CANDIDATE_REASON_CODE, "provider reason code is not registered", path, True))
    return ValidationReport.from_issues(issues)
