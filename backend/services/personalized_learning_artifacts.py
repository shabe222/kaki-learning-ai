"""PC02C causal personalized Artifact planning on accepted authorities only."""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping
from services.darya_domain_registry import get_domain_pack

logger = logging.getLogger(__name__)


def _block(block_id: str, kind: str, text: str, node: str, refs: list[str], **data: Any) -> dict[str, Any]:
    return {"block_id": block_id, "type": kind, "data": {"text": text, **data}, "knowledge_refs": [node], "source_refs": refs}


def learner_input_projection(*, state: Mapping[str, Any], route: Mapping[str, Any], preference: str | None) -> dict[str, Any]:
    return {"knowledge_node": state.get("concept_key"), "support_level": state.get("support_level"),
            "latest_valid_evaluation": {"observation": state.get("last_observation"), "confidence": state.get("diagnosis_confidence")},
            "current_learning_goal": route.get("learner_goal"), "previous_relevant_learning_evidence": list(state.get("evidence") or [])[-2:],
            "previous_resource_history": list(state.get("change_history") or [])[-2:], "confirmed_presentation_preference": preference}


def _node_claim_refs(pack, node) -> list[str]:
    """该节点自己的可引用来源。与 LLM 路径使用同一套推导，避免两条路径不一致。"""
    claim_keys = tuple(getattr(node.revised_template, "claim_keys", ()) or ()) or tuple(
        getattr(node.reviewer_policy, "allowed_claim_keys", ()) or ()
    )
    return sorted({ref for key in claim_keys for ref in pack.claim_source_map.get(key, ())})


