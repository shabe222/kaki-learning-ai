"""Authenticated inspection of DARYA's correctable current-thread perspective."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import ChatSession
from services.darya_perspective_engine import get_latest_perspective, public_perspective
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/tide/current-perspective", tags=["darya-current-perspective"])


@router.get("/{thread_id}")
async def get_thread_current_perspective(
    thread_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    owned = (await db.execute(select(ChatSession.id).where(
        ChatSession.user_id == current_user_id,
        ChatSession.thread_id == thread_id,
    ).limit(1))).scalar_one_or_none()
    if owned is None:
        raise HTTPException(status_code=404, detail="这段对话不存在或不可用。")
    snapshot = await get_latest_perspective(
        db, user_id=current_user_id, thread_id=thread_id,
    )
    return {"success": True, "data": public_perspective(snapshot, thread_id=thread_id)}
