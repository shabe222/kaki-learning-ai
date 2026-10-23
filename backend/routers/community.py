"""社区/同班同学路由 - active-peers / 热门知识点排行"""
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import User, LearningProgress
from utils.auth import get_current_user_id

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/community/active-peers")
async def get_active_peers(
    limit: int = 8,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    同班同学活跃榜（真实数据）：
    - 从 User 表取除自己外的所有用户
    - 按 LearningProgress.max(updated_at) 倒序（最近学习过 = 活跃）
    - 计算今日活跃数（updated_at >= 今天 00:00）
    - 总数 = 全站用户数 - 自己

    P0 修复（账户独立）：从 token 拿 current_user_id，不接受 path 参数。
    """
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    # 1) 全部"其他用户"（排除自己 + 未激活）
    other_users_q = select(User.id, User.username, User.avatar_url).where(
        User.id != current_user_id,
        User.is_active == True,
    )
    user_rows = (await db.execute(other_users_q)).all()
    if not user_rows:
        return {
            "success": True,
            "data": {
                "peers": [],
                "total": 0,
                "active_today": 0,
                "self_id": current_user_id,
            },
        }

    # 2) 查每个 user 的最近一次 progress 更新 + 当日 time_spent 总和
    user_ids = [r.id for r in user_rows]
    progress_q = (
        select(
            LearningProgress.user_id,
            func.max(LearningProgress.updated_at).label("last_active_at"),
            func.sum(LearningProgress.time_spent_minutes).label("total_minutes"),
        )
        .where(LearningProgress.user_id.in_(user_ids))
        .group_by(LearningProgress.user_id)
    )
    progress_rows = (await db.execute(progress_q)).all()
    progress_map = {r.user_id: r for r in progress_rows}

    # 3) 组装 peers（按最近活跃倒序，无 progress 记录的放最后）
    def _sort_key(r):
        p = progress_map.get(r.id)
        return p.last_active_at if (p and p.last_active_at) else datetime.min

    sorted_users = sorted(user_rows, key=_sort_key, reverse=True)

    peers = []
    for r in sorted_users[: max(1, min(limit, 20))]:
        p = progress_map.get(r.id)
        last_active = p.last_active_at if p else None
        peers.append({
            "id": r.id,
            "username": r.username,
            "avatar": r.avatar_url or "",
            "last_active_at": last_active.isoformat() if last_active else None,
            "time_spent_minutes": float(p.total_minutes or 0.0) if p else 0.0,
            "is_active_today": bool(last_active and last_active >= today_start),
        })

    # 4) 今日活跃数（peers 中 is_active_today=True 的数量）
    active_today = sum(1 for p in peers if p["is_active_today"])

    return {
        "success": True,
        "data": {
            "peers": peers,
            "total": len(user_rows),
            "active_today": active_today,
            "self_id": current_user_id,
        },
    }
