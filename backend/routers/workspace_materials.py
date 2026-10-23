"""Authenticated upload-material library and current-conversation binding routes."""
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import WorkspaceMaterial
from services.workspace_materials import (
    MAX_MATERIAL_BYTES,
    MaterialError,
    MaterialTooLargeError,
    MaterialTypeError,
    attach_workspace_material,
    create_workspace_material,
    delete_library_material,
    detach_workspace_material,
    list_conversation_materials,
    list_library_materials,
    normalize_material_category,
    normalize_material_tags,
)
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/chat/materials", tags=["workspace-materials"])


class MaterialMetadataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    category: Optional[str] = Field(default=None, max_length=60)
    tags: Optional[List[str]] = None


class MaterialBindingRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)


def _public_parse_error(item: WorkspaceMaterial) -> Optional[str]:
    if item.parse_status != "failed" or not item.parse_error:
        return None
    # Parsing errors are persisted for diagnostics, but the public projection stays
    # bounded and strips control characters rather than exposing traceback details.
    return " ".join(str(item.parse_error).replace("\x00", "").split())[:300]


def _serialize(item: WorkspaceMaterial):
    metadata = dict(item.parse_metadata or {})
    tags = normalize_material_tags(metadata.get("tags") or [])
    category = normalize_material_category(metadata.get("category"))
    return {
        "material_id": item.material_id,
        "file_name": item.file_name,
        "title": Path(item.file_name).stem,
        "file_type": item.file_type,
        "mime_type": item.mime_type,
        "size_bytes": item.size_bytes,
        "parse_status": item.parse_status,
        "parse_error": _public_parse_error(item),
        "page_count": item.page_count,
        "section_count": item.section_count,
        "char_count": item.char_count,
        "structure": item.structure or [],
        "preview_text": metadata.get("preview_text", ""),
        "category": category,
        "tags": tags,
        "learning_node_ids": metadata.get("learning_node_ids", []),
        "origin": metadata.get("origin", "upload"),
        "source_url": metadata.get("source_url", ""),
        "source_name": metadata.get("source_name", ""),
        "source_kind": metadata.get("source_kind", ""),
        "created_at": str(item.created_at),
        "updated_at": str(item.updated_at),
    }


def _status(error: MaterialError) -> int:
    if isinstance(error, MaterialTooLargeError):
        return 413
    if isinstance(error, MaterialTypeError):
        return 415
    return 422


@router.post("")
async def upload_workspace_material(
    thread_id: str = Form(..., min_length=1, max_length=100),
    file: UploadFile = File(...),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    payload = await file.read(MAX_MATERIAL_BYTES + 1)
    await file.close()
    if len(payload) > MAX_MATERIAL_BYTES:
        raise HTTPException(status_code=413, detail="文件不能超过 30 MB。")
    try:
        item = await create_workspace_material(
            db,
            user_id=current_user_id,
            thread_id=thread_id,
            file_name=file.filename or "upload.bin",
            content_type=file.content_type or "application/octet-stream",
            payload=payload,
        )
    except MaterialError as error:
        raise HTTPException(status_code=_status(error), detail=str(error)) from error
    return {"success": True, "data": _serialize(item)}


@router.get("")
async def list_workspace_materials(
    thread_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    items = await list_conversation_materials(db, current_user_id, thread_id)
    return {"success": True, "data": [_serialize(item) for item in items]}


@router.get("/library")
async def list_workspace_material_library(
    q: Optional[str] = Query(default=None, max_length=120),
    file_type: Optional[str] = Query(default=None, max_length=20),
    category: Optional[str] = Query(default=None, max_length=60),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await list_library_materials(
            db,
            current_user_id,
            query=q,
            file_type=file_type,
            category=category,
        )
    except MaterialError as error:
        raise HTTPException(status_code=_status(error), detail=str(error)) from error
    return {"success": True, "data": [_serialize(item) for item in items]}


async def _owned_material(db: AsyncSession, user_id: int, material_id: str):
    item = (await db.execute(select(WorkspaceMaterial).where(
        WorkspaceMaterial.material_id == material_id,
        WorkspaceMaterial.user_id == user_id,
    ))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="资料不存在或无权访问。")
    return item


@router.get("/{material_id}")
async def get_workspace_material_detail(
    material_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned_material(db, current_user_id, material_id)
    return {"success": True, "data": _serialize(item)}


@router.get("/{material_id}/download")
async def download_workspace_material(
    material_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned_material(db, current_user_id, material_id)
    path = Path(item.storage_path).resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="资料文件已缺失。")
    return FileResponse(path, media_type=item.mime_type, filename=item.file_name)


@router.delete("/{material_id}/bindings/{thread_id}")
async def detach_workspace_material_route(
    material_id: str,
    thread_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    # Ownership is checked independently so a second detach is idempotent while a
    # cross-user material ID still fails closed instead of looking like success.
    await _owned_material(db, current_user_id, material_id)
    detached = await detach_workspace_material(db, current_user_id, thread_id, material_id)
    return {"success": True, "data": {"material_id": material_id, "detached": detached}}


@router.post("/{material_id}/bindings")
async def attach_workspace_material_route(
    material_id: str,
    payload: MaterialBindingRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    attached = await attach_workspace_material(
        db, current_user_id, payload.thread_id, material_id,
    )
    if not attached:
        raise HTTPException(status_code=404, detail="资料不存在或无权访问。")
    return {"success": True, "data": {"material_id": material_id, "attached": True}}


@router.api_route("/{material_id}", methods=["PATCH", "PUT"])
async def update_workspace_material_metadata(
    material_id: str,
    payload: MaterialMetadataRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned_material(db, current_user_id, material_id)
    supplied = payload.model_dump(exclude_unset=True)
    if not supplied:
        raise HTTPException(status_code=422, detail="至少提供一个需要更新的资料字段。")

    requested_name = payload.file_name if "file_name" in supplied else payload.title if "title" in supplied else None
    if requested_name is not None:
        requested = Path(str(requested_name).replace("\x00", "")).name.strip()
        if not requested:
            raise HTTPException(status_code=422, detail="文件名无效。")
        original_suffix = Path(item.file_name).suffix
        requested_suffix = Path(requested).suffix
        if requested_suffix and requested_suffix.lower() != original_suffix.lower():
            raise HTTPException(status_code=422, detail="重命名不能改变资料文件类型。")
        item.file_name = requested if requested_suffix else requested + original_suffix

    metadata = dict(item.parse_metadata or {})
    try:
        if "category" in supplied:
            metadata["category"] = normalize_material_category(payload.category)
        if "tags" in supplied:
            metadata["tags"] = normalize_material_tags(payload.tags)
    except MaterialError as error:
        raise HTTPException(status_code=_status(error), detail=str(error)) from error
    item.parse_metadata = metadata

    await db.commit()
    await db.refresh(item)
    return {"success": True, "data": _serialize(item)}


@router.delete("/{material_id}")
async def delete_workspace_material(
    material_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    deleted = await delete_library_material(db, current_user_id, material_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="资料不存在或无权访问。")
    return {"success": True, "data": {"material_id": material_id, "deleted": True}}
