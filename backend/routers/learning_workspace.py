from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import LearningPlan, LearningProgress, PlanTask
from services.knowledge_graph import CHAPTER_ORDER, KNOWLEDGE_GRAPH
from utils.auth import get_current_user_id

router = APIRouter(prefix="/api/learning", tags=["learning-workspace"])


class LessonCompleteRequest(BaseModel):
    time_spent_minutes: float = 25.0


class TaskUpdateRequest(BaseModel):
    status: str


def _now() -> datetime:
    return datetime.now()


def _chapter_order(chapter_id: str) -> int:
    try:
        return CHAPTER_ORDER.index(chapter_id) + 1
    except ValueError:
        return 999


def _lesson_id(chapter_id: str, index: int) -> str:
    return f"{chapter_id}__lesson_{index}"


def _parse_lesson_id(lesson_id: str) -> tuple[str, int]:
    if "__lesson_" not in lesson_id:
        raise HTTPException(400, "invalid lesson id")
    chapter_id, raw_index = lesson_id.rsplit("__lesson_", 1)
    try:
        return chapter_id, int(raw_index)
    except ValueError as exc:
        raise HTTPException(400, "invalid lesson id") from exc


def _chapter_lessons(chapter_id: str) -> list[dict]:
    chapter = KNOWLEDGE_GRAPH.get(chapter_id)
    if not chapter:
        raise HTTPException(404, f"chapter not found: {chapter_id}")

    points = chapter.get("knowledge_points") or []
    lessons = []
    for index, point in enumerate(points[:5], 1):
        if isinstance(point, dict):
            title = point.get("name") or point.get("id") or f"Lesson {index}"
            point_id = point.get("id") or f"point_{index}"
        else:
            title = str(point)
            point_id = f"point_{index}"
        lessons.append(
            {
                "id": _lesson_id(chapter_id, index),
                "chapter_id": chapter_id,
                "knowledge_point_id": point_id,
                "title": title,
                "order": index,
                "estimated_minutes": max(18, min(35, int(chapter.get("estimated_hours", 2) * 60 / max(len(points), 1)))),
            }
        )
    if lessons:
        return lessons

    return [
        {
            "id": _lesson_id(chapter_id, 1),
            "chapter_id": chapter_id,
            "knowledge_point_id": "overview",
            "title": chapter.get("name", chapter_id),
            "order": 1,
            "estimated_minutes": 25,
        }
    ]


async def _ensure_plan_and_tasks(db: AsyncSession, user_id: int) -> LearningPlan:
    result = await db.execute(
        select(LearningPlan)
        .where(LearningPlan.user_id == user_id, LearningPlan.status == "active")
        .order_by(desc(LearningPlan.id))
        .limit(1)
    )
    plan = result.scalar_one_or_none()
    if plan is None:
        plan = LearningPlan(
            user_id=user_id,
            goal_type="intro",
            goal_detail="Machine learning workspace plan",
            deadline=_now() + timedelta(days=90),
            weekly_hours=10.0,
            chapter_ids=list(CHAPTER_ORDER),
            schedule=[],
            status="active",
            profile_snapshot=None,
        )
        db.add(plan)
        await db.flush()

    existing_result = await db.execute(select(PlanTask.chapter_id).where(PlanTask.user_id == user_id, PlanTask.plan_id == plan.id))
    existing = set(existing_result.scalars().all())
    today = _now()
    for index, chapter_id in enumerate(CHAPTER_ORDER, 1):
        if chapter_id in existing:
            continue
        chapter = KNOWLEDGE_GRAPH.get(chapter_id, {})
        db.add(
            PlanTask(
                plan_id=plan.id,
                user_id=user_id,
                chapter_id=chapter_id,
                name=chapter.get("name", chapter_id),
                task_type="new_learn" if index == 1 else "review",
                duration_min=30,
                status="pending",
                order_index=index,
                task_date=today + timedelta(days=index - 1),
            )
        )
    await db.commit()
    await db.refresh(plan)
    return plan


async def _progress_for_lessons(db: AsyncSession, user_id: int, lessons: list[dict]) -> dict[str, LearningProgress]:
    ids = [lesson["id"] for lesson in lessons]
    if not ids:
        return {}
    result = await db.execute(
        select(LearningProgress).where(
            LearningProgress.user_id == user_id,
            LearningProgress.stage_name.in_(ids),
        )
    )
    return {item.stage_name: item for item in result.scalars().all()}


