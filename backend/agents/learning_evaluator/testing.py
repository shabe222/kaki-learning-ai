from __future__ import annotations

import copy
import hashlib
import hmac
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .execution import (
    AbortOutcome,
    CommitOutcome,
    CommittedEvaluationOperation,
    EvaluationOperationIdentity,
    ExecutionIdentityDigest,
    ExecutionIdentityDigestCollision,
    ExecutionIdentityDigestPort,
    ExecutionIdentityDomain,
    CURRENT_EXECUTION_IDENTITY_ALGORITHM,
    ExecutionStorageFailure,
    QuarantinedExecutionRecord,
    ReservationOutcome,
    ReservationTombstone,
)
from .execution_codes import (
    ABORT_TO_TERMINAL,
    COMMIT_REJECTION_TO_TERMINAL,
    AbortReasonCode,
    CommitConflictCode,
    ExecutionPortContractViolationCode,
    MAPPING_VERSION,
    ReservationConflictCode,
    ReservationLifecycleState,
    ReservationStatus,
    ReservationTerminalCode,
)
from .operation_binding import EvaluationOperationBinding, deterministic_result_ref
from .execution_digest_validation import (
    ExecutionIdentityDigestValidationSource,
    validate_execution_identity_digest,
)
from .execution_migration import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_SCHEMA_VERSION,
    migrate_v017_tombstone,
)
from .result_authenticity import EvaluationResultEnvelope, canonical_result_digest


class TestOnlyHmacExecutionIdentityDigestPort:
    """TEST_ONLY / NOT_FOR_FORMAL_INTEGRATION keyed digest capability.

    The in-memory execution Port accepts this exact concrete class only. Tests
    may force lookup digests for collision probes, while collision guards remain
    independently keyed and domain separated.
    """

    TEST_ONLY = True
    NOT_FOR_FORMAL_INTEGRATION = True
    ALGORITHM_VERSION = CURRENT_EXECUTION_IDENTITY_ALGORITHM

    def __init__(
        self,
        key: bytes,
        *,
        forced_lookup_by_domain: dict[ExecutionIdentityDomain, str] | None = None,
    ):
        if not isinstance(key, bytes) or len(key) < 16 or not any(key):
            raise ValueError("TEST_DIGEST_KEY_REQUIRED")
        self._key = bytes(key)
        self._forced_lookup_by_domain = dict(forced_lookup_by_domain or {})
        if any(type(k) is not ExecutionIdentityDomain for k in self._forced_lookup_by_domain):
            raise TypeError("TEST_DIGEST_DOMAIN_REQUIRED")
        if any(not isinstance(v, str) or len(v) != 64 or any(c not in "0123456789abcdef" for c in v) for v in self._forced_lookup_by_domain.values()):
            raise ValueError("TEST_FORCED_LOOKUP_DIGEST_INVALID")

    def _digest(self, domain: ExecutionIdentityDomain, value: str) -> ExecutionIdentityDigest:
        if type(domain) is not ExecutionIdentityDomain:
            raise TypeError("IDENTITY_DOMAIN_REQUIRED")
        if not isinstance(value, str) or not value:
            raise TypeError("IDENTITY_VALUE_REQUIRED")
        label = domain.value.lower()
        lookup = self._forced_lookup_by_domain.get(domain)
        if lookup is None:
            lookup = hmac.new(
                self._key,
                f"lookup|{label}|{value}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        guard = hmac.new(
            self._key,
            f"guard|{label}|{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        result = ExecutionIdentityDigest(
            domain=domain,
            algorithm_version=self.ALGORITHM_VERSION,
            lookup_digest=lookup,
            collision_guard_digest=guard,
        )
        if value == result.lookup_digest or value == result.collision_guard_digest or value in result.lookup_digest or value in result.collision_guard_digest:
            raise ValueError("RAW_IDENTITY_DIGEST_LEAK")
        return result

    def digest_request_id(self, request_id: str) -> ExecutionIdentityDigest:
        return self._digest(ExecutionIdentityDomain.REQUEST_ID, request_id)

    def digest_idempotency_key(self, idempotency_key: str) -> ExecutionIdentityDigest:
        return self._digest(ExecutionIdentityDomain.IDEMPOTENCY_KEY, idempotency_key)

    def hash_reservation_token(self, reservation_token: str) -> ExecutionIdentityDigest:
        return self._digest(ExecutionIdentityDomain.RESERVATION_TOKEN, reservation_token)


def make_test_execution_identity_digest_port(key: bytes) -> TestOnlyHmacExecutionIdentityDigestPort:
    return TestOnlyHmacExecutionIdentityDigestPort(key=key)



@dataclass
class _ActiveReservationRecord:
    token_hash: str
    subject_ref: str
    graph_id: str
    graph_continuity_key: str
    evaluation_id: str
    request_id_digest: ExecutionIdentityDigest
    idempotency_key_digest: ExecutionIdentityDigest
    token_collision_guard_digest: str
    evaluation_mode: str
    expected_existing_revision: int
    request_digest: str
    existing_state_digest: str
    trusted_context_digest: str
    authority_digest: str
    node_contract_set_digest: str
    precommit_digest: str
    reservation_sequence: int
    lifecycle_state: ReservationLifecycleState = ReservationLifecycleState.ACTIVE


@dataclass
class _RepositorySnapshot:
    active_reservations: dict[str, _ActiveReservationRecord]
    tombstones: dict[str, ReservationTombstone]
    terminal_sequence: int
    reservation_sequence: int
    active_by_evaluation: dict[tuple[str, str, str], str]
    committed_by_operation: dict[str, CommittedEvaluationOperation]
    by_idempotency: dict[tuple[str, str, str], CommittedEvaluationOperation]
    by_request: dict[tuple[str, str, str], CommittedEvaluationOperation]
    current_revision: dict[tuple[str, str, str], int]
    envelopes: dict[str, EvaluationResultEnvelope]
    quarantined: list[QuarantinedExecutionRecord]
    quarantine_sequence: int


