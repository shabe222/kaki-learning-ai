"""Small evidence-first adaptive route policy for the DARYA learning kernel."""
from __future__ import annotations

from typing import Any, Mapping

from services.darya_domain_knowledge import DomainKnowledgePack
from services.darya_domain_registry import DEFAULT_PACK


ROUTE_CONTRACT_VERSION = "darya_adaptive_knowledge_route_v1"
READY_STATUSES = {"demonstrated", "mastered"}


def state_status(state: Mapping[str, Any] | None) -> str:
    return str((state or {}).get("mastery_status") or "unknown")


def is_ready(state: Mapping[str, Any] | None) -> bool:
    return state_status(state) in READY_STATUSES


def _goal_text(learner_goal: str, perspective: Mapping[str, Any]) -> str:
    current_goal = perspective.get("current_goal")
    perspective_goal = current_goal.get("value") if isinstance(current_goal, Mapping) else ""
    return f"{learner_goal} {perspective_goal or ''}".lower()


def _pack_branch_score(
    pack: DomainKnowledgePack, node_id: str, text: str, evidence: Mapping[str, Any],
) -> int:
    node = pack.get_node(node_id)
    score = sum(max(1, len(cue)) for cue in node.route_cues if cue.lower() in text)
    branch_hint = str(evidence.get("branch_hint") or "")
    if branch_hint == node_id:
        score += 100
    return score


def select_competing_branch(
    *, parent_node_id: str, learner_goal: str,
    perspective: Mapping[str, Any], evidence: Mapping[str, Any],
    pack: DomainKnowledgePack = DEFAULT_PACK,
) -> tuple[str | None, list[str]]:
    options = pack.children(parent_node_id)
    if len(options) < 2:
        return (options[0] if options else None), []
    text = _goal_text(learner_goal, perspective)
    ranked = sorted(options, key=lambda node_id: (_pack_branch_score(pack, node_id, text, evidence), node_id), reverse=True)
    return ranked[0], ranked[1:]


def _movement(pack: DomainKnowledgePack, current_node_id: str, next_node_id: str) -> str:
    if current_node_id == next_node_id:
        return "stay"
    if next_node_id in pack.ancestors(current_node_id):
        return "backward"
    return "forward"


def initial_route_plan(
    *, target_node_id: str, learner_goal: str,
    pack: DomainKnowledgePack = DEFAULT_PACK,
) -> dict[str, Any]:
    node = pack.get_node(target_node_id)
    return {
        "contract_version": ROUTE_CONTRACT_VERSION,
        "domain_id": pack.domain_id,
        "learner_goal": learner_goal,
        "target_node": target_node_id,
        "current_node": target_node_id,
        "next_node": target_node_id,
        "movement": "start",
        "reason": f"先从“{node.title}”做一个短诊断，已有能力会直接跳过，不先假设你什么都不会。",
        "blocking_prerequisite": None,
        "alternative_next_nodes": [],
        "confidence": 0.72,
        "recommended_intervention": "DIAGNOSTIC_PROBE",
        "evidence_overrode_perspective": False,
    }


