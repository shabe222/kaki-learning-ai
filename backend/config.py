from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """应用配置 - 从环境变量读取"""

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", case_sensitive=True)

    # 应用基础配置
    APP_NAME: str = "ML Learning Agent System"
    DEBUG: bool = True
    VERSION: str = "1.0.0"

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # 运行模式
    MODE: str = "mock"  # mock 或 production

    # 讯飞星火配置【备用】
    XUNFEI_API_KEY: str = ""
    XUNFEI_APP_ID: str = ""
    XUNFEI_API_SECRET: str = ""
    XUNFEI_API_PASSWORD: str = ""

    # 模型选择
    LLM_PROVIDER: str = "mock"  # mock / deepseek / spark / qwen（Day 4 画像接口契约对齐视频接口）
    LLM_MODEL: str = "spark-x"
    LLM_TIMEOUT: int = 30       # 兼容旧字段（被 LLM_TIMEOUT_SEC 替代）
    LLM_TIMEOUT_SEC: int = 30   # LLM 单次调用超时秒数（Day 5 强化：real provider 失败降级判定）

    # DeepSeek 配置 (支持火山方舟等第三方平台)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-v4.1-flash"
    DEEPSEEK_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/v3"

    # OpenAI 兼容第三方平台（可选第二供应商，如 b.ai；默认不启用，LLM_PROVIDER=openai_compatible 时生效）
    OPENAI_COMPATIBLE_BASE_URL: str = ""
    OPENAI_COMPATIBLE_API_KEY: str = ""
    OPENAI_COMPATIBLE_MODEL: str = "deepseek-v4.1-flash"
    OPENAI_COMPATIBLE_PROXY: str = ""  # 可选：该平台需代理时填写，如 http://127.0.0.1:7897
    # 检索出口。以前只有模型调用配了代理，检索没有，于是服务进程里的检索走直连——
    # 而 DuckDuckGo 直连在这条线路上不通（实测 EMPTY / 10s 超时），走代理 2s 就返回。
    # 取值：http://127.0.0.1:7897 走该代理；direct 强制直连；留空则沿用环境变量。
    SEARCH_PROXY: str = ""

    # 视觉绘本 GLM（智谱）Key：管理端可在线配置（DB 优先），此处为部署期兜底
    GLM_API_KEY: str = ""

    # Internal Cloud Video adapter. Keys pasted into chat are exposed and must
    # not be used until the owner explicitly confirms rotation locally.
    DASHSCOPE_API_KEY: str = ""
    CLOUD_VIDEO_KEYS_ROTATED: bool = False
    CLOUD_VIDEO_PROVIDER: str = "s2v"
    CLOUD_VIDEO_S2V_MODEL: str = "wan2.2-s2v"
    CLOUD_VIDEO_S2V_DETECT_MODEL: str = "wan2.2-s2v-detect"
    CLOUD_VIDEO_EMO_MODEL: str = "emo-v1"
    CLOUD_VIDEO_EMO_DETECT_MODEL: str = "emo-detect-v1"
    CLOUD_VIDEO_OUTPUT_DIR: str = ""

    # 阿里云通义千问配置
    QWEN_API_KEY: str = ""
    QWEN_MODEL: str = "qwen-plus"

    # 连续语音对话的本地 ASR（faster-whisper）。tiny 会把 Python / nonlocal 这类
    # 中英混说的术语听成谐音字，small + 领域提示词可以正确转写。
    WORKSPACE_WHISPER_MODEL: str = "small"
    WORKSPACE_WHISPER_DEVICE: str = "cpu"
    WORKSPACE_WHISPER_COMPUTE_TYPE: str = "int8"

    # 数据库配置 —— 绝对路径，根除 cwd 漂移导致的双 db 问题
    # （uvicorn 在 rjianb/、_seed_profile.py 在 backend/，相对路径会写到两个文件）
    _BACKEND_DIR = Path(__file__).resolve().parent
    DATABASE_URL: str = f"sqlite+aiosqlite:///{(_BACKEND_DIR / 'ml_learning.db').as_posix()}"
    DB_ENGINE: str = "sqlite"  # postgresql 或 sqlite
    PG_HOST: str = "localhost"
    PG_PORT: int = 5432
    PG_USER: str = "ml_learning"
    PG_PASSWORD: str = ""
    PG_DATABASE: str = "ml_learning"

    # Redis 配置
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = False

    # Milvus 向量数据库配置
    MILVUS_URI: str = "http://localhost:19530"
    MILVUS_ENABLED: bool = True

    # MinIO OSS 配置
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_SECURE: bool = False
    MINIO_ENABLED: bool = True

    # Seedance 视频生成配置（豆包/火山引擎）
    SEEDANCE_API_KEY: str = ""
    SEEDANCE_BASE_URL: str = "https://ark.cn-beijing.volces.com"
    SEEDANCE_MODEL: str = "doubao-seedance-1-5-pro-251215"
    SEEDANCE_ENABLED: bool = False

    # OpenMAIC 视频生成配置（AI交互式教学动画）
    OPENMAIC_BASE_URL: str = "http://localhost:3001"
    OPENMAIC_ENABLED: bool = True

    # JWT认证配置
    # ⚠️  生产环境必须通过环境变量 SECRET_KEY 设置强随机值（≥32 字节）
    # 启动时若处于 production 模式且 SECRET_KEY 仍为占位值，会在 app.py 启动校验中 fail-fast
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    # 高成本端点限流（对话 / 生成 / 上传）。公网部署必须保持开启，
    # 否则任何人都能无上限消耗 LLM 额度。
    RATE_LIMIT_ENABLED: bool = True

    # 由后端直接托管 frontend/dist（生产单进程部署；开发用 start.ps1 走 Vite）
    SERVE_FRONTEND: bool = True

    # CORS配置（开发环境允许所有来源；生产请收敛到具体域名）
    CORS_ORIGINS: list = ["*"]

    # 初始管理员配置（首次启动时创建，后续修改环境变量不会更新已有管理员）
    INITIAL_ADMIN_USERNAME: str = "admin"
    INITIAL_ADMIN_PASSWORD: str = ""
    INITIAL_ADMIN_DISPLAY_NAME: str = "超级管理员"

