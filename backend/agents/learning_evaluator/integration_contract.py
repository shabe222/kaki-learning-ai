from __future__ import annotations

import copy
from typing import Protocol

from .contracts import authority_context_digest
from .correction_invariants import validate_correction_history
from .evidence_validator import EvaluationContractError, issue
from .models import *
from .execution_codes import EvaluationIssueCode
from .result_authenticity import (
    EvaluationResultAuthenticityPort,
    EvaluationResultEnvelope,
    canonical_result_digest,
)
from .result_validator import validate_evaluation_result, validate_public_view


class GraphNodeContractPort(Protocol): ...
class WorkspaceLearningEventPort(Protocol): ...
class GeneratedItemEvidencePort(Protocol): ...
class QuestionAttemptPort(Protocol): ...
class AssessmentResultPort(Protocol): ...
class CodeExecutionPort(Protocol): ...
class AdventureEvidencePort(Protocol): ...
class NoteEvidencePort(Protocol): ...
class DialogueEvidencePort(Protocol): ...
class EvaluationPersistencePort(Protocol): ...


class LearnerStateSignalPort(Protocol):
    def build_signals(self, envelope: EvaluationResultEnvelope) -> list[dict]: ...


def _validate_projection_result(
    result: LearningEvaluationResult,
    request: LearningEvaluationRequest,
    trusted_context: TrustedEvaluationContext | None,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    if not isinstance(trusted_context, TrustedEvaluationContext):
        issues.append(issue(EvaluationIssueCode.MISSING_TRUSTED_EVALUATION_CONTEXT, "trusted evaluation context is mandatory"))
        return ValidationReport.from_issues(issues)
    if not result.validation_report.valid:
        issues.append(issue(EvaluationIssueCode.INVALID_RESULT_CANNOT_BE_PROJECTED, "only validation.valid=true results may become history"))
    if result.subject_ref != trusted_context.authenticated_subject_ref:
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "result owner does not match trusted context"))
    if result.trusted_context_version != trusted_context.context_version:
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "result trusted context version mismatch"))
    expected_authority = authority_context_digest(trusted_context)
    if result.authority_context_digest != expected_authority:
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "result authority digest mismatch"))
    if request.subject_ref != result.subject_ref or request.graph_ref.graph_id != result.graph_id:
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "validation request ownership differs from result"))
    provenance = result.provenance
    expected_provenance = {
        "subject_ref": result.subject_ref,
        "graph_continuity_key": result.graph_continuity_key,
        "trusted_context_version": result.trusted_context_version,
        "authority_context_digest": result.authority_context_digest,
        "node_contract_set_digest": result.node_contract_set_digest,
    }
    for key, value in expected_provenance.items():
        if provenance.get(key) != value:
            issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, f"result provenance field mismatch: {key}"))
    known_nodes = {n.node_id for n in result.node_assessments}
    global_events: set[str] = set()
    global_idempotency: dict[str, str] = {}
    for entry in result.evidence_ledger:
        if entry.subject_ref != result.subject_ref:
            issues.append(issue(EvaluationIssueCode.EVIDENCE_HISTORY_USER_MISMATCH, "nested ledger owner differs from result", entry.evidence_id))
        if entry.graph_id != result.graph_id or entry.graph_continuity_key != result.graph_continuity_key:
            issues.append(issue(EvaluationIssueCode.EVIDENCE_HISTORY_GRAPH_MISMATCH, "nested ledger graph differs from result", entry.evidence_id))
        if entry.node_id not in known_nodes:
            issues.append(issue(EvaluationIssueCode.UNKNOWN_EXISTING_NODE_REFERENCE, "nested ledger node not in validated result", entry.evidence_id))
        issues.extend(
            validate_correction_history(
                list(entry.correction_history),
                evidence_id=entry.evidence_id,
                state=entry.state,
                effective_weight=entry.effective_weight,
                effective_verified=entry.effective_verified,
                independent=entry.independent,
                global_events=global_events,
                global_idempotency=global_idempotency,
            )
        )
    if result.public_view.evaluation_id != result.evaluation_id or result.public_view.evaluation_revision != result.evaluation_revision:
        issues.append(issue(EvaluationIssueCode.PUBLIC_VIEW_VALIDATION_ERROR, "public view identity differs from result"))
    if result.public_view.graph_id != result.graph_id or result.public_view.graph_revision != result.graph_revision:
        issues.append(issue(EvaluationIssueCode.PUBLIC_VIEW_VALIDATION_ERROR, "public view graph differs from result"))
    issues.extend(validate_public_view(result.public_view).issues)
    # Full deterministic revalidation is mandatory even after proof verification.
    issues.extend(validate_evaluation_result(result, request, trusted_context).issues)
    return ValidationReport.from_issues(issues)


