"""视觉绘本提供方凭证的**服务端唯一读取口**。

契约：GLM Key 只存在于服务端（数据库或环境变量），
任何面向浏览器的响应、DOM 属性或 localStorage 都不得携带明文 Key。
浏览器侧一律经 `routers/darya_storybook_proxy.py` 的代理调用。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings

logger = logging.getLogger(__name__)

GLM_KEY_NAME = "glm_api_key"

# 允许经代理调用的模型白名单。共享付费 Key 不能变成通用 LLM 网关。
ALLOWED_CHAT_MODELS = {"glm-4-flash", "glm-4-air", "glm-4-airx", "glm-4-plus"}
ALLOWED_IMAGE_MODELS = {"glm-image", "cogview-4", "cogview-4-250304", "cogview-3-flash", "cogview-3"}

DEFAULT_CHAT_MODEL = "glm-4-flash"
DEFAULT_IMAGE_MODEL = "glm-image"

GLM_CHAT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
GLM_IMAGE_URL = "https://open.bigmodel.cn/api/paas/v4/images/generations"


def mask_secret(value: str) -> str:
    if not value:
        return ""
    return f"{value[:4]}****{value[-4:]}" if len(value) > 12 else "****"


async def read_glm_key(db: AsyncSession) -> str:
    """管理端配置优先，环境变量兜底。仅服务端调用。"""
    from database.models import StorybookProviderConfig

    row = (await db.execute(
        select(StorybookProviderConfig).where(StorybookProviderConfig.config_key == GLM_KEY_NAME)
    )).scalar_one_or_none()
    if row and row.config_value:
        return str(row.config_value).strip()
    return str(getattr(settings, "GLM_API_KEY", "") or "").strip()


async def write_glm_key(db: AsyncSession, value: str) -> None:
    from database.models import StorybookProviderConfig

    row = (await db.execute(
        select(StorybookProviderConfig).where(StorybookProviderConfig.config_key == GLM_KEY_NAME)
    )).scalar_one_or_none()
    if row:
        row.config_value = value
    else:
        db.add(StorybookProviderConfig(config_key=GLM_KEY_NAME, config_value=value))
    await db.commit()


async def glm_key_or_none(db: AsyncSession) -> str | None:
    """返回可用的 Key，或以 None 表示「未配置」。绝不把 Key 交给调用方之外的边界。"""
    key = await read_glm_key(db)
    return key or None


def resolve_chat_model(requested: str | None) -> str:
    model = str(requested or "").strip()
    return model if model in ALLOWED_CHAT_MODELS else DEFAULT_CHAT_MODEL


def resolve_image_model(requested: str | None) -> str:
    model = str(requested or "").strip()
    return model if model in ALLOWED_IMAGE_MODELS else DEFAULT_IMAGE_MODEL
