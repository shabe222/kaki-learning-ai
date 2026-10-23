from __future__ import annotations

import hashlib
from collections import defaultdict

from .candidate_validator import explicit_objective_score, objective_contract_for
from .confidence_engine import calculate_confidence
from .contradiction_detector import detect_contradictions, detect_evidence_quality_warnings
from .dimension_engine import build_dimension_assessments
from .mastery_requirements import assess_mastery_requirements, normalize_mastery_requirements
from .misconception_engine import deterministic_misconceptions, misconception_severity
from .models import *
from .objective_mapper import required_dimensions, objective_allowed_dimensions
from .serializer import build_public_view
from .contracts import authority_context_digest, node_contract_set_digest, node_contract_values
from .correction_invariants import neutralize_corrected_evidence


def stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:20]}"


def existing_to_normalized(existing: ExistingEvidenceSummary) -> NormalizedEvidence:
    # Preflight rejects missing canonical identity and semantic fields before this path.
    family = existing.evidence_family_key or stable_id("family", existing.evidence_type.value)
    canonical_item = existing.canonical_item_key or stable_id("item", existing.node_id, existing.evidence_type.value, existing.activity_key)
    canonical_activity = existing.canonical_activity_key or stable_id("activity", existing.node_id, existing.evidence_type.value, existing.activity_key)
    attempt = existing.attempt_group_key or stable_id("attempt", canonical_activity)
    normalized = NormalizedEvidence(
        evidence_id=existing.evidence_id,
        event_id=existing.event_id,
        node_id=existing.node_id,
        evidence_type=existing.evidence_type,
        occurred_at=existing.occurred_at,
        state=existing.state,
        normalized_score=existing.normalized_score,
        effective_weight=existing.effective_weight,
        dimensions=existing.dimensions,
        independent=existing.independent,
        trust_level=existing.trust_level,
        effective_verified=bool(existing.effective_verified),
        subject_ref=existing.subject_ref or "missing-subject",
        graph_id=existing.graph_id or "missing-graph",
        graph_continuity_key=existing.graph_continuity_key or "missing-graph-continuity",
        source_system=existing.source_system,
        activity_key=existing.activity_key,
        source_fingerprint=existing.source_fingerprint,
        evidence_family_key=family,
        canonical_activity_key=canonical_activity,
        canonical_item_key=canonical_item,
        attempt_group_key=attempt,
        weak=bool(existing.weak),
        transfer=bool(existing.transfer),
        hinted=bool(existing.hinted),
        error_codes=list(existing.error_codes or []),
        objective_scores=dict(existing.objective_scores),
        objective_rubric_scores=list(existing.objective_rubric_scores),
        negative_signal_codes=list(existing.negative_signal_codes or []),
        misconception_pattern_codes=list(existing.misconception_pattern_codes or existing.error_codes or []),
        quality_warning_codes=list(existing.quality_warning_codes or []),
        correction_history=list(existing.correction_history),
    )
    return neutralize_corrected_evidence(normalized)


def _ledger_entry(evidence: NormalizedEvidence, request: LearningEvaluationRequest) -> EvidenceLedgerEntry:
    age = (request.evaluated_at - evidence.occurred_at).days
    band = "recent" if age <= 30 else "aging" if age <= request.evaluation_policy.stale_after_days else "stale"
    return EvidenceLedgerEntry(
        evidence_id=evidence.evidence_id,
        event_id=evidence.event_id,
        node_id=evidence.node_id,
        evidence_type=evidence.evidence_type,
        occurred_at=evidence.occurred_at,
        state=evidence.state,
        normalized_score=evidence.normalized_score,
        effective_weight=evidence.effective_weight,
        dimensions=evidence.dimensions,
        independent=evidence.independent,
        trust_level=evidence.trust_level,
        effective_verified=evidence.effective_verified,
        subject_ref=evidence.subject_ref,
        graph_id=evidence.graph_id,
        graph_continuity_key=evidence.graph_continuity_key,
        source_system=evidence.source_system,
        recency_band=band,
        staleness_warning=band == "stale",
        source_fingerprint=evidence.source_fingerprint,
        evidence_family_key=evidence.evidence_family_key,
        canonical_activity_key=evidence.canonical_activity_key,
        canonical_item_key=evidence.canonical_item_key,
        attempt_group_key=evidence.attempt_group_key,
        weak=evidence.weak,
        transfer=evidence.transfer,
        hinted=evidence.hinted,
        error_codes=evidence.error_codes,
        objective_scores=evidence.objective_scores,
        objective_rubric_scores=evidence.objective_rubric_scores,
        negative_signal_codes=evidence.negative_signal_codes,
        misconception_pattern_codes=evidence.misconception_pattern_codes,
        quality_warning_codes=evidence.quality_warning_codes,
        correction_history=evidence.correction_history,
    )


