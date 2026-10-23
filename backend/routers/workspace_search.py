"""Authenticated authoritative search and search-result material import."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from services.workspace_search import (
    SearchError,
    append_search_result_message,
    get_search_result_set,
    import_search_result,
    persist_search_results,
    search_authoritative,
)
from services.workspace_task_contracts import AgentType, TaskState
from services.workspace_task_store import complete_task, fail_task, load_task_by_id
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/chat/searches", tags=["workspace-search"])


class WorkspaceSearchRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=240)
    source_scope: str = Field(default="official_first", pattern=r"^(official_first|academic|all)$")
    limit: int = Field(default=6, ge=1, le=10)
    task_id: str | None = Field(default=None, max_length=64)
    task_version: int | None = Field(default=None, ge=1)


class SearchImportRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    result_id: str = Field(min_length=8, max_length=80)


def _material_payload(item) -> dict:
    metadata = dict(item.parse_metadata or {})
    return {
        "material_id": item.material_id,
        "file_name": item.file_name,
        "file_type": item.file_type,
        "mime_type": item.mime_type,
        "size_bytes": item.size_bytes,
        "parse_status": item.parse_status,
        "parse_error": item.parse_error,
        "page_count": item.page_count,
        "section_count": item.section_count,
        "char_count": item.char_count,
        "structure": item.structure or [],
        "origin": metadata.get("origin", "upload"),
        "source_url": metadata.get("source_url", ""),
        "source_name": metadata.get("source_name", ""),
        "source_kind": metadata.get("source_kind", ""),
        "provider": metadata.get("provider", ""),
        "source_content_kind": metadata.get("source_content_kind", ""),
        "search_id": metadata.get("search_id", ""),
        "result_id": metadata.get("result_id", ""),
        "search_query": metadata.get("search_query", ""),
        "created_at": str(item.created_at),
        "updated_at": str(item.updated_at),
    }


@router.post("")
async def create_workspace_search(
    payload: WorkspaceSearchRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    task = None
    if payload.task_id:
        task = await load_task_by_id(db, current_user_id, payload.thread_id, payload.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="搜索任务不存在")
        if task.agent_type != AgentType.SEARCH:
            raise HTTPException(status_code=409, detail="任务类型已经改变，请重新确认")
        if payload.task_version != task.version:
            raise HTTPException(status_code=409, detail="任务参数已经改变，请确认最新版本")
        if task.state != TaskState.RUNNING or task.confirmed_version != task.version:
            raise HTTPException(status_code=409, detail="搜索任务尚未确认")
        query = str(task.slots.get("query") or payload.query)
        source_scope = str(task.slots.get("source_scope") or payload.source_scope)
        limit = int(task.slots.get("limit") or payload.limit)
    else:
        query, source_scope, limit = payload.query, payload.source_scope, payload.limit
    try:
        results = await search_authoritative(
            query,
            source_scope=source_scope,
            limit=limit,
        )
        saved = await persist_search_results(
            db,
            user_id=current_user_id,
            thread_id=payload.thread_id,
            query=query,
            results=results,
            source_scope=source_scope,
        )
        await append_search_result_message(
            db, user_id=current_user_id, thread_id=payload.thread_id, search=saved,
        )
    except SearchError as error:
        if task is not None:
            await fail_task(db, task, error_code="SEARCH_FAILED")
        raise HTTPException(status_code=422, detail=str(error)) from error
    if task is not None:
        await complete_task(db, task, artifact_id=int(saved["artifact_id"]))
    return {"success": True, "data": saved}


@router.get("/{search_id}")
async def get_workspace_search(
    search_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    saved = await get_search_result_set(db, user_id=current_user_id, search_id=search_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="搜索记录不存在")
    return {"success": True, "data": saved}


@router.post("/{search_id}/import")
async def import_workspace_search_result(
    search_id: str,
    payload: SearchImportRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        material = await import_search_result(
            db,
            user_id=current_user_id,
            thread_id=payload.thread_id,
            search_id=search_id,
            result_id=payload.result_id,
        )
    except SearchError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"success": True, "data": _material_payload(material)}
