"""管理端运营记录路由"""
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from utils.auth import get_current_admin_id
from services.admin_operations import list_operations, get_operation_detail, get_operations_stats
from schemas.admin import OperationListResponse, OperationDetailResponse

router = APIRouter()


@router.get("/api/admin/operations")
async def api_admin_operations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    agent_type: str = Query(""),
    status: str = Query(""),
    user_id: int = Query(None),
    start_date: str = Query(""),
    end_date: str = Query(""),
    error_category: str = Query(""),
    current_admin_id: int = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db),
):
    items, total = await list_operations(
        db, page=page, page_size=page_size,
        agent_type=agent_type or None,
        status_filter=status or None,
        user_id=user_id,
        start_date=start_date or None,
        end_date=end_date or None,
        error_category=error_category or None,
    )
    return {
        "success": True,
        "data": {"items": items, "total": total, "page": page, "page_size": page_size},
    }


@router.get("/api/admin/operations/{invocation_id}")
async def api_admin_operation_detail(
    invocation_id: int,
    current_admin_id: int = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db),
):
    detail = await get_operation_detail(db, invocation_id)
    if not detail:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": detail}


@router.get("/api/admin/operations-stats")
async def api_admin_operations_stats(
    current_admin_id: int = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db),
):
    """运营记录统计汇总（24h 统计、智能体分布、每日趋势、错误分类）"""
    stats = await get_operations_stats(db)
    return {"success": True, "data": stats}
