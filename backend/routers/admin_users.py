"""管理端用户管理路由"""
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from utils.auth import get_current_admin_id, require_admin_role
from services.admin_users import list_users, get_user_detail, set_user_status
from services.admin_audit import write_audit_log
from schemas.admin import AdminUserListResponse, AdminUserDetailResponse, AdminUserStatusRequest

router = APIRouter()


@router.get("/api/admin/users")
async def api_admin_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(""),
    status: str = Query("all"),
    profile: str = Query("all"),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    current_admin_id: int = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db),
):
    items, total = await list_users(
        db, page=page, page_size=page_size,
        keyword=keyword or None,
        status_filter=None if status == "all" else status,
        profile_filter=None if profile == "all" else profile,
        sort_by=sort_by, sort_order=sort_order,
    )
    return {
        "success": True,
        "data": AdminUserListResponse(items=items, total=total, page=page, page_size=page_size).model_dump(),
    }


@router.get("/api/admin/users/{user_id}")
async def api_admin_user_detail(
    user_id: int,
    current_admin_id: int = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db),
):
    detail = await get_user_detail(db, user_id)
    if not detail:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"success": True, "data": detail}


@router.put("/api/admin/users/{user_id}/status")
async def api_admin_user_status(
    user_id: int,
    req: AdminUserStatusRequest,
    current_admin_id: int = Depends(require_admin_role("super_admin", "operations")),
    db: AsyncSession = Depends(get_db),
):
    detail = await get_user_detail(db, user_id)
    if not detail:
        raise HTTPException(status_code=404, detail="用户不存在")

    action_label = "启用" if req.is_active else "停用"
    ok = await set_user_status(db, user_id, req.is_active)
    if not ok:
        raise HTTPException(status_code=500, detail="操作失败")

    await write_audit_log(
        db, admin_id=current_admin_id,
        action=f"user_{'enable' if req.is_active else 'disable'}",
        target_type="user", target_id=str(user_id),
        success=True,
        summary=f"账户启用 {detail['is_active']} → {req.is_active}；用户 ID {user_id}；原因：{req.reason.strip()}",
    )

    return {"success": True, "message": f"用户已{action_label}"}
