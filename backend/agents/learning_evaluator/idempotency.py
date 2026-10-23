from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import authority_context_digest, node_contract_set_digest
from .execution import EvaluationExecutionPort
from .models import ExistingEvaluationSummary, LearningEvaluationRequest, TrustedCorrectionContext, TrustedEvaluationContext
from .objective_mapper import objective_allowed_dimensions, required_dimensions


# Backward-compatible names. This is now an atomic execution Port, not find/find/record.
EvaluationIdempotencyPort = EvaluationExecutionPort


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def existing_state_digest(existing: ExistingEvaluationSummary | None) -> str:
    if existing is None:
        return "existing-state-none"
    payload = existing.model_dump(mode="json")
    # Caller-supplied processed snapshots are never part of trusted state or replay.
    payload.pop("processed_requests", None)
    raw = _canonical_json(payload)
    return "existing-state-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonical_evidence(records: list) -> list[dict]:
    canonical: list[tuple[str, str, dict]] = []
    seen: dict[str, str] = {}
    for record in records:
        payload = record.model_dump(mode="json")
        digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        previous = seen.get(record.evidence_id)
        if previous is not None and previous != digest:
            raise ValueError("DUPLICATE_EVIDENCE_ID_PAYLOAD_CONFLICT")
        seen[record.evidence_id] = digest
        canonical.append((record.evidence_id, digest, payload))
    canonical.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in canonical]


def _canonical_corrections(request: LearningEvaluationRequest) -> list[dict]:
    # Correction intents are not a correction-history chain; stable ordering is by
    # evidence + idempotency identity. The trusted server context supplies event order.
    payload = [item.model_dump(mode="json") for item in request.evidence_corrections]
    payload.sort(key=lambda x: (x["evidence_id"], x["idempotency_key"]))
    return payload


def evaluation_request_digest(
    request: LearningEvaluationRequest,
    trusted_context: TrustedEvaluationContext,
    trusted_correction_context: TrustedCorrectionContext | None = None,
) -> str:
    expected_revision = request.expected_existing_revision
    if expected_revision is None:
        raise ValueError("EXPECTED_EXISTING_REVISION_REQUIRED")
    request_payload = request.model_dump(mode="json")
    request_payload["evidence_records"] = _canonical_evidence(request.evidence_records)
    request_payload["evidence_corrections"] = _canonical_corrections(request)
    # Existing state is represented by a trusted semantic digest, not a caller replay snapshot.
    request_payload.pop("existing_evaluation_summary", None)
    request_payload["existing_state_digest"] = existing_state_digest(request.existing_evaluation_summary)
    request_payload["expected_existing_revision"] = expected_revision
    request_payload["node_contract_set_digest"] = node_contract_set_digest(
        request.node_contracts,
        required_dimensions,
        objective_allowed_dimensions,
    )
    execution = {
        "request": request_payload,
        "trusted_context_digest": authority_context_digest(trusted_context),
        "authority_digest": authority_context_digest(trusted_context),
        "trusted_context_version": trusted_context.context_version,
        "correction_context": (
            trusted_correction_context.model_dump(mode="json")
            if trusted_correction_context is not None
            else None
        ),
    }
    raw = _canonical_json(execution)
    return "request-digest-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def embedded_processed_requests(existing: ExistingEvaluationSummary | None):
    # Deprecated compatibility helper. Embedded records are explicitly untrusted and
    # never used for replay in v0.1.5.
    return list(existing.processed_requests) if existing is not None else []