def decide_route(
    *, learner_goal: str, target_node_id: str, current_node_id: str,
    node_states: Mapping[str, Mapping[str, Any]], diagnosis: Mapping[str, Any],
    evidence: Mapping[str, Any], perspective: Mapping[str, Any],
    allow_prerequisite_backtrack: bool = True,
    pack: DomainKnowledgePack = DEFAULT_PACK,
) -> dict[str, Any]:
    """Choose what to learn next; scaffold choice remains outside this policy."""
    blocking = str(diagnosis.get("blocking_prerequisite") or "") or None
    alternatives: list[str] = []
    recommended = "CONTINUE_NODE"
    confidence = float(diagnosis.get("confidence") or 0.55)
    evidence_overrode_perspective = False

    if blocking and allow_prerequisite_backtrack and not is_ready(node_states.get(blocking)):
        next_node_id = blocking
        current_title = pack.get_node(current_node_id).title
        block_title = pack.get_node(blocking).title
        reason = f"你卡的不是“{current_title}”本身，而是前面的“{block_title}”还没稳。先把这一格补掉，后面会顺很多。"
        recommended = "REPAIR_PREREQUISITE"
        confidence = max(confidence, 0.82)
        # Perspective may prioritize urgency, but observable prerequisite
        # evidence remains authoritative.
        if any(term in _goal_text(learner_goal, perspective) for term in ("明天", "面试", "赶", "tomorrow", "interview")):
            evidence_overrode_perspective = True
    elif not is_ready(node_states.get(current_node_id)):
        next_node_id = current_node_id
        title = pack.get_node(current_node_id).title
        reason = f"先留在“{title}”：当前证据只够调整帮助量，还不够把这一格当作已经掌握。"
        recommended = "CONTINUE_NODE"
    elif current_node_id == target_node_id:
        branch, alternatives = select_competing_branch(
            parent_node_id=current_node_id,
            learner_goal=learner_goal,
            perspective=perspective,
            evidence=evidence,
            pack=pack,
        )
        if branch:
            next_node_id = branch
            reason = f"“{pack.get_node(current_node_id).title}”已有可迁移证据，下一步按你的目标进入“{pack.get_node(branch).title}”。"
            recommended = "ADVANCE_BRANCH"
        else:
            next_node_id = current_node_id
            reason = f"“{pack.get_node(current_node_id).title}”已有进展；先做一次不同变体，再决定是否结束这一段。"
            recommended = "TRANSFER_CHECK"
    else:
        target = pack.get_node(target_node_id)
        unresolved_direct = [
            node_id for node_id in target.prerequisites
            if not is_ready(node_states.get(node_id))
        ]
        if unresolved_direct:
            # Probe the prerequisite closest to the goal.  If an earlier gap is
            # exposed, Diagnosis routes backward on the following decision.
            next_node_id = unresolved_direct[-1]
            title = pack.get_node(next_node_id).title
            reason = f"前一格已有可迁移证据，向目标推进到“{title}”做短诊断；如果更早的前置不稳，会再退回补。"
            recommended = "FORWARD_DIAGNOSTIC_PROBE"
        else:
            next_node_id = target_node_id
            reason = f"目标所需的前置已经有可观察证据，回到“{target.title}”继续。"
            recommended = "ADVANCE_TO_TARGET"

    movement = _movement(pack, current_node_id, next_node_id)
    return {
        "contract_version": ROUTE_CONTRACT_VERSION,
        "domain_id": pack.domain_id,
        "learner_goal": learner_goal,
        "target_node": target_node_id,
        "current_node": current_node_id,
        "next_node": next_node_id,
        "movement": movement,
        "reason": reason,
        "blocking_prerequisite": blocking if next_node_id == blocking else None,
        "alternative_next_nodes": alternatives,
        "confidence": round(min(0.96, max(0.3, confidence)), 2),
        "recommended_intervention": recommended,
        "evidence_overrode_perspective": evidence_overrode_perspective,
    }


def public_route(
    plan: Mapping[str, Any], *, pack: DomainKnowledgePack = DEFAULT_PACK,
) -> dict[str, Any]:
    current_id = str(plan.get("current_node") or "")
    next_id = str(plan.get("next_node") or "")
    blocking_id = str(plan.get("blocking_prerequisite") or "")
    return {
        **dict(plan),
        "current_title": pack.nodes[current_id].title if current_id in pack.nodes else None,
        "next_title": pack.nodes[next_id].title if next_id in pack.nodes else None,
        "blocking_prerequisite_title": pack.nodes[blocking_id].title if blocking_id in pack.nodes else None,
        "alternative_next_nodes": [
            {"id": node_id, "title": pack.nodes[node_id].title}
            for node_id in plan.get("alternative_next_nodes") or [] if node_id in pack.nodes
        ],
    }
