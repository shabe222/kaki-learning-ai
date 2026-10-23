"""Authenticated user controls for Tide's thread-bounded Shared Perspective."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import ChatSession
from services.tide_shared_perspective import (
    SharedPerspectiveError,
    apply_user_control,
    get_latest_snapshot,
    public_snapshot,
)
from services.tide_workspace_object import (
    get_current_workspace_object,
    sync_workspace_object_from_perspective,
)
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/tide/shared-perspective", tags=["tide-shared-perspective"])


class PerspectiveControlRequest(BaseModel):
    field: str = Field(min_length=1, max_length=50)
    action: Literal["confirm", "correct", "reject"]
    expected_revision: int = Field(ge=0)
    value: Any = None


async def _require_owned_thread(db: AsyncSession, user_id: int, thread_id: str) -> None:
    exists = (await db.execute(select(ChatSession.id).where(
        ChatSession.user_id == user_id,
        ChatSession.thread_id == thread_id,
    ).limit(1))).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="这段对话不存在或不可用。")


@router.get("/{thread_id}")
async def get_thread_perspective(
    thread_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _require_owned_thread(db, current_user_id, thread_id)
    snapshot = await get_latest_snapshot(db, user_id=current_user_id, thread_id=thread_id)
    workspace_object = await get_current_workspace_object(
        db, user_id=current_user_id, thread_id=thread_id,
    )
    return {"success": True, "data": public_snapshot(
        snapshot,
        thread_id=thread_id,
        workspace_object_id=workspace_object.id if workspace_object else None,
    )}


@router.patch("/{thread_id}")
async def control_thread_perspective(
    thread_id: str,
    request: PerspectiveControlRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _require_owned_thread(db, current_user_id, thread_id)
    try:
        snapshot = await apply_user_control(
            db,
            user_id=current_user_id,
            thread_id=thread_id,
            field_name=request.field,
            action=request.action,
            expected_revision=request.expected_revision,
            value=request.value,
        )
        workspace_object = await sync_workspace_object_from_perspective(
            db,
            user_id=current_user_id,
            thread_id=thread_id,
            perspective=snapshot,
        )
        await db.commit()
        await db.refresh(snapshot)
        if workspace_object is not None:
            await db.refresh(workspace_object)
        return {"success": True, "data": public_snapshot(
            snapshot,
            thread_id=thread_id,
            workspace_object_id=workspace_object.id if workspace_object else None,
        )}
    except SharedPerspectiveError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
