from __future__ import annotations

import re
from typing import Iterable

from .models import (
    ClarificationReason,
    DifficultyLevel,
    Domain,
    GoalKind,
    InteractionKind,
    KnowledgePlan,
    LLMPlanEnvelope,
    PlannerRequest,
    Question,
    QuestionKind,
)


FORBIDDEN_PREVIEW_TERMS = {
    "系统",
    "用户画像",
    "参数设置",
    "JSON",
    "API",
    "模型调用",
    "评分规则",
    "前置知识列表",
    "Boss",
}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _question_signature(question: Question) -> str:
    return _normalise(question.question)


def _iter_questions(plan: KnowledgePlan) -> Iterable[Question]:
    challenges = plan.challenges
    yield challenges.understanding_trial
    yield challenges.practice_trial
    yield challenges.remedy
    yield challenges.understanding_interleave
    yield challenges.practice_interleave
    for stage in challenges.boss:
        yield stage.task


def _validate_question(question: Question, errors: list[str]) -> None:
    if question.kind == QuestionKind.MULTIPLE_CHOICE and question.options:
        normal_options = [_normalise(item) for item in question.options]
        if len(normal_options) != len(set(normal_options)):
            errors.append(f"multiple-choice options are duplicated: {question.question}")
    if question.kind == QuestionKind.SHORT_ANSWER and question.accepted_groups:
        for group in question.accepted_groups:
            if not 1 <= len(group) <= 6:
                errors.append(f"accepted group must contain 1-6 terms: {question.question}")
            if any(not str(term).strip() for term in group):
                errors.append(f"accepted group contains empty term: {question.question}")

    if not question.verification_method:
        errors.append(f"question requires verification_method: {question.question}")
    if not question.answer_boundary:
        errors.append(f"question requires answer_boundary: {question.question}")
    if question.rubric is None:
        errors.append(f"question requires three-band rubric: {question.question}")
    if question.remediation is None:
        errors.append(f"question requires three-attempt remediation ladder: {question.question}")


def _validate_domain_design(plan: KnowledgePlan, errors: list[str]) -> None:
    interactions = {question.interaction_kind for question in _iter_questions(plan)}
    if len(interactions) < 2:
        errors.append("an adventure must use at least two different interaction forms")

    if plan.domain == Domain.MATH:
        expected = {
            InteractionKind.EXPLAIN,
            InteractionKind.STEP_COMPLETION,
            InteractionKind.COMPARE,
            InteractionKind.COUNTEREXAMPLE,
            InteractionKind.APPLICATION,
        }
        if not interactions.intersection(expected):
            errors.append("math plan requires explanation, comparison, step completion, counterexample or application")
    elif plan.domain == Domain.PROGRAMMING:
        if not interactions.intersection({InteractionKind.CODE_TASK, InteractionKind.FIND_ERROR}):
            errors.append("programming plan requires at least one code_task or find_error interaction")
        verification_text = " ".join(question.verification_method or "" for question in _iter_questions(plan))
        if not any(token in verification_text.lower() for token in ("测试", "输出", "输入", "断言", "test", "output")):
            errors.append("programming plan requires testable input/output or assertion evidence")
    elif plan.domain == Domain.LANGUAGE:
        if not interactions.intersection({InteractionKind.REWRITE, InteractionKind.COMPARE, InteractionKind.OPEN_RESPONSE}):
            errors.append("language plan requires rewrite, compare or open_response interaction")
    elif plan.domain == Domain.OPEN:
        if InteractionKind.OPEN_RESPONSE not in interactions:
            errors.append("open plan requires at least one open_response interaction")


def validate_plan(
    envelope: LLMPlanEnvelope,
    request: PlannerRequest,
    required_difficulty: DifficultyLevel,
) -> list[str]:
    errors: list[str] = []
    unresolved_conflicts = [item for item in request.source_conflicts if not item.resolved]

    if envelope.status == "needs_clarification":
        if request.clarification_count >= 1:
            errors.append("clarification_count is already 1; a plan must be produced")
        clarification = envelope.clarification
        if clarification and len({_normalise(item) for item in clarification.options}) != 2:
            errors.append("clarification options must be distinct")
        if clarification and clarification.reason == ClarificationReason.SOURCE_CONFLICT and not unresolved_conflicts:
            errors.append("source_conflict clarification requires an unresolved source conflict")
        return errors

    plan = envelope.plan
    if plan is None:
        return ["ready result has no plan"]
    if unresolved_conflicts:
        errors.append("unresolved source conflict requires one clarification before producing a plan")

    expected_order = [GoalKind.UNDERSTAND, GoalKind.DISTINGUISH, GoalKind.TRANSFER]
    actual_order = [goal.kind for goal in plan.preview.goals]
    if actual_order != expected_order:
        errors.append("preview goals must be ordered understand, distinguish, transfer")
    goal_texts = [_normalise(goal.text) for goal in plan.preview.goals]
    if len(set(goal_texts)) != 3:
        errors.append("preview goal texts must be distinct")
    for text in [plan.preview.theme, *(goal.text for goal in plan.preview.goals)]:
        for term in FORBIDDEN_PREVIEW_TERMS:
            if term.lower() in text.lower():
                errors.append(f"preview contains backstage term: {term}")

    if plan.difficulty != required_difficulty:
        errors.append(
            f"difficulty must equal required_difficulty={required_difficulty.value}, got {plan.difficulty.value}"
        )

    real_source_ids = {source.source_id for source in request.selected_sources}
    invented = [source_id for source_id in plan.source_grounding if source_id not in real_source_ids]
    if invented:
        errors.append(f"source_grounding contains unknown ids: {invented}")

    real_fact_ids = {fact.fact_id for fact in request.confirmed_facts}
    invented_facts = [fact_id for fact_id in plan.confirmed_fact_ids if fact_id not in real_fact_ids]
    if invented_facts:
        errors.append(f"confirmed_fact_ids contains unknown ids: {invented_facts}")
    required_fact_ids = {fact.fact_id for fact in request.confirmed_facts if fact.locked}
    if not required_fact_ids.issubset(set(plan.confirmed_fact_ids)):
        errors.append("all locked confirmed facts must be preserved in confirmed_fact_ids")

    boss = plan.challenges.boss
    expected_boss = [
        (GoalKind.UNDERSTAND, "解释"),
        (GoalKind.DISTINGUISH, "辨析"),
        (GoalKind.TRANSFER, "迁移"),
    ]
    actual_boss = [(stage.kind, stage.label) for stage in boss]
    if actual_boss != expected_boss:
        errors.append("boss order must be understand/解释, distinguish/辨析, transfer/迁移")

    signatures: dict[str, int] = {}
    for question in _iter_questions(plan):
        signature = _question_signature(question)
        signatures[signature] = signatures.get(signature, 0) + 1
        _validate_question(question, errors)
    if any(count > 1 for count in signatures.values()):
        errors.append("challenge questions must not be exact duplicates")

    if len(plan.core_concepts) != 1:
        errors.append("plan must focus on exactly one core concept")
    if len(plan.misconceptions) != 1:
        errors.append("plan must focus on exactly one key misconception")
    if not plan.completion_summary:
        errors.append("plan requires one completion_summary")
    if len(plan.completion_criteria) != 3:
        errors.append("completion_criteria must contain exactly three evidence criteria")

    _validate_domain_design(plan, errors)
    return errors
