from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

MAX_HISTORY_MESSAGES = 20
MAX_SELECTED_TURNS = 6
MAX_TOTAL_CHARS = 4000
MAX_TURN_CHARS = 900

_CURRENT_ITEM_ONLY_PATTERNS = (
    "只看这道题",
    "只根据这道题",
    "只用这道题",
    "只针对这道题",
    "只看当前题目",
    "只根据当前题目",
    "只用当前题目",
    "仅看这道题",
    "仅根据这道题",
    "仅针对当前题目",
    "不要参考之前",
    "忽略之前的对话",
    "不要看历史",
)

_SMALLTALK_PATTERNS = (
    "你好",
    "谢谢",
    "哈哈",
    "再见",
    "晚安",
    "吃饭了吗",
    "天气怎么样",
)

_INTERNAL_MESSAGE_KEYS = {
    "tool_call",
    "tool_calls",
    "function_call",
    "system_prompt",
    "internal",
    "hidden",
    "agent_event",
}

_INTERNAL_JSON_KEYS = {
    "agent",
    "resource_type",
    "request_id",
    "tool_calls",
    "function_call",
    "system_prompt",
}

_INTERNAL_PREFIXES = (
    "data:{",
    "data: {",
    "<|system|>",
    "<|assistant to=",
    "[tool]",
    "[工具调用]",
    "begin tool",
)

_STOP_TERMS = {
    "这个",
    "那个",
    "什么",
    "怎么",
    "为什么",
    "可以",
    "然后",
    "一下",
    "问题",
    "学习",
    "内容",
    "进行",
    "就是",
    "还是",
    "我们",
    "你们",
    "他们",
}


@dataclass(frozen=True)
class PlannerContextLoadResult:
    turns: list[dict[str, Any]]
    context_mode: str
    history_loaded: bool
    history_message_count: int


def resolve_context_mode(
    requested_mode: str | None,
    *intent_texts: str | None,
) -> str:
    """Resolve the effective context mode without trusting client history payloads."""
    normalized = (requested_mode or "relevant_recent").strip().lower()
    if normalized == "current_item_only":
        return "current_item_only"
    combined = "\n".join(text.strip() for text in intent_texts if isinstance(text, str) and text.strip())
    if any(pattern in combined for pattern in _CURRENT_ITEM_ONLY_PATTERNS):
        return "current_item_only"
    return "relevant_recent"


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())


