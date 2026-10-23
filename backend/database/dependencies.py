"""FastAPI 依赖项 - 共享 DB session + N+1 查询优化（Day 4 — 2b）"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import database.connection as db_conn
from database.models import User, LearningPlan


async def get_db() -> AsyncSession:
    if db_conn._async_session_factory is None:
        raise RuntimeError("数据库未初始化")
    async with db_conn._async_session_factory() as session:
        yield session


# ===== N+1 查询优化（Day 4 — 2b）=====
# 旧实现直接 db.query(User).filter_by(id=...).first() → 访问 .profile 时触发 lazy load = N+1
# 新实现用 selectinload 一次性预加载所有关联，避免 N+1 风暴

async def get_user_with_profile(db: AsyncSession, user_id: int) -> Optional[User]:
    """
    获取用户 + 一次性预加载 profile（避免访问 .profile 时的 N+1 二次查询）
    """
    result = await db.execute(
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == user_id)
    )
    return result.scalars().first()


async def get_user_with_resources(db: AsyncSession, user_id: int) -> Optional[User]:
    """
    获取用户 + 预加载 resources（learning_resources 表）
    """
    result = await db.execute(
        select(User)
        .options(selectinload(User.resources))
        .where(User.id == user_id)
    )
    return result.scalars().first()


async def get_user_paths(db: AsyncSession, user_id: int) -> List[LearningPlan]:
    """
    获取用户的所有学习计划（learning_plans）
    命名保留 `paths` 与原任务模板对齐（实际对应 LearningPlan）
    注意：当前 LearningPlan 尚未声明 relationship("tasks")，
    待 PlanTask 加上 back_populates 后再追加 selectinload(LearningPlan.tasks)
    """
    result = await db.execute(
        select(LearningPlan)
        .where(LearningPlan.user_id == user_id)
    )
    return result.scalars().all()