def _candidate_misconceptions(candidate: EvaluationCandidate, evidence_map: dict[str, NormalizedEvidence], evaluated_at) -> list[Misconception]:
    out: list[Misconception] = []
    for proposed in candidate.misconceptions:
        items = [evidence_map[x] for x in dict.fromkeys(proposed.evidence_ids) if x in evidence_map]
        if not items:
            continue
        negative = [e for e in items if e.normalized_score < 0.6 or e.error_codes or e.negative_signal_codes]
        independent = {e.canonical_activity_key for e in negative if e.independent}
        trusted = any(e.trust_level in {TrustLevel.VERIFIED, TrustLevel.SYSTEM_OBSERVED, TrustLevel.STRUCTURED_HUMAN} for e in negative)
        cap = min(0.8, 0.25 + 0.15 * len(independent) + (0.1 if trusted else 0))
        out.append(
            Misconception(
                code=proposed.code,
                node_id=items[0].node_id,
                summary=proposed.summary,
                evidence_ids=[e.evidence_id for e in negative],
                confidence=min(proposed.confidence, cap),
                severity=misconception_severity(negative, evaluated_at),
            )
        )
    return out


def _existing_node_summary(request: LearningEvaluationRequest, node_id: str) -> ExistingNodeAssessmentSummary | None:
    if not request.existing_evaluation_summary:
        return None
    return next((n for n in request.existing_evaluation_summary.node_assessments if n.node_id == node_id), None)


