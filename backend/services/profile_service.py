"""学生画像加载/保存服务"""
from __future__ import annotations

import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.models import StudentProfile
from services.cache_service import CacheService

logger = logging.getLogger(__name__)


PROFILE_STATUS_COMPLETED = "completed"
PROFILE_STATUS_IN_PROGRESS = "in_progress"
PROFILE_STATUS_PAUSED = "paused"
PROFILE_STATUS_SKIPPED = "skipped"
PROFILE_STATUS_NOT_STARTED = "not_started"


_PROFILE_SNAPSHOT_KEYS = {
    "major", "grade_level", "target_role", "prior_courses", "preferred_language",
    "skill_matrix", "cognitive_profile", "misconception_archive", "behavior_pattern",
    "motivation_profile", "capability_matrix", "profile_data", "confidence_score",
    "overall_score", "profile_version",
}


def normalize_profile_data(value: dict | None) -> dict:
    """Recover the flexible profile payload from accidentally persisted snapshots.

    Older assessment writers could place the full ``load_existing_profile`` result
    inside ``StudentProfile.profile_data``. Preserve rule/refiner fields from that
    wrapper, but stop at the first payload that owns the onboarding truth.
    """
    current = dict(value or {})
    recovered: dict = {}
    for _ in range(8):
        if "profile_onboarding" in current:
            break
        nested = current.get("profile_data")
        if not isinstance(nested, dict):
            break
        recovered.update({
            key: item for key, item in current.items()
            if key not in _PROFILE_SNAPSHOT_KEYS
        })
        current = dict(nested)
    current.pop("profile_data", None)
    current.update(recovered)
    return current


def get_profile_status(profile: "StudentProfile | None") -> str:
    """从 StudentProfile 中提取画像状态。

    优先级：
    1. onboarding_status 列（新增的单一事实源）
    2. profile_data.profile_onboarding.status（兼容旧数据）
    3. profile_data.normalized_profile 存在 → completed
    4. StudentProfile 行存在但无明确状态 → needs_review
    5. StudentProfile 不存在 → not_started
    """
    if profile is None:
        return PROFILE_STATUS_NOT_STARTED
    # 1. 检查 onboarding_status 列（新增单一事实源）
    db_status = getattr(profile, 'onboarding_status', None)
    if db_status == "completed":
        return PROFILE_STATUS_COMPLETED
    if db_status == "deferred":
        return PROFILE_STATUS_SKIPPED
    if db_status == "in_progress":
        return PROFILE_STATUS_IN_PROGRESS
    # 2. 兼容旧数据：profile_data.profile_onboarding.status
    pd = normalize_profile_data(profile.profile_data)
    onboarding = pd.get("profile_onboarding")
    if isinstance(onboarding, dict):
        status = onboarding.get("status")
        if status in ("active",):
            return PROFILE_STATUS_IN_PROGRESS
        if status == "suspended":
            return PROFILE_STATUS_PAUSED
        if status == "skipped":
            return PROFILE_STATUS_SKIPPED
        if status == "completed":
            return PROFILE_STATUS_COMPLETED
    # 3. 有 normalized_profile 视为完成
    if pd.get("normalized_profile"):
        return PROFILE_STATUS_COMPLETED
    # 4. 旧数据：有 capability_matrix 或 overall_score 视为完成
    if profile.capability_matrix or profile.overall_score:
        return PROFILE_STATUS_COMPLETED
    return "needs_review"


def is_profile_completed(profile: "StudentProfile | None") -> bool:
    """判断画像是否已完成（可用于路由守卫、功能解锁）。"""
    return get_profile_status(profile) in (PROFILE_STATUS_COMPLETED, PROFILE_STATUS_SKIPPED)


