"""DARYA 视觉主题令牌的唯一权威来源。

主题只是视觉边界：它决定配色、材质和气质，不决定版式、构图、叙事顺序、
插图题材或学习例子。版式与图文关系由技能包和模型每页的 design 意图决定。

PPTX 导出与视频逐帧渲染都从这里取调色，所以切换产品主题时不可能出现
「一半界面换色、另一半还留在旧主题」的越权。
"""
from __future__ import annotations

from typing import Any


# 版本级主题快照：生成时定格，之后不随产品主题变化。
# 两套主题都是严格莫兰迪：低饱和、中低对比，无纯白纯黑、无霓虹、无电商橙、无科技蓝。
PRESENTATION_THEME_SNAPSHOT: dict[str, dict[str, str]] = {
    "kaki": {
        # 暖米白 / 奶油灰纸张；低饱和柿子橙；柿叶灰绿；成熟柿红棕点缀；暖炭灰文字
        "background": "F1ECE4",
        "surface": "F7F2EA",
        "primary": "B47B60",
        "secondary": "7D8877",
        "accent": "9E6253",
        "text": "403D39",
        "muted": "817970",
        "line": "D9CFC0",
        "code_background": "3B3733",
        "code_text": "F0EAE0",
        "mood": "温暖、成熟、安静、秋日纸张、学术但不冷",
    },
    "ocean": {
        # 雾白 / 浅蓝灰；灰蓝；鼠尾草灰绿；低饱和岩粉点缀；蓝黑灰文字
        "background": "E8EDEC",
        "surface": "F0F3F1",
        "primary": "718891",
        "secondary": "7E8D83",
        "accent": "947B76",
        "text": "374045",
        "muted": "788185",
        "line": "C9D5D1",
        "code_background": "2E383C",
        "code_text": "E9EFED",
        "mood": "冷静、清透、专注、海雾、有呼吸感",
    },
}

DEFAULT_THEME = "kaki"


def normalize_theme(value: Any) -> str:
    return "ocean" if str(value or "").strip().lower() == "ocean" else DEFAULT_THEME


def theme_tokens(value: Any) -> dict[str, str]:
    """Full token set for one theme key (never partial, never a fallback mix)."""
    return PRESENTATION_THEME_SNAPSHOT[normalize_theme(value)]


def theme_rgb(value: Any, key: str) -> tuple[int, int, int]:
    """(r, g, b) for a token — used by the video frame renderer (PIL)."""
    hex_value = theme_tokens(value)[key]
    return tuple(int(hex_value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def theme_rgba(value: Any, key: str, alpha: int) -> tuple[int, int, int, int]:
    red, green, blue = theme_rgb(value, key)
    return red, green, blue, max(0, min(255, int(alpha)))


def theme_mood(value: Any) -> str:
    return theme_tokens(value)["mood"]


# 兼容旧名（历史调用点与测试以 presentation_* 命名引用同一份令牌）。
normalize_presentation_theme = normalize_theme
presentation_theme_tokens = theme_tokens
