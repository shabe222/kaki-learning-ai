"""Authenticated proactive Tutor inbox, unread state and bound actions."""
from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import ProactiveTutorMessage, ProactiveTutorPreference, TutorSession
from services.proactive_tutor import (
    _preference, current_tide_opportunity, defer_tide_opportunity,
    ensure_tide_continuation_opportunity, is_tide_sidecar_opportunity,
    mark_conversation_read, opened_opportunity_response, sweep_due_events,
)
from services.chat_service import append_chat_message
from services.tutor_core import prepare_tutor_decision_task, task_public_payload
from services.tutor_orchestrator import tutor_plan_payload
from services.tutor_state_graph import execute_tutor_graph, lifecycle_public_payload
from utils.auth import get_current_user_id

router = APIRouter(prefix="/api/chat/tutor/proactive", tags=["proactive-tutor"])


class ReadRequest(BaseModel):
    conversation_id: str = Field(min_length=4, max_length=100)


class PreferenceRequest(BaseModel):
    enabled: bool | None = None
    quiet_start_hour: int | None = Field(default=None, ge=0, le=23)
    quiet_end_hour: int | None = Field(default=None, ge=0, le=23)
    cooldown_minutes: int | None = Field(default=None, ge=15, le=1440)
    max_messages_per_day: int | None = Field(default=None, ge=0, le=10)
    category_preferences: dict[str, bool] | None = None


class LaterRequest(BaseModel):
    hours: int = Field(default=12, ge=1, le=72)


def _payload(row: ProactiveTutorMessage) -> dict[str, Any]:
    return {
        "message_id": row.message_id, "conversation_id": row.conversation_id,
        "tutor_session_id": row.tutor_session_id, "trigger_event": row.trigger_event,
        "trigger_event_id": row.trigger_event_id, "evidence_ids": list(row.evidence_ids or []),
        "learner_state_version": row.learner_state_version,
        "resource_artifact_ids": list(row.resource_artifact_ids or []),
        "title": row.title, "content": row.content, "priority": row.priority,
        "recommended_action": dict(row.recommended_action or {}),
        "resource_action": dict(row.resource_action or {}),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "dismissed_at": row.dismissed_at.isoformat() if row.dismissed_at else None,
        "actioned_at": row.actioned_at.isoformat() if row.actioned_at else None,
    }


async def _owned_message(
    db: AsyncSession, *, current_user_id: int, message_id: str,
) -> ProactiveTutorMessage:
    row = await db.scalar(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.message_id == message_id,
        ProactiveTutorMessage.user_id == current_user_id,
    ))
    if row is None:
        raise HTTPException(404, "导师消息不存在。")
    return row


@router.get("/messages")
async def list_proactive_messages(
    current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    await sweep_due_events(db)
    rows = list((await db.execute(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.user_id == current_user_id,
        ProactiveTutorMessage.dismissed_at.is_(None),
    ).order_by(desc(ProactiveTutorMessage.created_at)).limit(50))).scalars().all())
    return {"success": True, "data": {
        "items": [_payload(row) for row in rows],
        "unread_count": sum(1 for row in rows if row.read_at is None),
    }}


