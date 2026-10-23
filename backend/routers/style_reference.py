"""
风格参考图路由 — 暴露给 SOP Step 2 (image2 角色) 使用

端点（3 个）：
  - GET  /api/style-reference/styles            列出所有预制风格
  - GET  /api/style-reference/styles/{key}      获取单个风格元信息
  - POST /api/style-reference/generate          生成主题的 N 张风格参考图

注意：此服务面向"AI 团队 SOP 工具链"，不要求登录态；
     不接业务用户 JWT，但保留接口形式便于后续扩展。
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from services.style_reference_service import (
    STYLE_PRESETS,
    generate_style_references,
    get_style_meta,
)
from utils.auth import get_current_user_id

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== Pydantic Models ====================
class GenerateRequest(BaseModel):
    """POST /generate 请求体"""
    topic: str = Field(..., min_length=1, max_length=200, description="设计主题，如 '家长看学习报告'")
    styles: Optional[List[str]] = Field(
        default=None,
        description="风格 key 列表；None 时使用默认 3 套（wabi-sabi/cyber/ink）",
    )
    n: int = Field(default=1, ge=1, le=3, description="每种风格生成几张（1-3）")


# ==================== Endpoints ====================
@router.get("/api/style-reference/styles")
async def list_styles():
    """
    列出所有预制风格（含 5 种 key + 中文名 + 调色板 + 描述）

    供前端 / SOP Step 1 ChatGPT 反问完后，
    给用户/AI 选"想看哪种风格参考"用。
    """
    return {
        "success": True,
        "data": {
            "total": len(STYLE_PRESETS),
            "styles": [
                get_style_meta(k) for k in STYLE_PRESETS.keys()
            ],
        },
    }


@router.get("/api/style-reference/styles/{style_key}")
async def get_style(style_key: str):
    """
    获取单个风格的元信息（color_palette / description / 中文名）

    404 时返回标准格式，前端可统一处理。
    """
    try:
        meta = get_style_meta(style_key)
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "STYLE_NOT_FOUND",
                "message": str(e),
                "valid_styles": list(STYLE_PRESETS.keys()),
            },
        )
    return {"success": True, "data": meta}


@router.post("/api/style-reference/generate")
async def generate(
    req: GenerateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """
    生成主题的 N 张风格参考图（并发生成）

    返回结构：
    {
      "success": true,
      "data": {
        "topic": "...",
        "ref_images": [
          {"style": "wabi-sabi", "image_url": "/static/...", "success": true, "elapsed": 12.3},
          ...
        ],
        "elapsed_seconds": 25.6,
        "successful_count": 2,
        "failed_count": 1
      }
    }
    """
    result = await generate_style_references(
        topic=req.topic,
        styles=req.styles,
        n=req.n,
    )

    if not result.get("success"):
        # 风格 key 不合法等情况
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_STYLE",
                "message": result.get("error", "unknown error"),
                "valid_styles": list(STYLE_PRESETS.keys()),
            },
        )

    # 统计成功/失败
    ref_images = result.get("ref_images", [])
    successful = sum(1 for r in ref_images if r.get("success"))
    failed = len(ref_images) - successful

    return {
        "success": True,
        "data": {
            "topic": req.topic,
            "styles_requested": req.styles or ["wabi-sabi", "cyber", "ink"],
            "ref_images": ref_images,
            "elapsed_seconds": result.get("elapsed_seconds", 0),
            "successful_count": successful,
            "failed_count": failed,
        },
    }
