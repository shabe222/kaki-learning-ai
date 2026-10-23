"""DARYA generalized learning kernel and adaptive-route integration.

Diagnosis, Expert, Review, Learning Decision, and Companion share one engine.
Domain-specific meaning and evidence rules live in the selected knowledge pack.
Only observable evidence and compact decisions are persisted; private model
reasoning is neither requested nor exposed.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DaryaLearningLoopState, DaryaLearningRouteState
from services.darya_adaptive_route import decide_route, initial_route_plan, is_ready, public_route
from services.prompt_ledger import dump as dump_prompt
from services.darya_competition_learning import (
    build_competition_evidence_view,
    build_competition_trace,
    build_learner_profile_projection,
    build_visual_fit_projection,
    important_claims_pass,
    privacy_state_boundary_contract,
    validate_candidate_claims,
)
from services.darya_domain_knowledge import CandidateTemplate, DomainKnowledgePack, KnowledgeNode
from services.darya_domain_registry import DEFAULT_PACK, get_domain_pack, pack_for_node
from services.darya_domain_resolver import resolve_learning_domain
from services.darya_learning_resources import (
    RESOURCE_TYPES,
    apply_previous_resource_result,
    compose_learning_resource,
    decide_resource_intent,
    is_fit_correction,
    public_resource_history,
    render_resource_markdown,
    resource_record,
    requested_teaching_form,
)
from services.tide_artifact_quality_gate import run_artifact_quality_gate


CONTRACT_VERSION = "darya_generalized_learning_kernel_v2"
CONCEPT_KEY = DEFAULT_PACK.default_node_id
CONCEPT_TITLE = DEFAULT_PACK.get_node(CONCEPT_KEY).title

SCAFFOLD_POLICY: dict[str, dict[str, Any]] = {
    "L0": {"name": "观察或暂停", "intervention": "不增加教学帮助；让学习者继续，或在内容未通过审阅时暂停交付。"},
    "L1": {"name": "轻提示", "intervention": "只给最小方向提示，保留关键推断。"},
    "L2": {"name": "关键问题", "intervention": "提出一个能定位误区或推动核心推断的问题。"},
    "L3": {"name": "结构化步骤", "intervention": "拆分执行顺序，但由学习者完成最后判断。"},
    "L4": {"name": "类比示范", "intervention": "使用不泄露原题答案的文本类比示范；当前不依赖 Blackboard。"},
    "L5": {"name": "部分代做", "intervention": "仅在明确的任务完成模式下部分代做；学习模式默认禁止。"},
}

_WITHHOLDING_QUESTION = re.compile(r"为什么.*(?:不给|不直接给).*(?:答案|结论)|为什么.*提示", re.I)
_CURRENT_FACT = re.compile(r"最新|目前版本|截至|研究表明|数据显示|current version|latest", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def scaffold_policy_contract() -> dict[str, Any]:
    return {
        "contract_version": "darya_scaffold_policy_v1",
        "levels": copy.deepcopy(SCAFFOLD_POLICY),
        "selection_rule": "Use the lowest support justified by observable evidence and review status.",
        "l5_guardrail": "L5 requires explicit TASK_COMPLETION mode; it is forbidden in this learning kernel.",
    }


def is_learning_loop_message(
    message: str, *, has_existing_state: bool = False,
    current_domain_id: str | None = None, learner_goal: str = "",
    workspace_context: str = "",
) -> bool:
    text = _text(message, 3000)
    if has_existing_state:
        if not text:
            return False
        if re.search(r"(?:先不学|暂停学习|停一下|聊点别的|换个话题|不继续学|just chat|change the subject)", text, re.I):
            return False
        if re.search(r"(?:继续刚才|继续学|接着学|换个学法|给我练|做(?:道|个)?题|写(?:个|点)?代码|试试|再解释|没懂|还是不会)", text, re.I):
            return True
        # Existing route context must not turn an ordinary new topic into a
        # learning-loop message. Resolve this utterance on its own: the saved
        # goal/workspace may explain the route, but cannot be evidence that the
        # current question still belongs to it.
        resolution = resolve_learning_domain(
            message=text,
        )
        return resolution.pack is not None
    resolution = resolve_learning_domain(
        message=text,
        current_domain_id=current_domain_id,
        learner_goal=learner_goal,
        workspace_context=workspace_context,
    )
    return resolution.pack is not None


def _evaluable_evidence(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if row.get("supports_update")]


def _mastery_status(rows: Sequence[Mapping[str, Any]]) -> str:
    evaluated = _evaluable_evidence(rows)
    if not evaluated:
        return "unknown"
    strong = [row for row in evaluated if row.get("outcome") == "correct" and row.get("quality") in {"EXPLAINED_RULE", "TRANSFER_EXPLANATION"}]
    transfers = [row for row in strong if row.get("quality") == "TRANSFER_EXPLANATION"]
    if len(strong) >= 3 and transfers:
        return "mastered"
    if len(strong) >= 2 and transfers:
        return "demonstrated"
    if strong or any(row.get("outcome") == "correct" for row in evaluated):
        return "developing"
    return "needs_support"


def _public_state(
    row: DaryaLearningLoopState | None, node: KnowledgeNode | None = None,
    pack: DomainKnowledgePack = DEFAULT_PACK,
) -> dict[str, Any]:
    node = node or pack.get_node(row.concept_key if row and row.concept_key in pack.nodes else pack.default_node_id)
    evidence_rows = list(row.evidence or []) if row else []
    status = _mastery_status(evidence_rows)
    estimate = round(float(row.mastery_estimate or 0.0), 2) if row and status != "unknown" else None
    return {
        "concept_key": node.id, "node_id": node.id, "concept": node.title,
        "mastery_status": status, "mastery_estimate": estimate,
        "current_evidence_count": len(_evaluable_evidence(evidence_rows)),
        "current_misconception": row.current_misconception if row else None,
        "diagnosis_confidence": round(float(row.diagnosis_confidence or 0.0), 2) if row else 0.0,
        "support_level": row.support_level if row else "L2",
        "last_observation": row.last_observation if row else None,
        "cycle_count": int(row.cycle_count or 0) if row else 0,
        "revision": int(row.revision or 0) if row else 0,
    }


async def get_learning_state(db: AsyncSession, *, user_id: int, thread_id: str, concept_key: str = CONCEPT_KEY) -> DaryaLearningLoopState | None:
    return (await db.execute(select(DaryaLearningLoopState).where(
        DaryaLearningLoopState.user_id == user_id,
        DaryaLearningLoopState.thread_id == thread_id,
        DaryaLearningLoopState.concept_key == concept_key,
    ))).scalar_one_or_none()


async def get_learning_states(db: AsyncSession, *, user_id: int, thread_id: str) -> list[DaryaLearningLoopState]:
    return list((await db.execute(select(DaryaLearningLoopState).where(
        DaryaLearningLoopState.user_id == user_id,
        DaryaLearningLoopState.thread_id == thread_id,
    ).order_by(DaryaLearningLoopState.id))).scalars().all())


async def get_learning_route_state(db: AsyncSession, *, user_id: int, thread_id: str) -> DaryaLearningRouteState | None:
    return (await db.execute(select(DaryaLearningRouteState).where(
        DaryaLearningRouteState.user_id == user_id,
        DaryaLearningRouteState.thread_id == thread_id,
    ))).scalar_one_or_none()


def _uncertain_evidence(node: KnowledgeNode, text: str) -> dict[str, Any]:
    return {
        "kind": "learner_report", "outcome": "uncertain", "quality": "INSUFFICIENT_EVIDENCE",
        "observation": f"学习者提到“{node.title}”，但没有给出足以区分具体误区的解释或变体答案。",
        "source": "learner_message", "excerpt": text[:280], "supports_update": False,
        "likely_gap": f"{node.title}的具体缺口尚未确定", "confidence": 0.38,
        "basis": "当前只有目标或笼统困惑，没有可判别答案。", "understands": [],
        "alternatives": list(node.common_misconceptions[:2]), "blocking_prerequisite": None,
    }


def _observable_evidence(node: KnowledgeNode, message: str, previous: DaryaLearningLoopState | None) -> dict[str, Any]:
    text = _text(message, 1600)
    matched = [rule for rule in node.evidence_rules if rule.matches(text)]
    matched.sort(key=lambda rule: (
        0 if rule.quality == "TRANSFER_EXPLANATION" else
        1 if rule.outcome == "incorrect" else 2
    ))
    for rule in matched:
            quality = rule.quality
            if quality == "EXPLAINED_RULE" and not re.search(
                r"因为|所以|意味着|会把|判作|当作|定义位置|左边|右边|读取|初始化|不是.{0,20}而是|because|therefore",
                text,
                re.I,
            ):
                quality = "CORRECT_ASSERTION"
            return {
                "kind": rule.kind, "outcome": rule.outcome, "quality": quality,
                "observation": rule.observation, "source": "learner_message", "excerpt": text[:280],
                "supports_update": rule.outcome in {"correct", "incorrect"},
                "likely_gap": rule.likely_gap, "confidence": rule.confidence, "basis": rule.basis,
                "understands": list(rule.understands), "alternatives": list(rule.alternatives),
                "blocking_prerequisite": rule.blocking_prerequisite,
            }
    return _uncertain_evidence(node, text)


def diagnose_learning(
    message: str, evidence: Mapping[str, Any], node: KnowledgeNode | None = None,
    *, pack: DomainKnowledgePack | None = None,
    prior_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    pack = pack or (pack_for_node(node.id) if node else pack_for_node(str(evidence.get("node_id") or ""))) or DEFAULT_PACK
    node = node or pack.get_node(str(evidence.get("node_id") or pack.default_node_id))
    confidence = float(evidence.get("confidence") or 0.38)
    likely_gap = str(evidence.get("likely_gap") or f"{node.title}的具体缺口尚未确定")
    uncertain = (
        "一次正确解释不足以证明稳定掌握，仍需迁移或重复证据。" if evidence.get("outcome") == "correct"
        else "需要一个能区分候选误区的问题，不能假装已经定位。" if evidence.get("outcome") == "uncertain"
        else "误区已可观察，但仍需确认相关前置能力。"
    )
    return {
        "contract_version": "darya_learning_diagnosis_v2", "role": "DIAGNOSIS",
        "domain_id": pack.domain_id,
        "concept_key": node.id, "node_id": node.id, "node_title": node.title,
        "node_evidence_requirements": list(node.evidence_requirements),
        "observed": [{"observation": evidence.get("observation"), "source": evidence.get("source"), "excerpt": evidence.get("excerpt")}],
        "appears_to_understand": list(evidence.get("understands") or []),
        "inferred": [{"claim": likely_gap, "confidence": confidence, "basis": evidence.get("basis")}],
        "likely_gap": likely_gap, "alternatives": list(evidence.get("alternatives") or []),
        "confidence": confidence, "uncertain": uncertain,
        "evidence": {
            "kind": evidence.get("kind"), "quality": evidence.get("quality"),
            "source": evidence.get("source"), "prior_evidence_count": len(prior_evidence),
        },
        "blocking_prerequisite": evidence.get("blocking_prerequisite"),
    }


def _learning_memory(memories: Sequence[Any], pack: DomainKnowledgePack = DEFAULT_PACK) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    excluded: list[Any] = []
    for item in memories:
        row = item if isinstance(item, dict) else {"id": getattr(item, "id", None), "topic": getattr(item, "topic", None), "content": getattr(item, "content", None)}
        topic, content = str(row.get("topic") or ""), _text(row.get("content"), 300)
        relevant_context = any(term.lower() in content.lower() for term in pack.memory_context_terms)
        if topic == "technical:experience" and relevant_context:
            selected.append({"id": row.get("id"), "topic": topic, "content": content})
        elif topic in {
            "learning_preference:example_first",
            "learning_preference:rule_then_practice",
            "response_policy:plain_language_first",
        }:
            selected.append({"id": row.get("id"), "topic": topic, "content": content})
        else:
            excluded.append(row.get("id"))
    return {"selected": selected[:2], "excluded_ids": [item for item in excluded if item is not None]}


def _candidate_dict(
    node: KnowledgeNode, template: CandidateTemplate, gap: str, personalized: bool,
    pack: DomainKnowledgePack = DEFAULT_PACK,
) -> dict[str, Any]:
    return {
        "contract_version": "darya_learning_expert_v2", "role": "EXPERT_GENERATOR",
        "domain_id": pack.domain_id,
        "node_id": node.id, "node_title": node.title, "target_gap": gap,
        "kind": template.kind, "lead": template.lead, "prompt": template.prompt, "hint": template.hint,
        "answer_revealed": template.answer_revealed, "secondary_concepts": list(template.secondary_concepts),
        "assumed_prerequisites": list(template.assumed_prerequisites), "claim_keys": list(template.claim_keys),
        "external_claims": [], "personalized": personalized,
    }


def generate_expert_candidate(
    *, diagnosis: Mapping[str, Any], cycle_number: int, memory: Mapping[str, Any],
    resource_intent: Mapping[str, Any] | None = None,
    node: KnowledgeNode | None = None,
    pack: DomainKnowledgePack | None = None,
) -> dict[str, Any]:
    pack = pack or get_domain_pack(str(diagnosis.get("domain_id") or "")) or (pack_for_node(node.id) if node else None) or DEFAULT_PACK
    node = node or pack.get_node(str(diagnosis.get("node_id") or pack.default_node_id))
    evidence_kind = str((diagnosis.get("evidence") or {}).get("kind") or "")
    evidence_quality = str((diagnosis.get("evidence") or {}).get("quality") or "")
    if float(diagnosis.get("confidence") or 0.0) < 0.55:
        key = "uncertain"
    elif "TRANSFER" in evidence_quality or "EXPLAINED" in evidence_quality or evidence_kind == "learner_explanation":
        key = "correct"
    else:
        key = "incorrect"
    personalized = any(item.get("topic") == "technical:experience" for item in memory.get("selected") or [])
    if resource_intent is None:
        return _candidate_dict(node, node.candidate_templates[key], str(diagnosis.get("likely_gap") or ""), personalized, pack)
    return compose_learning_resource(
        node=node,
        intent=resource_intent,
        base_candidate=node.candidate_templates[key],
        pack=pack,
    )


def _normalize_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    for key, limit in (("target_gap", 500), ("lead", 500), ("prompt", 800), ("hint", 600), ("title", 180)):
        result[key] = _text(result.get(key), limit)
    for key, limit, count in (("secondary_concepts", 80, 6), ("assumed_prerequisites", 120, 8), ("claim_keys", 100, 10), ("external_claims", 500, 6)):
        result[key] = [_text(item, limit) for item in result.get(key) or [] if _text(item, limit)][:count]
    result["answer_revealed"] = bool(result.get("answer_revealed"))
    return result


async def review_learning_candidate(*, review_input: Mapping[str, Any], round_number: int) -> dict[str, Any]:
    candidate = _normalize_candidate(review_input.get("proposed_candidate") or {})
    diagnosis = review_input.get("diagnosis") or {}
    pack = get_domain_pack(str(candidate.get("domain_id") or diagnosis.get("domain_id") or ""))
    node_id = str(candidate.get("node_id") or diagnosis.get("node_id") or CONCEPT_KEY)
    pack = pack or pack_for_node(node_id) or DEFAULT_PACK
    node = pack.get_node(node_id if node_id in pack.nodes else pack.default_node_id)
    policy, goal = node.reviewer_policy, _text(review_input.get("learning_goal"), 300)
    learner_states = review_input.get("learner_states") or {}
    resource_type = str(candidate.get("resource_type") or "")
    resource_intent = candidate.get("resource_intent") or {}
    structure = candidate.get("structure") or {}
    issues: list[dict[str, Any]] = []
    claim_validation = validate_candidate_claims(candidate, pack=pack)

    def issue(category: str, description: str, constraint: str, *, evidence: str = "SUPPORTED") -> None:
        issues.append({
            "category": category, "severity": "high", "location": "learning_intervention",
            "description": description,
            "why_it_matters": "当前干预必须对准节点目标、证据缺口和已证明的前置能力，并保留学习者完成关键一步的空间。",
            "evidence_status": evidence, "recommended_constraint": constraint,
        })

    if candidate.get("target_gap") != _text(diagnosis.get("likely_gap"), 500):
        issue("target_gap_fit", "候选没有对准 Diagnosis 的具体缺口。", "只围绕 Diagnosis 的 likely_gap 重新生成。")
    if resource_type and resource_type not in RESOURCE_TYPES:
        issue("resource_type_integrity", "候选使用了未支持的资源类型。", "只使用已声明的四种文本学习资源类型。")
    if resource_type and resource_intent.get("resource_type") != resource_type:
        issue("resource_type_integrity", "资源内容与 Resource Intent 的类型不一致。", "按 Resource Intent 重新生成同一类型。")
    unknown_claims = [key for key in candidate.get("claim_keys") or [] if key not in policy.allowed_claim_keys]
    if unknown_claims:
        issue("factual_correctness", f"候选包含不属于本节点已接受语义的断言：{unknown_claims[0]}。", "删除错误断言，改用节点允许的稳定语义。")
    secondary = [key for key in candidate.get("secondary_concepts") or [] if key not in policy.allowed_secondary_concepts]
    if len(secondary) > policy.max_secondary_concepts:
        issue("difficulty", f"候选引入了额外概念 {secondary[0]}，超过当前节点负荷。", "移除额外概念，只保留当前节点的单一判断。")
    unmet = [key for key in candidate.get("assumed_prerequisites") or [] if str((learner_states.get(key) or {}).get("mastery_status") or "unknown") not in {"demonstrated", "mastered"}]
    if unmet:
        issue("prerequisite_mismatch", f"候选假设前置节点 {unmet[0]} 已会，但当前状态是 unknown 或未证明。", "移除未证明的前置假设，回到当前节点可回答的问题。")
    if candidate.get("answer_revealed") and resource_type in {"PRACTICE_TASK", "QUICK_CHECK"}:
        issue("gives_away_too_much", "候选在关键问题前替学习者完成了最后判断。", "不要直接给结论；改成一个保留最后判断的问题。")
    if resource_type == "WORKED_EXAMPLE" and (not candidate.get("answer_revealed") or not structure.get("completed_result") or len(structure.get("steps") or []) < 2):
        issue("resource_type_integrity", "完整例子没有展示可检查的执行过程与结果。", "补齐情境、至少两步执行过程和完整结果。")
    if resource_type == "PRACTICE_TASK" and (not structure.get("stem") or not structure.get("learner_action")):
        issue("resource_type_integrity", "练习没有形成真实的学习者作答动作。", "提供题干和学习者需要完成的判断，不给完整答案。")
    if resource_type == "EXPLANATION_CARD":
        explanation_length = sum(len(_text(value, 2000)) for value in structure.values() if isinstance(value, str))
        if not structure.get("plain_language") or not structure.get("key_distinction") or explanation_length > 900:
            issue("resource_type_integrity", "解释卡没有保持短、清楚、对准关键区分。", "保留人话规则、关键区分和一个微型例子，总长度控制在 900 字内。")
    if resource_type == "QUICK_CHECK" and (not structure.get("prompt") or len(_text(structure.get("prompt"), 1000)) > 240):
        issue("resource_type_integrity", "快速检查不够小或缺少单一问题。", "只保留一个 240 字以内的判断问题。")
    if policy.required_prompt_terms and not any(term.lower() in candidate.get("prompt", "").lower() for term in policy.required_prompt_terms):
        issue("learning_goal_alignment", "问题没有落到该节点要求的可观察判断。", f"问题至少应明确涉及：{'、'.join(policy.required_prompt_terms)}。")
    unsupported = [claim for claim in candidate.get("external_claims") or [] if _CURRENT_FACT.search(claim)]
    if unsupported:
        issue("unsupported_claim", f"候选含未核实的外部或时效性声明：{unsupported[0]}", "删除未核实声明；若必要，转交现有 Search/Evidence 契约。", evidence="UNVERIFIED")
    if not candidate.get("prompt"):
        issue("clarity", "候选缺少可执行的问题。", "加入一个与当前节点证据要求直接对应的问题。")
    if not important_claims_pass(claim_validation):
        issue("claim_validation", "候选含未通过声明级校验的事实。", "删除未支持声明，或经领域包/外部 Evidence 合同核验后再交付。", evidence="UNVERIFIED")
    verdict, categories = ("revise" if issues else "pass"), {item["category"] for item in issues}
    return {
        "contract_version": "darya_learning_review_v2", "role": "REVIEW_JUDGE",
        "domain_id": pack.domain_id, "node_id": node.id,
        "round": round_number, "verdict": verdict,
        "summary": "需要收窄后再交给学习者。" if issues else "正确、对准缺口、难度合适，可以进入学习决策。",
        "issues": issues,
        "checks": {
            "factual_correctness": "REVISE" if "factual_correctness" in categories else "PASS",
            "target_gap_fit": "REVISE" if "target_gap_fit" in categories else "PASS",
            "difficulty": "REVISE" if "difficulty" in categories else "PASS",
            "answer_leakage": "REVISE" if "gives_away_too_much" in categories else "PASS",
            "prerequisite_match": "REVISE" if "prerequisite_mismatch" in categories else "PASS",
            "unsupported_claims": "REVISE" if "unsupported_claim" in categories else "PASS",
            "learning_goal_alignment": "REVISE" if "learning_goal_alignment" in categories else "PASS",
            "resource_type_integrity": "REVISE" if "resource_type_integrity" in categories else "PASS",
        },
        "domain_evaluation_expectations": list(pack.review_expectations),
        "claim_validation": claim_validation,
    }


async def revise_expert_candidate(*, existing_candidate: Mapping[str, Any], constraints: Sequence[str], prior_review: Mapping[str, Any], round_number: int) -> dict[str, Any]:
    pack = get_domain_pack(str(existing_candidate.get("domain_id") or ""))
    node_id = str(existing_candidate.get("node_id") or CONCEPT_KEY)
    pack = pack or pack_for_node(node_id) or DEFAULT_PACK
    node = pack.get_node(node_id if node_id in pack.nodes else pack.default_node_id)
    intent = existing_candidate.get("resource_intent")
    if isinstance(intent, Mapping):
        revised = compose_learning_resource(node=node, intent=intent, base_candidate=node.revised_template, pack=pack)
    else:
        revised = _candidate_dict(node, node.revised_template, _text(existing_candidate.get("target_gap"), 500), bool(existing_candidate.get("personalized")), pack)
    revised["revision_source"] = {"review_round": round_number, "constraints_applied": list(constraints)}
    return revised


def decide_learning_intervention(
    *, diagnosis: Mapping[str, Any], review_result: Mapping[str, Any],
    state_before: Mapping[str, Any], state_after: Mapping[str, Any],
    evidence: Mapping[str, Any], perspective: Mapping[str, Any],
    due_review: Mapping[str, Any] | None = None,
    learner_message: str = "",
) -> dict[str, Any]:
    confidence = float(diagnosis.get("confidence") or 0.0)
    review_passed = str(review_result.get("status")) == "passed"
    if not review_passed:
        action, scaffold, reason = "PAUSE", "L0", "审阅没有通过，不能把未收紧的干预交给学习者。"
    elif int((due_review or {}).get("due_count") or 0) > 0:
        # 到期复习优先于讲新东西。间隔重复的效果全部来自"在快忘记的时候取回来"，
        # 而这条信号以前根本没进过决策 —— 卡片在另一个页面上按自己的时钟走。
        from services.review_schedule import review_reason

        action, scaffold = "PRACTICE", "L1"
        reason = review_reason(due_review or {}) or "有到期卡片，先做提取练习。"
    elif confidence < 0.55:
        action, scaffold, reason = "ASK", "L2", "诊断证据不足，先问一个能区分候选误区的问题。"
    elif evidence.get("outcome") == "correct":
        action, scaffold, reason = "PRACTICE", "L1", "已经出现解释证据，但单次或未重复证据不足以确认掌握；减少帮助并做变体迁移。"
    elif evidence.get("outcome") == "incorrect" and int(state_before.get("cycle_count") or 0) >= 2 and state_before.get("support_level") == "L3":
        action, scaffold, reason = "DEMONSTRATE", "L4", "结构化步骤后同一误区仍重复，用不泄露原题答案的文本类比再建立区分。"
    elif evidence.get("outcome") == "incorrect" and int(state_before.get("cycle_count") or 0) >= 1:
        action, scaffold, reason = "EXPLAIN", "L3", "同一误区重复出现，需要结构化步骤，但仍不替学习者完成最后判断。"
    else:
        action, scaffold, reason = "ASK", "L2", "具体误区已经可定位，用关键问题保留学习者完成核心推断的机会。"
    requested_form = requested_teaching_form(learner_message)
    if review_passed and requested_form:
        action, scaffold = {
            "WORKED_EXAMPLE": ("DEMONSTRATE", "L4"),
            "EXPLANATION_CARD": ("EXPLAIN", "L3"),
            "PRACTICE_TASK": ("PRACTICE", "L1"),
            "QUICK_CHECK": ("ASK", "L2"),
        }[requested_form]
        reason = "按你明确提出的讲法切换；是否掌握仍由后续独立作答决定。"
    return {
        "contract_version": "darya_learning_decision_v2", "role": "LEARNING_DECISION",
        "consumed": {
            "node_id": diagnosis.get("node_id"), "diagnosis_confidence": confidence,
            "diagnosed_gap": diagnosis.get("likely_gap"), "review_status": review_result.get("status"),
            "review_final_verdict": (review_result.get("reviews") or [{}])[-1].get("verdict"),
            "learner_state_revision": state_after.get("revision"),
            "learner_history_cycles": state_before.get("cycle_count"),
            "perspective_action": perspective.get("action"),
            "perspective_goal": (perspective.get("current_goal") or {}).get("value") if isinstance(perspective.get("current_goal"), dict) else None,
            "due_review_count": int((due_review or {}).get("due_count") or 0),
            "due_review_node": (due_review or {}).get("node_ref"),
        },
        "action": action, "scaffold_level": scaffold, "scaffold_policy": scaffold_policy_contract(),
        "l5_eligible": False, "l5_guardrail": "当前是学习模式，不是明确的任务完成模式；禁止部分代做。",
        "public_reason": reason, "enough_evidence_to_update": bool(evidence.get("supports_update")),
        "search_required": False,
        "demonstration": {"selected": scaffold == "L4", "blackboard_available": False, "reason": "当前 Tide 对话没有可调用的 Blackboard 合同；L4 仅使用文本类比。"},
    }


_logger = logging.getLogger("rjianb.learning_loop")


async def _compose_companion_response(
    *, message: str, diagnosis: Mapping[str, Any], decision: Mapping[str, Any],
    candidate: Mapping[str, Any], response_llm: Any, work_brief: Mapping[str, Any],
    template_body: str,
) -> str | None:
    """Foreground-model voice for the learning turn.

    The deterministic loop owns diagnosis, scaffold policy, and evidence; the
    model composes only the words, grounded in the learner's actual message.
    Returns None on any failure so the template stays the fallback.
    """
    try:
        prompt = (
            "你是 DARYA 本人在和学习者说话。给你：学习者这轮的原话、诊断骨架、本轮决策、准备的学习材料。\n"
            "要求：\n"
            "1. 直接针对学习者这轮的原话回应；如果学习者带来了自己的计算、答案或问题，必须先正面判断它对不对、错在哪一步，不许忽略。\n"
            "2. 遵守决策给出的支架层级：不替学习者完成最后一步判断；可以提示、追问、给例子，但不能把完整答案和盘托出。\n"
            "3. 不得声称学习者做过没有做过的事（没有提交过答案的题就是还没算）。\n"
            "4. 不出现「根据我的记忆/根据记录/系统显示」这类机械措辞；像正常人说话，不超过5句话。\n"
            "5. 材料里准备的练习/讲解会在界面右侧出现，你不需要复述它，只需要把话说到位。\n"
            # 2026-09-22 之前的形状：模型看到 work_brief.unknowns 里写着「用户倒着删时
            # 具体写的是什么代码…决定是索引越界、条件写错、还是删了别的对象」，
            # 它**知道**缺这个，但 prompt 没要求它先问一句，于是直接上了 7 张黑板。
            # 修法不是塞一句「该先问」，而是把判定条件写出来——unknowns 里出现的是
            # **学习者自己手里的内容**（代码、具体题、卡在哪一步），不是系统历史/偏好/
            # 环境。先问一句比开讲更对。
            "6. 看工作简报里的 `unknowns`：如果那里出现的是学习者自己的具体内容——"
            "他们的代码、他们要回答的题、他们卡在哪一步——这一类**不问你没法诊断的东西**，"
            "**应当先问**那个未知项，再决定要不要上黑板/讲套路/出练习。"
            "如果是系统历史/偏好/环境类的未知（这条不该讲、那个资源用过、用户已确认…），"
            "那是上下文层面的，不是这一轮的卡点，**不要**因为它们停下来问。\n"
            "只输出要说的话，不要任何标记或解释。\n"
            "学习者原话：" + str(message or "")[:500] + "\n"
            "诊断骨架：" + json.dumps({
                "confidence": diagnosis.get("confidence"), "likely_gap": diagnosis.get("likely_gap"),
                "node": str((candidate or {}).get("node_title") or ""),
            }, ensure_ascii=False) + "\n"
            "本轮决策：" + json.dumps({
                "action": decision.get("action"), "scaffold_level": decision.get("scaffold_level"),
                "reason": decision.get("public_reason"),
            }, ensure_ascii=False) + "\n"
            "工作简报：" + json.dumps(work_brief or {}, ensure_ascii=False)[:600] + "\n"
            "模板兜底（可参考其语气，不要照抄）：" + template_body[:300]
        )
        # 这条路自己拼提示、自己调模型，绕过了 chat_quick 的 PromptLedger，所以
        # 「模型实际收到了什么」以前在这里看不到。2026-09-22 实测：连续三轮真实
        # /darya 回合没有转储，走这条路的那些根本没有提示可查——而这条路的提示
        # 里含学习者原话、诊断骨架和本轮决策，恰恰是最需要能回看的那种。
        # 转储本身是 opt-in（DARYA_PROMPT_DUMP），默认不落盘。
        dump_prompt(
            prompt,
            request_id="learning-loop-" + hashlib.sha1(
                str(message or "").encode("utf-8")
            ).hexdigest()[:16],
            extra="[turn] " + json.dumps(
                {
                    "path": "darya_learning_loop._compose_companion_response",
                    "message": str(message or "")[:200],
                },
                ensure_ascii=False,
            ),
        )
        raw = await asyncio.wait_for(response_llm.chat(
            messages=[{"role": "user", "content": prompt}], temperature=0.3,
            max_tokens=500, use_cache=False, write_cache=False,
        ), timeout=30)
        content = raw.get("content") if isinstance(raw, Mapping) else raw
        text = str(content or "").strip().strip('"')
        if 8 <= len(text) <= 900 and "correct_index" not in text:
            return text
    except Exception as exc:
        _logger.warning("companion response composition failed: %s", type(exc).__name__)
    return None


def companion_intervention(
    *, candidate: Mapping[str, Any], decision: Mapping[str, Any], diagnosis: Mapping[str, Any],
    memory: Mapping[str, Any], evidence: Mapping[str, Any], node: KnowledgeNode | None = None,
    route_plan: Mapping[str, Any] | None = None,
    visual_fit: Mapping[str, Any] | None = None,
    pack: DomainKnowledgePack | None = None,
) -> dict[str, Any]:
    pack = pack or get_domain_pack(str(diagnosis.get("domain_id") or "")) or (pack_for_node(node.id) if node else None) or DEFAULT_PACK
    node = node or pack.get_node(str(diagnosis.get("node_id") or pack.default_node_id))
    scaffold = str(decision.get("scaffold_level") or "L0")
    technical = next((item for item in memory.get("selected") or [] if item.get("topic") == "technical:experience"), None)
    personal = node.memory_analogy if technical else "先只看当前这一格，不把相邻概念一起压过来。"
    route_prefix = ""
    if route_plan and route_plan.get("movement") in {"backward", "forward"}:
        route_prefix = f"{route_plan.get('reason')}\n\n"
    resource_type = str(candidate.get("resource_type") or "")
    if resource_type == "EXPLANATION_CARD":
        presentation_mode = str(candidate.get("presentation_mode") or "plain")
        if presentation_mode == "example_first":
            body = "先不堆定义。右侧从一个具体情境开始，再把它收回关键规则。"
        elif presentation_mode == "rule_then_practice":
            body = "先只给一条短规则，然后马上用一道小题检验，不把解释拉长。"
        else:
            body = f"先补清一个关键区分。{personal}\n\n右侧是短解释，读完后只回答最后那一问。"
    elif resource_type == "PRACTICE_TASK":
        practice_prompt = str((candidate.get("structure") or {}).get("stem") or candidate.get("prompt") or "")
        body = f"这次你已经不是在猜了，我把提示撤掉一点。直接做右侧这个变体，先给判断，再说依据。\n\n{practice_prompt}"
    elif resource_type == "WORKED_EXAMPLE":
        body = "抽象规则还没有落到执行过程。先看右侧完整例子，再把最后一问换回你自己的判断。"
    elif resource_type == "QUICK_CHECK":
        quick_prompt = str((candidate.get("structure") or {}).get("prompt") or candidate.get("prompt") or "")
        body = (
            f"我现在还不能确定具体卡点，所以先不装作已经诊断出来。{personal}\n\n{quick_prompt}"
            if float(diagnosis.get("confidence") or 0.0) < 0.55
            else f"只做一个很小的检查。你不用展开长答案，一句判断加一句理由就够。\n\n{quick_prompt}"
        )
    elif float(diagnosis.get("confidence") or 0.0) < 0.55:
        analogy = f"\n\n{personal}" if technical else ""
        body = f"我现在还不能确定你卡在“{node.title}”的哪一种情况，所以先不装作已经诊断出来。{analogy}\n\n{candidate.get('prompt')} {candidate.get('hint')}"
    elif scaffold == "L1":
        body = f"这次你已经不是在猜了——你把“{node.title}”里的关键规则说清楚了。我先把提示撤掉一点。\n\n{candidate.get('prompt')} {candidate.get('hint')}"
    elif scaffold == "L3":
        body = f"同一处又卡住了，我们把判断拆成两步：先确认当前节点规则，再把它带回变体。\n\n现在由你完成最后一步：{candidate.get('prompt')}"
    elif scaffold == "L4":
        body = f"换一个不直接替你解原题的文本类比：{personal}\n\n把这个类比带回题目，再判断：{candidate.get('prompt')}"
    elif scaffold == "L0":
        body = "这版干预还没有通过审阅，我先停在这里，不把不可靠的内容交给你。"
    else:
        body = f"{candidate.get('lead')} {personal}\n\n{candidate.get('prompt')}"
        if candidate.get("hint"):
            body += f" {candidate.get('hint')}"
    sidecar = ""
    if resource_type in RESOURCE_TYPES and scaffold != "L0":
        fit_payload = ""
        if visual_fit:
            fit_payload = f"[darya_learning_fit]{json.dumps(dict(visual_fit), ensure_ascii=False)}[/darya_learning_fit]\n"
        sidecar = (
            f"\n[tide_sidecar type=\"learning-fit\" title=\"这一轮为什么这样学\"]\n"
            f"{fit_payload}{render_resource_markdown(candidate, include_fit=False)}\n[/tide_sidecar]"
        )
    return {
        "contract_version": "darya_learning_companion_v2", "role": "COMPANION",
        "domain_id": pack.domain_id, "node_id": node.id,
        "follows_scaffold_level": scaffold, "tone": ["calm", "human", "clear", "experienced"],
        "user_response": f"[tide_center]\n{route_prefix}{body.strip()}\n[/tide_center]{sidecar}",
    }


def _projected_status(row: DaryaLearningLoopState | None, evidence: Mapping[str, Any]) -> str:
    rows = list(row.evidence or []) if row else []
    if evidence.get("supports_update"):
        rows.append(dict(evidence))
    return _mastery_status(rows)


def _apply_evidence_update(
    row: DaryaLearningLoopState | None, evidence: Mapping[str, Any], node: KnowledgeNode,
) -> tuple[dict[str, Any], dict[str, Any]]:
    before = _public_state(row, node)
    old = float(row.mastery_estimate if row is not None else 0.2)
    outcome, quality = evidence.get("outcome"), evidence.get("quality")
    if outcome == "correct" and quality == "TRANSFER_EXPLANATION":
        delta, reason = 0.25, "正确解释迁移到了变体；仍需重复证据才可能完成掌握。"
    elif outcome == "correct" and quality == "EXPLAINED_RULE":
        delta, reason = 0.23, "正确答案包含规则解释；单次证据只做保守上调，不标记掌握完成。"
    elif outcome == "correct":
        delta, reason = 0.10, "只有正确断言，缺少解释证据，因此仅小幅上调。"
    elif outcome == "incorrect":
        delta, reason = (-0.02 if row is not None else 0.0), "明确错误确认当前误区，但不据此建立永久个人记忆。"
    else:
        delta, reason = 0.0, "当前没有足够可观察证据更新掌握状态。"
    new = max(0.05, min(0.9, old + delta))
    status_after = _projected_status(row, evidence)
    update = {
        "applied": bool(evidence.get("supports_update")),
        "from": round(old, 2) if before.get("mastery_status") != "unknown" else None,
        "to": round(new, 2) if status_after != "unknown" else None,
        "delta": round(new - old, 2), "status_from": before.get("mastery_status"), "status_to": status_after,
        "reason": reason, "evidence_kind": evidence.get("kind"), "evidence_quality": quality,
        "mastery_complete": status_after == "mastered",
    }
    after = dict(before)
    after.update({
        "mastery_status": status_after, "mastery_estimate": round(new, 2) if status_after != "unknown" else None,
        "current_evidence_count": int(before.get("current_evidence_count") or 0) + (1 if evidence.get("supports_update") else 0),
        "last_observation": evidence.get("observation"), "revision": int(before.get("revision") or 0) + 1,
    })
    return update, after


def _state_map(
    rows: Sequence[DaryaLearningLoopState], pack: DomainKnowledgePack = DEFAULT_PACK,
) -> dict[str, dict[str, Any]]:
    result = {node_id: _public_state(None, node, pack) for node_id, node in pack.nodes.items()}
    for row in rows:
        if row.concept_key in pack.nodes:
            result[row.concept_key] = _public_state(row, pack.get_node(row.concept_key), pack)
    return result


async def _decision_explanation(db: AsyncSession, *, user_id: int, thread_id: str, route_row: DaryaLearningRouteState) -> dict[str, Any]:
    pack = get_domain_pack(route_row.domain_id) or DEFAULT_PACK
    node_id = route_row.current_node_id if route_row.current_node_id in pack.nodes else pack.default_node_id
    row = await get_learning_state(db, user_id=user_id, thread_id=thread_id, concept_key=node_id)
    prior_decision = (row.latest_cycle or {}).get("roles", {}).get("decision", {}) if row else {}
    explanation = (
        "因为你已经抓到关键点了，我现在直接告诉你，反而会把最后一步替你做掉。"
        if prior_decision.get("scaffold_level") in {"L0", "L1", "L2"}
        else "我在保留最后一个判断给你完成；如果同一处继续卡住，我会再把步骤拆开。"
    )
    state = _public_state(row, pack.get_node(node_id), pack)
    plan = public_route(route_row.latest_plan or {}, pack=pack)
    return {
        "contract_version": CONTRACT_VERSION, "kind": "decision_explanation",
        "domain_id": pack.domain_id, "concept_key": node_id,
        "concept": pack.get_node(node_id).title, "cycle": int(state.get("cycle_count") or 0),
        "route": {"before": plan, "after": plan},
        "learner_state": {"before": state, "update": {"applied": False}, "after": state},
        "roles": {"decision": prior_decision, "companion": {"role": "COMPANION", "follows_scaffold_level": prior_decision.get("scaffold_level"), "user_response": f"[tide_center]\n{explanation}\n[/tide_center]"}},
        "search_invoked": False,
    }


async def run_learning_turn(
    db: AsyncSession, *, user_id: int, thread_id: str, message: str,
    message_id: str, perspective: Mapping[str, Any], relevant_memories: Sequence[Any] = (),
    response_llm: Any = None, work_brief: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run one generic node kernel, update node evidence, then re-plan route."""
    route_row = await get_learning_route_state(db, user_id=user_id, thread_id=thread_id)
    perspective_goal = (
        (perspective.get("current_goal") or {}).get("value")
        if isinstance(perspective.get("current_goal"), Mapping) else ""
    )
    resolution = resolve_learning_domain(
        message=message,
        current_domain_id=route_row.domain_id if route_row else None,
        learner_goal=str(perspective_goal or ""),
        workspace_context=str(perspective.get("workspace_context") or ""),
    )
    pack = resolution.pack
    if pack is None:
        return None
    if route_row is not None and _WITHHOLDING_QUESTION.search(message):
        return await _decision_explanation(db, user_id=user_id, thread_id=thread_id, route_row=route_row)

    is_new_route = route_row is None
    learner_goal = route_row.learner_goal if route_row else _text(message, 500)
    # The explicit learner message selects the graph target.  Perspective helps
    # resolve the domain, but must not silently override the user's node cue.
    target_node_id = route_row.target_node_id if route_row else pack.select_goal_node(message)
    selected_node_id = route_row.current_node_id if route_row else target_node_id
    selected_node = pack.get_node(selected_node_id)
    route_before_raw = copy.deepcopy(route_row.latest_plan) if route_row and route_row.latest_plan else initial_route_plan(target_node_id=target_node_id, learner_goal=learner_goal, pack=pack)

    assessment_row = await get_learning_state(db, user_id=user_id, thread_id=thread_id, concept_key=selected_node_id)
    assessment_evidence = _observable_evidence(selected_node, message, assessment_row)
    assessment_evidence["node_id"] = selected_node_id
    assessment_diagnosis = diagnose_learning(
        message, assessment_evidence, selected_node, pack=pack,
        prior_evidence=list(assessment_row.evidence or []) if assessment_row else (),
    )

    intervention_node_id = selected_node_id
    blocking = assessment_diagnosis.get("blocking_prerequisite")
    if route_row is not None and blocking and blocking in pack.nodes:
        blocking_row = await get_learning_state(
            db, user_id=user_id, thread_id=thread_id, concept_key=str(blocking),
        )
        blocking_state = _public_state(blocking_row, pack.get_node(str(blocking)), pack)
        if not is_ready(blocking_state):
            intervention_node_id = str(blocking)
    node = pack.get_node(intervention_node_id)
    row = await get_learning_state(db, user_id=user_id, thread_id=thread_id, concept_key=intervention_node_id)
    if intervention_node_id != selected_node_id:
        evidence = _observable_evidence(node, message, row)
        evidence["node_id"] = intervention_node_id
        diagnosis = diagnose_learning(
            message, evidence, node, pack=pack,
            prior_evidence=list(row.evidence or []) if row else (),
        )
        diagnosis["prerequisite_discovery"] = {
            "assessed_node": selected_node_id,
            "blocking_prerequisite": intervention_node_id,
            "assessment_gap": assessment_diagnosis.get("likely_gap"),
            "assessment_confidence": assessment_diagnosis.get("confidence"),
        }
    else:
        evidence, diagnosis = assessment_evidence, assessment_diagnosis

    state_before = _public_state(row, node, pack)
    update, state_after = _apply_evidence_update(row, evidence, node)
    all_rows = await get_learning_states(db, user_id=user_id, thread_id=thread_id)
    node_states = _state_map(all_rows, pack)
    node_states[intervention_node_id] = state_after
    memory = _learning_memory(relevant_memories, pack)
    cycle_number = int(state_before.get("cycle_count") or 0) + 1
    state_after["cycle_count"] = cycle_number
    history_with_feedback, previous_resource = apply_previous_resource_result(
        list(row.change_history or []) if row else [],
        evidence=evidence,
        learner_message=message,
        diagnosed_gap=str(diagnosis.get("likely_gap") or ""),
    )

    provisional_decision = decide_learning_intervention(
        diagnosis=diagnosis,
        review_result={"status": "passed", "reviews": [{"verdict": "pass"}]},
        state_before=state_before,
        state_after=state_after,
        evidence=evidence,
        perspective=perspective,
        learner_message=message,
    )
    state_after["support_level"] = provisional_decision["scaffold_level"]
    state_after["diagnosis_confidence"] = diagnosis["confidence"]
    state_after["current_misconception"] = diagnosis["likely_gap"] if evidence.get("outcome") != "correct" else None
    node_states[intervention_node_id] = state_after

    if intervention_node_id != selected_node_id:
        route_after_raw = decide_route(
            learner_goal=learner_goal, target_node_id=target_node_id,
            current_node_id=selected_node_id, node_states=node_states,
            diagnosis=assessment_diagnosis, evidence=assessment_evidence,
            perspective=perspective, allow_prerequisite_backtrack=True,
            pack=pack,
        )
    else:
        route_after_raw = decide_route(
            learner_goal=learner_goal, target_node_id=target_node_id,
            current_node_id=intervention_node_id, node_states=node_states,
            diagnosis=diagnosis, evidence=evidence, perspective=perspective,
            allow_prerequisite_backtrack=not is_new_route,
            pack=pack,
        )
    route_before = public_route(route_before_raw, pack=pack)
    route_after = public_route(route_after_raw, pack=pack)
    resource_intent = decide_resource_intent(
        node=node,
        diagnosis=diagnosis,
        evidence=evidence,
        scaffold_level=provisional_decision["scaffold_level"],
        route_plan=route_after,
        resource_history=public_resource_history(history_with_feedback),
        perspective=perspective,
        memory=memory,
        learner_message=message,
    )
    initial = generate_expert_candidate(
        diagnosis=diagnosis,
        cycle_number=cycle_number,
        memory=memory,
        resource_intent=resource_intent,
        node=node,
        pack=pack,
    )
    quality = await run_artifact_quality_gate(
        source_candidate=diagnosis,
        initial_candidate=initial,
        review_context={
            "diagnosis": diagnosis, "learning_goal": node.learning_goal,
            "perspective": {"action": perspective.get("action"), "recommended_next_move": perspective.get("recommended_next_move")},
            "learner_state": state_after, "learner_states": node_states,
        },
        revise_candidate=revise_expert_candidate,
        normalize_candidate=_normalize_candidate,
        candidate_changed=lambda before, after: json.dumps(before, ensure_ascii=False, sort_keys=True) != json.dumps(after, ensure_ascii=False, sort_keys=True),
        reviewer=review_learning_candidate,
        max_review_rounds=2,
    )
    final_candidate = quality.get("candidate") or initial
    # 到期复习是这一轮决策的一个输入：它只报告"什么该回来了"，不写掌握度、不造证据。
    try:
        from services.review_schedule import due_review_signal

        due_review = await due_review_signal(db, user_id=user_id, node_ref=str(node.id))
    except Exception:  # noqa: BLE001 - 调度视图取不到不该拦住这一轮教学
        due_review = {"due_count": 0}
    decision = decide_learning_intervention(
        diagnosis=diagnosis, review_result=quality, state_before=state_before,
        state_after=state_after, evidence=evidence, perspective=perspective,
        due_review=due_review,
        learner_message=message,
    )
    state_after["support_level"] = decision["scaffold_level"]
    state_after["diagnosis_confidence"] = diagnosis["confidence"]
    state_after["current_misconception"] = diagnosis["likely_gap"] if evidence.get("outcome") != "correct" else None
    node_states[intervention_node_id] = state_after
    review_public = {
        "contract_version": "darya_learning_review_flow_v2", "role": "REVIEW_JUDGE",
        "domain_id": pack.domain_id, "node_id": node.id,
        "status": quality.get("status"), "verdicts": [item.get("verdict") for item in quality.get("reviews") or []],
        "reviews": quality.get("reviews") or [], "revision_rounds": quality.get("revision_rounds"),
        "materially_changed": bool(quality.get("materially_changed")),
        "initial_candidate": quality.get("original_candidate"), "final_candidate": final_candidate,
    }
    final_claim_validation = validate_candidate_claims(final_candidate, pack=pack)
    learner_profile = build_learner_profile_projection(
        pack=pack, learner_goal=learner_goal, message=message,
        node={"id": node.id, "title": node.title}, diagnosis=diagnosis,
        evidence=evidence, state_after=state_after, route_after=route_after,
        resource_intent=resource_intent, previous_resource=previous_resource,
        memory=memory, correction_detected=is_fit_correction(message),
    )
    visual_fit = build_visual_fit_projection(
        profile=learner_profile, pack=pack, route_before=route_before,
        route_after=route_after, learner_state={"before": state_before, "update": update, "after": state_after},
        resource_intent=resource_intent, evidence=evidence,
        previous_resource=previous_resource,
    )
    companion = companion_intervention(
        candidate=final_candidate, decision=decision, diagnosis=diagnosis,
        memory=memory, evidence=evidence, node=node, route_plan=route_after,
        visual_fit=visual_fit, pack=pack,
    )
    if response_llm is not None:
        composed = await _compose_companion_response(
            message=message, diagnosis=diagnosis, decision=decision,
            candidate=final_candidate, response_llm=response_llm,
            work_brief=work_brief or {},
            template_body=str(companion.get("user_response") or ""),
        )
        if composed:
            companion = {**companion, "user_response": composed, "composed_by": "foreground_model"}
    competition_trace = build_competition_trace(
        diagnosis=diagnosis, initial_candidate=initial,
        final_candidate=final_candidate, review=review_public,
        decision=decision, route_after=route_after,
        learner_state={"before": state_before, "update": update, "after": state_after},
        claim_validation=final_claim_validation,
    )
    result = {
        "contract_version": CONTRACT_VERSION, "kind": "learning_cycle",
        "domain_id": pack.domain_id, "domain": {"id": pack.domain_id, "title": pack.title},
        "domain_resolution": resolution.public_dict(),
        "concept_key": node.id, "concept": node.title, "cycle": cycle_number, "source_message_id": message_id,
        "route": {"before": route_before, "after": route_after},
        "roles": {
            "diagnosis": diagnosis,
            "expert": {
                "role": "EXPERT_GENERATOR", "domain_id": pack.domain_id, "node_id": node.id,
                "consumed_diagnosed_gap": diagnosis.get("likely_gap"),
                "consumed_resource_intent": resource_intent,
                "initial_candidate": initial, "final_candidate": final_candidate,
            },
            "review": review_public, "decision": decision, "companion": companion,
        },
        "learner_state": {"before": state_before, "update": update, "after": state_after},
        "node_states": node_states,
        "perspective_input": {
            "contract_version": perspective.get("contract_version"), "action": perspective.get("action"),
            "recommended_next_move": perspective.get("recommended_next_move"),
            "selected_context_counts": perspective.get("selected_context_counts"),
            "route_priority_applied": bool(route_after_raw.get("evidence_overrode_perspective") or perspective.get("current_goal")),
            "evidence_remained_authoritative": True,
        },
        "memory_selection": {**memory, "used_for_mastery": False},
        "resource_orchestration": {
            "contract_version": "darya_personalized_resource_orchestration_v1",
            "intent": resource_intent,
            "resource": final_candidate,
            "fit": resource_intent.get("fit"),
            "previous_resource_result": previous_resource,
            "history_scope": "THREAD_NODE_LEARNING_STATE",
            "saved_to_long_term_memory": False,
            "route_authority": "ADAPTIVE_ROUTE",
            "scaffold_authority": "L0_L5_SCAFFOLD_POLICY",
            "resource_authority": "RESOURCE_DECISION_POLICY",
        },
        "learner_profile": learner_profile,
        "visual_fit_report": visual_fit,
        "claim_validation": {
            "contract_version": "darya_claim_validation_v1",
            "initial": [item.get("claim_validation") for item in quality.get("reviews") or []],
            "final": final_claim_validation,
            "all_important_claims_pass": important_claims_pass(final_claim_validation),
        },
        "competition_trace": competition_trace,
        "privacy_state_boundaries": privacy_state_boundary_contract(),
        "capability_use": {
            "search_invoked": False,
            "search_reason": f"该节点来自已安装的稳定领域包“{pack.title}”，本轮没有时效性或外部事实声明。",
            "blackboard_available": False, "blackboard_invoked": False,
        },
        "created_at": _now(),
    }
    result["competition_evidence_view"] = build_competition_evidence_view(cycle=result, pack=pack)

    if row is None:
        row = DaryaLearningLoopState(user_id=user_id, thread_id=thread_id, concept_key=node.id, evidence=[], change_history=[])
        db.add(row)
    evidence_rows = list(row.evidence or [])
    evidence_rows.append({**dict(evidence), "message_id": message_id, "observed_at": _now()})
    history = history_with_feedback
    current_resource_record = resource_record(final_candidate)
    history.append({
        "cycle": cycle_number, "message_id": message_id,
        "mastery_from": update["from"], "mastery_to": update["to"],
        "status_from": update["status_from"], "status_to": update["status_to"],
        "support_from": state_before.get("support_level"), "support_to": decision["scaffold_level"],
        "evidence_quality": evidence.get("quality"), "recorded_at": _now(),
        "resource": current_resource_record,
    })
    row.mastery_estimate = float(update["to"] if update["to"] is not None else (row.mastery_estimate or 0.2))
    row.current_misconception = state_after["current_misconception"]
    row.diagnosis_confidence = diagnosis["confidence"]
    row.support_level = decision["scaffold_level"]
    row.last_observation = str(evidence.get("observation") or "")
    row.evidence = evidence_rows[-30:]
    row.change_history = history[-30:]
    row.cycle_count = cycle_number
    row.revision = int(row.revision or 0) + 1

    if route_row is None:
        route_row = DaryaLearningRouteState(
            user_id=user_id, thread_id=thread_id, domain_id=pack.domain_id,
            learner_goal=learner_goal, target_node_id=target_node_id,
            current_node_id=str(route_after_raw["next_node"]), latest_plan={}, route_history=[],
        )
        db.add(route_row)
    route_history = list(route_row.route_history or [])
    route_history.append({
        "message_id": message_id, "before": route_before, "after": route_after,
        "evidence_node": node.id, "evidence_quality": evidence.get("quality"), "recorded_at": _now(),
    })
    route_row.current_node_id = str(route_after_raw["next_node"])
    route_row.latest_plan = copy.deepcopy(route_after_raw)
    route_row.route_history = route_history[-30:]
    route_row.revision = int(route_row.revision or 0) + 1
    await db.flush()
    state_after["revision"] = row.revision
    result["learner_state"]["after"]["revision"] = row.revision
    result["route"]["revision"] = route_row.revision
    result["node_states"][node.id] = copy.deepcopy(state_after)
    result["resource_orchestration"]["history"] = public_resource_history(history)
    row.latest_cycle = copy.deepcopy(result)
    return result


