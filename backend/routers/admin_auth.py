"""管理员认证路由 - 登录 + 当前管理员信息"""
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.models import AdminUser
from database.dependencies import get_db
from schemas.admin import AdminLoginRequest, AdminInfoResponse, AdminLoginResponse
from utils.auth import verify_password, create_admin_token, get_current_admin_id

router = APIRouter()


def _admin_to_response(admin: AdminUser) -> AdminInfoResponse:
    """将管理员ORM对象转换为响应Schema（白名单，不含密码哈希）"""
    return AdminInfoResponse(
        admin_id=admin.id,
        username=admin.username,
        display_name=admin.display_name,
        role=admin.role,
        is_active=admin.is_active,
    )


@router.post("/api/admin/login")
async def api_admin_login(req: AdminLoginRequest, db: AsyncSession = Depends(get_db)):
    """管理员登录 - 查询独立管理员表，返回管理员专用Token"""
    result = await db.execute(
        select(AdminUser).where(AdminUser.username == req.username)
    )
    admin = result.scalar_one_or_none()

    # 统一错误信息，不泄露账号是否存在
    if not admin or not await verify_password(req.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not admin.is_active:
        raise HTTPException(status_code=403, detail="账户已被禁用")

    # 更新最近登录时间
    admin.last_login_at = datetime.utcnow()
    await db.commit()

    token = create_admin_token(admin.id, admin.username, admin.role)

    return {
        "success": True,
        "message": "登录成功",
        "data": AdminLoginResponse(
            token=token,
            admin=_admin_to_response(admin),
        ),
    }


@router.get("/api/admin/me")
async def api_admin_me(
    current_admin_id: int = Depends(get_current_admin_id),
    db: AsyncSession = Depends(get_db),
):
    """获取当前管理员信息 - 二次校验管理员状态"""
    result = await db.execute(
        select(AdminUser).where(AdminUser.id == current_admin_id)
    )
    admin = result.scalar_one_or_none()

    if not admin:
        raise HTTPException(status_code=401, detail="管理员不存在或登录已失效")

    if not admin.is_active:
        raise HTTPException(status_code=403, detail="账户已被禁用")

    return {
        "success": True,
        "message": "获取当前管理员成功",
        "data": _admin_to_response(admin),
    }
