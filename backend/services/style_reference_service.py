"""
风格参考图服务（rjianb image2 角色）

复用 backend/services/qwen_image_service.py 的 httpx 调用骨架，
但 prompt 模板面向"风格参考"而非"思维导图"。

5 种预制风格：wabi-sabi / cyber / children-illustration / cyberpunk / ink
"""
import os
import time
import asyncio
import logging
import httpx
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ==================== 配置 ====================
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = "qwen-image-2.0-pro-2026-04-22"
QWEN_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

BACKEND_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BACKEND_DIR / "static" / "style_references"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# ==================== 5 种预制风格 ====================
STYLE_PRESETS = {
    "wabi-sabi": {
        "name_zh": "侘寂日式",
        "color_palette": ["#F5F1E8", "#D4C5A9", "#8B7355", "#3E2723"],
        "description": "Minimalist Japanese wabi-sabi aesthetic. Muted earth tones, generous white space, natural paper texture, asymmetry, the beauty of imperfection.",
    },
    "cyber": {
        "name_zh": "赛博科技",
        "color_palette": ["#0F172A", "#06B6D4", "#8B5CF6", "#EC4899"],
        "description": "Futuristic cyber tech. Dark navy background, neon cyan/purple/pink accents, glowing effects, geometric shapes, modern sans-serif.",
    },
    "children-illustration": {
        "name_zh": "儿童插画",
        "color_palette": ["#FEF3C7", "#FCA5A5", "#93C5FD", "#86EFAC"],
        "description": "Warm children's book illustration. Soft pastel colors, rounded shapes, friendly characters, hand-drawn feel, warm and inviting.",
    },
    "cyberpunk": {
        "name_zh": "赛博朋克",
        "color_palette": ["#0A0A0A", "#FF0080", "#00FFFF", "#FFFF00"],
        "description": "High-contrast cyberpunk. Pure black background, vivid neon pink/cyan/yellow, glitch effects, dense information layers.",
    },
    "ink": {
        "name_zh": "国风水墨",
        "color_palette": ["#F8F4E9", "#1A1A1A", "#8B0000", "#D4A574"],
        "description": "Chinese ink wash painting. Rice paper texture, black/gray/red ink, brushstroke aesthetic, negative space, poetic atmosphere.",
    },
}

# 图片尺寸选项
STYLE_IMAGE_PRESETS = {
    "wabi-sabi": "1920*1080",
    "cyber": "1920*1080",
    "children-illustration": "1920*1080",
    "cyberpunk": "1920*1080",
    "ink": "1920*1080",
}


def get_style_meta(style_key: str) -> Dict[str, Any]:
    """返回某个风格的元信息（用于前端展示）"""
    if style_key not in STYLE_PRESETS:
        raise ValueError(f"Unknown style: {style_key}. Valid: {list(STYLE_PRESETS.keys())}")
    preset = STYLE_PRESETS[style_key]
    return {
        "style_key": style_key,
        "name_zh": preset["name_zh"],
        "color_palette": preset["color_palette"],
        "description": preset["description"],
    }


def build_style_reference_prompt(topic: str, style_key: str) -> str:
    """构造风格参考图的 prompt"""
    if style_key not in STYLE_PRESETS:
        raise ValueError(f"Unknown style: {style_key}")
    preset = STYLE_PRESETS[style_key]

    palette_str = ", ".join(preset["color_palette"])

    prompt = f"""A high-quality style reference image for a UI/UX design project.

**Subject (主题):** {topic}

**Visual Style:**
{preset['description']}

**Color Palette:** {palette_str}

**Composition Requirements:**
- Single cohesive scene representing the design language
- No text overlay, no watermarks, no logos
- Suitable as a design mood board reference
- 16:9 aspect ratio, 1920x1080
- High resolution, sharp details
- The image should evoke the FEELING of this style, not literal content

**Negative prompt (避免):** low quality, blurry, watermarks, English text, garbled text, distorted, ugly
"""
    return prompt


