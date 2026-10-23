"""Authenticated lifecycle for the isolated Kaki Xiaoji profile conversation."""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from schemas.profile_onboarding import (
    ProfileOnboardingConfirmRequest,
    ProfileOnboardingMessageRequest,
    ProfileOnboardingTaskRequest,
)
from services.profile_onboarding import (
    confirm_profile_onboarding,
    delete_onboarding_attachment,
    pause_profile_onboarding,
    send_profile_onboarding_message,
    skip_profile_onboarding,
    start_profile_onboarding,
    store_onboarding_attachment,
)
from services.workspace_input_adapters import (
    MAX_AUDIO_BYTES,
    MAX_IMAGE_BYTES,
    InputAdapterError,
    ocr_image_bytes,
    transcribe_audio_bytes,
)
from services.workspace_materials import (
    MAX_MATERIAL_BYTES,
    MaterialError,
    MaterialTooLargeError,
    MaterialTypeError,
    parse_material_bytes,
)
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/profile-onboarding", tags=["profile-onboarding"])


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(error, PermissionError):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))


@router.post("/start")
async def start(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return {"success": True, "data": await start_profile_onboarding(db, user_id)}


@router.post("/message")
async def message(
    request: ProfileOnboardingMessageRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await send_profile_onboarding_message(
            db, user_id, request.task_id, request.message,
            source_type=request.source_type,
            source_metadata=request.source_metadata,
        )
    except (PermissionError, LookupError, ValueError) as error:
        raise _translate_error(error) from error
    return {"success": True, "data": data}


@router.post("/confirm")
async def confirm(
    request: ProfileOnboardingConfirmRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await confirm_profile_onboarding(db, user_id, request.task_id, request.edits)
    except (PermissionError, LookupError, ValueError) as error:
        raise _translate_error(error) from error
    return {"success": True, "data": data}


@router.post("/pause")
async def pause(
    request: ProfileOnboardingTaskRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await pause_profile_onboarding(db, user_id, request.task_id)
    except (PermissionError, LookupError, ValueError) as error:
        raise _translate_error(error) from error
    return {"success": True, "data": data}


@router.post("/skip")
async def skip(
    request: ProfileOnboardingTaskRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await skip_profile_onboarding(db, user_id, request.task_id)
    except (PermissionError, LookupError, ValueError) as error:
        raise _translate_error(error) from error
    return {"success": True, "data": data}


# ---------- 多模态输入端点 ----------


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
):
    """上传音频文件，返回语音转写文本。复用 workspace ASR 能力。"""
    del user_id
    try:
        payload = await file.read(MAX_AUDIO_BYTES + 1)
        if len(payload) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail=f"音频不能超过 {MAX_AUDIO_BYTES // (1024 * 1024)} MB。")
        result = await transcribe_audio_bytes(
            payload,
            content_type=file.content_type or "",
            suffix=Path(file.filename or "recording.webm").suffix,
        )
    except InputAdapterError as error:
        status = 503 if "尚未安装" in str(error) else 422
        raise HTTPException(status_code=status, detail=str(error)) from error
    finally:
        await file.close()
    return {
        "success": True,
        "data": {
            "text": result.text,
            "engine": result.engine,
            "language": result.language,
            "duration_seconds": result.duration_seconds,
        },
    }


@router.post("/upload-material")
async def upload_onboarding_material(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
):
    """上传文件（PDF/DOCX/TXT/MD/图片），返回解析后的文本。复用 workspace materials 解析能力。"""
    del user_id
    try:
        payload = await file.read(MAX_MATERIAL_BYTES + 1)
        if len(payload) > MAX_MATERIAL_BYTES:
            raise HTTPException(status_code=413, detail=f"文件不能超过 {MAX_MATERIAL_BYTES // (1024 * 1024)} MB。")
        if not payload:
            raise HTTPException(status_code=422, detail="文件内容为空。")
        parsed = await asyncio.to_thread(
            parse_material_bytes,
            payload,
            file.filename or "upload.bin",
            file.content_type or "",
        )
    except MaterialTypeError as error:
        raise HTTPException(status_code=415, detail=str(error)) from error
    except MaterialTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except MaterialError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        await file.close()

    full_text = "\n\n".join(section.text for section in parsed.sections)
    return {
        "success": True,
        "data": {
            "text": full_text[:8000],
            "file_type": parsed.file_type,
            "file_name": file.filename,
            "page_count": parsed.page_count,
            "section_count": len(parsed.sections),
            "preview": "\n".join(section.heading for section in parsed.sections[:6]),
        },
    }


# ---------- 附件持久化端点 ----------

from pydantic import BaseModel, Field


class StoreAttachmentRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    file_name: str = Field(min_length=1, max_length=256)
    file_type: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=0, max_length=30000)
    page_count: int | None = None
    section_count: int = 0
    preview: str = Field(default="", max_length=500)


class DeleteAttachmentRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    attachment_id: str = Field(min_length=1, max_length=64)


@router.post("/attachment")
async def store_attachment(
    request: StoreAttachmentRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """持久化上传文件元信息到任务slots中，刷新后可恢复。"""
    try:
        att = await store_onboarding_attachment(
            db, user_id, request.task_id,
            file_name=request.file_name,
            file_type=request.file_type,
            text=request.text,
            page_count=request.page_count,
            section_count=request.section_count,
            preview=request.preview,
        )
    except (PermissionError, LookupError) as error:
        raise _translate_error(error) from error
    return {"success": True, "data": att}


@router.delete("/attachment/{task_id}/{attachment_id}")
async def delete_attachment(
    task_id: str,
    attachment_id: str,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """删除已上传文件附件。"""
    try:
        deleted = await delete_onboarding_attachment(
            db, user_id, task_id, attachment_id,
        )
    except (PermissionError, LookupError) as error:
        raise _translate_error(error) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="附件不存在")
    return {"success": True, "data": {"deleted": True}}
