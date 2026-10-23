import json
import logging
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy import select, and_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import LearningPlan, PlanTask, StudentProfile, LearningMaterial, MasteryHistory
from services.knowledge_graph import KNOWLEDGE_GRAPH, CHAPTER_ORDER
from services.material_service import get_stage, STAGE_CONFIG, get_user_chapter_mastery

logger = logging.getLogger(__name__)

GOAL_CONFIG = {
    "exam": {"label": "📝 考试冲刺", "desc": "期末/考研备考", "default_weeks": 4, "focus": "高频考点+推导题+计算题"},
    "project": {"label": "💼 项目实战", "desc": "做一个ML项目", "default_weeks": 6, "focus": "代码实现+实验报告"},
    "paper": {"label": "🔬 论文研读", "desc": "读懂某篇论文", "default_weeks": 3, "focus": "数学推导+前沿理解"},
    "intro": {"label": "🎓 系统入门", "desc": "从零学ML", "default_weeks": 12, "focus": "基础概念+编程实践"},
    "remedial": {"label": "🔧 补弱提分", "desc": "针对性补短板", "default_weeks": 3, "focus": "薄弱章节+易错点"},
    "extend": {"label": "🚀 技能拓展", "desc": "学新方向", "default_weeks": 4, "focus": "进阶方向+实战应用"},
    "custom": {"label": "🎯 自定义", "desc": "自定义目标", "default_weeks": 4, "focus": "灵活安排"},
}

def get_goal_config(goal_type: str) -> dict:
    return GOAL_CONFIG.get(goal_type, GOAL_CONFIG["custom"])

def resolve_chapters_for_goal(goal_type: str, chapter_scores: dict, target_chapters: list = None) -> list:
    if target_chapters:
        return [c for c in target_chapters if c in KNOWLEDGE_GRAPH]
    if goal_type == "exam":
        return [c for c in CHAPTER_ORDER if chapter_scores.get(c, 0) < 80]
    if goal_type == "remedial":
        return [c for c in CHAPTER_ORDER if 0 < chapter_scores.get(c, 0) < 60]
    if goal_type == "intro":
        return list(CHAPTER_ORDER)
    if goal_type == "extend":
        return [c for c in CHAPTER_ORDER if chapter_scores.get(c, 0) >= 60]
    if goal_type == "project":
        return ["programming", "supervised_learning", "model_evaluation", "advanced_interest"]
    if goal_type == "paper":
        return ["math_foundation", "neural_networks", "deep_learning", "advanced_interest"]
    return list(CHAPTER_ORDER)

def topological_sort(chapter_ids: list) -> list:
    in_degree = {c: 0 for c in chapter_ids}
    adj = {c: [] for c in chapter_ids}
    for c in chapter_ids:
        ch = KNOWLEDGE_GRAPH.get(c, {})
        for dep in ch.get("depends_on", []):
            if dep in in_degree:
                in_degree[c] += 1
                adj[dep].append(c)
    result = []
    queue = [c for c in chapter_ids if in_degree[c] == 0]
    while queue:
        queue.sort(key=lambda x: KNOWLEDGE_GRAPH.get(x, {}).get("difficulty", 3))
        node = queue.pop(0)
        result.append(node)
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    remaining = [c for c in chapter_ids if c not in result]
    return result + remaining

