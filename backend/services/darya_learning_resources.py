"""Bounded personalized learning-resource orchestration for DARYA Phase 3.3.

Route owns the knowledge node. Scaffold owns the amount of help. This module
owns the form of the next learning intervention. Resource records remain in
thread/node learning state and are never promoted to Long-Term Memory here.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services.darya_domain_knowledge import CandidateTemplate, DomainKnowledgePack, KnowledgeNode
from services.darya_domain_registry import DEFAULT_PACK, pack_for_node


RESOURCE_TYPES = (
    "EXPLANATION_CARD",
    "PRACTICE_TASK",
    "WORKED_EXAMPLE",
    "QUICK_CHECK",
)

RESOURCE_JOBS = {
    "EXPLANATION_CARD": "修补当前理解缺口",
    "PRACTICE_TASK": "让学习者产出可判断的变体证据",
    "WORKED_EXAMPLE": "用完整例子把抽象规则落到执行过程",
    "QUICK_CHECK": "用一个很小的问题确认迁移是否仍然成立",
}

_FIT_CORRECTION = re.compile(
    r"其实.{0,24}(?:定义|概念|规则).{0,20}(?:没懂|不懂|没明白|不清楚)|"
    r"(?:你判断|这个判断|适配判断).{0,20}(?:不对|错了)|"
    r"(?:不是只需要|不只是).{0,12}(?:练习|做题)",
    re.I,
)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def is_fit_correction(message: str) -> bool:
    return bool(_FIT_CORRECTION.search(_text(message, 1000)))


def requested_teaching_form(message: str) -> str | None:
    """Honor a learner's explicit delivery request without changing mastery."""
    text = _text(message, 500)
    if re.search(r"(?:请|先|换成|用|给我).{0,18}(?:完整例子|具体例子|例题|示范)", text):
        return "WORKED_EXAMPLE"
    if re.search(r"(?:请|先|换成|用).{0,18}(?:一步一步|分步|拆开讲|短解释)", text):
        return "EXPLANATION_CARD"
    if re.search(r"(?:请|先|给我|换成).{0,18}(?:做题|练习题|一道题|变体题)", text):
        return "PRACTICE_TASK"
    if re.search(r"(?:请|先|用).{0,18}(?:问我|追问我|检查我)", text):
        return "QUICK_CHECK"
    return None


