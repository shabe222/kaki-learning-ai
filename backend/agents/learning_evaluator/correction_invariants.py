from __future__ import annotations

import json
from typing import Iterable

from .enums import EvidenceState, Severity
from .models import EvidenceCorrectionAuditEntry, ValidationIssue


from .execution_codes import EvaluationIssueCode
def _issue(code: EvaluationIssueCode, message: str, path: str) -> ValidationIssue:
    return ValidationIssue(code=code, severity=Severity.ERROR, message=message, path=path, repairable=False)


def correction_payload_digest(entry: EvidenceCorrectionAuditEntry) -> str:
    payload = entry.model_dump(mode="json")
    payload.pop("correction_event_id", None)
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def validate_correction_history(
    history: list[EvidenceCorrectionAuditEntry],
    *,
    evidence_id: str,
    state: EvidenceState,
    effective_weight: float,
    effective_verified: bool,
    independent: bool,
    global_events: set[str] | None = None,
    global_idempotency: dict[str, str] | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    path = f"evidence[{evidence_id}].correction_history"
    global_events = global_events if global_events is not None else set()
    global_idempotency = global_idempotency if global_idempotency is not None else {}
    local_events: set[str] = set()
    previous: str | None = None
    event_to_previous: dict[str, str | None] = {}
    for index, entry in enumerate(history):
        ep = f"{path}[{index}]"
        if entry.correction_event_id in local_events or entry.correction_event_id in global_events:
            issues.append(_issue(EvaluationIssueCode.CORRECTION_EVENT_DUPLICATE, "correction event must be globally unique", ep))
        local_events.add(entry.correction_event_id)
        global_events.add(entry.correction_event_id)
        digest = correction_payload_digest(entry)
        prior_digest = global_idempotency.get(entry.idempotency_key)
        if prior_digest is not None:
            code = EvaluationIssueCode.CORRECTION_IDEMPOTENCY_PAYLOAD_CONFLICT if prior_digest != digest else EvaluationIssueCode.DUPLICATE_EVIDENCE_CORRECTION
            issues.append(_issue(code, "correction idempotency key must bind exactly one audit event", ep))
        else:
            global_idempotency[entry.idempotency_key] = digest
        if index == 0:
            if entry.supersedes_correction_event_id is not None:
                issues.append(_issue(EvaluationIssueCode.CORRECTION_STATE_CONFLICT, "first correction cannot supersede another event", ep))
        elif entry.supersedes_correction_event_id != previous:
            issues.append(_issue(EvaluationIssueCode.CORRECTION_STATE_CONFLICT, "correction chain must supersede the immediate tail", ep))
        if entry.supersedes_correction_event_id == entry.correction_event_id:
            issues.append(_issue(EvaluationIssueCode.CORRECTION_CYCLE, "correction cannot supersede itself", ep))
        if entry.supersedes_correction_event_id is not None and entry.supersedes_correction_event_id not in local_events:
            issues.append(_issue(EvaluationIssueCode.CORRECTION_STATE_CONFLICT, "superseded event must exist earlier in the same linear chain", ep))
        event_to_previous[entry.correction_event_id] = entry.supersedes_correction_event_id
        previous = entry.correction_event_id
    # Defensive cycle walk even though ordering rules should already prevent it.
    for event in event_to_previous:
        seen: set[str] = set()
        cur: str | None = event
        while cur is not None:
            if cur in seen:
                issues.append(_issue(EvaluationIssueCode.CORRECTION_CYCLE, "correction chain contains a cycle", path))
                break
            seen.add(cur)
            cur = event_to_previous.get(cur)
    if history:
        if state == EvidenceState.VALID:
            issues.append(_issue(EvaluationIssueCode.CORRECTED_EVIDENCE_REACTIVATED, "evidence with correction history cannot be valid", path))
        if effective_weight > 0:
            issues.append(_issue(EvaluationIssueCode.CORRECTED_EVIDENCE_NONZERO_WEIGHT, "corrected evidence must have zero weight", path))
        if effective_verified:
            issues.append(_issue(EvaluationIssueCode.CORRECTED_EVIDENCE_VERIFIED, "corrected evidence cannot remain verified", path))
        if independent:
            issues.append(_issue(EvaluationIssueCode.CORRECTED_EVIDENCE_INDEPENDENT, "corrected evidence cannot remain independent", path))
    return issues


def neutralize_corrected_evidence(evidence):
    if getattr(evidence, "correction_history", None):
        if evidence.state == EvidenceState.VALID:
            evidence.state = EvidenceState.INVALIDATED
        evidence.effective_weight = 0
        evidence.effective_verified = False
        evidence.independent = False
    return evidence