async def _get_or_create_lesson_progress(db: AsyncSession, user_id: int, lesson_id: str) -> LearningProgress:
    chapter_id, lesson_index = _parse_lesson_id(lesson_id)
    lesson_ids = {lesson["id"] for lesson in _chapter_lessons(chapter_id)}
    if lesson_id not in lesson_ids:
        raise HTTPException(404, "lesson not found")

    result = await db.execute(
        select(LearningProgress).where(
            LearningProgress.user_id == user_id,
            LearningProgress.stage_name == lesson_id,
        )
    )
    progress = result.scalar_one_or_none()
    if progress:
        return progress

    progress = LearningProgress(
        user_id=user_id,
        stage_name=lesson_id,
        stage_index=_chapter_order(chapter_id) * 100 + lesson_index,
        progress_pct=0.0,
        status="pending",
        completed_exercises=0,
        total_exercises=0,
        time_spent_minutes=0.0,
    )
    db.add(progress)
    await db.flush()
    return progress


async def _active_chapter_id(db: AsyncSession, user_id: int) -> str:
    task_result = await db.execute(
        select(PlanTask)
        .where(PlanTask.user_id == user_id, PlanTask.status == "in_progress")
        .order_by(PlanTask.order_index)
        .limit(1)
    )
    task = task_result.scalar_one_or_none()
    if task:
        return task.chapter_id

    pending_result = await db.execute(
        select(PlanTask)
        .where(PlanTask.user_id == user_id, PlanTask.status == "pending")
        .order_by(PlanTask.order_index)
        .limit(1)
    )
    pending = pending_result.scalar_one_or_none()
    return pending.chapter_id if pending else CHAPTER_ORDER[0]


async def _serialize_workspace(db: AsyncSession, user_id: int) -> dict:
    await _ensure_plan_and_tasks(db, user_id)
    active_id = await _active_chapter_id(db, user_id)

    task_result = await db.execute(select(PlanTask).where(PlanTask.user_id == user_id))
    tasks = task_result.scalars().all()
    tasks_by_chapter = {task.chapter_id: task for task in tasks}

    progress_result = await db.execute(select(LearningProgress).where(LearningProgress.user_id == user_id))
    progress_items = progress_result.scalars().all()
    progress_by_chapter: dict[str, list[LearningProgress]] = {}
    for item in progress_items:
        if "__lesson_" not in item.stage_name:
            continue
        chapter_id, _ = item.stage_name.rsplit("__lesson_", 1)
        progress_by_chapter.setdefault(chapter_id, []).append(item)

    chapters = []
    completed_chapters = 0
    for index, chapter_id in enumerate(CHAPTER_ORDER, 1):
        chapter = KNOWLEDGE_GRAPH.get(chapter_id, {})
        task = tasks_by_chapter.get(chapter_id)
        lesson_progress = progress_by_chapter.get(chapter_id, [])
        if lesson_progress:
            pct = round(sum(float(item.progress_pct or 0) for item in lesson_progress) / len(_chapter_lessons(chapter_id)), 1)
        else:
            pct = 0.0
        status = task.status if task else "pending"
        if pct >= 100:
            completed_chapters += 1
            status = "completed"
        chapters.append(
            {
                "id": chapter_id,
                "order": index,
                "name": chapter.get("name", chapter_id),
                "description": chapter.get("description", ""),
                "difficulty": chapter.get("difficulty", 3),
                "estimated_hours": chapter.get("estimated_hours", 0),
                "progress_pct": pct,
                "task_id": task.id if task else None,
                "task_status": status,
                "has_material": False,
                "has_exam": False,
                "material_count": 0,
                "resource_count": 0,
            }
        )

    lessons = _chapter_lessons(active_id)
    progress_by_lesson = await _progress_for_lessons(db, user_id, lessons)
    serialized_lessons = []
    for lesson in lessons:
        progress = progress_by_lesson.get(lesson["id"])
        status = progress.status if progress else "not_started"
        serialized_lessons.append(
            {
                **lesson,
                "status": "not_started" if status == "pending" else status,
                "progress_pct": round(float(progress.progress_pct or 0), 1) if progress else 0,
                "time_spent_minutes": round(float(progress.time_spent_minutes or 0), 1) if progress else 0,
            }
        )

    today_tasks = [
        {
            "id": task.id,
            "chapter_id": task.chapter_id,
            "title": task.name or KNOWLEDGE_GRAPH.get(task.chapter_id, {}).get("name", task.chapter_id),
            "description": f"{task.task_type} · {task.duration_min or 30} min",
            "status": task.status,
            "duration_min": task.duration_min or 30,
        }
        for task in sorted(tasks, key=lambda item: (item.status == "completed", item.order_index))[:3]
    ]

    week_start = _now() - timedelta(days=6)
    weekly = []
    for offset in range(7):
        day = (week_start + timedelta(days=offset)).date()
        minutes = sum(
            float(item.time_spent_minutes or 0)
            for item in progress_items
            if item.updated_at and item.updated_at.date() == day
        )
        weekly.append({"label": f"{day.month}/{day.day}", "value": round(minutes / 60, 1)})

    total_minutes = sum(float(item.time_spent_minutes or 0) for item in progress_items)
    completed_lessons = sum(1 for item in progress_items if item.status == "completed")
    completed_tasks = sum(1 for task in tasks if task.status == "completed")
    total_progress = round(sum(float(chapter["progress_pct"]) for chapter in chapters) / max(len(chapters), 1), 1)

    return {
        "user_id": user_id,
        "current_chapter_id": active_id,
        "chapters": chapters,
        "lessons": serialized_lessons,
        "today_tasks": today_tasks,
        "weekly_activity": weekly,
        "achievements": [
            {"key": "time", "label": "Focused learning", "value": round(total_minutes / 60, 1), "unit": "h"},
            {"key": "lessons", "label": "Completed lessons", "value": completed_lessons, "unit": ""},
            {"key": "tasks", "label": "Completed tasks", "value": completed_tasks, "unit": ""},
        ],
        "summary": {
            "total_progress": total_progress,
            "completed_chapters": completed_chapters,
            "total_chapters": len(chapters),
            "recent_status": next((lesson["status"] for lesson in serialized_lessons if lesson["status"] == "in_progress"), "not_started"),
        },
    }


