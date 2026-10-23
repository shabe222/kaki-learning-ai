"""Authenticated Artifact Action proposal and confirmation API."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import ChatSession
from services.tide_artifact_action import (
    ArtifactActionError,
    apply_artifact_action_control,
    create_artifact_action_proposal,
    get_latest_artifact_action,
    public_artifact_action,
)
from services.tide_workspace_object import public_workspace_object
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/tide/artifact-action", tags=["tide-artifact-action"])


class AnalyzeArtifactRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=1000)
    request_key: str = Field(min_length=1, max_length=240)


class ArtifactActionControlRequest(BaseModel):
    control: Literal["confirm", "cancel"]
    expected_revision: int = Field(ge=1)


async def _require_owned_thread(db: AsyncSession, user_id: int, thread_id: str) -> None:
    exists = (await db.execute(select(ChatSession.id).where(
        ChatSession.user_id == user_id,
        ChatSession.thread_id == thread_id,
    ).limit(1))).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="这段对话不存在或不可用。")


@router.get("/{thread_id}")
async def get_thread_artifact_action(
    thread_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _require_owned_thread(db, current_user_id, thread_id)
    value = await get_latest_artifact_action(db, user_id=current_user_id, thread_id=thread_id)
    return {"success": True, "data": public_artifact_action(value)}


@router.post("/{thread_id}/analyze")
async def analyze_thread_artifact(
    thread_id: str,
    request: AnalyzeArtifactRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _require_owned_thread(db, current_user_id, thread_id)
    try:
        value = await create_artifact_action_proposal(
            db,
            user_id=current_user_id,
            thread_id=thread_id,
            instruction=request.instruction,
            request_key=request.request_key,
        )
        await db.commit()
        if value is not None:
            await db.refresh(value)
        return {"success": True, "data": public_artifact_action(value)}
    except ArtifactActionError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.patch("/{thread_id}/{action_id}")
async def control_thread_artifact_action(
    thread_id: str,
    action_id: int,
    request: ArtifactActionControlRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _require_owned_thread(db, current_user_id, thread_id)
    try:
        action, workspace_object = await apply_artifact_action_control(
            db,
            user_id=current_user_id,
            thread_id=thread_id,
            action_id=action_id,
            control=request.control,
            expected_revision=request.expected_revision,
        )
        await db.commit()
        await db.refresh(action)
        await db.refresh(workspace_object)
        return {
            "success": True,
            "data": {
                "action": public_artifact_action(action),
                "workspace_object": public_workspace_object(workspace_object),
            },
        }
    except ArtifactActionError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            503,
            detail={"code": "ARTIFACT_ACTION_FAILED", "message": "新版本创建失败，原版本保持不变。"},
        ) from exc