def _looks_like_smalltalk(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return True
    if len(normalized) <= 12 and any(pattern in normalized for pattern in _SMALLTALK_PATTERNS):
        return True
    return False


def _looks_like_internal_json(content: str) -> bool:
    text = content.strip()
    lowered = text.lower()
    if any(lowered.startswith(prefix) for prefix in _INTERNAL_PREFIXES):
        return True
    if not (text.startswith("{") and text.endswith("}")):
        return False
    try:
        parsed = json.loads(text)
    except Exception:
        return False
    if not isinstance(parsed, dict):
        return False
    keys = {str(key).lower() for key in parsed.keys()}
    if "type" in keys and keys.intersection(_INTERNAL_JSON_KEYS):
        return True
    event_type = str(parsed.get("type") or "").lower()
    return event_type in {
        "context",
        "agent_start",
        "agent_content",
        "agent_complete",
        "memory_candidate",
        "memory_saved",
        "done",
    }


def _sanitize_message(message: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    role = str(message.get("role") or "").strip().lower()
    if role not in {"user", "assistant"}:
        return None
    if any(message.get(key) for key in _INTERNAL_MESSAGE_KEYS):
        return None
    content = _normalize_text(message.get("content"))
    if not content:
        return None
    if role == "assistant" and _looks_like_internal_json(content):
        return None
    return {
        "role": role,
        "content": content[:MAX_TURN_CHARS],
        "turn_id": str(message.get("id") or message.get("message_id") or f"history_{index}")[:100],
        "source_index": index,
    }


def _term_set(text: str) -> set[str]:
    normalized = _normalize_text(text).lower()
    terms: set[str] = set()
    for word in re.findall(r"[a-z0-9_+#.-]{2,}", normalized):
        if word not in _STOP_TERMS:
            terms.add(word)
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        if sequence not in _STOP_TERMS and len(sequence) <= 16:
            terms.add(sequence)
        for size in (2, 3, 4):
            if len(sequence) < size:
                continue
            for offset in range(len(sequence) - size + 1):
                token = sequence[offset : offset + size]
                if token not in _STOP_TERMS:
                    terms.add(token)
    return terms


def _overlap_score(content: str, query_terms: set[str]) -> float:
    if not query_terms:
        return 0.0
    content_terms = _term_set(content)
    overlap = query_terms.intersection(content_terms)
    if not overlap:
        return 0.0
    weighted = sum(min(len(term), 6) for term in overlap)
    return min(1.0, 0.35 + weighted / 24.0)


def _dedupe_turns(turns: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for turn in turns:
        key = (str(turn.get("role")), _normalize_text(turn.get("content")).lower())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(turn)
    return result


def _bound_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = turns[-MAX_SELECTED_TURNS:]
    remaining = MAX_TOTAL_CHARS
    bounded: list[dict[str, Any]] = []
    for turn in reversed(selected):
        if remaining <= 0:
            break
        content = str(turn["content"])
        if len(content) > remaining:
            content = content[-remaining:]
        item = dict(turn)
        item["content"] = content
        bounded.append(item)
        remaining -= len(content)
    bounded.reverse()
    return bounded


def select_relevant_chat_turns(
    messages: Iterable[Any],
    *,
    topic_hint: str,
    explicit_confusion: str | None = None,
    focus_adjustment: str | None = None,
    context_mode: str = "relevant_recent",
) -> list[dict[str, Any]]:
    """Select safe, chronological, topic-related chat turns using deterministic rules."""
    effective_mode = resolve_context_mode(
        context_mode,
        topic_hint,
        explicit_confusion,
        focus_adjustment,
    )
    if effective_mode == "current_item_only":
        return []

    history = list(messages)[-MAX_HISTORY_MESSAGES:]
    sanitized = [item for index, raw in enumerate(history) if (item := _sanitize_message(raw, index))]
    query_text = "\n".join(
        text for text in (topic_hint, explicit_confusion, focus_adjustment) if isinstance(text, str) and text.strip()
    )
    query_terms = _term_set(query_text)

    candidate_scores: dict[int, float] = {}
    for position, item in enumerate(sanitized):
        score = _overlap_score(item["content"], query_terms)
        distance_from_latest = len(sanitized) - 1 - position
        if item["role"] == "user" and distance_from_latest <= 1 and not _looks_like_smalltalk(item["content"]):
            score = max(score, 0.45 - distance_from_latest * 0.08)
        if score > 0:
            candidate_scores[position] = min(1.0, score)

    # Keep the assistant response that directly follows a relevant user turn.
    for position, score in list(candidate_scores.items()):
        if sanitized[position]["role"] == "user" and position + 1 < len(sanitized):
            if sanitized[position + 1]["role"] == "assistant":
                candidate_scores[position + 1] = max(candidate_scores.get(position + 1, 0.0), min(0.85, score * 0.85))

    selected: list[dict[str, Any]] = []
    total = len(sanitized)
    for position, item in enumerate(sanitized):
        score = candidate_scores.get(position, 0.0)
        if score <= 0:
            continue
        selected.append(
            {
                "role": item["role"],
                "content": item["content"],
                "related_to_topic": True,
                "relevance_score": round(score, 3),
                "recency_rank": max(1, total - position),
                "turn_id": item["turn_id"],
            }
        )

    # The current explicit confusion is authoritative and must survive even when not yet persisted.
    if explicit_confusion and explicit_confusion.strip():
        selected.append(
            {
                "role": "user",
                "content": _normalize_text(explicit_confusion)[:MAX_TURN_CHARS],
                "related_to_topic": True,
                "relevance_score": 1.0,
                "recency_rank": 1,
                "turn_id": "explicit_confusion",
            }
        )

    selected = _dedupe_turns(selected)
    return _bound_turns(selected)


async def load_planner_recent_turns(
    db: Any,
    user_id: int,
    thread_id: str | None,
    *,
    topic_hint: str,
    explicit_confusion: str | None = None,
    focus_adjustment: str | None = None,
    context_mode: str = "relevant_recent",
    history_loader: Callable[[Any, int, str], Awaitable[list[dict[str, Any]]]] | None = None,
) -> PlannerContextLoadResult:
    effective_mode = resolve_context_mode(
        context_mode,
        topic_hint,
        explicit_confusion,
        focus_adjustment,
    )
    normalized_thread = (thread_id or "").strip()
    is_legacy = not normalized_thread or normalized_thread.lower() == "default" or normalized_thread.lower().startswith("qa-")
    if effective_mode == "current_item_only" or is_legacy:
        return PlannerContextLoadResult([], effective_mode, False, 0)

    if history_loader is None:
        from services.chat_service import load_chat_history

        history_loader = load_chat_history
    try:
        history = await history_loader(db, user_id, normalized_thread)
    except Exception:
        # Chat history is optional context. Planning must still continue safely.
        return PlannerContextLoadResult([], effective_mode, False, 0)

    turns = select_relevant_chat_turns(
        history,
        topic_hint=topic_hint,
        explicit_confusion=explicit_confusion,
        focus_adjustment=focus_adjustment,
        context_mode=effective_mode,
    )
    return PlannerContextLoadResult(turns, effective_mode, True, len(history))
