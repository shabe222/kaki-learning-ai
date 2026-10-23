from __future__ import annotations

import json
from typing import Any

from .learner_state import resolve_learner_state
from .models import ContextMode, PlannerRequest


MAX_RECENT_TURNS = 6
MAX_RECENT_CONTEXT_CHARS = 4000
TARGET_RECENT_CONTEXT_CHARS = 3500
MAX_MODEL_CONTEXT_CHARS = 12000


def _select_recent_turns(request: PlannerRequest) -> list[dict[str, Any]]:
    if request.context_mode == ContextMode.CURRENT_ITEM_ONLY:
        return []

    relevant = [turn for turn in request.recent_turns if turn.related_to_topic and turn.relevance_score > 0]
    selected = relevant[-MAX_RECENT_TURNS:]

    # Preserve chronological order while trimming from the oldest content first.
    total = sum(len(turn.content) for turn in selected)
    if total <= MAX_RECENT_CONTEXT_CHARS:
        return [turn.model_dump(mode="json") for turn in selected]

    remaining = MAX_RECENT_CONTEXT_CHARS
    bounded: list[dict[str, Any]] = []
    for turn in reversed(selected):
        if remaining <= 0:
            break
        content = turn.content
        if len(content) > remaining:
            content = content[-remaining:]
        item = turn.model_dump(mode="json")
        item["content"] = content
        bounded.append(item)
        remaining -= len(content)
    bounded.reverse()
    return bounded


def build_model_context(request: PlannerRequest) -> dict[str, Any]:
    """Build ordered, bounded context with explicit intent and source authority separated."""
    turns = _select_recent_turns(request)
    sources = [source.model_dump(mode="json") for source in request.selected_sources]
    evidence = sorted(
        request.learning_evidence,
        key=lambda item: (item.recency_rank, -item.confidence),
    )[:12]
    conflicts = [item.model_dump(mode="json") for item in request.source_conflicts]
    confirmed = [item.model_dump(mode="json") for item in request.confirmed_facts]

    learner_state = resolve_learner_state(request)

    payload: dict[str, Any] = {
        "priority_rules": {
            "focus_priority": [
                "current_explicit_confusion",
                "current_focus_adjustment",
                "current_selected_item",
                "recent_related_turns",
                "current_session_learning_evidence",
                "older_context_summary_as_weak_reference",
            ],
            "fact_priority": [
                "user_selected_authoritative_sources",
                "locked_confirmed_facts",
                "current_related_conversation",
                "project_confirmed_course_content",
                "model_knowledge_only_as_supplement",
            ],
        },
        "topic_hint": request.topic_hint,
        "explicit_confusion": request.explicit_confusion,
        "focus_adjustment": request.focus_adjustment,
        "context_mode": request.context_mode.value,
        "recent_related_turns": turns,
        "older_context_summary": (
            None if request.context_mode == ContextMode.CURRENT_ITEM_ONLY else request.older_context_summary
        ),
        "selected_sources": sources,
        "source_conflicts": conflicts,
        "confirmed_facts": confirmed,
        "learning_evidence": [item.model_dump(mode="json") for item in evidence],
        "learner_state": learner_state.model_dump(mode="json"),
        "draft_revision": request.draft_revision,
        "adjustment_count": request.adjustment_count,
        "clarification_count": request.clarification_count,
        "locale": request.locale,
        "context_budget": {
            "max_relevant_turns": MAX_RECENT_TURNS,
            "target_recent_chars": TARGET_RECENT_CONTEXT_CHARS,
            "max_recent_chars": MAX_RECENT_CONTEXT_CHARS,
        },
    }

    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw) <= MAX_MODEL_CONTEXT_CHARS:
        return payload

    # Explicit intent, conflicts, confirmed facts and learning evidence are retained.
    for source in payload["selected_sources"]:
        source["excerpt"] = source["excerpt"][:900]
    if payload["older_context_summary"]:
        payload["older_context_summary"] = payload["older_context_summary"][:500]
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw) <= MAX_MODEL_CONTEXT_CHARS:
        return payload

    payload["selected_sources"] = payload["selected_sources"][:4]
    payload["recent_related_turns"] = payload["recent_related_turns"][-4:]
    return payload
