"""材料插图技能：为学习资料生成一张与主题匹配、不承载正文文字的教学示意图。

- 图片通道: GLM CogView（管理端配置的 glm_api_key，存于 StorybookProviderConfig）优先，
  DashScope qwen-image（settings key）兜底
- 风格: 默认跟随产品主题（柿子暖色 / Ocean 海蓝）；用户单独指定风格时以用户为准
- 约束: 图像模型写不好中文标注，插图不承载正文文字；文字解释由资料正文负责
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import httpx
from sqlalchemy import select

from config import settings

logger = logging.getLogger(__name__)

DASHSCOPE_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
DASHSCOPE_MODEL = "qwen-image-2.0-pro-2026-04-22"
GLM_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/images/generations"
GLM_MODEL = "cogview-4"

BACKEND_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BACKEND_DIR / "static" / "material_images"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_THEME_STYLES = {
    "kaki": "温暖手绘科普插画风格，米色纸面质感，柿子橙与浅海蓝点缀，线条柔和，画面安静克制",
    "ocean": "清冷海洋科普插画风格，浅色背景，蓝绿主色，线条干净，画面安静克制",
}


async def _glm_key() -> str:
    """管理端配置的 GLM key 存在数据库里；用完即关会话。"""
    try:
        from database.connection import get_db_session
        from database.models import StorybookProviderConfig

        async with get_db_session() as db:
            row = (await db.execute(
                select(StorybookProviderConfig).where(
                    StorybookProviderConfig.config_key == "glm_api_key"
                )
            )).scalars().first()
            return str(row.config_value or "").strip() if row else ""
    except Exception:
        logger.warning("[Illustration] 读取 GLM key 失败", exc_info=True)
        return ""


def _dashscope_key() -> str:
    return str(
        getattr(settings, "QWEN_API_KEY", "")
        or getattr(settings, "DASHSCOPE_API_KEY", "")
        or ""
    ).strip()


async def generate_material_illustration(
    concept: str,
    *,
    theme: str = "kaki",
    style_hint: str = "",
) -> dict:
    """按画面描述生成一张教学插图，返回 {ok, url, prompt} 或 {ok: False, reason}。"""
    concept = str(concept or "").strip()
    if len(concept) < 8:
        return {"ok": False, "reason": "concept too short"}

    style = (style_hint or "").strip() or _THEME_STYLES.get(str(theme or "").lower(), _THEME_STYLES["kaki"])
    from services.skill_library import skill_prompt_prefix
    # 「不要出现任何文字」是对模型的要求，不是已经兑现的结果。2026-09-21 实测：
    # 智谱这条通道返回的图片 URL 自己就叫 `*_watermark.png`，cogview-4 / glm-image /
    # cogview-3-flash 都一样，`watermark_enabled: false` 会被接受但不被采纳（文档写着
    # 只对已签署免责声明的客户开放）。所以这条通道出的图角上带着「AI生成」，
    # 那是提示词管不到的一类文字。要真正拿到无文字的图，得让账号签掉那份声明，
    # 或者换到能关水印的通道；不要以为提示词已经解决它。
    prompt = (
        skill_prompt_prefix("resource-art-direction", "illustration-prompting", max_chars_per_skill=1200)
        + f"为中学学习资料画一张教学示意图：{concept[:160]}。"
        f"{style}。画面中不要出现任何文字、字母、数字或标注，"
        "用图形、位置关系与颜色表达，构图简洁，突出一个核心过程。"
    )

    glm = await _glm_key()
    if glm:
        outcome = await _generate_with_glm(glm, prompt)
        if outcome.get("ok"):
            return outcome
        logger.info("[Illustration] GLM 通道未出图: %s，尝试 DashScope", outcome.get("reason"))

    dash = _dashscope_key()
    if dash:
        return await _generate_with_dashscope(dash, prompt)
    return {"ok": False, "reason": "no image key configured"}


async def _generate_with_glm(key: str, prompt: str, *, size: str = "1024x768") -> dict:
    payload = {
        "model": GLM_MODEL,
        "prompt": prompt,
        "size": size,
        # 字段名是 watermark_enabled（不是 watermark）：写错过一次，接口不报错、
        # 只是照旧烙水印。智谱文档标注 false「仅允许已签署免责声明的客户使用」，
        # 所以关不掉时不是我们写错——那是账号侧的限制。
        "watermark_enabled": False,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(GLM_ENDPOINT, json=payload, headers=headers)
        elapsed = time.time() - start
        if response.status_code != 200:
            logger.warning("[Illustration] GLM 生成失败 status=%s body=%s", response.status_code, response.text[:200])
            return {"ok": False, "reason": f"glm status {response.status_code}"}
        data = response.json()
        remote_url = str(((data.get("data") or [{}])[0]).get("url") or "").strip()
        if not remote_url:
            logger.warning("[Illustration] GLM 响应无图片 URL: %s", str(data)[:200])
            return {"ok": False, "reason": "glm no image url"}
        return await _download(remote_url, "glm", elapsed)
    except Exception as exc:
        logger.warning("[Illustration] GLM 生成异常: %s", exc)
        return {"ok": False, "reason": f"glm error: {exc}"}


async def _generate_with_dashscope(key: str, prompt: str) -> dict:
    payload = {
        "model": DASHSCOPE_MODEL,
        "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
        "parameters": {
            "size": "1024*768",
            "n": 1,
            "watermark": False,
            "prompt_extend": True,
            "negative_prompt": "low quality, blurry, watermark, garbled text, English text, Chinese characters, messy, distorted",
        },
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(DASHSCOPE_ENDPOINT, json=payload, headers=headers)
        elapsed = time.time() - start
        if response.status_code != 200:
            logger.warning("[Illustration] DashScope 生成失败 status=%s body=%s", response.status_code, response.text[:200])
            return {"ok": False, "reason": f"dashscope status {response.status_code}"}
        data = response.json()

        def _walk(node):
            if isinstance(node, dict):
                for key_name in ("url", "image_url"):
                    value = node.get(key_name)
                    if isinstance(value, str) and value.startswith(("http://", "https://")):
                        return value
                for value in node.values():
                    found = _walk(value)
                    if found:
                        return found
            elif isinstance(node, list):
                for value in node:
                    found = _walk(value)
                    if found:
                        return found
            return ""

        remote_url = str(_walk(data) or "")
        if not remote_url:
            return {"ok": False, "reason": "dashscope no image url"}
        return await _download(remote_url, "dashscope", elapsed)
    except Exception as exc:
        logger.warning("[Illustration] DashScope 生成异常: %s", exc)
        return {"ok": False, "reason": f"dashscope error: {exc}"}


async def _download(remote_url: str, channel: str, elapsed: float) -> dict:
    local_name = f"illus_{hashlib.sha1(f'{remote_url}{time.time()}'.encode()).hexdigest()[:12]}.png"
    local_path = OUTPUT_DIR / local_name
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            file_response = await client.get(remote_url)
        if file_response.status_code == 200 and len(file_response.content) > 1024:
            local_path.write_bytes(file_response.content)
            logger.info(
                "[Illustration] 插图已生成（%s） %s (%dKB, %.1fs)",
                channel, local_name, len(file_response.content) // 1024, elapsed,
            )
            return {"ok": True, "url": f"/static/material_images/{local_name}", "channel": channel}
        logger.warning("[Illustration] 图片下载失败 status=%s", file_response.status_code)
        return {"ok": False, "reason": "image download failed"}
    except Exception as exc:
        logger.warning("[Illustration] 图片下载异常: %s", exc)
        return {"ok": False, "reason": f"image download error: {exc}"}


_TEACHER_PROMPT = (
    "一位亲和力很强的年轻女教师半身肖像，正面微侧，穿浅蓝衬衫，深色长发，"
    "面向镜头正在亲切讲课，嘴唇自然闭合，表情温和自信，纯浅米色背景，"
    "柔和棚拍光线，干净无杂物。画面中没有任何文字、字母、数字、水印或标识。"
)
_TEACHER_CACHE_NAME = "teacher-portrait-v1.png"


async def generate_teacher_portrait() -> dict:
    """GLM 生成 DARYA 讲解老师形象（方形半身像），磁盘缓存复用。

    返回 {ok, path(本地绝对路径), url} 或 {ok: False, reason}。
    """
    cached = OUTPUT_DIR / "teacher" / _TEACHER_CACHE_NAME
    if cached.exists() and cached.stat().st_size > 2048:
        return {"ok": True, "path": str(cached), "url": f"/static/material_images/teacher/{_TEACHER_CACHE_NAME}"}
    glm = await _glm_key()
    if not glm:
        return {"ok": False, "reason": "no glm key"}
    outcome = await _generate_with_glm(glm, _TEACHER_PROMPT, size="1024x1024")
    if not outcome.get("ok"):
        return outcome
    remote_url = str(outcome.get("url") or "")
    if not remote_url.startswith("/static/"):
        return {"ok": False, "reason": "unexpected glm result"}
    local_path = OUTPUT_DIR / "teacher" / _TEACHER_CACHE_NAME
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes((BACKEND_DIR / remote_url.lstrip("/")).read_bytes())
    except OSError as exc:
        logger.warning("[Illustration] 老师形象落盘失败: %s", exc)
        return {"ok": False, "reason": "teacher portrait save failed"}
    return {"ok": True, "path": str(local_path), "url": remote_url}
