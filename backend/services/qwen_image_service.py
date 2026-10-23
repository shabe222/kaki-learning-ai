"""
阿里云 qwen-image-2.0-pro 思维导图图片生成服务

- 模型: qwen-image-2.0-pro-2026-04-22 (推荐用于思维导图)
- 端点: https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
- 模式: 同步调用 (一次返回结果)
- 优势: 复杂文字渲染、逻辑架构图、中文支持
"""

from __future__ import annotations
import os
import time
import logging
import httpx
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ==================== 配置 ====================
QWEN_API_KEY = os.getenv("QWEN_API_KEY") or ""
QWEN_MODEL = "qwen-image-2.0-pro-2026-04-22"
QWEN_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

# 静态目录
BACKEND_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BACKEND_DIR / "static" / "mindmap_images"
STATIC_DIR.mkdir(parents=True, exist_ok=True)


# ==================== 主入口 ====================
async def generate_mindmap_image(
    topic: str,
    branches_data: list,
    size: str = "2560*1440",
    style: str = "professional",
    n: int = 1
) -> Dict[str, Any]:
    """
    调用 qwen-image-2.0-pro 生成思维导图

    Args:
        topic: 思维导图主题
        branches_data: 分支数据 list[dict]，每个含 name, type, children
        size: 图片尺寸 (默认 2560*1440 横向 16:9)
        style: 风格 (professional/academic/tech/minimal)
        n: 生成张数 (1-6)

    Returns:
        {
            "success": True,
            "image_url": "/static/mindmap_images/xxx.png",  # 本地 URL
            "original_url": "https://dashscope.oss...",      # 原始 URL
            "model": "qwen-image-2.0-pro-2026-04-22",
            "size": "2560*1440"
        }
    """
    # 构造 prompt
    prompt = build_mindmap_prompt(topic, branches_data, style)

    # 请求体
    payload = {
        "model": QWEN_MODEL,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"text": prompt}
                    ]
                }
            ]
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

    logger.info(f"[QwenImage] 开始生成思维导图: topic={topic}, size={size}, style={style}")
    start_time = time.time()

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(QWEN_ENDPOINT, json=payload, headers=headers)

            elapsed = time.time() - start_time
            logger.info(f"[QwenImage] 响应状态: {response.status_code}, 耗时: {elapsed:.1f}s")

            if response.status_code != 200:
                error_msg = f"API 返回状态 {response.status_code}"
                try:
                    error_data = response.json()
                    error_msg += f": {error_data.get('message', response.text[:200])}"
                except Exception:
                    error_msg += f": {response.text[:200]}"
                logger.error(f"[QwenImage] {error_msg}")
                return {"success": False, "error": error_msg}

            data = response.json()

            # 解析图片 URL
            image_urls = extract_image_urls(data)
            if not image_urls:
                logger.error(f"[QwenImage] 响应中无图片 URL: {data}")
                return {"success": False, "error": "响应中未找到图片 URL", "raw_response": data}

            # 下载第一张到本地（避免防盗链）
            first_url = image_urls[0]
            local_url = await download_image(first_url, prefix="qwen_mindmap")

            return {
                "success": True,
                "image_url": local_url or first_url,
                "original_url": first_url,
                "all_urls": image_urls,
                "model": QWEN_MODEL,
                "size": size,
                "elapsed_seconds": round(elapsed, 1)
            }

    except httpx.TimeoutException:
        logger.error(f"[QwenImage] API 超时 (180s)")
        return {"success": False, "error": "API 调用超时（>180秒）"}
    except Exception as e:
        logger.exception(f"[QwenImage] 异常: {e}")
        return {"success": False, "error": f"异常: {str(e)}"}


