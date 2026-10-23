"""系统级路由 - 资源读取 / 进度读取 / 健康检查"""
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import database.connection as db_conn
from database.models import LearningResource, LearningProgress
from database.dependencies import get_db
from utils.auth import get_current_user_id, require_self
from utils.errors import BusinessError, ErrorCode
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/test/business-error")
async def test_business_error():
    """
    测试路由（Day 4 — 1a）
    故意抛出 BusinessError，验证全局 handler 返回标准 JSON。
    访问：http://127.0.0.1:PORT/api/test/business-error
    期望：504 + {"code": "LLM_001", "message": "...", "detail": "..."}
    """
    if not settings.DEBUG:
        raise HTTPException(status_code=404, detail="Not found")
    raise BusinessError(
        ErrorCode.LLM_TIMEOUT,
        detail="测试用 — 验证全局 BusinessError handler 是否生效",
    )


@router.get("/api/resources/{user_id}")
async def get_user_resources(
    user_id: int,
    resource_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id)
):
    """获取用户的学习资源"""
    require_self(user_id, current_user_id)
    query = select(LearningResource).where(LearningResource.user_id == user_id)
    if resource_type:
        query = query.where(LearningResource.resource_type == resource_type)
    query = query.order_by(LearningResource.created_at.desc()).limit(50)

    result = await db.execute(query)
    resources = result.scalars().all()

    return {
        "success": True,
        "data": [
            {
                "id": r.id,
                "resource_type": r.resource_type,
                "title": r.title,
                "content": r.content,
                "topic": r.topic,
                "difficulty": r.difficulty,
                "created_at": str(r.created_at)
            }
            for r in resources
        ]
    }


@router.get("/api/progress/{user_id}")
async def get_progress(user_id: int, db: AsyncSession = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    """获取学习进度"""
    require_self(user_id, current_user_id)
    result = await db.execute(
        select(LearningProgress)
        .where(LearningProgress.user_id == user_id)
        .order_by(LearningProgress.stage_index)
    )
    stages = result.scalars().all()

    return {
        "success": True,
        "data": [
            {
                "stage_name": s.stage_name,
                "stage_index": s.stage_index,
                "progress_pct": s.progress_pct,
                "status": s.status,
                "completed_exercises": s.completed_exercises,
                "total_exercises": s.total_exercises,
                "time_spent_minutes": s.time_spent_minutes
            }
            for s in stages
        ]
    }


@router.get("/api/health")
async def health_check():
    """服务健康检查（含 LLM 模式：mock / production / degraded / failed）"""
    from services.vector_service import get_milvus
    from services.oss_service import get_minio
    from services.llm_service import get_llm_status
    from services.llm_service_real import get_runtime_llm_status

    llm_status = get_llm_status()
    conversation_status = get_runtime_llm_status()
    db_engine = str(getattr(settings, "DB_ENGINE", "") or "").lower()
    return {
        "status": "ok",
        "services": {
            # 如实报告实际引擎，不再用「有 session factory」冒充 Postgres：
            # 之前 SQLite 部署下这个字段恒为 true，运维会读成 Postgres 正常。
            "database": db_engine or "unknown",
            "postgresql": db_engine == "postgresql" and db_conn._async_session_factory is not None,
            "redis": db_conn._redis_client is not None,
            "milvus": get_milvus() is not None,
            "minio": get_minio() is not None,
        },
        # 对话链路（/darya）与后台 agent 用的是两个工厂，两个都报，避免运维看错对象。
        "llm": {**llm_status, "conversation": conversation_status},
        "mock": settings.LLM_PROVIDER == "mock" or settings.MODE == "mock",
        "backend": "connected",
        "provider": settings.LLM_PROVIDER if settings.LLM_PROVIDER else "unknown",
        "mode": settings.MODE,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


# ──────────────────────────────────────────────────────────────
# 今日一句（learn.html 侧栏）
# ──────────────────────────────────────────────────────────────
# 设计：基于日期确定性返回（同一天同一条），不随机、不写死到 HTML。
# 30 条古文名句按 day_of_year 索引，365 天一轮回，覆盖常用学习场景。
_DAILY_QUOTES = [
    ("千里之行，始于足下。", "老子"),
    ("学而时习之，不亦说乎？", "孔子"),
    ("路漫漫其修远兮，吾将上下而求索。", "屈原"),
    ("业精于勤，荒于嬉；行成于思，毁于随。", "韩愈"),
    ("不积跬步，无以至千里；不积小流，无以成江海。", "荀子"),
    ("工欲善其事，必先利其器。", "论语"),
    ("知之者不如好之者，好之者不如乐之者。", "孔子"),
    ("学而不思则罔，思而不学则殆。", "孔子"),
    ("三人行，必有我师焉。", "孔子"),
    ("玉不琢，不成器；人不学，不知道。", "礼记"),
    ("盛年不重来，一日难再晨。及时当勉励，岁月不待人。", "陶渊明"),
    ("读书破万卷，下笔如有神。", "杜甫"),
    ("黑发不知勤学早，白首方悔读书迟。", "颜真卿"),
    ("问渠那得清如许，为有源头活水来。", "朱熹"),
    ("旧书不厌百回读，熟读精思子自知。", "苏轼"),
    ("博观而约取，厚积而薄发。", "苏轼"),
    ("非学无以广才，非志无以成学。", "诸葛亮"),
    ("立身百行，以学为基。", "唐·林逋"),
    ("学如逆水行舟，不进则退。", "增广贤文"),
    ("宝剑锋从磨砺出，梅花香自苦寒来。", "古训"),
    ("世上无难事，只怕有心人。", "古训"),
    ("莫等闲，白了少年头，空悲切。", "岳飞"),
    ("天行健，君子以自强不息。", "周易"),
    ("地势坤，君子以厚德载物。", "周易"),
    ("投我以木瓜，报之以琼琚。", "诗经"),
    ("随风潜入夜，润物细无声。", "杜甫"),
    ("海纳百川，有容乃大；壁立千仞，无欲则刚。", "郑板桥"),
    ("沉舟侧畔千帆过，病树前头万木春。", "刘禹锡"),
    ("问君能有几多愁？恰似一江春水向东流。", "李煜"),
    ("落红不是无情物，化作春泥更护花。", "龚自珍"),
]


@router.get("/api/quote/today")
async def get_quote_today():
    """
    今日一句 — 基于日期确定性返回
    设计原则：同一天同一句（同 URL 多次访问结果一致），全年 30 句轮回。
    返回：{ success, data: { text, author, date, index } }
    """
    today = datetime.utcnow().date()
    day_of_year = today.timetuple().tm_yday
    idx = day_of_year % len(_DAILY_QUOTES)
    text, author = _DAILY_QUOTES[idx]
    return {
        "success": True,
        "data": {
            "text": text,
            "author": author,
            "date": today.isoformat(),
            "index": idx,
        },
    }