def result_to_existing_summary(
    envelope: EvaluationResultEnvelope,
    trusted_context: TrustedEvaluationContext | None,
    authenticity_port: EvaluationResultAuthenticityPort | None = None,
) -> ExistingEvaluationSummary:
    """Verify an authentic envelope, rerun all contracts, then project immutable history."""
    if not isinstance(trusted_context, TrustedEvaluationContext):
        raise EvaluationContractError(
            ValidationReport.from_issues([
                issue(EvaluationIssueCode.MISSING_TRUSTED_EVALUATION_CONTEXT, "trusted evaluation context is mandatory")
            ])
        )
    if authenticity_port is None or not callable(getattr(authenticity_port, "verify", None)):
        raise EvaluationContractError(
            ValidationReport.from_issues([
                issue(EvaluationIssueCode.MISSING_RESULT_AUTHENTICITY_PORT, "history projection requires an authenticity port")
            ])
        )
    if not isinstance(envelope, EvaluationResultEnvelope):
        raise EvaluationContractError(
            ValidationReport.from_issues([
                issue(EvaluationIssueCode.RESULT_ENVELOPE_REQUIRED, "history projection requires an authenticated result envelope")
            ])
        )
    try:
        verified = authenticity_port.verify(envelope, trusted_context)
    except (ValueError, TypeError) as exc:
        raise EvaluationContractError(
            ValidationReport.from_issues([
                issue(EvaluationIssueCode.RESULT_AUTHENTICITY_MISMATCH, f"result envelope authenticity failed: {exc}")
            ])
        ) from exc
    raw = copy.deepcopy(verified.result)
    request = copy.deepcopy(verified.validation_request)
    if canonical_result_digest(raw) != envelope.canonical_result_digest:
        raise EvaluationContractError(
            ValidationReport.from_issues([
                issue(EvaluationIssueCode.RESULT_DIGEST_MISMATCH, "result changed after authenticity issuance")
            ])
        )
    report = _validate_projection_result(raw, request, trusted_context)
    if not report.valid:
        raise EvaluationContractError(report)
    return ExistingEvaluationSummary(
        evaluation_id=raw.evaluation_id,
        evaluation_revision=raw.evaluation_revision,
        graph_id=raw.graph_id,
        subject_ref=raw.subject_ref,
        graph_revision=raw.graph_revision,
        graph_continuity_key=raw.graph_continuity_key,
        trusted_context_version=raw.trusted_context_version,
        authority_context_digest=raw.authority_context_digest,
        node_contract_set_digest=raw.node_contract_set_digest,
        evidence_ledger=[
            ExistingEvidenceSummary(
                evidence_id=e.evidence_id,
                event_id=e.event_id,
                node_id=e.node_id,
                evidence_type=e.evidence_type,
                occurred_at=e.occurred_at,
                state=e.state,
                normalized_score=e.normalized_score,
                effective_weight=e.effective_weight,
                dimensions=copy.deepcopy(e.dimensions),
                independent=e.independent,
                trust_level=e.trust_level,
                activity_key=e.canonical_activity_key or e.evidence_id,
                source_fingerprint=e.source_fingerprint,
                subject_ref=e.subject_ref,
                graph_id=e.graph_id,
                graph_continuity_key=e.graph_continuity_key,
                source_system=e.source_system,
                effective_verified=e.effective_verified,
                evidence_family_key=e.evidence_family_key,
                canonical_activity_key=e.canonical_activity_key,
                canonical_item_key=e.canonical_item_key,
                attempt_group_key=e.attempt_group_key,
                weak=e.weak,
                transfer=e.transfer,
                hinted=e.hinted,
                error_codes=copy.deepcopy(e.error_codes),
                objective_scores=copy.deepcopy(e.objective_scores),
                objective_rubric_scores=copy.deepcopy(e.objective_rubric_scores),
                negative_signal_codes=copy.deepcopy(e.negative_signal_codes),
                misconception_pattern_codes=copy.deepcopy(e.misconception_pattern_codes),
                quality_warning_codes=copy.deepcopy(e.quality_warning_codes),
                correction_history=copy.deepcopy(e.correction_history),
            )
            for e in raw.evidence_ledger
        ],
        node_assessments=[
            ExistingNodeAssessmentSummary(
                node_id=n.node_id,
                assessment_status=n.assessment_status,
                mastery_signal=n.mastery_signal,
                confidence=n.confidence,
                evidence_sufficiency=n.evidence_sufficiency,
                dimension_assessments=copy.deepcopy(n.dimension_assessments),
                objective_assessments=copy.deepcopy(n.objective_assessments),
                mastery_requirement_assessments=copy.deepcopy(n.mastery_requirement_assessments),
                supported_claims=copy.deepcopy(n.supported_claims),
                unsupported_claims=copy.deepcopy(n.unsupported_claims),
                evidence_gaps=copy.deepcopy(n.evidence_gaps),
                completion_eligibility_signal=n.completion_eligibility_signal,
                needs_review=n.needs_review,
                node_contract_revision=n.node_contract_revision,
                node_contract_digest=n.node_contract_digest,
                mastery_requirement_digest=n.mastery_requirement_digest,
                objective_contracts=copy.deepcopy(n.objective_contracts),
            )
            for n in raw.node_assessments
        ],
        misconceptions=[
            ExistingMisconceptionSummary(
                code=m.code,
                node_id=m.node_id,
                summary=m.summary,
                evidence_ids=copy.deepcopy(m.evidence_ids),
                confidence=m.confidence,
                severity=m.severity,
                resolved=m.resolved,
            )
            for n in raw.node_assessments
            for m in n.misconceptions
        ],
        contradictions=[
            ExistingContradictionSummary(
                contradiction_code=c.contradiction_code,
                node_id=c.node_id,
                resolved=False,
            )
            for c in raw.contradictions
        ],
        evidence_quality_warnings=copy.deepcopy(raw.evidence_quality_warnings),
        processed_requests=[],
    )
