from __future__ import annotations

from collections import Counter
from datetime import timedelta
import re

from .authority import SUPPORTED_TRUSTED_CONTEXT_VERSIONS, TRUST_ORDER, find_authority
from .contracts import authority_context_digest, node_contract_set_digest, node_contract_values
from .corrections import actor_may_correct
from .correction_invariants import validate_correction_history, correction_payload_digest
from .models import *
from .objective_mapper import required_dimensions, objective_allowed_dimensions
from .closed_codes import unknown_evidence_error_codes, unknown_evidence_warning_codes, is_registered_misconception_code


from .execution_codes import EvaluationIssueCode
class EvaluationContractError(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__("evaluation request contract invalid")


def issue(code: EvaluationIssueCode, message: str, path: str | None = None, repairable: bool = False, severity: Severity = Severity.ERROR) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, message=message, path=path, repairable=repairable)


SUPPORTED_CORRECTION_CONTEXT_VERSIONS = {"correction-v1"}

SENSITIVE_CONTRACT = re.compile(
    r"(?i)(password\s*[=:]|token\s*[=:]|secret\s*[=:]|authorization\s*[=:]|bearer\s+[A-Za-z0-9._-]+|[A-Za-z]:[\\/]|(?:^|\s)/(?:home|users|var|etc)/|https?://|\\\\|[\x00-\x08\x0b\x0c\x0e-\x1f])"
)



