from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .execution_codes import (
    AbortReasonCode,
    CommitConflictCode,
    ExecutionPortContractViolationCode,
    PUBLIC_ISSUE_MESSAGES,
    PublicEvaluationIssueCode,
    ReservationConflictCode,
    ReservationLifecycleState,
    ReservationStatus,
    ReservationTerminalCode,
    TERMINAL_CODES_BY_STATE,
)
from .operation_binding import EvaluationOperationBinding
from .result_authenticity import EvaluationResultEnvelope




class ExecutionIdentityDigestCollision(RuntimeError):
    """Fixed digest collision failure. Raw identity values must never escape."""

    def __init__(self):
        super().__init__("Execution identity digest collision.")


class ExecutionStorageFailure(RuntimeError):
    """Fixed execution-storage failure. Original storage text must remain outside this module."""

    def __init__(self):
        super().__init__("Evaluation execution storage failed.")


class StrictExecutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


CURRENT_EXECUTION_IDENTITY_ALGORITHM = "hmac-sha256-v1"
SUPPORTED_EXECUTION_IDENTITY_ALGORITHMS = frozenset({CURRENT_EXECUTION_IDENTITY_ALGORITHM})


class ExecutionIdentityDomain(str, Enum):
    REQUEST_ID = "REQUEST_ID"
    IDEMPOTENCY_KEY = "IDEMPOTENCY_KEY"
    RESERVATION_TOKEN = "RESERVATION_TOKEN"


class ExecutionIdentityDigest(StrictExecutionModel):
    domain: ExecutionIdentityDomain
    algorithm_version: str = Field(pattern=r"^[a-z0-9._-]{3,40}$")
    lookup_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    collision_guard_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_digest_pair(self):
        if type(self.domain) is not ExecutionIdentityDomain:
            raise ValueError("EXECUTION_IDENTITY_DOMAIN_REQUIRED")
        if self.lookup_digest == self.collision_guard_digest:
            raise ValueError("EXECUTION_IDENTITY_DIGEST_LABEL_SEPARATION_REQUIRED")
        return self


@dataclass(frozen=True)
class EvaluationOperationIdentity:
    subject_ref: str
    graph_id: str
    evaluation_id: str
    request_id: str
    idempotency_key: str

    @property
    def canonical_key(self) -> str:
        raw = "|".join(
            (
                self.subject_ref,
                self.graph_id,
                self.evaluation_id,
                self.request_id,
                self.idempotency_key,
            )
        )
        return "operation-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def from_binding(cls, binding: EvaluationOperationBinding) -> "EvaluationOperationIdentity":
        return cls(
            subject_ref=binding.subject_ref,
            graph_id=binding.graph_id,
            evaluation_id=binding.evaluation_id,
            request_id=binding.request_id,
            idempotency_key=binding.idempotency_key,
        )


class ReservationOutcome(StrictExecutionModel):
    status: ReservationStatus
    reservation_token: str | None = Field(default=None, min_length=16, max_length=300)
    committed: "CommittedEvaluationOperation | None" = None
    conflict_code: ReservationConflictCode | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if type(self.status) is not ReservationStatus:
            raise ValueError("INVALID_RESERVATION_OUTCOME_TYPE")
        if self.status is ReservationStatus.RESERVED:
            if self.reservation_token is None or self.committed is not None or self.conflict_code is not None:
                raise ValueError("RESERVATION_STATUS_CODE_MISMATCH")
        elif self.status is ReservationStatus.IDEMPOTENT_REPLAY:
            if self.reservation_token is not None or self.committed is None or self.conflict_code is not None:
                raise ValueError("RESERVATION_STATUS_CODE_MISMATCH")
        elif self.status is ReservationStatus.IDEMPOTENCY_CONFLICT:
            if self.reservation_token is not None or self.committed is not None or self.conflict_code not in {
                ReservationConflictCode.IDEMPOTENCY_BASE_REVISION_CONFLICT,
                ReservationConflictCode.IDEMPOTENCY_KEY_REUSE_CONFLICT,
                ReservationConflictCode.REQUEST_ID_REUSE_CONFLICT,
            }:
                raise ValueError("RESERVATION_STATUS_CODE_MISMATCH")
        elif self.status is ReservationStatus.REVISION_CONFLICT:
            if self.reservation_token is not None or self.committed is not None or self.conflict_code is not ReservationConflictCode.EVALUATION_REVISION_CONFLICT:
                raise ValueError("RESERVATION_STATUS_CODE_MISMATCH")
        elif self.status is ReservationStatus.IN_PROGRESS:
            if self.reservation_token is not None or self.committed is not None or self.conflict_code is not ReservationConflictCode.EVALUATION_OPERATION_IN_PROGRESS:
                raise ValueError("RESERVATION_STATUS_CODE_MISMATCH")
        else:  # pragma: no cover - exact enum exhaustiveness
            raise ValueError("UNKNOWN_RESERVATION_STATUS")
        return self


class CommitOutcome(StrictExecutionModel):
    committed: bool
    operation: "CommittedEvaluationOperation | None" = None
    conflict_code: CommitConflictCode | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if type(self.committed) is not bool:
            raise ValueError("INVALID_COMMIT_OUTCOME_TYPE")
        if self.committed:
            if self.operation is None or self.conflict_code is not None:
                raise ValueError("COMMIT_SUCCESS_SHAPE_INVALID")
        elif self.operation is not None or type(self.conflict_code) is not CommitConflictCode:
            raise ValueError("COMMIT_FAILURE_SHAPE_INVALID")
        return self