def _objective_assessments(
    request: LearningEvaluationRequest,
    node: NodeEvaluationContract,
    node_evidence: list[NormalizedEvidence],
    candidate: EvaluationCandidate,
) -> list[ObjectiveAssessment]:
    mappings = {m.objective_index: m for m in candidate.objective_mappings if m.node_id == node.node_id}
    existing_node = _existing_node_summary(request, node.node_id)
    existing_by_id = {
        o.objective_id: o for o in existing_node.objective_assessments
        if existing_node and o.objective_id is not None
    } if existing_node else {}
    evidence_map = {e.evidence_id: e for e in node_evidence if e.state == EvidenceState.VALID}
    used_evidence: set[str] = set()
    used_activities: set[str] = set()
    out: list[ObjectiveAssessment] = []
    threshold = request.evaluation_policy.objective_achievement_threshold
    node_digest, _, raw_contracts = node_contract_values(
        node, required_dimensions(node.node_type), objective_allowed_dimensions
    )
    contracts = [ObjectiveEvaluationContract(**x) for x in raw_contracts]
    for index, objective in enumerate(node.learning_objectives):
        contract = contracts[index]
        mapping = mappings.get(index)
        old = existing_by_id.get(contract.objective_id)
        old_valid = bool(
            old
            and old.objective_digest == contract.objective_digest
            and old.node_contract_digest == contract.node_contract_digest
            and request.evaluation_mode == EvaluationMode.INCREMENTAL
        )
        source_ids = list(mapping.evidence_ids) if mapping else list(old.evidence_ids if old_valid else [])
        rationale = "OBJECTIVE_MAPPING_MISSING"
        confidence = 0.0
        supporting: list[str] = []
        counter: list[str] = []
        all_ids: list[str] = []
        scores: list[float] = []
        allowed = set(contract.required_dimensions)
        for evidence_id in dict.fromkeys(source_ids):
            item = evidence_map.get(evidence_id)
            if not item or item.weak or item.effective_weight < 0.25:
                continue
            if item.trust_level in {TrustLevel.INVALID, TrustLevel.INFERRED, TrustLevel.SELF_REPORTED}:
                continue
            explicit = explicit_objective_score(item, node, index)
            if item.objective_rubric_scores and explicit is None:
                continue
            if explicit is None and not set(item.dimensions).intersection(allowed):
                continue
            if (evidence_id in used_evidence or item.canonical_activity_key in used_activities) and explicit is None:
                continue
            score = explicit if explicit is not None else item.normalized_score
            all_ids.append(evidence_id)
            scores.append(score)
            if score >= threshold and not item.error_codes and not item.negative_signal_codes:
                supporting.append(evidence_id)
            if score < 0.6 or item.error_codes or item.negative_signal_codes:
                counter.append(evidence_id)
            if explicit is None:
                used_evidence.add(evidence_id)
                used_activities.add(item.canonical_activity_key)
        assessed = bool(all_ids)
        achievement_score = round(sum(scores) / len(scores), 4) if scores else 0.0
        strong_counter = any(
            evidence_map[eid].normalized_score < 0.4
            and evidence_map[eid].trust_level in {TrustLevel.VERIFIED, TrustLevel.SYSTEM_OBSERVED, TrustLevel.STRUCTURED_HUMAN}
            for eid in counter
        )
        achieved = bool(supporting) and achievement_score >= threshold and not strong_counter
        if supporting and counter and strong_counter:
            status = ObjectiveAchievementStatus.CONTRADICTORY
            rationale = "OBJECTIVE_CONTRADICTORY_EVIDENCE"
        elif achieved:
            status = ObjectiveAchievementStatus.ACHIEVED
            rationale = "OBJECTIVE_ACHIEVEMENT_VALIDATED"
        elif supporting:
            status = ObjectiveAchievementStatus.PARTIALLY_SUPPORTED
            rationale = "OBJECTIVE_PARTIALLY_SUPPORTED"
        elif counter:
            status = ObjectiveAchievementStatus.COUNTER_EVIDENCE_ONLY
            rationale = "OBJECTIVE_SUPPORTED_ONLY_BY_COUNTER_EVIDENCE"
        else:
            status = ObjectiveAchievementStatus.UNASSESSED
            rationale = "OBJECTIVE_MAPPING_INVALID" if mapping else ("OBJECTIVE_CONTRACT_CHANGED" if old and not old_valid else "OBJECTIVE_MAPPING_MISSING")
        if mapping:
            confidence = min(mapping.coverage_confidence, 0.9 if len(supporting) >= 2 else 0.7 if supporting else 0.45 if assessed else 0.0)
        elif old_valid:
            confidence = old.coverage_confidence
        out.append(
            ObjectiveAssessment(
                objective_index=index,
                objective=objective,
                objective_id=contract.objective_id,
                objective_digest=contract.objective_digest,
                node_contract_digest=node_digest,
                covered=assessed,
                assessed=assessed,
                achieved=achieved,
                achievement_status=status,
                achievement_score=achievement_score,
                evidence_ids=all_ids,
                supporting_evidence_ids=supporting,
                counter_evidence_ids=counter,
                coverage_rationale=rationale,
                coverage_confidence=confidence,
            )
        )
    return out