def generate_schedule(chapter_ids: list, chapter_scores: dict, weekly_hours: float, deadline: datetime = None) -> list:
    sorted_chapters = topological_sort(chapter_ids)
    tasks = []
    total_hours = 0
    for ch_id in sorted_chapters:
        ch = KNOWLEDGE_GRAPH.get(ch_id, {})
        score = chapter_scores.get(ch_id, 0)
        est_hours = ch.get("estimated_hours", 4)
        if score >= 80:
            learn_hours = est_hours * 0.2
            task_types = ["review"]
        elif score >= 40:
            learn_hours = est_hours * 0.6
            task_types = ["new_learn", "quiz"]
        else:
            learn_hours = est_hours * 1.0
            task_types = ["new_learn", "quiz", "derivation"]
        mastery = score / 100 * 5
        stage = get_stage(mastery)
        if stage in ("practice", "advanced", "master"):
            task_types.append("code_lab")
        if stage in ("advanced", "master"):
            task_types.append("compare")
        total_hours += learn_hours
        tasks.append({
            "chapter_id": ch_id,
            "chapter_name": ch.get("name", ch_id),
            "icon": ch.get("icon", "📌"),
            "estimated_hours": round(learn_hours, 1),
            "task_types": task_types,
            "mastery": mastery,
            "stage": stage,
            "score": score,
        })
    if deadline:
        days_available = max(1, (deadline - datetime.utcnow()).days)
    else:
        days_available = max(1, int(total_hours / (weekly_hours / 7)))
    hours_per_day = total_hours / days_available
    schedule = []
    current_date = datetime.utcnow().replace(hour=9, minute=0, second=0, microsecond=0)
    remaining_hours_today = hours_per_day
    for task in tasks:
        hours_left = task["estimated_hours"]
        while hours_left > 0:
            alloc = min(hours_left, remaining_hours_today)
            schedule.append({
                "date": current_date.strftime("%Y-%m-%d"),
                "chapter_id": task["chapter_id"],
                "chapter_name": task["chapter_name"],
                "icon": task["icon"],
                "task_type": task["task_types"][0] if task["task_types"] else "new_learn",
                "duration_min": int(alloc * 60),
                "status": "pending",
                "mastery": task["mastery"],
                "stage": task["stage"],
            })
            hours_left -= alloc
            remaining_hours_today -= alloc
            if remaining_hours_today <= 0:
                current_date += timedelta(days=1)
                remaining_hours_today = hours_per_day
    return schedule

async def create_plan(db: AsyncSession, user_id: int, goal_type: str, goal_detail: str = None,
                      deadline: datetime = None, weekly_hours: float = 10.0, target_chapters: list = None) -> LearningPlan:
    result = await db.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
    profile = result.scalar_one_or_none()
    chapter_scores = {}
    if profile and profile.capability_matrix:
        for ch_id, kp_data in profile.capability_matrix.items():
            if isinstance(kp_data, dict):
                scores = [v.get("score", 0) for v in kp_data.values() if isinstance(v, dict) and "score" in v]
                if scores:
                    chapter_scores[ch_id] = sum(scores) / len(scores)
    chapter_ids = resolve_chapters_for_goal(goal_type, chapter_scores, target_chapters)
    schedule = generate_schedule(chapter_ids, chapter_scores, weekly_hours, deadline)
    profile_snapshot = None
    if profile:
        profile_snapshot = {
            "major": profile.major, "grade_level": profile.grade_level,
            "skill_matrix": profile.skill_matrix, "cognitive_profile": profile.cognitive_profile,
        }
    plan = LearningPlan(
        user_id=user_id, goal_type=goal_type, goal_detail=goal_detail,
        deadline=deadline, weekly_hours=weekly_hours, chapter_ids=chapter_ids,
        schedule=schedule, status="active", profile_snapshot=profile_snapshot,
    )
    db.add(plan)
    # === P2 修复：先 flush 让 plan 拿到自增 id，再 add tasks（外键依赖）===
    # 修复前：先 commit plan 再 commit tasks，第二步失败 → orphan plan
    # 修复后：flush(plan) → SQLAlchemy 分配 plan.id 但不落盘 → add tasks → commit(一起落盘)
    # 中途失败 → 整个事务 rollback（plan 和 tasks 都不入库，无 orphan）
    try:
        await db.flush()  # 让 plan.id 被分配（不入库）
        for i, s in enumerate(schedule):
            task = PlanTask(
                plan_id=plan.id, user_id=user_id, chapter_id=s["chapter_id"],
                name=s.get("chapter_name", s["chapter_id"]),
                task_type=s["task_type"], task_date=datetime.strptime(s["date"], "%Y-%m-%d") if s.get("date") else None,
                duration_min=s["duration_min"], status="pending", order_index=i,
            )
            db.add(task)
        await db.commit()           # plan + tasks 一起落盘
        await db.refresh(plan)
        # 写后验证：plan 至少有一个 task
        tasks_count = (await db.execute(
            select(func.count()).select_from(PlanTask).where(PlanTask.plan_id == plan.id)
        )).scalar() or 0
        if not plan.id or tasks_count == 0:
            raise RuntimeError(f"create_plan 写后验证失败: plan.id={plan.id} tasks={tasks_count}")
        return plan
    except Exception as e:
        # === 关键：必须 rollback，否则 flush() 出去的 plan 会留下 orphan ===
        await db.rollback()
        logger.error(f"[create_plan] 失败，已 rollback: {e}", exc_info=True)
        # 二次验证：rollback 后必须显式 expire，清除 session 内残留对象
        try:
            db.expire_all()
        except Exception: pass
        raise HTTPException(500, f"学习计划创建失败: {str(e)[:200]}")