class AbortOutcome(StrictExecutionModel):
    accepted: bool
    lifecycle_state: ReservationLifecycleState | None = None
    terminal_code: ReservationTerminalCode | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.accepted:
            if self.lifecycle_state is not ReservationLifecycleState.ABORTED or self.terminal_code is None:
                raise ValueError("ABORT_OUTCOME_INVALID")
        elif self.lifecycle_state is not None or self.terminal_code is not None:
            raise ValueError("ABORT_OUTCOME_INVALID")
        return self


class ReservationTombstone(StrictExecutionModel):
    token_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_collision_guard_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    lifecycle_state: ReservationLifecycleState
    terminal_code: ReservationTerminalCode
    subject_ref: str = Field(min_length=1, max_length=200)
    graph_id: str = Field(min_length=1, max_length=200)
    evaluation_id: str = Field(min_length=1, max_length=200)
    expected_existing_revision: int = Field(ge=0)
    operation_binding_digest: str = Field(min_length=16, max_length=200)
    terminal_sequence: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_state_code(self):
        if type(self.lifecycle_state) is not ReservationLifecycleState:
            raise ValueError("UNKNOWN_TERMINAL_STATE")
        if type(self.terminal_code) is not ReservationTerminalCode:
            raise ValueError("UNKNOWN_TERMINAL_CODE")
        if self.terminal_code not in TERMINAL_CODES_BY_STATE[self.lifecycle_state]:
            raise ValueError("UNKNOWN_TERMINAL_CODE")
        return self


class QuarantinedExecutionRecord(StrictExecutionModel):
    record_identity_digest: str = Field(pattern=r"^[a-z0-9-]{16,200}$")
    source_schema_version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,50}$")
    fixed_violation_code: ExecutionPortContractViolationCode = ExecutionPortContractViolationCode.EXECUTION_PORT_CONTRACT_VIOLATION
    quarantined_at_or_sequence: int = Field(ge=1)


class PublicEvaluationError(StrictExecutionModel):
    code: PublicEvaluationIssueCode
    message: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_fixed_message(self):
        if type(self.code) is not PublicEvaluationIssueCode:
            raise ValueError("PUBLIC_EVALUATION_ISSUE_CODE_REQUIRED")
        if self.message != PUBLIC_ISSUE_MESSAGES[self.code]:
            raise ValueError("PUBLIC_EVALUATION_ISSUE_MESSAGE_MISMATCH")
        return self


def build_public_evaluation_error(code: PublicEvaluationIssueCode) -> PublicEvaluationError:
    if type(code) is not PublicEvaluationIssueCode:
        code = PublicEvaluationIssueCode.EVALUATION_EXECUTION_FAILED
    return PublicEvaluationError(code=code, message=PUBLIC_ISSUE_MESSAGES[code])


@dataclass(frozen=True)
class CommittedEvaluationOperation:
    binding: EvaluationOperationBinding
    operation_binding_digest: str
    committed_at: datetime
    request_id_digest: ExecutionIdentityDigest | None = None
    idempotency_key_digest: ExecutionIdentityDigest | None = None

    @property
    def operation_identity(self) -> EvaluationOperationIdentity:
        return EvaluationOperationIdentity.from_binding(self.binding)

    @property
    def request_digest(self) -> str:
        return self.binding.request_digest

    @property
    def expected_existing_revision(self) -> int:
        return self.binding.expected_existing_revision

    @property
    def existing_state_digest(self) -> str:
        return self.binding.existing_state_digest

    @property
    def result_revision(self) -> int:
        return self.binding.result_revision

    @property
    def result_ref(self) -> str:
        assert self.binding.result_ref is not None
        return self.binding.result_ref

    @property
    def result_digest(self) -> str:
        assert self.binding.canonical_result_digest is not None
        return self.binding.canonical_result_digest

    @property
    def trusted_context_digest(self) -> str:
        return self.binding.trusted_context_digest

    @property
    def authority_digest(self) -> str:
        return self.binding.authority_digest

    @property
    def node_contract_set_digest(self) -> str:
        return self.binding.node_contract_set_digest


@runtime_checkable
class ExecutionIdentityDigestPort(Protocol):
    def digest_request_id(self, request_id: str) -> ExecutionIdentityDigest: ...
    def digest_idempotency_key(self, idempotency_key: str) -> ExecutionIdentityDigest: ...
    def hash_reservation_token(self, reservation_token: str) -> ExecutionIdentityDigest: ...


@runtime_checkable
class ReservationRecoveryPolicy(Protocol):
    """Formal composition root may provide one. The module has no production default."""

    def resolve_active_after_restart(self, *, operation_binding_digest: str) -> ReservationLifecycleState: ...


@runtime_checkable
class EvaluationExecutionPort(Protocol):
    def reserve(self, binding: EvaluationOperationBinding) -> ReservationOutcome: ...

    def commit(
        self,
        reservation_token: str,
        binding: EvaluationOperationBinding,
        envelope: EvaluationResultEnvelope,
    ) -> CommitOutcome: ...

    def abort(self, reservation_token: str, reason: AbortReasonCode) -> AbortOutcome: ...

    def load_committed(
        self,
        operation_identity: EvaluationOperationIdentity,
    ) -> CommittedEvaluationOperation | None: ...

    def load_envelope(self, result_ref: str) -> EvaluationResultEnvelope | None: ...
