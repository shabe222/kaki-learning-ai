"""管理端系统状态路由"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from utils.auth import get_current_admin_id, require_admin_role
from services.admin_system import (
    get_system_info, get_current_config, update_system_config, clear_all_caches,
)
from services.admin_audit import write_audit_log

router = APIRouter()


class SystemConfigUpdateRequest(BaseModel):
    updates: dict  # {"LLM_PROVIDER": "deepseek", "MODE": "production", ...}


@router.get("/api/admin/system")
async def api_admin_system(
    request: Request,
    current_admin_id: int = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db),
):
    info = await get_system_info(db)
    info["api_routes"] = len(request.app.routes)
    return {"success": True, "data": info}


@router.get("/api/admin/system/config")
async def api_admin_system_config(
    current_admin_id: int = Depends(get_current_admin_id),
):
    """获取可配置项（运行时值 + .env 文件值）"""
    config = get_current_config()
    return {"success": True, "data": config}


@router.put("/api/admin/system/config")
async def api_admin_system_config_update(
    req: SystemConfigUpdateRequest,
    current_admin_id: int = Depends(require_admin_role("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """更新系统配置（写入 .env，需重启生效）"""
    result = update_system_config(req.updates)

    # 记录审计日志
    await write_audit_log(db, admin_id=current_admin_id, action="update_system_config_rejected",
                          target_type="system", target_id="runtime_config", success=False,
                          summary="已拒绝在线配置写入；未记录配置值。")

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    return result


@router.post("/api/admin/system/cache/clear")
async def api_admin_system_clear_cache(
    current_admin_id: int = Depends(require_admin_role("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """清空系统缓存"""
    result = clear_all_caches()

    # 记录审计日志
    await write_audit_log(
        db, admin_id=current_admin_id,
        action="clear_cache",
        target_type="system",
        target_id="cache",
        success=result["success"],
        summary=result["message"],
    )

    if not result["success"]: raise HTTPException(409, result["message"])
    return result
