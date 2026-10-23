"""SQLAlchemy EvaluationExecutionPort for the immutable Evaluator v0.1.10 core."""
from __future__ import annotations

import copy
import hashlib
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from agents.learning_evaluator.execution import (
    AbortOutcome,
    CommitOutcome,
    CommittedEvaluationOperation,
    EvaluationOperationIdentity,
    ExecutionIdentityDigestCollision,
    ExecutionStorageFailure,
    ReservationOutcome,
)
from agents.learning_evaluator.execution_codes import (
    ABORT_TO_TERMINAL,
    AbortReasonCode,
    CommitConflictCode,
    ReservationConflictCode,
    ReservationLifecycleState,
    ReservationStatus,
    ReservationTerminalCode,
)
from agents.learning_evaluator.operation_binding import EvaluationOperationBinding, deterministic_result_ref
from agents.learning_evaluator.result_authenticity import EvaluationResultEnvelope, canonical_result_digest
from database.models import (
    LearningEvaluation,
    LearningEvaluationEvidenceRef,
    LearningEvaluationExecution,
    LearningEvaluationResultRecord,
)
from integrations.learning_evaluator_security import (
    EvaluatorEnvelopeCipher,
    FormalHmacExecutionIdentityDigestPort,
)


def sync_database_url(database_url: str) -> str:
    return (
        database_url
        .replace("sqlite+aiosqlite://", "sqlite://")
        .replace("postgresql+asyncpg://", "postgresql://")
    )


