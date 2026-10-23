"""AI 深度画像评估——异步调用 DeepSeek 发现隐性弱点"""
import logging
import json
import asyncio
from typing import Dict, Any, Optional

from sqlalchemy import select, desc

logger = logging.getLogger(__name__)


async def refine(user_id: int):
    """
    异步 AI 深度评估。查最近 N 条答题记录 → 发给 DeepSeek → 合并画像更新 → 存 DB。
    不阻塞前端，失败不影响主流程。
    """
    from database.connection import _async_session_factory
    from database.models import AnswerRecord, StudentProfile
    from services.llm_service import LLMService
    from services.profile_service import normalize_profile_data, save_profile
    from config import settings

    if not _async_session_factory:
        logger.warning("[DeepRefiner] 数据库未初始化，跳过")
        return

    try:
        async with _async_session_factory() as db:
            # 查最近 10 条答题记录
            result = await db.execute(
                select(AnswerRecord)
                .where(AnswerRecord.user_id == user_id)
                .order_by(desc(AnswerRecord.created_at))
                .limit(10)
            )
            records = result.scalars().all()
            if len(records) < 3:
                return  # 不够 3 条不分析

            # 查现有画像
            profile_result = await db.execute(
                select(StudentProfile).where(StudentProfile.user_id == user_id)
            )
            profile_row = profile_result.scalar_one_or_none()
            current = normalize_profile_data(profile_row.profile_data) if profile_row else {}

            # 构造 prompt
            records_json = json.dumps([{
                "topic": r.topic, "correct": r.correct,
                "time_spent": r.time_spent_sec, "source": r.source
            } for r in records], ensure_ascii=False)

            prompt = f"""你是学习诊断专家。分析学生答题记录，找出隐性薄弱点和学习模式：

当前画像：{json.dumps(current, ensure_ascii=False)}

最近答题：{records_json}

请返回 JSON（只返回 JSON，不要其他文字）：
{{
  "hidden_weak_points": ["知识点A"],
  "learning_style_suggestion": "xxx",
  "ml_knowledge_adjustment": 0,
  "analysis_note": "一句话解释"
}}"""

            llm = LLMService(provider="deepseek")
            response = await asyncio.wait_for(
                llm.generate(prompt=prompt, temperature=0.3, max_tokens=500),
                timeout=30
            )

            # 解析 AI 返回
            ai_data = _extract_json(response)

            # 合并画像
            if ai_data:
                existing = set(current.get("weak_points", []))
                for wp in ai_data.get("hidden_weak_points", []):
                    existing.add(wp)
                current["weak_points"] = list(existing)

                if ai_data.get("learning_style_suggestion"):
                    current["learning_style"] = ai_data["learning_style_suggestion"]

                adj = ai_data.get("ml_knowledge_adjustment", 0)
                current["ml_knowledge_level"] = max(1, min(10, current.get("ml_knowledge_level", 5) + adj))

                current["_ai_refine_note"] = ai_data.get("analysis_note", "")

                await save_profile(db, user_id, {"profile_data": current})
                logger.info(f"[DeepRefiner] user={user_id} 画像已AI优化: {ai_data.get('analysis_note', '')[:60]}")

    except asyncio.TimeoutError:
        logger.warning(f"[DeepRefiner] user={user_id} AI 超时")
    except Exception as e:
        logger.error(f"[DeepRefiner] user={user_id} 失败: {type(e).__name__}: {e}")


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 返回中提取 JSON，统一使用 LLMService._extract_json()"""
    from services.llm_service import LLMService

    try:
        # LLMService._extract_json 是实例方法，但内部只用 self 做日志，不依赖实例状态
        llm = LLMService(provider="mock")
        return llm._extract_json(text)
    except (ValueError, json.JSONDecodeError):
        return None
