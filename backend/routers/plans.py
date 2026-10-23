from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import LearningPlan, PlanTask, StudentProfile
from services.knowledge_graph import KNOWLEDGE_GRAPH
from services.plan_service import (
    GOAL_CONFIG,
    adjust_plan,
    complete_task,
    create_plan,
    get_active_plan,
    get_plan_tasks,
    get_weekly_schedule,
)
from utils.auth import get_current_user_id, require_self

router = APIRouter(prefix="/api/plans", tags=["plans"])


class CreatePlanRequest(BaseModel):
    user_id: int
    goal_type: str
    goal_detail: Optional[str] = None
    deadline: Optional[str] = None
    weekly_hours: float = 10.0
    target_chapters: Optional[List[str]] = None


class CompleteTaskRequest(BaseModel):
    result_data: Optional[dict] = None


def _serialize_task(task: PlanTask) -> dict:
    return {
        "id": task.id,
        "chapter_id": task.chapter_id,
        "name": getattr(task, "name", None),
        "task_type": task.task_type,
        "task_date": str(task.task_date) if task.task_date else None,
        "duration_min": task.duration_min,
        "status": task.status,
        "order_index": task.order_index,
        "result": task.result,
    }


def _serialize_plan(plan: LearningPlan, tasks: list[PlanTask]) -> dict:
    return {
        "id": plan.id,
        "goal_type": plan.goal_type,
        "goal_detail": plan.goal_detail,
        "deadline": str(plan.deadline) if plan.deadline else None,
        "chapter_ids": plan.chapter_ids,
        "schedule": plan.schedule,
        "progress_pct": plan.progress_pct,
        "status": plan.status,
        "tasks": [_serialize_task(task) for task in tasks],
    }


@router.post("/generate-from-profile/{user_id}")
async def generate_plan_from_profile(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    require_self(user_id, current_user_id)

    result = await db.execute(
        select(StudentProfile)
        .where(StudentProfile.user_id == user_id)
        .order_by(StudentProfile.id.desc())
        .limit(1)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(400, "profile is required before generating a plan")

    target_role = (profile.target_role or "").lower()
    goal_map = {
        "software": "software_engineer",
        "algorithm": "algorithm_engineer",
        "data": "data_scientist",
        "ai": "ai_engineer",
        "student": "ml_student",
    }
    goal_type = "ml_student"
    for keyword, mapped_goal_type in goal_map.items():
        if keyword in target_role:
            goal_type = mapped_goal_type
            break

    capability = profile.capability_matrix or {}
    if isinstance(capability, str):
        import json

        try:
            capability = json.loads(capability)
        except Exception:
            capability = {}

    weak_chapters = []
    for chapter_id, point_data in capability.items():
        if not isinstance(point_data, dict):
            continue
        scores = [
            value.get("score", 3)
            for value in point_data.values()
            if isinstance(value, dict)
        ]
        average = sum(scores) / len(scores) if scores else 3
        if average < 3.5:
            weak_chapters.append(chapter_id)
    if not weak_chapters:
        weak_chapters = list(KNOWLEDGE_GRAPH.keys())[:8]

    role = profile.target_role or "machine learning engineer"
    goal_detail = (
        f"Auto-generated learning path based on profile target role: {role}. "
        f"Weak chapters: {', '.join(weak_chapters[:4])}."
    )
    selected_chapters = weak_chapters[:8]
    plan = await create_plan(
        db,
        user_id,
        goal_type,
        goal_detail,
        deadline=None,
        weekly_hours=10.0,
        target_chapters=selected_chapters,
    )
    tasks = await get_plan_tasks(db, plan.id)

    return {
        "success": True,
        "data": {
            "plan_id": plan.id,
            "goal_type": goal_type,
            "goal_detail": goal_detail,
            "chapter_count": len(selected_chapters),
            "tasks_count": len(tasks),
            "message": f"generated learning path with {len(selected_chapters)} stages",
        },
    }


@router.get("/goals")
async def list_goals():
    return {
        "success": True,
        "data": {
            key: {
                "label": value["label"],
                "desc": value["desc"],
                "default_weeks": value["default_weeks"],
                "focus": value["focus"],
            }
            for key, value in GOAL_CONFIG.items()
        },
    }


@router.get("/chapters")
async def list_chapters():
    chapters = []
    for chapter_id, chapter in KNOWLEDGE_GRAPH.items():
        chapters.append(
            {
                "id": chapter_id,
                "name": chapter["name"],
                "icon": chapter.get("icon", ""),
                "difficulty": chapter.get("difficulty", 3),
                "estimated_hours": chapter.get("estimated_hours", 0),
                "depends_on": chapter.get("depends_on", []),
            }
        )
    return {"success": True, "data": chapters}


@router.post("/create")
async def api_create_plan(
    req: CreatePlanRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if req.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="cannot create plan for another user")

    deadline = None
    if req.deadline:
        try:
            deadline = datetime.fromisoformat(req.deadline)
        except Exception:
            pass

    plan = await create_plan(
        db,
        req.user_id,
        req.goal_type,
        req.goal_detail,
        deadline,
        req.weekly_hours,
        req.target_chapters,
    )
    tasks = await get_plan_tasks(db, plan.id)
    return {"success": True, "data": _serialize_plan(plan, tasks)}


@router.get("/active/{user_id}")
async def api_get_active_plan(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    require_self(user_id, current_user_id)
    plan = await get_active_plan(db, user_id)
    if not plan:
        return {"success": True, "data": None}
    tasks = await get_plan_tasks(db, plan.id)
    return {"success": True, "data": _serialize_plan(plan, tasks)}


@router.post("/task/{task_id}/complete")
async def api_complete_task(
    task_id: int,
    req: CompleteTaskRequest = None,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    owner = await db.execute(select(PlanTask.user_id).where(PlanTask.id == task_id))
    task_owner_id = owner.scalar_one_or_none()
    if task_owner_id is None or task_owner_id != current_user_id:
        raise HTTPException(404, "task not found")

    task = await complete_task(db, task_id, req.result_data if req else None)
    if not task:
        raise HTTPException(404, "task not found")
    return {"success": True, "data": {"id": task.id, "status": task.status}}


@router.get("/weekly/{user_id}")
async def api_weekly_schedule(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    require_self(user_id, current_user_id)
    tasks = await get_weekly_schedule(db, user_id)
    return {"success": True, "data": tasks}


@router.post("/adjust/{plan_id}")
async def api_adjust_plan(
    plan_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    owner = await db.execute(select(LearningPlan.user_id).where(LearningPlan.id == plan_id))
    plan_owner_id = owner.scalar_one_or_none()
    if plan_owner_id is None or plan_owner_id != current_user_id:
        raise HTTPException(404, "plan not found")

    plan = await adjust_plan(db, plan_id, {})
    if not plan:
        raise HTTPException(404, "plan not found")
    return {
        "success": True,
        "data": {
            "id": plan.id,
            "schedule": plan.schedule,
            "progress_pct": plan.progress_pct,
        },
    }
