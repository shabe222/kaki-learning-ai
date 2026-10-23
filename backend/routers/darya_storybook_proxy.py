"""DARYA 视觉绘本 · 服务端代理。

存在理由：绘本组件在浏览器里运行，但它的模型调用必须使用产品自己的付费 GLM Key。
把 Key 下发到浏览器等于把凭证交给任意登录用户，因此浏览器只访问本代理，
Key 只在服务端注入。

契约：
- 所有端点都要求已登录用户。
- 模型走白名单，共享付费 Key 不作为通用 LLM 网关。
- 请求体有尺寸上限；上游错误按原状态码/结构回传，便于前端诚实报错。
"""
from __future__ import annotations

import ipaddress
import logging
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from services.storybook_provider import (
    GLM_CHAT_URL,
    GLM_IMAGE_URL,
    glm_key_or_none,
    resolve_chat_model,
    resolve_image_model,
)
from utils.auth import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter()

# ---- 成本护栏（进程内；多副本部署需换成共享存储，见 PRODUCT_PROGRESS 债务） ----
_MAX_CHAT_MESSAGES = 60
_MAX_CHAT_CHARS = 60_000
_MAX_CHAT_TOKENS = 8_000
_MAX_IMAGE_PROMPT_CHARS = 4_000
_MAX_ASSET_BYTES = 8 * 1024 * 1024
_IMAGE_INFLIGHT_PER_USER = 3

_WINDOW_SECONDS = 3600
_CHAT_PER_HOUR = 240
_IMAGE_PER_HOUR = 40

_chat_calls: dict[int, deque[float]] = defaultdict(deque)
_image_calls: dict[int, deque[float]] = defaultdict(deque)
_image_inflight: dict[int, int] = defaultdict(int)


def _rate_limit(bucket: dict[int, deque[float]], user_id: int, limit: int, label: str) -> None:
    now = time.time()
    calls = bucket[user_id]
    while calls and now - calls[0] > _WINDOW_SECONDS:
        calls.popleft()
    if len(calls) >= limit:
        raise HTTPException(status_code=429, detail=f"{label}调用过于频繁，请稍后再试")
    calls.append(now)


