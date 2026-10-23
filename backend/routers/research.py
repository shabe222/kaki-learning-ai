"""Long-running research as a job: create, watch, read, retry.

Mirrors the video job surface on purpose — same lifecycle words, same
owner-bound artifact, same explicit failure reason — so a learner sees one
pattern for "this takes a while" instead of two.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import GeneratedItem
from services.research_jobs import (
    RESEARCH_ITEM_TYPE,
    create_research_job,
    project_research_job,
    research_summary,
    run_research_job,
)
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/chat/tasks/research", tags=["research"])


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=4, max_length=400)
    depth: str = Field(default="standard", pattern="^(quick|standard|deep)$")
    confirmed: bool = True


async def _owned(db: AsyncSession, *, user_id: int, job_id: int) -> GeneratedItem:
    item = await db.scalar(select(GeneratedItem).where(
        GeneratedItem.id == job_id,
        GeneratedItem.user_id == user_id,
        GeneratedItem.type == RESEARCH_ITEM_TYPE,
    ))
    if item is None:
        raise HTTPException(404, {"code": "RESEARCH_NOT_FOUND", "message": "这个研究任务不存在或不属于你。"})
    return item


@router.post("")
async def start_research(
    request: ResearchRequest,
    background: BackgroundTasks,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Queue a research job. It keeps running after this request returns."""
    item = await create_research_job(
        db, user_id=user_id, thread_id=request.thread_id,
        question=request.question, depth=request.depth,
    )
    await db.commit()
    job_id = item.id
    background.add_task(_run_detached, job_id)
    projection = project_research_job(item)
    return {
        "success": True,
        "data": {**projection, "summary": research_summary(projection)},
    }


async def _run_detached(job_id: int) -> None:
    """Run in its own session: the request's session is already closed."""
    from database.connection import session_scope

    async with session_scope() as session:
        try:
            await run_research_job(session, job_id=job_id)
            await session.commit()
        except Exception:  # noqa: BLE001 - 后台任务不能把异常抛回已结束的请求
            await session.rollback()


@router.get("")
async def list_research(
    thread_id: str = Query(..., min_length=1, max_length=100),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """这个会话里的研究任务，新的在前。

    加这个列表是因为「关掉研究条就回不去了」：× 只清掉浏览器里的引用，任务本身
    一直作为 `workspace_research` 躺在这里、也一直能重试，缺的只是把它列出来。
    刷新页面同样会丢引用（researchJob 只在内存里），所以回不了头有两条路径，
    一条是收起、一条是刷新，这个接口同时堵住两条。

    thread_id 存在 raw_data 里而不是列上，所以按 JSON 字段过滤；这是 SQLite 的
    json_extract，本项目的库支持。
    """
    stmt = (
        select(GeneratedItem)
        .where(
            GeneratedItem.user_id == user_id,
            GeneratedItem.type == RESEARCH_ITEM_TYPE,
            GeneratedItem.raw_data["thread_id"].as_string() == thread_id,
        )
        .order_by(desc(GeneratedItem.id))
        .limit(20)
    )
    rows = list((await db.execute(stmt)).scalars())
    items = []
    for item in rows:
        projection = project_research_job(item)
        items.append({**projection, "summary": research_summary(projection)})
    return {"success": True, "data": items}


@router.get("/{job_id}")
async def read_research(
    job_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned(db, user_id=user_id, job_id=job_id)
    projection = project_research_job(item)
    return {"success": True, "data": {**projection, "summary": research_summary(projection)}}


@router.get("/{job_id}/status")
async def research_status(
    job_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned(db, user_id=user_id, job_id=job_id)
    projection = project_research_job(item)
    return {"success": True, "data": {
        "artifact_id": projection["artifact_id"],
        "status": projection["status"],
        "stage_copy": projection["stage_copy"],
        "error": projection["error"],
    }}


@router.post("/{job_id}/retry")
async def retry_research(
    job_id: int,
    background: BackgroundTasks,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Retry a failed job. A ready report is never re-run or discarded."""
    item = await _owned(db, user_id=user_id, job_id=job_id)
    data = dict(item.raw_data or {})
    if str(data.get("status") or "") == "READY":
        raise HTTPException(409, {"code": "RESEARCH_ALREADY_READY", "message": "这份报告已经完成，不需要重跑。"})
    data.update({"status": "QUEUED", "stage_copy": "已重新排队。", "error": None})
    item.raw_data = data
    await db.commit()
    background.add_task(_run_detached, job_id)
    projection = project_research_job(item)
    return {"success": True, "data": {**projection, "summary": research_summary(projection)}}