def _closed_code_issues(*, error_codes=(), warning_codes=(), misconception_codes=(), path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if unknown_evidence_error_codes(error_codes or []):
        issues.append(issue(EvaluationIssueCode.UNKNOWN_EVIDENCE_ERROR_CODE, "evidence contains an unregistered private code", path))
    if unknown_evidence_warning_codes(warning_codes or []):
        issues.append(issue(EvaluationIssueCode.UNKNOWN_EVIDENCE_WARNING_CODE, "evidence contains an unregistered warning code", path))
    if any(not is_registered_misconception_code(x) for x in (misconception_codes or [])):
        issues.append(issue(EvaluationIssueCode.UNKNOWN_MISCONCEPTION_CODE, "evidence contains an unregistered misconception code", path))
    return issues

def validate_node_contract_privacy(request: LearningEvaluationRequest) -> ValidationReport:
    issues: list[ValidationIssue] = []
    for i, node in enumerate(request.node_contracts):
        fields = [("title", node.title), ("difficulty_band", node.difficulty_band)]
        fields += [(f"learning_objectives[{j}]", x) for j, x in enumerate(node.learning_objectives)]
        fields += [(f"mastery_evidence_requirements[{j}]", x) for j, x in enumerate(node.mastery_evidence_requirements)]
        for name, text in fields:
            if SENSITIVE_CONTRACT.search(text):
                issues.append(issue(EvaluationIssueCode.NODE_CONTRACT_PRIVATE_DATA, "node contract contains private path, URL, credential-like text, or control character", f"node_contracts[{i}].{name}"))
    return ValidationReport.from_issues(issues)


def validate_trusted_context(request: LearningEvaluationRequest, trusted_context: TrustedEvaluationContext | None) -> ValidationReport:
    if not isinstance(trusted_context, TrustedEvaluationContext):
        return ValidationReport.from_issues([
            issue(EvaluationIssueCode.MISSING_TRUSTED_EVALUATION_CONTEXT, "trusted evaluation context is mandatory")
        ])
    issues: list[ValidationIssue] = []
    if trusted_context.authenticated_subject_ref != request.subject_ref:
        issues.append(issue(EvaluationIssueCode.UNTRUSTED_AUTHORITY_CONTEXT, "trusted context subject does not match request subject", "trusted_context.authenticated_subject_ref"))
    if trusted_context.context_version not in SUPPORTED_TRUSTED_CONTEXT_VERSIONS:
        issues.append(issue(EvaluationIssueCode.UNSUPPORTED_AUTHORITY_CONTEXT_VERSION, "trusted context version is unsupported", "trusted_context.context_version"))
    authorities = trusted_context.evidence_authority_policy.authorities
    source_counts = Counter(a.source_system for a in authorities)
    for source, count in source_counts.items():
        if count > 1:
            issues.append(issue(EvaluationIssueCode.DUPLICATE_AUTHORITY_SOURCE, "authority source appears more than once", source))
    used_sources = {e.source_system for e in request.evidence_records}
    if request.existing_evaluation_summary:
        used_sources |= {e.source_system for e in request.existing_evaluation_summary.evidence_ledger if e.source_system}
    for source in sorted(used_sources):
        authority = find_authority(trusted_context.evidence_authority_policy, source)
        if authority is None:
            issues.append(issue(EvaluationIssueCode.UNKNOWN_AUTHORITY_SOURCE, "evidence source is not registered in trusted authority context", source))
            continue
        actual = trusted_context.adapter_contract_versions.get(source)
        if actual is None:
            issues.append(issue(EvaluationIssueCode.AUTHORITY_ADAPTER_VERSION_MISSING, "used authority source lacks adapter contract version", source))
        elif actual != authority.authority_contract_version:
            issues.append(issue(EvaluationIssueCode.AUTHORITY_ADAPTER_VERSION_MISMATCH, "adapter version does not match authority contract", source))
    return ValidationReport.from_issues(issues)


def validate_request_contract(request: LearningEvaluationRequest, trusted_context: TrustedEvaluationContext | None) -> ValidationReport:
    issues = list(validate_trusted_context(request, trusted_context).issues)
    node_ids = [n.node_id for n in request.node_contracts]
    continuity = [n.node_continuity_key for n in request.node_contracts]
    if len(node_ids) != len(set(node_ids)):
        issues.append(issue(EvaluationIssueCode.DUPLICATE_NODE_ID, "node_id must be unique", "node_contracts"))
    if len(continuity) != len(set(continuity)):
        issues.append(issue(EvaluationIssueCode.DUPLICATE_NODE_CONTINUITY_KEY, "node continuity key must be unique", "node_contracts"))
    known = set(node_ids)
    for i, node in enumerate(request.node_contracts):
        if node.node_id in node.prerequisite_node_ids:
            issues.append(issue(EvaluationIssueCode.SELF_NODE_PREREQUISITE, "node cannot depend on itself", f"node_contracts[{i}]"))
        for ref in node.prerequisite_node_ids:
            if ref not in known:
                issues.append(issue(EvaluationIssueCode.UNKNOWN_PREREQUISITE_NODE, "prerequisite node missing", f"node_contracts[{i}].prerequisite_node_ids"))
    issues.extend(validate_node_contract_privacy(request).issues)
    return ValidationReport.from_issues(issues)


def _validate_corrections(
    request: LearningEvaluationRequest,
    trusted_correction_context: TrustedCorrectionContext | None,
    available_evidence: dict[str, tuple[EvidenceType, object]],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if request.evaluation_mode != EvaluationMode.EVIDENCE_CORRECTION:
        return issues
    if trusted_correction_context is None:
        return [issue(EvaluationIssueCode.MISSING_TRUSTED_CORRECTION_CONTEXT, "trusted correction context is mandatory")]
    if trusted_correction_context.correction_contract_version not in SUPPORTED_CORRECTION_CONTEXT_VERSIONS:
        issues.append(issue(EvaluationIssueCode.UNSUPPORTED_CORRECTION_CONTEXT_VERSION, "correction contract version is unsupported"))
    if (
        trusted_correction_context.actor_role == CorrectionActorRole.SUBJECT_USER
        and trusted_correction_context.authenticated_actor_ref != request.subject_ref
    ):
        issues.append(issue(EvaluationIssueCode.CORRECTION_ACTOR_UNAUTHORIZED, "subject user correction actor must match request subject"))
    intents = request.evidence_corrections
    ids = [x.evidence_id for x in intents]
    idem = [x.idempotency_key for x in intents]
    if len(ids) != len(set(ids)):
        issues.append(issue(EvaluationIssueCode.DUPLICATE_EVIDENCE_CORRECTION, "same evidence has multiple corrections in one request"))
    if len(idem) != len(set(idem)):
        issues.append(issue(EvaluationIssueCode.DUPLICATE_EVIDENCE_CORRECTION, "correction idempotency key is duplicated"))
    event_ids = [trusted_correction_context.correction_event_ids.get(x.idempotency_key) for x in intents]
    if any(x is None for x in event_ids):
        issues.append(issue(EvaluationIssueCode.CORRECTION_EVENT_MISSING, "server correction event is missing"))
    actual_events = [x for x in event_ids if x is not None]
    if len(actual_events) != len(set(actual_events)):
        issues.append(issue(EvaluationIssueCode.CORRECTION_EVENT_DUPLICATE, "correction event IDs must be unique"))
    if trusted_correction_context.corrected_at > request.evaluated_at + timedelta(seconds=request.evaluation_policy.clock_skew_seconds):
        issues.append(issue(EvaluationIssueCode.CORRECTION_FROM_FUTURE, "correction time is after evaluation time"))

    existing_history: dict[str, list[EvidenceCorrectionAuditEntry]] = {}
    if request.existing_evaluation_summary:
        existing_history = {e.evidence_id: list(e.correction_history) for e in request.existing_evaluation_summary.evidence_ledger}
    all_existing_events = {c.correction_event_id for history in existing_history.values() for c in history}

    for index, intent in enumerate(intents):
        path = f"evidence_corrections[{index}]"
        if intent.evidence_id not in available_evidence:
            issues.append(issue(EvaluationIssueCode.UNKNOWN_CORRECTION_EVIDENCE, "correction references missing evidence", path))
            continue
        evidence_type, occurred_at = available_evidence[intent.evidence_id]
        if trusted_correction_context.corrected_at < occurred_at:
            issues.append(issue(EvaluationIssueCode.CORRECTION_BEFORE_EVIDENCE, "correction predates evidence", path))
        if intent.requested_reason not in trusted_correction_context.allowed_correction_reasons:
            issues.append(issue(EvaluationIssueCode.CORRECTION_REASON_UNAUTHORIZED, "correction reason not allowed by trusted context", path))
        if not actor_may_correct(trusted_correction_context.actor_role, intent.requested_reason, evidence_type):
            issues.append(issue(EvaluationIssueCode.CORRECTION_ACTOR_UNAUTHORIZED, "actor role cannot apply this correction", path))
        event_id = trusted_correction_context.correction_event_ids.get(intent.idempotency_key)
        if event_id is None:
            continue
        expected = EvidenceCorrectionAuditEntry(
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
        history = existing_history.get(intent.evidence_id, [])
        same_event = next((c for c in history if c.correction_event_id == event_id), None)
        if same_event is not None:
            if same_event != expected:
                issues.append(issue(EvaluationIssueCode.CORRECTION_STATE_CONFLICT, "idempotent correction event does not match historical audit", path))
            continue
        if event_id in all_existing_events:
            issues.append(issue(EvaluationIssueCode.CORRECTION_EVENT_DUPLICATE, "correction event belongs to another historical correction", path))
            continue
        if history:
            supersedes = expected.supersedes_correction_event_id
            if supersedes is None or supersedes != history[-1].correction_event_id:
                issues.append(issue(EvaluationIssueCode.CORRECTION_STATE_CONFLICT, "new correction must explicitly supersede the latest historical correction", path))
    return issues


def validate_evidence_contract(
    request: LearningEvaluationRequest,
    trusted_context: TrustedEvaluationContext | None,
    trusted_correction_context: TrustedCorrectionContext | None = None,
) -> ValidationReport:
    if trusted_context is None:
        return ValidationReport.from_issues([issue(EvaluationIssueCode.MISSING_TRUSTED_EVALUATION_CONTEXT, "trusted evaluation context is mandatory")])
    issues: list[ValidationIssue] = []
    known_nodes = {n.node_id for n in request.node_contracts}
    seen_ids: set[str] = set()
    seen_events: set[str] = set()
    seen_sources: set[tuple[str, str]] = set()
    skew = timedelta(seconds=request.evaluation_policy.clock_skew_seconds)
    policy = trusted_context.evidence_authority_policy
    available: dict[str, tuple[EvidenceType, object]] = {}
    if request.existing_evaluation_summary:
        available.update({e.evidence_id: (e.evidence_type, e.occurred_at) for e in request.existing_evaluation_summary.evidence_ledger})
    for i, evidence in enumerate(request.evidence_records):
        path = f"evidence_records[{i}]"
        available[evidence.evidence_id] = (evidence.evidence_type, evidence.occurred_at)
        if evidence.evidence_id in seen_ids:
            issues.append(issue(EvaluationIssueCode.DUPLICATE_EVIDENCE_ID, "duplicate evidence_id", path))
        seen_ids.add(evidence.evidence_id)
        if evidence.event_id in seen_events:
            issues.append(issue(EvaluationIssueCode.DUPLICATE_EVENT_ID, "duplicate event_id", path))
        seen_events.add(evidence.event_id)
        source = (evidence.source_system, evidence.source_record_id)
        if source in seen_sources:
            issues.append(issue(EvaluationIssueCode.SAME_EVENT_DOUBLE_COUNTED, "same source record appears more than once", path))
        seen_sources.add(source)
        if evidence.user_ref != request.subject_ref:
            issues.append(issue(EvaluationIssueCode.EVIDENCE_USER_MISMATCH, "evidence belongs to another user", path))
        if evidence.graph_id != request.graph_ref.graph_id:
            issues.append(issue(EvaluationIssueCode.UNKNOWN_GRAPH_REFERENCE, "evidence belongs to another graph", path))
        if evidence.node_id not in known_nodes:
            issues.append(issue(EvaluationIssueCode.UNKNOWN_NODE_REFERENCE, "evidence node missing", path))
        if evidence.occurred_at > evidence.received_at + skew:
            issues.append(issue(EvaluationIssueCode.INVALID_TIME_ORDER, "occurred_at after received_at beyond skew", path))
        if evidence.occurred_at > request.evaluated_at + skew or evidence.received_at > request.evaluated_at + skew:
            issues.append(issue(EvaluationIssueCode.EVIDENCE_FROM_FUTURE, "evidence occurs or arrives after evaluation time", path))
        if evidence.evidence_type == EvidenceType.MANUAL_TEACHER_EVIDENCE and not request.constraints.allow_manual_evidence:
            issues.append(issue(EvaluationIssueCode.INVALID_EVIDENCE_TYPE, "manual evidence disabled by policy", path))
        if evidence.provenance.trust_level == TrustLevel.INVALID:
            issues.append(issue(EvaluationIssueCode.INVALID_EVIDENCE_SOURCE, "evidence trust level is INVALID", path))
        payload = evidence.evidence_payload
        raw_errors = list(getattr(payload, "error_codes", []) or [])
        runtime_error = getattr(payload, "runtime_error_category", None)
        if runtime_error:
            raw_errors.append(runtime_error)
        issues.extend(_closed_code_issues(error_codes=raw_errors, path=path))
        authority = find_authority(policy, evidence.source_system)
        if authority is None:
            issues.append(issue(EvaluationIssueCode.UNKNOWN_AUTHORITY_SOURCE, "evidence source is not registered in trusted authority context", path))
        elif evidence.evidence_type not in authority.allowed_evidence_types:
            issues.append(issue(EvaluationIssueCode.AUTHORITY_EVIDENCE_TYPE_UNSUPPORTED, "source authority does not allow this evidence type", path))
        if evidence.evidence_type == EvidenceType.MANUAL_TEACHER_EVIDENCE and (authority is None or evidence.evidence_type not in authority.allowed_evidence_types):
            issues.append(issue(EvaluationIssueCode.MANUAL_EVIDENCE_UNAUTHORIZED, "manual evidence requires authorized source contract", path))
    issues.extend(_validate_corrections(request, trusted_correction_context, available))
    return ValidationReport.from_issues(issues)


def _node_contract_projection(node: NodeEvaluationContract):
    digest, mastery_digest, raw = node_contract_values(node, required_dimensions(node.node_type), objective_allowed_dimensions)
    objectives = [ObjectiveEvaluationContract(**x) for x in raw]
    return digest, mastery_digest, objectives


def validate_existing_evaluation_contract(request: LearningEvaluationRequest, trusted_context: TrustedEvaluationContext | None) -> ValidationReport:
    issues: list[ValidationIssue] = []
    existing = request.existing_evaluation_summary
    if not existing:
        return ValidationReport(valid=True)
    if existing.processed_requests:
        issues.append(issue(EvaluationIssueCode.UNTRUSTED_PROCESSED_REQUEST_SNAPSHOT, "caller-supplied replay metadata is not trusted"))
    if trusted_context is None:
        return ValidationReport.from_issues([issue(EvaluationIssueCode.MISSING_TRUSTED_EVALUATION_CONTEXT, "trusted evaluation context is mandatory")])
    identity_fields = (
        existing.subject_ref,
        existing.graph_revision,
        existing.graph_continuity_key,
        existing.trusted_context_version,
        existing.authority_context_digest,
        existing.node_contract_set_digest,
    )
    if any(x is None for x in identity_fields):
        issues.append(issue(EvaluationIssueCode.LEGACY_EXISTING_EVALUATION_REQUIRES_MIGRATION, "existing evaluation lacks required ownership or provenance"))
    if existing.subject_ref is not None and existing.subject_ref != request.subject_ref:
        issues.append(issue(EvaluationIssueCode.EXISTING_EVALUATION_USER_MISMATCH, "existing evaluation belongs to another subject"))
    if existing.graph_id != request.graph_ref.graph_id:
        issues.append(issue(EvaluationIssueCode.EXISTING_EVALUATION_GRAPH_MISMATCH, "existing evaluation belongs to another graph"))
    if existing.graph_continuity_key is not None and existing.graph_continuity_key != request.graph_ref.graph_continuity_key:
        issues.append(issue(EvaluationIssueCode.EXISTING_EVALUATION_GRAPH_MISMATCH, "graph continuity identity differs"))
    if existing.graph_revision is not None and existing.graph_revision > request.graph_ref.graph_revision:
        issues.append(issue(EvaluationIssueCode.EXISTING_EVALUATION_GRAPH_REVISION_CONFLICT, "existing graph revision is newer than request"))
    current_authority_digest = authority_context_digest(trusted_context)
    if existing.trusted_context_version is not None and existing.trusted_context_version != trusted_context.context_version:
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "trusted context version differs from existing evaluation"))
    if existing.authority_context_digest is not None and existing.authority_context_digest != current_authority_digest:
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "authority context digest differs from existing evaluation"))
    current_contract_set_digest = node_contract_set_digest(request.node_contracts, required_dimensions, objective_allowed_dimensions)
    if (
        existing.graph_revision == request.graph_ref.graph_revision
        and existing.node_contract_set_digest is not None
        and existing.node_contract_set_digest != current_contract_set_digest
    ):
        issues.append(issue(EvaluationIssueCode.EVALUATION_PROVENANCE_MISMATCH, "node contract set digest differs without graph revision change"))
    node_ids = {n.node_id for n in request.node_contracts}
    ids: list[str] = []
    events: list[str] = []
    fingerprints: list[str] = []
    activity: list[tuple[str, object, bool]] = []
    correction_events: set[str] = set()
    correction_idempotency: dict[str, str] = {}
    policy = trusted_context.evidence_authority_policy
    for i, evidence in enumerate(existing.evidence_ledger):
        path = f"existing.evidence_ledger[{i}]"
        ids.append(evidence.evidence_id); events.append(evidence.event_id); fingerprints.append(evidence.source_fingerprint)
        if evidence.subject_ref != existing.subject_ref:
            issues.append(issue(EvaluationIssueCode.EVIDENCE_HISTORY_USER_MISMATCH, "historical evidence owner differs from evaluation", path))
        if evidence.graph_id != existing.graph_id or evidence.graph_continuity_key != existing.graph_continuity_key:
            issues.append(issue(EvaluationIssueCode.EVIDENCE_HISTORY_GRAPH_MISMATCH, "historical evidence graph identity differs", path))
        if evidence.node_id not in node_ids:
            issues.append(issue(EvaluationIssueCode.UNKNOWN_EXISTING_NODE_REFERENCE, "existing evidence references unknown node", path))
        canonical = (evidence.evidence_family_key, evidence.canonical_activity_key, evidence.canonical_item_key, evidence.attempt_group_key)
        if evidence.state == EvidenceState.VALID and evidence.independent and any(x is None for x in canonical):
            issues.append(issue(EvaluationIssueCode.LEGACY_EVIDENCE_IDENTITY_INCOMPLETE, "independent historical evidence lacks canonical identity", path))
        semantic = (evidence.weak, evidence.transfer, evidence.hinted, evidence.error_codes, evidence.negative_signal_codes, evidence.quality_warning_codes)
        issues.extend(_closed_code_issues(
            error_codes=list(evidence.error_codes or []) + list(evidence.negative_signal_codes or []),
            warning_codes=evidence.quality_warning_codes or [],
            misconception_codes=evidence.misconception_pattern_codes or [],
            path=path,
        ))
        if evidence.state == EvidenceState.VALID and any(x is None for x in semantic):
            issues.append(issue(EvaluationIssueCode.LEDGER_ROUNDTRIP_SEMANTIC_LOSS, "historical evidence lacks fields required for deterministic roundtrip", path))
        if evidence.canonical_activity_key:
            activity.append((evidence.canonical_activity_key, evidence.occurred_at, evidence.independent))
        if evidence.source_system is None or find_authority(policy, evidence.source_system) is None:
            issues.append(issue(EvaluationIssueCode.UNKNOWN_AUTHORITY_SOURCE, "historical evidence source is not registered", path))
        else:
            authority_for_source = find_authority(policy, evidence.source_system)
            actual_version = trusted_context.adapter_contract_versions.get(evidence.source_system)
            if actual_version is None:
                issues.append(issue(EvaluationIssueCode.AUTHORITY_ADAPTER_VERSION_MISSING, "historical source lacks adapter version", path))
            elif actual_version != authority_for_source.authority_contract_version:
                issues.append(issue(EvaluationIssueCode.AUTHORITY_ADAPTER_VERSION_MISMATCH, "historical source adapter version mismatch", path))
        if evidence.state == EvidenceState.VALID and evidence.effective_verified is False and evidence.trust_level == TrustLevel.VERIFIED:
            issues.append(issue(EvaluationIssueCode.INVALID_EXISTING_TRUST_CONTRACT, "active unverified existing evidence cannot be VERIFIED", path))
        if evidence.state == EvidenceState.VALID and evidence.evidence_type in {EvidenceType.SELF_REPORT, EvidenceType.RESOURCE_OPENED, EvidenceType.RESOURCE_COMPLETED} and evidence.independent:
            issues.append(issue(EvaluationIssueCode.INVALID_EXISTING_INDEPENDENCE_CONTRACT, "active weak evidence cannot be independent", path))
        if evidence.effective_verified:
            authority = find_authority(policy, evidence.source_system or "")
            version = trusted_context.adapter_contract_versions.get(evidence.source_system or "")
            if authority is None or evidence.evidence_type not in authority.allowed_evidence_types or not authority.can_assert_verified or version != authority.authority_contract_version:
                issues.append(issue(EvaluationIssueCode.INVALID_EXISTING_TRUST_CONTRACT, "historical verified evidence is not backed by current trusted authority context", path))
        if existing.graph_revision is not None and existing.graph_revision < request.graph_ref.graph_revision and evidence.objective_scores and not evidence.objective_rubric_scores:
            issues.append(issue(EvaluationIssueCode.STALE_OBJECTIVE_SCORE_REJECTED, "legacy index objective score cannot cross graph revision", path))
        issues.extend(
            validate_correction_history(
                list(evidence.correction_history),
                evidence_id=evidence.evidence_id,
                state=evidence.state,
                effective_weight=evidence.effective_weight,
                effective_verified=bool(evidence.effective_verified),
                independent=evidence.independent,
                global_events=correction_events,
                global_idempotency=correction_idempotency,
            )
        )
        for correction in evidence.correction_history:
            if correction.corrected_at < evidence.occurred_at:
                issues.append(issue(EvaluationIssueCode.CORRECTION_BEFORE_EVIDENCE, "historical correction predates evidence", path))
            if correction.corrected_at > request.evaluated_at + timedelta(seconds=request.evaluation_policy.clock_skew_seconds):
                issues.append(issue(EvaluationIssueCode.CORRECTION_FROM_FUTURE, "historical correction is after evaluation", path))
            if correction.contract_version not in SUPPORTED_CORRECTION_CONTEXT_VERSIONS:
                issues.append(issue(EvaluationIssueCode.UNSUPPORTED_CORRECTION_CONTEXT_VERSION, "historical correction contract version unsupported", path))
            if not actor_may_correct(correction.actor_role, correction.reason, evidence.evidence_type):
                issues.append(issue(EvaluationIssueCode.CORRECTION_ACTOR_UNAUTHORIZED, "historical correction actor was not authorized", path))
            if correction.actor_role == CorrectionActorRole.SUBJECT_USER and correction.actor_ref != existing.subject_ref:
                issues.append(issue(EvaluationIssueCode.CORRECTION_ACTOR_UNAUTHORIZED, "historical subject-user correction belongs to another subject", path))
    for item in existing.misconceptions:
        if not is_registered_misconception_code(item.code):
            issues.append(issue(EvaluationIssueCode.UNKNOWN_MISCONCEPTION_CODE, "existing misconception code is not registered", item.code))
    for item in existing.evidence_quality_warnings:
        if unknown_evidence_warning_codes([item.warning_code]):
            issues.append(issue(EvaluationIssueCode.UNKNOWN_EVIDENCE_WARNING_CODE, "existing warning code is not registered", item.warning_code))
    if len(ids) != len(set(ids)): issues.append(issue(EvaluationIssueCode.DUPLICATE_EXISTING_EVIDENCE_ID, "existing ledger contains duplicate evidence IDs"))
    if len(events) != len(set(events)): issues.append(issue(EvaluationIssueCode.DUPLICATE_EXISTING_EVENT_ID, "existing ledger contains duplicate event IDs"))
    if len(fingerprints) != len(set(fingerprints)): issues.append(issue(EvaluationIssueCode.DUPLICATE_EXISTING_SOURCE_FINGERPRINT, "existing ledger contains duplicate source fingerprints"))
    window = timedelta(seconds=request.evaluation_policy.repeated_activity_window_seconds)
    by: dict[str, tuple[object, bool]] = {}
    for key, when, independent in sorted(activity, key=lambda x: x[1]):
        if key in by and independent and by[key][1] and when - by[key][0] < window:
            issues.append(issue(EvaluationIssueCode.DUPLICATE_EXISTING_ACTIVITY_IDENTITY, "same canonical activity is independently counted more than once"))
        by[key] = (when, independent)
    existing_nodes = {n.node_id: n for n in existing.node_assessments}
    for node in request.node_contracts:
        old = existing_nodes.get(node.node_id)
        if not old:
            continue
        current_digest, current_mastery, current_objectives = _node_contract_projection(node)
        if old.node_contract_digest is None or old.mastery_requirement_digest is None or not old.objective_contracts:
            issues.append(issue(EvaluationIssueCode.LEGACY_EXISTING_EVALUATION_REQUIRES_MIGRATION, "existing node assessment lacks contract provenance", node.node_id))
            continue
        changed = old.node_contract_digest != current_digest
        revision_changed = old.node_contract_revision != node.node_contract_revision
        if (changed or revision_changed) and request.evaluation_mode == EvaluationMode.INCREMENTAL:
            issues.append(issue(EvaluationIssueCode.NODE_CONTRACT_CHANGED_REQUIRES_FULL_REASSESSMENT, "node contract revision or digest changed", node.node_id))
        if old.mastery_requirement_digest != current_mastery and request.evaluation_mode == EvaluationMode.INCREMENTAL:
            issues.append(issue(EvaluationIssueCode.MASTERY_REQUIREMENT_CONTRACT_CHANGED, "mastery contract changed", node.node_id))
        old_by_id = {o.objective_id: o for o in old.objective_contracts}
        current_by_id = {o.objective_id: o for o in current_objectives}
        if request.evaluation_mode == EvaluationMode.INCREMENTAL and set(old_by_id) != set(current_by_id):
            issues.append(issue(EvaluationIssueCode.OBJECTIVE_CONTRACT_CHANGED, "objective identities changed", node.node_id))
    return ValidationReport.from_issues(issues)


def validate_all_preflight(
    request: LearningEvaluationRequest,
    trusted_context: TrustedEvaluationContext | None,
    trusted_correction_context: TrustedCorrectionContext | None = None,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    for report in (
        validate_request_contract(request, trusted_context),
        validate_evidence_contract(request, trusted_context, trusted_correction_context),
        validate_existing_evaluation_contract(request, trusted_context),
    ):
        issues.extend(report.issues)
    return ValidationReport.from_issues(issues)
