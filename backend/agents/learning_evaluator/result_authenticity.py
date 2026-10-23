from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from .contracts import authority_context_digest
from .models import LearningEvaluationRequest, LearningEvaluationResult, TrustedEvaluationContext
from .operation_binding import EvaluationOperationBinding


def canonical_result_digest(result: LearningEvaluationResult) -> str:
    payload = result.model_dump(mode="json")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "result-digest-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def canonical_request_contract_digest(request: LearningEvaluationRequest) -> str:
    payload = request.model_dump(mode="json")
    payload.pop("evaluated_at", None)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "request-contract-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvaluationResultEnvelope:
    """Server-issued result envelope verified by an external authenticity Port."""

    result: LearningEvaluationResult
    validation_request: LearningEvaluationRequest
    operation_binding: EvaluationOperationBinding
    operation_binding_digest: str
    canonical_result_digest: str
    request_contract_digest: str
    evaluation_id: str
    result_revision: int
    subject_ref: str
    graph_id: str
    graph_continuity_key: str
    trusted_context_digest: str
    authority_digest: str
    node_contract_set_digest: str
    issued_at: datetime
    issuer_key_id: str
    authenticity_proof: str

    def __getattr__(self, name: str):
        data = object.__getattribute__(self, "__dict__")
        result = data.get("result")
        if result is None:
            raise AttributeError(name)
        return copy.deepcopy(getattr(result, name))

    def __copy__(self):
        return self.__deepcopy__({})

    def __deepcopy__(self, memo):
        copied = EvaluationResultEnvelope(
            result=copy.deepcopy(self.result, memo),
            validation_request=copy.deepcopy(self.validation_request, memo),
            operation_binding=copy.deepcopy(self.operation_binding, memo),
            operation_binding_digest=self.operation_binding_digest,
            canonical_result_digest=self.canonical_result_digest,
            request_contract_digest=self.request_contract_digest,
            evaluation_id=self.evaluation_id,
            result_revision=self.result_revision,
            subject_ref=self.subject_ref,
            graph_id=self.graph_id,
            graph_continuity_key=self.graph_continuity_key,
            trusted_context_digest=self.trusted_context_digest,
            authority_digest=self.authority_digest,
            node_contract_set_digest=self.node_contract_set_digest,
            issued_at=self.issued_at,
            issuer_key_id=self.issuer_key_id,
            authenticity_proof=self.authenticity_proof,
        )
        memo[id(self)] = copied
        return copied

    def model_dump(self, *args, **kwargs):
        return self.result.model_dump(*args, **kwargs)

    def model_dump_json(self, *args, **kwargs):
        return self.result.model_dump_json(*args, **kwargs)

    def model_copy(self, *args, **kwargs) -> LearningEvaluationResult:
        return self.result.model_copy(*args, **kwargs)

    def envelope_payload(self) -> dict:
        return {
            "result": self.result.model_dump(mode="json"),
            "validation_request": self.validation_request.model_dump(mode="json"),
            "operation_binding": self.operation_binding.canonical_payload(),
            "operation_binding_digest": self.operation_binding_digest,
            "canonical_result_digest": self.canonical_result_digest,
            "request_contract_digest": self.request_contract_digest,
            "evaluation_id": self.evaluation_id,
            "result_revision": self.result_revision,
            "subject_ref": self.subject_ref,
            "graph_id": self.graph_id,
            "graph_continuity_key": self.graph_continuity_key,
            "trusted_context_digest": self.trusted_context_digest,
            "authority_digest": self.authority_digest,
            "node_contract_set_digest": self.node_contract_set_digest,
            "issued_at": self.issued_at.isoformat(),
            "issuer_key_id": self.issuer_key_id,
        }


@dataclass(frozen=True)
class VerifiedEnvelopeResult:
    result: LearningEvaluationResult
    validation_request: LearningEvaluationRequest
    operation_binding: EvaluationOperationBinding
    envelope: EvaluationResultEnvelope


@runtime_checkable
class EvaluationResultAuthenticityPort(Protocol):
    def issue(
        self,
        result: LearningEvaluationResult,
        trusted_context: TrustedEvaluationContext,
        validation_request: LearningEvaluationRequest,
        operation_binding: EvaluationOperationBinding,
    ) -> EvaluationResultEnvelope: ...

    def verify(
        self,
        envelope: EvaluationResultEnvelope,
        trusted_context: TrustedEvaluationContext,
    ) -> VerifiedEnvelopeResult: ...


def envelope_binding_payload(
    result: LearningEvaluationResult,
    request: LearningEvaluationRequest,
    trusted_context: TrustedEvaluationContext,
    operation_binding: EvaluationOperationBinding,
    *,
    issued_at: datetime,
    issuer_key_id: str,
) -> dict:
    digest = canonical_result_digest(result)
    operation_binding.validate_committed()
    if operation_binding.canonical_result_digest != digest:
        raise ValueError("RESULT_DIGEST_MISMATCH")
    return {
        "operation_binding": operation_binding.canonical_payload(),
        "operation_binding_digest": operation_binding.operation_binding_digest,
        "canonical_result_digest": digest,
        "request_contract_digest": canonical_request_contract_digest(request),
        "evaluation_id": result.evaluation_id,
        "result_revision": result.evaluation_revision,
        "subject_ref": result.subject_ref,
        "graph_id": result.graph_id,
        "graph_continuity_key": result.graph_continuity_key,
        "trusted_context_digest": authority_context_digest(trusted_context),
        "authority_digest": result.authority_context_digest,
        "node_contract_set_digest": result.node_contract_set_digest,
        "issued_at": issued_at.isoformat(),
        "issuer_key_id": issuer_key_id,
    }


def proof_payload_json(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
