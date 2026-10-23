"""Durable LangGraph orchestration for the formal AI Tutor.

LangGraph owns transition routing.  ``TutorSession`` remains the durable,
cross-request checkpoint and the formal Evaluator remains the only mastery and
completion authority.  This module deliberately does not reuse the legacy
``graph/workflow.py`` assistant graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TutorSession
from services.tutor_core import TutorContext, TutorDecision, decide_tutor_action, explicit_practice_decision, resolve_tutor_context
from services.tutor_orchestrator import apply_trusted_evaluation_to_tutor_session


TutorIntent = Literal[
    "continue", "reteach", "change_presentation", "practice", "pause",
    "resume", "complete_request", "question",
]


class TutorGraphState(TypedDict, total=False):
    db: AsyncSession
    session: TutorSession
    user_id: int
    message: str
    active_artifact_id: int | None
    material_ids: list[str]
    intent: TutorIntent
    context: TutorContext
    decision: TutorDecision
    evaluator_transition: str | None
    route: str
    lifecycle_message: str | None
    should_prepare_task: bool
    request_key: str


@dataclass(frozen=True)
class TutorGraphResult:
    session: TutorSession
    context: TutorContext | None
    decision: TutorDecision | None
    intent: TutorIntent
    route: str
    lifecycle_message: str | None
    should_prepare_task: bool


_PAUSE_RE = re.compile(r"(?:暂停|先停|休息一下|稍后继续|今天先到这里|今天不学了|明天再学|晚点再说|pause|stop for now)", re.I)
_RESUME_RE = re.compile(r"(?:继续昨天|恢复学习|接着学|从刚才继续|resume)", re.I)
_RETEACH_RE = re.compile(r"(?:没听懂|不理解|再讲一遍|换.{0,3}讲法|讲慢一点|reteach)", re.I)
_PRACTICE_RE = re.compile(r"(?:给我做题|出.{0,3}题|练习|测验|quiz|practice)", re.I)
_PRESENTATION_RE = re.compile(r"(?:换种方式|用图|画图|视频讲|代码演示|举例讲|更直观)", re.I)
_COMPLETE_RE = re.compile(r"(?:我会了|我学完了|完成学习|结束课程|complete)", re.I)
_CONTINUE_RE = re.compile(r"^\s*(?:开始|开始吧|继续|继续吧|下一步|下一个|好|好的|可以|行|ok|next)\s*[。！!]?\s*$", re.I)


def classify_tutor_intent(message: str) -> TutorIntent:
    text = " ".join(str(message or "").split())
    if _PAUSE_RE.search(text):
        return "pause"
    if _RESUME_RE.search(text):
        return "resume"
    if _RETEACH_RE.search(text):
        return "reteach"
    if _PRACTICE_RE.search(text):
        return "practice"
    if _PRESENTATION_RE.search(text):
        return "change_presentation"
    if _COMPLETE_RE.search(text):
        return "complete_request"
    if _CONTINUE_RE.search(text):
        return "continue"
    return "question"


def _now() -> datetime:
    return datetime.utcnow()


def _learner_state_version(context: TutorContext | None) -> str | None:
    if context is None:
        return None
    state = dict(context.learner_state or {})
    explicit = state.get("state_version") or state.get("version") or state.get("generated_at")
    if explicit:
        return str(explicit)
    stable = {
        "evaluation": context.evaluation,
        "evidence_status": context.evidence_status,
        "current_tutor_session": state.get("current_tutor_session"),
    }
    return sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:20]


def _completion_summary(session: TutorSession, context: TutorContext | None) -> dict[str, Any]:
    states = {str(key): dict(value) for key, value in dict(session.node_states or {}).items()}
    titles = {str(item.get("id")): str(item.get("title") or item.get("id")) for item in list(session.plan_nodes or [])}
    mastered = [titles.get(node_id, node_id) for node_id, value in states.items() if value.get("status") == "completed"]
    weak = [titles.get(node_id, node_id) for node_id, value in states.items() if value.get("status") in {"needs_review", "practicing"}]
    assessment = dict(((context.evaluation or {}).get("node_assessment") if context else None) or {})
    return {
        "goal": session.goal,
        "mastered": mastered,
        "weak_points": weak,
        "progress_pct": round(float(session.progress_pct or 0), 1),
        "evaluator_revision": int(((context.evidence_status or {}).get("evaluator_revision") if context else 0) or 0),
        "completion_basis": "formal_evaluator_and_goal_condition",
        "next_recommendation": "开始一个新目标，或稍后复习本次学习中的薄弱点。" if not weak else f"优先复习：{'、'.join(weak[:3])}。",
        "assessment_status": assessment.get("assessment_status"),
    }


def _checkpoint(session: TutorSession, state: TutorGraphState) -> None:
    revision = int(session.workflow_revision or 0) + 1
    session.workflow_revision = revision
    session.last_transition_key = state.get("request_key")
    context = state.get("context")
    session.learner_state_version = _learner_state_version(context) or session.learner_state_version
    completed = [key for key, value in dict(session.node_states or {}).items() if dict(value).get("status") == "completed"]
    pending = [key for key, value in dict(session.node_states or {}).items() if dict(value).get("status") != "completed"]
    session.workflow_checkpoint = {
        "schema_version": 1,
        "revision": revision,
        "lifecycle_status": session.status,
        "current_stage": session.current_node_id,
        "completed_node_ids": completed,
        "pending_node_ids": pending,
        "goal": session.goal,
        "next_action": dict(session.next_action or {}),
        "learner_state_version": session.learner_state_version,
        "progress_pct": round(float(session.progress_pct or 0), 1),
        "intent": state.get("intent"),
        "route": state.get("route"),
        "saved_at": _now().isoformat() + "Z",
    }


async def _user_goal(state: TutorGraphState) -> TutorGraphState:
    state["intent"] = classify_tutor_intent(state.get("message", ""))
    return state


def _lifecycle_route(state: TutorGraphState) -> str:
    session = state["session"]
    intent = state["intent"]
    if intent == "pause":
        return "PAUSE"
    if intent == "resume":
        return "RESUME"
    if session.status in {"paused", "pausing"}:
        return "PAUSED"
    if session.status == "completed":
        return "COMPLETE"
    return "LOAD"


async def _pause(state: TutorGraphState) -> TutorGraphState:
    session = state["session"]
    if session.status not in {"paused", "completed"}:
        session.status = "pausing"
        session.pause_reason = state.get("message") or "用户主动暂停"
        _checkpoint(session, {**state, "route": "PAUSE_REQUESTED"})
        session.status = "paused"
        session.paused_at = _now()
    state["route"] = "PAUSED"
    state["lifecycle_message"] = "学习已暂停。我保存了当前进度和下一步，回来后可以从这里继续。"
    state["should_prepare_task"] = False
    return state


async def _resume(state: TutorGraphState) -> TutorGraphState:
    session = state["session"]
    if session.status in {"paused", "pausing"}:
        session.status = "resuming"
        _checkpoint(session, {**state, "route": "RESUME_REQUESTED"})
        session.status = "active"
        session.resumed_at = _now()
        session.pause_reason = None
        state["lifecycle_message"] = "已恢复到上次停下的位置，我们接着完成下一步。"
    return state


async def _paused(state: TutorGraphState) -> TutorGraphState:
    state["route"] = "PAUSED"
    state["lifecycle_message"] = "这次学习仍处于暂停状态。说“恢复学习”即可从原位置继续。"
    state["should_prepare_task"] = False
    return state


async def _load_learner_state(state: TutorGraphState) -> TutorGraphState:
    # The normalized learner state is loaded by resolve_tutor_context; keeping
    # this explicit node makes the state-machine contract inspectable.
    return state


async def _load_context(state: TutorGraphState) -> TutorGraphState:
    session = state["session"]
    state["context"] = await resolve_tutor_context(
        state["db"], user_id=state["user_id"], thread_id=session.thread_id,
        session=session, active_artifact_id=state.get("active_artifact_id"),
        requested_material_ids=state.get("material_ids") or (),
    )
    return state


async def _evidence_retrieval(state: TutorGraphState) -> TutorGraphState:
    return state


async def _formal_evaluator(state: TutorGraphState) -> TutorGraphState:
    context = state["context"]
    provisional = decide_tutor_action(context, state.get("message", ""))
    transition = apply_trusted_evaluation_to_tutor_session(state["session"], context=context, decision=provisional)
    state["evaluator_transition"] = transition
    if transition == "completed":
        state["session"].status = "completing"
    elif transition == "advanced":
        session = state["session"]
        state["context"] = await resolve_tutor_context(
            state["db"], user_id=state["user_id"], thread_id=session.thread_id,
            session=session, requested_material_ids=state.get("material_ids") or (),
        )
    return state


async def _tutor_planner(state: TutorGraphState) -> TutorGraphState:
    decision = decide_tutor_action(state["context"], state.get("message", ""))
    # Natural control intents adjust delivery, never mastery authority.
    if state["intent"] == "practice" and decision.action not in {"collect_more_evidence", "targeted_practice"}:
        decision = explicit_practice_decision(state["context"], base=decision, user_message=state.get("message", "给我做题"))
    state["decision"] = decision
    state["session"].next_action = decision.public_payload()
    return state


def _planner_route(state: TutorGraphState) -> str:
    if state["session"].status == "completing":
        return "COMPLETE"
    action = state["decision"].action
    return {
        "collect_more_evidence": "ASSESSMENT",
        "reteach_scaffold": "RETEACH",
        "misconception_distinction": "RETEACH",
        "targeted_practice": "PRACTICE",
        "transfer_challenge": "NEXT_CONCEPT",
    }.get(action, "EXPLAIN")


def _action_node(route: str):
    async def node(state: TutorGraphState) -> TutorGraphState:
        state["route"] = route
        state["should_prepare_task"] = True
        if state.get("intent") == "complete_request":
            state["lifecycle_message"] = "完成状态需要可信评估确认；先用当前检查验证掌握情况。"
        return state
    return node


async def _complete(state: TutorGraphState) -> TutorGraphState:
    session = state["session"]
    if session.status == "completing":
        session.session_summary = _completion_summary(session, state.get("context"))
        session.completed_at = _now()
        session.status = "completed"
    state["route"] = "COMPLETE"
    state["lifecycle_message"] = "学习目标已由可信评估确认完成。"
    state["should_prepare_task"] = False
    return state


async def _save_checkpoint(state: TutorGraphState) -> TutorGraphState:
    session = state["session"]
    request_key = state.get("request_key")
    current = dict(session.workflow_checkpoint or {})
    if request_key and session.last_transition_key == request_key and current.get("lifecycle_status") == session.status:
        return state
    _checkpoint(session, state)
    await state["db"].commit()
    await state["db"].refresh(session)
    return state


def _build_graph():
    graph = StateGraph(TutorGraphState)
    graph.add_node("USER_GOAL", _user_goal)
    graph.add_node("PAUSE", _pause)
    graph.add_node("RESUME", _resume)
    graph.add_node("PAUSED", _paused)
    graph.add_node("LOAD_LEARNER_STATE", _load_learner_state)
    graph.add_node("LOAD_CONTEXT", _load_context)
    graph.add_node("EVIDENCE_RETRIEVAL", _evidence_retrieval)
    graph.add_node("FORMAL_EVALUATOR", _formal_evaluator)
    graph.add_node("TUTOR_PLANNER", _tutor_planner)
    for name in ("EXPLAIN", "LECTURE", "VISUAL", "VIDEO", "CODE", "PRACTICE", "ASSESSMENT", "RETEACH", "NEXT_CONCEPT", "SUMMARY"):
        graph.add_node(name, _action_node(name))
    graph.add_node("COMPLETE", _complete)
    graph.add_node("SAVE_CHECKPOINT", _save_checkpoint)
    graph.add_edge(START, "USER_GOAL")
    graph.add_conditional_edges("USER_GOAL", _lifecycle_route, {
        "PAUSE": "PAUSE", "RESUME": "RESUME", "PAUSED": "PAUSED", "COMPLETE": "COMPLETE", "LOAD": "LOAD_LEARNER_STATE",
    })
    graph.add_edge("PAUSE", "SAVE_CHECKPOINT")
    graph.add_edge("PAUSED", "SAVE_CHECKPOINT")
    graph.add_edge("RESUME", "LOAD_LEARNER_STATE")
    graph.add_edge("LOAD_LEARNER_STATE", "LOAD_CONTEXT")
    graph.add_edge("LOAD_CONTEXT", "EVIDENCE_RETRIEVAL")
    graph.add_edge("EVIDENCE_RETRIEVAL", "FORMAL_EVALUATOR")
    graph.add_edge("FORMAL_EVALUATOR", "TUTOR_PLANNER")
    graph.add_conditional_edges("TUTOR_PLANNER", _planner_route, {
        "EXPLAIN": "EXPLAIN", "LECTURE": "LECTURE", "VISUAL": "VISUAL", "VIDEO": "VIDEO", "CODE": "CODE",
        "PRACTICE": "PRACTICE", "ASSESSMENT": "ASSESSMENT", "RETEACH": "RETEACH",
        "NEXT_CONCEPT": "NEXT_CONCEPT", "SUMMARY": "SUMMARY", "COMPLETE": "COMPLETE",
    })
    for name in ("EXPLAIN", "LECTURE", "VISUAL", "VIDEO", "CODE", "PRACTICE", "ASSESSMENT", "RETEACH", "NEXT_CONCEPT", "SUMMARY", "COMPLETE"):
        graph.add_edge(name, "SAVE_CHECKPOINT")
    graph.add_edge("SAVE_CHECKPOINT", END)
    return graph.compile()


TUTOR_STATE_GRAPH = _build_graph()


async def execute_tutor_graph(
    db: AsyncSession, *, session: TutorSession, user_id: int, message: str = "",
    active_artifact_id: int | None = None, material_ids: list[str] | None = None,
) -> TutorGraphResult:
    request_payload = {
        "session_id": session.session_id, "status": session.status, "message": message,
        "active_artifact_id": active_artifact_id, "material_ids": sorted(material_ids or []),
        "current_node_id": session.current_node_id,
    }
    request_key = sha256(json.dumps(request_payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    result = await TUTOR_STATE_GRAPH.ainvoke({
        "db": db, "session": session, "user_id": user_id, "message": message,
        "active_artifact_id": active_artifact_id, "material_ids": material_ids or [],
        "request_key": request_key, "should_prepare_task": False,
    })
    if result.get("context") is None:
        result["context"] = await resolve_tutor_context(
            db, user_id=user_id, thread_id=session.thread_id, session=session,
            active_artifact_id=active_artifact_id, requested_material_ids=material_ids or (),
        )
    if result.get("decision") is None:
        result["decision"] = decide_tutor_action(result["context"], message)
    return TutorGraphResult(
        session=result["session"], context=result.get("context"), decision=result.get("decision"),
        intent=result["intent"], route=result.get("route", "EXPLAIN"),
        lifecycle_message=result.get("lifecycle_message"),
        should_prepare_task=bool(result.get("should_prepare_task")),
    )


def lifecycle_public_payload(session: TutorSession, message: str | None = None) -> dict[str, Any]:
    labels = {
        "active": "学习进行中", "pausing": "正在保存学习位置", "paused": "学习已暂停",
        "resuming": "正在恢复学习", "completing": "正在确认学习完成", "completed": "学习已完成",
    }
    checkpoint = dict(session.workflow_checkpoint or {})
    return {
        "state": session.status,
        "label": labels.get(session.status, "学习进行中"),
        "message": message or labels.get(session.status, "学习进行中"),
        "can_pause": session.status == "active",
        "can_resume": session.status == "paused",
        "same_session_resume": True,
        "current_stage": checkpoint.get("current_stage") or session.current_node_id,
        "progress_pct": round(float(session.progress_pct or 0), 1),
        "summary": dict(session.session_summary or {}),
    }