def plan_personalized_artifact(*, experience: str, state: Mapping[str, Any], route: Mapping[str, Any], preference: str | None = None) -> dict[str, Any]:
    """确定性的因果计划器（同时是 LLM 不可用时的兜底）。

    决策部分（支架层级、顺序依据、偏好处理）是既有的个人化真值，保持不变。
    内容部分此前**整段写死在 Python 闭包 / nonlocal 上**：任何节点、任何学科
    走这条路都会产出一份讲 nonlocal 的讲义，标题却挂着别的知识点。
    现在内容全部取自该节点自己的领域包事实与资源种子；领域包没给到的部分
    由通用教学措辞补齐，不再冒充某个具体知识点。
    """
    pack = get_domain_pack(str(route.get("domain_id") or ""))
    if pack is None: raise ValueError("installed domain pack is required")
    node_id = str(state.get("concept_key") or route.get("current_node_id") or "")
    node = pack.get_node(node_id)
    seed = pack.resource_seeds.get(node_id)
    refs = _node_claim_refs(pack, node)
    support = str(state.get("support_level") or "L2"); evidence = list(state.get("evidence") or []); outcome = str((evidence[-1] if evidence else {}).get("outcome") or "uncertain")
    high = support in {"L3", "L4", "L5"} or outcome == "incorrect"
    goal = str(route.get("learner_goal") or node.learning_goal)
    misconception = str(node.common_misconceptions[0]) if node.common_misconceptions else ""
    rule = str(getattr(seed, "plain_rule", "") or node.description)
    example = str(getattr(seed, "micro_example", "") or node.memory_analogy)
    scenario = str(getattr(seed, "worked_scenario", "") or node.description)
    steps = [str(s) for s in (getattr(seed, "worked_steps", ()) or ())]
    practice = str(getattr(seed, "practice_instruction", "") or "先给出判断，再用一句话说明依据。")
    quick_check = str(getattr(seed, "quick_check_format", "") or "先给出结论，再补一句理由。")
    why = (
        f"你刚才在「{node.title}」上出现了可观察的缺口"
        + (f"（{misconception}）" if misconception else "")
        + "，所以先用一个可跟踪的例子拆开关键区分，再做一次很小的检查。"
    ) if high else (
        f"你已经能说明「{node.title}」的核心规则，当前只需要聚焦边界情形，所以直接使用规则对照和变体任务。"
    )
    plan = {"experience": experience, "knowledge_node": node.id, "learning_goal": goal,
            "why_these_blocks": "补足当前可观察缺口" if high else "避免重复已掌握的解释",
            "why_this_order": "先建立关键区分，再要求判断" if high else (
                "按你确认的偏好先看具体变体，再收回规则并迁移" if preference == "example_first"
                else "按你确认的偏好先定位短规则，再马上要求迁移" if preference == "rule_then_practice"
                else "先定位当前规则边界，再要求迁移"
            ),
            "why_this_difficulty": "高支架、小步检查" if high else "低支架、短规则和更高难度变体",
            "why_this_action": "先观察例子里的关键区分" if high else "自行判断边界并解释依据", "support_level": support,
            "content_focus": "prerequisite_distinction_then_check" if high else (
                "boundary_transfer" if preference == "example_first" else "rule_boundary_then_transfer"
            ),
            "example_complexity": "minimal" if high else "tricky", "action_difficulty": "guided" if high else "transfer",
            "confirmed_presentation_preference": preference,
            "preference_used_for_mastery": False}
    if experience == "PERSONALIZED_HANDOUT":
        heading = _block("handout-heading", "HEADING", f"先分清「{node.title}」里的关键区别", node.id, refs, level=1)
        concept_rule = _block("handout-concept", "CONCEPT", rule, node.id, refs)
        example_block = _block("handout-example", "EXAMPLE", example, node.id, refs)
        worked_block = _block(
            "handout-worked", "EXAMPLE",
            scenario + ("\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)) if steps else ""),
            node.id, refs,
        )
        callout_block = _block("handout-contrast", "CALLOUT", misconception or f"注意「{node.title}」里的常见混淆。", node.id, refs)
        checkpoint_block = _block("handout-checkpoint", "CHECKPOINT", practice, node.id, refs)
        if high:
            blocks = [heading, concept_rule, worked_block, callout_block, checkpoint_block]
        elif preference == "example_first":
            blocks = [heading, worked_block, callout_block, concept_rule, checkpoint_block]
        else:
            blocks = [heading, callout_block, example_block, concept_rule, checkpoint_block]
        title = "个性化讲义：" + node.title
    else:
        steps_text = "；".join(f"{i + 1}. {s}" for i, s in enumerate(steps)) if steps else practice
        blocks = [
            _block("guide-heading", "HEADING", f"动手掌握「{node.title}」", node.id, refs, level=1),
            _block("guide-goal", "CONCEPT", "目标：" + goal, node.id, refs),
            _block("guide-start", "EXAMPLE", "起点情境：" + scenario, node.id, refs),
            _block("guide-steps", "TASK", "步骤：" + steps_text, node.id, refs),
            _block("guide-checkpoint", "CHECKPOINT", quick_check, node.id, refs),
            _block("guide-mistake", "CALLOUT", "常见错误：" + (misconception or f"在「{node.title}」上凭感觉跳过依据。"), node.id, refs),
            _block("guide-transfer", "TASK", practice, node.id, refs),
        ]
        title = "实操指南：" + node.title
    blocks.append(_block("source-node-reference", "SOURCE", "来源：" + ("；".join(refs) if refs else "该节点的既有领域资料") + "。", node.id, refs))
    catalog = pack.source_catalog_by_id()
    return {"title": title, "blocks": blocks, "purpose": {"experience": experience, "learning_goal": plan["learning_goal"], "knowledge_refs": [node.id], "personalization_plan": plan},
            # 如实标注这条链路不是模型生成的，便于上游与用户区分。
            "generation": {"mode": "deterministic", "reason": "model output unavailable"},
            "personalization": {"why_this": why, "plan": plan}, "source_refs": [catalog[x] for x in refs if x in catalog], "plan": plan}


_ALLOWED_BLOCK_KINDS = {"HEADING", "CONCEPT", "EXAMPLE", "CALLOUT", "CHECKPOINT", "TASK", "SOURCE"}


def _node_facts_for_prompt(pack, node) -> dict[str, Any]:
    """Grounded facts the generator may use — nothing outside these may appear."""
    seed = pack.resource_seeds.get(node.id)
    facts: dict[str, Any] = {
        "title": node.title,
        "description": node.description,
        "learning_goal": node.learning_goal,
        "common_misconceptions": list(node.common_misconceptions),
        "memory_analogy": node.memory_analogy,
    }
    if seed is not None:
        facts["domain_facts"] = {
            "plain_rule": seed.plain_rule,
            "micro_example": seed.micro_example,
            "worked_scenario": seed.worked_scenario,
            "worked_steps": list(seed.worked_steps),
            "worked_result": seed.worked_result,
        }
    return facts


def _extract_json_object(text: str) -> dict[str, Any] | None:
    from services.extract_llm_json import extract_json_object
    return extract_json_object(text)


def _parse_blocks(parsed: Any) -> list[dict[str, Any]] | None:
    """把模型输出校验成合法 blocks；不合法返回 None（由调用方决定是否重试）。"""
    if not isinstance(parsed, dict):
        return None
    raw_blocks = parsed.get("blocks")
    if not isinstance(raw_blocks, list) or not (3 <= len(raw_blocks) <= 8):
        return None
    blocks: list[dict[str, Any]] = []
    for item in raw_blocks:
        if not isinstance(item, dict):
            return None
        kind = str(item.get("type") or "").upper()
        text = str(item.get("text") or "").strip()
        if kind not in _ALLOWED_BLOCK_KINDS or not text:
            return None
        blocks.append({"kind": kind, "text": text})
    return blocks


async def _retrieval_grounded_plan(
    *, experience: str, fallback: Mapping[str, Any], topic: str, preference: str | None,
    llm: Any, quality_reviewer: Any = None,
) -> dict[str, Any] | None:
    """A handout for a topic no installed knowledge node covers.

    The node-bound path is the personalized one, and it stays that way: only two
    bounded packs exist, so an arbitrary topic had nothing to ground a handout
    in and silently degraded to node-seed text. Here the grounding is retrieved
    sources instead — the same block schema, the same skill, but every fact
    traceable to a real source, and the artifact says which mode produced it.

    Returns None when nothing could be retrieved: an ungrounded "handout" for an
    unknown topic would be model recall dressed as material.
    """
    from services.content_quality_gate import (
        KIND_HANDOUT, quality_receipt, run_content_quality_gate,
    )
    from services.generation_grounding import grounding_for_topic, grounding_prompt_rule
    from services.llm_service_real import extract_llm_text
    from services.skill_library import get_skill, skill_directive

    clean_topic = str(topic or "").strip()
    if not clean_topic:
        return None
    grounding = await grounding_for_topic(clean_topic)
    if not grounding.get("searched"):
        return None
    skill = get_skill("personalized-handout")
    if skill is None:
        return None

    refs = list(grounding.get("source_refs") or [])
    prompt = {
        "experience": experience,
        "skill": skill_directive([skill]),
        "topic": clean_topic,
        "confirmed_presentation_preference": preference,
        "sources": grounding.get("source_context"),
        "requirement": (
            "这个主题没有已安装的知识节点，只能依据 sources 里的来源写。"
            "只允许使用来源中明确给出的事实；来源没有覆盖的内容写「来源未覆盖」，不要用常识补上。"
            "按 skill 输出 title 与 blocks（4-6 块，必须包含 EXAMPLE 与 CALLOUT），只输出 JSON。"
        ),
        "source_rule": grounding_prompt_rule(grounding),
    }
    messages = [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]

    async def ask(extra: str | None = None) -> tuple[list[dict[str, Any]] | None, str]:
        payload = messages + ([{"role": "user", "content": extra}] if extra else [])
        raw = await llm.chat(messages=payload, temperature=0.2, max_tokens=2400,
                             use_cache=False, write_cache=False)
        parsed = _extract_json_object(extract_llm_text(raw))
        blocks = _parse_blocks(parsed)
        title = str((parsed or {}).get("title") or "").strip()
        return blocks, title

    blocks, title = await ask()
    if blocks is None:
        blocks, title = await ask("上一次输出未通过校验。请只输出合法 JSON：title 与 blocks。")
    if blocks is None:
        return None

    def render(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rendered = [
            _block(f"grounded-block-{index}", item["kind"], item["text"], clean_topic, [],
                   **({"level": 1} if item["kind"] == "HEADING" else {}))
            for index, item in enumerate(items) if item["kind"] != "SOURCE"
        ]
        rendered.append(_block(
            "source-grounding", "SOURCE",
            "来源：" + "；".join(str(ref.get("title") or ref.get("url")) for ref in refs[:5]) + "。",
            clean_topic, [str(ref.get("url") or "") for ref in refs[:5]],
        ))
        return rendered

    rendered = render(blocks)
    final_title = title or f"{clean_topic}讲义"

    async def revise(*, existing_candidate, constraints, prior_review, round_number):
        rewritten, rewritten_title = await ask(
            "上一稿未通过内容质量检查，请按下面的要求重写，仍然只使用来源中的事实：\n"
            + "\n".join(f"- {row}" for row in constraints)
        )
        if rewritten is None:
            return existing_candidate
        return {"title": rewritten_title or final_title, "blocks": render(rewritten)}

    gate_kwargs: dict[str, Any] = {
        "kind": KIND_HANDOUT,
        "candidate": {"title": final_title, "blocks": rendered},
        "topic": clean_topic,
        "revise": revise,
    }
    if quality_reviewer is not None:
        gate_kwargs["reviewer"] = quality_reviewer
    gate = await run_content_quality_gate(**gate_kwargs)
    gated = gate["candidate"]
    return {
        "title": str(gated.get("title") or final_title),
        "blocks": list(gated.get("blocks") or rendered),
        "purpose": fallback["purpose"],
        "personalization": {
            **dict(fallback.get("personalization") or {}),
            "why_this": (
                f"这个主题没有已安装的知识节点，所以这份讲义按检索到的 {len(refs)} 条来源组织；"
                "每一条事实都能追到出处，来源没覆盖的地方会写明「来源未覆盖」。"
            ),
        },
        "source_refs": [str(ref.get("url") or ref.get("title")) for ref in refs],
        "plan": fallback["plan"],
        "generation": {
            "mode": "grounded_retrieval",
            "sources": len(refs),
            "content_review": quality_receipt(gate),
        },
    }


async def build_personalized_artifact_plan(
    *, experience: str, state: Mapping[str, Any], route: Mapping[str, Any],
    preference: str | None = None, llm: Any | None = None, quality_reviewer: Any = None,
) -> dict[str, Any]:
    """Skill-driven personalized artifact plan.

    The deterministic causal planner still owns every *decision* (scaffolding
    level, ordering rationale, preference handling) because those are the
    accepted evidence-driven personalization truths.  The *content* of the
    blocks is generated live by the LLM under the personalized-handout skill,
    grounded in the domain node facts.

    真实链路优先：模型输出不可用时先做一次**定向修复重试**，只有两次都拿不到
    合法结果才退到确定性计划。兜底只处理"模型确实不可用"，不承担"一次 JSON
    抖动就换内容"的职责。
    """
    from services.llm_service_real import get_llm_service
    from services.skill_library import get_skill, skill_directive

    fallback = plan_personalized_artifact(experience=experience, state=state, route=route, preference=preference)
    try:
        pack = get_domain_pack(str(route.get("domain_id") or ""))
        if pack is None:
            # 没有已安装的知识节点：退回检索来源，而不是退回节点种子文本。
            grounded = await _retrieval_grounded_plan(
                experience=experience, fallback=fallback,
                topic=str(route.get("learner_goal") or state.get("concept_key") or ""),
                preference=preference, llm=llm, quality_reviewer=quality_reviewer,
            )
            return grounded or fallback
        node_id = str(state.get("concept_key") or route.get("current_node_id") or "")
        node = pack.get_node(node_id)
        skill = get_skill("personalized-handout")
        if skill is None:
            return fallback
        refs = _node_claim_refs(pack, node) or list(fallback.get("source_refs") or [])
        support = str(state.get("support_level") or "L2")
        evidence = list(state.get("evidence") or [])
        outcome = str((evidence[-1] if evidence else {}).get("outcome") or "uncertain")
        prompt = {
            "experience": experience,
            "skill": skill_directive([skill]),
            "node_facts": _node_facts_for_prompt(pack, node),
            "learner": {
                "support_level": support,
                "last_outcome": outcome,
                "confirmed_presentation_preference": preference,
                "learning_goal": route.get("learner_goal") or node.learning_goal,
            },
            "decision_skeleton": fallback.get("plan") or {},
            "requirement": "按 skill 生成 title 与 blocks；只使用 node_facts 内的事实；输出 JSON。",
        }
        if llm is None:
            llm = await get_llm_service()
        from services.llm_service_real import extract_llm_text

        messages = [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]
        parsed: Any = None
        blocks: list[dict[str, Any]] | None = None
        for attempt in range(2):
            raw = await llm.chat(
                messages=messages,
                temperature=0.2 if attempt == 0 else 0.0,
                max_tokens=2400, use_cache=False, write_cache=False,
            )
            parsed = _extract_json_object(extract_llm_text(raw))
            blocks = _parse_blocks(parsed)
            if blocks is not None:
                break
            logger.info("personalized artifact JSON rejected, attempting one repair pass")
            messages = messages + [
                {"role": "assistant", "content": extract_llm_text(raw)[:1500]},
                {"role": "user", "content": (
                    "上一次输出未通过校验。请只输出合法 JSON："
                    "顶层含 title（字符串）与 blocks（3~8 个对象的数组），"
                    "每个对象含 type（HEADING/CONCEPT/EXAMPLE/CALLOUT/CHECKPOINT/TASK 之一）与 text（非空字符串）。"
                    "不要输出任何 JSON 以外的字符。"
                )},
            ]
        if blocks is None:
            return fallback
        rendered: list[dict[str, Any]] = []
        for index, item in enumerate(blocks):
            # 来源行由我们统一生成（保证引用的是该节点自己的可引用来源）。
            # 模型自己写的 SOURCE 会被丢弃：否则要么出现两条来源，要么它的占位文本
            # 直接顶替掉真实来源。
            if item["kind"] == "SOURCE":
                continue
            extra = {"level": 1} if item["kind"] == "HEADING" else {}
            rendered.append(_block(
                f"{experience.lower()}-block-{index}", item["kind"], item["text"], node.id, refs, **extra,
            ))
        rendered.append(_block(
            "source-node-reference", "SOURCE",
            "来源：" + ("；".join(refs) if refs else "该节点的既有领域资料") + "。", node.id, refs,
        ))
        title = str((parsed or {}).get("title") or "").strip() or str(fallback["title"])

        # 内容质量闸门：结构合法只说明它是合法 JSON，不说明它是一份好讲义。
        # 跑题、占位、套话、没有例子，这些由闸门判定，改写要求直接回到生成器。
        from services.content_quality_gate import (
            KIND_HANDOUT,
            quality_receipt,
            run_content_quality_gate,
        )

        async def revise_handout(*, existing_candidate, constraints, prior_review, round_number):
            rewrite = dict(prompt)
            rewrite["requirement"] = (
                "上一稿未通过内容质量检查。请按下面的要求重写整份讲义：\n"
                + "\n".join(f"- {item}" for item in constraints)
                + f"\n\n上一稿：{json.dumps(dict(existing_candidate), ensure_ascii=False)[:3000]}"
                + "\n仍然只使用 node_facts 内的事实；输出同样的 JSON（title 与 blocks）。"
            )
            raw_rewrite = await llm.chat(
                messages=[{"role": "user", "content": json.dumps(rewrite, ensure_ascii=False)}],
                temperature=0.2, max_tokens=2400, use_cache=False, write_cache=False,
            )
            reparsed = _extract_json_object(extract_llm_text(raw_rewrite))
            rewritten = _parse_blocks(reparsed)
            if rewritten is None:
                return existing_candidate
            rebuilt: list[dict[str, Any]] = []
            for index, item in enumerate(rewritten):
                if item["kind"] == "SOURCE":
                    continue
                extra = {"level": 1} if item["kind"] == "HEADING" else {}
                rebuilt.append(_block(f"{experience.lower()}-block-{index}", item["kind"], item["text"],
                                      node.id, refs, **extra))
            rebuilt.append(_block("source-node-reference", "SOURCE",
                                  "来源：" + ("；".join(refs) if refs else "该节点的既有领域资料") + "。",
                                  node.id, refs))
            return {
                "title": str((reparsed or {}).get("title") or title).strip(),
                "blocks": rebuilt,
            }

        gate_kwargs: dict[str, Any] = {
            "kind": KIND_HANDOUT,
            "candidate": {"title": title, "blocks": rendered},
            "topic": f"{node.title} {node.learning_goal}".strip(),
            "learning_goal": str(route.get("learner_goal") or node.learning_goal or ""),
            "revise": revise_handout,
        }
        if quality_reviewer is not None:
            gate_kwargs["reviewer"] = quality_reviewer
        gate = await run_content_quality_gate(**gate_kwargs)
        gated = gate["candidate"]
        return {
            "title": str(gated.get("title") or title),
            "blocks": list(gated.get("blocks") or rendered),
            "purpose": fallback["purpose"], "personalization": fallback["personalization"],
            "source_refs": fallback.get("source_refs") or [], "plan": fallback["plan"],
            "generation": {
                "mode": "skill_llm", "skills": ["personalized-handout"],
                "content_review": quality_receipt(gate),
            },
        }
    except Exception as exc:  # noqa: BLE001 - 模型确实不可用时才退到确定性计划
        logger.warning("personalized artifact LLM planning degraded to deterministic blocks: %s", type(exc).__name__)
        return fallback
