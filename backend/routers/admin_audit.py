"""管理端审计日志路由"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from utils.auth import get_current_admin_id
from services.admin_audit import list_audit_logs

router = APIRouter()


@router.get("/api/admin/audit-logs")
async def api_admin_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin_id: int = Query(None),
    action: str = Query(""),
    target_type: str = Query(""),
    current_admin_id: int = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db),
):
    items, total = await list_audit_logs(
        db, page=page, page_size=page_size,
        admin_id=admin_id,
        action_filter=action or None,
        target_type=target_type or None,
    )
    return {
        "success": True,
        "data": {"items": items, "total": total, "page": page, "page_size": page_size},
    }
