from __future__ import annotations

from typing import Any

from .models import KnowledgePlan, PlannerResult, Question, QuestionKind


def _question_to_legacy(question: Question) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": question.kind.value,
        "interaction_kind": question.interaction_kind.value if question.interaction_kind else None,
        "question": question.question,
        "hint": question.hint,
    }
    # 教学字段：考察点（答前可展示）、完整讲解与错误引导（答错后由后端下发到反馈）。
    if question.academic_rationale:
        payload["rationale"] = question.academic_rationale
    if question.remediation is not None:
        payload["answer_explanation"] = question.remediation.third_attempt_full_solution
        payload["wrong_guidance"] = question.remediation.first_attempt_hint
        payload["second_attempt"] = question.remediation.second_attempt_explanation
        if question.remediation.second_attempt_scaffold:
            payload["scaffold"] = list(question.remediation.second_attempt_scaffold)
    if question.kind == QuestionKind.MULTIPLE_CHOICE:
        payload["options"] = list(question.options or [])
        payload["correct_option"] = int(question.correct_option or 0)
    else:
        payload["accepted_groups"] = [list(group) for group in question.accepted_groups or []]
    return payload


def knowledge_plan_to_deck(plan: KnowledgePlan) -> dict[str, Any]:
    challenge = plan.challenges
    boss = []
    for stage in challenge.boss:
        task = _question_to_legacy(stage.task)
        task["label"] = stage.label
        boss.append(task)
    core = plan.core_concepts[0] if plan.core_concepts else None
    misconception = plan.misconceptions[0] if plan.misconceptions else None
    return {
        "source_note": plan.source_note,
        "rule": plan.core_concepts[0].explanation,
        "example": "；".join(
            [plan.worked_example.scenario] + list(plan.worked_example.steps)
        ) + "。" + plan.worked_example.conclusion,
        # 教学卡：核心概念 / 常见误区与纠正 / 教学策略（卷轴与守关讲解使用）
        "concept": {
            "name": core.name if core else plan.topic,
            "explanation": core.explanation if core else (plan.core_concepts[0].explanation if plan.core_concepts else plan.topic),
            "applicability": core.applicability if core else "",
        },
        "misconception": {
            "claim": misconception.mistaken_claim if misconception else "",
            "correction": misconception.correction if misconception else "",
            "cue": misconception.diagnostic_cue if misconception else "",
        },
        "teaching_strategy": list(plan.teaching_strategy or []),
        "completion_summary": plan.completion_summary or "",
        "next_step": plan.next_step or "",
        "understanding": {
            "trial": _question_to_legacy(challenge.understanding_trial),
            "interleave": _question_to_legacy(challenge.understanding_interleave),
        },
        "practice": {
            "trial": _question_to_legacy(challenge.practice_trial),
            "interleave": _question_to_legacy(challenge.practice_interleave),
        },
        "remedy": _question_to_legacy(challenge.remedy),
        "boss": boss,
    }


def plan_to_existing_content(result: PlannerResult) -> dict[str, Any]:
    if result.status != "ready" or result.plan is None:
        raise ValueError("cannot adapt a clarification result to adventure content")
    plan = result.plan
    deck = knowledge_plan_to_deck(plan)
    return {
        "title": f"{plan.preview.theme[:36]} · 拾光漫游",
        "subtitle": "用十分钟，完成一次有始有终的学习小旅程。",
        "topic": plan.topic,
        "source_note": deck["source_note"],
        "plan_preview": plan.preview.model_dump(mode="json"),
        "completion_summary": plan.completion_summary,
        "planner_meta": {
            "schema_version": result.schema_version,
            "generation_mode": result.metadata.generation_mode,
            "difficulty": plan.difficulty.value,
            "request_fingerprint": result.metadata.request_fingerprint,
            "content_fingerprint": result.metadata.content_fingerprint,
            "draft_revision": result.metadata.draft_revision,
            "adjustment_count": result.metadata.adjustment_count,
            "plan_state": result.metadata.plan_state,
            "warnings": list(result.metadata.warnings),
        },
        "scroll": {"rule": deck["rule"], "example": deck["example"]},
        "routes": deck["understanding"] | {"practice": deck["practice"]},
        "understanding": deck["understanding"],
        "practice": deck["practice"],
        "remedy": deck["remedy"],
        "boss": deck["boss"],
    }