def _merge_misconceptions(
    request: LearningEvaluationRequest,
    node_id: str,
    deterministic: list[Misconception],
    proposed: list[Misconception],
    evidence_map: dict[str, NormalizedEvidence],
    new_evidence_ids: set[str],
) -> list[Misconception]:
    current: dict[str, Misconception] = {}
    for misconception in deterministic + proposed:
        current.setdefault(misconception.code, misconception)
    if request.existing_evaluation_summary:
        resolved_codes = {
            old.code
            for old in request.existing_evaluation_summary.misconceptions
            if old.node_id == node_id and old.resolved
        }
        # A resolved historical pattern stays resolved when no new negative fact
        # supports it. New post-resolution negative evidence may reopen it.
        for code in list(current):
            if code in resolved_codes and not (set(current[code].evidence_ids) & new_evidence_ids):
                current.pop(code, None)
        for old in request.existing_evaluation_summary.misconceptions:
            if old.node_id != node_id or old.resolved or old.code in current:
                continue
            support = [evidence_map[eid] for eid in old.evidence_ids if eid in evidence_map and evidence_map[eid].state == EvidenceState.VALID]
            negative_activities = {e.canonical_activity_key for e in support if e.independent and (e.normalized_score < 0.6 or e.error_codes or e.negative_signal_codes)}
            if len(negative_activities) >= 2:
                current[old.code] = Misconception(
                    code=old.code,
                    node_id=node_id,
                    summary=old.summary or f"历史错误模式：{old.code}",
                    evidence_ids=[e.evidence_id for e in support],
                    confidence=old.confidence,
                    severity=max(old.severity, misconception_severity(support, request.evaluated_at), key=lambda x: {MisconceptionSeverity.LOW: 1, MisconceptionSeverity.MODERATE: 2, MisconceptionSeverity.HIGH: 3}[x]),
                )
    return list(current.values())