async def load_existing_profile(db: AsyncSession, user_id: int) -> dict | None:
    """加载已有画像（Redis 缓存 → DB 兜底）"""
    # 1. 先查 Redis 缓存
    cached = await CacheService.get_cached_profile(user_id)
    if cached:
        logger.info(f"[Context] 画像缓存命中 user_id={user_id}")
        return cached

    # 2. 缓存未命中，查数据库
    result = await db.execute(
        select(StudentProfile).where(StudentProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile:
        data = {
            "major": profile.major,
            "grade_level": profile.grade_level,
            "target_role": profile.target_role,
            "prior_courses": profile.prior_courses or [],
            "preferred_language": profile.preferred_language,
            "skill_matrix": profile.skill_matrix or {},
            "cognitive_profile": profile.cognitive_profile or {},
            "misconception_archive": profile.misconception_archive or {},
            "behavior_pattern": profile.behavior_pattern or {},
            "motivation_profile": profile.motivation_profile or {},
            "capability_matrix": profile.capability_matrix or {},
            "profile_data": normalize_profile_data(profile.profile_data),
            "confidence_score": profile.confidence_score or 0.5,
            "overall_score": profile.overall_score,
            "profile_version": profile.profile_version or 1
        }
        # 统一适配：从 normalized_profile 填充旧结构化字段
        from services.profile_onboarding import enrich_profile_from_normalized
        data = enrich_profile_from_normalized(data)
        await CacheService.cache_profile(user_id, data)
        logger.info(f"[Context] 画像从DB加载 user_id={user_id} v{data['profile_version']}")
        return data

    logger.info(f"[Context] 无已有画像 user_id={user_id}")
    return None


async def save_profile(db: AsyncSession, user_id: int, profile_data: dict):
    """保存或更新学生画像（6大类 + profile_data JSON）"""
    result = await db.execute(
        select(StudentProfile).where(StudentProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()

    # 新画像的字段列表
    basic_fields = ["major", "grade_level", "target_role", "preferred_language"]
    json_fields = ["prior_courses", "skill_matrix", "cognitive_profile",
                   "misconception_archive", "behavior_pattern", "motivation_profile",
                   "profile_data"]

    if profile:
        # 更新已有画像
        for field in basic_fields:
            if field in profile_data and profile_data[field] is not None:
                value = profile_data[field]
                if field == "profile_data":
                    value = normalize_profile_data(value)
                setattr(profile, field, value)
        for field in json_fields:
            if field in profile_data and profile_data[field] is not None:
                setattr(profile, field, profile_data[field])
        if "confidence_score" in profile_data:
            profile.confidence_score = profile_data["confidence_score"]
        if "profile_version" in profile_data:
            profile.profile_version = profile_data["profile_version"]
    else:
        # 创建新画像
        profile = StudentProfile(
            user_id=user_id,
            major=profile_data.get("major"),
            grade_level=profile_data.get("grade_level"),
            target_role=profile_data.get("target_role"),
            preferred_language=profile_data.get("preferred_language"),
            prior_courses=profile_data.get("prior_courses", []),
            skill_matrix=profile_data.get("skill_matrix", {}),
            cognitive_profile=profile_data.get("cognitive_profile", {}),
            misconception_archive=profile_data.get("misconception_archive", {}),
            behavior_pattern=profile_data.get("behavior_pattern", {}),
            motivation_profile=profile_data.get("motivation_profile", {}),
            profile_data=normalize_profile_data(profile_data.get("profile_data", {})),
            confidence_score=profile_data.get("confidence_score", 0.5),
            profile_version=profile_data.get("profile_version", 1)
        )
        db.add(profile)

    await db.commit()
    logger.info(f"[DB] 画像已保存 user_id={user_id} v{profile.profile_version}")


async def save_answer_records(db, user_id: int, answers: list, source: str, question_map: dict = None):
    """
    批量保存答题记录到 AnswerRecord 表，并异步触发 AI 深度评估。

    Args:
        db: AsyncSession
        user_id: 用户 ID
        answers: 答题结果 [{question_id, correct, time_spent_sec, topic?}, ...]
        source: "exam" | "wizard" | "quiz"
        question_map: {question_id: topic} 映射
    """
    from database.models import AnswerRecord
    from sqlalchemy import insert
    import asyncio as _asyncio

    records = []
    for a in answers:
        topic = a.get("topic", "")
        if not topic and question_map:
            topic = question_map.get(a.get("question_id", ""), "")
        if not topic:
            topic = "unknown"

        records.append({
            "user_id": user_id,
            "topic": topic[:100],
            "question_id": str(a.get("question_id", ""))[:50],
            "correct": bool(a.get("correct", False)),
            "time_spent_sec": float(a.get("time_spent_sec", 0)),
            "source": source,
        })

    if records:
        await db.execute(insert(AnswerRecord), records)
        await db.commit()

    # 异步触发 AI 深度评估（不阻塞）
    _asyncio.create_task(_maybe_refine_profile(user_id))


_last_refine_times: dict = {}

async def _maybe_refine_profile(user_id: int):
    """防抖：5 分钟内不重复触发 AI 深度评估"""
    import time
    now = time.time()
    last = _last_refine_times.get(user_id, 0)
    if last > now - 300:
        return
    _last_refine_times[user_id] = now

    try:
        from services.deep_profile_refiner import refine
        await refine(user_id)
    except Exception:
        pass