def _check_asset_host(url: str) -> None:
    """图片代理只允许公网 https 图片，避免变成 SSRF 跳板。"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="只允许 https 图片地址")
    host = (parsed.hostname or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="图片地址无效")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise HTTPException(status_code=400, detail="图片地址无效")


async def _require_glm_key(db: AsyncSession, user_id: int) -> str:
    key = await glm_key_or_none(db)
    if not key:
        raise HTTPException(status_code=503, detail="视觉绘本的模型通道尚未配置，请联系管理员")
    return key


@router.get("/api/darya/storybook/config")
async def api_darya_storybook_config(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """告诉前端「能不能用 + 该往哪调用」。

    这里**只**返回可用性与相对路径，绝不返回 Key。
    """
    configured = bool(await glm_key_or_none(db))
    return {
        "configured": configured,
        "chatPath": "/api/darya/storybook/llm/chat",
        "imagePath": "/api/darya/storybook/llm/image",
        "assetPath": "/api/darya/storybook/asset",
    }


@router.post("/api/darya/storybook/llm/chat")
async def api_darya_storybook_chat(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    key = await _require_glm_key(db, current_user_id)
    _rate_limit(_chat_calls, current_user_id, _CHAT_PER_HOUR, "绘本教研模型")

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")
    if len(messages) > _MAX_CHAT_MESSAGES:
        raise HTTPException(status_code=400, detail="对话轮次过多")

    cleaned: list[dict[str, str]] = []
    total_chars = 0
    for item in messages:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="messages 结构非法")
        role = str(item.get("role") or "").strip()
        content = item.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            raise HTTPException(status_code=400, detail="messages 结构非法")
        total_chars += len(content)
        cleaned.append({"role": role, "content": content})
    if total_chars > _MAX_CHAT_CHARS:
        raise HTTPException(status_code=413, detail="请求内容过长")

    max_tokens = body.get("max_tokens")
    try:
        max_tokens = int(max_tokens) if max_tokens is not None else 3000
    except (TypeError, ValueError):
        max_tokens = 3000
    max_tokens = max(1, min(max_tokens, _MAX_CHAT_TOKENS))

    temperature = body.get("temperature")
    try:
        temperature = float(temperature) if temperature is not None else 0.7
    except (TypeError, ValueError):
        temperature = 0.7

    payload = {
        "model": resolve_chat_model(body.get("model")),
        "messages": cleaned,
        "temperature": max(0.0, min(temperature, 1.0)),
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            upstream = await client.post(GLM_CHAT_URL, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("[Storybook] 教研模型上游不可达: %s", exc)
        raise HTTPException(status_code=502, detail="教研模型暂时不可达，请稍后重试")

    if upstream.status_code != 200:
        logger.warning("[Storybook] 教研模型返回 %s: %s", upstream.status_code, upstream.text[:200])
        # 原样回传上游状态，让前端按真实原因报错（限流 / 余额 / 鉴权）
        raise HTTPException(status_code=upstream.status_code, detail=_upstream_message(upstream))

    return upstream.json()


@router.post("/api/darya/storybook/llm/image")
async def api_darya_storybook_image(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    key = await _require_glm_key(db, current_user_id)
    _rate_limit(_image_calls, current_user_id, _IMAGE_PER_HOUR, "绘本插画")

    if _image_inflight[current_user_id] >= _IMAGE_INFLIGHT_PER_USER:
        raise HTTPException(status_code=429, detail="插画生成过于频繁，请稍后再试")

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")

    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt 不能为空")
    if len(prompt) > _MAX_IMAGE_PROMPT_CHARS:
        raise HTTPException(status_code=413, detail="prompt 过长")

    size = str(body.get("size") or "1152x864").strip()
    if not _valid_size(size):
        size = "1152x864"

    payload = {"model": resolve_image_model(body.get("model")), "prompt": prompt, "size": size}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    _image_inflight[current_user_id] += 1
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            upstream = await client.post(GLM_IMAGE_URL, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("[Storybook] 插画上游不可达: %s", exc)
        raise HTTPException(status_code=502, detail="插画服务暂时不可达，请稍后重试")
    finally:
        _image_inflight[current_user_id] = max(0, _image_inflight[current_user_id] - 1)

    if upstream.status_code != 200:
        logger.warning("[Storybook] 插画返回 %s: %s", upstream.status_code, upstream.text[:200])
        raise HTTPException(status_code=upstream.status_code, detail=_upstream_message(upstream))

    return upstream.json()


@router.get("/api/darya/storybook/asset")
async def api_darya_storybook_asset(
    url: str = Query(..., description="远端图片地址（https）"),
    current_user_id: int = Depends(get_current_user_id),
):
    """图片代理：绘本的像素级画风质检需要在同源下读取远端插图。"""
    _check_asset_host(url)
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as upstream:
                if upstream.status_code != 200:
                    raise HTTPException(status_code=upstream.status_code, detail="图片获取失败")
                content_type = upstream.headers.get("content-type", "")
                if not content_type.startswith("image/"):
                    raise HTTPException(status_code=415, detail="目标不是图片")
                chunks: list[bytes] = []
                size = 0
                async for chunk in upstream.aiter_bytes():
                    size += len(chunk)
                    if size > _MAX_ASSET_BYTES:
                        raise HTTPException(status_code=413, detail="图片过大")
                    chunks.append(chunk)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        logger.warning("[Storybook] 图片代理失败: %s", exc)
        raise HTTPException(status_code=502, detail="图片获取失败")

    return Response(
        content=b"".join(chunks),
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


def _upstream_message(upstream: httpx.Response) -> str:
    try:
        data = upstream.json()
    except Exception:
        return "视觉绘本模型调用失败"
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return "视觉绘本模型调用失败"


def _valid_size(size: str) -> bool:
    parts = size.lower().split("x")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return False
    width, height = int(parts[0]), int(parts[1])
    return 256 <= width <= 2048 and 256 <= height <= 2048