def compile_evaluation(
    request: LearningEvaluationRequest,
    new_evidence: list[NormalizedEvidence],
    candidate: EvaluationCandidate,
    preflight_warnings: list[ValidationIssue] | None = None,
    trusted_context: TrustedEvaluationContext | None = None,
    trusted_correction_context: TrustedCorrectionContext | None = None,
) -> LearningEvaluationResult:
    if not isinstance(trusted_context, TrustedEvaluationContext):
        raise ValueError("MISSING_TRUSTED_EVALUATION_CONTEXT")
    all_evidence: list[NormalizedEvidence] = []
    if request.existing_evaluation_summary:
        all_evidence.extend(existing_to_normalized(e) for e in request.existing_evaluation_summary.evidence_ledger)
    existing_ids = {e.evidence_id for e in all_evidence}
    all_evidence.extend(e for e in new_evidence if e.evidence_id not in existing_ids)
    corrections = {c.evidence_id: c for c in request.evidence_corrections}
    newly_invalidated: list[str] = []
    for item in all_evidence:
        intent = corrections.get(item.evidence_id)
        if intent is None:
            continue
        if trusted_correction_context is None:
            raise ValueError("MISSING_TRUSTED_CORRECTION_CONTEXT")
        event_id = trusted_correction_context.correction_event_ids[intent.idempotency_key]
        existing_event = next((x for x in item.correction_history if x.correction_event_id == event_id), None)
        audit = EvidenceCorrectionAuditEntry(
            correction_event_id=event_id,
            reason=intent.requested_reason,
            actor_ref=trusted_correction_context.authenticated_actor_ref,
            actor_role=trusted_correction_context.actor_role,
            corrected_at=trusted_correction_context.corrected_at,
            contract_version=trusted_correction_context.correction_contract_version,
            source=trusted_correction_context.correction_source,
            idempotency_key=intent.idempotency_key,
            supersedes_correction_event_id=trusted_correction_context.supersedes_correction_event_ids.get(intent.idempotency_key),
        )
        if existing_event is not None:
            if existing_event != audit:
                raise ValueError("CORRECTION_STATE_CONFLICT")
        else:
            item.correction_history.append(audit)
            newly_invalidated.append(item.evidence_id)
        item.state = EvidenceState.INVALIDATED
        item.effective_weight = 0
        item.independent = False
        item.effective_verified = False
    all_evidence.sort(key=lambda x: (x.occurred_at, x.evidence_id))
    evidence_map = {e.evidence_id: e for e in all_evidence}
    candidate_mis = _candidate_misconceptions(candidate, evidence_map, request.evaluated_at)
    node_assessments: list[NodeAssessment] = []
    all_contradictions: list[Contradiction] = []
    all_quality: list[EvidenceQualityWarning] = []
    all_actions: list[RecommendedEvaluationAction] = []
    warnings: list[str] = []
    if preflight_warnings:
        warnings.extend(i.code for i in preflight_warnings if i.severity != Severity.INFO)
    for node in request.node_contracts:
        node_evidence = [e for e in all_evidence if e.node_id == node.node_id and e.state == EvidenceState.VALID]
        dimensions = build_dimension_assessments(node, node_evidence)
        contradictions = detect_contradictions(node.node_id, node_evidence)
        quality = detect_evidence_quality_warnings(node.node_id, node_evidence)
        all_contradictions.extend(contradictions)
        all_quality.extend(quality)
        deterministic = deterministic_misconceptions(node.node_id, node_evidence, request.evaluated_at)
        proposed = [m for m in candidate_mis if m.node_id == node.node_id]
        misconceptions = _merge_misconceptions(
            request,
            node.node_id,
            deterministic,
            proposed,
            evidence_map,
            {e.evidence_id for e in new_evidence},
        )
        confidence = calculate_confidence(node_evidence, dimensions, request.evaluated_at, request.evaluation_policy, len(contradictions))
        required = required_dimensions(node.node_type)
        dimension_covered = sum(not d.missing_evidence for d in dimensions)
        independent = len({e.canonical_activity_key for e in node_evidence if e.independent})
        trusted = sum(e.effective_verified or e.trust_level in {TrustLevel.SYSTEM_OBSERVED, TrustLevel.STRUCTURED_HUMAN} for e in node_evidence)
        sufficiency = 0.5 * (dimension_covered / max(1, len(required))) + 0.25 * min(1, independent / 3) + 0.25 * min(1, trusted / 3)
        dimension_scores = [d.score for d in dimensions if not d.missing_evidence]
        mastery = sum(dimension_scores) / len(dimension_scores) if dimension_scores else 0.0
        if not node_evidence:
            mastery = 0
        elif all(e.weak for e in node_evidence):
            mastery = min(mastery, request.evaluation_policy.weak_evidence_mastery_cap)
        elif len([e for e in node_evidence if not e.weak]) == 1:
            mastery = min(mastery, request.evaluation_policy.single_evidence_mastery_cap)
        if independent < 2:
            mastery = min(mastery, 0.58)
        transfer_required = EvaluationDimension.TRANSFER in required
        transfer_good = any(e.transfer and e.normalized_score >= 0.65 and e.independent for e in node_evidence)
        if transfer_required and not transfer_good:
            mastery = min(mastery, 0.74)
        if contradictions:
            mastery = min(mastery, 0.68)
        objectives = _objective_assessments(request, node, node_evidence, candidate)
        normalized_requirements, unresolved = normalize_mastery_requirements(node)
        requirement_assessments = assess_mastery_requirements(normalized_requirements, node_evidence, objectives)
        all_objectives_achieved = bool(objectives) and all(o.achieved for o in objectives)
        all_requirements = bool(requirement_assessments) and all(r.satisfied for r in requirement_assessments)
        if candidate.fallback_used:
            mastery = min(mastery, 0.79)
        high_misconception = any(not m.resolved and m.severity == MisconceptionSeverity.HIGH for m in misconceptions)
        moderate_misconception = any(not m.resolved and m.severity == MisconceptionSeverity.MODERATE for m in misconceptions)
        if high_misconception:
            mastery = min(mastery, 0.58)
        elif moderate_misconception:
            mastery = min(mastery, 0.79)
        mastery = round(max(0, min(1, mastery)), 4)
        sufficiency = round(max(0, min(1, sufficiency)), 4)
        severe_contradiction = any(c.severity in {Severity.ERROR, Severity.REVIEW_REQUIRED} for c in contradictions)
        secure = (
            not candidate.fallback_used
            and not unresolved
            and all_objectives_achieved
            and all_requirements
            and mastery >= 0.82
            and confidence >= 0.7
            and sufficiency >= 0.75
            and independent >= request.evaluation_policy.secure_min_independent_evidence
            and trusted >= request.evaluation_policy.secure_min_verified_evidence
            and (not transfer_required or transfer_good)
            and not contradictions
            and not high_misconception
            and not moderate_misconception
        )
        if not node_evidence:
            status = AssessmentStatus.INSUFFICIENT_EVIDENCE
        elif unresolved or high_misconception:
            status = AssessmentStatus.NEEDS_REVIEW
        elif severe_contradiction:
            status = AssessmentStatus.CONTRADICTORY
        elif secure:
            status = AssessmentStatus.SECURE
        elif mastery >= 0.68 and sufficiency >= 0.55:
            status = AssessmentStatus.PROFICIENT
        elif mastery >= 0.45:
            status = AssessmentStatus.DEVELOPING
        else:
            status = AssessmentStatus.EMERGING
        if moderate_misconception and status == AssessmentStatus.SECURE:
            status = AssessmentStatus.PROFICIENT
        gaps = [f"missing:{d.dimension.value}" for d in dimensions if d.missing_evidence]
        if not all_objectives_achieved:
            gaps.append("unachieved_objective")
        if not all_requirements:
            gaps.append("unsatisfied_mastery_requirement")
        if unresolved:
            gaps.append("unresolved_mastery_requirement")
        if independent < 2:
            gaps.append("insufficient_independent_evidence")
        if transfer_required and not transfer_good:
            gaps.append("missing_transfer_evidence")
        if high_misconception or moderate_misconception:
            gaps.append("unresolved_misconception")
        actions = [RecommendedEvaluationAction(**a.model_dump()) for a in candidate.recommended_actions if a.target_node_id == node.node_id]
        if not actions and gaps:
            actions = [
                RecommendedEvaluationAction(
                    action_type=ActionType.MANUAL_REVIEW if unresolved or high_misconception else ActionType.MISCONCEPTION_CHECK if moderate_misconception else ActionType.TRANSFER_TASK if "missing_transfer_evidence" in gaps else ActionType.COLLECT_MORE_EVIDENCE,
                    target_node_id=node.node_id,
                    reason_codes=["UNRESOLVED_MASTERY_REQUIREMENT" if unresolved else "UNRESOLVED_MISCONCEPTION" if high_misconception or moderate_misconception else "EVIDENCE_GAP"],
                    required_evidence_dimension=EvaluationDimension.TRANSFER if "missing_transfer_evidence" in gaps else None,
                    priority=1 if high_misconception else 2,
                )
            ]
        all_actions.extend(actions)
        eligibility = (
            CompletionEligibility.ELIGIBLE
            if secure
            else CompletionEligibility.REVIEW_REQUIRED
            if severe_contradiction or unresolved or high_misconception
            else CompletionEligibility.MORE_EVIDENCE_REQUIRED
        )
        if moderate_misconception:
            eligibility = CompletionEligibility.MORE_EVIDENCE_REQUIRED
        if candidate.fallback_used and eligibility != CompletionEligibility.REVIEW_REQUIRED:
            eligibility = CompletionEligibility.MORE_EVIDENCE_REQUIRED
        supported = [f"{d.dimension.value}:{d.score_band.value}" for d in dimensions if d.score_band == ScoreBand.HIGH]
        unsupported = [f"{d.dimension.value}:insufficient" for d in dimensions if d.missing_evidence]
        node_digest, mastery_digest, raw_objective_contracts = node_contract_values(
            node, required_dimensions(node.node_type), objective_allowed_dimensions
        )
        objective_contracts = [ObjectiveEvaluationContract(**x) for x in raw_objective_contracts]
        node_assessments.append(
            NodeAssessment(
                node_id=node.node_id,
                node_continuity_key=node.node_continuity_key,
                node_contract_revision=node.node_contract_revision,
                node_contract_digest=node_digest,
                mastery_requirement_digest=mastery_digest,
                objective_contracts=objective_contracts,
                assessment_status=status,
                mastery_signal=mastery,
                confidence=confidence,
                evidence_sufficiency=sufficiency,
                dimension_assessments=dimensions,
                objective_assessments=objectives,
                mastery_requirement_assessments=requirement_assessments,
                supported_claims=supported,
                unsupported_claims=unsupported,
                misconceptions=misconceptions,
                contradictions=contradictions,
                evidence_quality_warnings=quality,
                evidence_gaps=gaps,
                completion_eligibility_signal=eligibility,
                recommended_evaluation_actions=actions,
                evidence_ids=[e.evidence_id for e in node_evidence],
                needs_review=unresolved or severe_contradiction or high_misconception,
            )
        )
    evaluation_id = request.existing_evaluation_summary.evaluation_id if request.existing_evaluation_summary else stable_id("eval", request.subject_ref, request.graph_ref.graph_id, request.graph_ref.graph_continuity_key)
    revision = request.existing_evaluation_summary.evaluation_revision + 1 if request.existing_evaluation_summary else 1
    ledger = [_ledger_entry(e, request) for e in all_evidence]
    old_nodes = {n.node_id: n for n in request.existing_evaluation_summary.node_assessments} if request.existing_evaluation_summary else {}
    current_mis = {m.code for n in node_assessments for m in n.misconceptions if not m.resolved}
    current_con = {c.contradiction_code for c in all_contradictions}
    old_mis = {m.code for m in request.existing_evaluation_summary.misconceptions if not m.resolved} if request.existing_evaluation_summary else set()
    old_con = {c.contradiction_code for c in request.existing_evaluation_summary.contradictions if not c.resolved} if request.existing_evaluation_summary else set()
    changes = EvaluationChangeSet(
        added_evidence_ids=[e.evidence_id for e in new_evidence if e.evidence_id not in existing_ids],
        invalidated_evidence_ids=newly_invalidated,
        updated_node_assessments=[n.node_id for n in node_assessments],
        new_misconceptions=sorted(current_mis - old_mis),
        resolved_misconceptions=sorted(old_mis - current_mis),
        new_contradictions=sorted(current_con - old_con),
        resolved_contradictions=sorted(old_con - current_con),
        mastery_signal_changes={n.node_id: round(n.mastery_signal - old_nodes[n.node_id].mastery_signal, 4) for n in node_assessments if n.node_id in old_nodes},
        confidence_changes={n.node_id: round(n.confidence - old_nodes[n.node_id].confidence, 4) for n in node_assessments if n.node_id in old_nodes},
    )
    public = build_public_view(evaluation_id, revision, request, node_assessments, ledger, warnings, all_actions)
    authority_digest = authority_context_digest(trusted_context)
    contract_set_digest = node_contract_set_digest(request.node_contracts, required_dimensions, objective_allowed_dimensions)
    return LearningEvaluationResult(
        evaluation_id=evaluation_id,
        evaluation_revision=revision,
        request_id=request.request_id,
        subject_ref=trusted_context.authenticated_subject_ref,
        graph_id=request.graph_ref.graph_id,
        graph_revision=request.graph_ref.graph_revision,
        graph_continuity_key=request.graph_ref.graph_continuity_key,
        trusted_context_version=trusted_context.context_version,
        authority_context_digest=authority_digest,
        node_contract_set_digest=contract_set_digest,
        evaluated_at=request.evaluated_at,
        evaluation_mode=request.evaluation_mode,
        node_assessments=node_assessments,
        evidence_ledger=ledger,
        contradictions=all_contradictions,
        evidence_quality_warnings=all_quality,
        global_warnings=warnings,
        next_evaluation_actions=all_actions,
        change_set=changes,
        validation_report=ValidationReport(valid=True),
        public_view=public,
        private_payload={
            "normalized_evidence": [e.model_dump(mode="json") for e in all_evidence],
            "candidate": candidate.model_dump(mode="json"),
            "normalized_mastery_requirements": {n.node_id: [r.model_dump(mode="json") for r in normalize_mastery_requirements(n)[0]] for n in request.node_contracts},
        },
        provenance={
            "module": "AGENT-LEARNING-EVALUATOR-01",
            "version": "0.1.10",
            "subject_ref": trusted_context.authenticated_subject_ref,
            "graph_continuity_key": request.graph_ref.graph_continuity_key,
            "trusted_context_version": trusted_context.context_version,
            "authority_context_digest": authority_digest,
            "node_contract_set_digest": contract_set_digest,
        },
        fallback_used=candidate.fallback_used,
    )