@router.post("/read")
async def read_conversation(request: ReadRequest,
    current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    count = await mark_conversation_read(
        db, user_id=current_user_id, conversation_id=request.conversation_id,
    )
    return {"success": True, "data": {"conversation_id": request.conversation_id, "marked_read": count}}


@router.post("/messages/{message_id}/dismiss")
async def dismiss_message(message_id: str,
    current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    row = await db.scalar(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.message_id == message_id,
        ProactiveTutorMessage.user_id == current_user_id,
    ))
    if row is None:
        raise HTTPException(404, "导师消息不存在。")
    if row.dismissed_at is None:
        row.dismissed_at = datetime.utcnow()
        pref = await _preference(db, current_user_id)
        pref.ignored_count = min(10, int(pref.ignored_count or 0) + 1)
        await db.commit()
    return {"success": True, "data": _payload(row)}


@router.get("/opportunities/current")
async def get_current_opportunity(
    conversation_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    row = await ensure_tide_continuation_opportunity(
        db, user_id=current_user_id, conversation_id=conversation_id,
    )
    if row is None:
        row = await current_tide_opportunity(
            db, user_id=current_user_id, conversation_id=conversation_id,
        )
    return {"success": True, "data": _payload(row) if row else None}


@router.post("/opportunities/{message_id}/later")
async def later_opportunity(
    message_id: str,
    request: LaterRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    row = await _owned_message(
        db, current_user_id=current_user_id, message_id=message_id,
    )
    if row.dismissed_at is not None or row.actioned_at is not None:
        raise HTTPException(409, "这条建议已经处理。")
    if not is_tide_sidecar_opportunity(row):
        raise HTTPException(409, "这不是当前对话中的可稍后建议。")
    await defer_tide_opportunity(db, message=row, hours=request.hours)
    await db.refresh(row)
    return {"success": True, "data": _payload(row)}


@router.get("/preferences")
async def get_preferences(
    current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    row = await _preference(db, current_user_id)
    await db.commit()
    return {"success": True, "data": {
        "enabled": bool(row.enabled), "quiet_start_hour": row.quiet_start_hour,
        "quiet_end_hour": row.quiet_end_hour, "cooldown_minutes": row.cooldown_minutes,
        "max_messages_per_day": row.max_messages_per_day,
        "category_preferences": dict(row.category_preferences or {}),
        "paused_until": row.paused_until.isoformat() if row.paused_until else None,
    }}


@router.patch("/preferences")
async def update_preferences(request: PreferenceRequest,
    current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    row = await _preference(db, current_user_id)
    for field in ("enabled", "quiet_start_hour", "quiet_end_hour", "cooldown_minutes", "max_messages_per_day"):
        value = getattr(request, field)
        if value is not None:
            setattr(row, field, value)
    if request.category_preferences is not None:
        row.category_preferences = dict(request.category_preferences)
    if request.enabled is True:
        row.paused_until = None
        row.ignored_count = 0
    await db.commit()
    await db.refresh(row)
    return await get_preferences(current_user_id=current_user_id, db=db)


@router.post("/messages/{message_id}/action")
async def execute_message_action(message_id: str,
    current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    row = await db.scalar(select(ProactiveTutorMessage).where(
        ProactiveTutorMessage.message_id == message_id,
        ProactiveTutorMessage.user_id == current_user_id,
    ))
    if row is None:
        raise HTTPException(404, "导师消息不存在。")
    action = dict(row.recommended_action or {})
    if row.actioned_at is not None:
        return {"success": True, "data": {
            "idempotent_replay": True, "action": action,
            "tutor_session_id": row.tutor_session_id,
            "conversation_id": row.conversation_id,
            "assistant_response": dict(row.resource_action or {}).get("opened_response"),
        }}
    if is_tide_sidecar_opportunity(row):
        action_type = str(action.get("type") or "continue_tide_learning")
        if action_type not in {"continue_tide_learning", "open_resource"}:
            raise HTTPException(409, "这条建议没有可执行的真实动作。")
        artifact_id = action.get("artifact_id")
        if action_type == "open_resource" and not artifact_id:
            raise HTTPException(409, "原学习资源已不可用，无法打开。")
        row.actioned_at = datetime.utcnow()
        row.read_at = row.read_at or datetime.utcnow()
        assistant_response = None
        if action_type == "continue_tide_learning":
            assistant_response = opened_opportunity_response(row)
        row.resource_action = {
            **dict(row.resource_action or {}),
            "interaction_state": "opened",
            "opened_at": row.actioned_at.isoformat(),
            "opened_response": assistant_response,
        }
        pref = await _preference(db, current_user_id)
        pref.ignored_count = 0
        if assistant_response:
            await append_chat_message(
                db,
                current_user_id,
                row.conversation_id,
                "assistant",
                assistant_response,
                f"opportunity-open-{uuid.uuid4()}",
                metadata={
                    "kind": "proactive_opportunity_open",
                    "proactive_message_id": row.message_id,
                },
            )
        else:
            await db.commit()
        return {"success": True, "data": {
            "idempotent_replay": False,
            "action": action,
            "conversation_id": row.conversation_id,
            "assistant_response": assistant_response,
            "artifact_id": artifact_id,
        }}
    session = None if not row.tutor_session_id else await db.scalar(select(TutorSession).where(
        TutorSession.session_id == row.tutor_session_id,
        TutorSession.user_id == current_user_id,
    ))
    if session is None:
        raise HTTPException(409, "原学习计划已不可用，无法执行这条导师建议。")
    action_type = str(action.get("type") or "open_tutor")
    if action_type == "open_resource":
        row.actioned_at = datetime.utcnow()
        row.read_at = row.read_at or datetime.utcnow()
        await db.commit()
        return {"success": True, "data": {
            "action": action, "artifact_id": action.get("artifact_id"),
            "tutor_session_id": session.session_id,
        }}
    prompt = {
        "resume_session": "恢复学习", "continue_session": "继续",
        "start_practice": "给我做题", "reteach_visual": "换成图解讲",
    }.get(action_type, "继续")
    result = await execute_tutor_graph(
        db, session=session, user_id=current_user_id, message=prompt,
    )
    data: dict[str, Any] = tutor_plan_payload(result.session)
    data["tutor_public"] = {"lifecycle": lifecycle_public_payload(result.session, result.lifecycle_message)}
    if result.should_prepare_task and result.context and result.decision:
        task, runtime = await prepare_tutor_decision_task(
            db, session=result.session, context=result.context, decision=result.decision,
        )
        data["task"] = task_public_payload(task, runtime)
    row.actioned_at = datetime.utcnow()
    row.read_at = row.read_at or datetime.utcnow()
    pref = await _preference(db, current_user_id)
    pref.ignored_count = 0
    await db.commit()
    data.update({
        "action": action, "tutor_session_id": session.session_id,
        "conversation_id": row.conversation_id, "idempotent_replay": False,
    })
    return {"success": True, "data": data}
