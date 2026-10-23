# -*- coding: utf-8 -*-
"""让生成智能体看自己的产出，并回报它看见的问题。

栅格图来自与 PPTX 导出同一份版式计划，所以模型审的就是用户会拿到的那份几何。
这里只负责"看"和"报告"：判定结果交回调用方，改不改、怎么改由生成链路决定。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from services.slide_raster import data_url, render_deck_pngs

logger = logging.getLogger(__name__)

MAX_PAGES_PER_REVIEW = 8

# 审的是版式事实，不是品味。每条都要求指出页码与具体位置，避免"看起来不错"式回答。
REVIEW_SYSTEM_PROMPT = """你在检查一份学习 PPT 的实际渲染效果。你会看到逐页图片，
每张图前面标着它的页码。请只报告看得见的版式缺陷，不要评价内容好坏、不要提改进建议。

要检查的缺陷类型：
1. 遮挡：文字被图片、色块或其它元素压住，看不全。
2. 溢出：文字超出画面边界，或压到页脚/页码上。
3. 碰撞：两处文字互相重叠。
4. 裁切：图片被拉变形，或关键内容被裁掉。
5. 空页：整页几乎只有背景，或声明的配图没有出现。

严格输出 JSON，不要输出其它内容：
{"pages":[{"page":1,"issues":[{"type":"遮挡","detail":"第 2 行要点被右侧插图压住"}]}]}
没有缺陷的页不要出现在 pages 里。每页最多 3 条，每条 detail 不超过 40 字。
只写你真正看到的；拿不准就不写。"""


def _review_message(pngs: list[bytes], page_numbers: list[int]) -> dict:
    blocks: list[dict[str, Any]] = [{
        "type": "text",
        "text": "以下是这份 PPT 的渲染图，按顺序对应页码：" + "、".join(str(n) for n in page_numbers) + "。",
    }]
    for png, number in zip(pngs, page_numbers):
        blocks.append({"type": "text", "text": f"第 {number} 页："})
        blocks.append({"type": "image_url", "image_url": {"url": data_url(png)}})
    return {"role": "user", "content": blocks}


def _parse_findings(raw: Any) -> list[dict]:
    text = str(raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(text[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return []
    pages = parsed.get("pages") if isinstance(parsed, dict) else None
    findings: list[dict] = []
    if not isinstance(pages, list):
        return findings
    for entry in pages:
        if not isinstance(entry, Mapping):
            continue
        try:
            page = int(entry.get("page"))
        except (TypeError, ValueError):
            continue
        issues = []
        for issue in (entry.get("issues") or [])[:3]:
            if not isinstance(issue, Mapping):
                continue
            detail = str(issue.get("detail") or "").strip()[:80]
            if detail:
                issues.append({"type": str(issue.get("type") or "缺陷")[:12], "detail": detail})
        if issues:
            findings.append({"page": page, "issues": issues})
    return findings


async def review_deck_visually(
    deck: Mapping[str, Any], *, llm: Any = None, max_pages: int = MAX_PAGES_PER_REVIEW,
) -> dict:
    """渲染 deck 并让多模态模型报告版式缺陷。

    返回 {"ok", "pages_reviewed", "findings", "raw"}。任何失败都不抛给调用方：
    复核是增强步骤，不能因为它不可用就让产物生成失败。
    """
    try:
        pngs = render_deck_pngs(deck)
    except Exception:
        logger.warning("[visual-review] 渲染失败，跳过复核", exc_info=True)
        return {"ok": False, "reason": "render_failed", "pages_reviewed": 0, "findings": []}
    if not pngs:
        return {"ok": False, "reason": "no_pages", "pages_reviewed": 0, "findings": []}

    limit = max(1, min(int(max_pages or len(pngs)), len(pngs)))
    if llm is None:
        from services.llm_service import get_llm_service

        llm = await get_llm_service()
    try:
        result = await llm.chat(
            [_review_message(pngs[:limit], list(range(1, limit + 1)))],
            system_prompt=REVIEW_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=1500,
        )
    except Exception:
        logger.warning("[visual-review] 模型复核失败，跳过", exc_info=True)
        return {"ok": False, "reason": "model_failed", "pages_reviewed": limit, "findings": []}

    raw = result.get("content") if isinstance(result, dict) else result
    findings = _parse_findings(raw)
    logger.info("[visual-review] 复核 %d 页，发现 %d 页有问题", limit, len(findings))
    return {"ok": True, "pages_reviewed": limit, "findings": findings, "raw": str(raw)[:1500]}
