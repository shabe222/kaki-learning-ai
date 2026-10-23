"""Server-owned, resumable state machine for 拾光漫游."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GeneratedItem, WorkspaceAgentTask, WorkspaceLearningEvent
from agents.adventure_planner.adapter import knowledge_plan_to_deck
from agents.adventure_planner.fallback import build_fallback_plan
from agents.adventure_planner.models import DifficultyLevel, PlannerRequest
from integrations.adventure_planner_adapter import (
    prepare_adventure_knowledge_plan,
    private_result_to_adventure_content,
)
from integrations.adventure_planner_context import (
    load_planner_recent_turns,
    resolve_context_mode,
)
from services.learner_state_service import (
    attach_private_snapshot,
    initialize_run_state,
    load_latest_topic_snapshot,
    planner_state_from_snapshot,
    record_hint_event,
    snapshot_from_payload,
    teaching_mode,
    update_after_submit,
)
from integrations.narrative_director_adapter import (
    PRIVATE_BLUEPRINT_KEY,
    PRIVATE_RUNTIME_KEY,
    PUBLIC_VIEW_KEY,
    METADATA_KEY,
    copy_narrative_to_run_slots,
    narrative_public_from_slots,
    prepare_narrative_for_plan,
)


ADVENTURE_TYPE = "harvest_adventure"
PLAN_AGENT_TYPE = "harvest_adventure_plan"
SCHEMA_VERSION = 2
REWARD_CATALOG_ID = "adventure_summary"
REWARD_DISPLAY_NAME = "拾光有成"
REWARD_EQUIP_SLOT = "journey_seal"
ADVENTURE_STATES = ("GOAL", "SCROLL", "METHOD", "TRIAL", "SUPPLY", "INTERLEAVE", "BOSS", "TREASURY")
NODE_META = (
    ("GOAL", "定下目标"),
    ("SCROLL", "微光卷轴"),
    ("METHOD", "方法选择"),
    ("TRIAL", "初次试炼"),
    ("SUPPLY", "补给营地"),
    ("INTERLEAVE", "交错挑战"),
    ("BOSS", "迷雾守关"),
    ("TREASURY", "拾光宝库"),
)
IDENTITIES = (
    ("wandering_mage", "云游符咒师"), ("ink_apprentice", "墨坛见习法师"),
    ("lamp_heir", "守灯人后裔"), ("herbal_mage", "山野药术师"),
    ("librarian", "藏书塔学徒"), ("star_apprentice", "星象台学徒"),
)
# 身份档案：每位角色的学习风格与剧情台词，随身份写入 archive，供剧情导演与对话使用。
IDENTITY_PROFILES = {
    "wandering_mage": {
        "style": "惯于把知识讲给同伴听，从复述中照见缺口",
        "trial_line": "我用我的方式把它讲出来，看看哪里还站不稳。",
        "boss_line": "最后一关，我会把三道证据串成一段完整的解释。",
    },
    "ink_apprentice": {
        "style": "习惯先闭卷回忆，再对照卷轴补上遗漏",
        "trial_line": "先不翻卷轴，我自己能想起多少。",
        "boss_line": "先凭记忆作答，再对照线索检查遗漏。",
    },
    "lamp_heir": {
        "style": "擅长把方法做成可操作的机关，靠结果校准理解",
        "trial_line": "把判断放进一次可观察的操作里试试。",
        "boss_line": "我会用行动验证每一条证据，而不是停在口头上。",
    },
    "herbal_mage": {
        "style": "偏好用例子对照，找出条件差异带来的变化",
        "trial_line": "我找一组相近例子，看看它们究竟差在哪。",
        "boss_line": "用例子和反例把边界看清楚，再给出答案。",
    },
    "librarian": {
        "style": "习惯先提取规则骨架，再逐层补充细节",
        "trial_line": "先把骨架立起来，再往里面填证据。",
        "boss_line": "逐层补全证据链，让每一道封印都站得住。",
    },
    "star_apprentice": {
        "style": "偏爱把概念放到陌生情境里检验迁移",
        "trial_line": "换个新情境想想，看它还能不能成立。",
        "boss_line": "把规则带过最后一道桥，检查它在陌生处的表现。",
    },
}
TALENTS = (
    ("rune_instinct", "符文直觉"), ("scroll_memory", "卷轴记忆"),
    ("spirit_stability", "灵息稳定"), ("ink_insight", "墨痕洞察"),
)
SCHOOLS = {
    "fire": {"name": "炎咒", "skill": "余焰"},
    "water": {"name": "水咒", "skill": "清心"},
    "wind": {"name": "风咒", "skill": "回风"},
    "wood": {"name": "木咒", "skill": "生息"},
}


def _now() -> str:
    return datetime.utcnow().isoformat()


# ===== 知识规划准备阶段 (v0.2.2) =====

async def _build_planner_request(
    db: AsyncSession,
    user_id: int,
    topic_hint: str,
    explicit_confusion: str | None = None,
    focus_adjustment: str | None = None,
    thread_id: str | None = None,
    selected_source_ids: list[str] | None = None,
    clarification_count: int = 0,
    adjustment_count: int = 0,
    draft_revision: int = 1,
    context_mode: str = "relevant_recent",
    recent_turns: list[dict[str, Any]] | None = None,
    confirmed_facts: list[dict[str, Any]] | None = None,
    learner_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded PlannerRequest from server-owned context.

    v0.2.1 loads the authenticated user's current ChatSession when the caller
    does not explicitly provide recent_turns. The planner never trusts browser-
    supplied history, never crosses thread/user boundaries, and honours
    current-item-only intent such as “只根据这道题”.
    """
    effective_context_mode = resolve_context_mode(
        context_mode,
        topic_hint,
        explicit_confusion,
        focus_adjustment,
    )
    if recent_turns is None:
        context_result = await load_planner_recent_turns(
            db,
            user_id,
            thread_id,
            topic_hint=topic_hint,
            explicit_confusion=explicit_confusion,
            focus_adjustment=focus_adjustment,
            context_mode=effective_context_mode,
        )
        recent_turns = context_result.turns
        effective_context_mode = context_result.context_mode

    payload: dict[str, Any] = {
        "topic_hint": topic_hint[:120],
        "clarification_count": clarification_count,
        "adjustment_count": adjustment_count,
        "draft_revision": draft_revision,
        "context_mode": effective_context_mode,
        "locale": "zh-CN",
    }
    if explicit_confusion:
        payload["explicit_confusion"] = explicit_confusion[:600]
    if focus_adjustment:
        payload["focus_adjustment"] = focus_adjustment[:500]
    if selected_source_ids:
        payload["selected_sources"] = [
            {
                "source_id": sid,
                "source_type": "user_selected",
                "title": sid[:80],
                "excerpt": "已由用户选择的学习材料",
            }
            for sid in selected_source_ids[:8]
        ]

    if effective_context_mode != "current_item_only" and recent_turns:
        payload["recent_turns"] = recent_turns[:6]
    elif effective_context_mode != "current_item_only" and explicit_confusion:
        payload["recent_turns"] = [
            {
                "role": "user",
                "content": explicit_confusion[:600],
                "related_to_topic": True,
                "relevance_score": 1.0,
                "recency_rank": 1,
                "turn_id": "explicit_confusion",
            }
        ]
    if explicit_confusion:
        payload["learning_evidence"] = [
            {
                "kind": "explicit_confusion",
                "summary": explicit_confusion[:300],
                "confidence": 0.95,
                "difficulty_signal": -0.3,
                "recency_rank": 1,
            }
        ]
    if confirmed_facts:
        payload["confirmed_facts"] = confirmed_facts[:12]
    if learner_state:
        payload["learner_state"] = learner_state
    return payload


