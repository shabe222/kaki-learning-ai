from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import (
    DashboardActivity,
    DashboardTodo,
    ExamPaper,
    GeneratedItem,
    LearningSuggestion,
    Notification,
    PlanTask,
    WeakPoint,
)
from utils.auth import get_current_user_id

router = APIRouter(prefix="/api", tags=["dashboard-system"])


class TodoUpdateRequest(BaseModel):
    status: str


def _notification_payload(item: Notification) -> dict:
    return {
        "id": item.id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "title": item.title,
        "content": item.content or "",
        "target_route": item.target_route or "/",
        "is_read": bool(item.is_read),
        "created_at": item.created_at.isoformat() if item.created_at else "",
        "read_at": item.read_at.isoformat() if item.read_at else None,
    }


def _todo_payload(item: DashboardTodo) -> dict:
    return {
        "id": item.id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "title": item.title,
        "description": item.description or "",
        "due_label": item.due_label or "",
        "status": item.status,
        "target_route": item.target_route or "/learning",
        "created_at": item.created_at.isoformat() if item.created_at else "",
        "updated_at": item.updated_at.isoformat() if item.updated_at else "",
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    }


def _activity_payload(item: DashboardActivity) -> dict:
    return {
        "id": item.id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "title": item.title,
        "description": item.description or "",
        "time_label": item.time_label or "",
        "target_route": item.target_route or "/learning",
        "opened_count": item.opened_count or 0,
        "created_at": item.created_at.isoformat() if item.created_at else "",
        "last_opened_at": item.last_opened_at.isoformat() if item.last_opened_at else None,
    }


def _suggestion_payload(item: LearningSuggestion) -> dict:
    return {
        "id": item.id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "title": item.title,
        "description": item.description or "",
        "target_route": item.target_route or "/learning",
        "status": item.status,
        "created_at": item.created_at.isoformat() if item.created_at else "",
        "applied_at": item.applied_at.isoformat() if item.applied_at else None,
    }


async def _first_or_none(db: AsyncSession, model, user_id: int):
    result = await db.execute(select(model).where(model.user_id == user_id).limit(1))
    return result.scalar_one_or_none()


async def _seed_notifications(db: AsyncSession, user_id: int) -> None:
    if await _first_or_none(db, Notification, user_id):
        return

    exams = (await db.execute(
        select(ExamPaper)
        .where(ExamPaper.user_id == user_id, ExamPaper.status.in_(["draft", "in_progress"]))
        .order_by(desc(ExamPaper.created_at))
        .limit(3)
    )).scalars().all()
    items = (await db.execute(
        select(GeneratedItem)
        .where(GeneratedItem.user_id == user_id)
        .order_by(desc(GeneratedItem.created_at))
        .limit(3)
    )).scalars().all()

    for exam in exams:
        db.add(Notification(
            user_id=user_id,
            source_type="exam",
            source_id=str(exam.id),
            title="待完成考试",
            content=f"{exam.exam_type} · {exam.time_limit_min} 分钟",
            target_route="/exam",
        ))
    for item in items:
        db.add(Notification(
            user_id=user_id,
            source_type="generated_item",
            source_id=str(item.id),
            title=item.title,
            content=item.chapter or item.type,
            target_route="/notes" if item.type in {"note", "chat_profile"} else "/resources",
        ))
    await db.commit()


async def _seed_todos_and_activities(db: AsyncSession, user_id: int) -> None:
    has_todo = await _first_or_none(db, DashboardTodo, user_id)
    has_activity = await _first_or_none(db, DashboardActivity, user_id)
    if has_todo and has_activity:
        return

    tasks = (await db.execute(
        select(PlanTask)
        .where(PlanTask.user_id == user_id)
        .order_by(PlanTask.order_index, desc(PlanTask.created_at))
        .limit(8)
    )).scalars().all()

    for task in tasks:
        title = task.name or task.chapter_id
        if not has_todo:
            db.add(DashboardTodo(
                user_id=user_id,
                source_type="plan_task",
                source_id=str(task.id),
                title=title,
                description=f"{task.task_type} · {task.chapter_id}",
                due_label=f"{task.duration_min} 分钟",
                status="done" if task.status == "completed" else task.status,
                target_route="/learning",
                completed_at=task.completed_at,
            ))
        if not has_activity:
            db.add(DashboardActivity(
                user_id=user_id,
                source_type="plan_task",
                source_id=str(task.id),
                title=title,
                description=f"{task.task_type} · {task.chapter_id}",
                time_label=task.status,
                target_route="/learning",
            ))
    await db.commit()


