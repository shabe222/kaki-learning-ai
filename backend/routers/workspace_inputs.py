"""Authenticated OCR and ASR upload endpoints for the AI workspace composer."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from services.workspace_input_adapters import (
    MAX_AUDIO_BYTES,
    MAX_IMAGE_BYTES,
    InputAdapterError,
    input_adapter_capabilities,
    ocr_image_bytes,
    transcribe_audio_bytes,
)
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/chat/input", tags=["workspace-input"])


@router.get("/capabilities")
async def workspace_input_capabilities(
    current_user_id: int = Depends(get_current_user_id),
):
    del current_user_id
    return {"success": True, "data": input_adapter_capabilities()}


def _error_status(error: InputAdapterError) -> int:
    message = str(error)
    if "不能超过" in message:
        return 413
    if "格式不支持" in message:
        return 415
    if "尚未安装" in message:
        return 503
    return 422


async def _read_upload(file: UploadFile, limit: int) -> bytes:
    payload = await file.read(limit + 1)
    if len(payload) > limit:
        label = "图片" if limit == MAX_IMAGE_BYTES else "音频"
        raise HTTPException(status_code=413, detail=f"{label}不能超过 {limit // (1024 * 1024)} MB。")
    return payload


@router.post("/ocr")
async def recognize_workspace_image(
    file: UploadFile = File(...),
    current_user_id: int = Depends(get_current_user_id),
):
    del current_user_id
    try:
        payload = await _read_upload(file, MAX_IMAGE_BYTES)
        result = await asyncio.to_thread(
            ocr_image_bytes,
            payload,
            content_type=file.content_type or "",
        )
    except InputAdapterError as error:
        raise HTTPException(status_code=_error_status(error), detail=str(error)) from error
    finally:
        await file.close()
    return {
        "success": True,
        "data": {
            "text": result.text,
            "engine": result.engine,
            "confidence": result.confidence,
            "line_count": result.line_count,
        },
    }


@router.post("/transcribe")
async def transcribe_workspace_audio(
    file: UploadFile = File(...),
    current_user_id: int = Depends(get_current_user_id),
):
    del current_user_id
    try:
        payload = await _read_upload(file, MAX_AUDIO_BYTES)
        result = await transcribe_audio_bytes(
            payload,
            content_type=file.content_type or "",
            suffix=Path(file.filename or "recording.webm").suffix,
        )
    except InputAdapterError as error:
        raise HTTPException(status_code=_error_status(error), detail=str(error)) from error
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


@router.post("/blackboard-recognize")
async def recognize_blackboard_handwriting(
    file: UploadFile = File(...),
    current_user_id: int = Depends(get_current_user_id),
):
    """黑板手写识别：接收画布截图（PNG），OCR 识别手写内容并返回文本。

    供黑板手写确认闭环使用：前端 canvas 提交 → 这里识别 → 结果回传给 DARYA。
    """
    del current_user_id
    try:
        payload = await _read_upload(file, MAX_IMAGE_BYTES)
        result = await asyncio.to_thread(
            ocr_image_bytes,
            payload,
            content_type=file.content_type or "image/png",
        )
    except InputAdapterError as error:
        raise HTTPException(status_code=_error_status(error), detail=str(error)) from error
    finally:
        await file.close()
    return {
        "success": True,
        "data": {
            "text": result.text,
            "engine": result.engine,
            "confidence": result.confidence,
            "line_count": result.line_count,
        },
    }
