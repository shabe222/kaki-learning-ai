"""管理端总览：仅聚合非内容型平台数据。"""
from collections import defaultdict
from datetime import datetime, timedelta, time

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AgentInvocation, ChatSession, GeneratedItem, StudentProfile, User, WorkspaceMaterial
from services.admin_privacy import (
    safe_agent_label,
    safe_agent_name,
    safe_resource_type,
    safe_status_error_summary,
    status_error_category,
)


def _today() -> datetime:
    now = datetime.utcnow()
    return datetime.combine(now.date(), time.min)


async def _interaction_rows(db: AsyncSession, since: datetime | None = None):
    # Do not load ChatSession.messages: admin metrics only need aggregate/session metadata.
    query = select(
        ChatSession.user_id,
        ChatSession.message_count,
        ChatSession.created_at,
        ChatSession.last_message_at,
    )
    if since is not None:
        query = query.where(ChatSession.last_message_at >= since)
    return (await db.execute(query)).all()


async def get_core_metrics(db: AsyncSession) -> dict:
    today = _today()
    week = _today() - timedelta(days=7)
    day_ago = datetime.utcnow() - timedelta(hours=24)
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    active_users = (await db.execute(select(func.count(User.id)).where(User.is_active.is_(True)))).scalar() or 0
    disabled_users = total_users - active_users
    today_new = (await db.execute(select(func.count(User.id)).where(User.created_at >= today))).scalar() or 0
    week_new = (await db.execute(select(func.count(User.id)).where(User.created_at >= week))).scalar() or 0
    profiled = (await db.execute(select(func.count(StudentProfile.id)))).scalar() or 0
    sessions = await _interaction_rows(db)
    week_sessions = await _interaction_rows(db, week)
    ai_interactions = sum(max(int(s.message_count or 0), 0) for s in sessions)
    week_interactions = sum(max(int(s.message_count or 0), 0) for s in week_sessions)
    total_sessions = len(sessions)
    today_sessions = sum(1 for s in sessions if s.created_at and s.created_at >= today)

    total_tasks = (await db.execute(select(func.count(AgentInvocation.id)))).scalar() or 0
    today_tasks = (await db.execute(select(func.count(AgentInvocation.id)).where(AgentInvocation.created_at >= today))).scalar() or 0
    running = (await db.execute(select(func.count(AgentInvocation.id)).where(AgentInvocation.status == "running"))).scalar() or 0
    success = (await db.execute(select(func.count(AgentInvocation.id)).where(and_(AgentInvocation.created_at >= week, AgentInvocation.status.in_(("success", "succeeded", "completed")))))).scalar() or 0
    failed = (await db.execute(select(func.count(AgentInvocation.id)).where(and_(AgentInvocation.created_at >= week, AgentInvocation.status.in_(("failed", "error")))))).scalar() or 0
    artifacts = (await db.execute(select(func.count(GeneratedItem.id)))).scalar() or 0
    week_artifacts = (await db.execute(select(func.count(GeneratedItem.id)).where(GeneratedItem.created_at >= week))).scalar() or 0
    materials = (await db.execute(select(func.count(WorkspaceMaterial.id)))).scalar() or 0
    active_ai_users = len({s.user_id for s in week_sessions if int(s.message_count or 0) > 0})
    error_24h = (await db.execute(select(func.count(AgentInvocation.id)).where(and_(AgentInvocation.created_at >= day_ago, AgentInvocation.status.in_(("failed", "error")))))).scalar() or 0
    finished = success + failed
    return {
        "total_users": total_users,
        "active_users": active_users,
        "disabled_users": disabled_users,
        "today_new_users": today_new,
        "week_new_users": week_new,
        "profiled_users": profiled,
        "profile_completion_rate": round(profiled / total_users * 100, 1) if total_users else None,
        "total_sessions": total_sessions,
        "today_sessions": today_sessions,
        "ai_interactions": ai_interactions,
        "week_ai_interactions": week_interactions,
        "active_ai_users": active_ai_users,
        "total_tasks": total_tasks,
        "today_tasks": today_tasks,
        "running_tasks": running,
        "week_success_tasks": success,
        "week_failed_tasks": failed,
        "week_success_rate": round(success / finished * 100, 1) if finished else None,
        "total_items": artifacts,
        "week_artifacts": week_artifacts,
        "total_materials": materials,
        "error_count_24h": error_24h,
    }


