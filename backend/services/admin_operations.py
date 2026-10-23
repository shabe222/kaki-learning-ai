"""管理端运营记录：真实任务记录 + 隐私安全的技术投影。"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AgentInvocation, ChatSession, GeneratedItem
from services.admin_privacy import (
    safe_agent_label,
    safe_agent_name,
    safe_resource_type,
    safe_result_summary,
    safe_status_error_summary,
    safe_task_summary,
    status_error_category,
)


def _artifact_record(item) -> dict:
    agent_name = safe_agent_name(item["type"], item["type"])
    output_type = safe_resource_type(item["type"])
    return {
        "id": -item["id"],
        "record_kind": "artifact_only",
        "tracking_status": "artifact_only",
        "user_id": item["user_id"],
        "source_module": "workspace",
        "agent_name": agent_name,
        "agent_label": safe_agent_label(agent_name, output_type),
        "task_type": output_type,
        "model": "资源服务",
        "status": "artifact_exists",
        "duration_ms": None,
        "created_at": item["created_at"].isoformat() if item["created_at"] else None,
        "output_type": output_type,
        "artifact_id": item["id"],
        "task_id": item["task_id"],
        "error_category": None,
    }


def _invocation_record(row) -> dict:
    agent_name = safe_agent_name(row["agent_name"], row["output_type"])
    output_type = safe_resource_type(row["output_type"])
    return {
        "id": row["id"],
        "record_kind": "agent_task",
        "tracking_status": "complete",
        "user_id": row["user_id"],
        "source_module": "agent_api",
        "agent_name": agent_name,
        "agent_label": safe_agent_label(agent_name, output_type),
        "task_type": safe_resource_type(row["output_type"] or row["agent_name"]),
        "model": "服务端当前模型",
        "status": row["status"],
        "duration_ms": row["duration_ms"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "output_type": output_type,
        "artifact_id": row["output_id"],
        "task_id": row["id"],
        "error_category": status_error_category(row["status"]),
    }


def _invocation_columns():
    return (
        AgentInvocation.id,
        AgentInvocation.user_id,
        AgentInvocation.agent_name,
        AgentInvocation.output_type,
        AgentInvocation.status,
        AgentInvocation.duration_ms,
        AgentInvocation.created_at,
        AgentInvocation.output_id,
    )


def _artifact_columns():
    return (
        GeneratedItem.id,
        GeneratedItem.user_id,
        GeneratedItem.type,
        GeneratedItem.created_at,
        GeneratedItem.task_id,
    )


async def _all_records(db: AsyncSession) -> list[dict]:
    invocations = (await db.execute(select(*_invocation_columns()))).mappings().all()
    tracked = {row["output_id"] for row in invocations if row["output_id"]}
    artifacts = (await db.execute(select(*_artifact_columns()))).mappings().all()
    result = [_invocation_record(row) for row in invocations]
    result.extend(_artifact_record(item) for item in artifacts if item["id"] not in tracked)
    result.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return result


async def list_operations(db: AsyncSession, page=1, page_size=20, agent_type=None,
                          status_filter=None, user_id=None, start_date=None, end_date=None,
                          error_category=None):
    records = await _all_records(db)

    def keep(row):
        if agent_type and row["agent_name"] != agent_type:
            return False
        if status_filter and row["status"] != status_filter:
            return False
        if user_id is not None and row["user_id"] != user_id:
            return False
        if error_category and row["error_category"] != error_category:
            return False
        if start_date and (row["created_at"] or "") < start_date:
            return False
        if end_date and (row["created_at"] or "") > end_date:
            return False
        return True

    filtered = [row for row in records if keep(row)]
    start = (page - 1) * page_size
    return filtered[start:start + page_size], len(filtered)


async def get_operation_detail(db: AsyncSession, invocation_id: int):
    if invocation_id < 0:
        item = (await db.execute(
            select(*_artifact_columns()).where(GeneratedItem.id == -invocation_id)
        )).mappings().one_or_none()
        if not item:
            return None
        base = _artifact_record(item)
        return {
            **base,
            "input_summary": safe_task_summary(base["agent_name"], base["output_type"]),
            "execution_stages": [],
            "result_summary": safe_result_summary(base["output_type"]),
            "error_message": None,
            "privacy_note": "学习问题、标题、章节名与正文均不会展示给管理员；这里只保留资源类型和技术状态。",
        }

    row = (await db.execute(
        select(*_invocation_columns()).where(AgentInvocation.id == invocation_id)
    )).mappings().one_or_none()
    if not row:
        return None
    base = _invocation_record(row)
    return {
        **base,
        "input_summary": safe_task_summary(base["agent_name"], base["output_type"]),
        "execution_stages": [],
        "result_summary": safe_result_summary(base["output_type"]),
        "error_message": safe_status_error_summary(row["status"]),
        "privacy_note": "运营详情仅展示技术状态；用户原始 Prompt、学习内容与原始错误正文均已隐藏。",
    }


async def get_operations_stats(db: AsyncSession) -> dict:
    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    records = await _all_records(db)
    day = [r for r in records if r.get("created_at") and datetime.fromisoformat(r["created_at"]) >= day_ago]
    week = [r for r in records if r.get("created_at") and datetime.fromisoformat(r["created_at"]) >= week_ago]
    day_tasks = [r for r in day if r["record_kind"] == "agent_task"]
    week_tasks = [r for r in week if r["record_kind"] == "agent_task"]

    # Privacy boundary: select only stored aggregate/session metadata; never load ChatSession.messages.
    session_rows = (await db.execute(
        select(ChatSession.user_id, ChatSession.message_count, ChatSession.last_message_at)
        .where(ChatSession.last_message_at >= week_ago)
    )).all()
    ai_interactions = sum(max(int(row.message_count or 0), 0) for row in session_rows)

    successes = [r for r in week_tasks if r["status"] in ("success", "succeeded", "completed")]
    failures = [r for r in week_tasks if r["status"] in ("failed", "error")]
    durations = [r["duration_ms"] for r in successes if r.get("duration_ms") is not None]
    agent_counts = Counter((r["agent_name"], r.get("agent_label")) for r in records if r["record_kind"] == "agent_task")
    daily = defaultdict(lambda: {"total": 0, "success": 0, "failed": 0, "artifacts": 0})
    for row in week:
        key = row["created_at"][:10]
        daily[key]["total"] += 1
        if row["record_kind"] == "artifact_only":
            daily[key]["artifacts"] += 1
        if row["status"] in ("success", "succeeded", "completed"):
            daily[key]["success"] += 1
        elif row["status"] in ("failed", "error"):
            daily[key]["failed"] += 1
    errors = Counter(r["error_category"] for r in day if r.get("error_category"))
    return {
        "total": sum(r["record_kind"] == "agent_task" for r in records),
        "day_total": len(day_tasks),
        "day_success": sum(r["status"] in ("success", "succeeded", "completed") for r in day_tasks),
        "day_failed": sum(r["status"] in ("failed", "error") for r in day_tasks),
        "day_running": sum(r["status"] == "running" for r in day_tasks),
        "week_success_rate": round(len(successes) / (len(successes) + len(failures)) * 100, 1) if successes or failures else None,
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
        "ai_interactions": ai_interactions,
        "agent_tasks": sum(r["record_kind"] == "agent_task" for r in week),
        "artifacts": sum(r["record_kind"] == "artifact_only" for r in week),
        "active_users": len({row.user_id for row in session_rows if int(row.message_count or 0) > 0}),
        "agent_distribution": [{"agent_name": k[0], "agent_label": k[1], "count": v} for k, v in agent_counts.most_common(10)],
        "daily_trend": [{"date": k, **v} for k, v in sorted(daily.items())],
        "error_categories": [{"category": k, "count": v} for k, v in errors.most_common(6)],
    }
