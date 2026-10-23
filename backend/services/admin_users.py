"""管理端用户查询与状态管理服务"""
from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc, asc

from database.models import (
    User, StudentProfile, ChatSession, AgentInvocation,
    GeneratedItem, WorkspaceMaterial
)
from services.admin_privacy import mask_email, safe_agent_label


async def list_users(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    keyword: Optional[str] = None,
    status_filter: Optional[str] = None,
    profile_filter: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> tuple[List[dict], int]:
    from services.admin_workbench import user_projection, paginate
    from sqlalchemy import or_
    q = user_projection().subquery()
    stmt = select(q)
    if keyword:
        stmt = stmt.where(or_(q.c.username.contains(keyword), q.c.user_id == (int(keyword) if keyword.isdigit() else -1)))
    if status_filter in {"active", "disabled"}: stmt = stmt.where(q.c.is_active == (status_filter == "active"))
    if profile_filter in {"has", "no"}: stmt = stmt.where(q.c.has_profile == (profile_filter == "has"))
    column = q.c.last_active_at if sort_by == "last_active_at" else q.c.username if sort_by == "username" else q.c.created_at
    result = await paginate(db, stmt.order_by(column.asc() if sort_order == "asc" else column.desc()), page, page_size)
    for row in result["items"]: row["profile_completeness"] = None
    return result["items"], result["total"]


async def get_user_detail(db: AsyncSession, user_id: int) -> Optional[dict]:
    u_r = await db.execute(select(User).where(User.id == user_id))
    user = u_r.scalar_one_or_none()
    if not user:
        return None

    # Admin only needs existence; never hydrate profile content.
    p_r = await db.execute(select(func.count(StudentProfile.id)).where(StudentProfile.user_id == user_id))
    has_profile = bool(p_r.scalar() or 0)

    chat_r = await db.execute(
        select(func.count(ChatSession.id)).where(ChatSession.user_id == user_id)
    )
    chat_count = chat_r.scalar() or 0

    task_r = await db.execute(
        select(func.count(AgentInvocation.id)).where(AgentInvocation.user_id == user_id)
    )
    task_count = task_r.scalar() or 0

    success_r = await db.execute(
        select(func.count(AgentInvocation.id)).where(
            and_(AgentInvocation.user_id == user_id, AgentInvocation.status == "success")
        )
    )
    success_tasks = success_r.scalar() or 0

    failed_r = await db.execute(
        select(func.count(AgentInvocation.id)).where(
            and_(AgentInvocation.user_id == user_id, AgentInvocation.status == "failed")
        )
    )
    failed_tasks = failed_r.scalar() or 0

    item_r = await db.execute(
        select(func.count(GeneratedItem.id)).where(GeneratedItem.user_id == user_id)
    )
    item_count = item_r.scalar() or 0

    material_r = await db.execute(
        select(func.count(WorkspaceMaterial.id)).where(WorkspaceMaterial.user_id == user_id)
    )
    material_count = material_r.scalar() or 0

    timeline = []
    recent_tasks = (await db.execute(
        select(
            AgentInvocation.agent_name,
            AgentInvocation.output_type,
            AgentInvocation.status,
            AgentInvocation.created_at,
        ).where(AgentInvocation.user_id == user_id)
        .order_by(AgentInvocation.created_at.desc()).limit(10)
    )).all()
    for t in recent_tasks:
        timeline.append({
            "event_type": f"agent_{t.status}",
            "event_label": f"{safe_agent_label(t.agent_name, t.output_type)} · {t.status}",
            "time": t.created_at.isoformat() if t.created_at else None,
        })

    recent_sessions = (await db.execute(
        select(ChatSession.created_at).where(ChatSession.user_id == user_id)
        .order_by(ChatSession.created_at.desc()).limit(5)
    )).scalars().all()
    for created_at in recent_sessions:
        timeline.append({
            "event_type": "chat_session",
            "event_label": "发生 AI 会话",
            "time": created_at.isoformat() if created_at else None,
        })

    timeline.sort(key=lambda x: x["time"] or "", reverse=True)
    timeline = timeline[:15]

    from services.admin_workbench import user_projection
    metadata = user_projection().subquery()
    actual = (await db.execute(select(metadata.c.last_active_at).where(metadata.c.user_id == user_id))).scalar()
    return {
        "user_id": user.id,
        "username": user.username,
        "email": mask_email(user.email),
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_active_at": actual.isoformat() if actual else None,
        "has_profile": has_profile,
        "profile_completeness": None,
        "chat_count": chat_count,
        "task_count": task_count,
        "success_tasks": success_tasks,
        "failed_tasks": failed_tasks,
        "item_count": item_count,
        "material_count": material_count,
        "activity_timeline": timeline,
    }


async def set_user_status(db: AsyncSession, user_id: int, is_active: bool) -> bool:
    u_r = await db.execute(select(User).where(User.id == user_id))
    user = u_r.scalar_one_or_none()
    if not user:
        return False
    user.is_active = is_active
    user.updated_at = datetime.utcnow()
    await db.commit()
    return True
