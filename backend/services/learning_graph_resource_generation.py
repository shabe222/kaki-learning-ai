"""Lazy materialization for formal v0.1.3 graph resource plans."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    GeneratedItem,
    GraphNode,
    GraphResourceGenerationRun,
    GraphResourcePlan,
    LearningGraph,
    StudentProfile,
    WorkspaceLearningEvent,
)
from services.workspace_artifact_versions import ensure_initial_version


ResourceDispatcher = Callable[..., Awaitable[dict[str, Any]]]

_RESOURCE_AGENT = {
    "HANDOUT": "lecture",
    "QUESTION": "exam",
    "CODE": "code",
    "MIND_MAP": "mindmap",
    "INTERACTIVE_CLASS": "presentation",
    "PPT": "presentation",
    "VIDEO": "video",
}

_ARTIFACT_TYPE = {
    "HANDOUT": "workspace_lecture",
    "QUESTION": "workspace_question",
    "CODE": "workspace_code",
    "MIND_MAP": "workspace_mindmap",
    "INTERACTIVE_CLASS": "workspace_interactive_lesson",
    "PPT": "workspace_presentation",
    "VIDEO": "workspace_video",
}


async def _owned_identity(
    db: AsyncSession, *, user_id: int, graph_id: int, node_id: int,
    resource_plan_id: int,
) -> tuple[LearningGraph, GraphNode, GraphResourcePlan]:
    graph = await db.get(LearningGraph, graph_id)
    if graph is None:
        raise ValueError("graph identity not found")
    node = await db.get(GraphNode, node_id)
    if node is None or node.graph_id != graph.id:
        raise ValueError("node identity does not belong to graph")
    plan = await db.get(GraphResourcePlan, resource_plan_id)
    if plan is None or plan.graph_id != graph.id or plan.node_id != node.id:
        raise ValueError("resource plan identity does not belong to graph node")
    if graph.user_id != user_id or node.user_id != user_id:
        raise PermissionError("graph resource identity belongs to another user")
    return graph, node, plan


async def resolve_resource_record_ids(
    db: AsyncSession, *, user_id: int, graph_id: str, node_id: str,
    resource_plan_id: str,
) -> tuple[int, int, int]:
    """Resolve public durable identities inside the authenticated aggregate."""
    graph = await db.scalar(select(LearningGraph).where(
        LearningGraph.user_id == user_id,
        LearningGraph.external_id == graph_id,
    ))
    if graph is None:
        exists_for_another_user = await db.scalar(select(LearningGraph.id).where(
            LearningGraph.external_id == graph_id,
        ))
        if exists_for_another_user is not None:
            raise PermissionError("graph belongs to another user")
        raise ValueError("graph identity not found")
    node = await db.scalar(select(GraphNode).where(
        GraphNode.graph_id == graph.id,
        GraphNode.user_id == user_id,
        GraphNode.external_id == node_id,
    ))
    if node is None:
        raise ValueError("node identity does not belong to graph")
    plan = await db.scalar(select(GraphResourcePlan).where(
        GraphResourcePlan.graph_id == graph.id,
        GraphResourcePlan.node_id == node.id,
        GraphResourcePlan.external_id == resource_plan_id,
    ))
    if plan is None:
        raise ValueError("resource plan identity does not belong to graph node")
    return graph.id, node.id, plan.id


async def _existing_execution(
    db: AsyncSession, *, user_id: int, execution_key: str,
) -> GraphResourceGenerationRun | None:
    return await db.scalar(select(GraphResourceGenerationRun).where(
        GraphResourceGenerationRun.user_id == user_id,
        GraphResourceGenerationRun.execution_key == execution_key,
    ))


async def materialize_resource_plan(
    db: AsyncSession, *, user_id: int, thread_id: str, graph_id: int,
    node_id: int, resource_plan_id: int, execution_key: str,
    dispatcher: ResourceDispatcher | None = None,
) -> GeneratedItem:
    """Materialize one exact plan; no title/type/only-plan identity fallback."""
    if not execution_key or len(execution_key) > 140:
        raise ValueError("invalid resource execution key")
    graph, node, plan = await _owned_identity(
        db,
        user_id=user_id,
        graph_id=graph_id,
        node_id=node_id,
        resource_plan_id=resource_plan_id,
    )
    claimed = await _existing_execution(
        db, user_id=user_id, execution_key=execution_key,
    )
    if claimed is not None and (
        claimed.graph_id != graph.id
        or claimed.node_id != node.id
        or claimed.resource_plan_id != plan.id
        or claimed.thread_id != thread_id
    ):
        raise ValueError("execution identity is bound to a different resource request")
    if plan.status == "generated":
        if (
            not plan.artifact_id
            or plan.execution_task_id != execution_key
            or claimed is None
            or claimed.status != "generated"
            or claimed.artifact_id != plan.artifact_id
        ):
            raise ValueError("generated resource identity does not match execution")
        artifact = await db.scalar(select(GeneratedItem).where(
            GeneratedItem.id == plan.artifact_id,
            GeneratedItem.user_id == user_id,
        ))
        if artifact is None:
            raise ValueError("generated plan has no owned artifact")
        return artifact
    if plan.status == "failed":
        raise ValueError("failed resource requires explicit retry_resource_generation")
    if plan.status == "generating":
        raise ValueError("resource generation is already in progress")
    if plan.status != "planned" or plan.artifact_id is not None:
        raise ValueError("resource plan historical status is not materializable")
    if claimed is not None:
        raise ValueError("execution identity already exists in resource history")

    agent_key = _RESOURCE_AGENT.get(plan.resource_type)
    artifact_type = _ARTIFACT_TYPE.get(plan.resource_type)
    if not agent_key or not artifact_type:
        raise ValueError("resource type has no executable registered agent")

    claim = await db.execute(update(GraphResourcePlan).where(
        GraphResourcePlan.id == plan.id,
        GraphResourcePlan.status == "planned",
        GraphResourcePlan.artifact_id.is_(None),
        GraphResourcePlan.execution_task_id.is_(None),
    ).values(
        status="generating",
        execution_task_id=execution_key,
        thread_id=thread_id,
        failure_metadata={},
    ))
    if claim.rowcount != 1:
        await db.rollback()
        raise ValueError("resource plan is already claimed")
    # Keep the already-loaded identity map aligned with the successful
    # compare-and-set without issuing a refresh transaction before dispatch.
    plan.status = "generating"
    plan.execution_task_id = execution_key
    plan.thread_id = thread_id
    plan.failure_metadata = {}
    run = GraphResourceGenerationRun(
        user_id=user_id,
        graph_id=graph.id,
        node_id=node.id,
        resource_plan_id=plan.id,
        execution_key=execution_key,
        thread_id=thread_id,
        status="claimed",
        failure_metadata={},
    )
    db.add(run)
    await db.commit()

    active_dispatcher = dispatcher or dispatch_registered_resource_agent
    try:
        output = await active_dispatcher(
            db=db,
            user_id=user_id,
            thread_id=thread_id,
            graph=graph,
            node=node,
            plan=plan,
            agent_key=agent_key,
            resource_type=plan.resource_type,
            purpose=plan.purpose,
            generation_spec=dict(plan.generation_spec or {}),
            required_inputs=list(plan.required_inputs or []),
        )
        title = str(output.get("title") or plan.purpose or node.title).strip()[:200]
        content = str(output.get("content") or "").strip()
        if not content:
            raise ValueError("registered resource agent returned no usable content")
        raw = dict(output.get("raw_data") or {})
        item = GeneratedItem(
            user_id=user_id,
            type=artifact_type,
            title=title,
            chapter=node.title[:120],
            content=content,
            raw_data={
                **raw,
                "source": "learning_graph",
                "thread_id": thread_id,
                "graph_id": graph.external_id,
                "graph_node_id": node.id,
                "graph_node_external_id": node.external_id,
                "resource_plan_id": plan.id,
                "resource_plan_external_id": plan.external_id,
                "resource_plan_continuity_key": plan.continuity_key,
                "version": 1,
            },
        )
        db.add(item)
        await db.flush()
        # A generated graph resource is not durable PC02 content until its
        # immutable V1 and current pointer exist in the same transaction.
        version = await ensure_initial_version(db, item)
        plan.status = "generated"
        plan.artifact_id = item.id
        plan.failure_metadata = {}
        run.status = "generated"
        run.artifact_id = item.id
        run.failure_metadata = {}
        db.add(WorkspaceLearningEvent(
            user_id=user_id,
            thread_id=thread_id,
            artifact_id=item.id,
            event_type="learning_graph_resource_generated",
            payload={
                "graph_id": graph.external_id,
                "graph_node_id": node.id,
                "resource_plan_id": plan.id,
                "resource_plan_external_id": plan.external_id,
                "artifact_version_id": version.id,
                "artifact_version_number": version.version_number,
            },
        ))
        _sync_legacy_resources(node, plan)
        await db.commit()
        await db.refresh(item)
        return item
    except Exception as exc:
        await db.rollback()
        # Reload after rollback so the durable generating claim can transition.
        plan = await db.get(GraphResourcePlan, resource_plan_id)
        run = await _existing_execution(
            db, user_id=user_id, execution_key=execution_key,
        )
        if plan is not None and plan.status == "generating" and plan.artifact_id is None:
            plan.status = "failed"
            plan.failure_metadata = {
                "code": "resource_generation_failed",
                "error_type": type(exc).__name__,
                "failed_at": datetime.utcnow().isoformat(),
            }
            if run is not None:
                run.status = "failed"
                run.failure_metadata = dict(plan.failure_metadata)
            await db.commit()
        raise


async def retry_resource_generation(
    db: AsyncSession, *, user_id: int, thread_id: str, graph_id: int,
    node_id: int, resource_plan_id: int, execution_key: str,
    dispatcher: ResourceDispatcher | None = None,
) -> GeneratedItem:
    """Explicit retry action; the orchestrator itself never resets failed plans."""
    _, _, plan = await _owned_identity(
        db,
        user_id=user_id,
        graph_id=graph_id,
        node_id=node_id,
        resource_plan_id=resource_plan_id,
    )
    if plan.status != "failed" or plan.artifact_id is not None:
        raise ValueError("only a failed resource plan without artifact can be retried")
    if await _existing_execution(
        db, user_id=user_id, execution_key=execution_key,
    ):
        raise ValueError("retry execution identity already exists")
    plan.status = "planned"
    plan.execution_task_id = None
    # Failure metadata is historical until the new explicit execution claims it.
    await db.commit()
    return await materialize_resource_plan(
        db,
        user_id=user_id,
        thread_id=thread_id,
        graph_id=graph_id,
        node_id=node_id,
        resource_plan_id=resource_plan_id,
        execution_key=execution_key,
        dispatcher=dispatcher,
    )


def _sync_legacy_resources(node: GraphNode, plan: GraphResourcePlan) -> None:
    resources = list(node.resources or [])
    for row in resources:
        if row.get("resource_plan_id") in {plan.id, plan.external_id}:
            row.update({"status": plan.status, "artifact_id": plan.artifact_id})
            break
    else:
        resources.append({
            "resource_plan_id": plan.id,
            "resource_plan_external_id": plan.external_id,
            "type": plan.resource_type,
            "title": plan.purpose,
            "status": plan.status,
            "artifact_id": plan.artifact_id,
            "agent": _RESOURCE_AGENT.get(plan.resource_type),
        })
    node.resources = resources


async def dispatch_registered_resource_agent(**kwargs: Any) -> dict[str, Any]:
    """Call an existing real workspace agent and return material for GeneratedItem."""
    from config import settings

    node: GraphNode = kwargs["node"]
    plan: GraphResourcePlan = kwargs["plan"]
    agent_key = kwargs["agent_key"]
    spec = dict(plan.generation_spec or {})
    if agent_key == "code" and str(spec.get("decision") or "") == "ADVANCE_TRANSFER_CHALLENGE":
        starter = (
            "def make_counter(start=0):\n"
            "    count = start\n\n"
            "    def increment(step=1):\n"
            "        # 修复这里：让每次调用都更新这个闭包自己的 count\n"
            "        count += step\n"
            "        return count\n\n"
            "    return increment\n\n"
            "counter = make_counter(2)\n"
            "print(counter())\n"
            "print(counter(2))\n"
        )
        refs = ["python-execution-model", "python-nonlocal-statement"]
        def block(block_id: str, kind: str, text: str, **data: Any) -> dict[str, Any]:
            return {
                "contract": "darya.artifact-block.v1", "block_id": block_id, "type": kind,
                "data": {"text": text, **data}, "knowledge_refs": [str(node.external_id)],
                "source_refs": refs, "pedagogy": {}, "interaction_policy": {},
                "evidence_policy": {}, "accessibility": {"text_equivalent": text[:1000]},
            }
        why = "你已经能解释 nonlocal 的基本作用，这一步不再重复概念定义，而是在一段新代码里处理闭包状态重绑定。"
        blocks = [
            block("code-lab-heading", "HEADING", "闭包计数器迁移挑战", level=1),
            block("code-lab-objective", "CALLOUT", why),
            block("code-lab-task", "TASK", "修复 make_counter，使每个计数器保存自己的状态，并支持不同的 step。运行可反复试验；只有明确提交后才会由服务器判定。"),
            block("code-lab-source", "RUNNABLE_CODE", starter, language="python", code=starter),
            block("code-lab-checkpoint", "CHECKPOINT", "提交前检查：连续调用会累积状态；两个独立闭包不会共享状态；step 参数仍然有效。"),
            block("code-lab-reference", "SOURCE", "来源：Python Language Reference — Execution model；nonlocal statement。"),
        ]
        judge_source = (
            "\n_darya_results = []\n"
            "def _darya_check(label, action):\n"
            "    try:\n"
            "        ok = bool(action())\n"
            "    except Exception:\n"
            "        ok = False\n"
            "    _darya_results.append((label, ok))\n"
            "_darya_a = make_counter(2)\n"
            "_darya_check('state', lambda: [_darya_a(), _darya_a(3), _darya_a()] == [3, 6, 7])\n"
            "_darya_b = make_counter(10)\n"
            "_darya_check('isolation', lambda: _darya_b(2) == 12 and _darya_a() == 8)\n"
            "_darya_c = make_counter(-1)\n"
            "_darya_check('transfer', lambda: [_darya_c(4), _darya_c(-2)] == [3, 1])\n"
            "for _darya_label, _darya_ok in _darya_results:\n"
            "    print('__DARYA_JUDGE__:' + _darya_label + ':' + ('PASS' if _darya_ok else 'FAIL'))\n"
        )
        return {
            "title": "Code Lab：闭包计数器迁移挑战", "content": starter,
            "raw_data": {
                "agent": "code", "kind": "code", "language": "python", "generation_status": "generated",
                "canonical_blocks": blocks,
                "canonical_purpose": {
                    "experience": "PERSONALIZED_CODE_LAB", "learning_goal": str(node.learning_goal or node.title),
                    "knowledge_refs": [str(node.external_id)],
                    "success_criteria": ["连续调用保持闭包状态", "独立闭包状态隔离", "step 参数迁移正确"],
                },
                "canonical_personalization": {"why_this": why},
                "canonical_source_refs": refs,
                "interaction_policy": {"run_is_submission": False, "explicit_submit": True, "python_only": True},
                "evidence_policy": {"run_is_evidence": False, "eligible_event": "code_submission_judged"},
                "judge_contract_private": {
                    "contract": "darya.code-lab-judge.v1", "tests_total": 3,
                    "judge_source": judge_source, "pass_marker": "__DARYA_JUDGE__",
                },
            },
        }

    if (
        str(getattr(settings, "MODE", "mock")).lower() == "mock"
        or str(getattr(settings, "LLM_PROVIDER", "")).lower() == "mock"
    ):
        raise RuntimeError("formal resource generation cannot use mock provider")
    db: AsyncSession = kwargs["db"]
    user_id = int(kwargs["user_id"])
    topic = str(spec.get("topic") or node.title)
    instruction = str(spec.get("instruction") or plan.purpose)

    if agent_key == "lecture":
        from agents.document_agent import DocumentAgent
        profile_row = await db.scalar(select(StudentProfile).where(StudentProfile.user_id == user_id))
        profile = {
            "grade_level": getattr(profile_row, "grade_level", None),
            "preferred_language": getattr(profile_row, "preferred_language", None),
        } if profile_row else {}
        agent = DocumentAgent()
        result = await agent._real_generate({
            "topic": topic,
            "instruction": instruction,
            "detail_level": spec.get("detail_level") or "简明",
            "profile": profile,
            "messages": [],
            "user_id": user_id,
        })
        return {
            "title": str(result.get("title") or f"{topic}讲义"),
            "content": str(result.get("content") or ""),
            "raw_data": {"agent": "lecture", "generation_status": "generated"},
        }
    if agent_key == "code":
        from services.workspace_code import generate_python_code
        result = await generate_python_code(
            topic=topic,
            language=str(spec.get("language") or "python"),
            instruction=instruction,
        )
        return {
            "title": str(result.get("title") or f"{topic}代码"),
            "content": str(result.get("code") or result.get("content") or ""),
            "raw_data": {"agent": "code", "result": result},
        }
    if agent_key == "mindmap":
        from services.workspace_mindmaps import generate_workspace_mindmap
        result = await generate_workspace_mindmap(topic=topic, instruction=instruction)
        return {
            "title": str(result.get("title") or f"{topic}思维导图"),
            "content": json.dumps(result, ensure_ascii=False),
            "raw_data": {"agent": "mindmap", "tree_data": result},
        }
    if agent_key == "presentation":
        from services.workspace_presentations import generate_workspace_presentation
        delivery = "interactive_lesson" if plan.resource_type == "INTERACTIVE_CLASS" else "presentation_only"
        result = await generate_workspace_presentation(
            topic=topic,
            delivery_mode=delivery,
            learning_goal=instruction,
        )
        return {
            "title": str(result.get("title") or f"{topic}课件"),
            "content": json.dumps(result, ensure_ascii=False),
            "raw_data": {"agent": "presentation", **result},
        }
    if agent_key == "video":
        from services.workspace_video import generate_workspace_video
        result = await generate_workspace_video(
            topic=topic,
            user_id=user_id,
            duration_sec=int(spec.get("duration_sec") or 60),
            instruction=instruction,
        )
        return {
            "title": str(result.get("title") or f"{topic}教学视频"),
            "content": str(result.get("transcript") or result.get("video_url") or ""),
            "raw_data": {"agent": "video", **result},
        }
    if agent_key == "exam":
        from agents.question_agent import QuestionAgent
        agent = QuestionAgent()
        result = await agent._real_generate({
            "topic": topic,
            "count": int(spec.get("question_count") or 5),
            "difficulty": str(spec.get("difficulty") or "intermediate"),
            "question_types": list(spec.get("question_types") or []),
            "instruction": instruction,
            "profile": {},
            "user_id": user_id,
        })
        questions = result.get("questions") or result
        return {
            "title": f"{topic}练习",
            "content": json.dumps(questions, ensure_ascii=False),
            "raw_data": {"agent": "exam", "questions": questions},
        }
    raise RuntimeError(f"registered agent is not supported for materialization: {agent_key}")
