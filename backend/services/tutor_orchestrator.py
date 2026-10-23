"""Deterministic tutor state transitions over a reusable learning graph."""

from __future__ import annotations

import uuid
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ChatSession, TutorLearningEvent, TutorSession
from services.learning_graph_adapter import GraphCatalog
from services.workspace_agent_adapters import AGENT_ADAPTERS
from services.workspace_task_contracts import AgentTask, AgentType, TaskState
from services.workspace_task_store import save_task
from services.tutor_core import (
    PATH_AUTHORITY_STATUS,
    TutorContext,
    TutorDecision,
    build_formal_tutor_plan,
    resolve_formal_graph_node,
)

DEFAULT_GRAPH_CATALOG = GraphCatalog(Path(__file__).resolve().parents[1] / "data" / "knowledge_graphs")


def _goal_topic(goal: str) -> str:
    """Keep an unmatched tutor plan anchored to the user's stated subject."""
    original = " ".join(str(goal or "").split()).strip(" ，。！？,.!?")
    topic = re.sub(r"(?:我想|我要|希望|请|帮我|带我)", "", original)
    number = r"[零〇一二三四五六七八九十百两\d]+"
    topic = re.sub(rf"每天\s*{number}\s*分钟|{number}\s*天", "", topic)
    topic = re.sub(r"(?:入门|学习|掌握|了解|精通)", "", topic)
    topic = topic.strip(" ，。！？,.!?")
    return topic or original or "当前主题"


def _generic_topic_plan(goal: str, budget_minutes: int) -> list[dict[str, Any]]:
    topic = _goal_topic(goal)
    stages = (
        ("foundation", "基础概念", 20, ["lecture", "question"]),
        ("core", "核心知识", 30, ["interactive_lesson", "mindmap", "question"]),
        ("application", "实例与应用", 30, ["lecture", "code", "question"]),
        ("review", "综合练习", 25, ["question", "mindmap"]),
    )
    plan: list[dict[str, Any]] = []
    used = 0
    for node_id, label, minutes, resources in stages:
        if plan and used + minutes > budget_minutes:
            break
        plan.append({
            "id": f"generic_{node_id}",
            "title": f"{topic}·{label}",
            "estimated_minutes": minutes,
            "recommended_resources": resources,
            "source": "generic_topic_fallback",
        })
        used += minutes
    return plan


def build_tutor_chat_directive(
    session: TutorSession,
    user_message: str,
    *,
    context: TutorContext | None = None,
    decision: TutorDecision | None = None,
    voice_session_summary: str | None = None,
) -> str:
    current = next(
        (node for node in session.plan_nodes or [] if str(node.get("id")) == str(session.current_node_id)),
        {},
    )
    title = str(
        ((context.current_object or {}).get("title") if context else None)
        or ((context.node or {}).get("title") if context else None)
        or current.get("title")
        or session.goal
    )
    action = str(decision.action if decision else (session.next_action or {}).get("action") or "continue_learning")
    is_control = bool(re.fullmatch(r"\s*(好|好的|开始|开始吧|继续|继续吧|可以|行|嗯|ok|okay)\s*[。！!]?\s*", str(user_message or ""), re.I))
    directive = [
        "你现在处于 AI 导师模式，不是普通自由聊天。",
        f"用户学习目标：{session.goal}",
        f"当前学习对象：{title}",
        f"程序基于正式学习事实指定的下一动作：{action}",
        f"Path 权威状态：{PATH_AUTHORITY_STATUS}。不得推进或声称推进 Path revision。",
        "掌握度、误区和完成资格必须以程序提供的 Evaluator/Learner State 事实为准；不得依据用户自述或客户端分数自行改写。",
        "不得仅用文字声称已生成题目、讲义或其他资源；需要产物时必须由现有 Agent Task/Artifact 流程真实执行。",
    ]
    if voice_session_summary:
        directive.append(f"上一次语音学习记录：{voice_session_summary}。开场时应自然地承接这次记录，但不得据此改写掌握度。")
    if context and context.material_context.get("material_ids"):
        directive.append(f"当前会话已绑定 {len(context.material_context.get('material_ids') or [])} 份真实资料；回答应沿用系统已提供的资料片段和引用边界。")
    if decision:
        directive.append("本次决策依据：" + "、".join(decision.reason_codes))
    if action == "misconception_distinction":
        directive.append("直接围绕当前对象辨析已识别的误区：先指出容易混淆的关系或反例，再给一个很短的检查问题；不要切换知识点。")
    elif action == "reteach_scaffold":
        directive.append("直接针对当前对象换一种更直观、分步骤的讲法，并在结尾用一句可回答的小问题检查理解；不要反问用户想学什么。")
    elif is_control:
        directive.append("用户已经表示开始/继续。立即执行程序指定的下一动作，不要再次询问是否开始，也不要让用户重新选择任务。")
    else:
        directive.append("先直接回答用户当前问题；回答后自然衔接程序指定的下一动作，不得越过可信证据擅自判定完成。")
    return "\n".join(directive)


