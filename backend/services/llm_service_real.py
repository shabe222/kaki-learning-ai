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

# 这个文件才是 /darya 对话真正使用的工厂，因此它必须自己记录运行时状态；
# 否则 /api/health 描述的是另一个工厂，运维看到的是错的。
_RUNTIME_STATUS: dict = {"provider": None, "mode": "unknown", "initialized": False, "last_error": None}


def get_runtime_llm_status() -> dict:
    return dict(_RUNTIME_STATUS)


def _record(provider: str, error: str | None = None) -> None:
    _RUNTIME_STATUS.update({
        "provider": provider,
        "mode": "failed" if error else "production",
        "initialized": error is None,
        "last_error": error,
    })


def extract_llm_text(result):
    """Return provider text from either the legacy string or shared chat shape."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and isinstance(result.get("content"), str):
        return result["content"]
    raise TypeError("LLM response does not contain text content")


async def get_llm_service():
    from config import settings

    provider = (settings.LLM_PROVIDER or "").strip().lower()

    try:
        if provider == "openai_compatible":
            if not settings.OPENAI_COMPATIBLE_API_KEY or not settings.OPENAI_COMPATIBLE_BASE_URL:
                raise RuntimeError("LLM_PROVIDER=openai_compatible 需要设置 OPENAI_COMPATIBLE_API_KEY 与 OPENAI_COMPATIBLE_BASE_URL")
            base = settings.OPENAI_COMPATIBLE_BASE_URL.rstrip("/")
            endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
            config = LLMConfig(
                api_key=settings.OPENAI_COMPATIBLE_API_KEY,
                model=settings.OPENAI_COMPATIBLE_MODEL,
                base_url=endpoint,
                proxy=settings.OPENAI_COMPATIBLE_PROXY,
            )
            logger.info(
                "[LLM] 初始化 OpenAI 兼容服务 model=%s endpoint_domain=%s credential_configured=%s",
                settings.OPENAI_COMPATIBLE_MODEL, urlparse(endpoint).netloc or "redacted", bool(settings.OPENAI_COMPATIBLE_API_KEY),
            )
            service = DeepSeekLLMService(config)
            _record(provider)
            return service

        if provider == "deepseek":
            if not settings.DEEPSEEK_API_KEY:
                raise RuntimeError("LLM_PROVIDER=deepseek 需要设置 DEEPSEEK_API_KEY")
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
            service = DeepSeekLLMService(config)
            _record(provider)
            return service

        if provider == "spark":
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
            service = XingHuoLLMService(config)
            _record(provider)
            return service

        if provider == "qwen":
            if not settings.QWEN_API_KEY:
                raise RuntimeError("LLM_PROVIDER=qwen 需要设置 QWEN_API_KEY")
            config = LLMConfig(
                api_key=settings.QWEN_API_KEY,
                model=getattr(settings, "QWEN_MODEL", "") or "qwen-plus",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            )
            logger.info(
                "[LLM] 初始化通义千问服务 model=%s credential_configured=%s",
                config.model, bool(settings.QWEN_API_KEY),
            )
            service = DeepSeekLLMService(config)
            _record(provider)
            return service

        # 以前这里会静默落到讯飞：写错 provider 名会拿到一个凭证为空的客户端。
        # 现在直接报错，让配置问题在启动/首次调用时可见。
        raise RuntimeError(
            f"不支持的 LLM_PROVIDER='{settings.LLM_PROVIDER}'；"
            "可选值：deepseek、openai_compatible、spark、qwen"
        )
    except Exception as exc:
        _record(provider or "unknown", f"{type(exc).__name__}: {exc}")
        raise


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
    return DeepSeekLLMService(LLMConfig(
        api_key=api_key, model=runtime_config.model, base_url=endpoint,
        proxy=settings.OPENAI_COMPATIBLE_PROXY,
    ))


__all__ = [
    "BaseLLMClient",
    "XingHuoLLMService",
    "DeepSeekLLMService",
    "LLMConfig",
    "get_llm_service",
    "get_runtime_llm_service",
    "get_runtime_llm_status",
    "extract_llm_text",
    "_make_cache_payload",
]
