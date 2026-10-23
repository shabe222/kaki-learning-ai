"""Authenticated PC05A surface over existing DARYA understanding authorities."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from services.darya_self_understanding import build_self_understanding
from services.tide_long_term_memory import (
    LongTermMemoryConflict,
    LongTermMemoryError,
    control_memory,
    correct_memory_from_user,
)
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/darya/self-understanding", tags=["darya-self-understanding"])


class UnderstandingMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(correct|forget)$")
    expected_revision: int = Field(ge=1)
    correction: str | None = Field(default=None, min_length=2, max_length=500)


@router.get("")
async def read_self_understanding(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return {"success": True, "data": await build_self_understanding(db, user_id=current_user_id)}


@router.patch("/memories/{memory_id}")
async def update_understanding_memory(
    memory_id: int,
    request: UnderstandingMemoryRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        if request.action == "correct":
            if not request.correction:
                raise LongTermMemoryError("请写下更准确的说法。")
            await correct_memory_from_user(
                db,
                user_id=current_user_id,
                memory_id=memory_id,
                expected_revision=request.expected_revision,
                correction=request.correction,
            )
        else:
            await control_memory(
                db,
                user_id=current_user_id,
                memory_id=memory_id,
                action="forget",
                expected_revision=request.expected_revision,
            )
    except LongTermMemoryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LongTermMemoryError as error:
        status = 404 if "不存在" in str(error) or "无权" in str(error) else 422
        raise HTTPException(status_code=status, detail=str(error)) from error
    return {"success": True, "data": await build_self_understanding(db, user_id=current_user_id)}