# ==================== Prompt 构造 ====================
def build_mindmap_prompt(topic: str, branches_data: list, style: str = "professional") -> str:
    """
    构造思维导图 prompt
    严格遵循"从左到右"布局
    """
    # 风格描述
    style_map = {
        "professional": "Clean modern professional learning platform style. White background, soft pastel colors (blue/purple/green/orange/red/purple). Subtle drop shadows, modern rounded rectangles, smooth bezier curve connections. Suitable for educational websites.",
        "academic": "Academic textbook style with cream/ivory background. Elegant serif Chinese typography. Hand-drawn feel with subtle paper texture. Sepia and earthy tones.",
        "tech": "Tech/futuristic style. Dark navy blue background with neon cyan, purple, and pink accents. Glowing effects, geometric shapes, modern sans-serif font.",
        "minimal": "Minimalist black and white. Simple clean lines, no decoration. Focus purely on structure and Chinese text. Modern sans-serif typography."
    }
    style_desc = style_map.get(style, style_map["professional"])

    # 类型颜色映射
    type_color_map = {
        "core":      "blue/purple (#4F46E5 to #7C3AED gradient)",
        "knowledge": "green (#10B981 to #059669 gradient)",
        "difficult": "orange (#F59E0B to #EF4444 gradient)",
        "practice":  "red (#EF4444 to #DC2626 gradient)",
        "extension": "purple (#8B5CF6 to #EC4899 gradient)"
    }

    # 构造分支描述
    branches_text = []
    for i, b in enumerate(branches_data[:6], 1):
        b_name = b.get("name", "")
        b_type = b.get("type", "knowledge")
        b_color = type_color_map.get(b_type, "blue")
        children = b.get("children", [])

        children_text = ", ".join([c.get("name", "") for c in children[:5]])
        branches_text.append(
            f"   Branch {i}: \"{b_name}\" (color: {b_color})\n"
            f"     Sub-nodes: {children_text}"
        )

    branches_summary = "\n".join(branches_text) if branches_text else "（暂无分支数据）"

    # 最终 prompt
    prompt = f"""A clean, professional horizontal mind map in landscape orientation (16:9 ratio, 2560x1440 pixels).

★★★ CRITICAL LAYOUT REQUIREMENT - READ FROM LEFT TO RIGHT ★★★

1. **LEFT SIDE - Central Topic Node (1 node):**
   - ONE large rounded rectangle on the far LEFT
   - Contains the title "{topic}" in large bold Chinese characters (黑体 / 思源黑体)
   - White background, purple border (#4F46E5)
   - Centered vertically in the canvas

2. **MIDDLE - Main Branches (4-5 nodes, vertically aligned):**
   - Main branch nodes placed in the middle of the canvas
   - Each main branch is a white rounded rectangle with a colored dot on the left
   - Connected to the central node with smooth bezier curves flowing from LEFT to RIGHT
   - Colors based on content type (see below)

3. **RIGHT SIDE - Leaf Nodes:**
   - 2-4 smaller leaf nodes per main branch
   - Positioned to the RIGHT of their parent branch
   - Light tinted background matching parent color
   - Connected with thin curved lines

**Content (Chinese, no English):**
Topic: {topic}

Main Branches (with sub-nodes):
{branches_summary}

**Style:**
{style_desc}

**Color Scheme for Branches:**
- 核心概念 (Core Concepts): blue/purple #4F46E5
- 关键知识点 (Key Knowledge): green #10B981
- 重点难点 (Difficult Points): orange #F59E0B
- 实战练习 (Practice): red #EF4444
- 拓展方向 (Extension): purple #8B5CF6

**Typography Requirements:**
- ALL TEXT in Chinese (Simplified Chinese characters)
- Clear, bold, modern sans-serif Chinese font (黑体 / 思源黑体 / Source Han Sans)
- Black or dark gray text (#1A202C)
- No English text, no romanization, no pinyin
- No watermarks, no logos, no decorative elements
- Professional learning platform / educational website aesthetic

**Quality Requirements:**
- High resolution, 2K quality
- Sharp, clear text rendering (this model excels at text in images)
- Proper spacing between nodes
- Smooth bezier curve connections between nodes
- Clean, readable, structured
"""
    return prompt


# ==================== 工具函数 ====================
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
        logger.error(f"[QwenImage] 提取图片 URL 失败: {e}")
    return urls


async def download_image(url: str, prefix: str = "qwen_mindmap") -> Optional[str]:
    """下载远程图片到本地（避免防盗链），返回 /static/... 相对路径"""
    try:
        timestamp = str(int(time.time() * 1000))
        filename = f"{prefix}_{timestamp}.png"
        local_path = STATIC_DIR / filename

        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code == 200 and len(response.content) > 1024:
                local_path.write_bytes(response.content)
                logger.info(f"[QwenImage] 图片已下载: {filename} ({len(response.content)//1024}KB)")
                return f"/static/mindmap_images/{filename}"
            else:
                logger.error(f"[QwenImage] 下载失败: status={response.status_code}, size={len(response.content) if response.content else 0}")
                return None
    except Exception as e:
        logger.error(f"[QwenImage] 下载异常: {e}")
        return None
