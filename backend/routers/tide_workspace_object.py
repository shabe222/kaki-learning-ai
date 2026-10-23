"""Authenticated controls for Tide's current thread Workspace Object."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import ChatSession
from services.tide_workspace_object import (
    WorkspaceObjectError,
    apply_workspace_object_control,
    get_current_workspace_object,
    public_workspace_object,
)
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/tide/workspace-object", tags=["tide-workspace-object"])


class WorkspaceObjectControlRequest(BaseModel):
    action: Literal["confirm", "rename", "correct_purpose", "set_focus"]
    expected_revision: int = Field(ge=1)
    value: Any = None


async def _require_owned_thread(db: AsyncSession, user_id: int, thread_id: str) -> None:
    exists = (await db.execute(select(ChatSession.id).where(
        ChatSession.user_id == user_id,
        ChatSession.thread_id == thread_id,
    ).limit(1))).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="这段对话不存在或不可用。")


@router.get("/{thread_id}")
async def get_thread_workspace_object(
    thread_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _require_owned_thread(db, current_user_id, thread_id)
    value = await get_current_workspace_object(db, user_id=current_user_id, thread_id=thread_id)
    return {"success": True, "data": public_workspace_object(value)}


@router.patch("/{thread_id}")
async def control_thread_workspace_object(
    thread_id: str,
    request: WorkspaceObjectControlRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _require_owned_thread(db, current_user_id, thread_id)
    try:
        value = await apply_workspace_object_control(
            db,
            user_id=current_user_id,
            thread_id=thread_id,
            action=request.action,
            expected_revision=request.expected_revision,
            value=request.value,
        )
        await db.commit()
        await db.refresh(value)
        return {"success": True, "data": public_workspace_object(value)}
    except WorkspaceObjectError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
