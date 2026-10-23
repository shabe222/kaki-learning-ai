"""Authenticated study-time routes: the heartbeat and the summary it feeds."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from services.study_time import MAX_REPORT_SECONDS, record, summary
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/darya/study-time", tags=["darya-study-time"])


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The client reports the seconds it just observed, not a running total, so a
    # retry or a dropped tab cannot double-count.
    seconds: int = Field(gt=0, le=MAX_REPORT_SECONDS)


@router.post("/heartbeat")
async def study_time_heartbeat(
    payload: HeartbeatRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Record presence. Called while the tab is visible and the person is here."""
    today_total = await record(db, user_id=current_user_id, seconds=payload.seconds)
    await db.commit()
    return {"ok": True, "today_seconds": today_total}


@router.get("/summary")
async def study_time_summary(
    days: int = Query(default=30, ge=7, le=400),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Everything the timer page needs, in one request."""
    return await summary(db, user_id=current_user_id, days=days)
