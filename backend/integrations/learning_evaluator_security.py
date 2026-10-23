"""Formal cryptographic ports for Learning Evaluator v0.1.10.

The candidate core remains immutable. This module supplies project-owned HMAC
identity digests, result-envelope authenticity and sealed private persistence.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from agents.learning_evaluator.contracts import authority_context_digest
from agents.learning_evaluator.execution import (
    CURRENT_EXECUTION_IDENTITY_ALGORITHM,
    ExecutionIdentityDigest,
    ExecutionIdentityDomain,
)
from agents.learning_evaluator.models import (
    LearningEvaluationRequest,
    LearningEvaluationResult,
    TrustedEvaluationContext,
)
from agents.learning_evaluator.operation_binding import EvaluationOperationBinding
from agents.learning_evaluator.result_authenticity import (
    EvaluationResultEnvelope,
    VerifiedEnvelopeResult,
    canonical_request_contract_digest,
    canonical_result_digest,
    envelope_binding_payload,
    proof_payload_json,
)
from config import settings


class EvaluatorSecurityConfigurationError(RuntimeError):
    pass


def _master_secret() -> bytes:
    value = os.environ.get("EVALUATOR_MASTER_KEY") or str(getattr(settings, "SECRET_KEY", ""))
    placeholders = {
        "",
        "your-secret-key-change-in-production",
        "dev-only-do-not-use-in-production-please-set-env-var",
    }
    if str(getattr(settings, "MODE", "mock")) == "production" and value in placeholders:
        raise EvaluatorSecurityConfigurationError("EVALUATOR_SECURITY_KEY_REQUIRED")
    if not value:
        raise EvaluatorSecurityConfigurationError("EVALUATOR_SECURITY_KEY_REQUIRED")
    return value.encode("utf-8")


def _derive(label: str, length: int = 32) -> bytes:
    return hmac.new(_master_secret(), f"kaki-evaluator|{label}|v1".encode(), hashlib.sha256).digest()[:length]


class FormalHmacExecutionIdentityDigestPort:
    ALGORITHM_VERSION = CURRENT_EXECUTION_IDENTITY_ALGORITHM

    def __init__(self, key: bytes | None = None):
        self._key = bytes(key or _derive("execution-identity"))
        if len(self._key) < 16 or not any(self._key):
            raise EvaluatorSecurityConfigurationError("EVALUATOR_DIGEST_KEY_INVALID")

    def _digest(self, domain: ExecutionIdentityDomain, value: str) -> ExecutionIdentityDigest:
        if type(domain) is not ExecutionIdentityDomain or not isinstance(value, str) or not value:
            raise TypeError("EXECUTION_IDENTITY_INPUT_INVALID")
        label = domain.value.lower()
        lookup = hmac.new(self._key, f"lookup|{label}|{value}".encode(), hashlib.sha256).hexdigest()
        guard = hmac.new(self._key, f"guard|{label}|{value}".encode(), hashlib.sha256).hexdigest()
        return ExecutionIdentityDigest(
            domain=domain,
            algorithm_version=self.ALGORITHM_VERSION,
            lookup_digest=lookup,
            collision_guard_digest=guard,
        )

    def digest_request_id(self, request_id: str) -> ExecutionIdentityDigest:
        return self._digest(ExecutionIdentityDomain.REQUEST_ID, request_id)

    def digest_idempotency_key(self, idempotency_key: str) -> ExecutionIdentityDigest:
        return self._digest(ExecutionIdentityDomain.IDEMPOTENCY_KEY, idempotency_key)

    def hash_reservation_token(self, reservation_token: str) -> ExecutionIdentityDigest:
        return self._digest(ExecutionIdentityDomain.RESERVATION_TOKEN, reservation_token)


class FormalHmacEvaluationResultAuthenticityPort:
    def __init__(self, key: bytes | None = None, issuer_key_id: str = "kaki-evaluator-hmac-v1"):
        self._key = bytes(key or _derive("result-authenticity"))
        if len(self._key) < 16:
            raise EvaluatorSecurityConfigurationError("EVALUATOR_AUTHENTICITY_KEY_INVALID")
        self.issuer_key_id = issuer_key_id

    def _proof(self, payload: dict[str, Any]) -> str:
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
            authenticity_proof=self._proof(payload),
        )

    def verify(
        self,
        envelope: EvaluationResultEnvelope,
        trusted_context: TrustedEvaluationContext,
    ) -> VerifiedEnvelopeResult:
        if not isinstance(envelope, EvaluationResultEnvelope) or envelope.issuer_key_id != self.issuer_key_id:
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
        if not hmac.compare_digest(self._proof(payload), envelope.authenticity_proof):
            raise ValueError("RESULT_AUTHENTICITY_MISMATCH")
        if canonical_result_digest(result) != envelope.canonical_result_digest:
            raise ValueError("RESULT_DIGEST_MISMATCH")
        if canonical_request_contract_digest(request) != envelope.request_contract_digest:
            raise ValueError("RESULT_REQUEST_CONTRACT_MISMATCH")
        if envelope.operation_binding_digest != binding.operation_binding_digest:
            raise ValueError("RESULT_OPERATION_BINDING_MISMATCH")
        if envelope.trusted_context_digest != authority_context_digest(trusted_context):
            raise ValueError("RESULT_AUTHENTICITY_CONTEXT_MISMATCH")
        checks = (
            (envelope.evaluation_id, result.evaluation_id),
            (envelope.result_revision, result.evaluation_revision),
            (envelope.subject_ref, result.subject_ref),
            (envelope.graph_id, result.graph_id),
            (envelope.graph_continuity_key, result.graph_continuity_key),
            (envelope.authority_digest, result.authority_context_digest),
            (envelope.node_contract_set_digest, result.node_contract_set_digest),
            (binding.request_id, request.request_id),
            (binding.idempotency_key, request.idempotency_key),
            (binding.expected_existing_revision, request.expected_existing_revision),
            (binding.evaluation_mode, request.evaluation_mode.value),
        )
        if any(actual != expected for actual, expected in checks):
            raise ValueError("RESULT_PROVENANCE_MISMATCH")
        return VerifiedEnvelopeResult(
            result=copy.deepcopy(result),
            validation_request=copy.deepcopy(request),
            operation_binding=copy.deepcopy(binding),
            envelope=copy.deepcopy(envelope),
        )


class EvaluatorEnvelopeCipher:
    def __init__(self, key: bytes | None = None):
        raw = bytes(key or _derive("private-envelope"))
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))

    def seal_envelope(self, envelope: EvaluationResultEnvelope) -> str:
        payload = {**envelope.envelope_payload(), "authenticity_proof": envelope.authenticity_proof}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return self._fernet.encrypt(raw).decode("ascii")

    def open_envelope(self, token: str) -> EvaluationResultEnvelope:
        try:
            data = json.loads(self._fernet.decrypt(token.encode("ascii")).decode("utf-8"))
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("EVALUATOR_PRIVATE_ENVELOPE_INVALID") from exc
        return EvaluationResultEnvelope(
            result=LearningEvaluationResult.model_validate(data["result"]),
            validation_request=LearningEvaluationRequest.model_validate(data["validation_request"]),
            operation_binding=EvaluationOperationBinding(**data["operation_binding"]),
            operation_binding_digest=data["operation_binding_digest"],
            canonical_result_digest=data["canonical_result_digest"],
            request_contract_digest=data["request_contract_digest"],
            evaluation_id=data["evaluation_id"],
            result_revision=int(data["result_revision"]),
            subject_ref=data["subject_ref"],
            graph_id=data["graph_id"],
            graph_continuity_key=data["graph_continuity_key"],
            trusted_context_digest=data["trusted_context_digest"],
            authority_digest=data["authority_digest"],
            node_contract_set_digest=data["node_contract_set_digest"],
            issued_at=datetime.fromisoformat(data["issued_at"]),
            issuer_key_id=data["issuer_key_id"],
            authenticity_proof=data["authenticity_proof"],
        )