def public_latest_learning_state(
    row: DaryaLearningLoopState | None, *, thread_id: str,
    route_row: DaryaLearningRouteState | None = None,
    rows: Sequence[DaryaLearningLoopState] | None = None,
) -> dict[str, Any]:
    all_rows = list(rows or ([row] if row else []))
    pack = (
        get_domain_pack(route_row.domain_id) if route_row else
        pack_for_node(row.concept_key) if row else None
    ) or DEFAULT_PACK
    current_node_id = (
        route_row.current_node_id if route_row and route_row.current_node_id in pack.nodes
        else row.concept_key if row and row.concept_key in pack.nodes
        else pack.default_node_id
    )
    fallback_row = row if row and row.concept_key == current_node_id else None
    current_row = next((item for item in all_rows if item.concept_key == current_node_id), fallback_row)
    latest_cycle = copy.deepcopy(current_row.latest_cycle or {}) if current_row else {}
    resource_history_by_node = {
        item.concept_key: public_resource_history(item.change_history or [])
        for item in all_rows if item.concept_key in pack.nodes
    }
    resource_history_all = [
        resource
        for node_id in pack.nodes
        for resource in resource_history_by_node.get(node_id, [])
    ]
    return {
        "thread_id": thread_id,
        "domain": {"id": pack.domain_id, "title": pack.title},
        "graph": pack.graph_contract(),
        "route": {
            "learner_goal": route_row.learner_goal, "target_node_id": route_row.target_node_id,
            "current_node_id": route_row.current_node_id,
            "latest_plan": public_route(route_row.latest_plan or {}, pack=pack),
            "history": copy.deepcopy(route_row.route_history or []), "revision": route_row.revision,
        } if route_row else None,
        "state": _public_state(current_row, pack.get_node(current_node_id), pack),
        "node_states": _state_map(all_rows, pack),
        "latest_cycle": latest_cycle,
        "learner_profile": copy.deepcopy(latest_cycle.get("learner_profile") or {}),
        "visual_fit_report": copy.deepcopy(latest_cycle.get("visual_fit_report") or {}),
        "competition_evidence_view": copy.deepcopy(latest_cycle.get("competition_evidence_view") or {}),
        "privacy_state_boundaries": copy.deepcopy(latest_cycle.get("privacy_state_boundaries") or privacy_state_boundary_contract()),
        "change_history": copy.deepcopy(current_row.change_history or []) if current_row else [],
        "resource_history": public_resource_history(current_row.change_history or []) if current_row else [],
        "resource_history_by_node": resource_history_by_node,
        "resource_history_all": resource_history_all,
        "resource_history_scope": "THREAD_NODE_LEARNING_STATE",
        "resource_history_saved_to_long_term_memory": False,
        "updated_at": route_row.updated_at.isoformat() if route_row and route_row.updated_at else (current_row.updated_at.isoformat() if current_row and current_row.updated_at else None),
    }
