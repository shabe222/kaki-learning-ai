# -*- coding: utf-8 -*-
"""拾光漫游 · 游戏智能体内容生成（brain）

独立于主平台：不依赖 database / services / integrations，
只使用 agents.adventure_planner（已确认零主平台依赖）。
输入任意主题 -> 产出完整学习蓝图（规则/概念/误区/题目/判定标准/教学反馈）。
"""
from __future__ import annotations

from typing import Any

from agents.adventure_planner.adapter import knowledge_plan_to_deck
from agents.adventure_planner.fallback import build_fallback_plan
from agents.adventure_planner.models import DifficultyLevel, PlannerRequest


# ===== 精选卡：高质量确定性内容（不依赖脚手架生成） =====
_CURATED: dict[str, dict[str, Any]] = {
    "machinelearningbasics": {
        "source_note": "课程知识点：机器学习基础与模型评估",
        "rule": "机器学习让模型从数据中寻找可复用的规律，并用未见数据检验泛化能力；训练集用于学习，验证集用于选择方案，测试集用于最终评估。",
        "example": "垃圾邮件分类器先从已标注邮件中学习，再用未参与训练的邮件检查分类效果。",
        "concept": {"name": "机器学习与模型评估", "explanation": "机器学习让模型从数据中寻找可复用的规律，并用未见数据检验泛化能力。", "applicability": "用于判断模型能否在未见数据上保持表现。"},
        "misconception": {"claim": "训练时看起来很顺，就能证明它在未见数据上也一样可靠。", "correction": "训练表现好只说明学到了训练集；还要用验证集选型、测试集终评来检验泛化。", "cue": "回答只有训练表现，缺少未见数据检验"},
        "knowledge_points": [
            {"title": "三份数据，三种职责", "text": "训练集用来学习规律，验证集用来在候选方案之间选型，测试集只留到最终评估——不能拿它反复调参。"},
            {"title": "记住这个信号", "text": "训练准确率远高于未见数据准确率，几乎总是过拟合的信号：模型记住了训练集，而不是学会了规律。"},
            {"title": "改善泛化的常用做法", "text": "正则化、交叉验证、增加数据、简化模型、早停或数据增强，都能让模型在未见数据上更稳。"},
        ],
        "understanding": {
            "trials": [
                {"type": "short_answer", "question": "用自己的话说明：为什么不能只看模型在训练数据上的表现？", "accepted_groups": [["未见", "新数据", "测试"], ["泛化", "过拟合"]], "hint": "想一想：背下训练样本和真正学会规律有什么不同？", "rationale": "检验用户是否理解训练与泛化的区别", "answer_explanation": "训练表现好只说明学到了训练集；真正可靠的判断要用未见数据检验泛化能力。"},
                {"type": "short_answer", "question": "过拟合和欠拟合，分别对应模型“记太牢”还是“没学会”？请分别说一句。", "accepted_groups": [["过拟合", "记", "背"], ["欠拟合", "没学会", "不足"]], "hint": "一个对训练集太好、对未见数据差；一个连训练集都没学好。", "rationale": "检验对过拟合/欠拟合两个方向的区分", "answer_explanation": "过拟合是记住训练集、未见数据表现差；欠拟合是连训练集都没学好。"},
            ],
            "interleaves": [
                {"type": "multiple_choice", "question": "要公平比较两个候选模型，哪种做法更可靠？", "options": ["使用同一份验证数据比较", "各自在训练集上挑最好成绩", "只比较模型参数数量"], "correct_option": 0, "hint": "比较条件要一致，而且不应拿最终测试集反复调参。", "rationale": "检验模型比较与数据分工的理解", "answer_explanation": "用同一份验证数据比较才公平；测试集只能留到最终评估。"},
                {"type": "multiple_choice", "question": "选型阶段反复用测试集看结果再调参，会带来什么问题？", "options": ["测试集失去最终评估的意义", "没有任何问题", "会让训练更快"], "correct_option": 0, "hint": "测试集应该只在方案确定后使用一次。", "rationale": "检验数据分工的边界意识", "answer_explanation": "反复用测试集调参会让它被模型'记住'，最终评估就失真了。"},
            ],
        },
        "practice": {
            "trials": [
                {"type": "multiple_choice", "question": "垃圾邮件模型训练准确率 99%，新邮件准确率 72%，最可能出现了什么？", "options": ["过拟合", "欠拟合一定不存在", "测试集参与训练太少"], "correct_option": 0, "hint": "训练表现远好于未见数据，是典型信号。", "rationale": "检验过拟合现象的识别", "answer_explanation": "训练远好于未见数据是过拟合的典型信号。"},
                {"type": "multiple_choice", "question": "模型连训练集上的准确率都不高，最可能处于什么状态？", "options": ["欠拟合", "过拟合", "数据完全无噪声"], "correct_option": 0, "hint": "连训练数据都没学好，说明模型容量或方法不足。", "rationale": "检验欠拟合的识别", "answer_explanation": "训练集表现差通常是欠拟合——模型还没学会训练数据里的规律。"},
            ],
            "interleaves": [
                {"type": "short_answer", "question": "给出一种减少过拟合、改善泛化的可执行办法。", "accepted_groups": [["正则", "交叉验证", "更多数据", "简化模型", "早停", "数据增强"]], "hint": "可以从数据、模型复杂度或训练停止时机入手。", "rationale": "检验用户能否给出可执行的反过拟合手段", "answer_explanation": "正则化、交叉验证、增加数据、简化模型、早停或数据增强都能改善泛化。"},
                {"type": "short_answer", "question": "给一个你熟悉的场景：你打算怎样用训练/验证/测试三份数据来评估一个新模型？", "accepted_groups": [["训练", "学习"], ["验证", "选"], ["测试", "评估"]], "hint": "按职责分工说一遍三份数据各自做什么。", "rationale": "检验能否把数据分工迁移到具体场景", "answer_explanation": "训练集学习、验证集选型、测试集最终评估，才是完整的评估流程。"},
            ],
        },
        "remedy": {"type": "multiple_choice", "question": "哪一组用途匹配正确？", "options": ["训练集学习、验证集选型、测试集终评", "训练集终评、测试集调参", "验证集只用于保存文件"], "correct_option": 0, "hint": "三份数据承担不同职责。", "rationale": "检验训练/验证/测试数据分工", "answer_explanation": "训练集学习、验证集选型、测试集终评才是正确分工。"},
        "boss": [
            {"label": "解释", "type": "short_answer", "question": "向同伴解释什么是泛化能力。", "accepted_groups": [["新数据", "未见"], ["表现", "预测", "规律"]], "hint": "用“未见数据上的表现”组织一句话。", "rationale": "检验概念解释能力", "answer_explanation": "泛化能力是模型在未见数据上的预测表现。"},
            {"label": "辨析", "type": "multiple_choice", "question": "模型调参与最终评估分别主要使用什么？", "options": ["验证集、测试集", "测试集、训练集", "训练集、验证集"], "correct_option": 0, "hint": "最终测试应尽量只在方案确定后使用。", "rationale": "检验数据分工的辨析", "answer_explanation": "调参用验证集，最终评估用测试集。"},
            {"label": "迁移", "type": "short_answer", "question": "若训练分数持续上升而验证分数下降，你会采取什么措施，并说明理由。", "accepted_groups": [["早停", "正则", "简化", "数据增强", "更多数据"], ["过拟合", "泛化"]], "hint": "先判断现象，再给出针对模型复杂度或数据的动作。", "rationale": "检验用户能否诊断过拟合并给出行动", "answer_explanation": "这是过拟合信号，可用早停、正则化、简化模型或增加数据改善泛化。"},
        ],
    },
    "pythonloop": {
        "source_note": "课程知识点：Python 循环",
        "rule": "for 循环适合遍历序列；while 循环会在条件为真时重复执行，必须让条件最终能够结束。",
        "example": "for fruit in fruits: print(fruit)",
        "concept": {"name": "Python 循环", "explanation": "for 适合遍历序列，while 由条件控制重复，必须保证条件能走向结束。", "applicability": "用于判断何时选 for、何时选 while。"},
        "misconception": {"claim": "循环只需要开始条件，不需要考虑它能否结束。", "correction": "while 循环必须让条件有机会变为假，否则会无限循环。", "cue": "回答只提循环开始，缺少终止条件"},
        "knowledge_points": [
            {"title": "两条路，两种选择", "text": "for 适合遍历序列（列表、字符串、字典键），while 由条件控制重复——什么时候该用哪个，看你要不要'数完一遍'。"},
            {"title": "循环最怕什么", "text": "while 的条件必须有机会变为假，否则就是无限循环。循环体内要更新条件，或用 break 兜底退出。"},
            {"title": "控制循环的两个词", "text": "break 立刻跳出整个循环，continue 跳过本次继续下一次。写轮询、查找时它们很常用。"},
        ],
        "understanding": {
            "trials": [
                {"type": "short_answer", "question": "说明 for 与 while 的主要使用差别。", "accepted_groups": [["for", "遍历"], ["while", "条件"]], "hint": "一个常从集合中逐项取值，一个由条件控制。", "rationale": "检验循环类型理解", "answer_explanation": "for 常用来遍历序列，while 由条件控制重复。"},
                {"type": "multiple_choice", "question": "要遍历字典的键值对，应优先选择哪种写法？", "options": ["for 循环", "while 死循环", "if 判断"], "correct_option": 0, "hint": "遍历字典键值对属于'逐项取值'。", "rationale": "检验遍历对象的循环选择", "answer_explanation": "遍历字典键值对优先用 for。"},
            ],
            "interleaves": [
                {"type": "multiple_choice", "question": "要依次读取列表中的元素，应优先选择？", "options": ["for", "if", "def"], "correct_option": 0, "hint": "这是遍历序列。", "rationale": "检验遍历选择", "answer_explanation": "遍历列表优先用 for。"},
                {"type": "multiple_choice", "question": "哪段代码会让 while 永远无法结束？", "options": ["while x < 3: x += 1", "while True: print('hi')", "for i in range(3)"], "correct_option": 1, "hint": "条件是恒真的 True，循环体里也没有让它变假的更新。", "rationale": "检验无限循环的识别", "answer_explanation": "while True 且没有 break，条件永远不会变假，必然无限循环。"},
            ],
        },
        "practice": {
            "trials": [
                {"type": "multiple_choice", "question": "哪段代码最直接遍历 items？", "options": ["for item in items:", "if item in items:", "def item(items):"], "correct_option": 0, "hint": "寻找循环关键字。", "rationale": "检验代码识别", "answer_explanation": "for item in items 是遍历。"},
                {"type": "multiple_choice", "question": "break 在循环里的作用是？", "options": ["立刻跳出当前循环", "跳过本次继续下一次", "结束整个程序"], "correct_option": 0, "hint": "break 和 continue 不同。", "rationale": "检验 break 语义", "answer_explanation": "break 立刻终止当前循环；continue 才是跳过本次继续。"},
            ],
            "interleaves": [
                {"type": "short_answer", "question": "写出避免 while 无限循环的关键做法。", "accepted_groups": [["结束条件", "更新条件", "break"]], "hint": "循环内部必须推动条件走向结束。", "rationale": "检验终止条件理解", "answer_explanation": "循环内部更新条件或使用 break 来保证终止。"},
                {"type": "short_answer", "question": "range(3) 会生成哪几个数？如果要生成 1、2、3 该写什么？", "accepted_groups": [["0", "1", "2"], ["range(1", "4)"]], "hint": "range 默认从 0 开始、不含终点。", "rationale": "检验 range 语义", "answer_explanation": "range(3) 生成 0、1、2；要 1、2、3 用 range(1, 4)。"},
            ],
        },
        "remedy": {"type": "multiple_choice", "question": "哪个说法正确？", "options": ["while 的条件需要有机会变为假", "for 只能循环数字", "循环不需要结束条件"], "correct_option": 0, "hint": "关注可终止性。", "rationale": "检验无限循环风险", "answer_explanation": "while 条件必须有机会变为假。"},
        "boss": [
            {"label": "解释", "type": "short_answer", "question": "解释 for 与 while 的使用差别。", "accepted_groups": [["for", "遍历"], ["while", "条件"]], "hint": "分别说清遍历对象和控制条件。", "rationale": "检验概念解释", "answer_explanation": "for 遍历序列，while 由条件控制。"},
            {"label": "辨析", "type": "multiple_choice", "question": "以下哪个最可能导致无限循环？", "options": ["while 条件始终为真且不更新", "for 遍历有限列表", "if 判断一次"], "correct_option": 0, "hint": "找不到走向终止的变化。", "rationale": "检验无限循环诊断", "answer_explanation": "while 条件恒真且不更新会无限循环。"},
            {"label": "迁移", "type": "short_answer", "question": "轮询任务状态时，怎样既重复检查又避免无限运行？", "accepted_groups": [["while", "条件"], ["次数", "超时", "break", "结束"]], "hint": "说明循环条件以及兜底退出机制。", "rationale": "检验迁移应用", "answer_explanation": "用 while 检查条件，同时设定次数或超时上限，或用 break 兜底退出。"},
        ],
    },
}


