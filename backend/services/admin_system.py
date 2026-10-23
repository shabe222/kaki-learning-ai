"""管理端系统状态服务"""
import sys
import logging
from datetime import datetime, timedelta
from typing import Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from config import settings, BASE_DIR
from database.models import (
    User, ChatSession, AgentInvocation, GeneratedItem,
    WorkspaceMaterial, StudentProfile, LearningPlan
)

logger = logging.getLogger(__name__)


async def get_system_info(db: AsyncSession) -> dict:
    day_ago = datetime.utcnow() - timedelta(hours=24)

    week_ago = datetime.utcnow() - timedelta(days=7)
    success_r = await db.execute(
        select(func.count(AgentInvocation.id)).where(
            and_(AgentInvocation.created_at >= week_ago, AgentInvocation.status == "success")
        )
    )
    week_success = success_r.scalar() or 0
    failed_r = await db.execute(
        select(func.count(AgentInvocation.id)).where(
            and_(AgentInvocation.created_at >= week_ago, AgentInvocation.status == "failed")
        )
    )
    week_failed = failed_r.scalar() or 0
    finished = week_success + week_failed
    success_rate = round(week_success / finished * 100, 1) if finished > 0 else None

    # Admin only needs a coarse technical aggregate. Do not materialize raw error text.
    recent_failed = (await db.execute(
        select(func.count(AgentInvocation.id)).where(
            and_(AgentInvocation.created_at >= day_ago, AgentInvocation.status.in_(("failed", "error")))
        )
    )).scalar() or 0
    error_cats = [{"category": "task_failed", "count": recent_failed}] if recent_failed else []

    tables = {
        "users": User, "student_profiles": StudentProfile,
        "chat_sessions": ChatSession, "agent_invocations": AgentInvocation,
        "generated_items": GeneratedItem, "workspace_materials": WorkspaceMaterial,
        "learning_plans": LearningPlan,
    }
    row_counts = {}
    for name, model in tables.items():
        r = await db.execute(select(func.count()).select_from(model))
        row_counts[name] = r.scalar() or 0

    agent_names = (await db.execute(
        select(AgentInvocation.agent_name).distinct()
    )).scalars().all()

    running = (await db.execute(select(func.count(AgentInvocation.id)).where(AgentInvocation.status == "running"))).scalar() or 0
    known_agents = {"profile", "document", "question", "mindmap", "code", "path", "tutor", "video"}
    observed_agents = {name for name in agent_names if name}

    def optional_status(enabled: bool) -> str:
        # 未建立实时探针时不把“启用配置”冒充在线。
        return "degraded" if enabled else "not_configured"

    return {
        "backend": "online",
        "database": "online",
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": getattr(settings, "DEEPSEEK_MODEL", settings.LLM_MODEL) if settings.LLM_PROVIDER == "deepseek" else settings.LLM_MODEL,
        "mode": settings.MODE,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "api_routes": None,  # The HTTP route supplies the actual registered count.
        "agent_count": len(observed_agents),
        "registered_agent_count": len(known_agents),
        "agent_status": "not_probed",
        "task_queue": {"status": "not_probed", "running": running, "backend": "database", "basis": "Persisted invocation counts; executor availability has not been checked."},
        "file_storage": {"status": "not_probed", "backend": "local", "path": "backend/storage/uploads"},
        "redis": {"status": optional_status(bool(settings.REDIS_ENABLED)), "enabled": bool(settings.REDIS_ENABLED)},
        "milvus": {"status": optional_status(bool(settings.MILVUS_ENABLED)), "enabled": bool(settings.MILVUS_ENABLED)},
        "minio": {"status": optional_status(bool(settings.MINIO_ENABLED)), "enabled": bool(settings.MINIO_ENABLED)},
        "last_health_check_at": datetime.utcnow().isoformat(),
        "health_check_basis": "This response and real database queries; not an agent, queue, storage, or provider capability probe.",
        "recent_success_rate": success_rate,
        "recent_error_categories": error_cats,
        "table_row_counts": row_counts,
        "version": settings.VERSION,
        "configurable": get_current_config(),
    }


# ===== 配置管理 =====

def _masked(value: str) -> str:
    if not value: return "未配置"
    return f"{value[:3]}••••{value[-3:]}" if len(value) > 8 else "••••••"


def get_current_config() -> Dict[str, dict]:
    """只返回安全白名单运行值；密钥不回传，且本接口不写文件。"""
    provider = settings.LLM_PROVIDER
    key_name = {"deepseek": "DEEPSEEK_API_KEY", "spark": "XUNFEI_API_PASSWORD", "qwen": "QWEN_API_KEY"}.get(provider)
    key_value = getattr(settings, key_name, "") if key_name else ""
    model = getattr(settings, "DEEPSEEK_MODEL", settings.LLM_MODEL) if provider == "deepseek" else settings.LLM_MODEL
    return {
        "LLM_PROVIDER": {"runtime": provider, "effect": "当前进程"},
        "LLM_MODEL": {"runtime": model, "effect": "当前进程"},
        "MODE": {"runtime": settings.MODE, "effect": "当前进程"},
        "API_KEY": {"runtime": _masked(key_value), "effect": "服务端安全配置", "secret": True},
        "editable": False,
        "notice": "当前未提供安全的在线配置写入接口；请在服务端受控环境配置并重启。",
    }


def update_system_config(updates: Dict[str, str]) -> Dict[str, object]:
    """兼容旧调用但明确拒绝在线写 .env。"""
    return {"success": False, "message": "在线配置写入已禁用；请在服务端安全配置并重启。"}


# ===== 缓存管理 =====

# 模块级缓存注册表，各服务模块可注册清空回调
_cache_clear_callbacks: Dict[str, callable] = {}


def register_cache_clear_callback(name: str, callback: callable) -> None:
    """注册缓存清空回调"""
    _cache_clear_callbacks[name] = callback


def clear_all_caches() -> Dict[str, any]:
    """清空所有已注册缓存"""
    results = {}
    for name, cb in _cache_clear_callbacks.items():
        try:
            cb()
            results[name] = "cleared"
        except Exception:
            results[name] = "failed"
            logger.error("清空缓存 [%s] 失败", name)

    # 清空 Python import 缓存中的非关键模块（可选）
    cleared_count = sum(1 for v in results.values() if v == "cleared")
    return {
        "success": all(v == "cleared" for v in results.values()),
        "message": f"已清空 {cleared_count}/{len(results)} 个缓存",
        "details": results,
    }