def _dev_secret_key() -> str:
    """
    提供一个开发用占位 SECRET_KEY。
    生产模式（settings.MODE == 'production'）必须在 app.py 启动校验里 fail-fast。
    优先读环境变量 SECRET_KEY；否则生成一个稳定占位。
    """
    return os.environ.get("SECRET_KEY") or "dev-only-do-not-use-in-production-please-set-env-var"


def _resolve_settings() -> Settings:
    """实例化 Settings 并补齐 SECRET_KEY 默认值。"""
    s = Settings()
    if not s.SECRET_KEY:
        s = s.model_copy(update={"SECRET_KEY": _dev_secret_key()})
    return s


settings = _resolve_settings()


def get_settings() -> Settings:
    return settings


def validate_production_secrets() -> None:
    """
    生产模式启动时 fail-fast 校验：占位 SECRET_KEY 一律拒绝启动。
    在 app.py 的 lifespan 中调用。
    """
    placeholder_secrets = {
        "your-secret-key-change-in-production",
        "dev-only-do-not-use-in-production-please-set-env-var",
    }
    if settings.MODE == "production" and settings.SECRET_KEY in placeholder_secrets:
        raise RuntimeError(
            "🚨 FATAL: 生产模式必须设置环境变量 SECRET_KEY（≥32 字节强随机值）\n"
            "   示例：export SECRET_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
        )
    if settings.MODE == "production" and settings.LLM_PROVIDER == "mock":
        raise RuntimeError(
            "🚨 FATAL: 生产模式不允许 LLM_PROVIDER=mock，请设置为 deepseek、spark 或 qwen"
        )
    if settings.MODE == "production":
        provider_key_fields = {
            "deepseek": "DEEPSEEK_API_KEY",
            "qwen": "QWEN_API_KEY",
            "spark": "XUNFEI_API_PASSWORD",
            "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
        }
        key_field = provider_key_fields.get(settings.LLM_PROVIDER)
        if key_field is None:
            raise RuntimeError(
                f"🚨 FATAL: 不支持的 LLM_PROVIDER={settings.LLM_PROVIDER}，请设置为 deepseek、spark、qwen 或 openai_compatible"
            )
        if not getattr(settings, key_field, ""):
            raise RuntimeError(
                f"🚨 FATAL: 生产模式使用 LLM_PROVIDER={settings.LLM_PROVIDER} 时必须设置环境变量 {key_field}"
            )
    if settings.MODE == "production" and settings.DB_ENGINE == "postgresql" and not settings.PG_PASSWORD:
        raise RuntimeError(
            "🚨 FATAL: 生产模式使用 PostgreSQL 时必须设置环境变量 PG_PASSWORD"
        )
