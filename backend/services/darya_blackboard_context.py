"""Compact, truthful context for the current conversation blackboard."""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from services.darya_teaching_stage import explicit_topic_switch_from_message


_KEEP_BOARD = re.compile(
    r"(?:保留|沿用|继续用|仍用|对照|参照|继续看).{0,12}(?:黑板|板书)"
    r"|(?:黑板|板书).{0,12}(?:保留|沿用|继续用|仍用|对照|参照)"
)


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def blackboard_context_from_action(action: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Describe a recorded board script without claiming browser rendering."""
    if not isinstance(action, Mapping) or action.get("kind") != "DEMONSTRATE":
        return None
    script = action.get("blackboard_script")
    if not isinstance(script, Mapping) or not script.get("ops"):
        return None
    demonstration = action.get("demonstration") or {}
    intent = demonstration.get("current_intent") if isinstance(demonstration, Mapping) else None
    intent = intent if isinstance(intent, Mapping) else {}
    raw_items = intent.get("items")
    items = [item for item in raw_items if isinstance(item, Mapping)][:8] if isinstance(raw_items, list) else []
    labels = {str(item.get("id")): _text(item.get("label"), 80) for item in items}
    arrows = []
    raw_relations = intent.get("relations")
    for relation in raw_relations if isinstance(raw_relations, list) else []:
        if not isinstance(relation, Mapping) or len(arrows) >= 6:
            continue
        source = labels.get(str(relation.get("from")), "")
        target = labels.get(str(relation.get("to")), "")
        if source and target:
            arrows.append({"from": source, "to": target, "meaning": _text(relation.get("label"), 80)})
    return {
        "status": "SCRIPT_RECORDED_RENDER_NOT_VERIFIED",
        "title": _text(intent.get("title"), 120),
        "items": [{"label": labels[str(item.get("id"))], "description": _text(item.get("description"), 150)} for item in items],
        "arrows": arrows,
    }


def current_blackboard_context(history: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Find the board still in this topic, respecting an explicit keep request."""
    for row in reversed(list(history or [])):
        if not isinstance(row, Mapping):
            continue
        if row.get("role") == "user":
            message = str(row.get("content") or "")
            if explicit_topic_switch_from_message(message) and not _KEEP_BOARD.search(message):
                return None
        context = blackboard_context_from_action(row.get("conversation_action"))
        if context is not None:
            return context
    return None
