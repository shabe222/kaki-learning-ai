import json
import logging
import re
import unicodedata
from datetime import datetime
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import ExamPaper, StudentProfile, MasteryHistory, GeneratedItem, LearningGraph, GraphNode
from config import settings
from services.knowledge_graph import KNOWLEDGE_GRAPH, CHAPTER_ORDER
from services.material_service import get_stage, STAGE_CONFIG, get_user_chapter_mastery
from services.normalized_learner_state import get_normalized_learner_state
from services.skill_library import skill_prompt_prefix

logger = logging.getLogger(__name__)

EXAM_TYPE_CONFIG = {
    "quiz": {"label": "章节练习", "time_min": 30, "question_types": ["choice", "fill", "short_answer"], "feedback_policy": "per_question", "source_policy": "current_chapter"},
    "mock": {"label": "模拟考试", "time_min": 120, "question_types": ["choice", "fill", "derivation", "calculation", "essay"], "feedback_policy": "after_submit", "source_policy": "selected_scope"},
    "weak_reinforcement": {"label": "薄弱点强化", "time_min": 35, "question_types": ["choice", "fill", "short_answer"], "feedback_policy": "after_submit", "source_policy": "learner_state_and_wrong_answers"},
    "custom": {"label": "自定义测评", "time_min": 60, "question_types": ["choice", "fill", "derivation", "calculation", "essay", "short_answer"], "feedback_policy": "after_submit", "source_policy": "user_selected"},
    # Kept for old records only. This is deliberately not presented as a real-paper bank.
    "real_exam": {"label": "能力测评", "time_min": 90, "question_types": ["choice", "fill", "calculation", "essay"], "feedback_policy": "after_submit", "source_policy": "legacy_generated_assessment"},
    "derivation": {"label": "推导专练", "time_min": 60, "question_types": ["derivation"]},
    "compare": {"label": "算法对比", "time_min": 45, "question_types": ["compare"]},
    "code_lab": {"label": "代码实验", "time_min": 90, "question_types": ["code"]},
    "report": {"label": "实验报告", "time_min": 120, "question_types": ["report"]},
}

PUBLIC_QUESTION_PRIVATE_FIELDS = {
    "answer", "correct_answer", "accepted_answers", "explanation", "key_points",
    "rubric", "solution", "reference_answer", "matched_points", "missing_points",
}


def public_exam_questions(questions: list | None) -> list[dict]:
    """Return a student-safe paper projection with every solution field removed."""
    return [
        {key: value for key, value in dict(question or {}).items() if key not in PUBLIC_QUESTION_PRIVATE_FIELDS}
        for question in list(questions or [])
    ]


def exam_mode_contract(exam_type: str, *, source_kind: str | None = None, specification: str | None = None, difficulty: str | None = None) -> dict:
    config = EXAM_TYPE_CONFIG.get(exam_type, EXAM_TYPE_CONFIG["custom"])
    return {
        "contract_version": "exam-mode-v1",
        "mode": exam_type,
        "label": config["label"],
        "feedback_policy": config.get("feedback_policy", "after_submit"),
        "source_policy": config.get("source_policy", "selected_scope"),
        "source_kind": source_kind or config.get("source_policy", "selected_scope"),
        "specification": specification or "standard",
        "difficulty": difficulty or "standard",
        "solutions_hidden_until": "question_check" if exam_type == "quiz" else "submission",
    }

QUESTION_TYPE_WEIGHTS = {
    "mock": {"choice": 30, "fill": 20, "derivation": 15, "calculation": 20, "essay": 15},
    "derivation": {"derivation": 100},
    "compare": {"compare": 100},
    "code_lab": {"code": 100},
    "report": {"report": 100},
}

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


# 中文题型 → 内部 type 字段映射
_QTYPE_CN_TO_INTERNAL = {
    "单选题": "choice",
    "多选题": "multi_choice",
    "判断题": "judge",
    "填空题": "fill",
    "简答题": "short_answer",
    "编程题": "code",
    "essay": "short_answer",  # 旧字段兼容
    "calculation": "short_answer",  # 计算题统一归类到简答
    "derivation": "short_answer",  # 推导题统一归类到简答
    "single_choice": "choice",
    "true_false": "judge",
    "fill_blank": "fill",
}


def _normalize_qtype_name(name: str) -> str:
    """中文题型 / 内部 type 字段 → 标准内部标识"""
    if not name:
        return ""
    name = str(name).strip()
    if name in _QTYPE_CN_TO_INTERNAL:
        return _QTYPE_CN_TO_INTERNAL[name]
    if name in _QTYPE_CN_TO_INTERNAL.values():
        return name
    return name.lower()


