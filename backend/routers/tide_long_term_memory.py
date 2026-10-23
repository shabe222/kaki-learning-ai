"""Authenticated DARYA Phase 2.9 long-term-memory routes."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from services.tide_long_term_memory import (
    LongTermMemoryConflict,
    LongTermMemoryError,
    control_memory,
    list_active_memories,
    memory_snapshot,
    promote_context_claim,
    select_relevant_memories,
)
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/tide/long-term-memory", tags=["tide-long-term-memory"])


class PromoteClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=100)
    claim_id: int = Field(gt=0)


class MemoryControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(correct|forget)$")
    expected_revision: int = Field(ge=1)
    content: str | None = Field(default=None, max_length=500)


def _status(error: Exception) -> int:
    if isinstance(error, LongTermMemoryConflict):
        return 409
    if "不存在" in str(error) or "无权" in str(error):
        return 404
    return 422


@router.get("")
async def read_memories(
    query: str | None = Query(default=None, max_length=500),
    all: bool = Query(default=False),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if all:
        items = await list_active_memories(db, user_id=current_user_id)
        reason = "inspect"
    elif query:
        items = await select_relevant_memories(
            db, user_id=current_user_id, message=query, mark_used=False,
        )
        reason = "relevant"
    else:
        items = []
        reason = "contextual_empty"
    return {"success": True, "data": memory_snapshot(items, reason=reason)}


@router.post("/from-context-claim")
async def save_from_context_claim(
    request: PromoteClaimRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await promote_context_claim(
            db,
            user_id=current_user_id,
            thread_id=request.thread_id,
            claim_id=request.claim_id,
        )
    except LongTermMemoryError as error:
        raise HTTPException(status_code=_status(error), detail=str(error)) from error
    return {"success": True, "data": memory_snapshot([item], reason="saved")}


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: int,
    request: MemoryControlRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        replacement = await control_memory(
            db,
            user_id=current_user_id,
            memory_id=memory_id,
            action=request.action,
            expected_revision=request.expected_revision,
            content=request.content,
        )
        items = [replacement] if replacement is not None else []
    except LongTermMemoryError as error:
        raise HTTPException(status_code=_status(error), detail=str(error)) from error
    return {"success": True, "data": memory_snapshot(items, reason=request.action)}
