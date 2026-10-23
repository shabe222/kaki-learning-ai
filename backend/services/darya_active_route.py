"""Public, thread-scoped read model for DARYA's visible active route.

This module deliberately owns no learning state.  It projects the established
Darya route authority together with the latest formally consumed assessment or
code-submission resource plan.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


CONTRACT_VERSION = "darya_active_route_v1"
EVIDENCE_TRIGGERS = {
    "ELIGIBLE_ASSESSMENT_EVIDENCE": "assessment",
    "ELIGIBLE_CODE_SUBMISSION_EVIDENCE": "code_submission",
}


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return _text(value, 80) or None


def _node_titles(snapshot: Mapping[str, Any]) -> dict[str, str]:
    graph = snapshot.get("graph") or {}
    nodes = graph.get("nodes") or []
    if isinstance(nodes, Mapping):
        iterable = nodes.values()
    else:
        iterable = nodes
    result: dict[str, str] = {}
    for node in iterable:
        if not isinstance(node, Mapping):
            continue
        node_id = _text(node.get("id") or node.get("node_id"), 120)
        title = _text(node.get("title"), 180)
        if node_id and title:
            result[node_id] = title
    return result


def _passed_milestones(snapshot: Mapping[str, Any], titles: Mapping[str, str]) -> list[dict[str, str]]:
    route = snapshot.get("route") or {}
    states = snapshot.get("node_states") or {}
    current_id = _text(route.get("current_node_id"), 120)
    milestones: list[dict[str, str]] = []
    seen: set[str] = set()
    history = route.get("history") or []
    for item in history:
        if not isinstance(item, Mapping):
            continue
        after = item.get("after") or {}
        node_id = _text(after.get("current_node") or item.get("evidence_node"), 120)
        if not node_id or node_id == current_id or node_id in seen:
            continue
        state = states.get(node_id) if isinstance(states, Mapping) else None
        status = _text((state or {}).get("mastery_status"), 40)
        if status not in {"developing", "demonstrated", "mastered"}:
            continue
        milestones.append({
            "title": titles.get(node_id) or _text((state or {}).get("concept"), 180) or "已探索的一步",
            "status": "已有证据" if status == "developing" else "已有迁移证据",
        })
        seen.add(node_id)
    return milestones[-4:]


def build_active_route_projection(
    snapshot: Mapping[str, Any], *, evidence_plan: Mapping[str, Any] | None = None,
    evidence_event: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a safe public view; return None for a genuinely fresh thread."""
    route = snapshot.get("route") or None
    if not isinstance(route, Mapping):
        return None
    latest_plan = route.get("latest_plan") or {}
    titles = _node_titles(snapshot)
    current_id = _text(route.get("current_node_id"), 120)
    target_id = _text(route.get("target_node_id"), 120)
    current_title = (
        _text(latest_plan.get("next_title"), 180)
        or titles.get(current_id)
        or _text((snapshot.get("state") or {}).get("concept"), 180)
        or "当前学习节点"
    )
    target_title = titles.get(target_id) or _text(latest_plan.get("current_title"), 180) or current_title
    state = snapshot.get("state") or {}
    trigger = _text((evidence_plan or {}).get("trigger"), 80)
    source = EVIDENCE_TRIGGERS.get(trigger, "conversation")
    event_payload = (evidence_event or {}).get("payload") or {}
    correct = event_payload.get("correct") if isinstance(event_payload, Mapping) else None
    changed = source in {"assessment", "code_submission"} and isinstance(correct, bool)

    if source == "assessment":
        summary = "测评作答已成为新的学习证据"
        explanation = (
            "这次作答通过正式评估，下一步增加迁移挑战。"
            if correct else "这次作答暴露了具体缺口，下一步先缩小范围补齐它。"
        )
    elif source == "code_submission":
        summary = "代码提交已成为新的学习证据"
        explanation = (
            "服务端测试确认提交通过，下一步进入更开放的迁移任务。"
            if correct else "服务端测试定位到仍未通过的部分，下一步先做定向调试。"
        )
    else:
        summary = "路线来自这段对话中的可观察学习证据"
        explanation = _text(latest_plan.get("reason"), 420) or "当前证据还不足以改变路线，先保留这一格。"

    plan_purpose = _text((evidence_plan or {}).get("purpose"), 300)
    next_title = plan_purpose or _text(latest_plan.get("next_title"), 180) or current_title
    next_reason = explanation if source != "conversation" else (_text(latest_plan.get("reason"), 420) or explanation)
    event_id = (evidence_event or {}).get("id")
    plan_id = (evidence_plan or {}).get("id")
    route_revision = int(route.get("revision") or 0)
    evidence_revision = f"p{plan_id or 0}-e{event_id or 0}"
    current_gap = _text(state.get("current_misconception"), 300)
    if not current_gap:
        status = _text(state.get("mastery_status"), 40)
        current_gap = "还需要不同情境中的独立证据" if status in {"developing", "demonstrated"} else "还需要一轮可观察证据才能确认"

    observed_at = _iso((evidence_event or {}).get("created_at")) or _iso(snapshot.get("updated_at"))
    return {
        "contract_version": CONTRACT_VERSION,
        "thread_id": _text(snapshot.get("thread_id"), 120),
        "revision": f"r{route_revision}-{evidence_revision}",
        "authority": {
            "route": "DaryaLearningRouteState",
            "evidence": "WorkspaceLearningEvent",
            "next_resource": "GraphResourcePlan",
        },
        "goal": _text(route.get("learner_goal"), 300) or target_title,
        "domain_title": _text((snapshot.get("domain") or {}).get("title"), 180),
        "target_title": target_title,
        "current_title": current_title,
        "passed_milestones": _passed_milestones(snapshot, titles),
        "current_gap": current_gap,
        "latest_change": {
            "source": source,
            "summary": summary,
            "explanation": explanation,
            "changed": changed,
            "event_ref": f"workspace-event-{event_id}" if event_id else None,
            "observed_at": observed_at,
        },
        "next": {
            "title": next_title,
            "reason": next_reason,
            "resource_type": _text((evidence_plan or {}).get("resource_type"), 80) or None,
        },
        "after_title": target_title if target_title != current_title else None,
        "why_this_learning": next_reason,
        "updated_at": observed_at,
    }
