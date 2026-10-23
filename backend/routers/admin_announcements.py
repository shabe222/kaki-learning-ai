"""管理端公告管理路由 + 用户端公告查询"""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from utils.auth import get_current_admin_id, get_current_user_id, require_admin_role
from services.admin_announcements import (
    list_announcements, create_announcement, update_announcement,
    publish_announcement, revoke_announcement,
    get_active_announcements_for_user, mark_announcement_read,
    _announcement_to_dict,
)
from services.admin_audit import write_audit_log
from schemas.admin import AnnouncementCreateRequest, AnnouncementUpdateRequest

router = APIRouter()


@router.get("/api/admin/announcements/{announcement_id}")
async def api_admin_announcement(announcement_id: int, current_admin_id: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    from services.admin_announcements import get_announcement
    item = await get_announcement(db, announcement_id)
    if item is None: raise HTTPException(404, "公告不存在")
    return {"success": True, "data": _announcement_to_dict(item)}


# ===== 管理端公告接口 =====

@router.get("/api/admin/announcements")
async def api_admin_announcements(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all"),
    current_admin_id: int = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db),
):
    items, total = await list_announcements(
        db, page=page, page_size=page_size,
        status_filter=None if status == "all" else status,
    )
    return {
        "success": True,
        "data": {
            "items": [_announcement_to_dict(a) for a in items],
            "total": total, "page": page, "page_size": page_size,
        },
    }


@router.post("/api/admin/announcements")
async def api_admin_create_announcement(
    req: AnnouncementCreateRequest,
    current_admin_id: int = Depends(require_admin_role("super_admin", "operations")),
    db: AsyncSession = Depends(get_db),
):
    if not req.title.strip() or not req.content.strip():
        raise HTTPException(status_code=400, detail="标题和内容不能为空")
    if req.level not in {"info", "important", "warning", "success"}:
        raise HTTPException(422, "公告级别无效")
    if req.expires_at is not None:
        req.expires_at = req.expires_at.replace(tzinfo=None) if req.expires_at.tzinfo is None else req.expires_at.astimezone(timezone.utc).replace(tzinfo=None)
        if req.expires_at <= datetime.utcnow(): raise HTTPException(422, "到期时间必须晚于当前时间")

    ann = await create_announcement(
        db, admin_id=current_admin_id,
        title=req.title.strip(), content=req.content.strip(),
        level=req.level, expires_at=req.expires_at,
    )
    await write_audit_log(
        db, admin_id=current_admin_id,
        action="announcement_create", target_type="announcement",
        target_id=str(ann.id), success=True,
        summary=f"创建公告草稿: {ann.title}",
    )
    return {"success": True, "data": _announcement_to_dict(ann)}


@router.put("/api/admin/announcements/{announcement_id}")
async def api_admin_update_announcement(
    announcement_id: int,
    req: AnnouncementUpdateRequest,
    current_admin_id: int = Depends(require_admin_role("super_admin", "operations")),
    db: AsyncSession = Depends(get_db),
):
    updates = {}
    if req.title is not None:
        updates["title"] = req.title.strip()
    if req.content is not None:
        updates["content"] = req.content.strip()
    if req.level is not None:
        updates["level"] = req.level
    if "expires_at" in req.model_fields_set:
        updates["expires_at"] = req.expires_at.astimezone(timezone.utc).replace(tzinfo=None) if req.expires_at and req.expires_at.tzinfo else req.expires_at
        if updates["expires_at"] and updates["expires_at"] <= datetime.utcnow(): raise HTTPException(422, "到期时间必须晚于当前时间")
    if any(k in updates and not updates[k] for k in ("title", "content")): raise HTTPException(422, "标题和正文不能为空")
    if "level" in updates and updates["level"] not in {"info", "important", "warning", "success"}: raise HTTPException(422, "公告级别无效")

    ann = await update_announcement(db, announcement_id, admin_id=current_admin_id, **updates)
    if not ann:
        raise HTTPException(status_code=404, detail="公告不存在")
    await write_audit_log(
        db, admin_id=current_admin_id,
        action="announcement_edit", target_type="announcement",
        target_id=str(ann.id), success=True,
        summary=f"编辑公告: {ann.title}",
    )
    return {"success": True, "data": _announcement_to_dict(ann)}


@router.post("/api/admin/announcements/{announcement_id}/publish")
async def api_admin_publish_announcement(
    announcement_id: int,
    current_admin_id: int = Depends(require_admin_role("super_admin", "operations")),
    db: AsyncSession = Depends(get_db),
):
    from services.admin_announcements import get_announcement
    existing = await get_announcement(db, announcement_id)
    if existing and existing.expires_at and existing.expires_at <= datetime.utcnow(): raise HTTPException(409, "公告已到期，请先调整到期时间")
    ann = await publish_announcement(db, announcement_id, current_admin_id)
    if not ann:
        raise HTTPException(status_code=404, detail="公告不存在")
    await write_audit_log(
        db, admin_id=current_admin_id,
        action="announcement_publish", target_type="announcement",
        target_id=str(ann.id), success=True,
        summary=f"发布公告: {ann.title}",
    )
    return {"success": True, "data": _announcement_to_dict(ann)}


@router.post("/api/admin/announcements/{announcement_id}/revoke")
async def api_admin_revoke_announcement(
    announcement_id: int,
    current_admin_id: int = Depends(require_admin_role("super_admin", "operations")),
    db: AsyncSession = Depends(get_db),
):
    ann = await revoke_announcement(db, announcement_id, current_admin_id)
    if not ann:
        raise HTTPException(status_code=404, detail="公告不存在")
    await write_audit_log(
        db, admin_id=current_admin_id,
        action="announcement_revoke", target_type="announcement",
        target_id=str(ann.id), success=True,
        summary=f"撤回公告: {ann.title}",
    )
    return {"success": True, "data": _announcement_to_dict(ann)}


# ===== 用户端公告接口 =====

@router.get("/api/announcements/active")
async def api_user_active_announcements(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    items = await get_active_announcements_for_user(db, current_user_id)
    unread_count = sum(1 for i in items if not i["is_read"])
    return {"success": True, "data": {"items": items, "unread_count": unread_count}}


@router.post("/api/announcements/{announcement_id}/read")
async def api_user_mark_read(
    announcement_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    ok = await mark_announcement_read(db, current_user_id, announcement_id)
    if not ok:
        raise HTTPException(status_code=404, detail="公告不存在")
    return {"success": True}