@dataclass
class TestOnlyExecutionRepository:
    """TEST_ONLY durable reference store with copy-on-write transactions."""

    TEST_ONLY: bool = True
    NOT_FOR_FORMAL_INTEGRATION: bool = True
    lock: threading.RLock = field(default_factory=threading.RLock)
    active_reservations: dict[str, _ActiveReservationRecord] = field(default_factory=dict)
    tombstones: dict[str, ReservationTombstone] = field(default_factory=dict)
    terminal_sequence: int = 0
    reservation_sequence: int = 0
    active_by_evaluation: dict[tuple[str, str, str], str] = field(default_factory=dict)
    committed_by_operation: dict[str, CommittedEvaluationOperation] = field(default_factory=dict)
    by_idempotency: dict[tuple[str, str, str], CommittedEvaluationOperation] = field(default_factory=dict)
    by_request: dict[tuple[str, str, str], CommittedEvaluationOperation] = field(default_factory=dict)
    current_revision: dict[tuple[str, str, str], int] = field(default_factory=dict)
    envelopes: dict[str, EvaluationResultEnvelope] = field(default_factory=dict)
    quarantined: list[QuarantinedExecutionRecord] = field(default_factory=list)
    quarantine_sequence: int = 0
    fault_injection_point: str | None = None
    fault_injection_once: bool = True
    _fault_consumed: bool = False

    def set_fault_injection(self, point: str | None, *, once: bool = True) -> None:
        self.fault_injection_point = point
        self.fault_injection_once = once
        self._fault_consumed = False

    def _maybe_fail(self, point: str) -> None:
        if self.fault_injection_point == point and (not self.fault_injection_once or not self._fault_consumed):
            self._fault_consumed = True
            raise OSError("TEST_ONLY_EXECUTION_STORAGE_FAILURE")

    def snapshot(self) -> _RepositorySnapshot:
        return _RepositorySnapshot(
            active_reservations=copy.deepcopy(self.active_reservations),
            tombstones=copy.deepcopy(self.tombstones),
            terminal_sequence=self.terminal_sequence,
            reservation_sequence=self.reservation_sequence,
            active_by_evaluation=copy.deepcopy(self.active_by_evaluation),
            committed_by_operation=copy.deepcopy(self.committed_by_operation),
            by_idempotency=copy.deepcopy(self.by_idempotency),
            by_request=copy.deepcopy(self.by_request),
            current_revision=copy.deepcopy(self.current_revision),
            envelopes=copy.deepcopy(self.envelopes),
            quarantined=copy.deepcopy(self.quarantined),
            quarantine_sequence=self.quarantine_sequence,
        )

    def _replace(self, staged: _RepositorySnapshot) -> None:
        # No fallible storage operations occur after the final-swap checkpoint.
        self.active_reservations = staged.active_reservations
        self.tombstones = staged.tombstones
        self.terminal_sequence = staged.terminal_sequence
        self.reservation_sequence = staged.reservation_sequence
        self.active_by_evaluation = staged.active_by_evaluation
        self.committed_by_operation = staged.committed_by_operation
        self.by_idempotency = staged.by_idempotency
        self.by_request = staged.by_request
        self.current_revision = staged.current_revision
        self.envelopes = staged.envelopes
        self.quarantined = staged.quarantined
        self.quarantine_sequence = staged.quarantine_sequence

    @contextmanager
    def transaction(self):
        with self.lock:
            staged = self.snapshot()
            try:
                yield staged
                self._maybe_fail("transaction_final_swap")
            except BaseException:
                raise
            self._replace(staged)




