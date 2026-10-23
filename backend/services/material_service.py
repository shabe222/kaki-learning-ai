import json
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import LearningMaterial, MasteryHistory, WeakPoint, StudentProfile
from services.knowledge_graph import KNOWLEDGE_GRAPH

logger = logging.getLogger(__name__)

STAGE_CONFIG = {
    "seedling":  {"min": 0.0, "max": 1.5, "label": "🌱 启蒙", "doc_style": "零公式，用生活类比建立直觉，回答'这是什么？为什么需要它？'", "quiz_ratio": "80%概念辨析+20%简单判断", "max_tokens": 4096},
    "sprout":    {"min": 1.5, "max": 2.5, "label": "📖 入门", "doc_style": "核心原理+关键公式，每步推导都写清楚，多用图示描述", "quiz_ratio": "60%直接应用+30%概念辨析+10%简单推导", "max_tokens": 4096},
    "practice":  {"min": 2.5, "max": 3.5, "label": "🔧 巩固", "doc_style": "变体与对比，易混淆点辨析，与已掌握知识点类比", "quiz_ratio": "30%基础+50%辨析应用+20%综合", "max_tokens": 4096},
    "advanced":  {"min": 3.5, "max": 4.5, "label": "🚀 进阶", "doc_style": "数学推导+代码实现+工程实践，深入底层原理", "quiz_ratio": "20%基础+30%综合+50%开放设计", "max_tokens": 4096},
    "master":    {"min": 4.5, "max": 5.0, "label": "👑 精通", "doc_style": "前沿论文解读+创新应用+教学他人，探索边界", "quiz_ratio": "10%综合+40%设计+50%论文复现", "max_tokens": 4096},
}

EBINGHAUS_INTERVALS = [1, 2, 4, 7, 15, 30]

def get_stage(mastery: float) -> str:
    for stage, cfg in STAGE_CONFIG.items():
        if cfg["min"] <= mastery < cfg["max"]:
            return stage
    return "master" if mastery >= 4.5 else "seedling"

def get_stage_config(stage: str) -> dict:
    return STAGE_CONFIG.get(stage, STAGE_CONFIG["seedling"])

def get_next_review_date(review_count: int, last_reviewed: datetime = None) -> datetime:
    idx = min(review_count, len(EBINGHAUS_INTERVALS) - 1)
    days = EBINGHAUS_INTERVALS[idx]
    base = last_reviewed or datetime.utcnow()
    return base + timedelta(days=days)

def calculate_mastery_delta(correct: bool, difficulty: int, current_mastery: float) -> float:
    base = 0.3 if correct else -0.2
    diff_factor = difficulty / 3.0
    mastery_factor = max(0.5, 1.0 - current_mastery / 5.0)
    delta = base * diff_factor * mastery_factor
    return round(delta, 2)

async def get_user_chapter_mastery(db: AsyncSession, user_id: int, chapter_id: str) -> float:
    result = await db.execute(
        select(MasteryHistory).where(
            and_(MasteryHistory.user_id == user_id, MasteryHistory.chapter_id == chapter_id)
        ).order_by(desc(MasteryHistory.created_at)).limit(1)
    )
    latest = result.scalar_one_or_none()
    if latest:
        return latest.mastery
    cap = await db.execute(
        select(StudentProfile).where(StudentProfile.user_id == user_id)
    )
    profile = cap.scalar_one_or_none()
    if profile and profile.capability_matrix:
        ch_data = profile.capability_matrix.get(chapter_id, {})
        if isinstance(ch_data, dict):
            scores = [v.get("score", 0) for v in ch_data.values() if isinstance(v, dict) and "score" in v]
            if scores:
                return round(sum(scores) / len(scores), 1)
    return 0.0

async def record_mastery(db: AsyncSession, user_id: int, chapter_id: str, new_mastery: float, source: str, detail: dict = None):
    old = await get_user_chapter_mastery(db, user_id, chapter_id)
    entry = MasteryHistory(
        user_id=user_id, chapter_id=chapter_id, mastery=new_mastery, source=source,
        detail={"old_mastery": old, "new_mastery": new_mastery, "delta": round(new_mastery - old, 2), **(detail or {})}
    )
    db.add(entry)
    await db.commit()
    return entry

