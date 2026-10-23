# -*- coding: utf-8 -*-
"""把版式计划渲染成 PNG —— 智能体看自己产出的眼睛。

PPTX 导出与这里的栅格图消费同一份 ``build_slide_plan``，所以模型审图时看到的
几何，就是用户打开导出文件时看到的几何。图形按计划顺序绘制（与 PPTX 一致），
因此"图压在字上"这类缺陷在栅格图里同样可见，而不是被预览器掩盖。
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from services.visual_theme import theme_tokens
from services.workspace_presentations import (
    EMU_PER_IN,
    SLIDE_H_EMU,
    SLIDE_W_EMU,
    build_slide_plan,
    cover_crop,
)

# 1600×900：够看清版式与压字，又不至于让多模态请求过大。
DEFAULT_PX_PER_INCH = 120

_BODY_FONTS = ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf")
_BODY_BOLD_FONTS = ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf")
_CODE_FONTS = ("C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/msyh.ttc")
_FONT_CACHE: dict[tuple[float, bool, bool], Any] = {}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = str(value or "").lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _font(size_pt: float, *, code: bool, bold: bool, px_per_inch: int):
    px = max(6, int(round(float(size_pt) * px_per_inch / 72.0)))
    key = (px, code, bold)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    candidates = _CODE_FONTS if code else (_BODY_BOLD_FONTS if bold else _BODY_FONTS)
    font = None
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            try:
                font = ImageFont.truetype(str(path), px)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _wrap(text: str, font, max_px: float) -> list[str]:
    """按测得宽度折行；中英混排逐字累积，避免整段溢出文本框。"""
    lines: list[str] = []
    for raw in str(text or "").split("\n"):
        if not raw:
            lines.append("")
            continue
        current = ""
        width = 0.0
        for ch in raw:
            advance = font.getlength(ch)
            if current and width + advance > max_px:
                lines.append(current)
                current, width = "", 0.0
            current += ch
            width += advance
        lines.append(current)
    return lines


def _draw_rect(draw: ImageDraw.ImageDraw, shape: dict, theme: dict, scale: float) -> None:
    x, y, w, h = (v / EMU_PER_IN * scale for v in shape["box"])
    if w <= 0 or h <= 0:
        return
    box = (x, y, x + w, y + h)
    fill = _hex_to_rgb(theme[shape["color"]])
    outline = None
    if shape.get("line"):
        outline = _hex_to_rgb(theme[shape["line"]])
    if shape["round"]:
        radius = shape["radius"] if shape["radius"] is not None else 0.12
        draw.rounded_rectangle(
            box, radius=max(0, int(radius * min(w, h) / 2)),
            fill=fill, outline=outline, width=1 if outline else 0,
        )
    else:
        draw.rectangle(box, fill=fill, outline=outline, width=1 if outline else 0)


def _draw_text(draw: ImageDraw.ImageDraw, shape: dict, theme: dict, scale: float) -> None:
    x, y, w, h = (v / EMU_PER_IN * scale for v in shape["box"])
    if w <= 0 or not shape["text"]:
        return
    font = _font(shape["size"], code=shape["font"] == "code",
                 bold=shape["bold"], px_per_inch=int(scale))
    color = _hex_to_rgb(theme[shape["color"]])
    lines = _wrap(shape["text"], font, max(1.0, w))
    line_h = shape["size"] * scale / 72.0 * 1.2 * shape["line_spacing"]
    top = y + (h - line_h * len(lines)) / 2 if shape["anchor"] == "middle" else y
    for index, line in enumerate(lines):
        if not line:
            continue
        if shape["align"] == "center":
            left = x + (w - font.getlength(line)) / 2
        elif shape["align"] == "right":
            left = x + w - font.getlength(line)
        else:
            left = x
        draw.text((left, top + index * line_h), line, font=font, fill=color)


def _paste_picture(target: Image.Image, shape: dict, scale: float) -> None:
    x, y, w, h = (v / EMU_PER_IN * scale for v in shape["box"])
    box_w, box_h = max(1, int(w)), max(1, int(h))
    path = Path(str(shape["path"]))
    try:
        if shape.get("cover"):
            image = cover_crop(path, box_w, box_h).convert("RGB")
        else:
            image = Image.open(path).convert("RGB")
    except Exception:
        return
    try:
        target.paste(image.resize((box_w, box_h), Image.LANCZOS), (int(x), int(y)))
    except Exception:
        return


def render_plan_pngs(plan: dict, *, px_per_inch: int = DEFAULT_PX_PER_INCH) -> list[bytes]:
    """Rasterise every page of a layout plan. Drawing order matches the PPTX backend."""
    theme = theme_tokens(plan.get("theme"))
    width = max(64, int(round(SLIDE_W_EMU / EMU_PER_IN * px_per_inch)))
    height = max(64, int(round(SLIDE_H_EMU / EMU_PER_IN * px_per_inch)))
    rendered: list[bytes] = []
    for page in plan.get("pages") or []:
        canvas = Image.new("RGB", (width, height), _hex_to_rgb(theme["background"]))
        draw = ImageDraw.Draw(canvas)
        for shape in page.get("shapes") or []:
            kind = shape.get("kind")
            if kind == "rect":
                _draw_rect(draw, shape, theme, px_per_inch)
            elif kind == "picture":
                _paste_picture(canvas, shape, px_per_inch)
            elif kind == "text":
                _draw_text(draw, shape, theme, px_per_inch)
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")
        rendered.append(buffer.getvalue())
    return rendered


def render_deck_pngs(deck: Any, *, px_per_inch: int = DEFAULT_PX_PER_INCH) -> list[bytes]:
    """Convenience entry: deck artifact -> one PNG per slide."""
    return render_plan_pngs(build_slide_plan(deck), px_per_inch=px_per_inch)


def data_url(png_bytes: bytes) -> str:
    """Inline data URL for the multimodal LLM message."""
    import base64

    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
