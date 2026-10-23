"""Formal project integration for immutable Learning Evaluator v0.1.10."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.learning_evaluator import (
    EvaluationContractError,
    EvaluationMode,
    FailingProvider,
    LearningEvaluationRequest,
    LearningEvaluatorRunner,
    result_to_existing_summary,
)
from agents.learning_evaluator.compiler import stable_id
from agents.learning_evaluator.contracts import authority_context_digest
from database.models import (
    GraphNode,
    LearningEvaluation,
    LearningEvaluationResultRecord,
    LearningEvaluationEvidenceRef,
    LearningGraph,
    WorkspaceLearningEvent,
)
from integrations.learning_evaluator_evidence import (
    build_pre_code_submission_trusted_context,
    build_graph_ref,
    build_node_contract,
    build_trusted_context,
    load_assessment_result_evidence,
    load_code_submission_evidence,
    load_question_attempt_evidence,
)
from integrations.learning_evaluator_execution import SqlAlchemyEvaluationExecutionPort
from integrations.learning_evaluator_security import (
    EvaluatorEnvelopeCipher,
    FormalHmacEvaluationResultAuthenticityPort,
)


class EvaluationNotFoundError(LookupError):
    pass


class EvaluationAccessDeniedError(PermissionError):
    pass


def _database_url(db: AsyncSession) -> str:
    bind = db.get_bind()
    return str(bind.url)


async def _owned_graph_node(
    db: AsyncSession, *, user_id: int, graph_ref: str, node_ref: str,
) -> tuple[LearningGraph, GraphNode]:
    graph_conditions = [LearningGraph.external_id == graph_ref]
    if graph_ref.isdigit():
        graph_conditions.append(LearningGraph.id == int(graph_ref))
    graph = await db.scalar(select(LearningGraph).where(
        LearningGraph.user_id == user_id,
        or_(*graph_conditions),
    ))
    if graph is None:
        # Deliberately do not reveal whether another user owns the identifier.
        raise EvaluationAccessDeniedError("learning evaluation target unavailable")
    node_conditions = [GraphNode.external_id == node_ref]
    if node_ref.isdigit():
        node_conditions.append(GraphNode.id == int(node_ref))
    node = await db.scalar(select(GraphNode).where(
        GraphNode.graph_id == graph.id,
        GraphNode.user_id == user_id,
        or_(*node_conditions),
    ))
    if node is None:
        raise EvaluationAccessDeniedError("learning evaluation target unavailable")
    return graph, node


async def _evaluation_node_contracts(
    db: AsyncSession, *, graph: LearningGraph, node: GraphNode,
) -> list:
    """Build the target node plus its declared prerequisites for the formal core.

    The evaluator validates prerequisite references inside one immutable request.
    A single-node evaluation therefore needs the existing prerequisite contracts
    alongside its target.  This is a read-only projection of the same graph,
    not a second learning model or a relaxation of prerequisite validation.
    """
    contracts = [await build_node_contract(db, graph, node)]
    prerequisite_ids = [int(value) for value in list(node.depends_on or []) if str(value).isdigit()]
    if not prerequisite_ids:
        return contracts
    rows = (await db.scalars(select(GraphNode).where(
        GraphNode.graph_id == graph.id,
        GraphNode.user_id == node.user_id,
        GraphNode.id.in_(prerequisite_ids),
    ))).all()
    by_id = {item.id: item for item in rows}
    for prerequisite_id in prerequisite_ids:
        prerequisite = by_id.get(prerequisite_id)
        if prerequisite is not None:
            contracts.append(await build_node_contract(db, graph, prerequisite))
    return contracts


async def _ensure_evaluation(
    db: AsyncSession, *, user_id: int, graph: LearningGraph, node: GraphNode,
) -> LearningEvaluation:
    row = await db.scalar(select(LearningEvaluation).where(
        LearningEvaluation.user_id == user_id,
        LearningEvaluation.graph_id == graph.id,
        LearningEvaluation.node_id == node.id,
    ))
    graph_ref = build_graph_ref(graph)
    external_id = stable_id("eval", f"user-{user_id}", graph_ref.graph_id, graph_ref.graph_continuity_key)
    if row is None:
        row = LearningEvaluation(
            external_id=external_id,
            user_id=user_id,
            graph_id=graph.id,
            graph_continuity_key=graph_ref.graph_continuity_key,
            node_id=node.id,
            node_external_id=str(node.external_id or f"node-{node.id}"),
            current_revision=0,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    elif row.external_id != external_id or row.graph_continuity_key != graph_ref.graph_continuity_key:
        raise RuntimeError("EVALUATION_AGGREGATE_IDENTITY_MISMATCH")
    return row


async def _latest_record(
    db: AsyncSession, *, evaluation_db_id: int,
) -> LearningEvaluationResultRecord | None:
    return await db.scalar(select(LearningEvaluationResultRecord).where(
        LearningEvaluationResultRecord.evaluation_id == evaluation_db_id,
    ).order_by(LearningEvaluationResultRecord.evaluation_revision.desc()).limit(1))


async def _record_at_revision(
    db: AsyncSession, *, evaluation_db_id: int, revision: int,
) -> LearningEvaluationResultRecord | None:
    return await db.scalar(select(LearningEvaluationResultRecord).where(
        LearningEvaluationResultRecord.evaluation_id == evaluation_db_id,
        LearningEvaluationResultRecord.evaluation_revision == revision,
    ))


def _stable_evaluated_at(evidence, graph: LearningGraph) -> datetime:
    """Use the newest durable server fact as the evaluation clock.

    A network replay must rebuild the exact same request digest; wall-clock now()
    would turn a legitimate replay into an idempotency conflict.
    """
    timestamps = [item.received_at for item in evidence if getattr(item, "received_at", None) is not None]
    value = max(timestamps) if timestamps else (graph.updated_at or graph.created_at or datetime.utcnow())
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _run_core(
    request: LearningEvaluationRequest,
    trusted_context,
    execution_port: SqlAlchemyEvaluationExecutionPort,
    authenticity_port: FormalHmacEvaluationResultAuthenticityPort,
):
    # The formal project provider boundary is intentionally unavailable in the
    # offline validation environment. The immutable core invokes its own strict,
    # deterministic fallback after a closed ProviderUnavailable failure.
    runner = LearningEvaluatorRunner(
        FailingProvider(),
        execution_port=execution_port,
        authenticity_port=authenticity_port,
    )
    return runner.run(request, trusted_context)


async def evaluate_graph_node(
    db: AsyncSession,
    *,
    user_id: int,
    graph_ref: str,
    node_ref: str,
    request_id: str,
    idempotency_key: str,
    expected_evaluation_revision: int | None,
) -> dict[str, Any]:
    graph, node = await _owned_graph_node(
        db, user_id=user_id, graph_ref=graph_ref, node_ref=node_ref,
    )
    graph_db_id = graph.id
    node_db_id = node.id
    graph_reference = build_graph_ref(graph)
    node_contracts = await _evaluation_node_contracts(db, graph=graph, node=node)
    question_evidence = await load_question_attempt_evidence(db, user_id=user_id, graph=graph, node=node)
    assessment_evidence = await load_assessment_result_evidence(db, user_id=user_id, graph=graph, node=node)
    code_evidence = await load_code_submission_evidence(db, user_id=user_id, graph=graph, node=node)
    evidence = sorted(
        [*question_evidence, *assessment_evidence, *code_evidence],
        key=lambda record: int(str(record.source_record_id).rsplit("-", 1)[-1]),
    )
    trusted_context = build_trusted_context(user_id)
    evaluation = await _ensure_evaluation(db, user_id=user_id, graph=graph, node=node)
    latest = await _latest_record(db, evaluation_db_id=evaluation.id)

    authenticity = FormalHmacEvaluationResultAuthenticityPort()
    cipher = EvaluatorEnvelopeCipher()
    existing_summary = None
    expected = (
        latest.evaluation_revision if latest is not None and expected_evaluation_revision is None
        else 0 if expected_evaluation_revision is None
        else expected_evaluation_revision
    )
    if expected > 0:
        # A retry of a committed incremental operation carries its original base
        # revision. Rebuild the request from that exact durable base result, not
        # from the newer latest result, so the request digest remains stable.
        base_record = await _record_at_revision(
            db, evaluation_db_id=evaluation.id, revision=expected,
        )
        if base_record is None:
            raise ValueError("EVALUATION_BASE_REVISION_NOT_FOUND")
        envelope = cipher.open_envelope(base_record.authenticity_envelope_sealed)
        history_context = trusted_context
        if envelope.trusted_context_digest != authority_context_digest(trusted_context):
            legacy_context = build_pre_code_submission_trusted_context(user_id)
            if envelope.trusted_context_digest != authority_context_digest(legacy_context):
                raise ValueError("EVALUATION_HISTORY_AUTHORITY_CONTEXT_UNKNOWN")
            history_context = legacy_context
        existing_summary = result_to_existing_summary(envelope, history_context, authenticity)
        if history_context is not trusted_context:
            # The old envelope has just been fully authenticated under its exact
            # historical policy. Rebind only the projected compatibility fields
            # to the known additive policy so the immutable core can issue the
            # next revision; evidence history and every node binding remain intact.
            existing_summary.trusted_context_version = trusted_context.context_version
            existing_summary.authority_context_digest = authority_context_digest(trusted_context)
        mode = EvaluationMode.INCREMENTAL
    else:
        mode = EvaluationMode.INITIAL

    request = LearningEvaluationRequest(
        request_id=request_id,
        idempotency_key=idempotency_key,
        subject_ref=f"user-{user_id}",
        evaluation_mode=mode,
        graph_ref=graph_reference,
        node_contracts=node_contracts,
        evidence_records=evidence,
        existing_evaluation_summary=existing_summary,
        expected_existing_revision=expected,
        locale="zh-CN",
        evaluated_at=_stable_evaluated_at(evidence, graph),
    )
    provider_runtime = "TEST_PROVIDER" if os.environ.get("KAKI_EVALUATOR_TEST_PROVIDER") == "1" else "DETERMINISTIC_FALLBACK"
    port = SqlAlchemyEvaluationExecutionPort(
        database_url=_database_url(db),
        evaluation_db_id=evaluation.id,
        evaluation_external_id=evaluation.external_id,
        user_id=user_id,
        graph_db_id=graph.id,
        graph_external_id=graph_reference.graph_id,
        graph_continuity_key=graph_reference.graph_continuity_key,
        node_db_id=node.id,
        provider_runtime=provider_runtime,
        adapter_contract_versions=dict(trusted_context.adapter_contract_versions),
        cipher=cipher,
    )
    # End the async read transaction before the synchronous formal execution
    # Port starts its atomic reservation/commit transaction over the same DB.
    await db.rollback()
    try:
        envelope = await asyncio.to_thread(_run_core, request, trusted_context, port, authenticity)
    finally:
        port.close()
    return envelope.result.public_view.model_dump(mode="json")


async def auto_consume_exam_submission_event(
    db: AsyncSession,
    *,
    user_id: int,
    workspace_event_id: int,
) -> dict[str, Any]:
    """Consume one authoritative exam_submitted event through the existing evaluator.

    This is deliberately a thin orchestration layer: WorkspaceLearningEvent and
    ExamPaper remain the evidence authorities, evaluate_graph_node remains the
    only evaluator execution path, and LearningEvaluationEvidenceRef is the
    durable consumed-event ledger.
    """
    event = await db.scalar(select(WorkspaceLearningEvent).where(
        WorkspaceLearningEvent.id == int(workspace_event_id),
        WorkspaceLearningEvent.user_id == user_id,
        WorkspaceLearningEvent.event_type == "exam_submitted",
    ))
    if event is None:
        raise EvaluationAccessDeniedError("learning evaluation evidence unavailable")
    event_id = int(event.id)
    payload = dict(event.payload or {})
    graph_ref = str(payload.get("graph_id") or "").strip()
    node_ref = str(payload.get("graph_node_external_id") or "").strip()
    if not graph_ref or not node_ref:
        raise ValueError("EXAM_EVIDENCE_TARGET_MISSING")

    graph, node = await _owned_graph_node(
        db, user_id=user_id, graph_ref=graph_ref, node_ref=node_ref,
    )
    graph_db_id = graph.id
    node_db_id = node.id
    assessment = await load_assessment_result_evidence(
        db, user_id=user_id, graph=graph, node=node,
    )
    source_record_id = f"workspace-event-{event_id}"
    if not any(record.source_record_id == source_record_id for record in assessment):
        raise ValueError("EXAM_EVIDENCE_NOT_TRUSTED")

    evaluation = await db.scalar(select(LearningEvaluation).where(
        LearningEvaluation.user_id == user_id,
        LearningEvaluation.graph_id == graph_db_id,
        LearningEvaluation.node_id == node_db_id,
    ))
    if evaluation is not None:
        consumed = await db.scalar(select(LearningEvaluationEvidenceRef).where(
            LearningEvaluationEvidenceRef.evaluation_id == evaluation.id,
            LearningEvaluationEvidenceRef.user_id == user_id,
            LearningEvaluationEvidenceRef.graph_id == graph_db_id,
            LearningEvaluationEvidenceRef.node_id == node_db_id,
            LearningEvaluationEvidenceRef.workspace_event_id == event_id,
        ))
        if consumed is not None:
            return {
                "evaluation_status": "evaluated",
                "evaluation_revision": int(evaluation.current_revision or consumed.first_seen_revision or 0),
                "idempotent_replay": True,
                "workspace_event_id": event_id,
            }
        expected_revision = int(evaluation.current_revision or 0)
    else:
        expected_revision = 0

    # Both identities are deterministic functions of server-owned durable facts.
    # Including the real base revision permits a safe later retry after a
    # concurrent evaluator operation advances the aggregate without consuming
    # this event.
    identity = f"workspace-exam-event-{event_id}-base-{expected_revision}"
    result = await evaluate_graph_node(
        db,
        user_id=user_id,
        graph_ref=graph_ref,
        node_ref=node_ref,
        request_id=f"auto-{identity}",
        idempotency_key=f"auto-{identity}",
        expected_evaluation_revision=expected_revision,
    )

    # A successful evaluator response is not enough by itself: require the
    # atomic evidence ledger to prove this exact event was consumed.
    evaluation = await db.scalar(select(LearningEvaluation).where(
        LearningEvaluation.user_id == user_id,
        LearningEvaluation.graph_id == graph_db_id,
        LearningEvaluation.node_id == node_db_id,
    ))
    consumed = None if evaluation is None else await db.scalar(select(LearningEvaluationEvidenceRef).where(
        LearningEvaluationEvidenceRef.evaluation_id == evaluation.id,
        LearningEvaluationEvidenceRef.workspace_event_id == event_id,
    ))
    if evaluation is None or consumed is None:
        raise RuntimeError("EXAM_EVIDENCE_COMMIT_NOT_OBSERVED")
    return {
        "evaluation_status": "evaluated",
        "evaluation_revision": int(result.get("evaluation_revision") or evaluation.current_revision or 0),
        "idempotent_replay": False,
        "workspace_event_id": event_id,
    }


async def get_latest_public_evaluation(
    db: AsyncSession, *, user_id: int, graph_ref: str, node_ref: str,
) -> dict[str, Any] | None:
    graph, node = await _owned_graph_node(db, user_id=user_id, graph_ref=graph_ref, node_ref=node_ref)
    evaluation = await db.scalar(select(LearningEvaluation).where(
        LearningEvaluation.user_id == user_id,
        LearningEvaluation.graph_id == graph.id,
        LearningEvaluation.node_id == node.id,
    ))
    if evaluation is None:
        return None
    latest = await _latest_record(db, evaluation_db_id=evaluation.id)
    return dict(latest.public_projection) if latest else None


async def get_public_evaluation_history(
    db: AsyncSession, *, user_id: int, graph_ref: str, node_ref: str,
) -> list[dict[str, Any]]:
    graph, node = await _owned_graph_node(db, user_id=user_id, graph_ref=graph_ref, node_ref=node_ref)
    evaluation = await db.scalar(select(LearningEvaluation).where(
        LearningEvaluation.user_id == user_id,
        LearningEvaluation.graph_id == graph.id,
        LearningEvaluation.node_id == node.id,
    ))
    if evaluation is None:
        return []
    rows = (await db.execute(select(LearningEvaluationResultRecord).where(
        LearningEvaluationResultRecord.evaluation_id == evaluation.id,
    ).order_by(LearningEvaluationResultRecord.evaluation_revision.desc()))).scalars().all()
    return [dict(row.public_projection) for row in rows]