async def prepare_adventure(
    db: AsyncSession,
    user_id: int,
    topic_hint: str,
    explicit_confusion: str | None = None,
    thread_id: str | None = None,
    source_ids: list[str] | None = None,
    recent_turns: list[dict[str, Any]] | None = None,
    confirmed_facts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the knowledge planning agent and store the private plan server-side.
    v0.2.1: tracks draft revision and stores only non-sensitive context statistics.
    v0.2.0 learner-state integration never reuses history in current-item-only mode.
    """
    fingerprint = hashlib.sha256(
        f"{user_id}:{topic_hint}:{thread_id or ''}".encode("utf-8")
    ).hexdigest()[:16]
    execution_key = f"plan_{fingerprint}"
    existing = (await db.execute(select(WorkspaceAgentTask).where(
        WorkspaceAgentTask.user_id == user_id,
        WorkspaceAgentTask.agent_type == PLAN_AGENT_TYPE,
        WorkspaceAgentTask.execution_key == execution_key,
        WorkspaceAgentTask.state == "prepared",
    ).limit(1))).scalar_one_or_none()
    if existing is not None:
        return _prepared_plan_payload(existing, idempotent_replay=True)

    effective_context_mode = resolve_context_mode(
        "relevant_recent", topic_hint, explicit_confusion, None
    )
    previous_learner_state = None
    if effective_context_mode != "current_item_only":
        previous_learner_state = await load_latest_topic_snapshot(db, user_id, topic_hint)
    planner_payload = await _build_planner_request(
        db, user_id, topic_hint, explicit_confusion=explicit_confusion,
        thread_id=thread_id, selected_source_ids=source_ids,
        adjustment_count=0, draft_revision=1,
        recent_turns=recent_turns, confirmed_facts=confirmed_facts,
        learner_state=planner_state_from_snapshot(previous_learner_state),
    )
    try:
        result = await prepare_adventure_knowledge_plan(planner_payload)
    except Exception:
        result = {
            "status": "ready",
            "clarification": None,
            "preview": {
                "theme": topic_hint[:64],
                "goals": [
                    {"kind": "understand", "text": "理解\u201c" + topic_hint[:40] + "\u201d的核心含义"},
                    {"kind": "distinguish", "text": "辨析\u201c" + topic_hint[:40] + "\u201d的常见误区"},
                    {"kind": "transfer", "text": "把\u201c" + topic_hint[:40] + "\u201d用于新情境"},
                ],
            },
            "metadata": {
                "generation_mode": "fallback",
                "request_fingerprint": "emergency",
                "content_fingerprint": None,
                "model": None,
                "tokens_used": 0,
                "warnings": ["planner_internal_error"],
                "diagnostics": ["adapter_exception_during_prepare"],
                "difficulty_trace": ["emergency_fallback"],
                "raw_attempts": 0,
                "draft_revision": 1,
                "adjustment_count": 0,
                "plan_state": "draft",
            },
        }
        result["_private_result"] = None

    plan_id = f"advplan_{uuid.uuid4().hex}"
    task = WorkspaceAgentTask(
        task_id=plan_id,
        user_id=user_id,
        thread_id=(thread_id or f"adventure_{uuid.uuid4().hex}")[:100],
        agent_type=PLAN_AGENT_TYPE,
        state="prepared",
        execution_key=execution_key,
        slots={
            "schema_version": SCHEMA_VERSION,
            "plan_schema_version": "1.1",
            "status": result["status"],
            "preview": result["preview"],
            "clarification": result["clarification"],
            "metadata": result["metadata"],
            "_private_result": result.get("_private_result"),
            "topic_hint": topic_hint,
            "explicit_confusion": explicit_confusion,
            "source_ids": source_ids or [],
            "confirmed_facts": confirmed_facts or [],
            "context_mode": planner_payload.get("context_mode", "relevant_recent"),
            "context_turn_count": len(planner_payload.get("recent_turns") or []),
            "context_char_count": sum(
                len(str(turn.get("content") or "")) for turn in (planner_payload.get("recent_turns") or [])
            ),
            "focus_adjustment_history": [],
            "draft_revision": 1,
            "adjustment_count": 0,
            "plan_state": "draft",
            "learner_state": previous_learner_state.model_dump(mode="json") if previous_learner_state else None,
        },
    )
    db.add(task)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(WorkspaceAgentTask).where(
            WorkspaceAgentTask.user_id == user_id,
            WorkspaceAgentTask.agent_type == PLAN_AGENT_TYPE,
            WorkspaceAgentTask.execution_key == execution_key,
            WorkspaceAgentTask.state == "prepared",
        ).limit(1))).scalar_one_or_none()
        if existing is not None:
            return _prepared_plan_payload(existing, idempotent_replay=True)
        raise
    await db.refresh(task)

    meta = result["metadata"]
    narrative_public = None
    if result["status"] == "ready" and result.get("_private_result"):
        try:
            narrative_bundle = await prepare_narrative_for_plan(
                result["_private_result"],
                teaching_mode=teaching_mode(previous_learner_state),
                plan_id=plan_id,
                plan_revision=1,
                focus_adjustment=None,
            )
            slots = dict(task.slots or {})
            for key in (PRIVATE_BLUEPRINT_KEY, PRIVATE_RUNTIME_KEY, PUBLIC_VIEW_KEY, METADATA_KEY):
                slots[key] = narrative_bundle.get(key)
            task.slots = slots
            await db.commit()
            narrative_public = narrative_public_from_slots(slots)
        except Exception:
            narrative_public = None
    return _prepared_plan_payload(task)


def _prepared_plan_payload(
    task: WorkspaceAgentTask,
    *,
    idempotent_replay: bool = False,
) -> dict[str, Any]:
    """Project one prepared draft consistently for initial and replay responses."""
    slots = dict(task.slots or {})
    meta = dict(slots.get("metadata") or {})
    return {
        "plan_id": task.task_id,
        "status": slots.get("status"),
        "preview": slots.get("preview"),
        "clarification": slots.get("clarification"),
        "draft_revision": slots.get("draft_revision", meta.get("draft_revision", 1)),
        "adjustment_count": slots.get("adjustment_count", meta.get("adjustment_count", 0)),
        "fallback_used": meta.get("generation_mode") == "fallback",
        "warnings": meta.get("warnings") or [],
        "narrative": narrative_public_from_slots(slots),
        "idempotent_replay": idempotent_replay,
    }


def _safe_nonnegative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _stored_adjustment_count(slots: dict[str, Any]) -> int:
    """Recover the authoritative adjustment count across v0.1-v0.2 storage shapes."""

    history = list(slots.get("focus_adjustment_history") or [])
    metadata = dict(slots.get("metadata") or {})
    revision_count = max(0, _safe_nonnegative_int(slots.get("draft_revision")) - 1)
    metadata_revision_count = max(0, _safe_nonnegative_int(metadata.get("draft_revision")) - 1)
    return max(
        len(history),
        _safe_nonnegative_int(slots.get("adjustment_count")),
        _safe_nonnegative_int(metadata.get("adjustment_count")),
        revision_count,
        metadata_revision_count,
    )


async def adjust_prepared_adventure(
    db: AsyncSession,
    user_id: int,
    plan_id: str,
    focus_adjustment: str,
) -> dict[str, Any]:
    """Adjust a draft plan with row locking and a storage-shape-safe two-adjustment limit."""
    statement = (
        select(WorkspaceAgentTask)
        .where(
            WorkspaceAgentTask.task_id == plan_id,
            WorkspaceAgentTask.agent_type == PLAN_AGENT_TYPE,
            WorkspaceAgentTask.state.in_(("prepared", "draft")),
        )
        .with_for_update()
    )
    task = (await db.execute(statement)).scalar_one_or_none()
    if task is None:
        raise LookupError("准备计划不存在或已确认")
    if task.user_id != user_id:
        raise PermissionError("无权访问此准备计划")

    slots = dict(task.slots or {})
    if str(slots.get("plan_state") or "draft") != "draft":
        raise ValueError("该计划已确认或已取消，不能继续调整")

    history = list(slots.get("focus_adjustment_history") or [])
    current_adjustment_count = _stored_adjustment_count(slots)
    if current_adjustment_count >= 2:
        raise ValueError("调整次数已达上限（最多 2 次）")

    topic_hint = slots.get("topic_hint", "")
    explicit_confusion = slots.get("explicit_confusion")
    source_ids = list(slots.get("source_ids") or [])
    confirmed_facts = list(slots.get("confirmed_facts") or [])
    new_adjustment_count = current_adjustment_count + 1

    planner_payload = await _build_planner_request(
        db, user_id, topic_hint, explicit_confusion=explicit_confusion,
        focus_adjustment=focus_adjustment, thread_id=task.thread_id,
        selected_source_ids=source_ids,
        clarification_count=min(current_adjustment_count, 1),
        adjustment_count=new_adjustment_count,
        draft_revision=new_adjustment_count + 1,
        context_mode=str(slots.get("context_mode") or "relevant_recent"),
        confirmed_facts=confirmed_facts,
        learner_state=planner_state_from_snapshot(snapshot_from_payload(slots.get("learner_state"))),
    )
    try:
        result = await prepare_adventure_knowledge_plan(planner_payload)
    except Exception:
        return {
            "plan_id": plan_id,
            "status": slots.get("status", "ready"),
            "preview": slots.get("preview"),
            "clarification": slots.get("clarification"),
            "draft_revision": slots.get("draft_revision", current_adjustment_count + 1),
            "adjustment_count": current_adjustment_count,
            "fallback_used": True,
            "warnings": ["planner_internal_error"],
        }

    history.append(focus_adjustment)
    meta = result["metadata"]
    slots["status"] = result["status"]
    slots["preview"] = result["preview"]
    slots["clarification"] = result["clarification"]
    slots["metadata"] = meta
    slots["_private_result"] = result.get("_private_result")
    slots["focus_adjustment_history"] = history
    slots["focus_adjustment"] = focus_adjustment
    slots["context_mode"] = planner_payload.get("context_mode", slots.get("context_mode", "relevant_recent"))
    slots["context_turn_count"] = len(planner_payload.get("recent_turns") or [])
    slots["context_char_count"] = sum(
        len(str(turn.get("content") or "")) for turn in (planner_payload.get("recent_turns") or [])
    )
    slots["draft_revision"] = new_adjustment_count + 1
    slots["adjustment_count"] = new_adjustment_count
    meta["draft_revision"] = new_adjustment_count + 1
    meta["adjustment_count"] = new_adjustment_count
    meta["plan_state"] = "draft"
    slots["metadata"] = meta

    # Narrative: regenerate on new revision; delete old story on failure
    narrative_public = None
    try:
        if result["status"] == "ready" and result.get("_private_result"):
            narrative_bundle = await prepare_narrative_for_plan(
                result["_private_result"],
                teaching_mode=teaching_mode(snapshot_from_payload(slots.get("learner_state"))),
                plan_id=plan_id,
                plan_revision=new_adjustment_count + 1,
                focus_adjustment=focus_adjustment,
            )
            for key in (PRIVATE_BLUEPRINT_KEY, PRIVATE_RUNTIME_KEY, PUBLIC_VIEW_KEY, METADATA_KEY):
                slots[key] = narrative_bundle.get(key)
            narrative_public = narrative_public_from_slots(slots)
    except Exception:
        for key in (PRIVATE_BLUEPRINT_KEY, PRIVATE_RUNTIME_KEY, PUBLIC_VIEW_KEY, METADATA_KEY):
            slots.pop(key, None)
        narrative_public = None

    task.slots = slots
    task.state = "prepared"
    task.execution_key = f"plan_{meta.get('content_fingerprint', hashlib.sha256(str(history).encode()).hexdigest()[:16])}"
    await db.commit()
    await db.refresh(task)

    return {
        "plan_id": plan_id,
        "status": result["status"],
        "preview": result["preview"],
        "clarification": result["clarification"],
        "draft_revision": new_adjustment_count + 1,
        "adjustment_count": new_adjustment_count,
        "fallback_used": meta.get("generation_mode") == "fallback",
        "warnings": meta.get("warnings") or [],
        "narrative": narrative_public,
    }


def _content_from_private_plan(private_result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert a private plan result into the legacy _content() format, if available."""
    if private_result is None:
        return None
    try:
        return private_result_to_adventure_content(private_result)
    except Exception:
        return None


def _stable_number(seed: str, key: str, modulo: int) -> int:
    digest = hashlib.sha256(f"{seed}:{key}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % modulo


def _normalise(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _topic_deck(topic: str) -> dict[str, Any]:
    """内容由独立游戏智能体（game_agent）产出：精选卡优先，其余领域感知生成。"""
    from game_agent.brain import _topic_deck as _agent_deck
    return _agent_deck(topic)


def teaching_check(topic: str) -> dict[str, Any]:
    """教学确认题由独立游戏智能体产出（与 game_agent 一致）。"""
    from game_agent.brain import teaching_check as _agent_teaching_check
    return _agent_teaching_check(topic)


def _content(topic: str) -> dict[str, Any]:
    deck = _topic_deck(topic)
    # 教学卡兜底：精选卡（机器学习/Python）没有 concept/misconception 字段时用规则生成。
    concept = deck.get("concept") or {
        "name": topic[:36],
        "explanation": deck.get("rule", ""),
        "applicability": "",
    }
    misconception = deck.get("misconception") or {
        "claim": "",
        "correction": "",
        "cue": "",
    }
    return {
        "title": f"{topic[:36]} · 拾光漫游",
        "subtitle": "用十分钟，完成一次有始有终的学习小旅程。",
        "topic": topic,
        "source_note": deck["source_note"],
        "scroll": {"rule": deck["rule"], "example": deck["example"], "teaching_check": teaching_check(topic)},
        "routes": deck["understanding"] | {"practice": deck["practice"]},
        "understanding": deck["understanding"],
        "practice": deck["practice"],
        "remedy": deck["remedy"],
        "boss": deck["boss"],
        # 教学卡：卷轴讲解、答错反馈与结尾复述使用
        "concept": concept,
        "misconception": misconception,
        "teaching_strategy": list(deck.get("teaching_strategy") or []),
        "completion_summary": deck.get("completion_summary") or "",
        "next_step": deck.get("next_step") or "",
    }


def _public_content(content: dict[str, Any]) -> dict[str, Any]:
    # 答案与答题引导不下发到前端 content；答错后由服务端组装进 feedback。
    hidden = {"correct_option", "accepted_groups", "answer_explanation", "scaffold", "second_attempt", "wrong_guidance"}
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in hidden}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    return clean(content)


def _teaching_feedback(current_task: dict[str, Any], content: dict[str, Any], message: str, attempt: int = 1) -> dict[str, Any]:
    """组装教学化答错反馈：三问阶梯，越错教得越透。

    attempt=1  -> 给方向（hint）
    attempt=2  -> 拆步骤讲解（wrong_guidance/second_attempt + scaffold）
    attempt>=3 -> 完整答案 + 知识点 + 误区纠正
    """
    concept = content.get("concept") or {}
    misconception = content.get("misconception") or {}
    hint = current_task.get("hint") or ""
    wrong_guidance = current_task.get("wrong_guidance") or ""
    rationale = current_task.get("rationale") or ""
    explanation = current_task.get("answer_explanation") or ""
    second_attempt = current_task.get("second_attempt") or ""
    scaffold = list(current_task.get("scaffold") or [])
    knowledge = concept.get("explanation") or ""
    correction = misconception.get("correction") or ""

    ladder = min(max(int(attempt or 1), 1), 3)
    guidance = hint or wrong_guidance
    if ladder >= 2 and wrong_guidance:
        guidance = wrong_guidance
    if ladder >= 2 and second_attempt:
        guidance = second_attempt
    steps = scaffold if ladder >= 2 else []

    return {
        "kind": "needs_review",
        "message": message,
        "wrong_guidance": guidance,
        "rationale": rationale,
        "explanation": explanation if ladder >= 3 else "",
        "second_attempt": second_attempt if ladder >= 2 else "",
        "scaffold": steps,
        "knowledge": knowledge if ladder >= 3 else "",
        "correction": correction if ladder >= 3 else "",
        "ladder": ladder,
    }


def _initial_node_states() -> dict[str, str]:
    return {state: ("active" if state == "GOAL" else "locked") for state, _ in NODE_META}


def _reward_reason(slots: dict[str, Any]) -> str:
    evidence = list(slots.get("timeline") or [])
    recovered = any(
        event.get("task_type") == "learning_check" and event.get("decision") == "needs_review"
        for event in evidence
    )
    if recovered:
        return "你完成了解释、回忆与迁移三重验证，并在证据不足后补全了关键一步。"
    return "你独立完成了解释、回忆与迁移三重验证，让丰穗谷重新亮起。"


def _reward_candidate(task: WorkspaceAgentTask, slots: dict[str, Any]) -> dict[str, Any]:
    return {
        "catalog_id": REWARD_CATALOG_ID,
        "artifact_id": task.artifact_id,
        "display_name": REWARD_DISPLAY_NAME,
        "description": "完成一次有始有终的拾光漫游后，收入宝库的旅程纪要印记。",
        "acquired_reason": _reward_reason(slots),
        "category": "journey",
        "equip_slot": REWARD_EQUIP_SLOT,
        "collected": bool(task.artifact_id),
        "equipped": False,
    }


def _snapshot(task: WorkspaceAgentTask, feedback: dict | None = None) -> dict[str, Any]:
    slots = dict(task.slots or {})
    snapshot = {
        "run_id": task.task_id,
        "task_type": ADVENTURE_TYPE,
        "schema_version": slots.get("schema_version", 1),
        "state": task.state,
        "nodes": [{"id": state, "title": title, "status": (slots.get("node_states") or {}).get(state, "locked")} for state, title in NODE_META],
        "archive": dict(slots.get("archive") or {}),
        "content": _public_content(dict(slots.get("content") or {})),
        "feedback": feedback if feedback is not None else dict(slots.get("feedback") or {}),
        "timeline": list(slots.get("timeline") or []),
        "artifact_id": task.artifact_id,
        "narrative": narrative_public_from_slots(slots),
    }
    if task.state in {"TREASURY", "completed"} or task.artifact_id:
        snapshot["reward"] = _reward_candidate(task, slots)
    return snapshot


def _reward_metadata(item: GeneratedItem) -> dict[str, Any]:
    raw_data = dict(item.raw_data or {})
    metadata = dict(raw_data.get("_adventure_reward") or {})
    evidence = list(raw_data.get("evidence") or [])
    recovered = any(
        event.get("task_type") == "learning_check" and event.get("decision") == "needs_review"
        for event in evidence
    )
    default_reason = (
        "你完成了解释、回忆与迁移三重验证，并在证据不足后补全了关键一步。"
        if recovered
        else "你独立完成了解释、回忆与迁移三重验证，让丰穗谷重新亮起。"
    )
    return {
        "catalog_id": metadata.get("catalog_id") or REWARD_CATALOG_ID,
        "display_name": metadata.get("display_name") or REWARD_DISPLAY_NAME,
        "description": metadata.get("description") or "完成一次有始有终的拾光漫游后，收入宝库的旅程纪要印记。",
        "acquired_reason": metadata.get("acquired_reason") or default_reason,
        "equip_slot": metadata.get("equip_slot") or REWARD_EQUIP_SLOT,
        "equipped": bool(metadata.get("equipped")),
        "equipped_at": metadata.get("equipped_at"),
        "collected_at": metadata.get("collected_at") or (item.created_at.isoformat() if item.created_at else None),
    }


def _serialize_reward(item: GeneratedItem) -> dict[str, Any]:
    raw_data = dict(item.raw_data or {})
    metadata = _reward_metadata(item)
    return {
        **metadata,
        "artifact_id": item.id,
        "title": item.title,
        "topic": raw_data.get("topic"),
        "run_id": raw_data.get("run_id"),
        "collected": True,
    }


def _set_reward_metadata(item: GeneratedItem, **updates: Any) -> None:
    raw_data = dict(item.raw_data or {})
    metadata = _reward_metadata(item)
    metadata.update(updates)
    raw_data["_adventure_reward"] = metadata
    item.raw_data = raw_data


async def _owned_rewards(db: AsyncSession, user_id: int) -> list[GeneratedItem]:
    return list((await db.execute(
        select(GeneratedItem).where(
            GeneratedItem.user_id == user_id,
            GeneratedItem.type == REWARD_CATALOG_ID,
        ).order_by(desc(GeneratedItem.id))
    )).scalars().all())


async def get_adventure_rewards(db: AsyncSession, user_id: int) -> dict[str, Any]:
    items = [_serialize_reward(item) for item in await _owned_rewards(db, user_id)]
    equipped = next((item for item in items if item["equipped"]), None)
    return {
        "catalog_count": 1,
        "owned_count": len(items),
        "equip_slot": REWARD_EQUIP_SLOT,
        "equipped": equipped,
        "items": items,
    }


async def _owned_reward(db: AsyncSession, user_id: int, artifact_id: int) -> GeneratedItem:
    item = (await db.execute(
        select(GeneratedItem).where(GeneratedItem.id == artifact_id)
    )).scalar_one_or_none()
    if item is None:
        raise LookupError("奖励不存在")
    if item.user_id != user_id or item.type != REWARD_CATALOG_ID:
        raise PermissionError("无权操作这枚旅印")
    return item


async def equip_adventure_reward(
    db: AsyncSession,
    user_id: int,
    artifact_id: int,
    *,
    replace_confirmed: bool = False,
) -> dict[str, Any]:
    candidate = await _owned_reward(db, user_id, artifact_id)
    owned = await _owned_rewards(db, user_id)
    current = next(
        (item for item in owned if item.id != candidate.id and _reward_metadata(item)["equipped"]),
        None,
    )
    if current is not None and not replace_confirmed:
        collection = await get_adventure_rewards(db, user_id)
        return {
            **collection,
            "status": "replace_required",
            "current_equipped": _serialize_reward(current),
            "candidate": _serialize_reward(candidate),
        }
    equipped_at = _now()
    for item in owned:
        _set_reward_metadata(
            item,
            equipped=item.id == candidate.id,
            equipped_at=equipped_at if item.id == candidate.id else None,
        )
    await db.commit()
    collection = await get_adventure_rewards(db, user_id)
    return {**collection, "status": "equipped"}


async def unequip_adventure_reward(
    db: AsyncSession,
    user_id: int,
    artifact_id: int,
) -> dict[str, Any]:
    item = await _owned_reward(db, user_id, artifact_id)
    _set_reward_metadata(item, equipped=False, equipped_at=None)
    await db.commit()
    collection = await get_adventure_rewards(db, user_id)
    return {**collection, "status": "unequipped"}


async def _owned_task(db: AsyncSession, user_id: int, run_id: str) -> WorkspaceAgentTask:
    task = (await db.execute(
        select(WorkspaceAgentTask)
        .where(WorkspaceAgentTask.task_id == run_id)
        .with_for_update()
    )).scalar_one_or_none()
    if task is None:
        raise LookupError("拾光漫游记录不存在")
    if task.user_id != user_id or task.agent_type != ADVENTURE_TYPE:
        raise PermissionError("无权访问这次拾光漫游")
    return task


def _group_hit(group: list[str], normal: str) -> bool:
    """判断回答是否命中一组可接受词。

    长词（如完整主题串）允许 2 字滑动片段命中：主题组只需确认回答
    "在谈这个主题"，而不必完整复述主题串；短词仍是整词包含匹配。
    真正把关回答质量的是语义证据组（accepted_groups 的第二组起）。
    """
    for term in group:
        key = _normalise(term)
        if not key:
            continue
        if key in normal:
            return True
        if len(key) >= 5:
            for start in range(0, len(key) - 1):
                if key[start:start + 2] in normal:
                    return True
    return False


def _evaluate(answer: Any, task: dict[str, Any]) -> tuple[bool, float]:
    """判定一次作答是否构成有效学习证据。

    v2.1 加固：简答题不再接受"含主题词即判对"的宽松匹配——
    1) 去空白后不足 4 个字符的回答不算证据；
    2) 只有单个 accepted_groups（如仅主题词）时，要求回答不是该组词的纯回声；
    3) 多组时维持"至少命中两组"的证据门槛，防止只重复题干词就通过；
    4) 长词组按 3 字片段匹配，长主题不必被完整复述。
    """
    normal = _normalise(answer)
    if task.get("type") == "multiple_choice":
        options = list(task.get("options") or [])
        expected = options[int(task.get("correct_option", 0))] if options else ""
        correct = normal in {_normalise(expected), _normalise(task.get("correct_option"))}
        return correct, 1.0 if correct else 0.0
    groups = list(task.get("accepted_groups") or [])
    if len(normal) < 4:
        return False, 0.0
    if not groups:
        return True, 1.0
    matched = sum(1 for group in groups if _group_hit(group, normal))
    if len(groups) == 1:
        is_echo = any(normal == _normalise(term) for term in groups[0])
        if matched < 1 or is_echo:
            return False, 0.0
        return True, 1.0
    threshold = min(2, len(groups))
    return matched >= threshold, matched / max(1, len(groups))


def _record(
    slots: dict[str, Any], node: str, response: Any, score: float, decision: str,
    *, hint: str | None = None, boss_stage: int | None = None,
) -> None:
    attempts = dict(slots.get("attempts") or {})
    attempts[node] = int(attempts.get(node, 0)) + 1
    slots["attempts"] = attempts
    timeline = list(slots.get("timeline") or [])
    timeline.append({
        "node": node, "boss_stage": boss_stage, "task_type": "learning_check", "response": str(response or "")[:500],
        "score": round(score, 2), "decision": decision, "attempts": attempts[node],
        "hint": hint, "result": "passed" if decision == "pass" else "needs_review",
        "evidence": f"{node.lower()}_attempt_{attempts[node]}", "duration_seconds": 0, "at": _now(),
    })
    slots["timeline"] = timeline


def _enter(task: WorkspaceAgentTask, slots: dict[str, Any], state: str, note: str, *, complete_previous: bool = True) -> None:
    states = dict(slots.get("node_states") or _initial_node_states())
    if complete_previous and task.state in states and states[task.state] != "needs_review":
        states[task.state] = "completed"
    states[state] = "active"
    slots["node_states"] = states
    task.state = state
    archive = dict(slots.get("archive") or {})
    archive["current_node"] = state.lower()
    slots["archive"] = archive
    slots["timeline"] = list(slots.get("timeline") or []) + [{"node": state, "decision": "enter", "result": note, "at": _now()}]


async def get_current_adventure(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    task = (await db.execute(select(WorkspaceAgentTask).where(
        WorkspaceAgentTask.user_id == user_id,
        WorkspaceAgentTask.agent_type == ADVENTURE_TYPE,
        WorkspaceAgentTask.state.notin_(("completed", "cancelled")),
    ).order_by(desc(WorkspaceAgentTask.id)).limit(1))).scalar_one_or_none()
    return _snapshot(task) if task else None


async def get_current_prepared_plan(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    task = (await db.execute(select(WorkspaceAgentTask).where(
        WorkspaceAgentTask.user_id == user_id,
        WorkspaceAgentTask.agent_type == PLAN_AGENT_TYPE,
        WorkspaceAgentTask.state == "prepared",
    ).order_by(desc(WorkspaceAgentTask.id)).limit(1))).scalar_one_or_none()
    if task is None:
        return None
    return _prepared_plan_payload(task)


async def start_adventure(
    db: AsyncSession, user_id: int, topic: str, magic_school: str, source_ids: list[str],
    thread_id: str | None = None, source_type: str | None = None, force_new: bool = False,
    prepared_plan_id: str | None = None, identity_id: str | None = None, talent_id: str | None = None,
) -> dict[str, Any]:
    active = (await db.execute(select(WorkspaceAgentTask).where(
        WorkspaceAgentTask.user_id == user_id,
        WorkspaceAgentTask.agent_type == ADVENTURE_TYPE,
        WorkspaceAgentTask.state.notin_(("completed", "cancelled")),
    ).order_by(desc(WorkspaceAgentTask.id)).limit(1))).scalar_one_or_none()
    if active is not None and not force_new:
        if int((active.slots or {}).get("schema_version", 1)) >= SCHEMA_VERSION:
            return _snapshot(active)
    if active is not None:
        active.state = "cancelled"
    topic = str(topic or "").strip()

    # --- v0.2.0: 使用知识规划智能体生成的计划，支持冻结 ---
    content = None
    initial_learner_state = None
    prepared_revision = None
    run_context_mode = resolve_context_mode("relevant_recent", topic, None, None)
    if prepared_plan_id:
        plan_task = (await db.execute(select(WorkspaceAgentTask).where(
            WorkspaceAgentTask.task_id == prepared_plan_id,
            WorkspaceAgentTask.user_id == user_id,
            WorkspaceAgentTask.agent_type == PLAN_AGENT_TYPE,
        ))).scalar_one_or_none()
        if plan_task is None:
            raise ValueError("准备计划不存在")
        if plan_task.state not in ("prepared", "draft"):
            raise ValueError("该计划已确认或已取消，无法再次启动")
        plan_slots = dict(plan_task.slots or {})
        private_result = plan_slots.get("_private_result")
        initial_learner_state = snapshot_from_payload(plan_slots.get("learner_state"))
        prepared_revision = plan_slots.get("draft_revision")
        run_context_mode = str(plan_slots.get("context_mode") or "relevant_recent")
        content = _content_from_private_plan(private_result)
        if content is not None:
            topic = content.get("topic", topic)
            # Freeze: save locked_plan snapshot and mark confirmed
            plan_slots["locked_plan"] = private_result
            plan_slots["confirmed_at"] = _now()
            plan_slots["plan_state"] = "locked"
            plan_task.slots = plan_slots
            plan_task.state = "confirmed"
        else:
            # Prepared plan had no valid private result; fall back to _topic_deck
            pass

    if topic and len(topic) < 2:
        raise ValueError("请先写下本次漫游主题")
    if magic_school not in SCHOOLS:
        raise ValueError("请选择有效的学习法术")
    run_id = f"adv_{uuid.uuid4().hex}"
    seed = uuid.uuid4().hex
    # 身份与天赋：玩家在准备界面主动选择；未传或无效时随机兜底。
    identity = next((item for item in IDENTITIES if item[0] == identity_id), None) or IDENTITIES[_stable_number(seed, "identity", len(IDENTITIES))]
    talent = next((item for item in TALENTS if item[0] == talent_id), None) or TALENTS[_stable_number(seed, "talent", len(TALENTS))]
    archive = {
        "run_id": run_id, "run_seed": seed, "topic": topic, "source_ids": list(source_ids or []),
        "source_type": source_type or ("selected_context" if source_ids else "current_topic"),
        "identity_id": identity[0], "identity_name": identity[1], "talent_id": talent[0], "talent_name": talent[1],
        "identity_style": (IDENTITY_PROFILES.get(identity[0]) or {}).get("style", ""),
        "identity_trial_line": (IDENTITY_PROFILES.get(identity[0]) or {}).get("trial_line", ""),
        "identity_boss_line": (IDENTITY_PROFILES.get(identity[0]) or {}).get("boss_line", ""),
        "magic_school": magic_school, "magic_school_name": SCHOOLS[magic_school]["name"],
        "selected_strategy": None, "selected_method": None, "branch_choice": None, "current_node": "goal", "boss_stage": 0,
        "boss_fog": 100, "remedial_required": False, "status": "playing", "started_at": _now(),
    }
    if initial_learner_state is None and run_context_mode != "current_item_only":
        initial_learner_state = await load_latest_topic_snapshot(db, user_id, topic)
    final_content = content or _content(topic)
    run_slots = {"schema_version": SCHEMA_VERSION, "archive": archive, "content": final_content,
                 "node_states": _initial_node_states(), "attempts": {}, "feedback": {},
                 "timeline": [{"node": "GOAL", "decision": "enter", "result": "目标已确认", "at": _now()}],
                 "explicit_confusion": None, "context_mode": run_context_mode}
    if prepared_plan_id:
        run_slots["explicit_confusion"] = plan_slots.get("explicit_confusion")
        run_slots["context_mode"] = plan_slots.get("context_mode", "relevant_recent")
        # Narrative v0.2.0: freeze prepared narrative into run slots (no model call)
        copy_narrative_to_run_slots(plan_slots, run_slots)
    initialize_run_state(
        run_slots, initial_learner_state,
        plan_id=prepared_plan_id, draft_revision=prepared_revision,
    )
    task = WorkspaceAgentTask(
        task_id=run_id, user_id=user_id, thread_id=(thread_id or f"adventure_{uuid.uuid4().hex}")[:100],
        agent_type=ADVENTURE_TYPE, state="GOAL", execution_key=run_id,
        slots=run_slots,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    if prepared_plan_id and content is not None:
        await db.commit()
    return _snapshot(task)


async def _claim(db: AsyncSession, task: WorkspaceAgentTask, slots: dict[str, Any]) -> dict[str, Any]:
    if task.artifact_id:
        return _snapshot(task)
    archive = dict(slots.get("archive") or {})
    node_states = dict(slots.get("node_states") or {})
    node_states["TREASURY"] = "completed"
    slots["node_states"] = node_states
    archive.update({"status": "completed", "current_node": "completed", "completed_at": _now(), "boss_fog": 0})
    slots["archive"] = archive
    summary = {
        "run_id": task.task_id, "topic": archive.get("topic"), "strategy": archive.get("selected_strategy"),
        "branch": archive.get("branch_choice"), "node_states": node_states,
        "evidence": list(slots.get("timeline") or []), "next_review": "建议在 24 小时内用 3 分钟回忆本次核心规则。",
    }
    summary["_adventure_reward"] = {
        "catalog_id": REWARD_CATALOG_ID,
        "display_name": REWARD_DISPLAY_NAME,
        "description": "完成一次有始有终的拾光漫游后，收入宝库的旅程纪要印记。",
        "acquired_reason": _reward_reason(slots),
        "equip_slot": REWARD_EQUIP_SLOT,
        "equipped": False,
        "equipped_at": None,
        "collected_at": _now(),
    }
    public_summary = dict(summary)
    private_raw_data = dict(summary)
    attach_private_snapshot(private_raw_data, slots)
    item = GeneratedItem(
        user_id=task.user_id, type="adventure_summary", title=f"{archive.get('topic', '学习主题')} · 拾光漫游纪要",
        chapter="八节点学习旅程纪要", content=json.dumps(public_summary, ensure_ascii=False), raw_data=private_raw_data,
    )
    db.add(item)
    await db.flush()
    task.artifact_id = item.id
    task.state = "completed"
    task.slots = slots
    for state, title in NODE_META:
        db.add(WorkspaceLearningEvent(
            user_id=task.user_id, thread_id=task.thread_id, artifact_id=item.id,
            event_type="adventure_node_completed",
            payload={"run_id": task.task_id, "node_id": state, "node_title": title, "status": node_states.get(state),
                     "evidence": [event for event in summary["evidence"] if event.get("node") == state]},
        ))
    db.add(WorkspaceLearningEvent(
        user_id=task.user_id, thread_id=task.thread_id, artifact_id=item.id,
        event_type="adventure_completed", payload={"run_id": task.task_id, "topic": archive.get("topic"), "next_review": summary["next_review"]},
    ))
    await db.commit()
    await db.refresh(task)
    return _snapshot(task, {"kind": "success", "message": "拾光漫游纪要已保存到学习产物。"})


async def advance_adventure(db: AsyncSession, user_id: int, run_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    task = await _owned_task(db, user_id, run_id)
    slots = dict(task.slots or {})
    if task.state == "completed":
        if action == "claim":
            return _snapshot(task)
        raise ValueError("本次拾光漫游已经完成")
    archive = dict(slots.get("archive") or {})
    content = dict(slots.get("content") or {})
    feedback: dict[str, Any] = {}

    if action == "hint" and task.state in {"TRIAL", "SUPPLY", "INTERLEAVE", "BOSS"}:
        if task.state == "BOSS":
            current_task = content["boss"][int(archive.get("boss_stage", 0))]
        elif task.state == "SUPPLY":
            current_task = content["remedy"]
        else:
            strategy = archive.get("selected_strategy") or "understanding"
            current_task = content[strategy][task.state.lower()]
        hint_message = current_task.get("hint", "回到规则和例子，再试一次。")
        record_hint_event(
            slots, node=task.state, hint=hint_message,
            boss_stage=int(archive.get("boss_stage", 0)) if task.state == "BOSS" else None,
        )
        feedback = {"kind": "hint", "message": hint_message}
    elif task.state == "GOAL" and action == "continue":
        _enter(task, slots, "SCROLL", "卷轴已展开")
    elif task.state == "SCROLL":
        # 教学环节：先看规则，再答确认题，答对才放行到方法选择。
        check = dict((content.get("scroll") or {}).get("teaching_check") or {})
        if action == "submit" and check:
            correct, score = _evaluate(payload.get("answer"), check)
            _record(slots, "SCROLL", payload.get("answer"), score, "pass" if correct else "needs_review", hint=check.get("hint") if not correct else None)
            await update_after_submit(task, slots, attempted_node="SCROLL")
            attempt = int((slots.get("attempts") or {}).get("SCROLL", 0))
            if correct:
                archive["teaching_passed"] = True
                slots["archive"] = archive
                _enter(task, slots, "METHOD", "教学确认通过，选择学习方法")
                feedback = {"kind": "success", "message": "你已经确认理解这条规则，可以选方法开始试炼了。"}
            else:
                feedback = _teaching_feedback(check, content, "这一问是为了确认你真的看懂了规则。", attempt=attempt)
        elif action == "continue" and archive.get("teaching_passed"):
            _enter(task, slots, "METHOD", "选择学习方法")
        elif action == "continue":
            feedback = {"kind": "needs_review", "message": "先完成卷轴上的理解确认题，答对后才能继续。"}
        else:
            raise ValueError("当前卷轴节点操作无效")
    elif task.state == "METHOD" and action in {"choose_strategy", "choose_branch"}:
        choice = str(payload.get("choice") or payload.get("strategy") or "")
        if choice not in {"understanding", "practice"}:
            raise ValueError("请选择理解路线或实战路线")
        archive["selected_strategy"] = choice
        archive["selected_method"] = str(payload.get("method") or ("practice" if choice == "practice" else "explain"))[:30]
        archive["branch_choice"] = choice
        slots["archive"] = archive
        _enter(task, slots, "TRIAL", "开始初次试炼")
    elif task.state == "TRIAL" and action == "submit":
        strategy = archive.get("selected_strategy") or "understanding"
        current_task = content[strategy]["trial"]
        correct, score = _evaluate(payload.get("answer"), current_task)
        _record(slots, "TRIAL", payload.get("answer"), score, "pass" if correct else "needs_review", hint=current_task.get("hint") if not correct else None)
        await update_after_submit(task, slots, attempted_node="TRIAL")
        if correct:
            _enter(task, slots, "SUPPLY", "抵达补给营地")
            feedback = {"kind": "success", "message": "初次试炼通过，已记录一条有效证据。"}
        else:
            states = dict(slots.get("node_states") or {})
            states["TRIAL"] = "needs_review"
            states["SUPPLY"] = "active"
            slots["node_states"] = states
            archive["remedial_required"] = True
            archive["current_node"] = "supply"
            slots["archive"] = archive
            task.state = "SUPPLY"
            feedback = _teaching_feedback(current_task, content, "这条证据还不充分。", attempt=int((slots.get("attempts") or {}).get("TRIAL", 1)))
    elif task.state == "SUPPLY":
        if archive.get("remedial_required") and action == "submit":
            current_task = content["remedy"]
            correct, score = _evaluate(payload.get("answer"), current_task)
            _record(slots, "SUPPLY", payload.get("answer"), score, "pass" if correct else "needs_review", hint=current_task.get("hint") if not correct else None)
            await update_after_submit(task, slots, attempted_node="SUPPLY")
            if correct:
                archive["remedial_required"] = False
                slots["archive"] = archive
                states = dict(slots.get("node_states") or {})
                states["TRIAL"] = "completed"
                slots["node_states"] = states
                _enter(task, slots, "INTERLEAVE", "补救完成，进入交错挑战")
                feedback = {"kind": "success", "message": "补救完成，初次试炼证据已更新。"}
            else:
                slots["node_states"] = dict(slots.get("node_states") or {}) | {"SUPPLY": "needs_review"}
                feedback = _teaching_feedback(current_task, content, "这一步还没补齐。", attempt=int((slots.get("attempts") or {}).get("SUPPLY", 1)))
        elif not archive.get("remedial_required") and action == "continue":
            _enter(task, slots, "INTERLEAVE", "补给完成，进入交错挑战")
        else:
            raise ValueError("当前补给营地操作无效")
    elif task.state == "INTERLEAVE" and action == "submit":
        strategy = archive.get("selected_strategy") or "understanding"
        current_task = content[strategy]["interleave"]
        correct, score = _evaluate(payload.get("answer"), current_task)
        _record(slots, "INTERLEAVE", payload.get("answer"), score, "pass" if correct else "needs_review", hint=current_task.get("hint") if not correct else None)
        await update_after_submit(task, slots, attempted_node="INTERLEAVE")
        if correct:
            archive["boss_ready"] = True
            slots["archive"] = archive
            slots["node_states"] = dict(slots.get("node_states") or {}) | {"INTERLEAVE": "completed"}
            feedback = {"kind": "success", "message": "交错挑战通过。最终守关已经显现，是否现在进入由你决定。"}
        else:
            slots["node_states"] = dict(slots.get("node_states") or {}) | {"INTERLEAVE": "needs_review"}
            feedback = _teaching_feedback(current_task, content, "迁移证据不足。", attempt=int((slots.get("attempts") or {}).get("INTERLEAVE", 1)))
    elif task.state == "INTERLEAVE" and action == "continue" and archive.get("boss_ready"):
        archive["boss_ready"] = False
        slots["archive"] = archive
        _enter(task, slots, "BOSS", "玩家决定进入迷雾守关")
        feedback = {"kind": "continue", "message": "你决定踏入古塔，最终守关开始。"}
    elif task.state == "BOSS" and action == "submit":
        stage = int(archive.get("boss_stage", 0))
        current_task = content["boss"][stage]
        correct, score = _evaluate(payload.get("answer"), current_task)
        _record(slots, "BOSS", payload.get("answer"), score, "pass" if correct else "needs_review", hint=current_task.get("hint") if not correct else None, boss_stage=stage)
        await update_after_submit(task, slots, attempted_node="BOSS", boss_stage=stage)
        if correct and stage < 2:
            archive["boss_stage"] = stage + 1
            archive["boss_fog"] = [100, 66, 33][stage + 1]
            slots["archive"] = archive
            slots["node_states"] = dict(slots.get("node_states") or {}) | {"BOSS": "active"}
            feedback = {"kind": "success", "message": f"{current_task['label']}阶段通过，迷雾继续消散。"}
        elif correct:
            archive["boss_stage"] = 3
            archive["boss_fog"] = 0
            slots["archive"] = archive
            _enter(task, slots, "TREASURY", "迷雾散尽，拾光宝库开启")
            feedback = {"kind": "success", "message": "解释、回忆与迁移三项证据齐全。"}
        else:
            slots["node_states"] = dict(slots.get("node_states") or {}) | {"BOSS": "needs_review"}
            feedback = _teaching_feedback(current_task, content, "这次回答还缺少关键证据。", attempt=int((slots.get("attempts") or {}).get("BOSS", 1)))
    elif task.state == "TREASURY" and action == "claim":
        return await _claim(db, task, slots)
    else:
        raise ValueError(f"当前节点 {task.state} 不接受操作 {action}")

    slots["feedback"] = feedback
    task.slots = slots
    await db.commit()
    await db.refresh(task)
    return _snapshot(task, feedback)
