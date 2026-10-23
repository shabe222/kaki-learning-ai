"""Deterministic, evidence-bound proactive Tutor messaging.

No LLM decides whether to contact a learner.  Durable server events enter this
policy, which applies quiet hours, cooldown, daily limits, dedupe, pause and
ignored-message backoff before writing a real Tutor conversation message.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import uuid
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import (
    ChatSession, DaryaLearningLoopState, DaryaLearningRouteState,
    ProactiveTutorEvent, ProactiveTutorMessage, ProactiveTutorPreference,
    TutorSession,
)
from services.normalized_learner_state import get_normalized_learner_state
from services.darya_domain_registry import get_domain_pack, pack_for_node


SUPPORTED_EVENTS = frozenset({
    "assessment_completed", "weak_point_detected", "repeated_error",
    "mastery_improved", "learning_session_interrupted", "tutor_plan_stalled",
    "stage_completed", "long_inactivity", "learner_state_changed", "resource_ready",
})
PRIORITY_EVENTS = frozenset({
    "assessment_completed", "weak_point_detected", "learning_session_interrupted",
    "stage_completed", "long_inactivity",
})
LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
_OPT_OUT_RE = re.compile(r"别催我|不要催|别提醒|停止提醒|不要主动联系|关闭主动导师", re.I)
_TODAY_PAUSE_RE = re.compile(r"今天不想学|今天不学了|晚点再说|明天再学|今天先到这里", re.I)
_PENDING_ACTION_REPLY_RE = re.compile(r"^\s*(好|好的|开始|继续|行|可以)\s*[。！!]?\s*$", re.I)
_INTERNAL_PRESENTATION_ID = re.compile(
    r"(?:\b[a-z][a-z0-9-]*:[a-z0-9_:-]+\b|\b[a-z][a-z0-9-]*(?:_[a-z0-9-]+){2,}(?:_v\d+)?\b)",
    re.I,
)


@dataclass(frozen=True)
class PolicyDecision:
    should_contact: bool
    reason: str
    priority: str
    recommended_action: dict[str, Any]
    resource_action: dict[str, Any]
    cooldown_minutes: int
    dedupe_key: str
    defer_until: datetime | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "should_contact": self.should_contact, "reason": self.reason,
            "priority": self.priority, "recommended_action": self.recommended_action,
            "resource_action": self.resource_action, "cooldown_minutes": self.cooldown_minutes,
            "dedupe_key": self.dedupe_key,
            "defer_until": self.defer_until.isoformat() if self.defer_until else None,
        }


async def _preference(db: AsyncSession, user_id: int) -> ProactiveTutorPreference:
    row = await db.scalar(select(ProactiveTutorPreference).where(ProactiveTutorPreference.user_id == user_id))
    if row is None:
        row = ProactiveTutorPreference(user_id=user_id, category_preferences={})
        db.add(row)
        await db.flush()
    return row


def _quiet_window(now_utc: datetime, preference: ProactiveTutorPreference) -> datetime | None:
    local = now_utc.replace(tzinfo=timezone.utc).astimezone(LOCAL_ZONE)
    start, end = int(preference.quiet_start_hour), int(preference.quiet_end_hour)
    in_quiet = local.hour >= start or local.hour < end if start > end else start <= local.hour < end
    if not in_quiet:
        return None
    next_local = local.replace(hour=end, minute=0, second=0, microsecond=0)
    if local.hour >= start and start > end:
        next_local += timedelta(days=1)
    return next_local.astimezone(timezone.utc).replace(tzinfo=None)


def _action_for(event: ProactiveTutorEvent) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = dict(event.payload or {})
    if payload.get("surface") == "tide_sidecar":
        return (
            dict(payload.get("recommended_action") or {}),
            {
                "surface": "tide_sidecar",
                "opportunity_kind": payload.get("opportunity_kind"),
                "why_now": payload.get("why_now"),
                "domain_id": payload.get("domain_id"),
                "node_id": payload.get("node_id"),
                "interaction_state": "available",
            },
        )
    if event.event_type in {"assessment_completed", "weak_point_detected", "repeated_error"}:
        return (
            {"type": "start_practice" if event.event_type != "repeated_error" else "reteach_visual", "label": "开始强化" if event.event_type != "repeated_error" else "换成图解"},
            {"type": "exam" if event.event_type != "repeated_error" else "visual", "question_count": int(payload.get("question_count") or 3), "requires_confirmation": False},
        )
    if event.event_type in {"learning_session_interrupted", "long_inactivity", "tutor_plan_stalled"}:
        return ({"type": "resume_session", "label": "继续上次学习"}, {})
    if event.event_type in {"stage_completed", "mastery_improved"}:
        return ({"type": "continue_session", "label": "进入下一步"}, {})
    if event.event_type == "resource_ready":
        return ({"type": "open_resource", "label": "打开学习资源", "artifact_id": payload.get("artifact_id")}, {})
    return ({"type": "open_tutor", "label": "查看导师建议"}, {})


async def evaluate_policy(
    db: AsyncSession, *, event: ProactiveTutorEvent, now: datetime | None = None,
) -> PolicyDecision:
    now = now or datetime.utcnow()
    pref = await _preference(db, event.user_id)
    action, resource = _action_for(event)
    priority = "high" if event.event_type in PRIORITY_EVENTS else "normal"
    cooldown = int(pref.cooldown_minutes or 180) * max(1, min(4, int(pref.ignored_count or 0) + 1))
    category = event.event_type
    category_enabled = dict(pref.category_preferences or {}).get(category, True)
    base = dict(
        priority=priority, recommended_action=action, resource_action=resource,
        cooldown_minutes=cooldown, dedupe_key=event.dedupe_key,
    )
    if not pref.enabled:
        return PolicyDecision(False, "user_opted_out", **base)
    if not category_enabled:
        return PolicyDecision(False, "category_disabled", **base)
    if pref.paused_until and pref.paused_until > now:
        return PolicyDecision(False, "user_pause_active", defer_until=pref.paused_until, **base)
    quiet_until = _quiet_window(now, pref)
    if quiet_until:
        return PolicyDecision(False, "quiet_hours", defer_until=quiet_until, **base)
    if event.available_after and event.available_after > now:
        return PolicyDecision(False, "event_not_due", defer_until=event.available_after, **base)
    existing = await db.scalar(select(ProactiveTutorMessage.id).where(
        ProactiveTutorMessage.user_id == event.user_id,
        ProactiveTutorMessage.dedupe_key == event.dedupe_key,
    ))
    if existing is not None:
        return PolicyDecision(False, "deduplicated", **base)
    local_now = now.replace(tzinfo=timezone.utc).astimezone(LOCAL_ZONE)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    today_count = int(await db.scalar(select(func.count(ProactiveTutorMessage.id)).where(
        ProactiveTutorMessage.user_id == event.user_id,
        ProactiveTutorMessage.created_at >= local_start,
    )) or 0)
    if today_count >= int(pref.max_messages_per_day or 3):
        next_day = (local_now + timedelta(days=1)).replace(hour=int(pref.quiet_end_hour), minute=0, second=0, microsecond=0)
        return PolicyDecision(False, "daily_limit", defer_until=next_day.astimezone(timezone.utc).replace(tzinfo=None), **base)
    if pref.last_contact_at and pref.last_contact_at + timedelta(minutes=cooldown) > now:
        # High-priority verified assessment/ready events can bypass one normal
        # cooldown, but never quiet hours, opt-out, pause, dedupe or daily limit.
        if priority != "high":
            return PolicyDecision(False, "cooldown", defer_until=pref.last_contact_at + timedelta(minutes=cooldown), **base)
    return PolicyDecision(True, "eligible_verified_event", **base)


def _node_titles(session: TutorSession | None, statuses: set[str]) -> list[str]:
    if session is None:
        return []
    states = {str(k): dict(v) for k, v in dict(session.node_states or {}).items()}
    return [
        str(node.get("title") or node.get("id")) for node in list(session.plan_nodes or [])
        if states.get(str(node.get("id")), {}).get("status") in statuses
    ]


def _message_copy(event: ProactiveTutorEvent, session: TutorSession | None) -> str:
    payload = dict(event.payload or {})
    if payload.get("surface") == "tide_sidecar":
        return str(payload.get("why_now") or "这里有一个与当前进度直接相关的下一步，你可以按需要打开。")
    topic = str(payload.get("topic") or payload.get("node_title") or (session.goal if session else "当前学习内容"))
    weak = [str(x) for x in payload.get("weak_points") or [] if str(x).strip()]
    completed = _node_titles(session, {"completed"})
    current = next((str(x.get("title") or x.get("id")) for x in list(session.plan_nodes or []) if str(x.get("id")) == str(session.current_node_id)), topic) if session else topic
    if event.event_type == "assessment_completed":
        score = payload.get("score")
        score_copy = f"，本次结果是 {score}" if score is not None else ""
        if weak:
            return f"我刚看完这次测评{score_copy}。{weak[0]}还需要巩固，我准备按当前知识点补一组短练习。"
        return f"我刚看完这次测评{score_copy}。可信评估已经更新，我们可以按新的学习状态继续下一步。"
    if event.event_type == "weak_point_detected":
        return f"最新可信评估显示“{weak[0] if weak else topic}”还不稳定。我先不增加难度，准备一组针对性练习。"
    if event.event_type == "repeated_error":
        return f"你在“{weak[0] if weak else topic}”连续出现相同问题。我先不继续出新题，换成图解重新讲一次。"
    if event.event_type in {"learning_session_interrupted", "long_inactivity", "tutor_plan_stalled"}:
        done = f"前面已经完成：{'、'.join(completed[:3])}。" if completed else "当前进度已经保存。"
        return f"{done}上次停在“{current}”，继续时会从保存的下一步开始，不需要重新学习。"
    if event.event_type in {"stage_completed", "mastery_improved"}:
        previous, current_score = payload.get("previous_signal"), payload.get("current_signal")
        change = f"，从 {previous} 提升到 {current_score}" if previous is not None and current_score is not None else ""
        return f"可信学习证据确认你完成了“{topic}”这一阶段{change}。下一步已经按最新状态调整。"
    if event.event_type == "resource_ready":
        return f"你需要的“{topic}”学习资源已经由正式任务生成，可以直接打开继续学习。"
    return f"你的学习状态刚刚有了新的可信变化。我们可以从“{current}”继续。"


async def create_event(
    db: AsyncSession, *, user_id: int, event_type: str, trigger_ref: str,
    conversation_id: str | None = None, tutor_session_id: str | None = None,
    evidence_ids: list[str] | None = None, payload: dict[str, Any] | None = None,
    learner_state_version: str | None = None, dedupe_key: str | None = None,
    available_after: datetime | None = None, process_now: bool = True,
) -> ProactiveTutorEvent:
    if event_type not in SUPPORTED_EVENTS:
        raise ValueError("unsupported proactive tutor event")
    key = dedupe_key or f"{event_type}:{trigger_ref}"
    existing = await db.scalar(select(ProactiveTutorEvent).where(
        ProactiveTutorEvent.user_id == user_id, ProactiveTutorEvent.dedupe_key == key,
    ))
    if existing is not None:
        if process_now and existing.status in {"pending", "deferred"}:
            await process_event(db, existing)
        return existing
    event = ProactiveTutorEvent(
        event_id=f"pte_{uuid.uuid4().hex}", user_id=user_id,
        conversation_id=conversation_id, tutor_session_id=tutor_session_id,
        event_type=event_type, trigger_ref=trigger_ref,
        evidence_ids=list(evidence_ids or []), payload=dict(payload or {}),
        learner_state_version=learner_state_version, dedupe_key=key,
        available_after=available_after, status="pending",
    )
    db.add(event)
    await db.flush()
    if process_now:
        await process_event(db, event)
    else:
        await db.commit()
        await db.refresh(event)
    return event


async def process_event(db: AsyncSession, event: ProactiveTutorEvent) -> ProactiveTutorMessage | None:
    if event.status == "contacted":
        return await db.scalar(select(ProactiveTutorMessage).where(ProactiveTutorMessage.proactive_event_id == event.event_id))
    session = None
    if event.tutor_session_id:
        session = await db.scalar(select(TutorSession).where(
            TutorSession.session_id == event.tutor_session_id,
            TutorSession.user_id == event.user_id,
        ))
    if session is None:
        session = await db.scalar(select(TutorSession).where(TutorSession.user_id == event.user_id).order_by(desc(TutorSession.updated_at)).limit(1))
    conversation_id = event.conversation_id or (session.thread_id if session else None)
    chat = None if not conversation_id else await db.scalar(select(ChatSession).where(
        ChatSession.user_id == event.user_id, ChatSession.thread_id == conversation_id,
    ))
    if chat is None:
        event.status = "suppressed"
        event.policy_result = {"should_contact": False, "reason": "tutor_conversation_missing"}
        event.processed_at = datetime.utcnow()
        await db.commit()
        return None
    decision = await evaluate_policy(db, event=event)
    event.policy_result = decision.payload()
    if not decision.should_contact:
        event.status = "deferred" if decision.defer_until else "suppressed"
        if decision.defer_until:
            event.available_after = decision.defer_until
        event.processed_at = datetime.utcnow()
        await db.commit()
        return None
    content = _message_copy(event, session)
    message_id = f"ptm_{uuid.uuid4().hex}"
    payload = dict(event.payload or {})
    sidecar_only = payload.get("surface") == "tide_sidecar"
    message = ProactiveTutorMessage(
        message_id=message_id, user_id=event.user_id, conversation_id=chat.thread_id,
        tutor_session_id=session.session_id if session else None,
        proactive_event_id=event.event_id, trigger_event=event.event_type,
        trigger_event_id=event.trigger_ref, evidence_ids=list(event.evidence_ids or []),
        learner_state_version=event.learner_state_version,
        resource_artifact_ids=list((event.payload or {}).get("resource_artifact_ids") or []),
        dedupe_key=event.dedupe_key,
        title=str(payload.get("title") or "DARYA · 下一步") if sidecar_only else "柿小吉 AI 导师",
        content=content, priority=decision.priority,
        recommended_action=decision.recommended_action, resource_action=decision.resource_action,
    )
    db.add(message)
    if not sidecar_only:
        messages = list(chat.messages or [])
        messages.append({
            "id": message_id, "role": "assistant", "content": content,
            "metadata": {
                "kind": "proactive_tutor", "proactive_message_id": message_id,
                "tutor_session_id": session.session_id if session else None,
                "trigger_event": event.event_type, "evidence_ids": list(event.evidence_ids or []),
                "learner_state_version": event.learner_state_version,
                "recommended_action": decision.recommended_action,
                "resource_action": decision.resource_action,
                "encouragement": event.event_type in {"stage_completed", "mastery_improved"},
            },
        })
        chat.messages = messages
        chat.message_count = len(messages)
        chat.mode = "tutor"
        chat.last_message_at = datetime.utcnow()
    pref = await _preference(db, event.user_id)
    pref.last_contact_at = datetime.utcnow()
    event.status = "contacted"
    event.processed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(message)
    return message


def _semantic_part(value: Any, limit: int = 48) -> str:
    cleaned = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", str(value or "").casefold()).strip("-")
    return cleaned[:limit] or "current"


def learning_target_title(domain_id: str | None, node_id: str | None) -> str:
    """Map persistence keys through the installed Domain Pack presentation authority."""
    pack = get_domain_pack(domain_id, default=None)
    if pack is None and node_id:
        pack = pack_for_node(node_id, default=None)
    if pack is not None and node_id in pack.nodes:
        return str(pack.get_node(str(node_id)).title)
    return "上次的学习重点"


def _public_action_text(value: Any, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or _INTERNAL_PRESENTATION_ID.search(text):
        return fallback
    return text


def _tide_sidecar_payload(
    *, opportunity_kind: str, title: str, why_now: str,
    prompt: str, target_title: str, domain_id: str, node_id: str,
) -> dict[str, Any]:
    return {
        "surface": "tide_sidecar",
        "opportunity_kind": opportunity_kind,
        "title": title,
        "why_now": why_now,
        "domain_id": domain_id,
        "node_id": node_id,
        "recommended_action": {
            "type": "continue_tide_learning",
            "label": "打开",
            "prompt": prompt,
            "target_title": target_title,
            "thread_bound": True,
        },
    }


async def emit_tide_learning_opportunity(
    db: AsyncSession, *, user_id: int, thread_id: str,
    learning_result: dict[str, Any],
) -> ProactiveTutorEvent | None:
    """Persist one evidence-bound Sidecar opportunity for an unresolved learning turn."""
    evidence = dict((learning_result.get("learner_state") or {}).get("update") or {})
    after = dict((learning_result.get("learner_state") or {}).get("after") or {})
    diagnosis = dict((learning_result.get("roles") or {}).get("diagnosis") or {})
    resource = dict((learning_result.get("resource_orchestration") or {}).get("resource") or {})
    outcome = str(evidence.get("outcome") or "")
    mastery_status = str(after.get("mastery_status") or "")
    if outcome == "correct" or mastery_status == "mastered":
        return None

    # A Sidecar dismissal is feedback about this class of optional learning
    # interruption, not merely one exact rendering of it.  Suppress adjacent
    # diagnostic variants in the same conversation for a bounded period so a
    # learner does not immediately see the recommendation they just refused.
    recent_dismissed = list((await db.execute(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.user_id == user_id,
        ProactiveTutorMessage.conversation_id == thread_id,
        ProactiveTutorMessage.dismissed_at.is_not(None),
        ProactiveTutorMessage.dismissed_at >= datetime.utcnow() - timedelta(hours=24),
    ).order_by(desc(ProactiveTutorMessage.dismissed_at)))).scalars().all())
    if any(
        is_tide_sidecar_opportunity(row)
        and str(dict(row.resource_action or {}).get("opportunity_kind") or "")
        in {"unresolved_difficulty", "repeated_difficulty"}
        for row in recent_dismissed
    ):
        return None

    domain_id = str(learning_result.get("domain_id") or "learning")
    node_id = str(learning_result.get("concept_key") or "current")
    node_title = _public_action_text(
        learning_result.get("concept"),
        learning_target_title(domain_id, node_id),
    )
    gap = str(diagnosis.get("likely_gap") or "这一处还需要一次可观察的检验")
    resource_type = str(resource.get("resource_type") or "QUICK_CHECK")
    prompt = str(resource.get("prompt") or resource.get("question") or f"围绕“{node_title}”给我一道两分钟小练习。")
    cycle = int(learning_result.get("cycle") or 1)
    kind = "repeated_difficulty" if cycle > 1 else "unresolved_difficulty"
    title = f"再检验一下{node_title}"
    why_now = f"刚才的可观察证据显示“{gap}”。这里可以接一小步，不需要重学整节。"
    semantic = ":".join((
        "tide-opportunity", "difficulty", _semantic_part(thread_id),
        _semantic_part(domain_id), _semantic_part(node_id), _semantic_part(resource_type),
        _semantic_part(gap),
    ))
    revision = after.get("revision")
    return await create_event(
        db,
        user_id=user_id,
        event_type="repeated_error" if cycle > 1 else "weak_point_detected",
        trigger_ref=f"darya-learning:{thread_id}:{node_id}:r{revision or cycle}",
        conversation_id=thread_id,
        evidence_ids=[
            f"message:{learning_result.get('source_message_id')}",
            f"learner-state:{thread_id}:{node_id}:r{revision or cycle}",
        ],
        learner_state_version=f"darya:{thread_id}:{node_id}:r{revision or cycle}",
        payload=_tide_sidecar_payload(
            opportunity_kind=kind,
            title=title,
            why_now=why_now,
            prompt=prompt,
            target_title=node_title,
            domain_id=domain_id,
            node_id=node_id,
        ),
        dedupe_key=semantic,
    )


def _later_until(message: ProactiveTutorMessage) -> datetime | None:
    value = dict(message.resource_action or {}).get("later_until")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def is_tide_sidecar_opportunity(message: ProactiveTutorMessage) -> bool:
    return dict(message.resource_action or {}).get("surface") == "tide_sidecar"


async def current_tide_opportunity(
    db: AsyncSession, *, user_id: int, conversation_id: str,
    now: datetime | None = None,
) -> ProactiveTutorMessage | None:
    now = now or datetime.utcnow()
    rows = list((await db.execute(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.user_id == user_id,
        ProactiveTutorMessage.conversation_id == conversation_id,
        ProactiveTutorMessage.dismissed_at.is_(None),
        ProactiveTutorMessage.actioned_at.is_(None),
    ).order_by(desc(ProactiveTutorMessage.created_at)))).scalars().all())
    return next((row for row in rows if is_tide_sidecar_opportunity(row) and not (_later_until(row) and _later_until(row) > now)), None)


async def defer_tide_opportunity(
    db: AsyncSession, *, message: ProactiveTutorMessage, hours: int = 12,
) -> None:
    if not is_tide_sidecar_opportunity(message):
        raise ValueError("not a Tide Sidecar opportunity")
    now = datetime.utcnow()
    message.resource_action = {
        **dict(message.resource_action or {}),
        "interaction_state": "later",
        "later_until": (now + timedelta(hours=max(1, min(72, hours)))).isoformat(),
    }
    message.read_at = message.read_at or now
    await db.commit()


async def defer_tide_opportunities_for_control(
    db: AsyncSession, *, user_id: int, conversation_id: str, hours: int = 12,
) -> int:
    """Honor a current-turn pause without inventing another preference store."""
    rows = list((await db.execute(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.user_id == user_id,
        ProactiveTutorMessage.conversation_id == conversation_id,
        ProactiveTutorMessage.dismissed_at.is_(None),
        ProactiveTutorMessage.actioned_at.is_(None),
    ))).scalars().all())
    pending = [row for row in rows if is_tide_sidecar_opportunity(row)]
    if not pending:
        return 0
    now = datetime.utcnow()
    later_until = (now + timedelta(hours=max(1, min(72, hours)))).isoformat()
    for row in pending:
        row.resource_action = {
            **dict(row.resource_action or {}),
            "interaction_state": "later",
            "later_until": later_until,
            "deferred_by": "explicit_response_constraint",
        }
        row.read_at = row.read_at or now
    await db.commit()
    return len(pending)


def opened_opportunity_response(message: ProactiveTutorMessage) -> str:
    action = dict(message.recommended_action or {})
    resource = dict(message.resource_action or {})
    title = _public_action_text(action.get("target_title"), "刚才的学习重点")
    prompt = _public_action_text(
        action.get("prompt"),
        "先接着上次停下的那一步，把当时的判断说清楚。",
    )
    if resource.get("opportunity_kind") == "useful_continuation":
        return f"我们接着看“{title}”。\n\n{prompt}"
    return f"我们就接着处理“{title}”这一处。\n\n{prompt}"


async def ensure_tide_continuation_opportunity(
    db: AsyncSession, *, user_id: int, conversation_id: str,
    now: datetime | None = None, inactivity_minutes: int = 30,
) -> ProactiveTutorMessage | None:
    """Create a quiet continuation only for a persisted, genuinely interrupted route."""
    now = now or datetime.utcnow()
    pending_rows = list((await db.execute(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.user_id == user_id,
        ProactiveTutorMessage.conversation_id == conversation_id,
        ProactiveTutorMessage.dismissed_at.is_(None),
        ProactiveTutorMessage.actioned_at.is_(None),
    ).order_by(desc(ProactiveTutorMessage.created_at)))).scalars().all())
    pending_sidecar = next((row for row in pending_rows if is_tide_sidecar_opportunity(row)), None)
    if pending_sidecar is not None:
        later = _later_until(pending_sidecar)
        return None if later and later > now else pending_sidecar
    current = await current_tide_opportunity(
        db, user_id=user_id, conversation_id=conversation_id, now=now,
    )
    if current is not None:
        return current
    chat = await db.scalar(select(ChatSession).where(
        ChatSession.user_id == user_id, ChatSession.thread_id == conversation_id,
    ))
    route = await db.scalar(select(DaryaLearningRouteState).where(
        DaryaLearningRouteState.user_id == user_id,
        DaryaLearningRouteState.thread_id == conversation_id,
    ))
    if chat is None or route is None or not chat.last_message_at:
        return None
    if chat.last_message_at > now - timedelta(minutes=max(1, inactivity_minutes)):
        return None
    state = await db.scalar(select(DaryaLearningLoopState).where(
        DaryaLearningLoopState.user_id == user_id,
        DaryaLearningLoopState.thread_id == conversation_id,
        DaryaLearningLoopState.concept_key == route.current_node_id,
    ))
    node_title = learning_target_title(route.domain_id, route.current_node_id)
    latest_cycle = dict(getattr(state, "latest_cycle", None) or {})
    latest_resource = dict((latest_cycle.get("resource_orchestration") or {}).get("resource") or {})
    latest_structure = dict(latest_resource.get("structure") or {})
    continuation_prompt = _public_action_text(
        latest_resource.get("prompt")
        or latest_resource.get("question")
        or latest_structure.get("prompt")
        or latest_structure.get("check_question"),
        f"先回到“{node_title}”，把上次没说清的判断接上。",
    )
    why_now = f"上次停在“{node_title}”，学习状态和路线都还在。现在继续，不需要从头来。"
    event = await create_event(
        db,
        user_id=user_id,
        event_type="learning_session_interrupted",
        trigger_ref=f"darya-continuation:{conversation_id}:r{route.revision}",
        conversation_id=conversation_id,
        evidence_ids=[
            f"route:{conversation_id}:r{route.revision}",
            f"learner-state:{conversation_id}:{route.current_node_id}:r{getattr(state, 'revision', 0)}",
        ],
        learner_state_version=f"darya:{conversation_id}:{route.current_node_id}:r{getattr(state, 'revision', 0)}",
        payload=_tide_sidecar_payload(
            opportunity_kind="useful_continuation",
            title=f"从“{node_title}”接着来",
            why_now=why_now,
            prompt=continuation_prompt,
            target_title=node_title,
            domain_id=route.domain_id,
            node_id=route.current_node_id,
        ),
        dedupe_key=f"tide-opportunity:continuation:{_semantic_part(conversation_id)}:{route.revision}",
    )
    return await db.scalar(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.proactive_event_id == event.event_id,
    ))


async def emit_after_exam_evaluation(
    db: AsyncSession, *, user_id: int, workspace_event, evaluation: dict[str, Any],
) -> ProactiveTutorEvent | None:
    if evaluation.get("evaluation_status") != "evaluated" or workspace_event is None:
        return None
    payload = dict(workspace_event.payload or {})
    session_id = str(payload.get("tutor_session_id") or "") or None
    state = await get_normalized_learner_state(db, user_id)
    weak_points = list(state.weak_points or [])
    event_type = "weak_point_detected" if weak_points else "assessment_completed"
    return await create_event(
        db, user_id=user_id, event_type=event_type,
        trigger_ref=f"workspace-event:{workspace_event.id}",
        conversation_id=workspace_event.thread_id, tutor_session_id=session_id,
        evidence_ids=[f"workspace-event:{workspace_event.id}", f"evaluation:r{evaluation.get('evaluation_revision')}"],
        learner_state_version=state.state_version,
        payload={
            "score": payload.get("score"), "question_count": payload.get("question_count"),
            "topic": state.current_topic, "weak_points": weak_points,
            "evaluation_revision": evaluation.get("evaluation_revision"),
        },
        dedupe_key=f"assessment:{workspace_event.id}:evaluation:{evaluation.get('evaluation_revision')}",
    )


async def schedule_interrupted_session(db: AsyncSession, session: TutorSession) -> ProactiveTutorEvent:
    # The in-conversation pause response is immediate; proactive re-contact is
    # eligible the next local day and still passes policy at delivery time.
    state = await get_normalized_learner_state(db, session.user_id)
    return await create_event(
        db, user_id=session.user_id, event_type="learning_session_interrupted",
        trigger_ref=f"tutor-pause:{session.session_id}:r{session.workflow_revision}",
        conversation_id=session.thread_id, tutor_session_id=session.session_id,
        evidence_ids=[f"tutor-session:{session.session_id}", f"checkpoint:r{session.workflow_revision}"],
        learner_state_version=state.state_version,
        payload={"topic": state.current_topic, "progress_pct": session.progress_pct},
        dedupe_key=f"pause-followup:{session.session_id}:{session.workflow_revision}",
        available_after=datetime.utcnow() + timedelta(hours=12), process_now=False,
    )


async def mark_conversation_read(db: AsyncSession, *, user_id: int, conversation_id: str) -> int:
    rows = list((await db.execute(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.user_id == user_id,
        ProactiveTutorMessage.conversation_id == conversation_id,
        ProactiveTutorMessage.read_at.is_(None),
    ))).scalars().all())
    now = datetime.utcnow()
    for row in rows:
        row.read_at = now
    if rows:
        await db.commit()
    return len(rows)


async def pending_action_for_reply(
    db: AsyncSession, *, user_id: int, conversation_id: str, text: str,
) -> ProactiveTutorMessage | None:
    """Bind short affirmative replies to the latest pending proactive action."""
    if not _PENDING_ACTION_REPLY_RE.match(text or ""):
        return None
    return await db.scalar(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.user_id == user_id,
        ProactiveTutorMessage.conversation_id == conversation_id,
        ProactiveTutorMessage.dismissed_at.is_(None),
        ProactiveTutorMessage.actioned_at.is_(None),
    ).order_by(desc(ProactiveTutorMessage.created_at)).limit(1))


def pending_action_prompt(message: ProactiveTutorMessage) -> str:
    action_type = str((message.recommended_action or {}).get("type") or "continue_session")
    return {
        "resume_session": "恢复学习", "continue_session": "继续",
        "start_practice": "给我做题", "reteach_visual": "换成图解讲",
    }.get(action_type, "继续")


async def mark_proactive_actioned(db: AsyncSession, message: ProactiveTutorMessage) -> None:
    now = datetime.utcnow()
    message.actioned_at = message.actioned_at or now
    message.read_at = message.read_at or now
    pref = await _preference(db, message.user_id)
    pref.ignored_count = 0
    await db.commit()


async def conversation_summaries(db: AsyncSession, *, user_id: int) -> dict[str, dict[str, Any]]:
    rows = list((await db.execute(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.user_id == user_id,
    ).order_by(ProactiveTutorMessage.created_at.asc()))).scalars().all())
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = result.setdefault(row.conversation_id, {"unread_count": 0, "latest_proactive_message": None})
        if row.read_at is None and row.dismissed_at is None:
            item["unread_count"] += 1
        item["latest_proactive_message"] = {
            "message_id": row.message_id, "content": row.content,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "trigger_event": row.trigger_event,
        }
    return result


async def update_contact_preference_from_user_text(db: AsyncSession, *, user_id: int, text: str) -> bool:
    if not (_OPT_OUT_RE.search(text or "") or _TODAY_PAUSE_RE.search(text or "")):
        return False
    pref = await _preference(db, user_id)
    if _OPT_OUT_RE.search(text or ""):
        pref.enabled = False
    else:
        local = datetime.now(LOCAL_ZONE)
        next_morning = (local + timedelta(days=1)).replace(hour=int(pref.quiet_end_hour), minute=0, second=0, microsecond=0)
        pref.paused_until = next_morning.astimezone(timezone.utc).replace(tzinfo=None)
    await db.commit()
    return True


async def sweep_due_events(db: AsyncSession) -> int:
    now = datetime.utcnow()
    # Additive repair for messages created before state/evidence binding became
    # mandatory. It derives only from the owned Tutor session and read model.
    unbound = list((await db.execute(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.learner_state_version.is_(None),
        ProactiveTutorMessage.tutor_session_id.is_not(None),
    ).limit(100))).scalars().all())
    for message in unbound:
        session = await db.scalar(select(TutorSession).where(
            TutorSession.session_id == message.tutor_session_id,
            TutorSession.user_id == message.user_id,
        ))
        if session is None:
            continue
        state = await get_normalized_learner_state(db, message.user_id)
        message.learner_state_version = state.state_version
        if not message.evidence_ids:
            message.evidence_ids = [
                f"tutor-session:{session.session_id}",
                f"checkpoint:r{session.workflow_revision}",
            ]
        if message.trigger_event in PRIORITY_EVENTS:
            message.priority = "high"
    if unbound:
        await db.commit()
    rows = list((await db.execute(select(ProactiveTutorEvent).where(
        ProactiveTutorEvent.status.in_(["pending", "deferred"]),
        (ProactiveTutorEvent.available_after.is_(None)) | (ProactiveTutorEvent.available_after <= now),
    ).order_by(ProactiveTutorEvent.created_at.asc()).limit(100))).scalars().all())
    contacted = 0
    for event in rows:
        if await process_event(db, event) is not None:
            contacted += 1
    # True inactivity source: durable Tutor session timestamp, one event/day.
    inactive = list((await db.execute(select(TutorSession).where(
        TutorSession.status.in_(["active", "paused"]),
        TutorSession.updated_at <= now - timedelta(hours=24),
    ).limit(100))).scalars().all())
    for session in inactive:
        day_key = now.strftime("%Y-%m-%d")
        state = await get_normalized_learner_state(db, session.user_id)
        event = await create_event(
            db, user_id=session.user_id, event_type="long_inactivity",
            trigger_ref=f"tutor-inactive:{session.session_id}:{day_key}",
            conversation_id=session.thread_id, tutor_session_id=session.session_id,
            evidence_ids=[f"tutor-session:{session.session_id}", f"checkpoint:r{session.workflow_revision}"],
            payload={"progress_pct": session.progress_pct, "topic": state.current_topic},
            learner_state_version=state.state_version,
            dedupe_key=f"inactivity:{session.session_id}:{day_key}",
        )
        if event.status == "contacted":
            contacted += 1
    return contacted


async def proactive_scheduler_loop(stop_event: asyncio.Event, interval_seconds: int = 300) -> None:
    while not stop_event.is_set():
        try:
            async with get_db_session() as db:
                await sweep_due_events(db)
        except Exception:
            # Scheduler failure is isolated; request paths and persisted events remain safe.
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
