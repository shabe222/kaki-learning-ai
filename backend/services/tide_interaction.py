"""Phase 1C Tide interaction seed.

This module makes a small, temporary decision for the current user turn.  It
does not read or write learner memory, create a user model, or orchestrate
multiple agents.  Its output only guides the existing real chat provider and
is emitted as non-visual stream metadata for the current browser session.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping


TideMode = Literal["EXECUTE", "ORIENT", "EXPLAIN", "CLARIFY"]


@dataclass(frozen=True)
class TideSituationSnapshot:
    goal: str
    current_stage: str
    urgency: Literal["high", "normal"]
    risk: str
    recommended_next_action: str


@dataclass(frozen=True)
class TideDecision:
    mode: TideMode
    reason: str
    proactive_level: Literal[0, 1, 2]
    sidecar: bool
    sidecar_type: str | None
    sidecar_title: str | None
    snapshot: TideSituationSnapshot

    def public_context(self) -> dict:
        """Return temporary stream metadata; it is never rendered or persisted."""
        return {
            "mode": self.mode,
            "reason": self.reason,
            "proactive_level": self.proactive_level,
            "sidecar": self.sidecar,
            "sidecar_type": self.sidecar_type,
            "snapshot": asdict(self.snapshot),
        }


@dataclass(frozen=True)
class ExplicitResponseConstraint:
    """Current-turn user control; never a second conversation authority."""

    exact_response: str | None = None
    one_line: bool = False
    no_explanation: bool = False
    no_followup: bool = False
    pause_coaching: bool = False
    suppress_proactive: bool = False
    suppress_learning: bool = False

    @property
    def deterministic_response(self) -> str | None:
        if self.exact_response:
            return self.exact_response
        if self.pause_coaching or self.no_followup:
            return "好，你先自己想。"
        return None

    def public_context(self) -> dict:
        return {
            "contract_version": "darya_explicit_response_constraint_v1",
            "exact_response": self.exact_response is not None,
            "one_line": self.one_line,
            "no_explanation": self.no_explanation,
            "no_followup": self.no_followup,
            "pause_coaching": self.pause_coaching,
            "suppress_proactive": self.suppress_proactive,
            "suppress_learning": self.suppress_learning,
        }


_DEADLINE = re.compile(r"明天|今晚|后天|马上|尽快|截止|截止|截止|答辩|考试|赶不及|来不及", re.I)
_DELIVERABLE = re.compile(r"PPT|幻灯片|演示|演讲|答辩|报告|作业|项目|初稿|稿子", re.I)
_INCOMPLETE = re.compile(r"还有|没整理|没做完|很多|一团乱|来不及|慌|着急|焦虑", re.I)
_NOVICE = re.compile(r"完全不懂|不懂|零基础|不会|没学过|入门", re.I)
_ASSESSMENT = re.compile(r"考试|测验|面试|明天考|答辩", re.I)
_VAGUE_IDEA = re.compile(r"想做|想开发|想设计|有个想法|做一个", re.I)
_UNCERTAIN = re.compile(r"不知道从哪|不知道怎么|从哪里开始|没方向|没想清楚|很模糊", re.I)
_EXPLAIN = re.compile(r"为什么|怎么理解|什么是|解释|原理|讲讲|讲清楚|why|explain", re.I)
_ACTION = re.compile(
    r"帮我|怎么办|下一步|开始|修改|整理|完成|优先|计划|清单|草稿|"
    r"help(?: me)?|next|start|improve|refine|revise|finish|plan|checklist|draft",
    re.I,
)
_WORKING_OBJECT = re.compile(r"计划|清单|路线|结构|草稿|资源|流程|学习地图|框架|方案", re.I)
_EXACT_AFTER_COLON = re.compile(
    r"(?:你|请|麻烦你)?只(?:回复|回答|说)(?:我)?\s*[：:]\s*[“\"']?([^\n”\"']{1,40}?)[”\"']?\s*$",
    re.I,
)
_EXACT_IN_QUOTES = re.compile(
    r"(?:你|请|麻烦你)?只(?:回复|回答|说)(?:我)?\s*[“\"']([^”\"'\n]{1,40})[”\"']",
    re.I,
)
_ONE_LINE_CONTROL = re.compile(r"只用一句话|用一句话|一句话(?:回答|回复|说明|说清)", re.I)
_NO_EXPLANATION_CONTROL = re.compile(r"先别解释|暂时不要解释|不要展开|先别展开|只要结论", re.I)
_NO_FOLLOWUP_CONTROL = re.compile(r"不要继续问(?:我)?|别继续问(?:我)?|不要追问|别追问", re.I)
_PAUSE_COACHING_CONTROL = re.compile(r"先让我自己想|我先自己想|先让我想想|我先想想", re.I)
_NO_RECOMMENDATION_CONTROL = re.compile(r"暂时不要推荐|先别推荐|不要推荐|别推荐", re.I)
_UNSAFE_EXACT_CONTEXT = re.compile(
    r"自杀|自残|伤害他人|炸弹|毒品|入侵.{0,8}账户|窃取.{0,8}(?:密码|令牌)|未成年人.{0,8}性",
    re.I,
)
_RITUAL_HEADING = re.compile(r"^(?:#{1,6}\s*)?(?:当前判断|当前结论|回答)\s*[：:]?\s*", re.I)


def _clean_goal(message: str) -> str:
    text = re.sub(r"\s+", " ", str(message or "")).strip(" 。！？?!")
    if not text:
        return "弄清当前需要解决的问题"
    return text[:72] + ("…" if len(text) > 72 else "")


def _normalize_exact_response(value: str) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" “”\"'")
    if not text or len(text) > 40 or "http" in text.casefold() or "```" in text or "[tide_" in text.casefold():
        return None
    if not re.search(r"[。！？.!?]$", text):
        text += "。"
    return text


def detect_explicit_response_constraint(message: str) -> ExplicitResponseConstraint | None:
    """Recognize response-shape controls without keying on requested reply text."""
    text = str(message or "").strip()
    exact = None
    if not _UNSAFE_EXACT_CONTEXT.search(text):
        match = _EXACT_AFTER_COLON.search(text) or _EXACT_IN_QUOTES.search(text)
        if match:
            exact = _normalize_exact_response(match.group(1))
    one_line = bool(_ONE_LINE_CONTROL.search(text))
    no_explanation = bool(_NO_EXPLANATION_CONTROL.search(text))
    no_followup = bool(_NO_FOLLOWUP_CONTROL.search(text))
    pause_coaching = bool(_PAUSE_COACHING_CONTROL.search(text))
    no_recommendation = bool(_NO_RECOMMENDATION_CONTROL.search(text))
    if not any((exact, one_line, no_explanation, no_followup, pause_coaching, no_recommendation)):
        return None
    suppress_learning = bool(exact or no_followup or pause_coaching)
    return ExplicitResponseConstraint(
        exact_response=exact,
        one_line=bool(one_line or exact),
        no_explanation=bool(no_explanation or exact),
        no_followup=bool(no_followup or pause_coaching or exact),
        pause_coaching=pause_coaching,
        suppress_proactive=bool(exact or no_followup or pause_coaching or no_recommendation),
        suppress_learning=suppress_learning,
    )


def build_explicit_response_directive(control: ExplicitResponseConstraint) -> str:
    rules = ["本轮用户的明确响应约束高于自动教学展开和通用包装。"]
    if control.one_line:
        rules.append("只输出一句，不加标题。")
    if control.no_explanation:
        rules.append("不补充解释或展开材料。")
    if control.no_followup:
        rules.append("不追问，不用问号结尾。")
    if control.suppress_proactive:
        rules.append("不附加推荐、主动继续或 Sidecar。")
    return "".join(rules)


def enforce_explicit_response_constraint(raw: str, control: ExplicitResponseConstraint) -> str:
    deterministic = control.deterministic_response
    if deterministic:
        return deterministic
    text = str(raw or "").strip()
    center = _CENTER_BLOCK.search(text)
    if center:
        text = center.group(1).strip()
    text = _SIDECAR_BLOCK.sub("", text)
    text = _BLACKBOARD_BLOCK.sub("", text)
    text = _RITUAL_HEADING.sub("", text).strip()
    if control.no_followup:
        text = re.sub(r"[^\n。！!]*[？?]", "", text).strip()
    if control.one_line or control.no_explanation:
        text = re.sub(r"\s+", " ", text).strip()
        sentence = re.match(r"^(.+?[。！？.!?])(?:\s|$)", text)
        if sentence:
            text = sentence.group(1).strip()
    return text


def decide_tide_interaction(
    message: str,
    shared_perspective: dict | None = None,
    current_perspective: dict | None = None,
) -> TideDecision:
    """Choose one Tide response mode using the current turn and thread context."""
    text = str(message or "").strip()
    perspective = shared_perspective or {}
    perspective_goal = str(perspective.get("goal") or "").strip()
    perspective_stage = str(perspective.get("stage") or "").strip()
    constraints = perspective.get("constraints") or []
    if not isinstance(constraints, list):
        constraints = [constraints]
    context_text = " ".join([perspective_goal, perspective_stage, *map(str, constraints), text])
    goal = _clean_goal(perspective_goal or text)
    deadline = bool(_DEADLINE.search(context_text))
    deliverable_pressure = deadline and bool(_DELIVERABLE.search(context_text)) and bool(_INCOMPLETE.search(context_text))
    novice_assessment = bool(_NOVICE.search(context_text)) and bool(_ASSESSMENT.search(context_text))
    vague_idea = bool(_VAGUE_IDEA.search(context_text)) and bool(_UNCERTAIN.search(context_text))
    perspective_action = str((current_perspective or {}).get("action") or "")
    perspective_next = str((current_perspective or {}).get("recommended_next_move") or "").strip()
    clarification_question = str((current_perspective or {}).get("clarification_question") or "").strip()

    if perspective_action == "CLARIFY":
        return TideDecision(
            mode="CLARIFY",
            reason="perspective_missing_material_decision_context",
            proactive_level=1,
            sidecar=False,
            sidecar_type=None,
            sidecar_title=None,
            snapshot=TideSituationSnapshot(
                goal=goal,
                current_stage=perspective_stage or "关键条件待确认",
                urgency="high" if deadline else "normal",
                risk="缺失信息会实质改变正确行动",
                recommended_next_action=clarification_question or perspective_next,
            ),
        )

    if perspective_action == "PREPARE":
        return TideDecision(
            mode="EXECUTE",
            reason="perspective_prepare_reversible_v0",
            proactive_level=2,
            sidecar=True,
            sidecar_type="working-draft",
            sidecar_title="先推进的一版",
            snapshot=TideSituationSnapshot(
                goal=goal,
                current_stage=perspective_stage or "准备第一版（可修改）",
                urgency="high" if deadline else "normal",
                risk="继续追问会推迟可逆的有用工作",
                recommended_next_action=perspective_next or "先准备第一版（可修改）",
            ),
        )

    if perspective_action == "WARN":
        return TideDecision(
            mode="ORIENT",
            reason="perspective_high_risk_interrupt",
            proactive_level=1,
            sidecar=False,
            sidecar_type=None,
            sidecar_title=None,
            snapshot=TideSituationSnapshot(
                goal=goal,
                current_stage=perspective_stage or "先处理风险边界",
                urgency="high",
                risk="继续执行可能造成高风险或不可逆后果",
                recommended_next_action=perspective_next or "先说明风险并改用安全路径",
            ),
        )

    if deliverable_pressure:
        return TideDecision(
            mode="EXECUTE",
            reason="deadline_with_unfinished_deliverable",
            proactive_level=2,
            sidecar=True,
            sidecar_type="action-plan",
            sidecar_title="答辩前的收口路径",
            snapshot=TideSituationSnapshot(
                goal=goal,
                current_stage=perspective_stage or "交付前收口",
                urgency="high",
                risk="时间被低优先级的完善工作消耗",
                recommended_next_action="先锁定答辩必须讲清的主线，再排剩余页面",
            ),
        )

    if novice_assessment:
        return TideDecision(
            mode="ORIENT",
            reason="novice_with_immediate_assessment",
            proactive_level=2,
            sidecar=True,
            sidecar_type="learning-map",
            sidecar_title="明天前的最小学习地图",
            snapshot=TideSituationSnapshot(
                goal=goal,
                current_stage=perspective_stage or "从零建立最小可用理解",
                urgency="high" if deadline else "normal",
                risk="知识范围过大，来不及建立可答题的骨架",
                recommended_next_action="先建一个可讲出的最小心智模型，再补考点",
            ),
        )

    if vague_idea:
        return TideDecision(
            mode="CLARIFY",
            reason="solution_idea_before_problem_definition",
            proactive_level=2,
            sidecar=True,
            sidecar_type="exploration-map",
            sidecar_title="学习 AI 的问题探索框架",
            snapshot=TideSituationSnapshot(
                goal=goal,
                current_stage=perspective_stage or "问题定义尚未收敛",
                urgency="normal",
                risk="方案先于问题定义，容易做成大而全",
                recommended_next_action="先确定一类学生在一个具体学习时刻的未满足需求",
            ),
        )

    if _EXPLAIN.search(text):
        has_working_object = (
            bool(_WORKING_OBJECT.search(text))
            or bool(re.search(r"解释|讲讲|讲清楚|原理|深入|explain", text, re.I))
            or len(text) > 80
        )
        level: Literal[0, 1, 2] = 2 if has_working_object else 1
        return TideDecision(
            mode="EXPLAIN",
            reason="explicit_explanation_request",
            proactive_level=level,
            sidecar=level == 2,
            sidecar_type="explanation" if level == 2 else None,
            sidecar_title="原理与结构" if level == 2 else None,
            snapshot=TideSituationSnapshot(
                goal=goal,
                current_stage=perspective_stage or "建立理解",
                urgency="high" if deadline else "normal",
                risk="细节先于核心概念，理解无法落地",
                recommended_next_action="先给出一个可复述的概念锚点",
            ),
        )

    if _ACTION.search(text):
        has_working_object = bool(_WORKING_OBJECT.search(text))
        level = 2 if has_working_object else 1
        return TideDecision(
            mode="EXECUTE",
            reason="actionable_request",
            proactive_level=level,
            sidecar=level == 2,
            sidecar_type="workflow" if level == 2 else None,
            sidecar_title="当前工作路径" if level == 2 else None,
            snapshot=TideSituationSnapshot(
                goal=goal,
                current_stage=perspective_stage or "准备执行",
                urgency="high" if deadline else "normal",
                risk="下一步不清楚，行动容易发散",
                recommended_next_action="锁定一个可立即完成的最小动作",
            ),
        )

    return TideDecision(
        mode="ORIENT",
        reason="needs_current_orientation",
        proactive_level=0,
        sidecar=False,
        sidecar_type=None,
        sidecar_title=None,
        snapshot=TideSituationSnapshot(
            goal=goal,
            current_stage=perspective_stage or "梳理当前问题",
            urgency="high" if deadline else "normal",
            risk="回答没有对准当前时刻",
            recommended_next_action="先给出对当前问题的简短判断",
        ),
    )


def tide_decision_from_model_support(
    cognition: Any,
    *,
    shared_perspective: Mapping[str, Any] | None = None,
) -> TideDecision:
    """Project the model's structured interaction choice into Tide UI state.

    This function intentionally performs no semantic text matching. It only
    validates and bounds a model decision before the UI/provider consume it.
    """
    payload = cognition.public_dict() if hasattr(cognition, "public_dict") else cognition
    payload = payload if isinstance(payload, Mapping) else {}
    interaction = payload.get("interaction")
    interaction = interaction if isinstance(interaction, Mapping) else {}
    help_opportunity = payload.get("help_opportunity")
    help_opportunity = help_opportunity if isinstance(help_opportunity, Mapping) else {}
    work_brief = payload.get("work_brief")
    work_brief = work_brief if isinstance(work_brief, Mapping) else {}
    shared = shared_perspective or {}

    mode = str(interaction.get("mode") or "ORIENT").upper()
    if mode not in {"EXECUTE", "ORIENT", "EXPLAIN", "CLARIFY"}:
        mode = "ORIENT"
    sidecar = interaction.get("sidecar") is True
    sidecar_type = str(interaction.get("sidecar_type") or "").strip()[:80] or None
    sidecar_title = str(interaction.get("sidecar_title") or "").strip()[:80] or None
    if not sidecar:
        sidecar_type = None
        sidecar_title = None
    goal = str(
        help_opportunity.get("true_goal")
        or work_brief.get("goal")
        or shared.get("goal")
        or "回应用户当前需要"
    ).strip()[:300]
    stage = str(work_brief.get("stage") or shared.get("stage") or "当前轮").strip()[:160]
    next_action = str(
        help_opportunity.get("best_immediate_deliverable")
        or help_opportunity.get("private_input_needed")
        or "由 DARYA 结合当前对话直接推进"
    ).strip()[:500]
    reason = str(interaction.get("reason") or "model_selected").strip()[:160]
    risk = str(interaction.get("risk") or help_opportunity.get("main_obstacle") or "").strip()[:240]
    proactive_level: Literal[0, 1, 2] = 2 if sidecar else (0 if mode == "ORIENT" else 1)
    return TideDecision(
        mode=mode,  # type: ignore[arg-type]
        reason=reason,
        proactive_level=proactive_level,
        sidecar=sidecar,
        sidecar_type=sidecar_type,
        sidecar_title=sidecar_title,
        snapshot=TideSituationSnapshot(
            goal=goal,
            current_stage=stage,
            urgency="high" if str(interaction.get("urgency") or "").lower() == "high" else "normal",
            risk=risk,
            recommended_next_action=next_action,
        ),
    )


def build_tide_system_directive(
    decision: TideDecision,
    shared_perspective: dict | None = None,
) -> str:
    """Build a provider contract that preserves center/Sidecar responsibility."""
    snapshot_json = json.dumps(asdict(decision.snapshot), ensure_ascii=False)
    perspective_json = json.dumps(shared_perspective or {}, ensure_ascii=False)
    if decision.sidecar:
        output_contract = (
            "必须严格输出两个区块，不要在区块外写任何文字：\n"
            "[tide_center]\n对用户当前请求的直接回应\n[/tide_center]\n"
            f"[tide_sidecar type=\"{decision.sidecar_type}\" title=\"{decision.sidecar_title}\"]\n"
            "可直接使用的工作对象（允许 Markdown）\n[/tide_sidecar]"
        )
    else:
        output_contract = (
            "必须严格输出一个区块，不要在区块外写任何文字：\n"
            "[tide_center]\n对用户当前请求的直接回应\n[/tide_center]"
        )

    mode_rule = {
        "EXECUTE": "判断真正的交付压力，明确优先级，给出下一个可立即开始的动作。",
        "ORIENT": "先建立最小有用心智模型或学习地图，不要立即倾倒教科书知识。",
        "EXPLAIN": "先给一个可复述的概念锚点，然后才展开原理。",
        "CLARIFY": (
            "指出最关键的未知量，中心必须以且只以一个高价值问题结束（只能有一个问号），"
            "Sidecar 只给探索框架，不再追问。"
        ),
    }[decision.mode]

    sidecar_rule = (
        "Sidecar 不是聊天复制，而是能继续工作的对象。"
        "使用有意义的小标题、顺序、判断标准或待填空位；不要只是聊天记录。"
        if decision.sidecar else
        "本轮不创建 Sidecar，不要偷偷附加详细文档。"
    )

    return (
        "你正在 DARYA 产品的 Tide（潮汐）世界中回答。DARYA 是产品，Tide 只是世界隐喻。"
        "你就是 DARYA，不是可爱吉祥物，不使用可爱的 AI 昵称，也不把‘潮汐’当作自己的名字。"
        "DARYA 像一位能力很强的导师兼朋友：沉着、有经验、人性化、清楚，偶尔轻微幽默，但不冷。\n"
        "核心表达原则是高价值密度、低理解成本：专业在脑子里，人话在嘴上。"
        "每个有意义的回答至少增加一项真实价值：非显然判断、更好的问题框架、具体建议、可用结果、已准备的下一步，或相关证据。"
        "如果草稿只是在复述用户、表达理解、给显而易见的建议，或追问现有上下文已经能回答的问题，就压缩或替换。"
        "先推进，再补全。已有上下文足以产出 V0/V1 时，先给出来；只有缺失信息会实质改变结果时才追问。"
        "确实需要追问时，只问一个高价值问题，不要连问，也不要用两个问号拆一个问题。"
        "保持大约领先半步，不制造噱头，也不一下领先十步。默认说法是一个尖锐判断，接一个具体例子或动作；需要时再补解释，不机械套模板。"
        "简单问题简短回答，不展开大框架；设计或 PPT 问题要用人话并给一个能想象出来的具体例子。"
        "你可以在自然对话中调用已经接通的真实能力：外部资料检索、讲义、实操指南、测评、Code Lab、"
        "PPT/演示文稿、思维导图、教学视频和可继续修改的图解。用户明确要求生成这些产物时，"
        "不得说自己没有该能力，也不得用大纲、脚本或普通文字冒充产物；对话路由会在能力真实创建后展示卡片。"
        "避免‘认知负荷’、‘视觉锚点’、‘差异化视觉变量’这类不必要术语；直接说‘别让用户不知道先看哪儿’、‘先让一个东西当主角’。\n"
        "禁止通用安慰、鸡汤、自动的‘三个技巧’、过量共情、可爱语气和无必要的动机鼓励。"
        "绝对不要说‘别慌’、‘慌是正常的’、‘不要紧张’、‘深呼吸’、‘我来帮你哦’或‘给你几个技巧’。"
        "不要从评价用户情绪开始，直接回应当前请求。"
        "不要机械添加‘当前判断’、‘当前结论’或‘回答’标题；只有复杂判断中该标签确实降低理解成本时才可使用。\n"
        "不要杜撰用户没有提供的精确时间、截止时刻或其他事实；需要排程时使用相对时间块。\n"
        "Center 只承担关系、当前回应与下一步：最多 2 个短段，不列长清单，不写详细文档。\n"
        f"当前决策模式：{decision.mode}。执行规则：{mode_rule}\n"
        f"临时当前情境：{snapshot_json}\n"
        f"已验证的线程共同视角：{perspective_json}\n"
        "共同视角只用于维持当前工作的连续性；不要把缺失字段补写成用户事实。\n"
        f"{sidecar_rule}\n"
        "不要向用户暴露模式名、reason、proactive level或临时 JSON。\n"
        f"{output_contract}"
    )


_CENTER_BLOCK = re.compile(r"\[tide_center\]([\s\S]*?)\[/tide_center\]", re.I)
_SIDECAR_BLOCK = re.compile(r"\[tide_sidecar\b[^\]]*\]([\s\S]*?)\[/tide_sidecar\]", re.I)
_BLACKBOARD_BLOCK = re.compile(r"\[blackboard\]([\s\S]*?)\[/blackboard\]", re.I)
_CONTROL_TOKEN = re.compile(r"\[control:[^\]]+\]", re.I)
_UNSAFE_INLINE_HTML = re.compile(r"</?(?:details|summary)\b[^>]*>", re.I)


def _without_legacy_blackboard_markers(value: str) -> str:
    return re.sub(r"\[/?blackboard\]", "", str(value or ""), flags=re.I).strip()


def _concise_center(value: str) -> str:
    visible = _BLACKBOARD_BLOCK.sub("", str(value or "")).strip()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", visible) if part.strip()]
    summary = paragraphs[0] if paragraphs else visible
    summary = re.sub(r"^#{1,6}\s+", "", summary)
    summary = re.sub(r"\s+", " ", summary).strip()
    if len(summary) <= 260:
        return summary
    clipped = summary[:260]
    boundaries = list(re.finditer(r"[。！？.!?]", clipped))
    if boundaries and boundaries[-1].end() >= 72:
        return clipped[:boundaries[-1].end()].strip()
    return clipped.rstrip() + "…"


def enforce_tide_response_contract(raw: str, decision: TideDecision) -> str:
    """Repair only protocol-level omissions in a completed provider response.

    Provider content remains the source of the answer.  This guard guarantees
    that a CLARIFY decision actually asks one valuable question and that a
    promised level-2 Sidecar cannot disappear because the model omitted tags.
    """
    text = _CONTROL_TOKEN.sub("", str(raw or ""))
    text = _UNSAFE_INLINE_HTML.sub("", text).strip()
    if len(re.findall(r"\[tide_center\]", text, flags=re.I)) > 1:
        sidecar_start = re.search(r"\[tide_sidecar\b", text, flags=re.I)
        boundary = sidecar_start.start() if sidecar_start else len(text)
        center_region = re.sub(
            r"\[/?tide_center\]",
            "",
            text[:boundary],
            flags=re.I,
        ).strip()
        text = (
            f"[tide_center]\n{center_region}\n[/tide_center]"
            + ("\n" + text[boundary:] if boundary < len(text) else "")
        )
    # Providers occasionally finish after opening the center block but before
    # emitting its closing marker.  Close that block in place; wrapping the
    # whole response would nest a second opening marker and leak protocol text
    # into the learner-visible answer.
    if re.search(r"\[tide_center\]", text, flags=re.I) and not re.search(
        r"\[/tide_center\]", text, flags=re.I,
    ):
        text = text.rstrip() + "\n[/tide_center]"
    center_match = _CENTER_BLOCK.search(text)
    if not center_match:
        text = f"[tide_center]\n{text}\n[/tide_center]"
        center_match = _CENTER_BLOCK.search(text)

    if decision.mode == "CLARIFY" and center_match:
        center = center_match.group(1).strip()
        if decision.reason == "perspective_missing_material_decision_context":
            question = decision.snapshot.recommended_next_action
            if not re.search(r"[?？]$", question):
                question = question.rstrip("。.!！") + "？"
            replacement = f"[tide_center]\n{question}\n[/tide_center]"
            text = text[:center_match.start()] + replacement + text[center_match.end():]
        elif not re.search(r"[?？]", center):
            question = "你最想先服务哪一类学生，他们卡在哪个具体学习时刻？"
            replacement = f"[tide_center]\n{center}\n\n{question}\n[/tide_center]"
            text = text[:center_match.start()] + replacement + text[center_match.end():]

    if decision.sidecar:
        center_match = _CENTER_BLOCK.search(text)
        sidecar_match = _SIDECAR_BLOCK.search(text)
        center_content = center_match.group(1).strip() if center_match else ""
        blackboard_content = "\n\n".join(
            part.strip() for part in _BLACKBOARD_BLOCK.findall(center_content) if part.strip()
        )
        center_is_too_deep = len(center_content) > 620 or bool(blackboard_content)

        if not sidecar_match:
            # A provider may finish after emitting only an opening Sidecar
            # marker.  Remove orphan protocol tokens before appending the
            # canonical block; otherwise the frontend treats the first opener
            # and our final closer as one nested block and displays the token.
            orphan_match = re.search(
                r"\[tide_sidecar\b[^\]]*\]\s*([\s\S]*)$", text, flags=re.I,
            )
            orphan_content = orphan_match.group(1).strip() if orphan_match else ""
            if orphan_match:
                text = text[:orphan_match.start()].strip()
            else:
                text = re.sub(r"\[/?tide_sidecar\b[^\]]*\]", "", text, flags=re.I).strip()
            center_match = _CENTER_BLOCK.search(text)
            center_content = center_match.group(1).strip() if center_match else ""
            working_content = (
                orphan_content
                or (_without_legacy_blackboard_markers(center_content) if center_is_too_deep else "")
            )
            if not working_content:
                working_content = (
                    f"## 当前目标\n{decision.snapshot.goal}\n\n"
                    f"## 当前阶段\n{decision.snapshot.current_stage}\n\n"
                    f"## 先做这一步\n{decision.snapshot.recommended_next_action}\n\n"
                    f"## 需要避免\n{decision.snapshot.risk}"
                )
            if center_match and center_is_too_deep:
                concise = _concise_center(center_content)
                replacement = f"[tide_center]\n{concise}\n[/tide_center]"
                text = text[:center_match.start()] + replacement + text[center_match.end():]
            text += (
                f"\n[tide_sidecar type=\"{decision.sidecar_type}\" title=\"{decision.sidecar_title}\"]\n"
                f"{working_content}\n[/tide_sidecar]"
            )
        elif center_match and center_is_too_deep:
            existing_sidecar = sidecar_match.group(1).strip()
            moved = _without_legacy_blackboard_markers(center_content)
            concise = _concise_center(center_content)
            center_replacement = f"[tide_center]\n{concise}\n[/tide_center]"
            text = text[:center_match.start()] + center_replacement + text[center_match.end():]
            sidecar_match = _SIDECAR_BLOCK.search(text)
            if sidecar_match:
                combined = "\n\n".join(part for part in (existing_sidecar, moved) if part)
                sidecar_replacement = sidecar_match.group(0).replace(
                    sidecar_match.group(1), combined,
                )
                text = text[:sidecar_match.start()] + sidecar_replacement + text[sidecar_match.end():]
    if not decision.sidecar:
        text = _BLACKBOARD_BLOCK.sub("", text)
    text = _without_legacy_blackboard_markers(text)
    if decision.mode == "CLARIFY":
        # The one high-value question belongs in the center.  Sidecar prompts
        # become fillable working fields instead of a second interrogation.
        text = _SIDECAR_BLOCK.sub(
            lambda match: re.sub(r"[?？]", "：____", match.group(0)),
            text,
        )
    center_match = _CENTER_BLOCK.search(text)
    if center_match:
        center = center_match.group(1)
        question_positions = [match.start() for match in re.finditer(r"[?？]", center)]
        if len(question_positions) > 1:
            keep = question_positions[-1]
            center = "".join(
                "：" if char in "?？" and index != keep else char
                for index, char in enumerate(center)
            )
            replacement = f"[tide_center]\n{center.strip()}\n[/tide_center]"
            text = text[:center_match.start()] + replacement + text[center_match.end():]
    return text.strip()
