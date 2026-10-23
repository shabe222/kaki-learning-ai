"""管理员总览接口 - 平台聚合统计数据"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from utils.auth import get_current_admin_id
from services.admin_dashboard import (
    get_core_metrics, get_weekly_trend, get_recent_tasks, get_system_status,
)

router = APIRouter()


@router.get("/api/admin/dashboard")
async def api_admin_dashboard(
    current_admin_id: int = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db),
):
    core = await get_core_metrics(db)
    trend = await get_weekly_trend(db)
    recent = await get_recent_tasks(db)
    sys_status = await get_system_status(db)

    return {
        "success": True,
        "message": "获取总览数据成功",
        "data": {
            "core_metrics": core,
            "weekly_trend": trend,
            "recent_tasks": recent,
            "system_status": sys_status,
        },
    }
