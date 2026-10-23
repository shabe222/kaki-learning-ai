"""Authenticated Phase 2A long-term memory endpoints."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from services.memory_service import (
    build_explicit_memory_candidate,
    delete_confirmed_memory,
    list_confirmed_memories,
    upsert_confirmed_memory,
)
from utils.auth import get_current_user_id


router = APIRouter()


class MemoryUpsertRequest(BaseModel):
    value: Any
    evidence_text: str = Field(default="", max_length=160)
    source_thread_id: str = Field(min_length=1, max_length=100)
    source_message_id: str = Field(min_length=1, max_length=100)
    confirmed: bool = True


class MemoryFromTextRequest(BaseModel):
    text: str = Field(min_length=2, max_length=300)
    source_thread_id: str = Field(min_length=1, max_length=100)
    source_message_id: str = Field(min_length=1, max_length=100)


def _serialize(item):
    return {
        "id": item.id,
        "memory_type": item.memory_type,
        "key": item.key,
        "value": item.value,
        "status": item.status,
        "evidence_text": item.evidence_text,
        "source_thread_id": item.source_thread_id,
        "source_message_id": item.source_message_id,
        "schema_version": item.schema_version,
        "created_at": str(item.created_at),
        "updated_at": str(item.updated_at),
    }


@router.get("/api/chat/memories")
async def list_memories(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    items = await list_confirmed_memories(db, current_user_id)
    return {"success": True, "data": [_serialize(item) for item in items]}


@router.post("/api/chat/memories/from-text")
async def save_memory_from_text(
    payload: MemoryFromTextRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    candidate = build_explicit_memory_candidate(
        payload.text,
        payload.source_thread_id,
        payload.source_message_id,
    )
    if candidate is None:
        raise HTTPException(
            status_code=422,
            detail="这条内容包含不适合作为长期记忆保存的信息。",
        )
    try:
        item = await upsert_confirmed_memory(
            db=db,
            user_id=current_user_id,
            key=candidate["key"],
            value=candidate["value"],
            evidence_text=candidate["evidence_text"],
            source_thread_id=candidate["source_thread_id"],
            source_message_id=candidate["source_message_id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "success": True,
        "data": _serialize(item),
        "interpretation": candidate["display_text"],
    }


@router.put("/api/chat/memories/{key}")
async def upsert_memory(
    key: str,
    payload: MemoryUpsertRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="Memory requires explicit confirmation")
    try:
        item = await upsert_confirmed_memory(
            db=db,
            user_id=current_user_id,
            key=key,
            value=payload.value,
            evidence_text=payload.evidence_text,
            source_thread_id=payload.source_thread_id,
            source_message_id=payload.source_message_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"success": True, "data": _serialize(item)}


@router.delete("/api/chat/memories/{memory_id}")
async def delete_memory(
    memory_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    deleted = await delete_confirmed_memory(db, current_user_id, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True, "data": {"id": memory_id, "deleted": True}}