async def get_weekly_trend(db: AsyncSession) -> list[dict]:
    start = _today() - timedelta(days=6)
    sessions = await _interaction_rows(db, start)
    invocations = (await db.execute(
        select(AgentInvocation.created_at, AgentInvocation.status)
        .where(AgentInvocation.created_at >= start)
    )).all()
    artifacts = (await db.execute(
        select(GeneratedItem.created_at).where(GeneratedItem.created_at >= start)
    )).scalars().all()
    users = (await db.execute(select(User.created_at).where(User.created_at >= start))).scalars().all()
    stats = defaultdict(lambda: {"ai_interactions": 0, "agent_tasks": 0, "artifacts": 0, "success_count": 0, "failed_count": 0, "new_users": 0})
    # Stored message_count is aggregate metadata. Attribute it to the session's latest activity day;
    # no chat body or per-message timestamp is loaded into the admin process.
    for session in sessions:
        when = session.last_message_at or session.created_at
        if when:
            stats[when.strftime("%Y-%m-%d")]["ai_interactions"] += max(int(session.message_count or 0), 0)
    for row in invocations:
        day = row.created_at.strftime("%Y-%m-%d")
        stats[day]["agent_tasks"] += 1
        if row.status in ("success", "succeeded", "completed"):
            stats[day]["success_count"] += 1
        elif row.status in ("failed", "error"):
            stats[day]["failed_count"] += 1
    for created_at in artifacts:
        if created_at:
            stats[created_at.strftime("%Y-%m-%d")]["artifacts"] += 1
    for created in users:
        if created:
            stats[created.strftime("%Y-%m-%d")]["new_users"] += 1
    result = []
    for offset in range(7):
        ds = (start + timedelta(days=offset)).strftime("%Y-%m-%d")
        item = stats[ds]
        result.append({"date": ds, **item, "task_count": item["agent_tasks"]})
    return result


async def get_recent_tasks(db: AsyncSession, limit: int = 10) -> list[dict]:
    invocations = (await db.execute(
        select(
            AgentInvocation.id,
            AgentInvocation.user_id,
            AgentInvocation.agent_name,
            AgentInvocation.output_type,
            AgentInvocation.status,
            AgentInvocation.duration_ms,
            AgentInvocation.created_at,
            AgentInvocation.output_id,
        ).order_by(AgentInvocation.created_at.desc()).limit(limit)
    )).mappings().all()
    tracked = {row["output_id"] for row in invocations if row["output_id"]}
    artifacts = (await db.execute(
        select(
            GeneratedItem.id,
            GeneratedItem.user_id,
            GeneratedItem.type,
            GeneratedItem.created_at,
        ).order_by(GeneratedItem.created_at.desc()).limit(limit * 2)
    )).mappings().all()
    rows = []
    for row in invocations:
        agent_name = safe_agent_name(row["agent_name"], row["output_type"])
        rows.append({
            "id": row["id"],
            "record_kind": "agent_task",
            "user_id": row["user_id"],
            "source_module": "agent_api",
            "agent_name": agent_name,
            "agent_label": safe_agent_label(agent_name, row["output_type"]),
            "status": row["status"],
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "artifact_id": row["output_id"],
            "output_type": safe_resource_type(row["output_type"]),
            "tracking_status": "complete",
            "error_category": status_error_category(row["status"]),
            "error_message": safe_status_error_summary(row["status"]),
        })
    for row in artifacts:
        if row["id"] in tracked:
            continue
        agent_name = safe_agent_name(row["type"], row["type"])
        rows.append({
            "id": -row["id"],
            "record_kind": "artifact_only",
            "user_id": row["user_id"],
            "source_module": "workspace",
            "agent_name": agent_name,
            "agent_label": safe_agent_label(agent_name, row["type"]),
            "status": "success",
            "duration_ms": None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "artifact_id": row["id"],
            "output_type": safe_resource_type(row["type"]),
            "tracking_status": "artifact_only",
            "error_category": None,
            "error_message": None,
        })
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return rows[:limit]


async def get_system_status(db: AsyncSession) -> dict:
    import sys
    from config import settings
    return {
        "backend": "online",
        "database": "online",
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": getattr(settings, "DEEPSEEK_MODEL", settings.LLM_MODEL) if settings.LLM_PROVIDER == "deepseek" else settings.LLM_MODEL,
        "mode": settings.MODE,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "api_routes": 0,
        "agent_count": 0,
        "error_count_24h": 0,
    }