def extract_image_urls(response_data: dict) -> list:
    """从 API 响应中提取图片 URL 列表"""
    urls = []
    try:
        choices = response_data.get("output", {}).get("choices", [])
        for choice in choices:
            content = choice.get("message", {}).get("content", [])
            for item in content:
                if isinstance(item, dict) and "image" in item:
                    urls.append(item["image"])
    except Exception as e:
        logger.error(f"[StyleRef] 提取图片 URL 失败: {e}")
    return urls


async def download_image(url: str, prefix: str = "style_ref") -> Optional[str]:
    """下载远程图片到本地"""
    try:
        timestamp = str(int(time.time() * 1000))
        filename = f"{prefix}_{timestamp}.png"
        local_path = STATIC_DIR / filename
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code == 200 and len(response.content) > 1024:
                local_path.write_bytes(response.content)
                logger.info(f"[StyleRef] 图片已下载: {filename} ({len(response.content)//1024}KB)")
                return f"/static/style_references/{filename}"
            else:
                logger.error(f"[StyleRef] 下载失败: status={response.status_code}")
                return None
    except Exception as e:
        logger.error(f"[StyleRef] 下载异常: {e}")
        return None


async def _generate_one(topic: str, style_key: str, n: int = 1) -> Dict[str, Any]:
    """生成单张风格参考图"""
    prompt = build_style_reference_prompt(topic, style_key)
    size = STYLE_IMAGE_PRESETS.get(style_key, "1920*1080")

    payload = {
        "model": QWEN_MODEL,
        "input": {
            "messages": [{"role": "user", "content": [{"text": prompt}]}]
        },
        "parameters": {
            "size": size,
            "n": n,
            "watermark": False,
            "prompt_extend": True,
            "negative_prompt": "low quality, blurry, watermark, text errors, garbled, distorted, ugly, English text"
        }
    }
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json"
    }

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(QWEN_ENDPOINT, json=payload, headers=headers)
            elapsed = time.time() - start

            if response.status_code != 200:
                error_msg = f"API {response.status_code}: {response.text[:200]}"
                logger.error(f"[StyleRef] {error_msg}")
                return {"style": style_key, "success": False, "error": error_msg, "elapsed": round(elapsed, 1)}

            data = response.json()
            urls = extract_image_urls(data)
            if not urls:
                return {"style": style_key, "success": False, "error": "no image in response", "elapsed": round(elapsed, 1)}

            first_url = urls[0]
            local_url = await download_image(first_url, prefix=f"ref_{style_key}")
            return {
                "style": style_key,
                "success": True,
                "image_url": local_url or first_url,
                "original_url": first_url,
                "prompt": prompt,
                "elapsed": round(elapsed, 1),
            }
    except Exception as e:
        logger.exception(f"[StyleRef] 异常: {e}")
        return {"style": style_key, "success": False, "error": str(e), "elapsed": 0}


async def generate_style_references(
    topic: str,
    styles: Optional[List[str]] = None,
    n: int = 1,
) -> Dict[str, Any]:
    """
    并发生成多张风格参考图

    Args:
        topic: 设计主题
        styles: 风格 key 列表，默认 ["wabi-sabi", "cyber", "ink"] 3 种
        n: 每种风格生成几张（1-3）

    Returns:
        {
            "success": True,
            "ref_images": [
                {"style": "wabi-sabi", "image_url": "/static/...", "success": True, "elapsed": 12.3},
                ...
            ],
            "elapsed_seconds": 25.6
        }
    """
    if styles is None:
        styles = ["wabi-sabi", "cyber", "ink"]

    # 校验
    for s in styles:
        if s not in STYLE_PRESETS:
            return {"success": False, "error": f"Unknown style: {s}", "valid_styles": list(STYLE_PRESETS.keys())}

    start = time.time()
    tasks = [_generate_one(topic, s, n=n) for s in styles]
    results = await asyncio.gather(*tasks)
    elapsed = time.time() - start

    return {
        "success": True,
        "ref_images": results,
        "elapsed_seconds": round(elapsed, 1),
    }
