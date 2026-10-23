"""Authenticated structured controls for Conversation-owned learning actions."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from services.conversation_capability_actions import (
    _review_failure_message,
    get_conversation_capability,
    prepare_conversation_capability,
    review_conversation_capability,
)
from utils.auth import get_current_user_id


router = APIRouter(
    prefix="/api/chat/capability-actions",
    tags=["conversation-capability-actions"],
)


class PrepareActionBody(BaseModel):
    # 客户端确认时携带它当前的产品主题，作为这次产物的版本级视觉快照。
    theme: str = Field(default="", max_length=20)


def _http_error(exc: Exception, *, stage: str) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, {
            "code": "CONVERSATION_ACTION_NOT_FOUND",
            "message": "这次学习行动已经不在当前对话中。",
        })
    code = str(exc)[:80] if isinstance(exc, ValueError) else f"CONVERSATION_ACTION_{stage.upper()}_FAILED"
    if stage == "review":
        # 检查没过的真实原因要跟着 HTTP 响应一起回去，不能只在 action 里。
        message = _review_failure_message(exc)
    elif stage == "prepare":
        message = "这份资料暂时没有准备完成；对话和已有资料都保留着，可以重试。"
    else:
        message = "资料已经保留，但检查没有完成；可以重试检查。"
    return HTTPException(409 if isinstance(exc, ValueError) else 503, {
        "code": code,
        "message": message,
    })


@router.get("/{action_key}")
async def get_action(
    action_key: str,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        action = await get_conversation_capability(
            db, user_id=user_id, action_key=action_key,
        )
        return {"success": True, "data": action}
    except Exception as exc:
        raise _http_error(exc, stage="read") from exc


@router.post("/{action_key}/prepare")
async def prepare_action(
    action_key: str,
    background_tasks: BackgroundTasks,
    body: PrepareActionBody | None = None,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        action = await prepare_conversation_capability(
            db, user_id=user_id, action_key=action_key, background_tasks=background_tasks,
            theme=str((body or PrepareActionBody()).theme or ""),
        )
        return {"success": True, "data": action}
    except Exception as exc:
        raise _http_error(exc, stage="prepare") from exc


@router.post("/{action_key}/review")
async def review_action(
    action_key: str,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        action = await review_conversation_capability(
            db, user_id=user_id, action_key=action_key,
        )
        return {"success": True, "data": action}
    except Exception as exc:
        raise _http_error(exc, stage="review") from exc
