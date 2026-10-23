from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from .execution import (
    QuarantinedExecutionRecord,
    ReservationTombstone,
)
from .execution_codes import (
    ExecutionPortContractViolationCode,
    ReservationLifecycleState,
    ReservationTerminalCode,
)
from .operation_binding import EvaluationOperationBinding

LEGACY_SCHEMA_VERSION = "0.1.7"
CURRENT_SCHEMA_VERSION = "0.1.9"

# Exact, case-sensitive migration table. Unknown, whitespace, case variants, and
# Unicode lookalikes are intentionally absent and must be quarantined.
LEGACY_V017_TERMINAL_MAP: Mapping[
    tuple[ReservationLifecycleState, str], ReservationTerminalCode
] = MappingProxyType(
    {
        (ReservationLifecycleState.COMMITTED, "RESERVATION_ALREADY_COMMITTED"):
            ReservationTerminalCode.COMMIT_SUCCEEDED,
        (ReservationLifecycleState.ABORTED, "RUN_ABORTED"):
            ReservationTerminalCode.ABORTED_RUNNER_CONTRACT_FAILURE,
        (ReservationLifecycleState.ABORTED, "RUNNER_CONTRACT_FAILURE"):
            ReservationTerminalCode.ABORTED_RUNNER_CONTRACT_FAILURE,
        (ReservationLifecycleState.REJECTED, "INVALID_RESERVATION_STATE"):
            ReservationTerminalCode.REJECTED_INVALID_STATE,
        (ReservationLifecycleState.REJECTED, "RESERVATION_RESULT_BINDING_MISMATCH"):
            ReservationTerminalCode.REJECTED_BINDING_MISMATCH,
        (ReservationLifecycleState.REJECTED, "RESULT_REF_MISMATCH"):
            ReservationTerminalCode.REJECTED_RESULT_REF_MISMATCH,
        (ReservationLifecycleState.REJECTED, "RESULT_DIGEST_MISMATCH"):
            ReservationTerminalCode.REJECTED_RESULT_DIGEST_MISMATCH,
        (ReservationLifecycleState.REJECTED, "RESULT_REVISION_MISMATCH"):
            ReservationTerminalCode.REJECTED_RESULT_REVISION_MISMATCH,
        (ReservationLifecycleState.REJECTED, "EVALUATION_REVISION_CONFLICT"):
            ReservationTerminalCode.REJECTED_EVALUATION_REVISION_CONFLICT,
    }
)


def quarantine_record(
    *,
    record_identity_digest: str,
    source_schema_version: str,
    sequence: int,
) -> QuarantinedExecutionRecord:
    return QuarantinedExecutionRecord(
        record_identity_digest=record_identity_digest,
        source_schema_version=source_schema_version,
        fixed_violation_code=(
            ExecutionPortContractViolationCode.EXECUTION_PORT_CONTRACT_VIOLATION
        ),
        quarantined_at_or_sequence=sequence,
    )


def migrate_v017_tombstone(
    raw: Any,
    *,
    record_identity_digest: str,
    sequence: int,
) -> tuple[ReservationTombstone | None, QuarantinedExecutionRecord | None]:
    """Migrate one exact v0.1.7 tombstone without preserving unknown text."""

    quarantine = quarantine_record(
        record_identity_digest=record_identity_digest,
        source_schema_version=LEGACY_SCHEMA_VERSION,
        sequence=sequence,
    )
    if not isinstance(raw, dict):
        return None, quarantine
    try:
        token_hash = raw["token_hash"]
        state_raw = raw["state"]
        code_raw = raw["terminal_code"]
        binding = raw["binding"]
        precommit_digest = raw["precommit_digest"]
        terminal_sequence = raw["terminal_sequence"]
    except KeyError:
        return None, quarantine
    if not isinstance(state_raw, str) or not isinstance(code_raw, str):
        return None, quarantine
    try:
        state = ReservationLifecycleState(state_raw)
    except ValueError:
        return None, quarantine
    terminal = LEGACY_V017_TERMINAL_MAP.get((state, code_raw))
    if terminal is None:
        return None, quarantine
    if state is ReservationLifecycleState.ACTIVE:
        return None, quarantine
    if type(binding) is not EvaluationOperationBinding:
        return None, quarantine
    if binding.precommit_digest != precommit_digest:
        return None, quarantine
    try:
        tombstone = ReservationTombstone(
            token_hash=token_hash,
            token_collision_guard_digest="0" * 64,
            lifecycle_state=state,
            terminal_code=terminal,
            subject_ref=binding.subject_ref,
            graph_id=binding.graph_id,
            evaluation_id=binding.evaluation_id,
            expected_existing_revision=binding.expected_existing_revision,
            operation_binding_digest=binding.precommit_digest,
            terminal_sequence=int(terminal_sequence),
        )
    except (TypeError, ValueError):
        return None, quarantine
    return tombstone, None