class SqlAlchemyEvaluationExecutionPort:
    """One formal port instance is scoped to one owned evaluation aggregate."""

    def __init__(
        self,
        *,
        database_url: str,
        evaluation_db_id: int,
        evaluation_external_id: str,
        user_id: int,
        graph_db_id: int,
        graph_external_id: str,
        graph_continuity_key: str,
        node_db_id: int,
        provider_runtime: str,
        adapter_contract_versions: dict[str, str] | None = None,
        identity_digest_port: FormalHmacExecutionIdentityDigestPort | None = None,
        cipher: EvaluatorEnvelopeCipher | None = None,
    ):
        self.evaluation_db_id = evaluation_db_id
        self.evaluation_external_id = evaluation_external_id
        self.user_id = user_id
        self.graph_db_id = graph_db_id
        self.graph_external_id = graph_external_id
        self.graph_continuity_key = graph_continuity_key
        self.node_db_id = node_db_id
        self.provider_runtime = provider_runtime
        self.adapter_contract_versions = dict(adapter_contract_versions or {})
        self.identity_digest_port = identity_digest_port or FormalHmacExecutionIdentityDigestPort()
        self.cipher = cipher or EvaluatorEnvelopeCipher()
        url = sync_database_url(database_url)
        connect_args = {"check_same_thread": False, "timeout": 10} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=10000")
                cursor.close()
        self.sessions = sessionmaker(self.engine, class_=Session, expire_on_commit=False, future=True)

    def close(self) -> None:
        self.engine.dispose()

    def _request_digest(self, raw: str):
        return self.identity_digest_port.digest_request_id(raw)

    def _idem_digest(self, raw: str):
        return self.identity_digest_port.digest_idempotency_key(raw)

    def _token_digest(self, raw: str):
        return self.identity_digest_port.hash_reservation_token(raw)

    @staticmethod
    def _guard(existing_lookup: str, existing_guard: str, incoming) -> None:
        if existing_lookup == incoming.lookup_digest and existing_guard != incoming.collision_guard_digest:
            raise ExecutionIdentityDigestCollision()

    def _validate_scope(self, binding: EvaluationOperationBinding) -> None:
        if (
            binding.subject_ref != f"user-{self.user_id}"
            or binding.graph_id != self.graph_external_id
            or binding.graph_continuity_key != self.graph_continuity_key
            or binding.evaluation_id != self.evaluation_external_id
        ):
            raise ValueError("EVALUATION_EXECUTION_SCOPE_MISMATCH")

    def _committed(self, row: LearningEvaluationExecution, binding: EvaluationOperationBinding) -> CommittedEvaluationOperation:
        committed_binding = EvaluationOperationBinding(**{
            **binding.canonical_payload(),
            "canonical_result_digest": row.result_digest,
            "result_ref": row.result_ref,
        })
        committed_binding.validate_committed()
        if row.operation_binding_digest != committed_binding.operation_binding_digest:
            raise ExecutionStorageFailure()
        return CommittedEvaluationOperation(
            binding=copy.deepcopy(committed_binding),
            operation_binding_digest=committed_binding.operation_binding_digest,
            committed_at=(row.terminal_at or datetime.now(timezone.utc)),
            request_id_digest=self._request_digest(binding.request_id),
            idempotency_key_digest=self._idem_digest(binding.idempotency_key),
        )

    def reserve(self, binding: EvaluationOperationBinding) -> ReservationOutcome:
        if type(binding) is not EvaluationOperationBinding:
            raise TypeError("EVALUATION_OPERATION_BINDING_REQUIRED")
        if binding.canonical_result_digest is not None or binding.result_ref is not None:
            raise ValueError("RESERVATION_BINDING_MUST_BE_PRECOMMIT")
        self._validate_scope(binding)
        request = self._request_digest(binding.request_id)
        idem = self._idem_digest(binding.idempotency_key)
        token = "reservation-" + uuid.uuid4().hex
        token_digest = self._token_digest(token)
        active_scope = f"{self.user_id}|{self.graph_external_id}|{self.evaluation_external_id}"
        try:
            with self.sessions.begin() as session:
                aggregate = session.get(LearningEvaluation, self.evaluation_db_id)
                if aggregate is None:
                    raise ExecutionStorageFailure()

                idem_row = session.scalar(select(LearningEvaluationExecution).where(
                    LearningEvaluationExecution.evaluation_external_id == self.evaluation_external_id,
                    LearningEvaluationExecution.idempotency_lookup_digest == idem.lookup_digest,
                ))
                if idem_row is not None:
                    self._guard(idem_row.idempotency_lookup_digest, idem_row.idempotency_collision_guard_digest, idem)
                    if idem_row.status == "COMMITTED":
                        if idem_row.base_revision != binding.expected_existing_revision or idem_row.existing_state_digest != binding.existing_state_digest:
                            return ReservationOutcome(status=ReservationStatus.IDEMPOTENCY_CONFLICT, conflict_code=ReservationConflictCode.IDEMPOTENCY_BASE_REVISION_CONFLICT)
                        if idem_row.request_digest != binding.request_digest:
                            return ReservationOutcome(status=ReservationStatus.IDEMPOTENCY_CONFLICT, conflict_code=ReservationConflictCode.IDEMPOTENCY_KEY_REUSE_CONFLICT)
                        self._guard(idem_row.request_lookup_digest, idem_row.request_collision_guard_digest, request)
                        return ReservationOutcome(status=ReservationStatus.IDEMPOTENT_REPLAY, committed=self._committed(idem_row, binding))
                    if idem_row.status == "ACTIVE":
                        return ReservationOutcome(status=ReservationStatus.IN_PROGRESS, conflict_code=ReservationConflictCode.EVALUATION_OPERATION_IN_PROGRESS)
                    return ReservationOutcome(status=ReservationStatus.IDEMPOTENCY_CONFLICT, conflict_code=ReservationConflictCode.IDEMPOTENCY_KEY_REUSE_CONFLICT)

                request_row = session.scalar(select(LearningEvaluationExecution).where(
                    LearningEvaluationExecution.evaluation_external_id == self.evaluation_external_id,
                    LearningEvaluationExecution.request_lookup_digest == request.lookup_digest,
                ))
                if request_row is not None:
                    self._guard(request_row.request_lookup_digest, request_row.request_collision_guard_digest, request)
                    return ReservationOutcome(status=ReservationStatus.IDEMPOTENCY_CONFLICT, conflict_code=ReservationConflictCode.REQUEST_ID_REUSE_CONFLICT)

                if aggregate.current_revision != binding.expected_existing_revision:
                    return ReservationOutcome(status=ReservationStatus.REVISION_CONFLICT, conflict_code=ReservationConflictCode.EVALUATION_REVISION_CONFLICT)
                if session.scalar(select(LearningEvaluationExecution.id).where(
                    LearningEvaluationExecution.active_scope_key == active_scope,
                    LearningEvaluationExecution.status == "ACTIVE",
                )) is not None:
                    return ReservationOutcome(status=ReservationStatus.IN_PROGRESS, conflict_code=ReservationConflictCode.EVALUATION_OPERATION_IN_PROGRESS)

                session.add(LearningEvaluationExecution(
                    evaluation_id=self.evaluation_db_id,
                    evaluation_external_id=self.evaluation_external_id,
                    user_id=self.user_id,
                    graph_id=self.graph_db_id,
                    graph_external_id=self.graph_external_id,
                    graph_continuity_key=self.graph_continuity_key,
                    request_lookup_digest=request.lookup_digest,
                    request_collision_guard_digest=request.collision_guard_digest,
                    idempotency_lookup_digest=idem.lookup_digest,
                    idempotency_collision_guard_digest=idem.collision_guard_digest,
                    reservation_lookup_digest=token_digest.lookup_digest,
                    reservation_collision_guard_digest=token_digest.collision_guard_digest,
                    evaluation_mode=binding.evaluation_mode,
                    base_revision=binding.expected_existing_revision,
                    target_revision=binding.result_revision,
                    request_digest=binding.request_digest,
                    existing_state_digest=binding.existing_state_digest,
                    trusted_context_digest=binding.trusted_context_digest,
                    authority_digest=binding.authority_digest,
                    node_contract_set_digest=binding.node_contract_set_digest,
                    precommit_digest=binding.precommit_digest,
                    status="ACTIVE",
                    active_scope_key=active_scope,
                ))
            return ReservationOutcome(status=ReservationStatus.RESERVED, reservation_token=token)
        except ExecutionIdentityDigestCollision:
            raise
        except IntegrityError:
            # A concurrent claimant won a durable uniqueness race.
            return ReservationOutcome(status=ReservationStatus.IN_PROGRESS, conflict_code=ReservationConflictCode.EVALUATION_OPERATION_IN_PROGRESS)
        except ExecutionStorageFailure:
            raise
        except SQLAlchemyError as exc:
            raise ExecutionStorageFailure() from exc

    def _row_matches_binding(self, row: LearningEvaluationExecution, binding: EvaluationOperationBinding) -> bool:
        return all((
            row.evaluation_external_id == binding.evaluation_id,
            row.graph_external_id == binding.graph_id,
            row.graph_continuity_key == binding.graph_continuity_key,
            row.evaluation_mode == binding.evaluation_mode,
            row.base_revision == binding.expected_existing_revision,
            row.target_revision == binding.result_revision,
            row.request_digest == binding.request_digest,
            row.existing_state_digest == binding.existing_state_digest,
            row.trusted_context_digest == binding.trusted_context_digest,
            row.authority_digest == binding.authority_digest,
            row.node_contract_set_digest == binding.node_contract_set_digest,
            row.precommit_digest == binding.precommit_digest,
        ))

    def commit(self, reservation_token: str, binding: EvaluationOperationBinding, envelope: EvaluationResultEnvelope) -> CommitOutcome:
        self._validate_scope(binding)
        token = self._token_digest(reservation_token)
        request = self._request_digest(binding.request_id)
        idem = self._idem_digest(binding.idempotency_key)
        try:
            binding.validate_committed()
        except ValueError:
            return CommitOutcome(committed=False, conflict_code=CommitConflictCode.RESERVATION_RESULT_BINDING_MISMATCH)
        try:
            with self.sessions.begin() as session:
                row = session.scalar(select(LearningEvaluationExecution).where(
                    LearningEvaluationExecution.reservation_lookup_digest == token.lookup_digest,
                ))
                if row is None:
                    return CommitOutcome(committed=False, conflict_code=CommitConflictCode.RESERVATION_TOKEN_INVALID)
                self._guard(row.reservation_lookup_digest, row.reservation_collision_guard_digest, token)
                if row.status != "ACTIVE":
                    status_map = {
                        "COMMITTED": CommitConflictCode.RESERVATION_ALREADY_COMMITTED,
                        "ABORTED": CommitConflictCode.RESERVATION_ALREADY_ABORTED,
                        "REJECTED": CommitConflictCode.RESERVATION_ALREADY_REJECTED,
                    }
                    return CommitOutcome(committed=False, conflict_code=status_map.get(row.status, CommitConflictCode.INVALID_RESERVATION_STATE))
                self._guard(row.request_lookup_digest, row.request_collision_guard_digest, request)
                self._guard(row.idempotency_lookup_digest, row.idempotency_collision_guard_digest, idem)
                if not self._row_matches_binding(row, binding):
                    return CommitOutcome(committed=False, conflict_code=CommitConflictCode.RESERVATION_RESULT_BINDING_MISMATCH)
                if binding.result_revision != binding.expected_existing_revision + 1:
                    return CommitOutcome(committed=False, conflict_code=CommitConflictCode.RESULT_REVISION_MISMATCH)
                if binding.result_ref != deterministic_result_ref(binding.evaluation_id, binding.result_revision, binding.canonical_result_digest):
                    return CommitOutcome(committed=False, conflict_code=CommitConflictCode.RESULT_REF_MISMATCH)
                if canonical_result_digest(envelope.result) != binding.canonical_result_digest:
                    return CommitOutcome(committed=False, conflict_code=CommitConflictCode.RESULT_DIGEST_MISMATCH)
                if envelope.operation_binding != binding or envelope.operation_binding_digest != binding.operation_binding_digest:
                    return CommitOutcome(committed=False, conflict_code=CommitConflictCode.RESERVATION_RESULT_BINDING_MISMATCH)

                aggregate = session.get(LearningEvaluation, self.evaluation_db_id)
                if aggregate is None or aggregate.current_revision != binding.expected_existing_revision:
                    return CommitOutcome(committed=False, conflict_code=CommitConflictCode.EVALUATION_REVISION_CONFLICT)
                if os.environ.get("KAKI_EVALUATOR_FAULT_POINT") == "before_result_insert":
                    raise OSError("CONTROLLED_EVALUATOR_COMMIT_FAILURE")

                result_record = LearningEvaluationResultRecord(
                    evaluation_id=self.evaluation_db_id,
                    execution_id=row.id,
                    user_id=self.user_id,
                    graph_id=self.graph_db_id,
                    node_id=self.node_db_id,
                    evaluation_revision=binding.result_revision,
                    graph_revision=envelope.result.graph_revision,
                    result_ref=binding.result_ref,
                    canonical_result_digest=binding.canonical_result_digest,
                    node_contract_digest=binding.node_contract_set_digest,
                    authority_context_digest=binding.authority_digest,
                    authenticity_envelope_sealed=self.cipher.seal_envelope(envelope),
                    public_projection=envelope.result.public_view.model_dump(mode="json"),
                    evidence_event_ids=[record.event_id for record in envelope.validation_request.evidence_records],
                    provider_runtime=self.provider_runtime,
                )
                session.add(result_record)
                session.flush()

                for evidence in envelope.validation_request.evidence_records:
                    try:
                        workspace_event_id = int(str(evidence.source_record_id).rsplit("-", 1)[-1])
                        generated_item_id = int(str(evidence.artifact_id).rsplit("-", 1)[-1]) if evidence.artifact_id else 0
                    except (TypeError, ValueError):
                        raise ExecutionStorageFailure() from None
                    existing = session.scalar(select(LearningEvaluationEvidenceRef).where(
                        LearningEvaluationEvidenceRef.evaluation_id == self.evaluation_db_id,
                        LearningEvaluationEvidenceRef.workspace_event_id == workspace_event_id,
                    ))
                    if existing is None:
                        session.add(LearningEvaluationEvidenceRef(
                            evaluation_id=self.evaluation_db_id,
                            user_id=self.user_id,
                            graph_id=self.graph_db_id,
                            node_id=self.node_db_id,
                            workspace_event_id=workspace_event_id,
                            generated_item_id=generated_item_id,
                            evidence_external_id=evidence.evidence_id,
                            source_system=evidence.source_system,
                            adapter_contract_version=self.adapter_contract_versions.get(
                                evidence.source_system, "unknown-adapter-contract"
                            ),
                            structured_features=evidence.evidence_payload.model_dump(mode="json"),
                            occurred_at=evidence.occurred_at.replace(tzinfo=None),
                            first_seen_revision=binding.result_revision,
                        ))

                if os.environ.get("KAKI_EVALUATOR_FAULT_POINT") == "before_revision_update":
                    raise OSError("CONTROLLED_EVALUATOR_COMMIT_FAILURE")
                aggregate.current_revision = binding.result_revision
                row.status = "COMMITTED"
                row.active_scope_key = None
                row.result_ref = binding.result_ref
                row.result_digest = binding.canonical_result_digest
                row.operation_binding_digest = binding.operation_binding_digest
                row.terminal_at = datetime.now(timezone.utc).replace(tzinfo=None)
                row.terminal_tombstone = {
                    "state": ReservationLifecycleState.COMMITTED.value,
                    "code": ReservationTerminalCode.COMMIT_SUCCEEDED.value,
                    "operation_binding_digest": binding.operation_binding_digest,
                }
            operation = CommittedEvaluationOperation(
                binding=copy.deepcopy(binding),
                operation_binding_digest=binding.operation_binding_digest,
                committed_at=datetime.now(timezone.utc),
                request_id_digest=request,
                idempotency_key_digest=idem,
            )
            return CommitOutcome(committed=True, operation=operation)
        except (ExecutionIdentityDigestCollision, ExecutionStorageFailure):
            raise
        except (IntegrityError, SQLAlchemyError, OSError) as exc:
            raise ExecutionStorageFailure() from exc

    def abort(self, reservation_token: str, reason: AbortReasonCode) -> AbortOutcome:
        if type(reason) is not AbortReasonCode:
            raise TypeError("ABORT_REASON_CODE_REQUIRED")
        token = self._token_digest(reservation_token)
        try:
            with self.sessions.begin() as session:
                row = session.scalar(select(LearningEvaluationExecution).where(
                    LearningEvaluationExecution.reservation_lookup_digest == token.lookup_digest,
                ))
                if row is None:
                    return AbortOutcome(accepted=False)
                self._guard(row.reservation_lookup_digest, row.reservation_collision_guard_digest, token)
                if row.status != "ACTIVE":
                    return AbortOutcome(accepted=False)
                terminal = ABORT_TO_TERMINAL[reason]
                row.status = "ABORTED"
                row.active_scope_key = None
                row.public_issue_code = reason.value
                row.terminal_at = datetime.now(timezone.utc).replace(tzinfo=None)
                row.terminal_tombstone = {
                    "state": ReservationLifecycleState.ABORTED.value,
                    "code": terminal.value,
                    "operation_binding_digest": row.precommit_digest,
                }
            return AbortOutcome(
                accepted=True,
                lifecycle_state=ReservationLifecycleState.ABORTED,
                terminal_code=terminal,
            )
        except ExecutionIdentityDigestCollision:
            raise
        except SQLAlchemyError as exc:
            raise ExecutionStorageFailure() from exc

    def load_committed(self, operation_identity: EvaluationOperationIdentity) -> CommittedEvaluationOperation | None:
        request = self._request_digest(operation_identity.request_id)
        idem = self._idem_digest(operation_identity.idempotency_key)
        try:
            with self.sessions() as session:
                row = session.scalar(select(LearningEvaluationExecution).where(
                    LearningEvaluationExecution.evaluation_external_id == operation_identity.evaluation_id,
                    LearningEvaluationExecution.request_lookup_digest == request.lookup_digest,
                    LearningEvaluationExecution.idempotency_lookup_digest == idem.lookup_digest,
                    LearningEvaluationExecution.status == "COMMITTED",
                ))
                if row is None:
                    return None
                self._guard(row.request_lookup_digest, row.request_collision_guard_digest, request)
                self._guard(row.idempotency_lookup_digest, row.idempotency_collision_guard_digest, idem)
                binding = EvaluationOperationBinding(
                    subject_ref=operation_identity.subject_ref,
                    graph_id=operation_identity.graph_id,
                    graph_continuity_key=row.graph_continuity_key,
                    evaluation_id=operation_identity.evaluation_id,
                    request_id=operation_identity.request_id,
                    idempotency_key=operation_identity.idempotency_key,
                    evaluation_mode=row.evaluation_mode,
                    expected_existing_revision=row.base_revision,
                    result_revision=row.target_revision,
                    request_digest=row.request_digest,
                    existing_state_digest=row.existing_state_digest,
                    trusted_context_digest=row.trusted_context_digest,
                    authority_digest=row.authority_digest,
                    node_contract_set_digest=row.node_contract_set_digest,
                    canonical_result_digest=row.result_digest,
                    result_ref=row.result_ref,
                )
                return CommittedEvaluationOperation(
                    binding=binding,
                    operation_binding_digest=row.operation_binding_digest,
                    committed_at=(row.terminal_at or datetime.utcnow()).replace(tzinfo=timezone.utc),
                    request_id_digest=request,
                    idempotency_key_digest=idem,
                )
        except ExecutionIdentityDigestCollision:
            raise
        except SQLAlchemyError as exc:
            raise ExecutionStorageFailure() from exc

    def load_envelope(self, result_ref: str) -> EvaluationResultEnvelope | None:
        try:
            with self.sessions() as session:
                row = session.scalar(select(LearningEvaluationResultRecord).where(
                    LearningEvaluationResultRecord.result_ref == result_ref,
                ))
                return self.cipher.open_envelope(row.authenticity_envelope_sealed) if row else None
        except SQLAlchemyError as exc:
            raise ExecutionStorageFailure() from exc