class InMemoryEvaluationExecutionPort:
    """TEST_ONLY / NOT_FOR_FORMAL_INTEGRATION atomic reference Port."""

    TEST_ONLY = True
    NOT_FOR_FORMAL_INTEGRATION = True

    def __init__(
        self,
        *,
        identity_digest_port: ExecutionIdentityDigestPort,
        repository: TestOnlyExecutionRepository | None = None,
    ):
        if type(identity_digest_port) is not TestOnlyHmacExecutionIdentityDigestPort:
            raise TypeError("TEST_HMAC_EXECUTION_IDENTITY_DIGEST_PORT_REQUIRED")
        if identity_digest_port.TEST_ONLY is not True or identity_digest_port.NOT_FOR_FORMAL_INTEGRATION is not True:
            raise TypeError("TEST_IDENTITY_DIGEST_PORT_MARKERS_REQUIRED")
        self.identity_digest_port = identity_digest_port
        self._repo = repository or TestOnlyExecutionRepository()
        if type(self._repo) is not TestOnlyExecutionRepository:
            raise TypeError("TEST_EXECUTION_REPOSITORY_REQUIRED")
        self._lock = self._repo.lock
        self._refresh_aliases()

    def _refresh_aliases(self) -> None:
        # Compatibility aliases are implementation details used by retained tests.
        self._reservations = self._repo.active_reservations
        self._reservation_tombstones = self._repo.tombstones
        self._active_by_evaluation = self._repo.active_by_evaluation
        self._committed_by_operation = self._repo.committed_by_operation
        self._by_idempotency = self._repo.by_idempotency
        self._by_request = self._repo.by_request
        self._current_revision = self._repo.current_revision
        self._envelopes = self._repo.envelopes

    @staticmethod
    def _eval_key(binding: EvaluationOperationBinding) -> tuple[str, str, str]:
        return (binding.subject_ref, binding.graph_id, binding.evaluation_id)

    @staticmethod
    def _require_digest(
        digest: ExecutionIdentityDigest,
        domain: ExecutionIdentityDomain,
        raw_value: str,
    ) -> ExecutionIdentityDigest:
        return validate_execution_identity_digest(
            digest,
            expected_domain=domain,
            raw_value=raw_value,
            source=ExecutionIdentityDigestValidationSource.PORT_OUTPUT,
        )

    def _request_digest(self, request_id: str) -> ExecutionIdentityDigest:
        return self._require_digest(
            self.identity_digest_port.digest_request_id(request_id),
            ExecutionIdentityDomain.REQUEST_ID,
            request_id,
        )

    def _idempotency_digest(self, key: str) -> ExecutionIdentityDigest:
        return self._require_digest(
            self.identity_digest_port.digest_idempotency_key(key),
            ExecutionIdentityDomain.IDEMPOTENCY_KEY,
            key,
        )

    def _token_digest(self, token: str) -> ExecutionIdentityDigest:
        return self._require_digest(
            self.identity_digest_port.hash_reservation_token(token),
            ExecutionIdentityDomain.RESERVATION_TOKEN,
            token,
        )

    @staticmethod
    def _assert_guard(existing: ExecutionIdentityDigest | None, incoming: ExecutionIdentityDigest) -> None:
        if existing is not None and (
            existing.lookup_digest == incoming.lookup_digest
            and existing.collision_guard_digest != incoming.collision_guard_digest
        ):
            raise ExecutionIdentityDigestCollision()

    def _idem_key_from_digest(self, binding: EvaluationOperationBinding, digest: ExecutionIdentityDigest) -> tuple[str, str, str]:
        return (binding.subject_ref, binding.graph_id, digest.lookup_digest)

    def _request_key_from_digest(self, binding: EvaluationOperationBinding, digest: ExecutionIdentityDigest) -> tuple[str, str, str]:
        return (binding.subject_ref, binding.graph_id, digest.lookup_digest)

    def _idem_key(self, binding: EvaluationOperationBinding) -> tuple[str, str, str]:
        return self._idem_key_from_digest(binding, self._idempotency_digest(binding.idempotency_key))

    def _request_key(self, binding: EvaluationOperationBinding) -> tuple[str, str, str]:
        return self._request_key_from_digest(binding, self._request_digest(binding.request_id))

    def _operation_key_from_digests(
        self,
        binding: EvaluationOperationBinding,
        request_digest: ExecutionIdentityDigest,
        idempotency_digest: ExecutionIdentityDigest,
    ) -> str:
        raw = "|".join(
            (
                binding.subject_ref,
                binding.graph_id,
                binding.evaluation_id,
                request_digest.lookup_digest,
                request_digest.collision_guard_digest,
                idempotency_digest.lookup_digest,
                idempotency_digest.collision_guard_digest,
            )
        )
        return "operation-" + hashlib.sha256(raw.encode()).hexdigest()

    def _operation_key(self, binding: EvaluationOperationBinding) -> str:
        return self._operation_key_from_digests(
            binding,
            self._request_digest(binding.request_id),
            self._idempotency_digest(binding.idempotency_key),
        )

    def _token_hash(self, token: str) -> str:
        return self._token_digest(token).lookup_digest

    def _record_from_binding(
        self,
        token_digest: ExecutionIdentityDigest,
        binding: EvaluationOperationBinding,
        request_digest: ExecutionIdentityDigest | None = None,
        idempotency_digest: ExecutionIdentityDigest | None = None,
    ) -> _ActiveReservationRecord:
        request_digest = request_digest or self._request_digest(binding.request_id)
        idempotency_digest = idempotency_digest or self._idempotency_digest(binding.idempotency_key)
        self._repo.reservation_sequence += 1
        return _ActiveReservationRecord(
            token_hash=token_digest.lookup_digest,
            token_collision_guard_digest=token_digest.collision_guard_digest,
            subject_ref=binding.subject_ref,
            graph_id=binding.graph_id,
            graph_continuity_key=binding.graph_continuity_key,
            evaluation_id=binding.evaluation_id,
            request_id_digest=request_digest,
            idempotency_key_digest=idempotency_digest,
            evaluation_mode=binding.evaluation_mode,
            expected_existing_revision=binding.expected_existing_revision,
            request_digest=binding.request_digest,
            existing_state_digest=binding.existing_state_digest,
            trusted_context_digest=binding.trusted_context_digest,
            authority_digest=binding.authority_digest,
            node_contract_set_digest=binding.node_contract_set_digest,
            precommit_digest=binding.precommit_digest,
            reservation_sequence=self._repo.reservation_sequence,
        )

    def _record_matches_binding(self, record: _ActiveReservationRecord, binding: EvaluationOperationBinding) -> bool:
        request_digest = self._request_digest(binding.request_id)
        idempotency_digest = self._idempotency_digest(binding.idempotency_key)
        return all(
            (
                record.subject_ref == binding.subject_ref,
                record.graph_id == binding.graph_id,
                record.graph_continuity_key == binding.graph_continuity_key,
                record.evaluation_id == binding.evaluation_id,
                record.request_id_digest == request_digest,
                record.idempotency_key_digest == idempotency_digest,
                record.evaluation_mode == binding.evaluation_mode,
                record.expected_existing_revision == binding.expected_existing_revision,
                record.request_digest == binding.request_digest,
                record.existing_state_digest == binding.existing_state_digest,
                record.trusted_context_digest == binding.trusted_context_digest,
                record.authority_digest == binding.authority_digest,
                record.node_contract_set_digest == binding.node_contract_set_digest,
                record.precommit_digest == binding.precommit_digest,
            )
        )

    def _terminalize(
        self,
        token_hash: str,
        state: ReservationLifecycleState,
        terminal_code: ReservationTerminalCode,
    ) -> ReservationTombstone | None:
        record = self._repo.active_reservations.pop(token_hash, None)
        if record is None:
            return None
        eval_key = (record.subject_ref, record.graph_id, record.evaluation_id)
        if self._repo.active_by_evaluation.get(eval_key) == token_hash:
            self._repo.active_by_evaluation.pop(eval_key, None)
        self._repo.terminal_sequence += 1
        tombstone = ReservationTombstone(
            token_hash=token_hash,
            token_collision_guard_digest=record.token_collision_guard_digest,
            lifecycle_state=state,
            terminal_code=terminal_code,
            subject_ref=record.subject_ref,
            graph_id=record.graph_id,
            evaluation_id=record.evaluation_id,
            expected_existing_revision=record.expected_existing_revision,
            operation_binding_digest=record.precommit_digest,
            terminal_sequence=self._repo.terminal_sequence,
        )
        self._repo.tombstones[token_hash] = tombstone
        return tombstone

    def _terminal_token_outcome(self, token_hash: str) -> CommitOutcome | None:
        tombstone = self._repo.tombstones.get(token_hash)
        if tombstone is None:
            return None
        mapping = {
            ReservationLifecycleState.COMMITTED: CommitConflictCode.RESERVATION_ALREADY_COMMITTED,
            ReservationLifecycleState.ABORTED: CommitConflictCode.RESERVATION_ALREADY_ABORTED,
            ReservationLifecycleState.REJECTED: CommitConflictCode.RESERVATION_ALREADY_REJECTED,
        }
        return CommitOutcome(committed=False, conflict_code=mapping[tombstone.lifecycle_state])

    def reserve(self, binding: EvaluationOperationBinding) -> ReservationOutcome:
        if type(binding) is not EvaluationOperationBinding:
            raise TypeError("EVALUATION_OPERATION_BINDING_REQUIRED")
        if binding.canonical_result_digest is not None or binding.result_ref is not None:
            raise ValueError("RESERVATION_BINDING_MUST_BE_PRECOMMIT")
        request_digest = self._request_digest(binding.request_id)
        idempotency_digest = self._idempotency_digest(binding.idempotency_key)
        with self._repo.lock:
            existing = self._repo.committed_by_operation.get(
                self._operation_key_from_digests(binding, request_digest, idempotency_digest)
            )
            if existing is not None:
                self._assert_guard(existing.request_id_digest, request_digest)
                self._assert_guard(existing.idempotency_key_digest, idempotency_digest)
                if (
                    existing.binding.expected_existing_revision != binding.expected_existing_revision
                    or existing.binding.existing_state_digest != binding.existing_state_digest
                ):
                    return ReservationOutcome(
                        status=ReservationStatus.IDEMPOTENCY_CONFLICT,
                        conflict_code=ReservationConflictCode.IDEMPOTENCY_BASE_REVISION_CONFLICT,
                    )
                if existing.binding.request_digest != binding.request_digest:
                    return ReservationOutcome(
                        status=ReservationStatus.IDEMPOTENCY_CONFLICT,
                        conflict_code=ReservationConflictCode.IDEMPOTENCY_KEY_REUSE_CONFLICT,
                    )
                return ReservationOutcome(status=ReservationStatus.IDEMPOTENT_REPLAY, committed=copy.deepcopy(existing))

            idem_key = self._idem_key_from_digest(binding, idempotency_digest)
            by_idem = self._repo.by_idempotency.get(idem_key)
            if by_idem is not None:
                self._assert_guard(by_idem.idempotency_key_digest, idempotency_digest)
                code = (
                    ReservationConflictCode.IDEMPOTENCY_BASE_REVISION_CONFLICT
                    if by_idem.binding.expected_existing_revision != binding.expected_existing_revision
                    or by_idem.binding.existing_state_digest != binding.existing_state_digest
                    else ReservationConflictCode.IDEMPOTENCY_KEY_REUSE_CONFLICT
                )
                return ReservationOutcome(status=ReservationStatus.IDEMPOTENCY_CONFLICT, conflict_code=code)

            request_key = self._request_key_from_digest(binding, request_digest)
            by_request = self._repo.by_request.get(request_key)
            if by_request is not None:
                self._assert_guard(by_request.request_id_digest, request_digest)
                return ReservationOutcome(
                    status=ReservationStatus.IDEMPOTENCY_CONFLICT,
                    conflict_code=ReservationConflictCode.REQUEST_ID_REUSE_CONFLICT,
                )

            for active in self._repo.active_reservations.values():
                if active.subject_ref != binding.subject_ref or active.graph_id != binding.graph_id:
                    continue
                if active.request_id_digest.lookup_digest == request_digest.lookup_digest:
                    self._assert_guard(active.request_id_digest, request_digest)
                if active.idempotency_key_digest.lookup_digest == idempotency_digest.lookup_digest:
                    self._assert_guard(active.idempotency_key_digest, idempotency_digest)

            eval_key = self._eval_key(binding)
            current = self._repo.current_revision.get(eval_key, 0)
            if binding.expected_existing_revision != current:
                return ReservationOutcome(
                    status=ReservationStatus.REVISION_CONFLICT,
                    conflict_code=ReservationConflictCode.EVALUATION_REVISION_CONFLICT,
                )
            if eval_key in self._repo.active_by_evaluation:
                return ReservationOutcome(
                    status=ReservationStatus.IN_PROGRESS,
                    conflict_code=ReservationConflictCode.EVALUATION_OPERATION_IN_PROGRESS,
                )

            selected: tuple[str, ExecutionIdentityDigest] | None = None
            for _ in range(8):
                token = "reservation-" + uuid.uuid4().hex
                token_digest = self._token_digest(token)
                active = self._repo.active_reservations.get(token_digest.lookup_digest)
                tomb = self._repo.tombstones.get(token_digest.lookup_digest)
                if active is None and tomb is None:
                    selected = (token, token_digest)
                    break
                existing_guard = (
                    active.token_collision_guard_digest if active is not None
                    else tomb.token_collision_guard_digest if tomb is not None else None
                )
                if existing_guard != token_digest.collision_guard_digest:
                    continue
            if selected is None:
                raise ExecutionIdentityDigestCollision()
            token, token_digest = selected
            record = self._record_from_binding(token_digest, binding, request_digest, idempotency_digest)
            self._repo.active_reservations[token_digest.lookup_digest] = record
            self._repo.active_by_evaluation[eval_key] = token_digest.lookup_digest
            return ReservationOutcome(status=ReservationStatus.RESERVED, reservation_token=token)

    @staticmethod
    def _binding_matches_envelope(binding: EvaluationOperationBinding, envelope: EvaluationResultEnvelope) -> bool:
        request = envelope.validation_request
        result = envelope.result
        return all(
            (
                envelope.operation_binding == binding,
                envelope.operation_binding_digest == binding.operation_binding_digest,
                envelope.canonical_result_digest == binding.canonical_result_digest,
                canonical_result_digest(result) == binding.canonical_result_digest,
                envelope.subject_ref == binding.subject_ref == result.subject_ref,
                envelope.graph_id == binding.graph_id == result.graph_id == request.graph_ref.graph_id,
                envelope.graph_continuity_key == binding.graph_continuity_key == result.graph_continuity_key == request.graph_ref.graph_continuity_key,
                envelope.evaluation_id == binding.evaluation_id == result.evaluation_id,
                envelope.result_revision == binding.result_revision == result.evaluation_revision,
                request.request_id == binding.request_id,
                request.idempotency_key == binding.idempotency_key,
                request.evaluation_mode.value == binding.evaluation_mode,
                request.expected_existing_revision == binding.expected_existing_revision,
                envelope.trusted_context_digest == binding.trusted_context_digest,
                envelope.authority_digest == binding.authority_digest == result.authority_context_digest,
                envelope.node_contract_set_digest == binding.node_contract_set_digest == result.node_contract_set_digest,
                binding.result_ref is not None,
            )
        )

    def _reject(self, token_hash: str, code: CommitConflictCode) -> CommitOutcome:
        terminal = COMMIT_REJECTION_TO_TERMINAL.get(code)
        if terminal is None:
            terminal = ReservationTerminalCode.REJECTED_INVALID_STATE
        self._terminalize(token_hash, ReservationLifecycleState.REJECTED, terminal)
        return CommitOutcome(committed=False, conflict_code=code)

    def commit(
        self,
        reservation_token: str,
        binding: EvaluationOperationBinding,
        envelope: EvaluationResultEnvelope,
    ) -> CommitOutcome:
        token_digest = self._token_digest(reservation_token)
        token_hash = token_digest.lookup_digest
        with self._repo.lock:
            record = self._repo.active_reservations.get(token_hash)
            if record is None:
                tomb = self._repo.tombstones.get(token_hash)
                if tomb is not None and tomb.token_collision_guard_digest not in {"0" * 64, token_digest.collision_guard_digest}:
                    raise ExecutionIdentityDigestCollision()
                return self._terminal_token_outcome(token_hash) or CommitOutcome(
                    committed=False, conflict_code=CommitConflictCode.RESERVATION_TOKEN_INVALID
                )
            if record.token_collision_guard_digest != token_digest.collision_guard_digest:
                raise ExecutionIdentityDigestCollision()
            if record.lifecycle_state is not ReservationLifecycleState.ACTIVE:
                return self._reject(token_hash, CommitConflictCode.INVALID_RESERVATION_STATE)
            if type(binding) is not EvaluationOperationBinding:
                return self._reject(token_hash, CommitConflictCode.RESERVATION_RESULT_BINDING_MISMATCH)
            try:
                binding.validate_committed()
            except ValueError:
                return self._reject(token_hash, CommitConflictCode.RESERVATION_RESULT_BINDING_MISMATCH)
            if not self._record_matches_binding(record, binding):
                return self._reject(token_hash, CommitConflictCode.RESERVATION_RESULT_BINDING_MISMATCH)
            if binding.result_revision != binding.expected_existing_revision + 1:
                return self._reject(token_hash, CommitConflictCode.RESULT_REVISION_MISMATCH)
            if binding.result_ref != deterministic_result_ref(binding.evaluation_id, binding.result_revision, binding.canonical_result_digest):
                return self._reject(token_hash, CommitConflictCode.RESULT_REF_MISMATCH)
            try:
                if canonical_result_digest(envelope.result) != binding.canonical_result_digest:
                    return self._reject(token_hash, CommitConflictCode.RESULT_DIGEST_MISMATCH)
                if not self._binding_matches_envelope(binding, envelope):
                    return self._reject(token_hash, CommitConflictCode.RESERVATION_RESULT_BINDING_MISMATCH)
            except (AttributeError, KeyError, TypeError, ValueError):
                return self._reject(token_hash, CommitConflictCode.RESERVATION_RESULT_BINDING_MISMATCH)

            eval_key = self._eval_key(binding)
            if self._repo.current_revision.get(eval_key, 0) != binding.expected_existing_revision:
                return self._reject(token_hash, CommitConflictCode.EVALUATION_REVISION_CONFLICT)

            request_digest = self._request_digest(binding.request_id)
            idempotency_digest = self._idempotency_digest(binding.idempotency_key)
            operation = CommittedEvaluationOperation(
                binding=copy.deepcopy(binding),
                operation_binding_digest=binding.operation_binding_digest,
                committed_at=datetime.now(timezone.utc),
                request_id_digest=request_digest,
                idempotency_key_digest=idempotency_digest,
            )
            operation_copy = copy.deepcopy(operation)
            idem_key = self._idem_key_from_digest(binding, idempotency_digest)
            request_key = self._request_key_from_digest(binding, request_digest)
            existing_idem = self._repo.by_idempotency.get(idem_key)
            if existing_idem is not None:
                self._assert_guard(existing_idem.idempotency_key_digest, idempotency_digest)
            existing_request = self._repo.by_request.get(request_key)
            if existing_request is not None:
                self._assert_guard(existing_request.request_id_digest, request_digest)
            identity_key = self._operation_key_from_digests(binding, request_digest, idempotency_digest)
            assert binding.result_ref is not None

            try:
                with self._repo.transaction() as staged:
                    self._repo._maybe_fail("committed_by_operation")
                    staged.committed_by_operation[identity_key] = copy.deepcopy(operation_copy)
                    self._repo._maybe_fail("by_idempotency")
                    staged.by_idempotency[idem_key] = copy.deepcopy(operation_copy)
                    self._repo._maybe_fail("by_request")
                    staged.by_request[request_key] = copy.deepcopy(operation_copy)
                    self._repo._maybe_fail("current_revision")
                    staged.current_revision[eval_key] = binding.result_revision
                    self._repo._maybe_fail("envelope")
                    staged.envelopes[binding.result_ref] = copy.deepcopy(envelope)
                    self._repo._maybe_fail("terminal_tombstone")
                    staged.terminal_sequence += 1
                    staged.tombstones[token_hash] = ReservationTombstone(
                        token_hash=token_hash,
                        token_collision_guard_digest=record.token_collision_guard_digest,
                                    lifecycle_state=ReservationLifecycleState.COMMITTED,
                        terminal_code=ReservationTerminalCode.COMMIT_SUCCEEDED,
                        subject_ref=record.subject_ref,
                        graph_id=record.graph_id,
                        evaluation_id=record.evaluation_id,
                        expected_existing_revision=record.expected_existing_revision,
                        operation_binding_digest=record.precommit_digest,
                        terminal_sequence=staged.terminal_sequence,
                    )
                    self._repo._maybe_fail("active_reservation_removal")
                    staged.active_reservations.pop(token_hash, None)
                    self._repo._maybe_fail("active_lock_removal")
                    if staged.active_by_evaluation.get(eval_key) == token_hash:
                        staged.active_by_evaluation.pop(eval_key, None)
            except Exception:
                self._refresh_aliases()
                raise ExecutionStorageFailure() from None
            self._refresh_aliases()
            return CommitOutcome(committed=True, operation=copy.deepcopy(operation))

    def abort(self, reservation_token: str, reason: AbortReasonCode) -> AbortOutcome:
        if type(reason) is not AbortReasonCode:
            raise TypeError("ABORT_REASON_CODE_REQUIRED")
        token_digest = self._token_digest(reservation_token)
        token_hash = token_digest.lookup_digest
        with self._repo.lock:
            active = self._repo.active_reservations.get(token_hash)
            if active is not None and active.token_collision_guard_digest != token_digest.collision_guard_digest:
                raise ExecutionIdentityDigestCollision()
            tomb = self._repo.tombstones.get(token_hash)
            if tomb is not None and tomb.token_collision_guard_digest not in {"0" * 64, token_digest.collision_guard_digest}:
                raise ExecutionIdentityDigestCollision()
            if token_hash in self._repo.active_reservations:
                terminal = ABORT_TO_TERMINAL[reason]
                self._terminalize(token_hash, ReservationLifecycleState.ABORTED, terminal)
                return AbortOutcome(accepted=True, lifecycle_state=ReservationLifecycleState.ABORTED, terminal_code=terminal)
            return AbortOutcome(accepted=False)

    def load_committed(self, operation_identity: EvaluationOperationIdentity) -> CommittedEvaluationOperation | None:
        # Convert raw operation identity to the repository's digest identity.
        raw = "|".join(
            (
                operation_identity.subject_ref,
                operation_identity.graph_id,
                operation_identity.evaluation_id,
                self._request_digest(operation_identity.request_id).lookup_digest,
                self._request_digest(operation_identity.request_id).collision_guard_digest,
                self._idempotency_digest(operation_identity.idempotency_key).lookup_digest,
                self._idempotency_digest(operation_identity.idempotency_key).collision_guard_digest,
            )
        )
        key = "operation-" + hashlib.sha256(raw.encode()).hexdigest()
        with self._repo.lock:
            return copy.deepcopy(self._repo.committed_by_operation.get(key))

    def load_envelope(self, result_ref: str) -> EvaluationResultEnvelope | None:
        with self._repo.lock:
            return copy.deepcopy(self._repo.envelopes.get(result_ref))

    def restart(self) -> "InMemoryEvaluationExecutionPort":
        """Simulate a new worker over the same durable test repository."""
        return InMemoryEvaluationExecutionPort(
            identity_digest_port=self.identity_digest_port,
            repository=self._repo,
        )

    def export_test_state(self) -> dict[str, Any]:
        """Privacy-safe administrative projection; never contains raw tokens or request keys."""
        with self._repo.lock:
            active = [
                {
                    "token_hash": r.token_hash,
                    "token_collision_guard_digest": r.token_collision_guard_digest,
                    "lifecycle_state": r.lifecycle_state.value,
                    "subject_ref": r.subject_ref,
                    "graph_id": r.graph_id,
                    "graph_continuity_key": r.graph_continuity_key,
                    "evaluation_id": r.evaluation_id,
                    "request_id_digest": r.request_id_digest.model_dump(mode="json"),
                    "idempotency_key_digest": r.idempotency_key_digest.model_dump(mode="json"),
                    "evaluation_mode": r.evaluation_mode,
                    "expected_existing_revision": r.expected_existing_revision,
                    "request_digest": r.request_digest,
                    "existing_state_digest": r.existing_state_digest,
                    "trusted_context_digest": r.trusted_context_digest,
                    "authority_digest": r.authority_digest,
                    "node_contract_set_digest": r.node_contract_set_digest,
                    "precommit_digest": r.precommit_digest,
                    "reservation_sequence": r.reservation_sequence,
                }
                for r in self._repo.active_reservations.values()
            ]
            tombstones = [x.model_dump(mode="json") for x in self._repo.tombstones.values()]
            committed = [
                {
                    "operation_binding_digest": x.operation_binding_digest,
                    "subject_ref": x.binding.subject_ref,
                    "graph_id": x.binding.graph_id,
                    "evaluation_id": x.binding.evaluation_id,
                    "request_id_digest": x.request_id_digest.model_dump(mode="json") if x.request_id_digest else None,
                    "idempotency_key_digest": x.idempotency_key_digest.model_dump(mode="json") if x.idempotency_key_digest else None,
                    "result_revision": x.result_revision,
                    "result_ref": x.result_ref,
                    "result_digest": x.result_digest,
                }
                for x in self._repo.committed_by_operation.values()
            ]
            return {
                "schema_version": CURRENT_SCHEMA_VERSION,
                "code_mapping_version": MAPPING_VERSION,
                "active_reservations": copy.deepcopy(active),
                "reservation_tombstones": copy.deepcopy(tombstones),
                "active_locks": [
                    {"subject_ref": k[0], "graph_id": k[1], "evaluation_id": k[2], "token_hash": v}
                    for k, v in self._repo.active_by_evaluation.items()
                ],
                "committed_operations": copy.deepcopy(committed),
                "current_revision": [
                    {"subject_ref": k[0], "graph_id": k[1], "evaluation_id": k[2], "revision": v}
                    for k, v in self._repo.current_revision.items()
                ],
                "terminal_sequence": self._repo.terminal_sequence,
                "reservation_sequence": self._repo.reservation_sequence,
                "quarantined": [x.model_dump(mode="json") for x in self._repo.quarantined],
            }

    @classmethod
    def from_test_state(
        cls,
        state: dict[str, Any],
        *,
        identity_digest_port: ExecutionIdentityDigestPort,
    ) -> "InMemoryEvaluationExecutionPort":
        """Load the privacy-safe v0.1.8 test projection.

        This projection can restore ACTIVE locks and terminal tombstones. It is
        deliberately insufficient for committed-result replay; durable replay
        uses :meth:`restart` over the repository of record.
        """

        port = cls(identity_digest_port=identity_digest_port)
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != CURRENT_SCHEMA_VERSION
            or state.get("code_mapping_version") != MAPPING_VERSION
        ):
            port._quarantine("state-root", "unknown", 1)
            return port

        expected_top = {
            "schema_version", "code_mapping_version", "active_reservations", "reservation_tombstones",
            "active_locks", "committed_operations", "current_revision",
            "terminal_sequence", "reservation_sequence", "quarantined",
        }
        if set(state) != expected_top:
            port._quarantine("state-top-level", CURRENT_SCHEMA_VERSION, 1)

        active_raw = state.get("active_reservations", [])
        locks_raw = state.get("active_locks", [])
        tombstones_raw = state.get("reservation_tombstones", [])
        revisions_raw = state.get("current_revision", [])
        quarantined_raw = state.get("quarantined", [])
        if not all(isinstance(x, list) for x in (active_raw, locks_raw, tombstones_raw, revisions_raw, quarantined_raw)):
            port._quarantine("state-collection-shape", CURRENT_SCHEMA_VERSION, 1)
            return port

        expected_active_fields = {
            "token_hash", "token_collision_guard_digest", "lifecycle_state", "subject_ref", "graph_id",
            "graph_continuity_key", "evaluation_id", "request_id_digest",
            "idempotency_key_digest", "evaluation_mode",
            "expected_existing_revision", "request_digest",
            "existing_state_digest", "trusted_context_digest",
            "authority_digest", "node_contract_set_digest",
            "precommit_digest", "reservation_sequence",
        }
        parsed_active: dict[str, _ActiveReservationRecord] = {}
        parsed_locks: dict[tuple[str, str, str], str] = {}
        with port._repo.lock:
            for index, raw in enumerate(active_raw, start=1):
                try:
                    if not isinstance(raw, dict) or set(raw) != expected_active_fields:
                        raise ValueError
                    if raw["lifecycle_state"] != ReservationLifecycleState.ACTIVE.value:
                        raise ValueError
                    token_identity = validate_execution_identity_digest(
                        {
                            "domain": ExecutionIdentityDomain.RESERVATION_TOKEN.value,
                            "algorithm_version": CURRENT_EXECUTION_IDENTITY_ALGORITHM,
                            "lookup_digest": raw["token_hash"],
                            "collision_guard_digest": raw["token_collision_guard_digest"],
                        },
                        expected_domain=ExecutionIdentityDomain.RESERVATION_TOKEN,
                        source=ExecutionIdentityDigestValidationSource.ACTIVE_STATE_IMPORT,
                    )
                    token_hash = token_identity.lookup_digest
                    token_guard = token_identity.collision_guard_digest
                    request_identity = validate_execution_identity_digest(
                        raw["request_id_digest"],
                        expected_domain=ExecutionIdentityDomain.REQUEST_ID,
                        source=ExecutionIdentityDigestValidationSource.ACTIVE_STATE_IMPORT,
                    )
                    idempotency_identity = validate_execution_identity_digest(
                        raw["idempotency_key_digest"],
                        expected_domain=ExecutionIdentityDomain.IDEMPOTENCY_KEY,
                        source=ExecutionIdentityDigestValidationSource.ACTIVE_STATE_IMPORT,
                    )
                    rec = _ActiveReservationRecord(
                        token_hash=token_hash,
                        token_collision_guard_digest=token_guard,
                        subject_ref=raw["subject_ref"],
                        graph_id=raw["graph_id"],
                        graph_continuity_key=raw["graph_continuity_key"],
                        evaluation_id=raw["evaluation_id"],
                        request_id_digest=request_identity,
                        idempotency_key_digest=idempotency_identity,
                        evaluation_mode=raw["evaluation_mode"],
                        expected_existing_revision=int(raw["expected_existing_revision"]),
                        request_digest=raw["request_digest"],
                        existing_state_digest=raw["existing_state_digest"],
                        trusted_context_digest=raw["trusted_context_digest"],
                        authority_digest=raw["authority_digest"],
                        node_contract_set_digest=raw["node_contract_set_digest"],
                        precommit_digest=raw["precommit_digest"],
                        reservation_sequence=int(raw["reservation_sequence"]),
                    )
                    text_fields = (
                        rec.subject_ref, rec.graph_id, rec.graph_continuity_key,
                        rec.evaluation_id, rec.evaluation_mode,
                        rec.request_digest, rec.existing_state_digest,
                        rec.trusted_context_digest, rec.authority_digest,
                        rec.node_contract_set_digest, rec.precommit_digest,
                    )
                    if any(not isinstance(v, str) or not v or v != v.strip() or any(ord(c) < 32 for c in v) for v in text_fields):
                        raise ValueError
                    if rec.expected_existing_revision < 0 or rec.reservation_sequence < 1:
                        raise ValueError
                    key = (rec.subject_ref, rec.graph_id, rec.evaluation_id)
                    if token_hash in parsed_active or key in parsed_locks:
                        raise ValueError
                    parsed_active[token_hash] = rec
                    parsed_locks[key] = token_hash
                except (KeyError, TypeError, ValueError):
                    port._quarantine(f"active-{index}", CURRENT_SCHEMA_VERSION, index)

            supplied_locks: dict[tuple[str, str, str], str] = {}
            locks_valid = True
            for index, raw in enumerate(locks_raw, start=1):
                try:
                    if not isinstance(raw, dict) or set(raw) != {"subject_ref", "graph_id", "evaluation_id", "token_hash"}:
                        raise ValueError
                    key = (raw["subject_ref"], raw["graph_id"], raw["evaluation_id"])
                    token_hash = raw["token_hash"]
                    if key in supplied_locks or not isinstance(token_hash, str):
                        raise ValueError
                    supplied_locks[key] = token_hash
                except (KeyError, TypeError, ValueError):
                    locks_valid = False
                    port._quarantine(f"lock-{index}", CURRENT_SCHEMA_VERSION, index)
            if supplied_locks != parsed_locks:
                locks_valid = False
                port._quarantine("active-lock-set", CURRENT_SCHEMA_VERSION, 1)

            parsed_tombstones: dict[str, ReservationTombstone] = {}
            for index, raw in enumerate(tombstones_raw, start=1):
                try:
                    if not isinstance(raw, dict):
                        raise ValueError
                    typed = dict(raw)
                    if "token_collision_guard_digest" not in typed:
                        active_match = parsed_active.get(typed.get("token_hash"))
                        if active_match is None:
                            raise ValueError
                        typed["token_collision_guard_digest"] = active_match.token_collision_guard_digest
                    token_identity = validate_execution_identity_digest(
                        {
                            "domain": ExecutionIdentityDomain.RESERVATION_TOKEN.value,
                            "algorithm_version": CURRENT_EXECUTION_IDENTITY_ALGORITHM,
                            "lookup_digest": raw["token_hash"],
                            "collision_guard_digest": typed["token_collision_guard_digest"],
                        },
                        expected_domain=ExecutionIdentityDomain.RESERVATION_TOKEN,
                        source=ExecutionIdentityDigestValidationSource.TERMINAL_STATE_IMPORT,
                    )
                    typed["token_hash"] = token_identity.lookup_digest
                    typed["token_collision_guard_digest"] = token_identity.collision_guard_digest
                    typed["lifecycle_state"] = ReservationLifecycleState(raw["lifecycle_state"])
                    typed["terminal_code"] = ReservationTerminalCode(raw["terminal_code"])
                    tomb = ReservationTombstone.model_validate(typed, strict=True)
                    if tomb.token_hash in parsed_tombstones:
                        raise ValueError
                    if tomb.token_hash in parsed_active:
                        conflicting = parsed_active.pop(tomb.token_hash)
                        parsed_locks.pop(
                            (conflicting.subject_ref, conflicting.graph_id, conflicting.evaluation_id),
                            None,
                        )
                        locks_valid = False
                        raise ValueError
                    parsed_tombstones[tomb.token_hash] = tomb
                except (KeyError, TypeError, ValueError):
                    port._quarantine(f"tombstone-{index}", CURRENT_SCHEMA_VERSION, index)

            if locks_valid:
                port._repo.active_reservations.update(parsed_active)
                port._repo.active_by_evaluation.update(parsed_locks)
            else:
                for index, _ in enumerate(parsed_active.values(), start=1):
                    port._quarantine(f"active-lock-rejected-{index}", CURRENT_SCHEMA_VERSION, index)
            port._repo.tombstones.update(parsed_tombstones)

            for index, raw in enumerate(revisions_raw, start=1):
                try:
                    if not isinstance(raw, dict) or set(raw) != {"subject_ref", "graph_id", "evaluation_id", "revision"}:
                        raise ValueError
                    key = (raw["subject_ref"], raw["graph_id"], raw["evaluation_id"])
                    revision = raw["revision"]
                    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
                        raise ValueError
                    if key in port._repo.current_revision:
                        raise ValueError
                    port._repo.current_revision[key] = revision
                except (KeyError, TypeError, ValueError):
                    port._quarantine(f"revision-{index}", CURRENT_SCHEMA_VERSION, index)

            for index, raw in enumerate(quarantined_raw, start=1):
                try:
                    if not isinstance(raw, dict):
                        raise ValueError
                    typed = dict(raw)
                    typed["fixed_violation_code"] = ExecutionPortContractViolationCode(raw["fixed_violation_code"])
                    record = QuarantinedExecutionRecord.model_validate(typed, strict=True)
                    port._repo.quarantined.append(record)
                except (KeyError, TypeError, ValueError):
                    port._quarantine(f"quarantine-{index}", CURRENT_SCHEMA_VERSION, index)

            try:
                terminal_sequence = state.get("terminal_sequence", 0)
                reservation_sequence = state.get("reservation_sequence", 0)
                if (
                    not isinstance(terminal_sequence, int)
                    or isinstance(terminal_sequence, bool)
                    or terminal_sequence < 0
                    or not isinstance(reservation_sequence, int)
                    or isinstance(reservation_sequence, bool)
                    or reservation_sequence < 0
                ):
                    raise ValueError
                port._repo.terminal_sequence = max(
                    terminal_sequence,
                    max((x.terminal_sequence for x in port._repo.tombstones.values()), default=0),
                )
                port._repo.reservation_sequence = max(
                    reservation_sequence,
                    max((x.reservation_sequence for x in port._repo.active_reservations.values()), default=0),
                )
            except (TypeError, ValueError):
                port._quarantine("sequence", CURRENT_SCHEMA_VERSION, 1)
        return port

    @classmethod
    def from_legacy_v017_state(
        cls,
        state: dict[str, Any],
        *,
        identity_digest_port: ExecutionIdentityDigestPort,
    ) -> "InMemoryEvaluationExecutionPort":
        """Migrate the exact v0.1.7 TEST_ONLY repository shape.

        Unknown values, incompatible state/code combinations, and incomplete
        committed records are quarantined without retaining their raw values.
        """

        port = cls(identity_digest_port=identity_digest_port)
        if not isinstance(state, dict):
            port._quarantine("legacy-root", LEGACY_SCHEMA_VERSION, 1)
            return port

        reservations = state.get("reservations", {})
        tombstones = state.get("reservation_tombstones", {})
        committed_map = state.get("committed_by_operation", {})
        envelopes = state.get("envelopes", {})
        current_revision = state.get("current_revision", {})
        if not all(isinstance(x, dict) for x in (reservations, tombstones, committed_map, envelopes, current_revision)):
            port._quarantine("legacy-root-shape", LEGACY_SCHEMA_VERSION, 1)
            return port

        with port._repo.lock:
            # ACTIVE records retain their lock across restart. Raw legacy tokens
            # are consumed once and only their keyed hash is stored.
            for index, (raw_token, raw) in enumerate(reservations.items(), start=1):
                safe_id = f"legacy-active-{index}"
                try:
                    if not isinstance(raw_token, str) or not isinstance(raw, dict):
                        raise ValueError
                    if raw.get("state") != ReservationLifecycleState.ACTIVE.value:
                        raise ValueError
                    binding = raw["binding"]
                    if type(binding) is not EvaluationOperationBinding:
                        raise ValueError
                    if raw.get("precommit_digest") != binding.precommit_digest:
                        raise ValueError
                    token_identity = port._token_digest(raw_token)
                    token_hash = token_identity.lookup_digest
                    if token_hash in port._repo.tombstones:
                        raise ValueError
                    rec = port._record_from_binding(token_identity, binding)
                    port._repo.active_reservations[token_hash] = rec
                    eval_key = (rec.subject_ref, rec.graph_id, rec.evaluation_id)
                    if eval_key in port._repo.active_by_evaluation:
                        raise ValueError
                    port._repo.active_by_evaluation[eval_key] = token_hash
                except (KeyError, TypeError, ValueError):
                    port._quarantine(safe_id, LEGACY_SCHEMA_VERSION, index)

            legacy_locks = state.get("active_by_evaluation", {})
            if not isinstance(legacy_locks, dict):
                port._quarantine("legacy-active-lock-shape", LEGACY_SCHEMA_VERSION, 1)
            else:
                expected_locks = {
                    key: raw_token
                    for raw_token, raw in reservations.items()
                    if isinstance(raw_token, str)
                    and isinstance(raw, dict)
                    and raw.get("state") == ReservationLifecycleState.ACTIVE.value
                    and type(raw.get("binding")) is EvaluationOperationBinding
                    for key in [port._eval_key(raw["binding"])]
                }
                if legacy_locks != expected_locks:
                    port._quarantine("legacy-active-lock-map", LEGACY_SCHEMA_VERSION, 1)
                    port._repo.active_reservations.clear()
                    port._repo.active_by_evaluation.clear()

            # Terminal records use an exact, case-sensitive versioned map.
            for index, raw in enumerate(tombstones.values(), start=1):
                digest = "record-" + hashlib.sha256(f"legacy-tombstone-{index}".encode()).hexdigest()
                tomb, quarantine = migrate_v017_tombstone(
                    raw,
                    record_identity_digest=digest,
                    sequence=index,
                )
                if tomb is not None:
                    if tomb.token_hash in port._repo.active_reservations:
                        conflicting = port._repo.active_reservations.pop(tomb.token_hash)
                        port._repo.active_by_evaluation.pop(
                            (conflicting.subject_ref, conflicting.graph_id, conflicting.evaluation_id),
                            None,
                        )
                        port._quarantine(f"legacy-tombstone-active-{index}", LEGACY_SCHEMA_VERSION, index)
                        continue
                    port._repo.tombstones[tomb.token_hash] = tomb
                    port._repo.terminal_sequence = max(port._repo.terminal_sequence, tomb.terminal_sequence)
                elif quarantine is not None:
                    port._repo.quarantined.append(quarantine)

            # Known committed records migrate only when operation, envelope and
            # revision are all complete and mutually consistent. Caller-provided
            # replay indexes are ignored and rebuilt from trusted records.
            for index, operation in enumerate(committed_map.values(), start=1):
                try:
                    if type(operation) is not CommittedEvaluationOperation:
                        raise ValueError
                    binding = operation.binding
                    if type(binding) is not EvaluationOperationBinding:
                        raise ValueError
                    binding.validate_committed()
                    if operation.operation_binding_digest != binding.operation_binding_digest:
                        raise ValueError
                    envelope = envelopes.get(binding.result_ref)
                    if type(envelope) is not EvaluationResultEnvelope:
                        raise ValueError
                    if envelope.operation_binding != binding:
                        raise ValueError
                    eval_key = (binding.subject_ref, binding.graph_id, binding.evaluation_id)
                    if current_revision.get(eval_key) != binding.result_revision:
                        raise ValueError
                    migrated = CommittedEvaluationOperation(
                        binding=copy.deepcopy(binding),
                        operation_binding_digest=binding.operation_binding_digest,
                        committed_at=operation.committed_at,
                        request_id_digest=port._request_digest(binding.request_id),
                        idempotency_key_digest=port._idempotency_digest(binding.idempotency_key),
                    )
                    migrated_copy = copy.deepcopy(migrated)
                    port._repo.committed_by_operation[port._operation_key(binding)] = migrated_copy
                    port._repo.by_idempotency[port._idem_key(binding)] = migrated_copy
                    port._repo.by_request[port._request_key(binding)] = migrated_copy
                    port._repo.current_revision[eval_key] = binding.result_revision
                    port._repo.envelopes[binding.result_ref] = copy.deepcopy(envelope)
                except (AssertionError, KeyError, TypeError, ValueError):
                    port._quarantine(f"legacy-committed-{index}", LEGACY_SCHEMA_VERSION, index)

            # Revision records without a complete committed operation do not
            # advance the migrated repository and are quarantined.
            migrated_eval_keys = set(port._repo.current_revision)
            for index, (raw_key, raw_revision) in enumerate(current_revision.items(), start=1):
                if raw_key in migrated_eval_keys:
                    continue
                try:
                    if (
                        not isinstance(raw_key, tuple)
                        or len(raw_key) != 3
                        or not isinstance(raw_revision, int)
                        or raw_revision < 0
                    ):
                        raise ValueError
                    if raw_revision == 0:
                        port._repo.current_revision[raw_key] = 0
                    else:
                        raise ValueError
                except (TypeError, ValueError):
                    port._quarantine(f"legacy-revision-{index}", LEGACY_SCHEMA_VERSION, index)
        return port

    def _quarantine(self, identity: str, schema: str, sequence: int) -> None:
        self._repo.quarantine_sequence += 1
        digest = "record-" + hashlib.sha256(identity.encode("utf-8", "ignore")).hexdigest()
        safe_schema = schema if schema and all(c.isalnum() or c in "._-" for c in schema) else "unknown"
        self._repo.quarantined.append(
            QuarantinedExecutionRecord(
                record_identity_digest=digest,
                source_schema_version=safe_schema,
                fixed_violation_code=ExecutionPortContractViolationCode.EXECUTION_PORT_CONTRACT_VIOLATION,
                quarantined_at_or_sequence=max(sequence, self._repo.quarantine_sequence),
            )
        )

    def find_by_idempotency_key(self, idempotency_key: str):
        """TEST_ONLY lookup through the keyed digest index."""
        digest = self._idempotency_digest(idempotency_key)
        with self._repo.lock:
            matches = [op for key, op in self._repo.by_idempotency.items() if key[2] == digest.lookup_digest]
            if len(matches) == 1:
                self._assert_guard(matches[0].idempotency_key_digest, digest)
                return copy.deepcopy(matches[0])
            return None



InMemoryEvaluationIdempotencyPort = InMemoryEvaluationExecutionPort
InMemoryEvaluationExecutionPort.__test__ = False
