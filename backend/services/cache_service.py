"""
Redis 缓存层 - 画像缓存 + 会话管理
禁用时自动降级为空操作，不影响功能
"""

from __future__ import annotations

import json
import logging
from redis.asyncio import Redis
from database.connection import get_redis

logger = logging.getLogger(__name__)


class CacheService:
    """Redis 缓存服务"""

    # 缓存 Key 前缀
    PROFILE_PREFIX = "profile:"
    SESSION_PREFIX = "session:"
    RATE_LIMIT_PREFIX = "rate:"
    LLM_PREFIX = "llm:"

    # 过期时间
    PROFILE_TTL = 3600    # 画像缓存1小时
    SESSION_TTL = 1800    # 会话缓存30分钟
    RATE_TTL = 60         # 限流窗口1分钟
    LLM_DOC_TTL = 86400   # LLM 文档生成缓存24小时
    LLM_QUIZ_TTL = 86400  # LLM 题目生成缓存24小时

    @staticmethod
    def _make_llm_key(agent: str, chapter_id: str, stage: str, profile_hash: str = "", user_id: int = 0) -> str:
        safe_user = int(user_id) if user_id else 0
        return f"{CacheService.LLM_PREFIX}u{safe_user}:{agent}:{chapter_id}:{stage}:{profile_hash}"

    @staticmethod
    async def get_llm_cache(agent: str, chapter_id: str, stage: str, profile_hash: str = "", user_id: int = 0) -> dict | None:
        r = CacheService._get_redis()
        if r is None:
            return None
        key = CacheService._make_llm_key(agent, chapter_id, stage, profile_hash, user_id)
        try:
            data = await r.get(key)
            if data:
                logger.info(f"[Cache] LLM cache hit: {key}")
                return json.loads(data)
        except Exception as e:
            logger.warning(f"[Cache] LLM cache read failed: {e}")
        return None

    @staticmethod
    async def set_llm_cache(agent: str, chapter_id: str, stage: str, content: str, profile_hash: str = "", user_id: int = 0) -> None:
        r = CacheService._get_redis()
        if r is None:
            return
        key = CacheService._make_llm_key(agent, chapter_id, stage, profile_hash, user_id)
        ttl = CacheService.LLM_DOC_TTL if agent == "doc" else CacheService.LLM_QUIZ_TTL
        try:
            await r.setex(key, ttl, json.dumps({"content": content}, ensure_ascii=False))
            logger.info(f"[Cache] LLM cache set: {key}, ttl={ttl}s, len={len(content)}")
        except Exception as e:
            logger.warning(f"[Cache] LLM cache write failed: {e}")

    @staticmethod
    async def invalidate_llm_cache(agent: str = None, chapter_id: str = None, user_id: int = 0) -> None:
        r = CacheService._get_redis()
        if r is None:
            return
        try:
            user_part = f"u{user_id}" if user_id else "u*"
            pattern = f"{CacheService.LLM_PREFIX}{user_part}:{agent or '*'}:{chapter_id or '*'}:*"
            async for key in r.scan_iter(match=pattern, count=100):
                await r.delete(key)
        except Exception:
            pass

    @staticmethod
    def _get_redis() -> Redis | None:
        return get_redis()

    # ===== 画像缓存 =====

    @staticmethod
    async def cache_profile(user_id: int, profile_data: dict) -> None:
        """缓存学生画像"""
        r = CacheService._get_redis()
        if r is None:
            return
        key = f"{CacheService.PROFILE_PREFIX}{user_id}"
        try:
            await r.setex(key, CacheService.PROFILE_TTL, json.dumps(profile_data, ensure_ascii=False))
            logger.debug(f"[Cache] 画像已缓存 user_id={user_id}")
        except Exception as e:
            logger.warning(f"[Cache] 画像缓存失败: {e}")

    @staticmethod
    async def get_cached_profile(user_id: int) -> dict | None:
        """获取缓存的画像"""
        r = CacheService._get_redis()
        if r is None:
            return None
        key = f"{CacheService.PROFILE_PREFIX}{user_id}"
        try:
            data = await r.get(key)
            if data:
                logger.debug(f"[Cache] 画像命中 user_id={user_id}")
                return json.loads(data)
        except Exception as e:
            logger.warning(f"[Cache] 画像读取失败: {e}")
        return None

    @staticmethod
    async def invalidate_profile(user_id: int) -> None:
        """清除画像缓存"""
        r = CacheService._get_redis()
        if r is None:
            return
        key = f"{CacheService.PROFILE_PREFIX}{user_id}"
        try:
            await r.delete(key)
        except Exception as e:
            logger.warning(f"[Cache] 画像缓存清除失败 user_id={user_id}: {e}")

    # ===== 会话上下文缓存 =====

    @staticmethod
    async def cache_session_context(thread_id: str, context: dict) -> None:
        """缓存会话上下文（包含历史消息摘要）"""
        r = CacheService._get_redis()
        if r is None:
            return
        key = f"{CacheService.SESSION_PREFIX}{thread_id}"
        try:
            await r.setex(key, CacheService.SESSION_TTL, json.dumps(context, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"[Cache] 会话缓存失败: {e}")

    @staticmethod
    async def get_session_context(thread_id: str) -> dict | None:
        """获取会话上下文"""
        r = CacheService._get_redis()
        if r is None:
            return None
        key = f"{CacheService.SESSION_PREFIX}{thread_id}"
        try:
            data = await r.get(key)
            if data:
                return json.loads(data)
        except Exception:
            pass
        return None

    # ===== 热点数据缓存 =====

    @staticmethod
    async def cache_json(key: str, data: dict, ttl: int = 300) -> None:
        """通用 JSON 缓存"""
        r = CacheService._get_redis()
        if r is None:
            return
        try:
            await r.setex(key, ttl, json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"[Cache] JSON 缓存写入失败 key={key}: {e}")

    @staticmethod
    async def get_json(key: str) -> dict | None:
        """通用 JSON 读取"""
        r = CacheService._get_redis()
        if r is None:
            return None
        try:
            data = await r.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.warning(f"[Cache] JSON 缓存读取失败 key={key}: {e}")
            return None