def _safe_text(value, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return default
    return str(value).strip() or default


def _first_chapter_id(question: dict, chapter_ids: list) -> str:
    """题目 → 章节归属：优先用题目自带 chapter_id，否则从 chapter_ids 列表中
    按题目索引轮询分配，确保多章节试卷每章都有题。"""
    raw = question.get("chapter_id")
    if not raw and isinstance(question.get("chapter_ids"), list) and question["chapter_ids"]:
        raw = question["chapter_ids"][0]
    if not raw and chapter_ids:
        raw = chapter_ids[0]
    return _safe_text(raw, "ml_overview")


def _distribute_chapter(idx: int, chapter_ids: list) -> str:
    """根据题号 idx 在 chapter_ids 列表中轮询分配章节。"""
    if not chapter_ids:
        return "ml_overview"
    return chapter_ids[idx % len(chapter_ids)]


def _chapter_name(chapter_id: str) -> str:
    chapter = KNOWLEDGE_GRAPH.get(chapter_id, {})
    return chapter.get("name") or chapter_id or "机器学习"


def _default_knowledge_point(chapter_id: str) -> str:
    chapter = KNOWLEDGE_GRAPH.get(chapter_id, {})
    points = chapter.get("knowledge_points") or []
    if points and isinstance(points[0], dict):
        return points[0].get("name") or _chapter_name(chapter_id)
    return _chapter_name(chapter_id)


def _coerce_points(value, default: int = 10) -> int:
    try:
        points = int(float(value))
    except (TypeError, ValueError):
        points = default
    return max(1, min(points, 100))


def _normalize_answer(question: dict, options: list) -> str:
    answer = question.get("answer", question.get("correct_answer", question.get("reference_answer", "")))
    if question.get("type") == "choice":
        if isinstance(answer, int) and 0 <= answer < len(LETTERS):
            return LETTERS[answer]
        answer_text = _safe_text(answer)
        if answer_text.isdigit():
            idx = int(answer_text)
            if 0 <= idx < len(LETTERS):
                return LETTERS[idx]
        if len(answer_text) == 1 and answer_text.upper() in LETTERS:
            return answer_text.upper()
        for idx, option in enumerate(options):
            if answer_text and answer_text == option:
                return LETTERS[idx]
        return "A" if options else answer_text
    if isinstance(answer, list):
        return "；".join(_safe_text(item) for item in answer if _safe_text(item))
    return _safe_text(answer)


def _normalize_question(question: dict, exam_type: str, chapter_ids: list, index: int = 0) -> dict:
    """Normalize generated questions into the display/grading contract."""
    raw = question if isinstance(question, dict) else {}
    q_type = _normalize_qtype_name(_safe_text(raw.get("type"), "short_answer"))
    chapter_id = _first_chapter_id(raw, chapter_ids)
    knowledge_point = _safe_text(raw.get("knowledge_point"), _default_knowledge_point(chapter_id))
    points = _coerce_points(raw.get("points", raw.get("score", raw.get("total_points", 10))))

    options = []
    for opt in raw.get("options") or []:
        opt_text = _safe_text(opt)
        if opt_text:
            options.append(opt_text)
    if q_type == "choice" and len(options) < 2:
        options = [
            "A. 使用有标签样本学习输入到输出的映射",
            "B. 完全不需要训练数据",
            "C. 只能处理文本任务",
            "D. 不需要评估泛化能力",
        ]
    elif q_type == "judge" and len(options) < 2:
        options = ["A. 正确", "B. 错误"]

    content = _safe_text(
        raw.get("content"),
        _safe_text(raw.get("title"), f"请完成关于{knowledge_point}的{EXAM_TYPE_CONFIG.get(exam_type, {}).get('label', exam_type)}题。"),
    )
    title = _safe_text(raw.get("title"), content)
    answer = _normalize_answer({**raw, "type": q_type}, options)
    if not answer and q_type in {"short_answer", "fill", "calculation"}:
        answer = "围绕核心概念、适用条件和关键步骤作答。"

    explanation = _safe_text(raw.get("explanation"))
    if not explanation:
        if q_type == "choice":
            explanation = f"本题检验{knowledge_point}的关键判断，正确选项需要符合课程定义与适用条件。"
        elif raw.get("key_points"):
            explanation = "评分时关注：" + "；".join(_safe_text(p) for p in raw.get("key_points", []) if _safe_text(p))
        elif raw.get("steps"):
            explanation = "按步骤检查推导过程、关键公式和结论是否完整。"
        elif raw.get("sections"):
            explanation = "报告需要覆盖实验目的、步骤、结果分析和讨论反思。"
        else:
            explanation = f"本题用于检验{knowledge_point}的理解、应用与表达完整度。"

    normalized = dict(raw)
    normalized.update({
        "id": _safe_text(raw.get("id", raw.get("question_id")), f"q{index + 1}"),
        "type": q_type,
        "title": title,
        "content": content,
        "chapter_id": chapter_id,
        "knowledge_point": knowledge_point,
        "points": points,
        "answer": answer,
        "explanation": explanation,
        "quality_tags": sorted(set((raw.get("quality_tags") or []) + ["normalized"])),
    })
    if options:
        normalized["options"] = options
    return normalized


def _has_minimum_raw_quality(question: dict) -> bool:
    if not isinstance(question, dict):
        return False
    content = _safe_text(question.get("content"), _safe_text(question.get("title"), _safe_text(question.get("description"))))
    if not content:
        return False
    q_type = _safe_text(question.get("type"), "short_answer")
    if q_type == "choice":
        return len([opt for opt in question.get("options") or [] if _safe_text(opt)]) >= 2
    if q_type in {"derivation", "compare", "code", "report"}:
        return bool(question.get("steps") or question.get("dimensions") or question.get("sections") or question.get("skeleton"))
    return bool(question.get("answer") or question.get("key_points") or question.get("explanation"))


def _quality_check_questions(
    questions: list,
    exam_type: str,
    chapter_ids: list,
    allow_fallback: bool = True,
) -> list:
    if not isinstance(questions, list) or not questions:
        if not allow_fallback:
            raise ValueError("LLM did not return exam questions")
        return _fallback_questions(exam_type, chapter_ids)
    if not all(_has_minimum_raw_quality(q) for q in questions):
        if not allow_fallback:
            raise ValueError("LLM exam questions failed quality checks")
        logger.warning("[Exam] 生成题目未达到最低质量要求，使用降级题目")
        return _fallback_questions(exam_type, chapter_ids)
    return [_normalize_question(q, exam_type, chapter_ids, i) for i, q in enumerate(questions)]

def _build_exam_prompt(exam_type: str, chapter_ids: list, chapter_scores: dict, profile: dict = None, user_id: int = 0, difficulty: str = None, question_types: list = None, count: int = None, user_instruction: str = None) -> str:
    chapters_info = []
    for ch_id in chapter_ids:
        ch = KNOWLEDGE_GRAPH.get(ch_id, {})
        score = chapter_scores.get(ch_id, 0)
        mastery = score / 100 * 5
        stage = get_stage(mastery)
        stage_cfg = STAGE_CONFIG.get(stage, STAGE_CONFIG["seedling"])
        kps = [kp["name"] for kp in ch.get("knowledge_points", [])]
        chapters_info.append(f"- {ch.get('icon','')} {ch.get('name',ch_id)}: 掌握度{mastery:.1f}/5.0, 阶段{stage_cfg['label']}, 知识点: {', '.join(kps)}")

    chapters_text = "\n".join(chapters_info)
    type_cfg = EXAM_TYPE_CONFIG.get(exam_type, EXAM_TYPE_CONFIG["mock"])
    weights = QUESTION_TYPE_WEIGHTS.get(exam_type, QUESTION_TYPE_WEIGHTS["mock"])
    profile_text = ""
    if profile:
        normalized_state = dict(profile.get("normalized_learner_state") or {})
        weak = list(normalized_state.get("weak_points") or [])
        cap = profile.get("capability_matrix") or {}
        if not weak and isinstance(cap, dict):
            for ch_id, ch_data in cap.items():
                if isinstance(ch_data, dict):
                    kp_map = ch_data.get("知识点掌握度", ch_data)
                    if isinstance(kp_map, dict):
                        for kp, v in kp_map.items():
                            if isinstance(v, dict) and v.get("score", 0) <= 2:
                                weak.append(f"{ch_id}/{kp}")
        profile_text = (
            f"\n学生画像: 专业={profile.get('major','未知')}, "
            f"年级={profile.get('grade_level','未知')}, "
            f"目标={profile.get('target_role','未知')}, "
            f"偏好语言={profile.get('preferred_language','Python')}"
        )
        if weak:
            profile_text += f"\n薄弱知识点（重点出题）: {', '.join(weak[:10])}"
    if user_id:
        profile_text += f"\n[exam_user_id={user_id} | exam_type={exam_type} | chapter_ids={','.join(chapter_ids)}]"

    # 用户自定义约束：难度 / 题型 / 数量 → 直接拼到 prompt 头部，影响出题
    custom_constraints = []
    if difficulty and difficulty not in ("全部", "all", ""):
        custom_constraints.append(f"难度偏好：{difficulty}（围绕此难度出题）")
    if question_types and len(question_types) > 0:
        type_map = {"单选题": "choice", "多选题": "multi_choice", "判断题": "judge", "填空题": "fill", "简答题": "short_answer", "编程题": "code"}
        mapped = [type_map.get(t, t) for t in question_types]
        custom_constraints.append(f"题型约束：仅生成 {', '.join(question_types)}（type 字段使用 {', '.join(mapped)}）")
    if count and count > 0:
        custom_constraints.append(f"题目数量：{count} 道（严格控制）")
    if user_instruction:
        custom_constraints.append(
            "用户确认的原始要求：%s。必须忠实执行其中的具体题干、范围和表达；"
            "不要擅自扩展成证明题、进阶题或其他知识点。" % user_instruction.strip()
        )
    custom_text = ("\n" + "\n".join(f"- {c}" for c in custom_constraints)) if custom_constraints else ""

    if exam_type == "derivation":
        return f"""你是机器学习课程推导教练。为以下章节生成逐步推导题，每道题包含多个步骤，每步有提示和答案。

章节信息：
{chapters_text}
{profile_text}

要求：
1. 生成3道推导题，覆盖核心知识点
2. 每道题格式：
{{
  "type": "derivation",
  "title": "推导题标题",
  "content": "题目描述",
  "steps": [
    {{"step": 1, "instruction": "写出假设函数", "hint": "回顾线性模型的形式", "answer": "h(x)=w^Tx+b", "points": 5}},
    {{"step": 2, "instruction": "写出损失函数", "hint": "使用均方误差", "answer": "J(w)=1/2m Σ(h(x_i)-y_i)^2", "points": 5}},
    ...
  ],
  "total_points": 15,
  "difficulty": 3,
  "chapter_id": "章节ID",
  "knowledge_point": "知识点名"
}}

请严格返回JSON数组，不要其他文字。"""

    elif exam_type == "compare":
        return f"""你是机器学习课程算法对比专家。为以下章节生成算法对比分析题。

章节信息：
{chapters_text}
{profile_text}

要求：
1. 生成2组算法对比，每组对比2-3个算法
2. 每组格式：
{{
  "type": "compare",
  "title": "算法对比标题",
  "algorithms": ["算法A", "算法B"],
  "dimensions": [
    {{"name": "目标函数", "values": ["算法A的目标", "算法B的目标"]}},
    {{"name": "适用场景", "values": ["算法A的场景", "算法B的场景"]}},
    {{"name": "优缺点", "values": ["算法A的优缺点", "算法B的优缺点"]}}
  ],
  "questions": [
    {{"content": "以下场景适合用哪个算法？", "options": ["算法A", "算法B", "两者皆可", "两者都不"], "answer": 0, "explanation": "原因..."}}
  ],
  "chapter_ids": ["章节ID1", "章节ID2"],
  "difficulty": 3
}}

请严格返回JSON数组。"""

    elif exam_type == "code_lab":
        return f"""你是机器学习课程代码实验教练。为以下章节生成代码实验题。

章节信息：
{chapters_text}
{profile_text}

要求：
1. 生成2道代码实验题
2. 每道题格式：
{{
  "type": "code",
  "title": "代码实验标题",
  "description": "实验描述和目标",
  "skeleton": "Python代码骨架，关键部分用TODO标记",
  "hints": ["提示1", "提示2"],
  "test_cases": [
    {{"input": "测试输入描述", "expected_output": "期望输出描述"}}
  ],
  "reference_solution": "完整参考实现",
  "chapter_id": "章节ID",
  "difficulty": 3,
  "estimated_min": 30
}}

请严格返回JSON数组。"""

    elif exam_type == "report":
        return f"""你是机器学习课程实验报告助手。为以下章节生成实验报告框架。

章节信息：
{chapters_text}
{profile_text}

要求：
1. 生成1份实验报告框架
2. 格式：
{{
  "type": "report",
  "title": "实验报告标题",
  "sections": [
    {{"heading": "一、实验目的", "content": "实验目的描述", "template": "掌握...的原理和实现"}},
    {{"heading": "二、实验环境", "content": "", "template": "Python 3.10, scikit-learn, numpy, matplotlib"}},
    {{"heading": "三、实验步骤", "content": "", "subsections": [
      {{"title": "3.1 数据准备", "template": "# 加载数据\\nfrom sklearn.datasets import ..."}},
      {{"title": "3.2 模型训练", "template": "# 训练模型\\nfrom sklearn.xxx import ..."}},
      {{"title": "3.3 结果分析", "template": "# 可视化和评估"}}
    ]}},
    {{"heading": "四、实验结果", "content": "", "template": "插入实验截图和表格"}},
    {{"heading": "五、分析与讨论", "content": "", "questions": ["为什么...", "如果改变...会怎样？"]}}
  ],
  "chapter_ids": ["章节ID"],
  "difficulty": 3
}}

请严格返回JSON。"""

    else:
        # 章节测验使用精简 prompt，加速生成
        # 章节范围：列出所有选中章节，告知题量分配
        if not chapter_ids:
            chapter_ids = ["ml_overview"]
        # 题目分配：每章均分，剩余题加到第一章
        per_chapter = max(1, (count if (count and count > 0) else 3) // len(chapter_ids))
        chapter_names = [_chapter_name(c) for c in chapter_ids]
        chapters_overview = "、".join(chapter_names)
        # 题型约束：转成可读列表
        if question_types and len(question_types) > 0:
            types_hint = " / ".join(question_types)
        else:
            types_hint = "选择题 / 填空题 / 简答题"
        # 难度约束
        if difficulty and difficulty not in ("全部", "all", ""):
            diff_hint = f"难度：{difficulty}"
        else:
            diff_hint = "难度：覆盖 简单 / 中等 / 困难 三档"
        # 题数约束
        target_count = count if (count and count > 0) else 3
        # 题型分布
        type_distribution_hint = ""
        if question_types and len(question_types) > 0:
            n = len(question_types)
            per = max(1, target_count // n)
            r = target_count - per * n
            parts = []
            for i, t in enumerate(question_types):
                parts.append(f"{t} {per + (1 if i < r else 0)} 道")
            type_distribution_hint = "题型分布：" + "，".join(parts) + "。"
        chapter_distribution_hint = f"章节分配：每章 {per_chapter} 道，优先覆盖核心知识点。共 {len(chapter_ids)} 章 · 章节：{chapters_overview}。"
        return f"""为「{chapters_overview}」共 {len(chapter_ids)} 章生成 {target_count} 道测验题。
{diff_hint}
{chapter_distribution_hint}
{type_distribution_hint}
要求：
1. 严格生成 {target_count} 道题（不多不少），覆盖所选章节的全部核心知识点
2. 题型分布参考上面的「题型分布」要求
3. 每道题必须包含完整字段：type / content / answer / points / chapter_id / knowledge_point / explanation
4. 选择题必须提供 4 个选项（options），answer 用字母 A/B/C/D
5. 多选题 answer 使用 JSON 数组如 ["A","C"]
6. 判断题 answer 用 "A"（正确）或 "B"（错误），options 为 ["A. 正确", "B. 错误"]
7. 填空题用 ___ 表示空，answer 给标准答案
8. 简答题 answer 给要点 + 完整参考回答
9. 编程题 answer 给参考实现代码
10. chapter_id 必须使用以下之一：{', '.join(chapter_ids)}
11. 严格返回 JSON 数组，不要其他文字
{profile_text}{custom_text}"""


def _parse_questions(raw: str) -> list:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end+1])
            except json.JSONDecodeError as e:
                logger.warning(f"[Exam] 解析 [] 区间 JSON 失败: {e}")
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return [json.loads(text[start:end+1])]
            except json.JSONDecodeError as e:
                logger.warning(f"[Exam] 解析 {{}} 区间 JSON 失败: {e}")
    return []


def _questions_match_user_instruction(questions: list, user_instruction: str = None) -> bool:
    instruction = str(user_instruction or "").strip()
    if not instruction:
        return True
    exact_arithmetic = re.search(
        r"(\d+\s*[+\-×÷*/]\s*\d+)\s*(?:=|等于)?\s*(?:几|多少|\?)",
        instruction,
    )
    if not exact_arithmetic:
        return True
    expected_expression = re.sub(r"\s+", "", exact_arithmetic.group(1))
    forbids_expansion = not re.search(r"证明|归纳法|公理|推导", instruction)
    for question in questions or []:
        content = re.sub(r"\s+", "", str(question.get("content") or question.get("title") or ""))
        if expected_expression not in content:
            return False
        if forbids_expansion and re.search(r"证明|归纳法|皮亚诺|公理化|推导", content):
            return False
    return bool(questions)


def _build_chapter_qbank() -> dict:
    """为每个数据结构和 ML 章节构建题型题库（6 题型 × 多变体）。"""
    return {
        "chapter-1": {
            "name": "第1章 绪论与算法基础",
            "choice": [
                ("下列关于算法时间复杂度的说法，正确的是", ["A. O(1) 表示最坏情况", "B. O(n) 一定比 O(log n) 慢", "C. 渐近记号描述输入规模趋近无穷时的增长", "D. 同一算法的时间复杂度唯一"], "C"),
                ("递归与分治法的关系是", ["A. 递归 = 分治", "B. 分治是策略，递归常作为实现方式", "C. 分治必须用循环", "D. 递归不能用于分治"], "B"),
                ("下列哪个不是算法分析的内容", ["A. 时间复杂度", "B. 空间复杂度", "C. 可读性", "D. 正确性"], "C"),
            ],
            "multi_choice": [
                ("下列属于算法特性的是", ["A. 有穷性", "B. 确定性", "C. 可行性", "D. 随机性"], ["A", "B", "C"]),
                ("以下时间复杂度中随 n 增长最快的是", ["A. O(n)", "B. O(n log n)", "C. O(n²)", "D. O(2ⁿ)"], ["C", "D"]),
            ],
            "judge": [
                ("算法的时间复杂度只取决于算法本身，与输入规模无关", False),
                ("分治法可以将规模为 n 的问题拆分为更小子问题分别求解", True),
            ],
            "fill": [
                ("算法的时间开销记为 T(n)=3n²+5n+10，其渐近时间复杂度为____", "O(n²)"),
                ("一个递归算法若递归深度为 n，则其空间复杂度通常为____", "O(n)"),
            ],
            "short_answer": [
                ("简述时间复杂度与空间复杂度的关系，并举例说明如何取舍", "时间复杂度反映运行时间增长趋势，空间复杂度反映额外存储增长趋势。实际中常通过哈希表以空间换时间，或通过原地算法以时间换空间。"),
                ("什么是渐近记号？O、Ω、Θ 有什么区别", "渐近记号描述函数在 n→∞ 时的增长量级。O 是上界、Ω 是下界、Θ 是紧界（同时给出上界和下界）。"),
            ],
            "code": [
                ("用 Python 编写递归实现斐波那契数列第 n 项", "def fib(n):\n    if n < 2: return n\n    return fib(n-1) + fib(n-2)"),
            ],
        },
        "chapter-2": {
            "name": "第2章 线性表",
            "choice": [
                ("顺序表与链表相比，最主要的优势是", ["A. 插入删除快", "B. 随机访问快", "C. 节省存储", "D. 长度可变"], "B"),
                ("在长度为 n 的顺序表中，删除第 i 个元素需要移动多少元素", ["A. i-1", "B. n-i", "C. n-i+1", "D. n"], "B"),
                ("双向链表相比单链表的优势是", ["A. 节省空间", "B. 可双向遍历", "C. 插入更快", "D. 节省时间"], "B"),
            ],
            "multi_choice": [
                ("下列属于线性表的是", ["A. 顺序表", "B. 链表", "C. 栈", "D. 队列"], ["A", "B", "C", "D"]),
                ("顺序表插入操作的时间复杂度可能是", ["A. O(1)", "B. O(n)", "C. O(log n)", "D. O(n²)"], ["A", "B"]),
            ],
            "judge": [
                ("顺序表的存储空间必须是连续的", True),
                ("链表的随机访问时间复杂度为 O(1)", False),
            ],
            "fill": [
                ("在单链表中，删除节点 p（已知前驱 q）的时间复杂度是____", "O(1)"),
                ("顺序表插入元素的平均移动次数是表长的____", "n/2"),
            ],
            "short_answer": [
                ("比较顺序表与链表在查找、插入、删除操作上的性能差异", "顺序表查找 O(1)，插入/删除 O(n)；链表查找 O(n)，插入/删除 O(1)（已知位置）。"),
                ("什么是循环链表？它有什么应用场景", "循环链表尾节点指针指向头节点。适合环形缓冲区、约瑟夫问题等。"),
            ],
            "code": [
                ("用 Python 实现单链表的反转", "def reverse(head):\n    pre, cur = None, head\n    while cur:\n        nxt = cur.next\n        cur.next = pre\n        pre, cur = cur, nxt\n    return pre"),
            ],
        },
        "chapter-3": {
            "name": "第3章 栈、队列与字符串",
            "choice": [
                ("栈的典型应用不包括下列哪项", ["A. 括号匹配", "B. 表达式求值", "C. 函数调用", "D. 哈希查找"], "D"),
                ("循环队列为了区分队空和队满，通常采用", ["A. 头尾指针相等", "B. 牺牲一个存储单元", "C. 增加 size 字段", "D. 头尾指针相等或 size 字段"], "D"),
                ("字符串匹配 KMP 算法的时间复杂度是", ["A. O(n×m)", "B. O(n+m)", "C. O(n log m)", "D. O(n²)"], "B"),
            ],
            "multi_choice": [
                ("下列属于栈的应用是", ["A. 括号匹配", "B. 中缀转后缀", "C. 浏览器前进后退", "D. 进程调度"], ["A", "B", "C"]),
                ("队列的变体包括", ["A. 双端队列", "B. 循环队列", "C. 优先队列", "D. 链式队列"], ["A", "B", "C", "D"]),
            ],
            "judge": [
                ("栈是后进先出（LIFO）的线性结构", True),
                ("队列的入队和出队操作只能在同一端进行", False),
            ],
            "fill": [
                ("栈的插入操作称为____，删除操作称为____", "入栈（push）|出栈（pop）"),
                ("循环队列中 front=0, rear=5, capacity=8，则队列中有____个元素", "5"),
            ],
            "short_answer": [
                ("为什么循环队列要预留一个空位", "为区分队空（front==rear）和队满状态，避免歧义。"),
                ("简述 KMP 算法的核心思想", "利用已匹配部分的信息，通过 next 数组避免主串指针回退，使复杂度降至 O(n+m)。"),
            ],
            "code": [
                ("用 Python 实现一个支持 min() 查询的栈", "class MinStack:\n    def __init__(self):\n        self.s, self.m = [], []\n    def push(self, x):\n        self.s.append(x)\n        self.m.append(min(x, self.m[-1] if self.m else x))\n    def pop(self):\n        self.s.pop(); self.m.pop()\n    def min(self):\n        return self.m[-1]"),
            ],
        },
        "chapter-4": {
            "name": "第4章 链表",
            "choice": [
                ("判断单链表是否有环的最优方法是", ["A. 遍历计数", "B. 哈希表", "C. 快慢指针", "D. 双重循环"], "C"),
                ("将单链表就地反转后，链表头节点是", ["A. 原来的尾节点", "B. 原来的头节点", "C. NULL", "D. 任意节点"], "A"),
                ("在单链表中，在 p 节点后插入新节点的时间复杂度是", ["A. O(1)", "B. O(n)", "C. O(log n)", "D. O(n²)"], "A"),
            ],
            "multi_choice": [
                ("关于快慢指针说法正确的是", ["A. 可用于检测环", "B. 可用于找链表中点", "C. 必须使用额外空间", "D. 空间复杂度 O(1)"], ["A", "B", "D"]),
                ("有序单链表的归并操作时间复杂度是", ["A. O(n)", "B. O(n log n)", "C. O(n²)", "D. O(log n)"], ["A", "B"]),
            ],
            "judge": [
                ("双向链表的每个节点都包含指向前驱和后继的指针", True),
                ("单链表的随机访问时间复杂度为 O(n)", True),
            ],
            "fill": [
                ("快慢指针算法中，快指针每次走____步，慢指针每次走____步", "2|1"),
                ("判断链表有环后，找到环入口的标准做法是：将快指针重置到头，然后两指针同时每次走____步", "1"),
            ],
            "short_answer": [
                ("描述快慢指针判断链表环的原理", "快指针每次走两步，慢指针每次走一步。若链表有环，快指针必追上慢指针；若无环，快指针先到达 NULL。"),
                ("链表反转的迭代法与递归法各有何优缺点", "迭代法空间 O(1)，代码直观；递归法代码简洁但栈深 O(n)，大数据量易栈溢出。"),
            ],
            "code": [
                ("用 Python 实现判断链表是否有环（快慢指针）", "def hasCycle(head):\n    s, f = head, head\n    while f and f.next:\n        s = s.next; f = f.next.next\n        if s is f: return True\n    return False"),
            ],
        },
        "chapter-5": {
            "name": "第5章 递归与分治",
            "choice": [
                ("快速排序的平均时间复杂度是", ["A. O(n)", "B. O(n log n)", "C. O(n²)", "D. O(log n)"], "B"),
                ("汉诺塔问题的时间复杂度是", ["A. O(n)", "B. O(2ⁿ)", "C. O(n²)", "D. O(n log n)"], "B"),
                ("归并排序的空间复杂度是", ["A. O(1)", "B. O(log n)", "C. O(n)", "D. O(n²)"], "C"),
            ],
            "multi_choice": [
                ("下列属于分治法的算法是", ["A. 快速排序", "B. 归并排序", "C. 二分查找", "D. 冒泡排序"], ["A", "B", "C"]),
                ("递归算法的三要素是", ["A. 终止条件", "B. 递归调用", "C. 状态推进", "D. 循环结构"], ["A", "B", "C"]),
            ],
            "judge": [
                ("斐波那契的递归实现比迭代实现更快", False),
                ("分治法将问题分解为相互独立、规模更小的子问题", True),
            ],
            "fill": [
                ("快速排序的 partition 函数期望时间复杂度是____", "O(n)"),
                ("二分查找的前提是序列必须____", "有序"),
            ],
            "short_answer": [
                ("为什么朴素斐波那契递归效率低？如何优化", "存在大量重复子问题计算，复杂度 O(2ⁿ)。用记忆化（DP 数组）或迭代可将复杂度降至 O(n)。"),
                ("归并排序与快速排序的稳定性与适用场景对比", "归并稳定 O(n log n) 需 O(n) 空间；快排不稳定、原地、平均 O(n log n) 但最坏 O(n²)。"),
            ],
            "code": [
                ("用 Python 实现归并排序", "def merge_sort(a):\n    if len(a) <= 1: return a\n    m = len(a)//2\n    return merge(merge_sort(a[:m]), merge_sort(a[m:]))"),
            ],
        },
        "chapter-6": {
            "name": "第6章 树与二叉树",
            "choice": [
                ("一棵有 n 个节点的二叉树，其空指针域数量为", ["A. n", "B. n+1", "C. 2n", "D. n-1"], "B"),
                ("二叉树的前序遍历顺序是", ["A. 根-左-右", "B. 左-根-右", "C. 左-右-根", "D. 层序"], "A"),
                ("哈夫曼树是", ["A. 完全二叉树", "B. 最优二叉树（带权路径最短）", "C. 平衡二叉树", "D. 搜索树"], "B"),
            ],
            "multi_choice": [
                ("下列属于树的术语是", ["A. 节点的度", "B. 树的深度", "C. 叶子节点", "D. 子树"], ["A", "B", "C", "D"]),
                ("二叉树的遍历方式包括", ["A. 前序", "B. 中序", "C. 后序", "D. 层序"], ["A", "B", "C", "D"]),
            ],
            "judge": [
                ("二叉树的每个节点最多有两个子节点", True),
                ("层序遍历可以用栈实现", False),
            ],
            "fill": [
                ("深度为 k 的满二叉树节点总数为____", "2^k - 1"),
                ("中序遍历一棵二叉搜索树得到的结果是____序列", "升序"),
            ],
            "short_answer": [
                ("简述前序、中序、后序遍历的访问顺序及典型应用", "前序：根-左-右，用于复制树；中序：左-根-右，BST 得升序；后序：左-右-根，用于释放树。"),
                ("什么是堆？最大堆与最小堆的区别", "堆是完全二叉树实现的优先队列。最大堆父≥子（堆顶最大），最小堆父≤子（堆顶最小）。"),
            ],
            "code": [
                ("用 Python 实现二叉树前序遍历（递归）", "def preorder(root):\n    if not root: return\n    yield root.val\n    yield from preorder(root.left)\n    yield from preorder(root.right)"),
            ],
        },
    }


def _fallback_questions(exam_type: str, chapter_ids: list, count: int = None,
                    question_types: list = None) -> list:
    """LLM 不可用时的降级题目：基于 KNOWLEDGE_GRAPH 的真实题库，
    按章节轮询、按题型分配，足量生成（避免循环复用）。"""
    qbank = _build_chapter_qbank()
    # 解析章节：优先用传入的，缺数据时退到 chapter-3
    valid_ids = [c for c in chapter_ids if c in qbank]
    if not valid_ids:
        valid_ids = ["chapter-3"]
    # 题型白名单
    allowed_types = ["choice", "multi_choice", "judge", "fill", "short_answer", "code"]
    if question_types and len(question_types) > 0:
        type_norm = []
        for t in question_types:
            n = _normalize_qtype_name(t)
            if n in allowed_types and n not in type_norm:
                type_norm.append(n)
        if type_norm:
            allowed_types = type_norm
    target_count = count if (count and count > 0) else 3
    out: list = []
    # 独立轮询：章节 idx / 题型 idx，避免 (idx//len(chapters))%len(types) 因章节数 > 题型数
    # 导致前 N 题全落在一个题型上的 bug
    chapter_idx = 0
    type_idx = 0
    type_pointer = {t: 0 for t in allowed_types}
    while len(out) < target_count:
        cid = valid_ids[chapter_idx % len(valid_ids)]
        qtype = allowed_types[type_idx % len(allowed_types)]
        bank = qbank[cid].get(qtype, [])
        if not bank:
            # 该章节没此题型 → 跳到下一章节（保持题型不变）
            chapter_idx += 1
            # 安全保护：循环超过 2 轮还没找到 → 退到该题型的第一题
            if chapter_idx - len(out) > len(valid_ids) * 2:
                qtype = allowed_types[type_idx % len(allowed_types)]
                bank_first = next((qbank[c].get(qtype, []) for c in valid_ids if qbank[c].get(qtype)), [])
                if not bank_first:
                    type_idx += 1
                    chapter_idx = len(out)
                    continue
                v = bank_first[type_pointer[qtype] % len(bank_first)]
                type_pointer[qtype] += 1
                item = _build_fallback_item(qtype, v, valid_ids[0], len(out) + 1)
                out.append(item)
                type_idx += 1
                chapter_idx += 1
                continue
            continue
        v = bank[type_pointer[qtype] % len(bank)]
        type_pointer[qtype] += 1
        item = _build_fallback_item(qtype, v, cid, len(out) + 1)
        out.append(item)
        type_idx += 1
        chapter_idx += 1
    return [_normalize_question(q, exam_type, valid_ids, i) for i, q in enumerate(out)]


def _build_fallback_item(qtype: str, v, chapter_id: str, qnum: int) -> dict:
    """把题库中的一条记录构造成 _normalize_question 能处理的题目。"""
    chapter_label = _chapter_name(chapter_id)
    kp_pool = KNOWLEDGE_GRAPH.get(chapter_id, {}).get("knowledge_points") or []
    kp = (kp_pool[qnum % len(kp_pool)]["name"] if kp_pool else chapter_label) if kp_pool else chapter_label
    base = {
        "id": f"q{qnum}",
        "chapter_id": chapter_id,
        "knowledge_point": kp,
        "quality_tags": ["fallback", "normalized"],
    }
    if qtype == "choice":
        stem, options, answer = v
        return {**base, "type": "choice", "title": f"{chapter_label}·选择题", "content": stem, "options": options, "answer": answer, "points": 5}
    if qtype == "multi_choice":
        stem, options, answer = v
        return {**base, "type": "multi_choice", "title": f"{chapter_label}·多选题", "content": stem, "options": options, "answer": answer, "points": 5}
    if qtype == "judge":
        stem, answer = v
        return {**base, "type": "judge", "title": f"{chapter_label}·判断题", "content": stem, "options": ["A. 正确", "B. 错误"], "answer": "A" if answer else "B", "points": 5}
    if qtype == "fill":
        stem, answer = v
        return {**base, "type": "fill", "title": f"{chapter_label}·填空题", "content": stem, "answer": answer, "points": 5}
    if qtype == "code":
        stem, answer = v
        return {**base, "type": "code", "title": f"{chapter_label}·编程题", "content": stem, "answer": answer, "points": 15}
    # short_answer
    stem, answer = v
    return {**base, "type": "short_answer", "title": f"{chapter_label}·简答题", "content": stem, "answer": answer, "points": 10}


async def create_exam(
    db: AsyncSession,
    user_id: int,
    exam_type: str,
    chapter_ids: list,
    difficulty: str = None,
    question_types: list = None,
    count: int = None,
    user_instruction: str = None,
    source_kind: str = None,
    specification: str = None,
) -> ExamPaper:
    result = await db.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
    profile = result.scalar_one_or_none()
    learner_state = await get_normalized_learner_state(db, user_id)
    chapter_scores = {}
    for chapter_id in chapter_ids:
        scores = [
            value for key, value in learner_state.mastery_by_knowledge_point.items()
            if str(chapter_id).lower() in str(key).lower()
        ]
        if scores:
            # Legacy exam prompt uses a 0..5 chapter scale.
            chapter_scores[chapter_id] = round(5 * sum(scores) / len(scores), 2)
    profile_dict = None
    if profile:
        profile_dict = {
            "major": profile.major,
            "grade_level": profile.grade_level,
            "target_role": profile.target_role,
            "preferred_language": profile.preferred_language,
            "capability_matrix": profile.capability_matrix,
            "normalized_learner_state": {
                "state_version": learner_state.state_version,
                "knowledge_level": learner_state.knowledge_level,
                "weak_points": learner_state.weak_points,
                "preferred_modalities": learner_state.preferred_modalities,
                "practice_preference": learner_state.practice_preference,
                "code_preference": learner_state.code_preference,
            },
        }
    elif learner_state:
        profile_dict = {"normalized_learner_state": learner_state.model_dump(mode="json")}
    type_cfg = EXAM_TYPE_CONFIG.get(exam_type, EXAM_TYPE_CONFIG["mock"])
    # 尝试 LLM 生成题目（启用缓存以加速重复请求）
    prompt = skill_prompt_prefix("question-authoring") + _build_exam_prompt(
        exam_type, chapter_ids, chapter_scores, profile_dict, user_id=user_id,
        difficulty=difficulty, question_types=question_types, count=count,
        user_instruction=user_instruction,
    )
    questions = None
    from services.llm_service import get_llm_service
    try:
        llm = await get_llm_service()
        chat_result = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            cache_agent="exam",
            cache_chapter="|".join(chapter_ids)[:64] if chapter_ids else "all",
            cache_stage=exam_type,
            cache_profile_hash=f"u{user_id}",
            cache_user_id=user_id,
            use_cache=not bool(user_instruction),
            write_cache=not bool(user_instruction),
        )
        raw_response = chat_result.get("content", "")
        questions = _parse_questions(raw_response)
    except RuntimeError as e:
        logger.warning(f"[Exam] LLM 不可用: {e}")
        if settings.MODE == "production":
            raise RuntimeError("Exam LLM service unavailable") from e
    if not questions:
        if settings.MODE == "production":
            raise RuntimeError("Exam LLM returned no usable questions")
        logger.warning(f"[Exam] LLM 返回不可解析或无题目，降级到题库生成")
        questions = _fallback_questions(exam_type, chapter_ids, count=count, question_types=question_types)
    else:
        questions = _quality_check_questions(
            questions,
            exam_type,
            chapter_ids,
            allow_fallback=settings.MODE != "production",
        )

    if not _questions_match_user_instruction(questions, user_instruction):
        raise RuntimeError("Exam LLM result expanded beyond the confirmed user instruction")

    # === 硬约束：生产环境只接受真实 LLM 达标结果，不用题库伪装生成成功 ===
    if question_types and len(question_types) > 0:
        type_norm = {_normalize_qtype_name(t) for t in question_types}
        type_norm.discard("")
        present_types = {_normalize_qtype_name(q.get("type")) for q in questions}
        missing_types = type_norm - present_types
        if settings.MODE == "production" and ((count and len(questions) < count) or missing_types):
            raise RuntimeError("Exam LLM result did not satisfy requested count or question types")
        # 数量不足 OR 缺失任何指定题型 → 强制用题库兜底（按用户题型 + 章节轮询）
        if (count and len(questions) < count) or missing_types:
            logger.info(
                f"[Exam] 题型/数量不达标：现有 {len(questions)} 题 / 类型 {present_types}，"
                f"用户约束 数量={count} / 类型={type_norm}，缺失 {missing_types}，用题库强制兜底"
            )
            questions = _fallback_questions(exam_type, chapter_ids, count=count or len(questions), question_types=question_types)
    elif count and len(questions) < count:
        if settings.MODE == "production":
            raise RuntimeError("Exam LLM returned fewer questions than requested")
        # 用户没指定题型，但指定了数量：按 EXAM_TYPE_CONFIG 默认题型补题库
        questions = _fallback_questions(exam_type, chapter_ids, count=count, question_types=None)

    # === 硬约束：用户指定了多章节时，确保每章都有题 ===
    if chapter_ids and len(chapter_ids) > 1 and count and len(questions) >= len(chapter_ids):
        chapter_counts = {}
        for q in questions:
            cid = q.get("chapter_id") or ""
            chapter_counts[cid] = chapter_counts.get(cid, 0) + 1
        empty_chapters = [c for c in chapter_ids if chapter_counts.get(c, 0) == 0]
        if empty_chapters:
            if settings.MODE == "production":
                raise RuntimeError("Exam LLM result did not cover every requested chapter")
            logger.info(
                f"[Exam] 章节覆盖不足：{empty_chapters} 缺题，"
                f"当前分布 {chapter_counts}，按缺题章节补题库"
            )
            # 用缺题章节 + 用户题型生成补充题
            for ch in empty_chapters:
                fill = _fallback_questions(
                    exam_type, [ch],
                    count=max(1, (count or len(questions)) // len(chapter_ids)),
                    question_types=question_types,
                )
                # 替换末尾空位
                questions = questions[: max(0, count - len(fill))] + fill[: count - max(0, count - len(fill))]
                if len(questions) >= (count or len(questions)):
                    break

    graph = await db.scalar(select(LearningGraph).where(
        LearningGraph.user_id == user_id,
        LearningGraph.is_primary == True,
        LearningGraph.status == "active",
    ).order_by(desc(LearningGraph.updated_at), desc(LearningGraph.id)).limit(1))
    node = None
    if graph is not None:
        node = await db.scalar(select(GraphNode).where(
            GraphNode.graph_id == graph.id,
            GraphNode.user_id == user_id,
            GraphNode.status.notin_(["completed", "hidden"]),
        ).order_by(GraphNode.order_index.asc(), GraphNode.id.asc()).limit(1))
    mode_contract = exam_mode_contract(
        exam_type, source_kind=source_kind, specification=specification, difficulty=difficulty,
    )
    evidence_context = {
        "graph_id": str(graph.external_id or f"graph-{graph.id}") if graph else None,
        "graph_node_external_id": str(node.external_id or f"node-{node.id}") if node else None,
        "graph_node_title": node.title if node else None,
    }
    persisted_snapshot = dict(profile_dict or {})
    persisted_snapshot["exam_mode_contract"] = mode_contract
    persisted_snapshot["evidence_context"] = evidence_context
    persisted_snapshot["learner_state_version"] = learner_state.state_version
    persisted_snapshot["learner_state_weak_points"] = learner_state.weak_points[:10]
    exam = ExamPaper(
        user_id=user_id, exam_type=exam_type, chapter_ids=chapter_ids,
        questions=questions, status="draft", profile_snapshot=persisted_snapshot,
        time_limit_min=type_cfg["time_min"],
        total_score=sum(_coerce_points(q.get("points", q.get("score", 10))) for q in questions),
    )
    db.add(exam)
    await db.flush()
    artifact = GeneratedItem(
        user_id=user_id,
        type="exam_assessment",
        title=f"{type_cfg['label']} · {len(questions)}题",
        chapter="、".join(chapter_ids[:3]),
        content=None,
        raw_data={
            "exam_id": exam.id,
            "exam_mode_contract": mode_contract,
            **evidence_context,
            "authority": "exam_paper",
        },
    )
    db.add(artifact)
    await db.flush()
    exam.profile_snapshot = {**persisted_snapshot, "evidence_artifact_id": artifact.id}
    await db.commit()
    await db.refresh(exam)
    return exam


async def get_exam(db: AsyncSession, exam_id: int) -> ExamPaper:
    result = await db.execute(select(ExamPaper).where(ExamPaper.id == exam_id))
    return result.scalar_one_or_none()


def _submitted_answer(raw):
    if isinstance(raw, dict):
        return raw.get("answer", raw.get("value", raw.get("response")))
    return raw


def _choice_answer_correct(value, expected, options) -> bool:
    if value is None:
        return False
    value_s = str(value).strip()
    expected_s = str(expected).strip()
    if value_s == expected_s:
        return True
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if isinstance(expected, int):
        if value_s == str(expected):
            return True
        if 0 <= expected < len(letters) and value_s.upper() == letters[expected]:
            return True
        if 0 <= expected < len(options) and value_s == str(options[expected]).strip():
            return True
    if expected_s.isdigit():
        idx = int(expected_s)
        if 0 <= idx < len(letters) and value_s.upper() == letters[idx]:
            return True
        if 0 <= idx < len(options) and value_s == str(options[idx]).strip():
            return True
    if len(expected_s) == 1 and expected_s.upper() in letters:
        idx = letters.index(expected_s.upper())
        if value_s == str(idx):
            return True
        if idx < len(options) and value_s == str(options[idx]).strip():
            return True
    return False


def _multi_choice_tokens(value, options) -> set[str]:
    """Normalize multi-choice letters, indices, and full option labels."""
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        text = str(value).strip()
        raw_items = [item for item in re.split(r"[;,；、|/]+", text) if item.strip()]

    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    normalized: set[str] = set()
    option_texts = [str(option).strip() for option in options]
    for raw_item in raw_items:
        item = str(raw_item).strip()
        if not item:
            continue
        if item in option_texts:
            normalized.add(letters[option_texts.index(item)])
            continue
        label_match = re.match(r"^\s*([A-Za-z])(?:\s*[.、:：)）]|\s|$)", item)
        if label_match:
            normalized.add(label_match.group(1).upper())
            continue
        if item.isdigit():
            index = int(item)
            if 0 <= index < len(option_texts):
                normalized.add(letters[index])
            continue
        if len(item) == 1 and item.upper() in letters:
            normalized.add(item.upper())
    return normalized


def _normalize_fill_part(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"\s+", "", text)
    return text.strip("，。！？、；;,.!?")


def _split_fill_answer(value) -> list:
    if isinstance(value, (list, tuple)):
        return [_normalize_fill_part(item) for item in value]
    text = str(value or "")
    return [
        _normalize_fill_part(item)
        for item in re.split(r"[|；;\n]+", text)
        if _normalize_fill_part(item)
    ]


def _score_fill_answer(question: dict, raw_answer, points: float) -> tuple:
    submitted = _split_fill_answer(_submitted_answer(raw_answer))
    expected_values = [question.get("answer", "")]
    expected_values.extend(question.get("accepted_answers") or [])
    best_matched = 0
    best_total = 1
    for expected_value in expected_values:
        expected = _split_fill_answer(expected_value)
        if not expected:
            continue
        matched = sum(
            1 for index, value in enumerate(expected)
            if index < len(submitted) and submitted[index] == value
        )
        if matched / len(expected) > best_matched / best_total:
            best_matched, best_total = matched, len(expected)
    earned = round(points * best_matched / best_total, 2) if best_matched else 0
    return best_matched == best_total and len(submitted) == best_total, earned


def _validated_subjective_grade(question: dict, result: dict) -> tuple:
    points = _coerce_points(question.get("points", question.get("score", question.get("total_points", 10))))
    try:
        raw_earned = result["earned"]
        if isinstance(raw_earned, bool):
            raise ValueError("boolean is not a score")
        earned = float(raw_earned)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("subjective grader returned an invalid score") from exc
    earned = round(max(0, min(float(points), earned)), 2)
    detail = {
        "grading_method": "llm_rubric",
        "matched_points": [str(item) for item in result.get("matched_points", [])],
        "missing_points": [str(item) for item in result.get("missing_points", [])],
        "feedback": str(result.get("feedback", "")).strip(),
    }
    return earned >= float(points), earned, detail


async def _score_subjective_answer(question: dict, raw_answer) -> tuple:
    answer = _submitted_answer(raw_answer)
    if answer is None or not str(answer).strip():
        return False, 0, {
            "grading_method": "llm_rubric",
            "matched_points": [],
            "missing_points": ["未作答"],
            "feedback": "未作答，本题得 0 分。",
        }
    from services.llm_service import get_llm_service

    llm = await get_llm_service()
    points = _coerce_points(question.get("points", question.get("score", question.get("total_points", 10))))
    rubric = question.get("key_points") or question.get("explanation") or question.get("answer") or ""
    result = await llm.structured_output(
        messages=[{"role": "user", "content": json.dumps({
            "question": question.get("content") or question.get("title") or "",
            "student_answer": answer,
            "reference_answer": question.get("answer") or "",
            "rubric": rubric,
            "max_points": points,
        }, ensure_ascii=False)}],
        output_schema={
            "earned": None,
            "matched_points": [],
            "missing_points": [],
            "feedback": "",
        },
        system_prompt=(
            "你是严格、公平的试题批改员。只依据题目、参考答案和评分点判分；"
            "允许语义等价表达，不按字数给分。earned 必须在 0 到 max_points 之间。"
            "matched_points 和 missing_points 只写简短评分点，feedback 给出可核验的简短反馈。"
        ),
    )
    return _validated_subjective_grade(question, result)


def _score_question_answer(question: dict, raw_answer) -> tuple:
    """Return correctness and earned points without partial credit for objective items."""
    answer = _submitted_answer(raw_answer)
    points = _coerce_points(question.get("points", question.get("score", question.get("total_points", 10))))
    question_type = _normalize_qtype_name(question.get("type"))
    if question_type == "multi_choice":
        options = question.get("options") or []
        submitted = _multi_choice_tokens(answer, options)
        expected = _multi_choice_tokens(question.get("answer"), options)
        correct = bool(expected) and submitted == expected
    elif question_type in {"choice", "judge"}:
        correct = _choice_answer_correct(answer, question.get("answer"), question.get("options") or [])
    elif question_type == "fill":
        return _score_fill_answer(question, raw_answer, points)
    else:
        return False, 0
    if correct:
        earned = points
    else:
        earned = 0
    return correct, earned


async def check_practice_answer(exam: ExamPaper, question_index: int, answer) -> dict:
    """Grade one chapter-practice item; other modes remain submit-only."""
    if exam.exam_type != "quiz":
        raise ValueError("PER_QUESTION_FEEDBACK_NOT_AVAILABLE")
    questions = list(exam.questions or [])
    if question_index < 0 or question_index >= len(questions):
        raise IndexError("QUESTION_NOT_FOUND")
    question = dict(questions[question_index] or {})
    question_type = _normalize_qtype_name(question.get("type"))
    if question_type in {"choice", "multi_choice", "judge", "fill"}:
        correct, earned = _score_question_answer(question, answer)
        detail = {"grading_method": "deterministic", "feedback": question.get("explanation") or ("回答正确。" if correct else "答案还不对，请结合解析再试一次。")}
    else:
        correct, earned, detail = await _score_subjective_answer(question, answer)
    return {
        "question_index": question_index,
        "correct": bool(correct),
        "earned": earned,
        "total": _coerce_points(question.get("points", question.get("score", 10))),
        "correct_answer": question.get("answer"),
        "explanation": detail.get("feedback") or question.get("explanation") or "已完成本题检查。",
    }


async def submit_exam(db: AsyncSession, exam_id: int, answers: list, time_spent_sec: int = 0) -> dict:
    result = await db.execute(select(ExamPaper).where(ExamPaper.id == exam_id))
    exam = result.scalar_one_or_none()
    if not exam:
        return {"error": "试卷不存在"}
    if exam.status == "completed":
        # Idempotent replay: subjective grading bills one LLM call per question,
        # so a repeated submit must return the stored verdict, never re-grade.
        previous = list(dict(exam.profile_snapshot or {}).get("last_grading") or [])
        if previous:
            return {
                "exam_id": exam_id,
                "score": exam.score,
                "total_score": exam.total_score,
                "grading": previous,
                "already_submitted": True,
            }
        return {"error": "试卷已提交过，无法重复判分"}
    exam.answers = answers
    exam.time_spent_sec = time_spent_sec
    exam.status = "completed"
    exam.completed_at = datetime.utcnow()
    score = 0
    total = 0
    grading = []
    for i, q in enumerate(exam.questions):
        pts = _coerce_points(q.get("points", q.get("score", q.get("total_points", 10))))
        total += pts
        raw_answer = answers[i] if i < len(answers) else None
        ans = _submitted_answer(raw_answer)
        question_type = _normalize_qtype_name(q.get("type"))
        detail = {"grading_method": "deterministic"}
        if question_type in {"choice", "multi_choice", "judge", "fill"}:
            correct, earned = _score_question_answer(q, raw_answer)
        else:
            correct, earned, detail = await _score_subjective_answer(q, raw_answer)
        score += earned
        grading.append({
            "question_index": i,
            "question_id": str(q.get("id") or q.get("question_id") or f"q{i + 1}"),
            "question": q.get("content") or q.get("title") or "",
            "topic": q.get("knowledge_point") or q.get("chapter_id") or (exam.chapter_ids[0] if exam.chapter_ids else ""),
            "answer": ans,
            "correct_answer": q.get("answer"),
            "explanation": q.get("explanation") or "",
            "correct": correct,
            "earned": round(earned, 1),
            "total": pts,
            **detail,
        })
    exam.score = round(score, 1)
    exam.total_score = total
    snapshot = dict(exam.profile_snapshot or {})
    snapshot["last_grading"] = grading
    exam.profile_snapshot = snapshot
    # === P2 加固：批改 + 评分 commit 失败时 rollback，写后 verify 状态 ===
    try:
        await db.commit()
        await db.refresh(exam)
        if exam.status != "completed" or exam.score is None:
            raise RuntimeError(f"submit_exam 写后状态异常: status={exam.status} score={exam.score}")
    except Exception as e:
        await db.rollback()
        logger.error(f"[submit_exam] 失败，已 rollback: {e}", exc_info=True)
        raise
    return {"exam_id": exam_id, "score": exam.score, "total_score": total, "grading": grading}


async def get_user_exams(db: AsyncSession, user_id: int, exam_type: str = None) -> list:
    conditions = [ExamPaper.user_id == user_id]
    if exam_type:
        conditions.append(ExamPaper.exam_type == exam_type)
    result = await db.execute(
        select(ExamPaper).where(and_(*conditions)).order_by(desc(ExamPaper.created_at)).limit(20)
    )
    return result.scalars().all()