async def save_material(db: AsyncSession, user_id: int, chapter_id: str, stage: str, content: dict, profile_snapshot: dict = None, weak_points: list = None):
    """
    持久化学习资源到 learning_materials 表。
    P2 关键修复：原顺序是「先标旧记录 is_current=False，再插新记录」—
    若新记录 commit 失败，旧记录已经是 is_current=False，会让该章节的
    "当前资源"凭空消失。M6 测试已验证。
    现改为：先 insert 新记录（commit），再把其他记录标 False（commit）。
    任何一步失败，新记录入库失败或旧记录仍为 is_current=True，资源不丢。
    """
    try:
        mastery = await get_user_chapter_mastery(db, user_id, chapter_id)
        material = LearningMaterial(
            user_id=user_id, chapter_id=chapter_id, stage=stage,
            mastery_at_creation=mastery, content=content,
            profile_snapshot=profile_snapshot, weak_points=weak_points,
            is_current=True, next_review_at=get_next_review_date(0)
        )
        db.add(material)
        await db.commit()           # ① 先把新记录 commit（原子）
        await db.refresh(material)  # ② 重读确认

        # ③ 新记录入库成功后，再把同章节其他记录标为 is_current=False
        await db.execute(
            LearningMaterial.__table__.update().where(
                and_(
                    LearningMaterial.user_id == user_id,
                    LearningMaterial.chapter_id == chapter_id,
                    LearningMaterial.id != material.id
                )
            ).values(is_current=False)
        )
        await db.commit()
        return material
    except Exception as e:
        # 任何失败：rollback 不留半成品
        await db.rollback()
        logger.error(f"[save_material] 写入失败，已 rollback: {e}", exc_info=True)
        raise

async def get_current_material(db: AsyncSession, user_id: int, chapter_id: str):
    result = await db.execute(
        select(LearningMaterial).where(
            and_(LearningMaterial.user_id == user_id, LearningMaterial.chapter_id == chapter_id, LearningMaterial.is_current == True)
        ).order_by(desc(LearningMaterial.created_at)).limit(1)
    )
    return result.scalar_one_or_none()

async def get_all_materials(db: AsyncSession, user_id: int):
    result = await db.execute(
        select(LearningMaterial).where(
            LearningMaterial.user_id == user_id
        ).order_by(LearningMaterial.chapter_id, desc(LearningMaterial.created_at))
    )
    return result.scalars().all()

async def get_chapter_materials(db: AsyncSession, user_id: int, chapter_id: str):
    result = await db.execute(
        select(LearningMaterial).where(
            and_(LearningMaterial.user_id == user_id, LearningMaterial.chapter_id == chapter_id)
        ).order_by(desc(LearningMaterial.created_at))
    )
    return result.scalars().all()

async def get_mastery_history(db: AsyncSession, user_id: int, chapter_id: str, limit: int = 30):
    result = await db.execute(
        select(MasteryHistory).where(
            and_(MasteryHistory.user_id == user_id, MasteryHistory.chapter_id == chapter_id)
        ).order_by(MasteryHistory.created_at).limit(limit)
    )
    return result.scalars().all()

async def get_due_reviews(db: AsyncSession, user_id: int):
    now = datetime.utcnow()
    result = await db.execute(
        select(LearningMaterial).where(
            and_(LearningMaterial.user_id == user_id, LearningMaterial.is_current == True, LearningMaterial.next_review_at <= now)
        )
    )
    return result.scalars().all()

async def record_weak_point(db: AsyncSession, user_id: int, chapter_id: str, knowledge_point: str, doc_section_id: str = None):
    result = await db.execute(
        select(WeakPoint).where(
            and_(WeakPoint.user_id == user_id, WeakPoint.chapter_id == chapter_id, WeakPoint.knowledge_point == knowledge_point, WeakPoint.status == "active")
        )
    )
    existing = result.scalar_one_or_none()
    # === P2 加固：commit 失败时 rollback，写后 verify 计数已加 1 ===
    try:
        if existing:
            old_count = existing.occurrence_count
            existing.occurrence_count += 1
            await db.commit()
            await db.refresh(existing)
            if existing.occurrence_count != old_count + 1:
                raise RuntimeError(f"weak_point 计数未更新: {old_count} -> {existing.occurrence_count}")
            return existing
        wp = WeakPoint(user_id=user_id, chapter_id=chapter_id, knowledge_point=knowledge_point, doc_section_id=doc_section_id)
        db.add(wp)
        await db.commit()
        await db.refresh(wp)
        if not wp.id:
            raise RuntimeError("weak_point 插入后无 id")
        return wp
    except Exception as e:
        await db.rollback()
        logger.error(f"[record_weak_point] 失败，已 rollback: {e}", exc_info=True)
        raise

async def get_weak_points(db: AsyncSession, user_id: int, chapter_id: str = None):
    conditions = [WeakPoint.user_id == user_id, WeakPoint.status == "active"]
    if chapter_id:
        conditions.append(WeakPoint.chapter_id == chapter_id)
    result = await db.execute(select(WeakPoint).where(and_(*conditions)).order_by(desc(WeakPoint.occurrence_count)))
    return result.scalars().all()

async def resolve_weak_point(db: AsyncSession, wp_id: int):
    result = await db.execute(select(WeakPoint).where(WeakPoint.id == wp_id))
    wp = result.scalar_one_or_none()
    if wp:
        wp.status = "resolved"
        wp.resolved_at = datetime.utcnow()
        await db.commit()
    return wp