def _normalise(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _domain(topic: str) -> str:
    """轻量领域推断：教学确认题按领域出题。"""
    text = str(topic or "").lower()
    if any(t in text for t in ("python", "java", "javascript", "代码", "编程", "循环", "算法", "数据库", "机器学习", "machinelearning", "模型", "数据")):
        return "programming"
    if any(t in text for t in ("英语", "英文", "语法", "写作", "口语", "翻译", "表达", "词汇")):
        return "language"
    if any(t in text for t in ("数学", "函数", "梯度", "概率", "几何", "代数", "微积分", "方程", "向量", "导数")):
        return "math"
    if any(t in text for t in ("物理", "化学", "生物", "实验", "力学", "细胞", "反应", "量子")):
        return "science"
    if any(t in text for t in ("历史", "政治", "哲学", "经济", "文学", "社会", "文化", "三国")):
        return "humanities"
    return "open"


# 教学确认题：看完卷轴规则后，用一道选择题确认"真的理解了"，答对才放行。
_TEACHING_CHECK: dict[str, dict[str, Any]] = {
    "programming": {
        "question": "看完这条规则，你认为怎样才算真正理解了它？",
        "options": ["能说明它解决什么问题，并给出一个可验证的例子", "能把这句话背下来", "看过一遍就算懂"],
        "correct_option": 0,
        "hint": "真正的理解需要能解释用途，并给出可检验的例子。",
        "answer_explanation": "理解不是背下规则，而是能说清它解决什么问题，并给出一个可验证的例子。",
    },
    "language": {
        "question": "要确认你真正掌握了这条表达规则，最可靠的做法是？",
        "options": ["把它用在一个具体情境里，检查是否自然准确", "记住它的例句", "默写一遍"],
        "correct_option": 0,
        "hint": "语境中的实际运用是检验表达理解的最好方式。",
        "answer_explanation": "表达规则要在具体对象和语境中运用，才能检验是否真正掌握。",
    },
    "math": {
        "question": "关于这条数学规则，哪一项最接近真正的理解？",
        "options": ["知道它何时成立、何时不成立，并能用例子验证", "记住公式", "会做同一道题"],
        "correct_option": 0,
        "hint": "数学理解的核心是适用条件与可验证的例子。",
        "answer_explanation": "理解数学规则需要知道它的适用条件，并能用例子（含反例）验证。",
    },
    "science": {
        "question": "要确认你理解了这条科学规律，应该做到？",
        "options": ["能说明机制，并能预测一个可观察的结果来检验", "记住结论", "看过实验演示"],
        "correct_option": 0,
        "hint": "科学理解要能解释机制，并用可观察结果检验。",
        "answer_explanation": "理解科学规律需要说明机制，并给出可检验的预测。",
    },
    "humanities": {
        "question": "对这条关于文本/论述的规则，怎样算真正理解？",
        "options": ["能把它放回语境，并用材料支撑自己的解释", "记住观点", "复述原文"],
        "correct_option": 0,
        "hint": "人文理解需要放回语境，并用材料与推理支撑。",
        "answer_explanation": "理解论述需要结合语境与材料，而不是复述表面观点。",
    },
    "open": {
        "question": "看完这条规则，怎样才算真正理解了它？",
        "options": ["能说出它解决什么问题，并给出一个可验证的例子", "能背下这句话", "看过一遍"],
        "correct_option": 0,
        "hint": "真正的理解需要能说明用途，并给出可验证的例子。",
        "answer_explanation": "理解不是背下规则，而是能说清它解决什么问题，并给出可验证的例子。",
    },
}


def teaching_check(topic: str) -> dict[str, Any]:
    """生成教学确认题：检验玩家是否真正理解卷轴规则。

    带三问阶梯字段：hint(方向) -> wrong_guidance/second_attempt(讲解) -> answer_explanation(完整答案)。
    """
    domain = _domain(topic)
    item = dict(_TEACHING_CHECK.get(domain, _TEACHING_CHECK["open"]))
    item["type"] = "multiple_choice"
    item.setdefault("wrong_guidance", item.get("hint", ""))
    item.setdefault("second_attempt", item.get("answer_explanation", ""))
    item.setdefault("scaffold", ["先把题目说清楚，它到底在问什么", "对照刚才卷轴上的规则找关键点", "用规则选出一个可验证的说法"])
    return item


_SECTION_ALIASES = (("trial", "trials"), ("interleave", "interleaves"))


def _with_singular_sections(deck: dict[str, Any]) -> dict[str, Any]:
    """Expose the singular section key beside the plural one.

    Curated cards and the local fallback write `trials`/`interleaves`, while the
    planner adapter writes `trial`/`interleave`. The adventure engine and the game
    store read the singular form, so every deck must carry both shapes.
    """
    for name in ("understanding", "practice"):
        section = deck.get(name)
        if not isinstance(section, dict):
            continue
        section = dict(section)  # never mutate a shared curated card
        for singular, plural in _SECTION_ALIASES:
            if singular in section:
                continue
            items = section.get(plural)
            if isinstance(items, list) and items:
                section[singular] = items[0]
            elif isinstance(items, dict):
                section[singular] = items
        deck[name] = section
    return deck


def _topic_deck(topic: str) -> dict[str, Any]:
    """任意主题 -> 学习蓝图（教学卡 + 题目 + 判定标准）。

    精选卡按关键词匹配（机器学习/Python循环）；其余走领域感知生成器，
    主题词注入规则卡，保证"在谈这个主题"。
    """
    key = _normalise(topic)
    if any(token in key for token in ("机器学习", "machinelearning", "模型评估", "泛化")):
        return _with_singular_sections(dict(_CURATED["machinelearningbasics"]))
    if "python" in key and any(token in key for token in ("循环", "loop")):
        return _with_singular_sections(dict(_CURATED["pythonloop"]))
    safe = topic.strip()[:80] or "本次主题"
    try:
        request = PlannerRequest(topic_hint=safe)
        plan = build_fallback_plan(request, DifficultyLevel.STANDARD)
        deck = knowledge_plan_to_deck(plan)
        deck["rule"] = f"关于“{safe}”：{deck['rule']}"
        return _with_singular_sections(deck)
    except Exception:
        return _with_singular_sections({
            "source_note": "用户当前主题；通用学习脚手架",
            "rule": f"学习“{safe}”时，先说明核心含义和适用条件，再用可验证例子检查理解。",
            "example": "先解释是什么，再判断何时使用，最后用例子验证。",
            "concept": {"name": safe[:36], "explanation": f"先说明“{safe}”的核心含义，再列出成立条件，最后用一个例子验证。", "applicability": ""},
            "misconception": {"claim": "给出结论就等于完成了解释。", "correction": "解释还需要说明为什么成立、在什么条件下成立以及何时不成立。", "cue": ""},
            "knowledge_points": [
                {"title": "核心含义", "text": f"“{safe}”先要能说清它是什么、解决什么问题，再谈怎么用。"},
                {"title": "成立条件", "text": "任何理解都要带上适用条件——在什么情况下成立、什么情况下不成立。"},
                {"title": "用例子检验", "text": "给一个具体例子验证理解，比背下定义更能说明真的会了。"},
            ],
            "understanding": {
                "trials": [
                    {"type": "short_answer", "question": f"用自己的话说明“{safe}”的核心含义，并指出它解决什么问题。", "accepted_groups": [[safe], ["含义", "作用", "问题"]], "hint": "先说是什么，再说它帮助我们判断或完成什么。"},
                    {"type": "short_answer", "question": f"在什么条件下“{safe}”会失效或不该使用？", "accepted_groups": [["条件", "不成立", "失效", "例外"]], "hint": "想一想边界：哪种情况它不适用。"},
                ],
                "interleaves": [
                    {"type": "multiple_choice", "question": "迁移到新问题前，最先应确认什么？", "options": ["适用条件是否一致", "答案字数是否相同", "页面颜色是否相同"], "correct_option": 0, "hint": "迁移依赖条件，而不是表面形式。"},
                    {"type": "short_answer", "question": f"举一个“{safe}”不适用的小例子，说明为什么不适用。", "accepted_groups": [["条件", "不适用", "例外", "反例"]], "hint": "找到条件不满足的情境。"},
                ],
            },
            "practice": {
                "trials": [
                    {"type": "multiple_choice", "question": f"开始练习“{safe}”前，哪种做法更可靠？", "options": ["先确定一个可检验目标", "直接记住结论", "跳过反馈"], "correct_option": 0, "hint": "目标需要能够被结果验证。"},
                    {"type": "short_answer", "question": f"用一句话说清“{safe}”的适用条件。", "accepted_groups": [[safe], ["条件", "前提"]], "hint": "条件常常藏在'当…时'的句子里。"},
                ],
                "interleaves": [
                    {"type": "short_answer", "question": f"给出一个可以验证“{safe}”理解程度的小任务。", "accepted_groups": [["例", "题", "解释", "实现", "比较"]], "hint": "可以设计一道题、一个例子或一次实现。"},
                    {"type": "short_answer", "question": f"如果别人把“{safe}”用错场景，你会怎样指出？", "accepted_groups": [["条件", "反例", "不适用"]], "hint": "用条件或反例说明哪里不对。"},
                ],
            },
            "remedy": {"type": "multiple_choice", "question": "哪条学习链路最完整？", "options": ["含义→条件→例子→反馈", "只读结论", "只看答案"], "correct_option": 0, "hint": "完整链路应包含验证与反馈。"},
            "boss": [
                {"label": "解释", "type": "short_answer", "question": f"解释“{safe}”的核心含义。", "accepted_groups": [[safe], ["含义", "作用", "条件"]], "hint": "围绕主题关键词说明它是什么。"},
                {"label": "辨析", "type": "multiple_choice", "question": "可靠理解至少需要什么？", "options": ["含义、条件和例子", "只记标题", "只看一次答案"], "correct_option": 0, "hint": "选择能形成完整证据的组合。"},
                {"label": "迁移", "type": "short_answer", "question": f"说明你会怎样把“{safe}”用于一个新问题。", "accepted_groups": [["条件", "例", "步骤", "验证"]], "hint": "描述条件、动作和验证结果。"},
            ],
        })


def _assemble_content(topic: str, deck: dict[str, Any]) -> dict[str, Any]:
    """由 deck 组装完整内容蓝图（build_content 与 LLM 生成共用）。"""
    concept = deck.get("concept") or {
        "name": topic[:36],
        "explanation": deck.get("rule", ""),
        "applicability": "",
    }
    misconception = deck.get("misconception") or {"claim": "", "correction": "", "cue": ""}
    return {
        "title": f"{topic[:36]} · 拾光漫游",
        "subtitle": "一场把'不会'变成'会'的学习冒险。",
        "topic": topic,
        "source_note": deck.get("source_note", ""),
        "scroll": {"rule": deck["rule"], "example": deck["example"], "teaching_check": teaching_check(topic)},
        "understanding": deck["understanding"],
        "practice": deck["practice"],
        "remedy": deck["remedy"],
        "boss": deck["boss"],
        "concept": concept,
        "misconception": misconception,
        "completion_summary": deck.get("completion_summary") or "",
        "next_step": deck.get("next_step") or "",
        "knowledge_points": deck.get("knowledge_points") or [],
    }


def build_content(topic: str) -> dict[str, Any]:
    """完整内容蓝图：教学卡 + 各节点题目 + 结束语（确定性模板）。"""
    deck = _topic_deck(topic)
    return _assemble_content(topic, deck)


async def build_content_llm(topic: str) -> dict[str, Any]:
    """题目智能体（LLM）当场生成主题蓝图；失败时降级到确定性模板。

    优先使用主程序 adventure_planner 的 AdventureKnowledgePlanner（LLM），
    让任意主题都能得到专属题目，而不是套模板。LLM 不可用/超时/解析失败时
    回退 build_content（脚手架模板），保证演示永不中断。
    """
    import asyncio

    safe = str(topic or "").strip()[:80] or "本次主题"
    key = _normalise(safe)
    # 精选卡命中：直接用确定性高质量内容，不依赖 LLM
    if any(token in key for token in ("机器学习", "machinelearning", "模型评估", "泛化")):
        return build_content(topic)
    if "python" in key and any(token in key for token in ("循环", "loop")):
        return build_content(topic)
    try:
        from services.llm_service import get_llm_service
        from agents.adventure_planner.planner import AdventureKnowledgePlanner
        from agents.adventure_planner.models import PlannerRequest

        llm = await get_llm_service()
        planner = AdventureKnowledgePlanner(llm=llm)
        request = PlannerRequest(topic_hint=safe)
        result = await asyncio.wait_for(planner.generate(request), timeout=35)
        plan = result.plan
        if plan is None:
            raise ValueError("planner 未返回计划")
        deck = knowledge_plan_to_deck(plan)
        deck["rule"] = f"关于“{safe}”：{deck['rule']}"
        return _assemble_content(safe, deck)
    except Exception:
        # LLM 不可用或生成失败：降级确定性模板
        return build_content(topic)


def public_content(content: dict[str, Any]) -> dict[str, Any]:
    """公开内容：隐藏答案与答题引导（答错后由服务端组装进 feedback）。"""
    hidden = {"correct_option", "accepted_groups", "answer_explanation", "scaffold", "second_attempt", "wrong_guidance"}

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in hidden}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(content)
