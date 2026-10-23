"""Safe, bounded excerpts from a real draft rejected by formal generation review."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


MAX_WORKING_CHARS = 6000


def _text(value: Any, limit: int = 600) -> str:
    return " ".join(str(value or "").split())[:limit]


class GenerationDraftBlocked(ValueError):
    """A generated candidate exists, but it must not receive artifact PASS."""

    def __init__(self, code: str, *, reason: str, body: str):
        super().__init__(code)
        self.code = code
        self.public_reason = _text(reason, 320)
        self.working_result = {
            "kind": "UNREVIEWED_DRAFT",
            "body": body[:MAX_WORKING_CHARS],
            "limitation": self.public_reason,
        }


def presentation_draft(deck: Mapping[str, Any], *, reason: str) -> str:
    lines = [
        "## 可继续修改的 PPT 初稿",
        "这份逐页内容已生成，但正式质量检查未通过；事实与表述仍需核对，尚不能作为正式 PPT 打开或导出。",
        f"待修正：{_text(reason, 320)}",
    ]
    for index, slide in enumerate(list(deck.get("slides") or [])[:30], start=1):
        if not isinstance(slide, Mapping):
            continue
        title = _text(slide.get("title"), 120) or f"第 {index} 页"
        lines.append(f"\n### {index}. {title}")
        for field in ("body", "subtitle", "narration"):
            value = _text(slide.get(field), 480)
            if value:
                lines.append(value)
        for item in list(slide.get("items") or [])[:5]:
            value = _text(item, 180)
            if value:
                lines.append(f"- {value}")
    return "\n".join(lines)[:MAX_WORKING_CHARS]


def mindmap_draft(tree: Any, *, reason: str) -> str:
    lines = [
        "## 可继续修改的思维导图初稿",
        "节点已生成，但结构或主题检查未通过；以下只是草稿关系，尚未取得正式产物审核。",
        f"待修正：{_text(reason, 320)}",
    ]
    if not isinstance(tree, Mapping):
        lines.append(_text(tree, 4000))
        return "\n".join(lines)[:MAX_WORKING_CHARS]
    pending: list[tuple[Mapping[str, Any], int]] = [(tree, 0)]
    while pending and len(lines) < 90:
        node, depth = pending.pop()
        label = _text(node.get("label"), 120)
        note = _text(node.get("note"), 240)
        if label:
            lines.append(f"{'  ' * min(depth, 4)}- {label}" + (f"：{note}" if note else ""))
        children = [child for child in (node.get("children") or []) if isinstance(child, Mapping)]
        pending.extend((child, depth + 1) for child in reversed(children))
    return "\n".join(lines)[:MAX_WORKING_CHARS]


def handout_draft(blocks: Sequence[Mapping[str, Any]], *, reason: str) -> str:
    lines = [
        "## 可继续修改的资料初稿",
        "正文已生成，但必需的内容结构尚未齐全；以下内容未经正式产物审核。",
        f"待补：{_text(reason, 320)}",
    ]
    for block in blocks[:20]:
        kind = _text(block.get("kind"), 30)
        body = _text(block.get("text"), 650)
        if body:
            lines.append(f"\n### {kind or '内容'}\n{body}")
    return "\n".join(lines)[:MAX_WORKING_CHARS]
