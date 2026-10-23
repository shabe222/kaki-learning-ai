"""Authenticated Phase 2.8 document-to-user-context routes."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from services.tide_user_context import (
    MAX_USER_CONTEXT_BYTES,
    UserContextError,
    UserContextRevisionConflict,
    apply_user_context_control,
    get_user_context_snapshot,
    ingest_user_context_document,
)
from services.workspace_materials import MaterialError, MaterialTooLargeError, MaterialTypeError
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/tide/user-context", tags=["tide-user-context"])


class ClaimControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(confirm|correct|reject)$")
    expected_revision: int = Field(ge=1)
    value: str | None = Field(default=None, max_length=500)


def _status(error: Exception) -> int:
    if isinstance(error, (MaterialTooLargeError,)) or "不能超过" in str(error):
        return 413
    if isinstance(error, MaterialTypeError) or "仅支持" in str(error) or "格式不支持" in str(error):
        return 415
    if isinstance(error, UserContextRevisionConflict):
        return 409
    return 422


@router.post("/{thread_id}/documents")
async def upload_user_context_document(
    thread_id: str,
    file: UploadFile = File(...),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    payload = await file.read(MAX_USER_CONTEXT_BYTES + 1)
    await file.close()
    try:
        snapshot = await ingest_user_context_document(
            db,
            user_id=current_user_id,
            thread_id=thread_id,
            file_name=file.filename or "upload.bin",
            content_type=file.content_type or "application/octet-stream",
            payload=payload,
        )
    except (UserContextError, MaterialError) as error:
        raise HTTPException(status_code=_status(error), detail=str(error)) from error
    return {"success": True, "data": snapshot}


@router.get("/{thread_id}")
async def read_user_context(
    thread_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        snapshot = await get_user_context_snapshot(db, user_id=current_user_id, thread_id=thread_id)
    except UserContextError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"success": True, "data": snapshot}


@router.patch("/{thread_id}/claims/{claim_id}")
async def control_user_context_claim(
    thread_id: str,
    claim_id: int,
    request: ClaimControlRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        snapshot = await apply_user_context_control(
            db,
            user_id=current_user_id,
            thread_id=thread_id,
            claim_id=claim_id,
            action=request.action,
            expected_revision=request.expected_revision,
            value=request.value,
        )
    except UserContextError as error:
        raise HTTPException(status_code=_status(error), detail=str(error)) from error
    return {"success": True, "data": snapshot}


class UrlIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=4, max_length=2000)


@router.post("/{thread_id}/url")
async def ingest_user_context_url(
    thread_id: str,
    request: UrlIngestRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Import a web page as source material, through the same ingestion path as files."""
    from services.web_ingestion import WebIngestionError, ingest_url_as_material

    try:
        result = await ingest_url_as_material(
            db, user_id=current_user_id, thread_id=thread_id, url=request.url,
        )
    except WebIngestionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (UserContextError, MaterialError) as error:
        raise HTTPException(status_code=_status(error), detail=str(error)) from error
    return {"success": True, "data": result}
