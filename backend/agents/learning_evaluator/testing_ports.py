from __future__ import annotations

import copy
import hashlib
import hmac
from datetime import datetime, timezone

from .contracts import authority_context_digest
from .models import LearningEvaluationRequest, LearningEvaluationResult, TrustedEvaluationContext
from .operation_binding import EvaluationOperationBinding
from .result_authenticity import (
    EvaluationResultAuthenticityPort,
    EvaluationResultEnvelope,
    VerifiedEnvelopeResult,
    canonical_request_contract_digest,
    canonical_result_digest,
    envelope_binding_payload,
    proof_payload_json,
)


class TestOnlyHmacEvaluationResultAuthenticityPort(EvaluationResultAuthenticityPort):
    __test__ = False
    TEST_ONLY = True
    NOT_FOR_FORMAL_INTEGRATION = True

    def __init__(self, key: bytes, issuer_key_id: str = "test-only-hmac-v1"):
        if not isinstance(key, bytes) or len(key) < 16:
            raise ValueError("TEST_AUTHENTICITY_KEY_TOO_SHORT")
        self._key = bytes(key)
        self.issuer_key_id = issuer_key_id

    def _proof(self, payload: dict) -> str:
        return hmac.new(self._key, proof_payload_json(payload), hashlib.sha256).hexdigest()

    def issue(
        self,
        result: LearningEvaluationResult,
        trusted_context: TrustedEvaluationContext,
        validation_request: LearningEvaluationRequest,
        operation_binding: EvaluationOperationBinding,
    ) -> EvaluationResultEnvelope:
        issued_at = datetime.now(timezone.utc)
        payload = envelope_binding_payload(
            result,
            validation_request,
            trusted_context,
            operation_binding,
            issued_at=issued_at,
            issuer_key_id=self.issuer_key_id,
        )
        proof = self._proof(payload)
        return EvaluationResultEnvelope(
            result=result.model_copy(deep=True),
            validation_request=validation_request.model_copy(deep=True),
            operation_binding=copy.deepcopy(operation_binding),
            operation_binding_digest=payload["operation_binding_digest"],
            canonical_result_digest=payload["canonical_result_digest"],
            request_contract_digest=payload["request_contract_digest"],
            evaluation_id=result.evaluation_id,
            result_revision=result.evaluation_revision,
            subject_ref=result.subject_ref,
            graph_id=result.graph_id,
            graph_continuity_key=result.graph_continuity_key,
            trusted_context_digest=payload["trusted_context_digest"],
            authority_digest=result.authority_context_digest,
            node_contract_set_digest=result.node_contract_set_digest,
            issued_at=issued_at,
            issuer_key_id=self.issuer_key_id,
            authenticity_proof=proof,
        )

    def verify(
        self,
        envelope: EvaluationResultEnvelope,
        trusted_context: TrustedEvaluationContext,
    ) -> VerifiedEnvelopeResult:
        if not isinstance(envelope, EvaluationResultEnvelope):
            raise ValueError("RESULT_ENVELOPE_REQUIRED")
        if envelope.issuer_key_id != self.issuer_key_id:
            raise ValueError("RESULT_AUTHENTICITY_MISMATCH")
        result = envelope.result.model_copy(deep=True)
        request = envelope.validation_request.model_copy(deep=True)
        binding = copy.deepcopy(envelope.operation_binding)
        payload = envelope_binding_payload(
            result,
            request,
            trusted_context,
            binding,
            issued_at=envelope.issued_at,
            issuer_key_id=envelope.issuer_key_id,
        )
        expected = self._proof(payload)
        if not hmac.compare_digest(expected, envelope.authenticity_proof):
            raise ValueError("RESULT_AUTHENTICITY_MISMATCH")
        if canonical_result_digest(result) != envelope.canonical_result_digest:
            raise ValueError("RESULT_DIGEST_MISMATCH")
        if canonical_request_contract_digest(request) != envelope.request_contract_digest:
            raise ValueError("RESULT_REQUEST_CONTRACT_MISMATCH")
        if envelope.operation_binding_digest != binding.operation_binding_digest:
            raise ValueError("RESULT_OPERATION_BINDING_MISMATCH")
        if envelope.trusted_context_digest != authority_context_digest(trusted_context):
            raise ValueError("RESULT_AUTHENTICITY_CONTEXT_MISMATCH")
        for actual, expected_value in (
            (envelope.evaluation_id, result.evaluation_id),
            (envelope.result_revision, result.evaluation_revision),
            (envelope.subject_ref, result.subject_ref),
            (envelope.graph_id, result.graph_id),
            (envelope.graph_continuity_key, result.graph_continuity_key),
            (envelope.authority_digest, result.authority_context_digest),
            (envelope.node_contract_set_digest, result.node_contract_set_digest),
            (binding.evaluation_id, result.evaluation_id),
            (binding.result_revision, result.evaluation_revision),
            (binding.subject_ref, result.subject_ref),
            (binding.graph_id, result.graph_id),
            (binding.graph_continuity_key, result.graph_continuity_key),
            (binding.request_id, request.request_id),
            (binding.idempotency_key, request.idempotency_key),
            (binding.expected_existing_revision, request.expected_existing_revision),
            (binding.evaluation_mode, request.evaluation_mode.value),
            (binding.canonical_result_digest, envelope.canonical_result_digest),
        ):
            if actual != expected_value:
                raise ValueError("RESULT_PROVENANCE_MISMATCH")
        return VerifiedEnvelopeResult(
            result=copy.deepcopy(result),
            validation_request=copy.deepcopy(request),
            operation_binding=copy.deepcopy(binding),
            envelope=copy.deepcopy(envelope),
        )
