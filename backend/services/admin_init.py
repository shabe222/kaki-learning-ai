"""管理员初始化服务 - 首次启动时幂等创建初始管理员"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import AdminUser
from utils.auth import hash_password_async

logger = logging.getLogger(__name__)


async def ensure_initial_admin(db: AsyncSession) -> None:
    """
    确保初始管理员存在。
    - 仅当 admin_users 表为空且配置了初始密码时创建
    - 重复启动不会重复创建
    - 修改环境变量不会更新已有管理员密码（安全设计）
    """
    if not settings.INITIAL_ADMIN_PASSWORD:
        logger.info("[AdminInit] 未配置 INITIAL_ADMIN_PASSWORD，跳过初始管理员创建")
        return

    # 检查是否已有管理员
    result = await db.execute(select(AdminUser).limit(1))
    existing = result.scalar_one_or_none()
    if existing:
        logger.info(f"[AdminInit] 管理员表已有记录，跳过初始化（现有管理员: {existing.username}）")
        return

    # 创建初始管理员
    username = settings.INITIAL_ADMIN_USERNAME or "admin"
    display_name = settings.INITIAL_ADMIN_DISPLAY_NAME or "超级管理员"

    admin = AdminUser(
        username=username,
        password_hash=await hash_password_async(settings.INITIAL_ADMIN_PASSWORD),
        display_name=display_name,
        role="super_admin",
        is_active=True,
    )
    db.add(admin)
    await db.commit()
    logger.info(f"[AdminInit] 初始管理员创建成功: {username} (角色: super_admin)")