async def _seed_suggestions(db: AsyncSession, user_id: int) -> None:
    if await _first_or_none(db, LearningSuggestion, user_id):
        return

    active_task = (await db.execute(
        select(PlanTask)
        .where(PlanTask.user_id == user_id, PlanTask.status.in_(["pending", "in_progress"]))
        .order_by(PlanTask.order_index, desc(PlanTask.created_at))
        .limit(1)
    )).scalar_one_or_none()
    weak_points = (await db.execute(
        select(WeakPoint)
        .where(WeakPoint.user_id == user_id, WeakPoint.status == "active")
        .order_by(desc(WeakPoint.occurrence_count), desc(WeakPoint.first_seen_at))
        .limit(3)
    )).scalars().all()

    if active_task:
        db.add(LearningSuggestion(
            user_id=user_id,
            source_type="plan_task",
            source_id=str(active_task.id),
            title=f"继续{active_task.name or active_task.chapter_id}",
            description=f"继续推进 {active_task.name or active_task.chapter_id}。",
            target_route="/learning",
        ))
    for point in weak_points:
        db.add(LearningSuggestion(
            user_id=user_id,
            source_type="weak_point",
            source_id=str(point.id),
            title=point.knowledge_point,
            description="来自后端薄弱点诊断，建议优先复习。",
            target_route="/learning",
        ))
    await db.commit()


@router.get("/notifications")
async def list_notifications(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _seed_notifications(db, current_user_id)
    rows = (await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user_id)
        .order_by(Notification.is_read.asc(), desc(Notification.created_at))
        .limit(50)
    )).scalars().all()
    return {"success": True, "data": [_notification_payload(item) for item in rows]}


@router.patch("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = (await db.execute(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == current_user_id)
    )).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="notification not found")
    item.is_read = True
    item.read_at = datetime.now()
    await db.commit()
    await db.refresh(item)
    return {"success": True, "data": _notification_payload(item)}


@router.get("/todos")
async def list_todos(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _seed_todos_and_activities(db, current_user_id)
    rows = (await db.execute(
        select(DashboardTodo)
        .where(DashboardTodo.user_id == current_user_id)
        .order_by(DashboardTodo.status.asc(), desc(DashboardTodo.created_at))
        .limit(50)
    )).scalars().all()
    return {"success": True, "data": [_todo_payload(item) for item in rows]}


@router.patch("/todos/{todo_id}")
async def update_todo(
    todo_id: int,
    req: TodoUpdateRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if req.status not in {"pending", "active", "in_progress", "done", "skipped"}:
        raise HTTPException(status_code=400, detail="invalid todo status")
    item = (await db.execute(
        select(DashboardTodo).where(DashboardTodo.id == todo_id, DashboardTodo.user_id == current_user_id)
    )).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="todo not found")
    item.status = req.status
    item.completed_at = datetime.now() if req.status == "done" else None
    await db.commit()
    await db.refresh(item)
    return {"success": True, "data": _todo_payload(item)}


@router.get("/activities")
async def list_activities(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _seed_todos_and_activities(db, current_user_id)
    rows = (await db.execute(
        select(DashboardActivity)
        .where(DashboardActivity.user_id == current_user_id)
        .order_by(desc(DashboardActivity.created_at))
        .limit(50)
    )).scalars().all()
    return {"success": True, "data": [_activity_payload(item) for item in rows]}


@router.post("/activities/{activity_id}/open")
async def open_activity(
    activity_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = (await db.execute(
        select(DashboardActivity).where(DashboardActivity.id == activity_id, DashboardActivity.user_id == current_user_id)
    )).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="activity not found")
    item.opened_count = (item.opened_count or 0) + 1
    item.last_opened_at = datetime.now()
    await db.commit()
    await db.refresh(item)
    return {"success": True, "data": _activity_payload(item)}


@router.get("/suggestions")
async def list_suggestions(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _seed_suggestions(db, current_user_id)
    rows = (await db.execute(
        select(LearningSuggestion)
        .where(LearningSuggestion.user_id == current_user_id)
        .order_by(LearningSuggestion.status.asc(), desc(LearningSuggestion.created_at))
        .limit(50)
    )).scalars().all()
    return {"success": True, "data": [_suggestion_payload(item) for item in rows]}


@router.post("/suggestions/{suggestion_id}/apply")
async def apply_suggestion(
    suggestion_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = (await db.execute(
        select(LearningSuggestion).where(LearningSuggestion.id == suggestion_id, LearningSuggestion.user_id == current_user_id)
    )).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="suggestion not found")
    item.status = "applied"
    item.applied_at = datetime.now()
    await db.commit()
    await db.refresh(item)
    return {"success": True, "data": _suggestion_payload(item)}
