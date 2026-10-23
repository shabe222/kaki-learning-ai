"""PC02E adapters over existing code runner, attempt, evidence, evaluator, and plan authorities."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    GeneratedItem, GeneratedItemVersion, GraphNode, GraphResourcePlan,
    InteractiveQuestionAttempt, LearningEvaluation, LearningEvaluationEvidenceRef,
    LearningGraph, WorkspaceLearningEvent,
)
from services.learning_evaluator_integration import evaluate_graph_node
from services.workspace_artifact_versions import ArtifactVersionNotFound, get_owned_artifact, get_version
from services.code_executor import run_private_closure_counter_judge


class CodeLabError(ValueError):
    code = "CODE_LAB_ERROR"
    status_code = 409


class CodeLabNotFound(CodeLabError):
    code = "CODE_LAB_NOT_FOUND"
    status_code = 404


class CodeLabInvalid(CodeLabError):
    code = "CODE_LAB_INVALID"
    status_code = 422


class CodeLabConflict(CodeLabError):
    code = "CODE_LAB_CONFLICT"
    status_code = 409


ITEM_IDENTITY = "code-lab-submit"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _attempt_public(row: InteractiveQuestionAttempt) -> dict[str, Any]:
    selected = dict(row.selected_answer_public or {})
    return {
        "attempt_id": row.id, "artifact_id": row.artifact_id,
        "artifact_version_id": row.artifact_version_id, "item_identity": row.item_identity,
        "attempt_number": row.attempt_number, "code_digest": selected.get("code_digest"),
        "correct": bool(row.correct), "tests_total": int(selected.get("tests_total") or 0),
        "tests_passed": int(selected.get("tests_passed") or 0),
        "tests_failed": int(selected.get("tests_failed") or 0),
        "exit_status": int(selected.get("exit_status") or 0),
        "runtime_error_category": selected.get("runtime_error_category"),
        "status": row.status, "submitted_at": row.submitted_at.isoformat() if row.submitted_at else "",
        "learning_event_ref": f"workspace-event-{row.event_id}" if row.event_id else None,
    }


async def _owned_code_lab(
    db: AsyncSession, *, user_id: int, artifact_id: int, artifact_version_id: int,
    thread_id: str | None = None, require_current: bool = False,
) -> tuple[GeneratedItem, GeneratedItemVersion, dict[str, Any], dict[str, Any]]:
    try:
        item = await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
        version = await get_version(db, item=item, version_id=artifact_version_id)
    except ArtifactVersionNotFound as exc:
        raise CodeLabNotFound("Code Lab 不存在或无权访问") from exc
    if item.type != "workspace_code":
        raise CodeLabInvalid("当前 Artifact 不是 Code Lab")
    data = dict(version.public_data_snapshot or {})
    private = dict(version.private_data_snapshot or {})
    if dict(data.get("canonical_purpose") or {}).get("experience") != "PERSONALIZED_CODE_LAB":
        raise CodeLabInvalid("当前代码资源不支持正式提交")
    owned_thread = str(data.get("thread_id") or (item.raw_data or {}).get("thread_id") or "")
    if thread_id is not None and owned_thread and owned_thread != thread_id:
        raise CodeLabNotFound("Code Lab 不属于当前会话")
    if require_current and int(item.current_version_id or 0) != int(version.id):
        raise CodeLabConflict("Code Lab 版本已变化，请刷新后再提交")
    judge = dict(private.get("judge_contract_private") or {})
    if judge.get("contract") != "darya.code-lab-judge.v1" or not judge.get("judge_source"):
        raise CodeLabInvalid("当前 Code Lab 缺少可信判定契约")
    return item, version, data, judge


def _judge(source: str, judge: Mapping[str, Any]) -> dict[str, Any]:
    result = run_private_closure_counter_judge(source)
    total = int(judge.get("tests_total") or 0)
    passed = int(result.tests_passed)
    correct = result.exit_code == 0 and not result.timed_out and result.tests_total == total and passed == total
    return {
        "correct": correct, "tests_total": total, "tests_passed": passed,
        "tests_failed": max(total - passed, 0), "exit_status": int(result.exit_code),
        "timed_out": bool(result.timed_out), "runtime_error_category": result.error_category or None,
        "stdout": "",
        "stderr": str(result.error_message or "")[-2000:],
        "duration_ms": int(result.duration_ms or 0),
    }


async def submit_code_lab(
    db: AsyncSession, *, user_id: int, thread_id: str, artifact_id: int,
    artifact_version_id: int, source: str, submission_request_id: str, idempotency_key: str,
) -> tuple[dict[str, Any], bool]:
    source = str(source or "")
    if not source.strip():
        raise CodeLabInvalid("请先填写代码再提交")
    if len(source.encode("utf-8")) > 24 * 1024 or source.count("\n") + 1 > 400:
        raise CodeLabInvalid("提交代码超过当前练习运行环境限制")
    if not submission_request_id or len(submission_request_id) > 120:
        raise CodeLabInvalid("缺少有效提交身份")
    if not idempotency_key or len(idempotency_key) > 240:
        raise CodeLabInvalid("缺少有效幂等身份")
    item, version, data, judge = await _owned_code_lab(
        db, user_id=user_id, artifact_id=artifact_id, artifact_version_id=artifact_version_id,
        thread_id=thread_id, require_current=True,
    )
    code_digest = f"sha256:{_sha(source)}"
    digest = _sha(f"code-lab-submit-v1\0{user_id}\0{artifact_id}\0{version.id}\0{idempotency_key}")
    guard = _sha(_canonical({
        "submission_request_id": submission_request_id, "thread_id": thread_id,
        "artifact_version_id": version.id, "code_digest": code_digest,
    }))
    existing = await db.scalar(select(InteractiveQuestionAttempt).where(
        InteractiveQuestionAttempt.artifact_version_id == version.id,
        InteractiveQuestionAttempt.item_identity == ITEM_IDENTITY,
        InteractiveQuestionAttempt.submission_key_digest == digest,
    ))
    if existing is not None:
        if existing.request_guard != guard:
            raise CodeLabConflict("相同幂等身份对应了不同代码")
        return {"attempt": _attempt_public(existing), "judgment": dict(existing.selected_answer_public or {})}, True

    judgment = _judge(source, judge)
    attempt_number = int(await db.scalar(select(func.max(InteractiveQuestionAttempt.attempt_number)).where(
        InteractiveQuestionAttempt.user_id == user_id,
        InteractiveQuestionAttempt.artifact_version_id == version.id,
        InteractiveQuestionAttempt.item_identity == ITEM_IDENTITY,
    )) or 0) + 1
    public_judgment = {
        "code_digest": code_digest, "tests_total": judgment["tests_total"],
        "tests_passed": judgment["tests_passed"], "tests_failed": judgment["tests_failed"],
        "exit_status": judgment["exit_status"], "timed_out": judgment["timed_out"],
        "runtime_error_category": judgment["runtime_error_category"],
        "stdout": judgment["stdout"], "stderr": judgment["stderr"], "duration_ms": judgment["duration_ms"],
    }
    attempt = InteractiveQuestionAttempt(
        user_id=user_id, thread_id=thread_id, artifact_id=item.id, artifact_version_id=version.id,
        item_identity=ITEM_IDENTITY, attempt_number=attempt_number,
        submission_key_digest=digest, request_guard=guard,
        selected_answer_public=public_judgment, correct=bool(judgment["correct"]),
        hint_used=False, solution_viewed=False, status="SUBMITTED", submitted_at=_now(),
    )
    db.add(attempt)
    await db.flush()
    payload = {
        "attempt_id": attempt.id, "attempt_number": attempt.attempt_number,
        "artifact_version_id": version.id, "artifact_version_number": version.version_number,
        "item_identity": ITEM_IDENTITY, "code_digest": code_digest,
        "correct": bool(judgment["correct"]), "tests_total": judgment["tests_total"],
        "tests_passed": judgment["tests_passed"], "tests_failed": judgment["tests_failed"],
        "exit_status": judgment["exit_status"], "runtime_error_category": judgment["runtime_error_category"],
        "manual_copy_detected": False, "independent_solution": True,
        "graph_id": data.get("graph_id"),
        "graph_node_external_id": data.get("graph_node_external_id") or data.get("graph_node_id"),
        "resource_plan_id": data.get("resource_plan_id") or data.get("resource_plan_external_id"),
        "evidence_source": "server_judged_code_submission",
    }
    event = WorkspaceLearningEvent(
        user_id=user_id, thread_id=thread_id, artifact_id=item.id,
        event_type="code_submission_judged", payload=payload,
    )
    db.add(event)
    await db.flush()
    attempt.event_id = event.id
    await db.flush()
    return {
        "attempt": _attempt_public(attempt),
        "judgment": {**public_judgment, "correct": bool(judgment["correct"])},
        "learning_event_ref": f"workspace-event-{event.id}",
        "evaluation_status": "READY_FOR_EVALUATION",
    }, False


async def list_code_lab_submissions(
    db: AsyncSession, *, user_id: int, artifact_id: int, artifact_version_id: int,
) -> list[dict[str, Any]]:
    await _owned_code_lab(db, user_id=user_id, artifact_id=artifact_id, artifact_version_id=artifact_version_id)
    rows = list((await db.scalars(select(InteractiveQuestionAttempt).where(
        InteractiveQuestionAttempt.user_id == user_id,
        InteractiveQuestionAttempt.artifact_id == artifact_id,
        InteractiveQuestionAttempt.artifact_version_id == artifact_version_id,
        InteractiveQuestionAttempt.item_identity == ITEM_IDENTITY,
    ).order_by(InteractiveQuestionAttempt.attempt_number.desc()))).all())
    return [_attempt_public(row) for row in rows]


async def _owned_graph_node(db: AsyncSession, *, user_id: int, graph_ref: str, node_ref: str):
    graph = await db.scalar(select(LearningGraph).where(LearningGraph.user_id == user_id, LearningGraph.external_id == graph_ref))
    node = None if graph is None else await db.scalar(select(GraphNode).where(
        GraphNode.user_id == user_id, GraphNode.graph_id == graph.id, GraphNode.external_id == node_ref,
    ))
    if graph is None or node is None:
        raise CodeLabNotFound("学习目标不存在或无权访问")
    return graph, node


def _result(correct: bool, plan: GraphResourcePlan, revision: int) -> dict[str, Any]:
    if correct:
        return {
            "outcome": "ADVANCE", "title": "提交通过，继续提升",
            "demonstrated": "可信测试确认你能让闭包保持独立状态，并把重绑定规则迁移到不同步长。",
            "remaining": "还需要在更开放的业务状态场景中继续确认迁移稳定性。",
            "recommendation": "下一步进入更开放的状态封装任务，不重复这个计数器。",
            "why_changed": "只有这次明确提交并经服务器判定的结果成为证据，所以资源计划现在可以继续提升。",
            "resource_type": plan.resource_type, "evaluation_revision": revision,
        }
    return {
        "outcome": "REMEDIATION", "title": "提交未通过，先定向调试",
        "demonstrated": "运行结果已经定位到闭包状态更新仍有具体缺口。",
        "remaining": "需要先修复外层名字重绑定，并再次检查独立闭包是否互不影响。",
        "recommendation": "下一步缩小到带单步检查点的调试任务，不重复整份 Code Lab。",
        "why_changed": "明确提交后的可信测试未全部通过，因此下一资源降低问题表面积并增加调试支架。",
        "resource_type": plan.resource_type, "evaluation_revision": revision,
    }


async def replan_after_code_submission(
    db: AsyncSession, *, user_id: int, artifact_id: int, artifact_version_id: int, attempt_id: int,
) -> dict[str, Any]:
    item, version, data, _ = await _owned_code_lab(
        db, user_id=user_id, artifact_id=artifact_id, artifact_version_id=artifact_version_id,
    )
    attempt = await db.scalar(select(InteractiveQuestionAttempt).where(
        InteractiveQuestionAttempt.id == attempt_id, InteractiveQuestionAttempt.user_id == user_id,
        InteractiveQuestionAttempt.artifact_id == item.id,
        InteractiveQuestionAttempt.artifact_version_id == version.id,
        InteractiveQuestionAttempt.item_identity == ITEM_IDENTITY,
    ))
    if attempt is None or attempt.event_id is None:
        raise CodeLabInvalid("需要已保存的可信提交证据")
    attempt_db_id = int(attempt.id)
    event_id, correct, thread_id = int(attempt.event_id), bool(attempt.correct), str(attempt.thread_id)
    graph_ref = str(data.get("graph_id") or "")
    node_ref = str(data.get("graph_node_external_id") or "")
    graph, node = await _owned_graph_node(db, user_id=user_id, graph_ref=graph_ref, node_ref=node_ref)
    graph_id, node_id = int(graph.id), int(node.id)
    evaluation = await db.scalar(select(LearningEvaluation).where(
        LearningEvaluation.user_id == user_id, LearningEvaluation.graph_id == graph_id,
        LearningEvaluation.node_id == node_id,
    ))
    consumed = None if evaluation is None else await db.scalar(select(LearningEvaluationEvidenceRef).where(
        LearningEvaluationEvidenceRef.evaluation_id == evaluation.id,
        LearningEvaluationEvidenceRef.workspace_event_id == event_id,
    ))
    if consumed is None:
        base = int(evaluation.current_revision or 0) if evaluation else 0
        identity = f"pc02e-attempt-{attempt_db_id}-base-{base}"
        await evaluate_graph_node(
            db, user_id=user_id, graph_ref=graph_ref, node_ref=node_ref,
            request_id=identity, idempotency_key=identity, expected_evaluation_revision=base,
        )
        evaluation = await db.scalar(select(LearningEvaluation).where(
            LearningEvaluation.user_id == user_id, LearningEvaluation.graph_id == graph_id,
            LearningEvaluation.node_id == node_id,
        ))
        consumed = None if evaluation is None else await db.scalar(select(LearningEvaluationEvidenceRef).where(
            LearningEvaluationEvidenceRef.evaluation_id == evaluation.id,
            LearningEvaluationEvidenceRef.workspace_event_id == event_id,
        ))
        if consumed is None:
            raise RuntimeError("CODE_SUBMISSION_EVIDENCE_NOT_CONSUMED")
    revision = int(evaluation.current_revision or consumed.first_seen_revision or 0)
    external_id = f"pc02e-next-{event_id}"
    plan = await db.scalar(select(GraphResourcePlan).where(
        GraphResourcePlan.graph_id == graph_id, GraphResourcePlan.external_id == external_id,
    ))
    if plan is None:
        node = await db.get(GraphNode, node_id)
        if node is None or node.user_id != user_id:
            raise RuntimeError("CODE_LAB_NODE_RELOAD_FAILED")
        plan = GraphResourcePlan(
            graph_id=graph_id, node_id=node_id, external_id=external_id,
            continuity_key=f"pc02e-event-{event_id}", resource_type="CODE",
            purpose=("进入更开放的闭包状态封装迁移任务" if correct else "用单步检查点调试闭包重绑定和状态隔离"),
            generation_spec={
                "decision": "ADVANCE_OPEN_STATE_TASK" if correct else "REMEDIATE_CODE_REBINDING",
                "source_evidence_event": f"workspace-event-{event_id}",
                "target_capability": "closure_state_transfer" if correct else "closure_rebinding_debug",
                "support": "low_open_transfer" if correct else "focused_debug_scaffold",
                "next_action": "open_project_candidate" if correct else "smaller_debug_challenge",
                "auto_generate": False,
            },
            required_inputs=["latest_formal_evaluation", "knowledge_node", "source_constraints"],
            trigger="ELIGIBLE_CODE_SUBMISSION_EVIDENCE", status="planned", priority=95,
            private_annotations={"code_lab_artifact_id": artifact_id, "attempt_id": attempt_db_id, "event_id": event_id},
            thread_id=thread_id,
        )
        db.add(plan)
        await db.flush()
        ids = list(node.resource_plan_ids or [])
        if external_id not in ids:
            node.resource_plan_ids = [*ids, external_id]
        await db.flush()
    return _result(correct, plan, revision)


async def get_code_lab_result(
    db: AsyncSession, *, user_id: int, artifact_id: int, artifact_version_id: int,
) -> dict[str, Any] | None:
    attempt = await db.scalar(select(InteractiveQuestionAttempt).where(
        InteractiveQuestionAttempt.user_id == user_id,
        InteractiveQuestionAttempt.artifact_id == artifact_id,
        InteractiveQuestionAttempt.artifact_version_id == artifact_version_id,
        InteractiveQuestionAttempt.item_identity == ITEM_IDENTITY,
    ).order_by(InteractiveQuestionAttempt.attempt_number.desc()))
    if attempt is None or attempt.event_id is None:
        return None
    plan = await db.scalar(select(GraphResourcePlan).where(GraphResourcePlan.external_id == f"pc02e-next-{attempt.event_id}"))
    if plan is None:
        return None
    evaluation = await db.scalar(select(LearningEvaluation).where(
        LearningEvaluation.user_id == user_id, LearningEvaluation.graph_id == plan.graph_id,
        LearningEvaluation.node_id == plan.node_id,
    ))
    return _result(bool(attempt.correct), plan, int(evaluation.current_revision or 0) if evaluation else 0)
