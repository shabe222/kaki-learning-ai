"""Single-turn cognition contract for the DARYA conversation.

This module deliberately stays in-memory and side-effect free.  It consolidates
the decisions already produced by the existing goal, capability, search, and
current-perspective authorities into one bounded contract that the response
provider can follow.  It does not expose private chain-of-thought and it does
not execute tools.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


CONTRACT_VERSION = "darya_cognition_frame_v1"


@dataclass(frozen=True)
class CognitionFrame:
    """The smallest useful contract for one user turn."""

    contract_version: str = CONTRACT_VERSION
    user_situation: str = ""
    goal: str = ""
    stakes: str = "GENERAL"
    known_facts: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    decision_criteria: tuple[str, ...] = ()
    assistant_commitments: tuple[str, ...] = ()
    selected_capability: str = "CONVERSATION_ONLY"
    next_action: str = "ANSWER"
    success_signal: str = "给出一个能让用户继续行动的具体结果。"
    risk_level: str = "NONE"
    search_required: bool = False
    evidence_status: str = "NOT_REQUIRED"
    confidence: float = 0.0
    fallback_policy: str = "PRESERVE_GOAL_AND_BOUNDARIES"

    def public_dict(self) -> dict[str, Any]:
        """Return a safe projection for SSE/debugging and tests."""
        return asdict(self)


def _plain(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "public_dict"):
        candidate = value.public_dict()
        if isinstance(candidate, Mapping):
            return candidate
    if hasattr(value, "evidence"):
        candidate = value.evidence()
        if isinstance(candidate, Mapping):
            return candidate
    return {}


def _value(value: Any, key: str, default: Any = None) -> Any:
    item = _mapping(value).get(key, default)
    if isinstance(item, Mapping) and "value" in item:
        return item.get("value", default)
    return item


def _texts(value: Any, *, limit: int = 4, item_limit: int = 180) -> tuple[str, ...]:
    if isinstance(value, str):
        values: Sequence[Any] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        values = ()
    result: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            item = item.get("fact") or item.get("value") or item.get("evidence_span") or ""
        text = _plain(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _capability_from(*sources: Any) -> str:
    for source in sources:
        for key in ("kind", "outcome", "recommended_capability"):
            value = _plain(_value(source, key), 80).upper()
            if value and value not in {"NONE", "CONVERSATION_ONLY", "N/A"}:
                return value
    return "CONVERSATION_ONLY"


def _action_for(*, risk_level: str, clarification: bool, search_required: bool, capability: str) -> str:
    if risk_level == "URGENT":
        return "SAFETY"
    if clarification:
        return "CLARIFY"
    if search_required:
        return "SEARCH"
    if capability in {"ASSESSMENT", "CODE_LAB"}:
        return "PRACTICE"
    if capability not in {"CONVERSATION_ONLY", "NONE"}:
        return "CREATE"
    return "ANSWER"


def _success_signal(*, action: str, capability: str, goal: str, fallback: str) -> str:
    if action == "SAFETY":
        return "用户确认当前安全，并连接到一位可靠的现实支持者。"
    if action == "CLARIFY":
        return "用户只需回答一个会实质改变方案的关键问题。"
    if action == "SEARCH":
        suffix = f" 下一步接上：{fallback}" if fallback else ""
        return "完成与用户目标直接相关的核实，并明确哪些事实仍未核实。" + suffix
    if action == "PRACTICE":
        return "用户完成一小步真实练习，得到可解释的反馈。"
    if action == "CREATE":
        return f"完成一个可保存、可修改的{capability}成果，并让它服务于“{goal or '当前目标'}”。"
    return fallback or "给出一个能让用户继续行动的具体结果。"


def build_cognition_frame(
    message: str,
    *,
    goal_plan: Any | None = None,
    capability_intent: Any | None = None,
    capability_action: Any | None = None,
    search_plan: Any | None = None,
    current_perspective: Any | None = None,
    evidence_bundle: Any | None = None,
) -> CognitionFrame:
    """Merge existing authorities without creating a second decision system."""
    goal_data = _mapping(goal_plan)
    intent_data = _mapping(capability_intent)
    action_data = _mapping(capability_action)
    perspective_data = _mapping(current_perspective)
    search_data = _mapping(search_plan)
    evidence_data = _mapping(evidence_bundle)

    risk_level = _plain(
        _value(goal_plan, "risk_level") or _value(perspective_data.get("current_risks"), "risk_level") or "NONE",
        40,
    ).upper()
    if risk_level not in {"NONE", "CONCERN", "URGENT"}:
        risk_level = "NONE"

    goal = _plain(
        _value(goal_plan, "goal")
        or _value(perspective_data, "current_goal")
        or _value(perspective_data, "explicit_user_need")
        or message,
        300,
    )
    user_situation = _plain(
        _value(perspective_data, "likely_underlying_need")
        or _value(goal_plan, "protected_need")
        or message,
        420,
    )
    known_facts = _texts(
        [
            *(_value(goal_plan, "observed_user_facts") or ()),
            *(_value(goal_plan, "constraints") or ()),
        ],
    )
    unknowns = _texts(
        _value(goal_plan, "missing_information")
        or _value(perspective_data, "uncertainty")
        or (),
    )
    criteria = _texts(_value(goal_plan, "decision_criteria") or ())
    commitments = _texts(_value(goal_plan, "assistant_commitments") or ())

    capability = _capability_from(capability_action, intent_data, goal_plan)
    search_required = bool(
        _value(goal_plan, "external_research_needed")
        or _value(search_plan, "should_search")
        or _value(search_plan, "decision") in {"SEARCH_RECOMMENDED", "SEARCH_REQUIRED"}
    )
    clarification = bool(
        _value(goal_plan, "clarification_question")
        or _value(perspective_data, "clarification_required")
    )
    action = _action_for(
        risk_level=risk_level,
        clarification=clarification,
        search_required=search_required,
        capability=capability,
    )

    evidence_status = _plain(
        _value(evidence_bundle, "status")
        or ("SEARCH_REQUIRED" if search_required else "NOT_REQUIRED"),
        60,
    ).upper()
    if not evidence_status:
        evidence_status = "NOT_REQUIRED"

    fallback = _plain(
        _value(goal_plan, "useful_first_move")
        or _value(perspective_data, "recommended_next_move")
        or "给出一个能让用户继续行动的具体结果。",
        300,
    )
    confidence = _value(goal_plan, "confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0

    return CognitionFrame(
        user_situation=user_situation,
        goal=goal,
        stakes=_plain(_value(goal_plan, "priority") or "GENERAL", 40).upper(),
        known_facts=known_facts,
        unknowns=unknowns,
        decision_criteria=criteria,
        assistant_commitments=commitments,
        selected_capability=capability,
        next_action=action,
        success_signal=_success_signal(
            action=action,
            capability=capability,
            goal=goal,
            fallback=fallback,
        ),
        risk_level=risk_level,
        search_required=search_required,
        evidence_status=evidence_status,
        confidence=confidence,
    )


def build_cognition_frame_directive(frame: CognitionFrame) -> str:
    """Build bounded provider instructions; never request hidden reasoning."""
    selected = {
        "contract_version": frame.contract_version,
        "user_situation": frame.user_situation,
        "goal": frame.goal,
        "stakes": frame.stakes,
        "known_facts": frame.known_facts,
        "unknowns": frame.unknowns,
        "decision_criteria": frame.decision_criteria,
        "assistant_commitments": frame.assistant_commitments,
        "selected_capability": frame.selected_capability,
        "next_action": frame.next_action,
        "success_signal": frame.success_signal,
        "risk_level": frame.risk_level,
        "search_required": frame.search_required,
        "evidence_status": frame.evidence_status,
        "confidence": frame.confidence,
    }
    return (
        "DARYA 认知契约（仅用于本轮行动决策，不向用户展示字段或隐藏推理）：\n"
        + json.dumps(selected, ensure_ascii=False)
        + "\n必须先站在用户处境中推进目标，再处理字面问题。"
        "next_action=CLARIFY 时最多只问一个会改变方案的问题，并同时给出一个临时有用结果；"
        "其他情况下先完成 useful first move，不要把搜索、整理、比较、生成资料等核心认知劳动推回给用户。"
        "若 evidence_status=UNVERIFIED 或存在弱证据，只校准事实边界，不得编造；但仍保留 goal、decision_criteria 和下一步。"
        "能力暂时不可用时，保留目标并选择可逆替代动作，不要把内部降级标签端给用户。"
        "回答结束前应让用户看到当前完成了什么，以及如何判断下一步成功。"
    )


__all__ = [
    "CONTRACT_VERSION",
    "CognitionFrame",
    "build_cognition_frame",
    "build_cognition_frame_directive",
]
