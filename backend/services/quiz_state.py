"""
答题进度状态 — 内存缓存 + DB 持久化（profile_detail.quiz_progress）

内存缓存作为快速访问层，DB 持久化防止服务重启丢数据。
"""
from __future__ import annotations

import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import StudentProfile

logger = logging.getLogger(__name__)

# 内存级缓存（快速读写，生产环境可替换为 Redis）
quiz_progress_store: Dict[str, Dict[str, Any]] = {}
# 记录上次从 DB 加载的时间，避免频繁 DB 读取
_last_db_load: Dict[int, float] = {}
# DB 加载冷却时间（秒）
DB_LOAD_COOLDOWN = 10
# quiz session 超时时间
QUIZ_SESSION_TIMEOUT_HOURS = 24


async def _load_quiz_progress_from_db(db: AsyncSession, user_id: int) -> Optional[Dict[str, Any]]:
    """从 StudentProfile.profile_detail 读取 quiz_progress"""
    result = await db.execute(
        select(StudentProfile).where(StudentProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if not profile or not profile.profile_detail:
        return None
    return profile.profile_detail.get("quiz_progress")


async def _save_quiz_progress_to_db(db: AsyncSession, user_id: int, progress: Dict[str, Any]):
    """将 quiz_progress 写入 StudentProfile.profile_detail"""
    result = await db.execute(
        select(StudentProfile).where(StudentProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        profile = StudentProfile(user_id=user_id)
        db.add(profile)
    existing_detail = dict(profile.profile_detail or {})
    existing_detail["quiz_progress"] = progress
    profile.profile_detail = existing_detail
    await db.commit()


def _is_session_expired(progress: Dict[str, Any]) -> bool:
    """检查 quiz session 是否超过 24 小时"""
    start_time_str = progress.get("start_time", "")
    if not start_time_str:
        return False
    try:
        start_time = datetime.fromisoformat(start_time_str)
        return datetime.now() - start_time > timedelta(hours=QUIZ_SESSION_TIMEOUT_HOURS)
    except (ValueError, TypeError):
        return False


async def get_quiz_progress(db: AsyncSession, user_id: int, force_reload: bool = False) -> Dict[str, Any]:
    """
    获取用户的完整 quiz progress（内存优先，DB 兜底）。
    返回格式: { "start_time": "...", "current_chapter": "ml_overview", "current_index": 0,
                 "chapters": { "ml_overview": {"correct":0, "asked":0, "last_correct":null}, ... } }
    """
    now = time.time()
    # 检查内存缓存
    user_keys_exist = any(k.startswith(f"{user_id}_") for k in quiz_progress_store)
    last_load = _last_db_load.get(user_id, 0)

    if not force_reload and user_keys_exist and (now - last_load < DB_LOAD_COOLDOWN):
        # 从内存重建完整 progress
        progress = _rebuild_progress_from_memory(user_id)
        if progress:
            return progress

    # 从 DB 加载
    db_progress = await _load_quiz_progress_from_db(db, user_id)
    if db_progress and not _is_session_expired(db_progress):
        # 同步到内存
        _sync_db_to_memory(user_id, db_progress)
        _last_db_load[user_id] = now
        return db_progress

    # 过期或不存在，返回空
    return {}


async def save_quiz_progress(db: AsyncSession, user_id: int, progress: Dict[str, Any]):
    """同时写入内存和 DB"""
    # 写入内存
    _sync_progress_to_memory(user_id, progress)
    # 写入 DB
    await _save_quiz_progress_to_db(db, user_id, progress)
    _last_db_load[user_id] = time.time()


async def clear_quiz_progress(db: AsyncSession, user_id: int):
    """清除用户的所有 quiz progress（内存 + DB）"""
    # 清除内存
    keys_to_del = [k for k in quiz_progress_store if k.startswith(f"{user_id}_")]
    for k in keys_to_del:
        del quiz_progress_store[k]
    quiz_progress_store.pop(f"_meta_{user_id}", None)
    _last_db_load.pop(user_id, None)

    # 清除 DB
    result = await db.execute(
        select(StudentProfile).where(StudentProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile and profile.profile_detail:
        existing_detail = dict(profile.profile_detail)
        existing_detail.pop("quiz_progress", None)
        profile.profile_detail = existing_detail
        await db.commit()


def _rebuild_progress_from_memory(user_id: int) -> Dict[str, Any]:
    """从内存中的 per-chapter 数据重建完整 progress"""
    meta = quiz_progress_store.get(f"_meta_{user_id}")
    chapters = {}
    for k, v in quiz_progress_store.items():
        if isinstance(k, str) and k.startswith(f"{user_id}_") and not k.startswith(f"{user_id}_meta_"):
            chapter = k[len(str(user_id)) + 1:]  # 去掉 "user_id_"
            chapters[chapter] = dict(v)
    if not chapters and not meta:
        return {}
    return {
        "start_time": meta.get("start_time", datetime.now().isoformat()) if meta else datetime.now().isoformat(),
        "current_chapter": meta.get("current_chapter", ""),
        "current_index": meta.get("current_index", 0),
        "chapters": chapters,
    }


def _sync_db_to_memory(user_id: int, progress: Dict[str, Any]):
    """将 DB progress 同步到内存"""
    chapters = progress.get("chapters", {})
    for ch_id, ch_data in chapters.items():
        key = f"{user_id}_{ch_id}"
        quiz_progress_store[key] = dict(ch_data)
    quiz_progress_store[f"_meta_{user_id}"] = {
        "start_time": progress.get("start_time", ""),
        "current_chapter": progress.get("current_chapter", ""),
        "current_index": progress.get("current_index", 0),
    }


def _sync_progress_to_memory(user_id: int, progress: Dict[str, Any]):
    """将完整 progress 同步到内存"""
    chapters = progress.get("chapters", {})
    for ch_id, ch_data in chapters.items():
        key = f"{user_id}_{ch_id}"
        quiz_progress_store[key] = dict(ch_data)
    quiz_progress_store[f"_meta_{user_id}"] = {
        "start_time": progress.get("start_time", datetime.now().isoformat()),
        "current_chapter": progress.get("current_chapter", ""),
        "current_index": progress.get("current_index", 0),
    }


def get_chapter_progress_from_memory(user_id: int, chapter: str) -> Dict[str, Any]:
    """快速获取单章节进度（仅内存，不查 DB）"""
    key = f"{user_id}_{chapter}"
    return quiz_progress_store.get(key, {"correct": 0, "asked": 0, "last_correct": None})


def set_chapter_progress_in_memory(user_id: int, chapter: str, data: Dict[str, Any]):
    """快速设置单章节进度（仅内存）"""
    quiz_progress_store[f"{user_id}_{chapter}"] = data