def public_resource_history(change_history: Sequence[Mapping[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    resources = [copy.deepcopy(dict(item["resource"])) for item in change_history if isinstance(item.get("resource"), Mapping)]
    return resources[-limit:]


def apply_previous_resource_result(
    change_history: Sequence[Mapping[str, Any]], *, evidence: Mapping[str, Any],
    learner_message: str, diagnosed_gap: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Attach the current observable response to the previous resource record."""
    history = copy.deepcopy([dict(item) for item in change_history])
    previous: dict[str, Any] | None = None
    for item in reversed(history):
        resource = item.get("resource")
        if not isinstance(resource, dict):
            continue
        previous = resource
        if resource.get("effectiveness") != "PENDING":
            break
        quality = str(evidence.get("quality") or "INSUFFICIENT_EVIDENCE")
        outcome = str(evidence.get("outcome") or "uncertain")
        if is_fit_correction(learner_message):
            effectiveness = "FIT_ASSUMPTION_CORRECTED"
            reason = "学习者明确修正了当前资源适配判断；不把该修正写入长期记忆。"
        elif outcome == "correct" and quality == "TRANSFER_EXPLANATION":
            effectiveness = "EFFECTIVE_TRANSFER"
            reason = "学习者在变体中给出了可观察的迁移解释。"
        elif outcome == "correct":
            effectiveness = "PARTIAL_PROGRESS"
            reason = "学习者给出了正确证据，但仍需要迁移或重复证据。"
        elif outcome == "incorrect":
            effectiveness = "INEFFECTIVE_SAME_GAP"
            reason = "资源后仍出现同类明确误区，下一次必须改变策略。"
        else:
            effectiveness = "INCONCLUSIVE"
            reason = "当前回答不足以判断资源是否有效。"
        resource.update({
            "learner_response": _text(learner_message, 360),
            "effectiveness": effectiveness,
            "effectiveness_evidence": {
                "outcome": outcome,
                "quality": quality,
                "diagnosed_gap": _text(diagnosed_gap, 500),
                "reason": reason,
            },
            "evaluated_at": _now(),
        })
        break
    return history, copy.deepcopy(previous) if previous else None


def _presentation_context(memory: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None]:
    selected = list(memory.get("selected") or [])
    plain = next((item for item in selected if item.get("topic") == "response_policy:plain_language_first"), None)
    if plain:
        return "plain_language_first", plain
    technical = next((item for item in selected if item.get("topic") == "technical:experience"), None)
    if technical:
        return "code_centered", technical
    example_first = next((item for item in selected if item.get("topic") == "learning_preference:example_first"), None)
    if example_first:
        return "example_first", example_first
    rule_then_practice = next((item for item in selected if item.get("topic") == "learning_preference:rule_then_practice"), None)
    if rule_then_practice:
        return "rule_then_practice", rule_then_practice
    return "plain", None


def decide_resource_intent(
    *, node: KnowledgeNode, diagnosis: Mapping[str, Any], evidence: Mapping[str, Any],
    scaffold_level: str, route_plan: Mapping[str, Any], resource_history: Sequence[Mapping[str, Any]],
    perspective: Mapping[str, Any], memory: Mapping[str, Any], learner_message: str,
) -> dict[str, Any]:
    recent = [dict(item) for item in resource_history if item.get("target_node") == node.id]
    previous = recent[-1] if recent else None
    outcome = str(evidence.get("outcome") or "uncertain")
    quality = str(evidence.get("quality") or "INSUFFICIENT_EVIDENCE")
    correction = is_fit_correction(learner_message)
    requested_form = requested_teaching_form(learner_message)
    if requested_form:
        resource_type = requested_form
        rationale = "按你明确提出的讲法切换这一轮材料，并继续保留独立判断。"
        alternative = "本轮优先采用你要求的形式；掌握判断仍以作答证据为准。"
    elif correction:
        resource_type = "EXPLANATION_CARD"
        rationale = "你修正了“只需要练习”的当前判断，所以先补清定义与关键区分。"
        alternative = "不继续做题，因为当前修正说明概念基础也需要补。"
    elif previous and previous.get("resource_type") == "WORKED_EXAMPLE" and previous.get("effectiveness") == "INEFFECTIVE_SAME_GAP":
        resource_type = "PRACTICE_TASK"
        rationale = "完整例子后同一误区仍在，改用一个能区分误区的练习来收集证据。"
        alternative = "不再重复例子，因为上一种形式没有改变这个判断。"
    elif previous and previous.get("effectiveness") == "INEFFECTIVE_SAME_GAP":
        resource_type = "WORKED_EXAMPLE" if previous.get("resource_type") != "WORKED_EXAMPLE" else "PRACTICE_TASK"
        rationale = "上一种资源后同一误区仍在，需要更换学习动作。"
        alternative = "不重复上一种形式，因为它暂时没有产生有效变化。"
    elif outcome == "correct" and quality == "TRANSFER_EXPLANATION":
        resource_type = "QUICK_CHECK"
        rationale = "你已经把规则迁移到变体，只用一个很小的检查确认它仍然稳定。"
        alternative = "不再给长解释，因为当前已经出现迁移证据。"
    elif outcome == "correct":
        resource_type = "PRACTICE_TASK"
        rationale = "规则已经能说清，但还缺一个新的变体证据。"
        alternative = "不重复讲定义，因为当前缺的是应用证据。"
    elif outcome == "incorrect" and int(len(recent)) >= 1:
        resource_type = "WORKED_EXAMPLE"
        rationale = "同一抽象判断再次出错，先用完整例子把执行过程落地。"
        alternative = "不立刻加难度，因为当前误区还没有被例子拆开。"
    elif outcome == "incorrect":
        resource_type = "EXPLANATION_CARD"
        rationale = "当前有明确误区，先用短解释修补关键区分。"
        alternative = "不马上给迁移题，因为基础判断还没有稳定。"
    else:
        resource_type = "QUICK_CHECK"
        rationale = "当前证据不足，先用一个很小的问题区分可能的卡点。"
        alternative = "不先写长解释，因为还不能确定真正缺口。"

    presentation_mode, preference = _presentation_context(memory)
    confidence = float(diagnosis.get("confidence") or 0.0)
    fit_level = "HIGH" if confidence >= 0.75 or correction else "MEDIUM"
    preference_reason = None
    if presentation_mode == "plain_language_first":
        preference_reason = "按已确认偏好先讲人话，再放专业术语。"
    elif presentation_mode == "code_centered":
        preference_reason = "用已确认的代码经验降低理解成本；它不参与掌握判断。"
    elif presentation_mode == "example_first":
        preference_reason = "按已确认偏好先给具体情境；它不参与掌握判断。"
    elif presentation_mode == "rule_then_practice":
        preference_reason = "按已确认偏好先给一条短规则，然后马上练习；它不参与掌握判断。"

    fit = {
        "contract_version": "darya_learner_resource_fit_v1",
        "level": fit_level,
        "current_gap": _text(diagnosis.get("likely_gap"), 500),
        "current_node": node.title,
        "evidence_strength": "明确" if confidence >= 0.75 else "有限",
        "scaffold_need": scaffold_level,
        "recent_effectiveness": previous.get("effectiveness") if previous else "NO_HISTORY",
        "why_now": rationale,
        "not_chosen_reason": alternative,
        "preference_reason": preference_reason,
        "correctable": True,
        "correction_prompt": "如果这个判断不对，直接告诉我，例如：其实定义我也没懂。",
        "contains_calibrated_percentage": False,
    }
    return {
        "contract_version": "darya_learning_resource_intent_v1",
        "learner_need": RESOURCE_JOBS[resource_type],
        "target_node": node.id,
        "target_node_title": node.title,
        "resource_type": resource_type,
        "learning_goal": node.learning_goal,
        "diagnosed_gap": _text(diagnosis.get("likely_gap"), 500),
        "scaffold_level": scaffold_level,
        "desired_outcome": "产出与当前节点证据要求相匹配的下一条可观察反应。",
        "constraints": {
            "stable_domain_only": True,
            "practice_must_not_reveal_answer": resource_type in {"PRACTICE_TASK", "QUICK_CHECK"},
            "worked_example_may_show_solution": resource_type == "WORKED_EXAMPLE",
            "blackboard_supported": False,
            "visual_demonstration_supported": False,
        },
        "relevant_context": {
            "route_movement": route_plan.get("movement"),
            "perspective_goal": (perspective.get("current_goal") or {}).get("value") if isinstance(perspective.get("current_goal"), Mapping) else None,
            "presentation_mode": presentation_mode,
            "preference_id": preference.get("id") if preference else None,
            "preference_used_for_mastery": False,
            "recent_resource_count": len(recent),
        },
        "rationale": rationale,
        "fit": fit,
        "fit_correction": {"detected": correction, "saved_to_long_term_memory": False},
        "artifact_boundary": {
            "persistence": "EPHEMERAL_LEARNING_PAYLOAD",
            "promotion_rule": "Only an explicit save/edit/version request may use the existing GeneratedItem/GeneratedItemVersion path.",
            "second_artifact_store_created": False,
        },
    }


def compose_learning_resource(
    *, node: KnowledgeNode, intent: Mapping[str, Any], base_candidate: CandidateTemplate,
    pack: DomainKnowledgePack | None = None,
) -> dict[str, Any]:
    pack = pack or pack_for_node(node.id) or DEFAULT_PACK
    resource_type = str(intent.get("resource_type") or "QUICK_CHECK")
    mode = str((intent.get("relevant_context") or {}).get("presentation_mode") or "plain")
    seed = pack.resource_seeds[node.id]
    plain_rule = seed.plain_rule
    micro_example = seed.micro_example
    if mode == "code_centered":
        opening = f"代码视角：{plain_rule}"
        contextual_example = node.memory_analogy
    elif mode == "plain_language_first":
        opening = f"先讲人话：{plain_rule}"
        contextual_example = micro_example
    elif mode == "example_first":
        opening = micro_example
        contextual_example = plain_rule
    elif mode == "rule_then_practice":
        opening = f"先记一条规则：{plain_rule}"
        contextual_example = f"马上用它判断：{base_candidate.prompt}"
    else:
        opening = plain_rule
        contextual_example = micro_example

    common = {
        "contract_version": "darya_learning_expert_v3",
        "role": "EXPERT_GENERATOR",
        "domain_id": pack.domain_id,
        "node_id": node.id,
        "node_title": node.title,
        "target_gap": _text(intent.get("diagnosed_gap"), 500),
        "resource_type": resource_type,
        "resource_intent": copy.deepcopy(dict(intent)),
        "lead": base_candidate.lead,
        "prompt": base_candidate.prompt,
        "hint": base_candidate.hint,
        "secondary_concepts": list(base_candidate.secondary_concepts),
        "assumed_prerequisites": list(base_candidate.assumed_prerequisites),
        "claim_keys": list(base_candidate.claim_keys),
        "external_claims": [],
        "personalized": mode != "plain",
        "presentation_mode": mode,
    }
    if resource_type == "EXPLANATION_CARD":
        structure = {
            "plain_language": opening,
            "key_distinction": node.description,
            "micro_example": contextual_example,
            "check_question": base_candidate.prompt,
        }
        if mode == "rule_then_practice":
            structure = {
                "short_rule": opening,
                "immediate_practice": base_candidate.prompt,
                "response_format": seed.quick_check_format,
                "hint": base_candidate.hint,
            }
        common.update({
            "title": f"先补清：{node.title}",
            "answer_revealed": False,
            "structure": structure,
        })
    elif resource_type == "PRACTICE_TASK":
        common.update({
            "title": f"做一个变体：{node.title}",
            "answer_revealed": False,
            "structure": {
                "stem": base_candidate.prompt,
                "learner_action": seed.practice_instruction,
                "hint": base_candidate.hint if str(intent.get("scaffold_level")) not in {"L0", "L1"} else "",
                "expected_evidence": list(node.evidence_requirements),
            },
        })
    elif resource_type == "WORKED_EXAMPLE":
        common.update({
            "title": f"看一个完整例子：{node.title}",
            "answer_revealed": True,
            "structure": {
                "scenario": seed.worked_scenario,
                "steps": list(seed.worked_steps),
                "completed_result": seed.worked_result,
                "takeaway": plain_rule,
                "follow_up": base_candidate.prompt,
            },
        })
    else:
        common.update({
            "title": f"只检查一件事：{node.title}",
            "answer_revealed": False,
            "structure": {
                "prompt": base_candidate.prompt,
                "response_format": seed.quick_check_format,
            },
        })
    return common


def resource_record(resource: Mapping[str, Any]) -> dict[str, Any]:
    intent = resource.get("resource_intent") or {}
    return {
        "resource_type": resource.get("resource_type"),
        "target_node": resource.get("node_id"),
        "purpose": intent.get("learner_need"),
        "learner_response": None,
        "effectiveness": "PENDING",
        "effectiveness_evidence": None,
        "timestamp": _now(),
    }


def render_resource_markdown(resource: Mapping[str, Any], *, include_fit: bool = True) -> str:
    resource_type = str(resource.get("resource_type") or "QUICK_CHECK")
    title = _text(resource.get("title"), 160)
    structure = resource.get("structure") or {}
    if resource_type == "EXPLANATION_CARD":
        if structure.get("short_rule"):
            body = (
                f"### {title}\n\n{structure.get('short_rule')}\n\n"
                f"**马上试一下**\n\n{structure.get('immediate_practice')}\n\n"
                f"回答方式：{structure.get('response_format')}\n\n"
                f"需要时再看提示：{structure.get('hint')}"
            )
        else:
            body = (
                f"### {title}\n\n{structure.get('plain_language')}\n\n"
                f"**关键区分**\n\n{structure.get('key_distinction')}\n\n"
                f"**放进一个小例子**\n\n{structure.get('micro_example')}\n\n"
                f"读完只回答这一问：{structure.get('check_question')}"
            )
    elif resource_type == "PRACTICE_TASK":
        hint = f"\n\n**需要时再看提示**\n\n{structure.get('hint')}" if structure.get("hint") else ""
        body = (
            f"### {title}\n\n**题目**\n\n{structure.get('stem')}\n\n"
            f"**你来完成**\n\n{structure.get('learner_action')}{hint}"
        )
    elif resource_type == "WORKED_EXAMPLE":
        steps = "\n".join(f"{index}. {step}" for index, step in enumerate(structure.get("steps") or [], 1))
        body = (
            f"### {title}\n\n**情境**\n\n{structure.get('scenario')}\n\n"
            f"**执行过程**\n\n{steps}\n\n**完整结果**\n\n{structure.get('completed_result')}\n\n"
            f"**带走这一句**\n\n{structure.get('takeaway')}\n\n"
            f"现在换回你的判断：{structure.get('follow_up')}"
        )
    else:
        body = (
            f"### {title}\n\n问题在当前互动区。\n\n回答方式：{structure.get('response_format')}"
        )

    fit = (resource.get("resource_intent") or {}).get("fit") or {}
    preference = f"\n\n呈现方式：{fit.get('preference_reason')}" if fit.get("preference_reason") else ""
    fit_block = (
        f"#### 为什么现在给你这个\n\n"
        f"当前卡点：{fit.get('current_gap')}\n\n"
        f"所以这次：{fit.get('why_now')}\n\n"
        f"{fit.get('not_chosen_reason')}{preference}\n\n"
        f"适配判断：{'高' if fit.get('level') == 'HIGH' else '中'}（定性判断，不是精确分数）\n\n"
        f"{fit.get('correction_prompt')}"
    )
    return f"{fit_block}\n\n---\n\n{body}" if include_fit else body
