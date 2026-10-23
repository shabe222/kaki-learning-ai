"""Thread-scoped teaching-stage controls and honest learner-facing projection.

The route remains the authority for what to learn next. A stage can be paused
or closed without turning conversation evidence into a formal mastery verdict.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DaryaLearningLoopState, DaryaLearningRouteState
from services.darya_domain_registry import get_domain_pack


_PAUSE = re.compile(r"(?:暂停(?:这段|当前)?学习|先不学|今天先到这里|先停在这里|稍后继续)")
_END = re.compile(r"(?:结束(?:这一|这|当前)?(?:阶段|段)(?:教学|学习)?|这(?:一)?段(?:教学|学习)到此结束|完成本阶段)")
_RESUME = re.compile(r"(?:继续刚才(?:的学习)?|恢复(?:这段|当前)?学习|接着(?:刚才|上次)(?:的学习)?|从当前节点接着教)")
_TOPIC_SWITCH = re.compile(r"^\s*(?:我们\s*)?(?:现在|接下来)?\s*(?:先\s*)?(?:换个话题|转到另一个话题|聊点别的)(?=[，,。！？：:\s]|$)")


def explicit_topic_switch_from_message(message: str) -> bool:
    """Only an actual request to change topic can release a prior route."""
    return bool(_TOPIC_SWITCH.search(str(message or "")))


def stage_action_from_message(message: str) -> str | None:
    text = " ".join(str(message or "").split())
    if _END.search(text):
        return "end"
    if _PAUSE.search(text):
        return "pause"
    if _RESUME.search(text):
        return "resume"
    return None


def _summary(route: DaryaLearningRouteState, rows: Sequence[DaryaLearningLoopState]) -> dict[str, Any]:
    pack = get_domain_pack(route.domain_id)
    current_title = (
        pack.nodes[route.current_node_id].title
        if pack and route.current_node_id in pack.nodes else route.current_node_id
    )
    observations = [
        str(item.get("observation") or "").strip()
        for row in rows for item in (row.evidence or [])
        if item.get("supports_update") and str(item.get("observation") or "").strip()
    ]
    current = next((row for row in rows if row.concept_key == route.current_node_id), None)
    latest_plan = route.latest_plan or {}
    return {
        "topic": current_title,
        "observed": observations[-1] if observations else "还没有足以判断的独立学习表现。",
        "evidence_count": len(observations),
        "remaining_gap": (
            str(current.current_misconception).strip()
            if current and current.current_misconception else "还需要一次新情境下的独立检查。"
        ),
        "next_step": str(latest_plan.get("reason") or "从当前知识点继续，先做一个短检查。"),
        "mastery_conclusion": "本段已收束；掌握情况仍需独立作答或正式评估确认。",
    }


async def control_teaching_stage(
    db: AsyncSession, *, user_id: int, thread_id: str, action: str,
    message_id: str | None = None,
) -> dict[str, Any] | None:
    if action not in {"pause", "end", "resume"}:
        return None
    route = (await db.execute(select(DaryaLearningRouteState).where(
        DaryaLearningRouteState.user_id == user_id,
        DaryaLearningRouteState.thread_id == thread_id,
    ))).scalar_one_or_none()
    if route is None:
        return None
    rows = list((await db.execute(select(DaryaLearningLoopState).where(
        DaryaLearningLoopState.user_id == user_id,
        DaryaLearningLoopState.thread_id == thread_id,
    ))).scalars().all())
    status = {"pause": "PAUSED", "end": "ENDED", "resume": "ACTIVE"}[action]
    previous_status = str((route.latest_plan or {}).get("teaching_stage_status") or "ACTIVE")
    if previous_status == status:
        return _summary(route, rows)
    plan = copy.deepcopy(route.latest_plan or {})
    summary = _summary(route, rows)
    if status == "PAUSED":
        summary["mastery_conclusion"] = "进度已保留；暂停不代表掌握，继续时仍需独立检查。"
    plan["teaching_stage_status"] = status
    plan["teaching_stage_summary"] = summary if status != "ACTIVE" else None
    plan["teaching_stage_updated_at"] = datetime.now(timezone.utc).isoformat()
    history = list(route.route_history or [])
    history.append({
        "kind": "teaching_stage_control", "action": action, "status": status,
        "message_id": message_id, "recorded_at": plan["teaching_stage_updated_at"],
    })
    route.latest_plan = plan
    route.route_history = history[-30:]
    route.revision = int(route.revision or 0) + 1
    await db.flush()
    return summary


def teaching_stage_projection(
    route: DaryaLearningRouteState | None,
    rows: Sequence[DaryaLearningLoopState],
) -> dict[str, Any] | None:
    if route is None:
        return None
    history = list(route.route_history or [])
    latest_learning = next((entry for entry in reversed(history) if entry.get("evidence_node")), None)
    evidence_node = str((latest_learning or {}).get("evidence_node") or "")
    cycle_row = next((row for row in rows if row.concept_key == evidence_node), None)
    cycle = cycle_row.latest_cycle or {} if cycle_row else {}
    decision = (cycle.get("roles") or {}).get("decision") or {}
    resource = (cycle.get("resource_orchestration") or {}).get("intent") or {}
    method = {
        "ASK": "关键问题", "EXPLAIN": "分步讲解",
        "DEMONSTRATE": "例子示范", "PRACTICE": "独立练习",
        "PAUSE": "暂停交付",
    }.get(str(decision.get("action") or ""), "短诊断")
    transition = (
        latest_learning
        if (latest_learning or {}).get("after", {}).get("movement") == "forward"
        else None
    )
    transition_public = None
    if transition:
        before, after = transition.get("before") or {}, transition.get("after") or {}
        transition_public = {
            "from": before.get("current_title") or before.get("current_node"),
            "to": after.get("next_title") or after.get("next_node"),
            "reason": after.get("reason"),
            "evidence_quality": transition.get("evidence_quality"),
        }
    plan = route.latest_plan or {}
    status = str(plan.get("teaching_stage_status") or "ACTIVE")
    stored_summary = copy.deepcopy(plan.get("teaching_stage_summary"))
    if stored_summary and status == "PAUSED":
        stored_summary["mastery_conclusion"] = "进度已保留；暂停不代表掌握，继续时仍需独立检查。"
    return {
        "scope": "bounded",
        "status": status,
        "method": method,
        "method_reason": str(decision.get("public_reason") or "先用一个短问题确定具体卡点。"),
        "resource_type": resource.get("resource_type"),
        "summary": stored_summary,
        "progress": _summary(route, rows),
        "last_transition": transition_public,
        "revision": int(route.revision or 0),
    }