async def get_active_plan(db: AsyncSession, user_id: int) -> LearningPlan:
    result = await db.execute(
        select(LearningPlan).where(
            and_(LearningPlan.user_id == user_id, LearningPlan.status == "active")
        ).order_by(desc(LearningPlan.created_at)).limit(1)
    )
    return result.scalar_one_or_none()

async def get_plan_tasks(db: AsyncSession, plan_id: int) -> list:
    result = await db.execute(
        select(PlanTask).where(PlanTask.plan_id == plan_id).order_by(PlanTask.order_index)
    )
    return result.scalars().all()

async def complete_task(db: AsyncSession, task_id: int, result_data: dict = None) -> PlanTask:
    result = await db.execute(select(PlanTask).where(PlanTask.id == task_id))
    task = result.scalar_one_or_none()
    if task:
        try:
            task.status = "completed"
            task.completed_at = datetime.utcnow()
            if result_data:
                task.result = result_data
            await db.commit()
            # 写后验证：状态必须变成 completed
            await db.refresh(task)
            if task.status != "completed":
                raise RuntimeError(f"complete_task 状态未更新: 实际={task.status}")
            await _update_plan_progress(db, task.plan_id)
        except Exception as e:
            await db.rollback()
            logger.error(f"[complete_task] 失败，已 rollback: {e}", exc_info=True)
            raise
    return task

async def _update_plan_progress(db: AsyncSession, plan_id: int):
    result = await db.execute(select(PlanTask).where(PlanTask.plan_id == plan_id))
    tasks = result.scalars().all()
    if not tasks:
        return
    completed = sum(1 for t in tasks if t.status == "completed")
    pct = round(completed / len(tasks) * 100, 1)
    plan_result = await db.execute(select(LearningPlan).where(LearningPlan.id == plan_id))
    plan = plan_result.scalar_one_or_none()
    if plan:
        plan.progress_pct = pct
        if pct >= 100:
            plan.status = "completed"
        await db.commit()

async def adjust_plan(db: AsyncSession, plan_id: int, chapter_scores: dict) -> LearningPlan:
    result = await db.execute(select(LearningPlan).where(LearningPlan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        return None
    new_schedule = generate_schedule(plan.chapter_ids, chapter_scores, plan.weekly_hours, plan.deadline)
    plan.schedule = new_schedule
    await db.execute(PlanTask.__table__.delete().where(PlanTask.plan_id == plan_id))
    for i, s in enumerate(new_schedule):
        task = PlanTask(
            plan_id=plan_id, user_id=plan.user_id, chapter_id=s["chapter_id"],
            task_type=s["task_type"], task_date=datetime.strptime(s["date"], "%Y-%m-%d") if s.get("date") else None,
            duration_min=s["duration_min"], status="pending", order_index=i,
        )
        db.add(task)
    await db.commit()
    await db.refresh(plan)
    return plan

async def get_weekly_schedule(db: AsyncSession, user_id: int) -> list:
    plan = await get_active_plan(db, user_id)
    if not plan:
        return []
    tasks = await get_plan_tasks(db, plan.id)
    today = datetime.utcnow().date()
    week_start = today - timedelta(days=today.weekday())
    week_tasks = []
    for t in tasks:
        if t.task_date:
            td = t.task_date.date() if isinstance(t.task_date, datetime) else t.task_date
            if week_start <= td <= week_start + timedelta(days=6):
                ch = KNOWLEDGE_GRAPH.get(t.chapter_id, {})
                week_tasks.append({
                    "id": t.id, "date": str(td), "chapter_id": t.chapter_id,
                    "chapter_name": ch.get("name", t.chapter_id), "icon": ch.get("icon", "📌"),
                    "task_type": t.task_type, "duration_min": t.duration_min,
                    "status": t.status, "result": t.result,
                })
    return week_tasks