@router.get("/workspace")
async def get_learning_workspace(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return {"success": True, "data": await _serialize_workspace(db, current_user_id)}


@router.post("/lessons/{lesson_id}/start")
async def start_lesson(
    lesson_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    chapter_id, _ = _parse_lesson_id(lesson_id)
    await _ensure_plan_and_tasks(db, current_user_id)
    progress = await _get_or_create_lesson_progress(db, current_user_id, lesson_id)
    if progress.status != "completed":
        progress.status = "in_progress"
        progress.progress_pct = max(float(progress.progress_pct or 0), 10.0)
        progress.started_at = progress.started_at or _now()

    task_result = await db.execute(
        select(PlanTask)
        .where(PlanTask.user_id == current_user_id, PlanTask.chapter_id == chapter_id)
        .order_by(PlanTask.order_index)
        .limit(1)
    )
    task = task_result.scalar_one_or_none()
    if task and task.status != "completed":
        task.status = "in_progress"

    await db.commit()
    return {"success": True, "data": await _serialize_workspace(db, current_user_id)}


@router.post("/lessons/{lesson_id}/complete")
async def complete_lesson(
    lesson_id: str,
    req: LessonCompleteRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    chapter_id, _ = _parse_lesson_id(lesson_id)
    progress = await _get_or_create_lesson_progress(db, current_user_id, lesson_id)
    progress.status = "completed"
    progress.progress_pct = 100.0
    progress.started_at = progress.started_at or _now()
    progress.completed_at = _now()
    progress.time_spent_minutes = float(progress.time_spent_minutes or 0) + max(1, req.time_spent_minutes)

    all_lessons = _chapter_lessons(chapter_id)
    lesson_progress = await _progress_for_lessons(db, current_user_id, all_lessons)
    if all(
        item["id"] == lesson_id or (lesson_progress.get(item["id"]) and lesson_progress[item["id"]].status == "completed")
        for item in all_lessons
    ):
        task_result = await db.execute(
            select(PlanTask)
            .where(PlanTask.user_id == current_user_id, PlanTask.chapter_id == chapter_id)
            .order_by(PlanTask.order_index)
            .limit(1)
        )
        task = task_result.scalar_one_or_none()
        if task:
            task.status = "completed"
            task.completed_at = _now()
            task.result = {"completed_by": "lesson_progress", "completed_at": _now().isoformat()}

    await db.commit()
    return {"success": True, "data": await _serialize_workspace(db, current_user_id)}


@router.patch("/tasks/{task_id}")
async def update_learning_task(
    task_id: int,
    req: TaskUpdateRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if req.status not in {"pending", "in_progress", "completed", "skipped"}:
        raise HTTPException(400, "invalid task status")
    result = await db.execute(select(PlanTask).where(PlanTask.id == task_id, PlanTask.user_id == current_user_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(404, "task not found")
    task.status = req.status
    if req.status == "completed":
        task.completed_at = _now()
    task.result = {**(task.result or {}), "updated_from": "learning_workspace", "updated_at": _now().isoformat()}
    await db.commit()
    return {"success": True, "data": await _serialize_workspace(db, current_user_id)}
