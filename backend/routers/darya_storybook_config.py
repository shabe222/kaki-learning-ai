"""DARYA 视觉绘本 · 提供方配置路由（管理端）。

契约：GLM Key 只允许在管理端配置，且**只存在于服务端**（审计留痕，读取时脱敏）。
浏览器侧不再领取明文 Key —— 用户端一律经 `routers/darya_storybook_proxy.py`
的同源代理调用模型，用户无需也不应持有 Key。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from services.admin_audit import write_audit_log
from services.storybook_provider import mask_secret as _masked
from services.storybook_provider import read_glm_key as _read_glm_key
from services.storybook_provider import write_glm_key as _write_glm_key
from utils.auth import get_current_admin_id, require_admin_role

router = APIRouter()

# 写入/清空付费 GLM 凭证属于敏感操作，必须是最高权限角色。
_super_admin = require_admin_role("super_admin")


@router.get("/api/admin/storybook/glm-key")
async def api_admin_storybook_key_get(
    current_admin_id: int = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db),
):
    value = await _read_glm_key(db)
    return {"success": True, "data": {"configured": bool(value), "masked": _masked(value)}}


class StorybookKeyUpdateRequest(BaseModel):
    glmKey: str


@router.put("/api/admin/storybook/glm-key")
async def api_admin_storybook_key_put(
    req: StorybookKeyUpdateRequest,
    current_admin_id: int = Depends(_super_admin),
    db: AsyncSession = Depends(get_db),
):
    value = (req.glmKey or "").strip()
    await _write_glm_key(db, value)
    await write_audit_log(
        db, admin_id=current_admin_id,
        action="update_storybook_glm_key",
        target_type="system", target_id="storybook_glm_key",
        success=True,
        summary="已配置视觉绘本 GLM Key" if value else "已清空视觉绘本 GLM Key",
    )
    return {"success": True, "data": {"message": "视觉绘本 GLM Key 已更新，用户端打开绘本时自动生效"}}


@router.delete("/api/admin/storybook/glm-key")
async def api_admin_storybook_key_delete(
    current_admin_id: int = Depends(_super_admin),
    db: AsyncSession = Depends(get_db),
):
    await _write_glm_key(db, "")
    await write_audit_log(
        db, admin_id=current_admin_id,
        action="update_storybook_glm_key",
        target_type="system", target_id="storybook_glm_key",
        success=True, summary="已清空视觉绘本 GLM Key",
    )
    return {"success": True, "data": {"message": "视觉绘本 GLM Key 已清空"}}
