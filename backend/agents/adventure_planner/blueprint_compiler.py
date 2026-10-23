from __future__ import annotations

import re

from .learner_state import resolve_learner_state
from .models import (
    BlueprintTask,
    BossStage,
    ChallengeSet,
    ConceptRelationship,
    ConfirmationTask,
    CoreConcept,
    DifficultyLevel,
    Domain,
    GoalKind,
    InteractionKind,
    KnowledgePlan,
    LearningBlueprint,
    Misconception,
    PlanPreview,
    PlannerRequest,
    PreviewGoal,
    Prerequisite,
    Question,
    QuestionKind,
    QuestionRubric,
    RemediationLadder,
    RubricBand,
    ScoreBandKind,
)


_DIFFICULTY_LABELS = {
    DifficultyLevel.FOUNDATIONAL: "基础",
    DifficultyLevel.STANDARD: "标准",
    DifficultyLevel.STANDARD_PLUS: "标准偏进取",
    DifficultyLevel.CHALLENGING: "挑战",
    DifficultyLevel.ADVANCED: "进阶",
}


def _clean(value: str, *, fallback: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    return (text or fallback)[:limit]


def _hint(value: str) -> str:
    text = _clean(value, fallback="先找出题目中的关键条件", limit=170)
    if len(text) < 4:
        text = f"思考提示：{text}"
    return text[:180]


def _domain_verification(domain: Domain) -> str:
    if domain == Domain.MATH:
        return "通过定义、推导、代入计算、图形或反例检查条件与结论是否一致。"
    if domain == Domain.PROGRAMMING:
        return "使用明确输入、预期输出和测试断言检查代码或算法行为。"
    if domain == Domain.LANGUAGE:
        return "按语义准确、语气合适、表达自然和具体语境比较答案。"
    if domain == Domain.SCIENCE:
        return "核对变量、条件、机制、可观察预测和替代解释是否一致。"
    if domain == Domain.HUMANITIES:
        return "按主张、材料、语境、推理和适用边界检查解释。"
    return "按主张、理由、证据、逻辑、边界和反例分维度检查答案。"


def _answer_evidence(task: BlueprintTask) -> str:
    if task.kind == QuestionKind.MULTIPLE_CHOICE:
        assert task.options is not None and task.correct_option is not None
        return task.options[task.correct_option]
    groups = task.accepted_groups or []
    return "；".join("/".join(group) for group in groups)


def _answer_boundary(task: BlueprintTask, domain: Domain) -> str:
    if task.kind == QuestionKind.MULTIPLE_CHOICE:
        correct = _answer_evidence(task)
        base = f"最佳答案必须是“{correct}”，并能说明其他选项为什么不满足关键条件。"
    else:
        base = f"允许不同措辞，但回答应覆盖这些证据组：{_answer_evidence(task)}。"

    if domain == Domain.LANGUAGE:
        return (base + "同时考虑语义、语气、自然度和语境，不要求唯一标准句。")[:260]
    if domain in {Domain.HUMANITIES, Domain.OPEN}:
        return (base + "论证还应包含主张、理由、证据、逻辑与适用边界。")[:260]
    if domain == Domain.PROGRAMMING:
        return (base + "代码结论必须能由输入、输出或测试结果验证。")[:260]
    if domain == Domain.MATH:
        return (base + "必须保留定义、成立条件和推理或计算依据。")[:260]
    return (base + "必须说明机制、条件和可观察证据。")[:260]


def _rubric(task: BlueprintTask) -> QuestionRubric:
    evidence = _answer_evidence(task)
    return QuestionRubric(
        bands=[
            RubricBand(
                band=ScoreBandKind.CORRECT,
                criteria=[f"核心判断正确，并给出与“{evidence[:48]}”一致的关键依据"],
                feedback_focus="指出已经成立的核心判断与证据链，再进入下一步。",
            ),
            RubricBand(
                band=ScoreBandKind.PARTIAL,
                criteria=["方向基本正确，但缺少一个关键条件、步骤或证据"],
                feedback_focus="先认可正确部分，再指出唯一缺失的关键一步。",
            ),
            RubricBand(
                band=ScoreBandKind.REMEDIATION,
                criteria=["核心判断与关键条件冲突，或答案没有可验证依据"],
                feedback_focus="定位关键误区，给最小提示，不直接公布完整答案。",
            ),
        ]
    )


def _scaffold(domain: Domain, support_level: str) -> list[str]:
    if domain == Domain.PROGRAMMING:
        steps = ["明确输入和目标行为", "跟踪关键状态或变量", "用预期输出或测试断言核对"]
    elif domain == Domain.MATH:
        steps = ["写出核心定义或关系", "核对成立条件", "用推导、计算或反例验证"]
    elif domain == Domain.LANGUAGE:
        steps = ["先确定表达意图和对象", "比较语义与语气", "放回具体语境检查自然度"]
    elif domain in {Domain.HUMANITIES, Domain.OPEN}:
        steps = ["先写清核心主张", "补充材料与推理", "说明边界或可能反例"]
    else:
        steps = ["明确对象和变量", "说明机制与条件", "用可观察结果检查预测"]
    return steps[:2] if support_level == "independent" else steps


def _full_solution(task: BlueprintTask) -> str:
    evidence = _answer_evidence(task)
    if task.kind == QuestionKind.MULTIPLE_CHOICE:
        return _clean(
            f"正确答案是“{evidence}”。{task.rationale} 其余选项没有同时满足题目的关键条件与验证要求。",
            fallback="根据关键条件逐项比较后，可确定最佳答案。",
            limit=590,
        )
    return _clean(
        f"{task.rationale} 一个完整回答应至少覆盖：{evidence}，并说明这些要点怎样支持结论。",
        fallback="完整回答需要给出核心判断、成立条件和可验证依据。",
        limit=590,
    )


def _confirmation(task: BlueprintTask) -> ConfirmationTask:
    if task.kind == QuestionKind.MULTIPLE_CHOICE:
        evidence = _answer_evidence(task)
        return ConfirmationTask(
            question=f"再用一句话说明为什么“{evidence[:80]}”满足关键条件。",
            accepted_groups=[[evidence[:80]], ["条件", "依据", "验证"]],
            academic_rationale="完整讲解后用短任务确认学生能独立说明关键条件。",
        )
    return ConfirmationTask(
        question="再用一句话概括这道题必须包含的关键证据。",
        accepted_groups=[list(group) for group in (task.accepted_groups or [])],
        academic_rationale="完整讲解后用短任务确认学生能独立提取关键证据。",
    )


def _question(
    task: BlueprintTask,
    *,
    domain: Domain,
    misconception: Misconception,
    support_level: str,
) -> Question:
    hint = _hint(task.hint)
    full_solution = _full_solution(task)
    if len(full_solution) <= len(hint):
        full_solution = (full_solution + " 还要说明该结论成立的条件与验证方式。")[:600]

    remediation = RemediationLadder(
        first_attempt_hint=hint,
        second_attempt_explanation=_clean(
            f"换一种看法：先把“{misconception.mistaken_claim}”与“{misconception.correction}”对照，再回到题目条件。",
            fallback="换一种讲法，先区分正确机制与关键误区。",
            limit=290,
        ),
        second_attempt_scaffold=_scaffold(domain, support_level),
        third_attempt_full_solution=full_solution,
        third_attempt_confirmation=_confirmation(task),
    )
    return Question(
        kind=task.kind,
        interaction_kind=task.interaction_kind,
        question=task.question,
        hint=hint,
        options=list(task.options) if task.options is not None else None,
        correct_option=task.correct_option,
        accepted_groups=[list(group) for group in task.accepted_groups] if task.accepted_groups else None,
        academic_rationale=task.rationale,
        verification_method=_domain_verification(domain),
        answer_boundary=_answer_boundary(task, domain),
        rubric=_rubric(task),
        remediation=remediation,
    )


def _misconception_interleave(blueprint: LearningBlueprint) -> BlueprintTask:
    correction = _clean(
        blueprint.misconception.correction,
        fallback="应依据关键条件与证据判断",
        limit=150,
    )
    mistaken = _clean(
        blueprint.misconception.mistaken_claim,
        fallback="只要记住结论就不必检查条件",
        limit=150,
    )
    neutral = "只要表面形式相似，就可以直接套用原结论"
    if _clean(neutral, fallback="", limit=150) in {correction, mistaken}:
        neutral = "只需记住术语，不需要说明理由或验证方式"
    return BlueprintTask(
        kind=QuestionKind.MULTIPLE_CHOICE,
        interaction_kind=InteractionKind.COMPARE,
        question=f"换一个表述后，哪项最符合“{blueprint.core_concept.name}”的正确理解？",
        hint="比较每个说法是否保留了关键条件和验证依据。",
        options=[correction, mistaken, neutral],
        correct_option=0,
        rationale="通过新表述再次区分正确机制与关键误区，检查理解是否稳定。",
    )


def _source_note(blueprint: LearningBlueprint, request: PlannerRequest) -> str:
    if blueprint.source_grounding:
        return "优先依据用户主动选择的资料，并结合当前明确困惑生成学习计划。"
    if request.selected_sources:
        return "当前计划基于相关对话生成；未引用未确认的资料内容。"
    return "基于当前明确困惑与相关对话生成学习计划。"


def compile_blueprint(
    blueprint: LearningBlueprint,
    request: PlannerRequest,
    difficulty: DifficultyLevel,
) -> KnowledgePlan:
    """Compile a compact academic blueprint into the legacy full KnowledgePlan."""

    learner_state = resolve_learner_state(request)
    domain = blueprint.domain
    misconception = blueprint.misconception
    tasks = blueprint.tasks

    understanding_trial = _question(
        tasks.understanding_trial,
        domain=domain,
        misconception=misconception,
        support_level=learner_state.support_level,
    )
    practice_trial = _question(
        tasks.distinction_trial,
        domain=domain,
        misconception=misconception,
        support_level=learner_state.support_level,
    )
    remedy = _question(
        tasks.remedy,
        domain=domain,
        misconception=misconception,
        support_level="scaffold",
    )
    understanding_interleave = _question(
        _misconception_interleave(blueprint),
        domain=domain,
        misconception=misconception,
        support_level=learner_state.support_level,
    )
    practice_interleave = _question(
        tasks.transfer_interleave,
        domain=domain,
        misconception=misconception,
        support_level=learner_state.support_level,
    )

    boss = [
        BossStage(
            kind=GoalKind.UNDERSTAND,
            label="解释",
            task=_question(
                tasks.boss_understand,
                domain=domain,
                misconception=misconception,
                support_level=learner_state.support_level,
            ),
        ),
        BossStage(
            kind=GoalKind.DISTINGUISH,
            label="辨析",
            task=_question(
                tasks.boss_distinguish,
                domain=domain,
                misconception=misconception,
                support_level=learner_state.support_level,
            ),
        ),
        BossStage(
            kind=GoalKind.TRANSFER,
            label="迁移",
            task=_question(
                tasks.boss_transfer,
                domain=domain,
                misconception=misconception,
                support_level=learner_state.support_level,
            ),
        ),
    ]

    goal_texts = [
        blueprint.goals.understand,
        blueprint.goals.distinguish,
        blueprint.goals.transfer,
    ]
    support_note = {
        "independent": "当前证据支持减少支架并谨慎增加迁移要求",
        "scaffold": "当前证据显示需要分步支架，卡住时立即回退",
        "full": "当前证据显示需要完整示范与短确认任务",
        "hint": "当前证据适合从最小提示开始，保留逐步加支架的空间",
    }[learner_state.support_level]

    return KnowledgePlan(
        topic=blueprint.topic,
        specific_confusion=blueprint.specific_confusion,
        domain=domain,
        difficulty=difficulty,
        difficulty_reason=(
            f"采用{_DIFFICULTY_LABELS[difficulty]}难度；{support_note}。"
        )[:240],
        preview=PlanPreview(
            theme=blueprint.preview_theme,
            goals=[
                PreviewGoal(kind=GoalKind.UNDERSTAND, text=blueprint.goals.understand),
                PreviewGoal(kind=GoalKind.DISTINGUISH, text=blueprint.goals.distinguish),
                PreviewGoal(kind=GoalKind.TRANSFER, text=blueprint.goals.transfer),
            ],
        ),
        source_grounding=list(blueprint.source_grounding),
        source_note=_source_note(blueprint, request),
        source_conflict_note=None,
        confirmed_fact_ids=[fact.fact_id for fact in request.confirmed_facts if fact.locked],
        prerequisites=[
            Prerequisite(
                name=f"{blueprint.core_concept.name}相关基本术语"[:60],
                why_needed="用于确保后续解释中的对象、条件和结论含义一致。",
                required=False,
            )
        ],
        core_concepts=[
            CoreConcept(
                name=blueprint.core_concept.name,
                explanation=blueprint.core_concept.explanation,
                applicability=blueprint.core_concept.applicability,
            )
        ],
        relationships=[
            ConceptRelationship(
                from_concept=blueprint.core_concept.name,
                relation="需要通过关键条件与证据区别于",
                to_concept="常见误区",
            )
        ],
        misconceptions=[misconception.model_copy(deep=True)],
        teaching_strategy=[
            "先建立核心机制，再用关键误区做对照辨析。",
            "用最小提示开始，根据回答逐步增加支架并完成迁移。",
        ],
        worked_example=blueprint.worked_example.model_copy(deep=True),
        challenges=ChallengeSet(
            understanding_trial=understanding_trial,
            practice_trial=practice_trial,
            remedy=remedy,
            understanding_interleave=understanding_interleave,
            practice_interleave=practice_interleave,
            boss=boss,
        ),
        completion_summary=(
            f"能{goal_texts[0]}，能{goal_texts[1]}，并能{goal_texts[2]}。"
        )[:240],
        completion_criteria=goal_texts,
        next_step=blueprint.follow_up_recommendation,
    )
