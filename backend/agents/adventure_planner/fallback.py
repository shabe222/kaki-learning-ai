from __future__ import annotations

import re

from .models import (
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
    WorkedExample,
)


def infer_domain(topic: str) -> Domain:
    text = topic.lower()
    if any(token in text for token in ("python", "java", "javascript", "代码", "编程", "循环", "算法", "报错", "数据库")):
        return Domain.PROGRAMMING
    if any(token in text for token in ("英语", "英文", "语法", "写作", "口语", "翻译", "表达", "词汇")):
        return Domain.LANGUAGE
    if any(token in text for token in ("数学", "函数", "梯度", "概率", "几何", "代数", "微积分", "方程", "向量")):
        return Domain.MATH
    if any(token in text for token in ("物理", "化学", "生物", "实验", "力学", "细胞", "反应")):
        return Domain.SCIENCE
    if any(token in text for token in ("历史", "政治", "哲学", "经济", "文学", "社会", "文化")):
        return Domain.HUMANITIES
    return Domain.OPEN


def _safe_topic(value: str) -> str:
    clean = re.sub(r"\s+", " ", (value or "").strip())
    return clean[:80] or "本次主题"


def _rubric(core: str, evidence: str) -> QuestionRubric:
    return QuestionRubric(
        bands=[
            RubricBand(
                band=ScoreBandKind.CORRECT,
                criteria=[f"准确说明{core}", f"给出{evidence}"],
                feedback_focus="指出已经形成的完整证据链，并进入下一步。",
            ),
            RubricBand(
                band=ScoreBandKind.PARTIAL,
                criteria=[f"方向基本正确，但缺少{evidence}"],
                feedback_focus="先认可正确部分，再指出唯一缺失的关键一步。",
            ),
            RubricBand(
                band=ScoreBandKind.REMEDIATION,
                criteria=[f"核心判断与{core}冲突，或没有可验证依据"],
                feedback_focus="定位关键误区，给最小提示，不直接公布完整答案。",
            ),
        ]
    )


def _remediation(
    *,
    hint: str,
    alternate: str,
    scaffold: list[str],
    full_solution: str,
    confirm_question: str,
    confirm_groups: list[list[str]],
) -> RemediationLadder:
    return RemediationLadder(
        first_attempt_hint=hint,
        second_attempt_explanation=alternate,
        second_attempt_scaffold=scaffold,
        third_attempt_full_solution=full_solution,
        third_attempt_confirmation=ConfirmationTask(
            question=confirm_question,
            accepted_groups=confirm_groups,
            academic_rationale="完整讲解后用一个短确认任务检查是否真正理解，而不是只看过答案。",
        ),
    )


def _short(
    question: str,
    hint: str,
    groups: list[list[str]],
    rationale: str,
    *,
    interaction: InteractionKind,
    verification: str,
    boundary: str,
    full_solution: str,
) -> Question:
    return Question(
        kind=QuestionKind.SHORT_ANSWER,
        interaction_kind=interaction,
        question=question,
        hint=hint,
        accepted_groups=groups,
        academic_rationale=rationale,
        verification_method=verification,
        answer_boundary=boundary,
        rubric=_rubric("核心概念与关键条件", "理由、条件或验证方式"),
        remediation=_remediation(
            hint=hint,
            alternate="把任务拆成‘它是什么、在什么条件下成立、怎样检查’三个小问题。",
            scaffold=["先写出核心判断", "补充成立条件", "给出一个可检查的例子或结果"],
            full_solution=full_solution,
            confirm_question="用一句话说明这个结论成立时必须检查什么。",
            confirm_groups=[["条件", "前提"], ["验证", "检查", "证据"]],
        ),
    )


def _choice(
    question: str,
    options: list[str],
    correct: int,
    hint: str,
    rationale: str,
    *,
    interaction: InteractionKind,
    verification: str,
    boundary: str,
    full_solution: str,
) -> Question:
    return Question(
        kind=QuestionKind.MULTIPLE_CHOICE,
        interaction_kind=interaction,
        question=question,
        options=options,
        correct_option=correct,
        hint=hint,
        academic_rationale=rationale,
        verification_method=verification,
        answer_boundary=boundary,
        rubric=_rubric("正确选项及其成立条件", "排除其他选项的理由"),
        remediation=_remediation(
            hint=hint,
            alternate="逐个比较每个选项是否同时满足目标、条件和可验证结果。",
            scaffold=["找出任务目标", "检查每个选项的条件", "排除缺少证据或偷换概念的选项"],
            full_solution=full_solution,
            confirm_question="说明正确选项比其余选项多满足了哪一个关键条件。",
            confirm_groups=[["条件", "前提"], ["验证", "证据", "结果"]],
        ),
    )