async def recent_voice_session_summary(db: AsyncSession, *, user_id: int, thread_id: str) -> str | None:
    """Return a short, evidence-bounded summary of the latest voice learning session.

    Reads only the audit event written at call end; never fabricates mastery or
    performance the formal Evaluator did not confirm.
    """
    event = (await db.execute(
        select(TutorLearningEvent)
        .where(
            TutorLearningEvent.user_id == user_id,
            TutorLearningEvent.thread_id == thread_id,
            TutorLearningEvent.event_type == "voice_session_ended",
        )
        .order_by(TutorLearningEvent.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    if event is None:
        return None
    payload = dict(event.payload or {})
    topic = str(payload.get("topic") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    parts = []
    if topic:
        parts.append(f"主题「{topic}」")
    if summary:
        parts.append(summary)
    return "；".join(parts) if parts else None


async def ensure_tutor_plan_alignment(
    db: AsyncSession, *, catalog: GraphCatalog, session: TutorSession,
) -> TutorSession:
    # A user-owned formal graph is authoritative. Static GraphCatalog is compatibility only.
    formal = await build_formal_tutor_plan(
        db, user_id=session.user_id, goal=session.goal, graph_ref=session.graph_id,
    )
    if formal is not None:
        graph, plan = formal
        graph_id = str(graph.external_id or graph.id)
        known_ids = {str(item.get("id")) for item in plan}
        current_id = str(session.current_node_id or "")
        changed = False
        if session.graph_id != graph_id:
            session.graph_id = graph_id
            changed = True
        if list(session.plan_nodes or []) != plan:
            session.plan_nodes = plan
            changed = True
        if current_id not in known_ids:
            session.current_node_id = str(plan[0]["id"])
            changed = True
        states = {str(k): dict(v) for k, v in (session.node_states or {}).items() if str(k) in known_ids}
        for index, node in enumerate(plan):
            node_id = str(node["id"])
            states.setdefault(node_id, {"status": "learning" if node_id == str(session.current_node_id) else "pending"})
            # Legacy mastery display is deliberately removed; formal Evaluator/Learner State owns mastery.
            states[node_id].pop("mastery", None)
        if session.node_states != states:
            session.node_states = states
            changed = True
        if changed:
            await _upsert_plan_card(db, session)
            await db.commit()
            await db.refresh(session)
        return session

    # No formal graph exists: keep the legacy/static compatibility plan instead of inventing a second graph.
    resolved = catalog.resolve_goal(session.goal)
    expected_graph_id = resolved.graph_id if resolved.score > 0 else "generic_topic"
    if session.graph_id == expected_graph_id:
        return session
    return await start_tutor_session(
        db,
        catalog=catalog,
        user_id=session.user_id,
        thread_id=session.thread_id,
        goal=session.goal,
        duration_days=session.duration_days,
        daily_minutes=session.daily_minutes,
    )


def tutor_plan_payload(session: TutorSession) -> dict[str, Any]:
    nodes = list(session.plan_nodes or [])
    current = next((node for node in nodes if str(node.get("id")) == str(session.current_node_id)), None)
    completed_count = sum(
        1 for value in dict(session.node_states or {}).values()
        if value.get("status") == "completed"
    )
    return {
        "kind": "tutor_plan",
        "session_id": session.session_id,
        "title": session.goal,
        "graph_id": session.graph_id,
        "status": session.status,
        "current_node_id": session.current_node_id,
        "plan_nodes": list(session.plan_nodes or []),
        "node_states": dict(session.node_states or {}),
        "progress_pct": round(float(session.progress_pct or 0), 1),
        "next_action": dict(session.next_action or {}),
        "duration_days": session.duration_days,
        "daily_minutes": session.daily_minutes,
        "start": nodes[0] if nodes else None,
        "current_stage": current,
        "completion_condition": "所有学习阶段均由可信评估确认达标",
        "summary": f"已完成 {completed_count}/{len(nodes)} 个学习阶段" if nodes else "暂无学习阶段",
        "complete": session.status == "completed",
        "workflow_revision": int(session.workflow_revision or 0),
        "learner_state_version": session.learner_state_version,
        "session_summary": dict(session.session_summary or {}),
    }


def apply_trusted_evaluation_to_tutor_session(
    session: TutorSession,
    *,
    context: TutorContext,
    decision: TutorDecision,
) -> str | None:
    """Project trusted Evaluator facts into TutorSession without changing the graph."""
    if (context.evidence_status or {}).get("state") != "trusted_evaluation":
        return None
    assessment = dict((context.evaluation or {}).get("node_assessment") or {})
    status = str(assessment.get("assessment_status") or "").upper()
    mastery = float(assessment.get("mastery_signal") or 0)
    eligibility = str(assessment.get("completion_eligibility_signal") or "").upper()
    node_id = str(session.current_node_id or "")
    states = {str(key): dict(value) for key, value in dict(session.node_states or {}).items()}
    if node_id not in states:
        return None

    state = states[node_id]
    transition = "updated"
    if status == "SECURE" and mastery >= 0.8 and eligibility == "ELIGIBLE":
        state["status"] = "completed"
        state["mastery"] = round(mastery * 100, 1)
        ordered = [str(node.get("id")) for node in list(session.plan_nodes or [])]
        index = ordered.index(node_id)
        if index + 1 < len(ordered):
            next_node_id = ordered[index + 1]
            states.setdefault(next_node_id, {})["status"] = "learning"
            session.current_node_id = next_node_id
            session.next_action = {
                "action": "start_next_node",
                "node_id": next_node_id,
                "reason": "当前阶段已由可信评估确认达标",
            }
            transition = "advanced"
        else:
            session.status = "completed"
            session.next_action = {
                "action": "plan_completed",
                "node_id": node_id,
                "reason": "全部阶段已由可信评估确认达标",
            }
            transition = "completed"
    elif status in {"EMERGING", "NEEDS_REVIEW"} or mastery < 0.55:
        state["status"] = "needs_review"
        state["mastery"] = round(mastery * 100, 1)
        session.next_action = decision.public_payload()
    else:
        state["status"] = "practicing"
        state["mastery"] = round(mastery * 100, 1)
        session.next_action = decision.public_payload()

    states[node_id] = state
    completed = sum(1 for value in states.values() if value.get("status") == "completed")
    partial = 0.0 if transition in {"advanced", "completed"} else 0.25
    session.node_states = states
    session.progress_pct = round(100 * (completed + partial) / max(1, len(states)), 1)
    return transition


def _card_content(session: TutorSession) -> str:
    if session.status == "completed":
        return f"恭喜你完成《{session.goal}》学习计划，总进度 100%。"
    current = next((node for node in session.plan_nodes or [] if node["id"] == session.current_node_id), None)
    current_title = current["title"] if current else "计划已完成"
    return f"学习计划已更新：当前学习“{current_title}”，总进度 {round(session.progress_pct or 0)}%。"


async def _upsert_plan_card(db: AsyncSession, session: TutorSession) -> None:
    chat = (await db.execute(select(ChatSession).where(
        ChatSession.user_id == session.user_id,
        ChatSession.thread_id == session.thread_id,
    ))).scalar_one_or_none()
    if chat is None:
        chat = ChatSession(user_id=session.user_id, thread_id=session.thread_id, messages=[], mode="tutor")
        db.add(chat)
    chat.mode = "tutor"
    messages = list(chat.messages or [])
    card_id = f"tutor_plan_{session.session_id}"
    card = {
        "id": card_id,
        "role": "assistant",
        "content": _card_content(session),
        "artifact": tutor_plan_payload(session),
    }
    index = next((i for i, item in enumerate(messages) if item.get("id") == card_id), None)
    if index is None:
        messages.append(card)
    else:
        messages[index] = card
    chat.messages = messages
    chat.message_count = len(messages)
    chat.last_message_at = datetime.utcnow()


async def _append_completion_message(db: AsyncSession, session: TutorSession) -> None:
    chat = (await db.execute(select(ChatSession).where(
        ChatSession.user_id == session.user_id,
        ChatSession.thread_id == session.thread_id,
    ))).scalar_one_or_none()
    if chat is None:
        return
    message_id = f"tutor_completion_{session.session_id}"
    messages = list(chat.messages or [])
    if any(item.get("id") == message_id for item in messages):
        return
    messages.append({
        "id": message_id,
        "role": "assistant",
        "content": (
            f"恭喜你完成《{session.goal}》学习计划！所有节点都已达标，"
            "总进度 100%。这次学习已经正式结束；想继续时，可以告诉我新的目标。"
        ),
        "metadata": {"kind": "tutor_completion", "tutor_session_id": session.session_id},
    })
    chat.messages = messages
    chat.message_count = len(messages)
    chat.last_message_at = datetime.utcnow()


async def start_tutor_session(
    db: AsyncSession,
    *,
    catalog: GraphCatalog,
    user_id: int,
    thread_id: str,
    goal: str,
    duration_days: int,
    daily_minutes: int,
) -> TutorSession:
    budget = max(15, int(duration_days) * int(daily_minutes))
    formal = await build_formal_tutor_plan(db, user_id=user_id, goal=goal)
    if formal is not None:
        graph, plan = formal
        graph_id = str(graph.external_id or graph.id)
    else:
        # Compatibility fallback only when the formal graph authority has no applicable graph.
        resolved = catalog.resolve_goal(goal)
        if resolved.score > 0:
            graph_id = resolved.graph_id
            plan = catalog.build_learning_path(resolved.graph_id, resolved.target_node_id, budget_minutes=budget)
            if not plan:
                plan = catalog.build_learning_path(resolved.graph_id, resolved.target_node_id)[:1]
        else:
            graph_id = "generic_topic"
            plan = _generic_topic_plan(goal, budget)
    if not plan:
        raise ValueError("tutor plan has no available learning node")
    node_states = {
        str(node["id"]): {"status": "learning" if index == 0 else "pending"}
        for index, node in enumerate(plan)
    }
    session = (await db.execute(select(TutorSession).where(
        TutorSession.user_id == user_id,
        TutorSession.thread_id == thread_id,
    ))).scalar_one_or_none()
    if session is not None and " ".join(str(session.goal or "").split()) == " ".join(str(goal or "").split()) and session.status in {
        "active", "pausing", "paused", "resuming",
    }:
        # Reopening the same goal is a durable resume/idempotent read, not a
        # destructive plan rebuild. Lifecycle routing decides whether to resume.
        return session
    if session is None:
        session = TutorSession(
            session_id=f"tutor_{uuid.uuid4()}", user_id=user_id, thread_id=thread_id,
            graph_id=graph_id, goal=goal,
        )
        db.add(session)
    session.graph_id = graph_id
    session.goal = goal
    session.duration_days = int(duration_days)
    session.daily_minutes = int(daily_minutes)
    session.status = "active"
    session.current_node_id = str(plan[0]["id"])
    session.plan_nodes = plan
    session.node_states = node_states
    session.progress_pct = 0.0
    session.next_action = {
        "action": "refresh_tutor_decision",
        "node_id": session.current_node_id,
        "reason": "等待正式 Graph / Evaluator / Learner State 决策",
        "path_authority_status": PATH_AUTHORITY_STATUS,
    }
    session.workflow_checkpoint = {}
    session.session_summary = {}
    session.learner_state_version = None
    session.workflow_revision = 0
    session.last_transition_key = None
    session.pause_reason = None
    session.paused_at = None
    session.resumed_at = None
    session.completed_at = None
    await db.flush()
    await _upsert_plan_card(db, session)
    await db.commit()
    await db.refresh(session)
    return session


async def prepare_tutor_question_task(
    db: AsyncSession, *, session: TutorSession, user_message: str,
) -> AgentTask:
    """Turn a tutor assessment action into the existing real exam pipeline."""
    if session.status == "completed":
        raise ValueError("tutor plan is already completed")
    action = str((session.next_action or {}).get("action") or "")
    counts = {"diagnostic_question": 1, "targeted_practice": 2, "assign_questions": 3}
    if action not in counts:
        raise ValueError("current tutor action is not a question assessment")
    current = next(
        (node for node in session.plan_nodes or [] if node.get("id") == session.current_node_id),
        None,
    )
    if current is None:
        raise ValueError("current tutor node is missing")

    adapter = AGENT_ADAPTERS[AgentType.EXAM]
    slots = adapter.initial_slots()
    slots.update(adapter.normalise_updates({
        "topic": str(current.get("title") or session.goal),
        "question_count": counts[action],
        "question_types": ["single_choice"],
        "difficulty": "基础" if action == "diagnostic_question" else "自适应",
        "additional_requirements": [
            "只考查当前导师学习节点",
            "用于真实掌握度评估，题干与答案必须明确",
        ],
    }))
    task = AgentTask(
        user_id=session.user_id,
        thread_id=session.thread_id,
        agent_type=AgentType.EXAM,
        slots=slots,
        state=TaskState.RUNNING,
    )
    task.confirmed_version = task.version
    task.execution_key = f"{task.task_id}:{task.version}"
    task = await save_task(db, task)

    chat = (await db.execute(select(ChatSession).where(
        ChatSession.user_id == session.user_id,
        ChatSession.thread_id == session.thread_id,
    ))).scalar_one_or_none()
    if chat is not None:
        messages = list(chat.messages or [])
        messages.append({
            "id": f"tutor_control_{uuid.uuid4()}",
            "role": "user",
            "content": str(user_message or "开始").strip() or "开始",
            "metadata": {"kind": "tutor_control", "tutor_session_id": session.session_id},
        })
        chat.messages = messages
        chat.message_count = len(messages)
        chat.last_message_at = datetime.utcnow()
        await db.commit()
    return task


async def record_tutor_event(
    db: AsyncSession,
    *,
    session: TutorSession,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> TutorSession:
    """Record compatibility/audit events without mutating mastery from client claims.

    Formal WorkspaceLearningEvent -> Evaluator/Learner State remains the mastery authority.
    """
    payload = dict(payload or {})
    node_id = str(payload.get("node_id") or session.current_node_id or "")
    known_ids = {str(node.get("id")) for node in list(session.plan_nodes or [])}
    if node_id not in known_ids:
        raise ValueError("event node is not in the active tutor plan")

    audit_payload = dict(payload)
    audit_payload["authority"] = "compatibility_audit_only"
    audit_payload["mastery_write"] = False
    audit_payload["path_revision_write"] = False
    db.add(TutorLearningEvent(
        tutor_session_id=session.session_id,
        user_id=session.user_id,
        thread_id=session.thread_id,
        node_id=node_id,
        event_type=event_type,
        payload=audit_payload,
    ))
    session.next_action = {
        "action": "refresh_tutor_decision",
        "node_id": node_id,
        "reason": "学习行为已记录为兼容审计；重新读取可信学习事实后再决定下一步",
        "path_authority_status": PATH_AUTHORITY_STATUS,
    }
    # node_states/progress are intentionally unchanged: client score is not mastery authority.
    await _upsert_plan_card(db, session)
    await db.commit()
    await db.refresh(session)
    return session
