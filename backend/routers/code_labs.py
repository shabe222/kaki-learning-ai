from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from utils.auth import get_current_user_id
from services.code_lab_submissions import (
    CodeLabError, get_code_lab_result, list_code_lab_submissions,
    replan_after_code_submission, submit_code_lab,
)


router = APIRouter(prefix="/api/code-labs", tags=["code-labs"])


class CodeLabSubmitRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    artifact_version_id: int = Field(ge=1)
    source: str = Field(min_length=1, max_length=24576)
    submission_request_id: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=240)


class CodeLabReplanRequest(BaseModel):
    artifact_version_id: int = Field(ge=1)
    attempt_id: int = Field(ge=1)


def _raise(exc: CodeLabError):
    raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


@router.post("/{artifact_id}/submit")
async def submit(
    artifact_id: int, req: CodeLabSubmitRequest,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    try:
        result, replayed = await submit_code_lab(
            db, user_id=user_id, thread_id=req.thread_id, artifact_id=artifact_id,
            artifact_version_id=req.artifact_version_id, source=req.source,
            submission_request_id=req.submission_request_id, idempotency_key=req.idempotency_key,
        )
        await db.commit()
        return {"success": True, "data": {**result, "idempotent_replay": replayed}}
    except CodeLabError as exc:
        await db.rollback(); _raise(exc)


@router.get("/{artifact_id}/submissions")
async def submissions(
    artifact_id: int, artifact_version_id: int,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    try:
        return {"success": True, "data": await list_code_lab_submissions(
            db, user_id=user_id, artifact_id=artifact_id, artifact_version_id=artifact_version_id,
        )}
    except CodeLabError as exc:
        _raise(exc)


@router.post("/{artifact_id}/replan")
async def replan(
    artifact_id: int, req: CodeLabReplanRequest,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    try:
        result = await replan_after_code_submission(
            db, user_id=user_id, artifact_id=artifact_id,
            artifact_version_id=req.artifact_version_id, attempt_id=req.attempt_id,
        )
        await db.commit()
        return {"success": True, "data": result}
    except CodeLabError as exc:
        await db.rollback(); _raise(exc)


@router.get("/{artifact_id}/result")
async def result(
    artifact_id: int, artifact_version_id: int,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    try:
        return {"success": True, "data": await get_code_lab_result(
            db, user_id=user_id, artifact_id=artifact_id, artifact_version_id=artifact_version_id,
        )}
    except CodeLabError as exc:
        _raise(exc)
