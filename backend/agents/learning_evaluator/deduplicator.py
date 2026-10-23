from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from .evidence_policy import normalize_record
from .evidence_validator import EvaluationContractError, issue
from .models import *


from .execution_codes import EvaluationIssueCode
def normalize_and_deduplicate(
    request: LearningEvaluationRequest,
    trusted_context: TrustedEvaluationContext | None,
) -> tuple[list[NormalizedEvidence], list[ValidationIssue]]:
    if not isinstance(trusted_context, TrustedEvaluationContext):
        raise EvaluationContractError(
            ValidationReport.from_issues([
                issue(EvaluationIssueCode.MISSING_TRUSTED_EVALUATION_CONTEXT, "trusted evaluation context is mandatory")
            ])
        )
    existing_ledger = request.existing_evaluation_summary.evidence_ledger if request.existing_evaluation_summary else []
    existing_ids = {e.evidence_id for e in existing_ledger}
    existing_events = {e.event_id for e in existing_ledger}
    existing_fingerprints = {e.source_fingerprint for e in existing_ledger}
    normalized: list[NormalizedEvidence] = []
    issues: list[ValidationIssue] = []
    for record in sorted(request.evidence_records, key=lambda x: (x.occurred_at, x.evidence_id)):
        fingerprint = f"{record.source_system}:{record.source_record_id}"
        if request.existing_evaluation_summary and (
            record.evidence_id in existing_ids or record.event_id in existing_events or fingerprint in existing_fingerprints
        ):
            issues.append(ValidationIssue(code=EvaluationIssueCode.DUPLICATE_EVIDENCE_IGNORED, severity=Severity.INFO, message="historical fact not counted again", path=record.evidence_id))
            continue
        normalized.append(
            normalize_record(
                record,
                request.evaluation_policy,
                trusted_context,
                request.graph_ref.graph_continuity_key,
            )
        )
    by_activity: dict[str, list[NormalizedEvidence]] = defaultdict(list)
    for item in normalized:
        by_activity[item.canonical_activity_key].append(item)
    existing_by_activity: dict[str, list[ExistingEvidenceSummary]] = defaultdict(list)
    for old in existing_ledger:
        if old.canonical_activity_key:
            existing_by_activity[old.canonical_activity_key].append(old)
    window = timedelta(seconds=request.evaluation_policy.repeated_activity_window_seconds)
    spaced = timedelta(days=request.evaluation_policy.spaced_recheck_days)
    for key, group in by_activity.items():
        group.sort(key=lambda x: x.occurred_at)
        previous: list[tuple[object, bool]] = [(old.occurred_at, old.independent) for old in existing_by_activity.get(key, [])]
        for item in group:
            prior_times = [time for time, _ in previous if time <= item.occurred_at]
            if prior_times:
                delta = item.occurred_at - max(prior_times)
                if delta < window:
                    item.independent = False
                    item.effective_weight *= 0.2
                    item.quality_warning_codes = list(dict.fromkeys(item.quality_warning_codes + ["DUPLICATE_ATTEMPT"]))
                    issues.append(ValidationIssue(code=EvaluationIssueCode.DUPLICATE_ATTEMPT, severity=Severity.WARNING, message="cross-revision or short-window repeat discounted", path=item.evidence_id))
                elif delta >= spaced:
                    item.dimensions = list(dict.fromkeys(item.dimensions + [EvaluationDimension.RETENTION]))
                    if item.trust_level in {TrustLevel.VERIFIED, TrustLevel.SYSTEM_OBSERVED, TrustLevel.STRUCTURED_HUMAN}:
                        item.independent = True
            previous.append((item.occurred_at, item.independent))
    return normalized, issues
