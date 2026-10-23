from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class EvaluationOperationBinding:
    """Closed operation identity shared by reservation, commit, replay and envelope.

    Result fields are ``None`` only while a reservation is pending. A committed
    binding must contain all fields and must pass ``validate_committed``.
    """

    subject_ref: str
    graph_id: str
    graph_continuity_key: str
    evaluation_id: str
    request_id: str
    idempotency_key: str
    evaluation_mode: str
    expected_existing_revision: int
    result_revision: int
    request_digest: str
    existing_state_digest: str
    trusted_context_digest: str
    authority_digest: str
    node_contract_set_digest: str
    canonical_result_digest: str | None = None
    result_ref: str | None = None

    def canonical_payload(self) -> dict:
        return asdict(self)

    @property
    def operation_binding_digest(self) -> str:
        raw = _canonical_json(self.canonical_payload())
        return "operation-binding-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def precommit_payload(self) -> dict:
        payload = self.canonical_payload()
        payload["canonical_result_digest"] = None
        payload["result_ref"] = None
        return payload

    @property
    def precommit_digest(self) -> str:
        raw = _canonical_json(self.precommit_payload)
        return "operation-precommit-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def with_result(self, *, canonical_result_digest: str, result_ref: str | None = None) -> "EvaluationOperationBinding":
        deterministic = deterministic_result_ref(
            self.evaluation_id,
            self.result_revision,
            canonical_result_digest,
        )
        if result_ref is not None and result_ref != deterministic:
            raise ValueError("RESULT_REF_NOT_DETERMINISTIC")
        return replace(
            self,
            canonical_result_digest=canonical_result_digest,
            result_ref=deterministic,
        )

    def validate_committed(self) -> None:
        if not self.canonical_result_digest or not self.result_ref:
            raise ValueError("OPERATION_BINDING_RESULT_FIELDS_MISSING")
        expected = deterministic_result_ref(
            self.evaluation_id,
            self.result_revision,
            self.canonical_result_digest,
        )
        if self.result_ref != expected:
            raise ValueError("RESULT_REF_NOT_DETERMINISTIC")
        if self.result_revision != self.expected_existing_revision + 1:
            raise ValueError("EVALUATION_REVISION_CONFLICT")


def deterministic_result_ref(evaluation_id: str, result_revision: int, canonical_result_digest: str) -> str:
    raw = _canonical_json(
        {
            "evaluation_id": evaluation_id,
            "result_revision": result_revision,
            "canonical_result_digest": canonical_result_digest,
        }
    )
    return "evaluation-result-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compare_bindings(expected: EvaluationOperationBinding, actual: EvaluationOperationBinding) -> list[str]:
    mismatches: list[str] = []
    for field in expected.__dataclass_fields__:
        if getattr(expected, field) != getattr(actual, field):
            mismatches.append(field)
    return mismatches
