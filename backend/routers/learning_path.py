from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import (
    LearningMaterial,
    LearningPlan,
    LearningResource,
    PlanTask,
)
from services.knowledge_graph import CHAPTER_ORDER, KNOWLEDGE_GRAPH
from services.material_service import get_user_chapter_mastery
from services.material_service import get_stage, save_material
from utils.auth import get_current_user_id, require_self

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/learning-path", tags=["learning-path"])


def json_dumps_fallback(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return str(value)


class StartChapterRequest(BaseModel):
    chapter_id: str


class GenerateResourceRequest(BaseModel):
    chapter_id: str
    resource_type: str = "document"
    title: Optional[str] = None


def _stage_for_ui(mastery: float) -> str:
    if mastery <= 0:
        return "inactive"
    if mastery < 2.0:
        return "weak"
    if mastery < 3.5:
        return "partial"
    if mastery < 4.5:
        return "active"
    return "completed"


@router.get("/{user_id}")
async def get_learning_path(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    require_self(user_id, current_user_id)

    chapters = []
    for index, chapter_id in enumerate(CHAPTER_ORDER, 1):
        chapter = KNOWLEDGE_GRAPH.get(chapter_id, {})
        mastery = await get_user_chapter_mastery(db, user_id, chapter_id)
        stage = _stage_for_ui(mastery)

        task_result = await db.execute(
            select(PlanTask)
            .where(PlanTask.user_id == user_id, PlanTask.chapter_id == chapter_id)
            .order_by(PlanTask.order_index)
            .limit(1)
        )
        task = task_result.scalar_one_or_none()

        material_result = await db.execute(
            select(LearningMaterial)
            .where(
                LearningMaterial.user_id == user_id,
                LearningMaterial.chapter_id == chapter_id,
            )
            .order_by(desc(LearningMaterial.created_at))
            .limit(5)
        )
        materials = material_result.scalars().all()

        resource_result = await db.execute(
            select(LearningResource)
            .where(
                LearningResource.user_id == user_id,
                LearningResource.topic == chapter_id,
            )
            .order_by(desc(LearningResource.created_at))
            .limit(5)
        )
        resources = resource_result.scalars().all()

        chapters.append(
            {
                "id": chapter_id,
                "order": index,
                "name": chapter.get("name", chapter_id),
                "description": chapter.get("description", ""),
                "icon": chapter.get("icon", ""),
                "difficulty": chapter.get("difficulty", 3),
                "estimated_hours": chapter.get("estimated_hours", 0),
                "mastery": mastery,
                "stage": stage,
                "progress_pct": round(min(100, mastery / 5.0 * 100), 1),
                "depends_on": chapter.get("depends_on", []),
                "task_id": task.id if task else None,
                "task_status": task.status if task else None,
                "task_name": task.name if task else None,
                "has_material": len(materials) > 0,
                "has_exam": any(resource.resource_type == "questions" for resource in resources),
                "resource_count": len(resources),
                "material_count": len(materials),
            }
        )

    return {
        "success": True,
        "data": {
            "user_id": user_id,
            "total_chapters": len(chapters),
            "chapters": chapters,
        },
    }


@router.post("/start")
async def start_chapter(
    req: StartChapterRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    chapter = KNOWLEDGE_GRAPH.get(req.chapter_id)
    if not chapter:
        raise HTTPException(404, f"chapter not found: {req.chapter_id}")

    plan_result = await db.execute(
        select(LearningPlan)
        .where(LearningPlan.user_id == current_user_id, LearningPlan.status == "active")
        .order_by(desc(LearningPlan.id))
        .limit(1)
    )
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        plan = LearningPlan(
            user_id=current_user_id,
            goal_type="intro",
            goal_detail="Auto-generated introductory machine learning plan",
            target_role=None,
            deadline=datetime.now() + timedelta(days=90),
            weekly_hours=10.0,
            chapter_ids=list(CHAPTER_ORDER),
            schedule=[],
            status="active",
            profile_snapshot=None,
        )
        db.add(plan)
        await db.flush()
        logger.info(
            "[learning_path] created default LearningPlan plan_id=%s user_id=%s",
            plan.id,
            current_user_id,
        )

    task_result = await db.execute(
        select(PlanTask)
        .where(PlanTask.user_id == current_user_id, PlanTask.chapter_id == req.chapter_id)
        .order_by(PlanTask.order_index)
        .limit(1)
    )
    task = task_result.scalar_one_or_none()
    now = datetime.now()
    if task is None:
        task = PlanTask(
            plan_id=plan.id,
            user_id=current_user_id,
            chapter_id=req.chapter_id,
            name=chapter.get("name", req.chapter_id),
            task_type="new_learn",
            duration_min=30,
            status="in_progress",
            order_index=999,
            task_date=now,
        )
        db.add(task)
    else:
        task.status = "in_progress"

    await db.commit()
    await db.refresh(task)
    return {
        "success": True,
        "data": {
            "task_id": task.id,
            "plan_id": plan.id,
            "chapter_id": task.chapter_id,
            "status": task.status,
            "started_at": now.isoformat(),
        },
    }


@router.get("/chapter/{user_id}/{chapter_id}")
async def get_chapter_detail(
    user_id: int,
    chapter_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    require_self(user_id, current_user_id)
    chapter = KNOWLEDGE_GRAPH.get(chapter_id)
    if not chapter:
        raise HTTPException(404, f"chapter not found: {chapter_id}")

    mastery = await get_user_chapter_mastery(db, user_id, chapter_id)
    stage = _stage_for_ui(mastery)

    material_result = await db.execute(
        select(LearningMaterial)
        .where(LearningMaterial.user_id == user_id, LearningMaterial.chapter_id == chapter_id)
        .order_by(desc(LearningMaterial.created_at))
    )
    materials = material_result.scalars().all()

    resource_result = await db.execute(
        select(LearningResource)
        .where(LearningResource.user_id == user_id, LearningResource.topic == chapter_id)
        .order_by(desc(LearningResource.created_at))
    )
    resources = resource_result.scalars().all()

    task_result = await db.execute(
        select(PlanTask)
        .where(PlanTask.user_id == user_id, PlanTask.chapter_id == chapter_id)
        .order_by(PlanTask.order_index)
        .limit(1)
    )
    task = task_result.scalar_one_or_none()

    return {
        "success": True,
        "data": {
            "id": chapter_id,
            "name": chapter.get("name", chapter_id),
            "description": chapter.get("description", ""),
            "icon": chapter.get("icon", ""),
            "knowledge_points": chapter.get("knowledge_points", []),
            "mastery": mastery,
            "stage": stage,
            "task_id": task.id if task else None,
            "task_status": task.status if task else None,
            "materials": [
                {
                    "id": material.id,
                    "title": (material.content or {}).get(
                        "title", chapter.get("name", chapter_id)
                    )
                    if material.content
                    else chapter.get("name", chapter_id),
                    "stage": material.stage,
                    "is_current": material.is_current,
                    "created_at": material.created_at.isoformat()
                    if material.created_at
                    else None,
                }
                for material in materials
            ],
            "resources": [
                {
                    "id": resource.id,
                    "type": resource.resource_type,
                    "title": resource.title or "",
                    "tokens_used": resource.tokens_used or 0,
                    "created_at": resource.created_at.isoformat()
                    if resource.created_at
                    else None,
                }
                for resource in resources
            ],
        },
    }


@router.post("/{user_id}/generate-material")
async def generate_material(
    user_id: int,
    req: GenerateResourceRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    require_self(user_id, current_user_id)
    chapter = KNOWLEDGE_GRAPH.get(req.chapter_id)
    if not chapter:
        raise HTTPException(404, f"chapter not found: {req.chapter_id}")

    from routers.agents_day4 import (
        TopicRequest,
        code_generate,
        document_generate,
        mindmap_generate,
        question_generate,
    )

    handlers = {
        "document": document_generate,
        "questions": question_generate,
        "code": code_generate,
        "mindmap": mindmap_generate,
    }
    handler = handlers.get(req.resource_type)
    if handler is None:
        raise HTTPException(400, "unsupported resource_type")

    topic = chapter.get("name", req.chapter_id)
    payload = TopicRequest(user_id=str(user_id), topic=topic)
    if req.resource_type == "questions":
        payload.count = 5
    elif req.resource_type == "code":
        payload.language = "python"

    try:
        result = await handler(payload, current_user_id=current_user_id, db=db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[learning_path] lazy generation failed: %s", exc)
        raise HTTPException(500, f"generation failed: {str(exc)[:200]}")

    if req.resource_type in ("document", "questions"):
        try:
            mastery = await get_user_chapter_mastery(db, user_id, req.chapter_id)
            stage = get_stage(mastery)
            if req.resource_type == "document":
                document = result.get("document") if isinstance(result, dict) else None
                content_text = ""
                if isinstance(document, dict):
                    content_text = str(document.get("content") or document.get("text") or "")
                elif isinstance(document, str):
                    content_text = document
                material_content = {
                    "document": {
                        "title": f"{topic} 讲义",
                        "content": content_text or json_dumps_fallback(result),
                        "sections": [],
                    },
                    "questions": [],
                }
            else:
                questions = result.get("questions") if isinstance(result, dict) else []
                material_content = {
                    "document": {
                        "title": f"{topic} 练习",
                        "content": "",
                        "sections": [],
                    },
                    "questions": questions if isinstance(questions, list) else [],
                }
            await save_material(db, user_id, req.chapter_id, stage, material_content)
        except Exception as exc:
            logger.warning("[learning_path] material sync failed: %s", exc)

    return {
        "success": True,
        "data": {
            "chapter_id": req.chapter_id,
            "resource_type": req.resource_type,
            "topic": topic,
            "result": result,
        },
    }