def build_fallback_plan(request: PlannerRequest, difficulty: DifficultyLevel) -> KnowledgePlan:
    topic = _safe_topic(request.topic_hint)
    confusion = _safe_topic(request.focus_adjustment or request.explicit_confusion or f"需要弄清楚“{topic}”的含义、条件与用法")
    domain = infer_domain(topic + " " + confusion)

    if domain == Domain.PROGRAMMING:
        core = "程序行为"
        relation = "输入、状态变化与输出共同决定程序是否符合需求"
        example = "选择一个最小输入，逐步跟踪关键变量，再核对实际输出和测试断言。"
        misconception = "只要代码能运行，就说明逻辑一定正确。"
        correction = "能运行只说明语法和运行条件满足；还要用正常、边界与异常输入验证输出。"
        transfer = "把同一测试方法用于一个新输入或相邻功能"
        understand_interaction = InteractionKind.EXPLAIN
        practice_interaction = InteractionKind.FIND_ERROR
        transfer_interaction = InteractionKind.CODE_TASK
        verification = "用明确输入、预期输出和至少一个测试断言核对程序行为。"
    elif domain == Domain.LANGUAGE:
        core = "表达意图"
        relation = "词语、语法、语气和语境共同决定表达是否准确自然"
        example = "先写出一句目标表达，再比较正式与自然两种改写。"
        misconception = "语法正确的句子在任何语境里都同样自然。"
        correction = "语法只是底线；语气、对象和场景会改变更合适的表达。"
        transfer = "在新的对象或语境中表达同一意图"
        understand_interaction = InteractionKind.REWRITE
        practice_interaction = InteractionKind.COMPARE
        transfer_interaction = InteractionKind.OPEN_RESPONSE
        verification = "按语义准确、语气合适、表达自然三个维度比较改写。"
    elif domain == Domain.MATH:
        core = "概念与适用条件"
        relation = "定义说明对象是什么，条件决定结论何时成立，推理或计算用于验证"
        example = "先用自己的话解释概念，再用一个满足条件和一个不满足条件的例子对照。"
        misconception = "记住公式外形，就等于理解了概念。"
        correction = "真正理解还需要说明量的意义、成立条件，并通过推导、代入或反例检查。"
        transfer = "把概念用于一个表面形式不同的新问题"
        understand_interaction = InteractionKind.EXPLAIN
        practice_interaction = InteractionKind.COMPARE
        transfer_interaction = InteractionKind.APPLICATION
        verification = "检查定义、成立条件和推理或计算结果是否一致。"
    elif domain == Domain.SCIENCE:
        core = "机制与证据"
        relation = "机制提出因果解释，控制变量与观察证据用于检验解释"
        example = "明确变量和条件，再预测改变一个变量会带来什么可观察结果。"
        misconception = "两个现象同时出现，就一定存在直接因果。"
        correction = "同时出现只说明相关；还要检查机制、控制变量和替代解释。"
        transfer = "用同一机制预测一个条件变化后的结果"
        understand_interaction = InteractionKind.EXPLAIN
        practice_interaction = InteractionKind.COUNTEREXAMPLE
        transfer_interaction = InteractionKind.APPLICATION
        verification = "用变量、条件、可观察预测和替代解释检查因果链。"
    elif domain == Domain.HUMANITIES:
        core = "主张、语境与证据"
        relation = "解释必须把主张放回语境，并由具体材料和推理支撑"
        example = "先写出一个主张，再指出支持材料、语境和可能的另一种解释。"
        misconception = "只要观点听起来合理，就不需要证据。"
        correction = "观点需要材料、语境和推理链支撑，并应说明适用边界。"
        transfer = "用同一分析框架比较一个新的案例"
        understand_interaction = InteractionKind.OPEN_RESPONSE
        practice_interaction = InteractionKind.COMPARE
        transfer_interaction = InteractionKind.APPLICATION
        verification = "按主张、材料、推理、语境和边界检查论证。"
    else:
        core = "核心主张与适用边界"
        relation = "清晰结论需要理由、证据、反例和边界共同支撑"
        example = "先用一句话说清主张，再补充理由、例子和一个可能的反例。"
        misconception = "给出结论就等于完成了解释。"
        correction = "解释还需要说明为什么成立、在什么条件下成立以及何时不成立。"
        transfer = "把同一推理框架用于一个新的情境"
        understand_interaction = InteractionKind.OPEN_RESPONSE
        practice_interaction = InteractionKind.COMPARE
        transfer_interaction = InteractionKind.APPLICATION
        verification = "按主张、理由、证据、边界和反例进行分维度检查。"

    keyword = topic[:24]
    common_solution = f"围绕“{topic}”，先说明核心含义，再列出成立条件，最后用一个可检查的例子或结果验证。"
    understand_trial = _short(
        f"用自己的话说明“{topic}”中最核心的含义，并指出它解决什么问题。",
        "先说是什么，再说它帮助我们判断或完成什么。",
        [[keyword], ["含义", "作用", "问题"]],
        "检验用户是否形成概念解释，而非只复述标题。",
        interaction=understand_interaction,
        verification=verification,
        boundary="允许不同措辞，但必须包含核心含义与用途；不能只重复标题。",
        full_solution=common_solution,
    )
    practice_trial = _choice(
        f"开始检验对“{topic}”的理解时，哪种做法更可靠？",
        ["明确条件并用可检查的例子验证", "只记住一句结论", "跳过反馈直接进入下一题"],
        0,
        "可靠理解需要条件、例子和反馈。",
        "辨析完整验证链与表面记忆的差别。",
        interaction=practice_interaction,
        verification=verification,
        boundary="只有同时包含条件和可检查证据的选项是最佳答案。",
        full_solution="第一项正确，因为它同时保留了成立条件和可验证证据；其余选项只能证明看过或记过。",
    )
    remedy = _choice(
        "如果解释只有结论、没有依据，下一步最应该补什么？",
        ["补充成立条件和一个可验证例子", "把结论重复三遍", "换一个更长的标题"],
        0,
        "寻找能让结论被检查的证据。",
        "补救针对‘只有结论没有证据’的缺口。",
        interaction=InteractionKind.STEP_COMPLETION,
        verification="确认补充内容能让原结论被具体检查。",
        boundary="应同时补条件和验证方式，而不是增加无关文字。",
        full_solution="补上成立条件，再给一个能检验这些条件和结论的例子，解释才成为可检查的知识。",
    )
    understanding_interleave = _choice(
        f"把“{topic}”用于新问题前，最先应该核对什么？",
        ["新问题的关键条件是否与原结论匹配", "两个问题的字数是否相同", "页面展示是否相同"],
        0,
        "迁移依赖条件，而不是表面形式。",
        "检验用户能否从概念表面转向适用条件。",
        interaction=InteractionKind.COMPARE,
        verification="比较新旧情境的关键条件和目标是否一致。",
        boundary="表面相似不是迁移依据，必须核对适用条件。",
        full_solution="应先核对关键条件。条件匹配时，原规律才可能迁移；字数或页面外观与知识成立无关。",
    )
    practice_interleave = _short(
        f"设计一个新的小任务，用来验证你是否真正理解“{topic}”。",
        "写清情境、要做的动作和如何判断结果。",
        [["情境", "例子", "任务"], ["验证", "判断", "结果"]],
        "让用户主动构造可验证任务，形成迁移证据。",
        interaction=transfer_interaction,
        verification=verification,
        boundary="任务必须包含新情境、行动与明确验证方式。",
        full_solution=f"给出一个不同于原例的新情境，应用“{topic}”的关键条件，并说明用什么结果判断应用是否正确。",
    )
    boss = [
        BossStage(
            kind=GoalKind.UNDERSTAND,
            label="解释",
            task=_short(
                f"向同伴解释“{topic}”的核心含义、作用和一个成立条件。",
                "组织成‘是什么—为什么有用—何时成立’。",
                [[keyword], ["作用", "问题"], ["条件", "适用"]],
                "解释阶段要求形成完整的概念证据链。",
                interaction=understand_interaction,
                verification=verification,
                boundary="必须覆盖含义、作用和至少一个成立条件。",
                full_solution=common_solution,
            ),
        ),
        BossStage(
            kind=GoalKind.DISTINGUISH,
            label="辨析",
            task=_choice(
                "下面哪种学习证据最能说明已经理解，而不是只记住了表面？",
                ["能解释条件并辨认一个不适用的例子", "能原样背出标题", "看过一次标准答案"],
                0,
                "区分‘记住’与‘能判断条件’。",
                "辨析阶段聚焦表面记忆误区。",
                interaction=practice_interaction,
                verification=verification,
                boundary="最佳证据必须同时包含解释、条件判断和反例辨认。",
                full_solution="第一项是理解证据，因为它能解释成立条件并识别边界；背标题或看答案不能证明可迁移理解。",
            ),
        ),
        BossStage(
            kind=GoalKind.TRANSFER,
            label="迁移",
            task=_short(
                f"把“{topic}”用于一个新情境，说明你会检查哪些条件、采取什么动作、怎样验证结果。",
                "至少写出条件、动作和验证方式。",
                [["条件", "前提"], ["步骤", "动作", "应用"], ["验证", "结果", "检查"]],
                "迁移阶段检验概念结构能否用于新情境。",
                interaction=transfer_interaction,
                verification=verification,
                boundary="新情境可以不同，但必须明确条件、行动与验证结果。",
                full_solution=f"先确认新情境满足“{topic}”的适用条件，再执行对应动作，最后用可观察结果、计算、测试或证据检查结论。",
            ),
        ),
    ]

    return KnowledgePlan(
        topic=topic,
        specific_confusion=confusion,
        domain=domain,
        difficulty=difficulty,
        difficulty_reason="模型规划不可用，采用与当前主题和难度一致的确定性学习脚手架。",
        preview=PlanPreview(
            theme=topic[:64],
            goals=[
                PreviewGoal(kind=GoalKind.UNDERSTAND, text=f"理解“{topic}”的核心含义与关键条件"[:80]),
                PreviewGoal(kind=GoalKind.DISTINGUISH, text=f"辨析“{topic}”中最容易混淆的理解"[:80]),
                PreviewGoal(kind=GoalKind.TRANSFER, text=f"把“{topic}”用于一个新的可验证情境"[:80]),
            ],
        ),
        source_grounding=[source.source_id for source in request.selected_sources[:4]],
        source_note=(
            "基于用户主动选择的资料与当前对话生成确定性学习脚手架"
            if request.selected_sources
            else "基于当前对话生成确定性学习脚手架"
        ),
        source_conflict_note=None,
        confirmed_fact_ids=[fact.fact_id for fact in request.confirmed_facts if fact.locked],
        prerequisites=[
            Prerequisite(name="主题基本术语", why_needed="确保后续解释使用同一含义", required=False)
        ],
        core_concepts=[
            CoreConcept(name=core, explanation=relation, applicability=f"用于判断“{topic}”何时成立以及如何验证")
        ],
        relationships=[ConceptRelationship(from_concept=core, relation="需要通过", to_concept="条件与证据")],
        misconceptions=[
            Misconception(
                mistaken_claim=misconception,
                correction=correction,
                diagnostic_cue="回答只有结论，缺少条件、理由或验证方式",
            )
        ],
        teaching_strategy=["先形成通俗解释，再补足条件", "用对照例辨析误区", f"通过{transfer}完成迁移"],
        worked_example=WorkedExample(
            scenario=example,
            steps=["明确要解释或完成的对象", "列出关键条件并执行最小验证"],
            conclusion="只有能说明条件并经受新例子检验，才算形成了可迁移的理解。",
        ),
        challenges=ChallengeSet(
            understanding_trial=understand_trial,
            practice_trial=practice_trial,
            remedy=remedy,
            understanding_interleave=understanding_interleave,
            practice_interleave=practice_interleave,
            boss=boss,
        ),
        completion_summary="能解释核心概念、辨认关键误区，并在新情境中给出可验证的应用。",
        completion_criteria=["能解释核心含义与关键条件", "能辨认并修正常见误区", "能在新情境中说明应用与验证过程"],
        next_step="完成旅程后，用一个新的例子做一次简短回忆。",
    )
