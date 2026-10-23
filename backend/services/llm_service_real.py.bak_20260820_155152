"""
LLM 服务入口

实际实现已抽离到 services/llm_base.py：
- BaseLLMClient: 统一 chat/chat_stream/structured_output/cache
- XingHuoLLMService / DeepSeekLLMService: 仅实现 _get_auth_token
- LLMConfig / _make_cache_payload: 数据类与工具

本文件保留：
- 兼容旧路径的类 re-export
- get_llm_service() 工厂函数
"""
import logging
from urllib.parse import urlparse

from services.llm_base import (
    BaseLLMClient,
    XingHuoLLMService,
    DeepSeekLLMService,
    LLMConfig,
    _make_cache_payload,
)

logger = logging.getLogger(__name__)


def extract_llm_text(result):
    """Return provider text from either the legacy string or shared chat shape."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and isinstance(result.get("content"), str):
        return result["content"]
    raise TypeError("LLM response does not contain text content")


async def get_llm_service():
    from config import settings

    provider = settings.LLM_PROVIDER.lower()

    if provider == "deepseek":
        config = LLMConfig(
            api_key=settings.DEEPSEEK_API_KEY,
            model=settings.DEEPSEEK_MODEL,
            base_url=f"{settings.DEEPSEEK_BASE_URL}/chat/completions"
        )
        endpoint_domain = urlparse(config.base_url).netloc or "redacted"
        logger.info(
            "[LLM] 初始化 DeepSeek 服务 model=%s endpoint_domain=%s credential_configured=%s",
            settings.DEEPSEEK_MODEL, endpoint_domain, bool(settings.DEEPSEEK_API_KEY),
        )
        return DeepSeekLLMService(config)

    config = LLMConfig(
        api_password=settings.XUNFEI_API_PASSWORD,
        api_key=settings.XUNFEI_API_KEY,
        app_id=settings.XUNFEI_APP_ID,
        api_secret=settings.XUNFEI_API_SECRET,
        model=settings.LLM_MODEL
    )

    logger.info(
        "[LLM] 初始化讯飞星火服务 model=%s credential_configured=%s",
        settings.LLM_MODEL, bool(config.api_password),
    )
    return XingHuoLLMService(config)


async def get_runtime_llm_service(runtime_config):
    """构造当前请求专用的 OpenAI 兼容客户端；配置不缓存、不落库。"""
    from config import settings
    if runtime_config is None:
        return await get_llm_service()
    provider = runtime_config.provider
    api_key = (runtime_config.api_key or "").strip()
    if provider == "deepseek":
        api_key = api_key or settings.DEEPSEEK_API_KEY
        base = runtime_config.base_url or settings.DEEPSEEK_BASE_URL
    else:
        if not api_key or not runtime_config.base_url:
            raise ValueError("自定义兼容接口需要 API Key 和 Base URL")
        base = runtime_config.base_url
    endpoint = base if base.rstrip("/").endswith("/chat/completions") else f"{base.rstrip('/')}/chat/completions"
    return DeepSeekLLMService(LLMConfig(api_key=api_key, model=runtime_config.model, base_url=endpoint))


__all__ = [
    "BaseLLMClient",
    "XingHuoLLMService",
    "DeepSeekLLMService",
    "LLMConfig",
    "get_llm_service",
    "get_runtime_llm_service",
    "extract_llm_text",
    "_make_cache_payload",
]
