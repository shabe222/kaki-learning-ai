"""Deterministic presentation and resource sequencing from learner preferences.

This module changes how a learning node is presented.  It never changes formal
mastery, Evaluator output, graph order, or Tutor completion eligibility.
"""
from __future__ import annotations

from typing import Any


def _modalities(state: Any) -> list[str]:
    if hasattr(state, "preferred_modalities"):
        return list(state.preferred_modalities or [])
    return list((state or {}).get("preferred_modalities") or [])


def _weak_points(state: Any) -> list[str]:
    if hasattr(state, "weak_points"):
        return list(state.weak_points or [])
    return list((state or {}).get("weak_points") or [])


def _topic(state: Any) -> str:
    if hasattr(state, "current_topic"):
        return str(state.current_topic or "当前知识点")
    return str((state or {}).get("current_topic") or "当前知识点")


def strategy_family(state: Any) -> str:
    modalities = _modalities(state)
    if "practice" in modalities:
        return "practice_first"
    if "reading" in modalities or "document" in modalities:
        return "reading_structured"
    if "visual" in modalities or "video" in modalities:
        return "visual_guided"
    if "code" in modalities:
        return "code_guided"
    return "balanced"


def build_personalized_learning_strategy(state: Any, available_minutes: int = 20) -> dict[str, Any]:
    """Return a real ordered plan/resource contract, not a wording-only persona."""
    family = strategy_family(state)
    modalities = _modalities(state)
    weak = _weak_points(state)
    topic = _topic(state)
    minutes = max(10, min(90, int(available_minutes or 20)))

    if family == "visual_guided":
        plan = [
            {"action": "visual_concept", "resource_type": "mindmap", "ratio": 0.30},
            {"action": "guided_visual_example", "resource_type": "ppt_interactive", "ratio": 0.40},
            {"action": "quick_check", "resource_type": "question", "ratio": 0.30},
        ]
        policy = {"primary": "visual", "explanation": "diagram_then_example", "practice_ratio": 0.30}
    elif family == "practice_first":
        plan = [
            {"action": "micro_explanation", "resource_type": "tutor", "ratio": 0.15},
            {"action": "targeted_practice", "resource_type": "question", "ratio": 0.50},
            {"action": "evidence_feedback", "resource_type": "document", "ratio": 0.15},
            {"action": "retry_or_advance", "resource_type": "question", "ratio": 0.20},
        ]
        policy = {"primary": "practice", "explanation": "minimal_before_attempt", "practice_ratio": 0.70}
    elif family == "reading_structured":
        plan = [
            {"action": "structured_lecture", "resource_type": "document", "ratio": 0.50},
            {"action": "core_summary", "resource_type": "tutor", "ratio": 0.20},
            {"action": "comprehension_check", "resource_type": "question", "ratio": 0.30},
        ]
        policy = {"primary": "reading", "explanation": "structure_then_summary", "practice_ratio": 0.30}
    elif family == "code_guided":
        plan = [
            {"action": "mechanism_brief", "resource_type": "document", "ratio": 0.20},
            {"action": "worked_code", "resource_type": "code", "ratio": 0.50},
            {"action": "code_check", "resource_type": "question", "ratio": 0.30},
        ]
        policy = {"primary": "code", "explanation": "code_then_mechanism", "practice_ratio": 0.50}
    else:
        plan = [
            {"action": "concept_brief", "resource_type": "document", "ratio": 0.35},
            {"action": "worked_example", "resource_type": "tutor", "ratio": 0.30},
            {"action": "quick_check", "resource_type": "question", "ratio": 0.35},
        ]
        policy = {"primary": "balanced", "explanation": "concept_example_check", "practice_ratio": 0.35}

    remaining = minutes
    for index, step in enumerate(plan):
        if index == len(plan) - 1:
            allocated = remaining
        else:
            allocated = max(1, round(minutes * float(step["ratio"])))
            remaining -= allocated
        step["minutes"] = max(1, allocated)
        step.pop("ratio", None)

    reasons = [f"{family}_preference"]
    if weak:
        reasons.append("recent_weak_point_present")
    why = (
        f"因为你当前更偏好{policy['primary']}方式"
        + (f"，而且“{weak[0]}”仍需要加强" if weak else "")
        + "，所以这次资源顺序会按这个策略安排。"
    )
    return {
        "family": family,
        "topic": topic,
        "available_minutes": minutes,
        "plan": plan,
        "resource_order": [step["resource_type"] for step in plan],
        "presentation_policy": policy,
        "why_this_action": {"reason_codes": reasons, "text": why},
        "video_generation_requires_confirmation": "video" in modalities,
        "authority_scope": {
            "changes": ["presentation", "resource_order", "practice_ratio"],
            "does_not_change": ["mastery", "formal_evaluator", "graph_order", "completion"],
        },
    }


def resource_blueprint(state: Any, *, knowledge_type: str = "theory") -> list[str]:
    """Select three unique supported legacy resource types in strategy order."""
    strategy = build_personalized_learning_strategy(state)
    ordered = list(strategy["resource_order"])
    supported = {"document", "question", "mindmap", "code", "ppt_interactive", "tutor"}
    result: list[str] = []
    for value in ordered:
        if value in supported and value not in result:
            result.append(value)
    if knowledge_type in {"code", "practice"} and "code" not in result:
        result.insert(1 if result else 0, "code")
    for fallback in ("document", "question", "mindmap"):
        if fallback not in result:
            result.append(fallback)
        if len(result) >= 3:
            break
    return result[:3]
