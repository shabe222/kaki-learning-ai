"""Model-primary semantic activity projection for the PC03A authority.

The classifier does not execute or persist anything. A structured model reads
the current utterance together with recent conversation and the Active Route.
This module then validates that decision against explicit user controls and the
capability boundaries. The route may disambiguate an ellipsis, but it can never
create an artifact request on its own.

Deterministic language handling is deliberately narrow: explicit negative
controls, exact capability names, and high-signal action/object requests used
as a resilient compatibility path when the model is unavailable. Ordinary
mentions still fail closed to conversation.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

_logger = logging.getLogger("rjianb.conversation_capability.intent")


CAPABILITY_OUTCOMES = {
    "CODE_LAB", "ASSESSMENT", "PRACTICAL_GUIDE", "HANDOUT",
    "PRESENTATION", "MINDMAP", "VIDEO", "STORYBOOK", "DEMONSTRATE", "CONVERSATION_ONLY",
}
ACTIVITY_BY_OUTCOME = {
    "CODE_LAB": "HANDS_ON_PROGRAMMING",
    "ASSESSMENT": "KNOWLEDGE_VERIFICATION",
    "PRACTICAL_GUIDE": "STEP_BY_STEP_ACTION",
    "HANDOUT": "DURABLE_MATERIAL",
    "PRESENTATION": "PRESENTATION_CREATION",
    "MINDMAP": "MINDMAP_CREATION",
    "VIDEO": "VIDEO_CREATION",
    "STORYBOOK": "STORYBOOK_CREATION",
    "DEMONSTRATE": "VISUAL_DEMONSTRATION",
    "CONVERSATION_ONLY": "CONVERSATION",
}


@dataclass(frozen=True)
class CapabilityIntentDecision:
    user_goal: str
    desired_activity: str
    outcome: str
    wants_self_attempt: bool = False
    wants_explanation: bool = False
    wants_assessment: bool = False
    wants_durable_material: bool = False
    wants_step_by_step_action: bool = False
    negative_constraints: tuple[str, ...] = ()
    domain_context: str = ""
    recent_conversation_context: tuple[str, ...] = ()
    active_route_context: str = ""
    confidence: float = 0.0
    confidence_band: str = "LOW"
    reason_code: str = "conversation_only"
    clarification: str | None = None
    source: str = "semantic_safe_fallback"
    evidence_span: str = ""
    explicit_activity_request: bool = False

    def evidence(self) -> dict[str, Any]:
        return asdict(self)


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _negative_constraints(message: str) -> tuple[str, ...]:
    """Recognise only explicit veto/control clauses, never positive intent."""
    text = _compact(message)
    constraints: list[str] = []
    negative = r"(?:先别|别|不要|不用|暂时不|不想)"
    if re.search(negative + r"[^，,。.!！?？]{0,10}(?:代码|写|敲|实现|跑|动手)", text, re.I) \
            or "今天不想动手" in text:
        constraints.append("NO_CODE_LAB")
    if re.search(negative + r"[^，,。.!！?？]{0,10}(?:考|出题|测验|测我|问我)", text, re.I):
        constraints.append("NO_ASSESSMENT")
    if re.search(negative + r"[^，,。.!！?？]{0,10}(?:生成|做东西|产物|资料|讲义|整理|提示|追问|推荐|打扰)", text, re.I) \
            or re.search(r"(?:先)?让我自己想|我先想一会|你先听我说完", text, re.I):
        constraints.append("NO_ARTIFACT")
    if re.search(r"(?:只|先).{0,6}(?:解释|讲解|说思路|回答)", text, re.I) \
            or "我只是问问" in text:
        constraints.append("EXPLANATION_ONLY")
    if re.search(r"(?:先别|别|不要|不用|不必|无需).{0,10}(?:画|图|可视化|黑板|板书)", text, re.I) \
            or re.search(r"(?:把)?黑板(?:擦|清)(?:掉|了)|不用黑板|别用黑板|我想自己写板书", text, re.I) \
            or re.search(r"(?:我想|让我)?自己先画|我自己来画|简单说一句就行|一句话就行", text, re.I):
        constraints.append("NO_DEMONSTRATION")
    return tuple(dict.fromkeys(constraints))


# A bounded instruction aimed at a board that is already open. Kept separate
# from the request patterns above because it is only meaningful together with a
# real open scene -- on its own "把那一步突出" is just a sentence.
_BOARD_REFERENCE = r"(?:黑板|板书|板子上?|这张图|这幅图|那张图|刚才(?:那张|画的)|图上|图里|图中)"
_BOARD_EDIT = r"(?:突出|标出|标记|圈出?|划出?|修改|改动|挪动|删掉|去掉|移除|取消|添加|加上|强调|画出?|画上|写上|补上|擦掉)"
_BLACKBOARD_CONTINUATION = re.compile(
    rf"{_BOARD_REFERENCE}[^，。！!?；;]{{0,24}}{_BOARD_EDIT}"
    rf"|{_BOARD_EDIT}[^，。！!?；;]{{0,24}}{_BOARD_REFERENCE}"
    rf"|(?:那|这|上)(?:一)?(?:步|句|行|条|块|个)[^，。！!?；;]{{0,10}}{_BOARD_EDIT}",
    re.I,
)


def explicit_blackboard_continuation(message: str) -> bool:
    """Whether this turn explicitly asks to change the board already on screen.

    Callers must also confirm an open scene: the phrasing alone is not proof,
    and turning an ordinary sentence into a board would be worse than missing
    one.
    """
    text = _compact(message)
    if not text or len(text) > 60:
        return False
    return bool(_BLACKBOARD_CONTINUATION.search(text))


def _programming_context(value: str) -> bool:
    """Capability prerequisite, not an intent trigger."""
    return bool(re.search(
        r"python|javascript|typescript|java|c\+\+|编程|程序|代码|函数|变量|闭包|"
        r"nonlocal|作用域|算法|接口|api|sql|调试|单元测试|实现",
        str(value or ""), re.I,
    ))


def _base(
    message: str, *, domain_context: str, active_route_context: str,
    recent_conversation_context: Sequence[str],
) -> CapabilityIntentDecision:
    return CapabilityIntentDecision(
        user_goal=str(message or "").strip(),
        desired_activity="CONVERSATION",
        outcome="CONVERSATION_ONLY",
        negative_constraints=_negative_constraints(message),
        domain_context=domain_context,
        recent_conversation_context=tuple(recent_conversation_context[-6:]),
        active_route_context=active_route_context,
        confidence=.0,
        confidence_band="LOW",
        reason_code="awaiting_semantic_decision",
    )


def _model_prompt(base: CapabilityIntentDecision) -> str:
    return f"""你是 DARYA 对话内的活动意图分类器。你的唯一任务是判断“用户这一轮明确想进行什么活动”。不要回答用户，不要执行活动，不输出推理过程。

可选活动：
- HANDS_ON_PROGRAMMING -> CODE_LAB：用户要亲自在编程任务中写、补或实现代码。
- KNOWLEDGE_VERIFICATION -> ASSESSMENT：用户要通过题目、提问或测验检验掌握情况。
- STEP_BY_STEP_ACTION -> PRACTICAL_GUIDE：用户要一份现在可以照着执行的步骤或操作引导。
- DURABLE_MATERIAL -> HANDOUT：用户要整理成可保存、复习或系统阅读的资料。
- PRESENTATION_CREATION -> PRESENTATION：用户明确要生成 PPT、幻灯片或演示文稿；这意味着创建真正可打开、可导出的演示文稿，不是只给文字大纲。
- MINDMAP_CREATION -> MINDMAP：用户明确要生成思维导图或脑图；必须创建真正可打开的导图产物。
- VIDEO_CREATION -> VIDEO：用户明确要生成教学视频；必须创建可查看真实生成阶段的视频产物。
- STORYBOOK_CREATION -> STORYBOOK：用户明确要生成教学绘本；打开产品内真实的视觉绘本创作器，不用普通文字冒充绘本。
- VISUAL_DEMONSTRATION -> DEMONSTRATE：用户明确希望 DARYA 用黑板讲——在板上写板书、作图、用空间关系/箭头/分组/流程图演示，并且由 DARYA 动手。用户点名要黑板或板书同样是这个请求。
- CONVERSATION -> CONVERSATION_ONLY：解释、讨论、情绪表达、元问题、普通问答、否定/暂停控制、已理解陈述、意图不清。

判定规则：
1. 必须以“当前用户消息”为请求证据。近期对话和 Active Route 只能解释“我来”“轮到我了”这类省略，不能单独触发产物。
2. 提到“代码、题、资料、测试”等名词不等于请求活动。
3. CODE_LAB 同时要求编程语境、亲自动手意图和明确活动请求；请求解释/排错不是 Code Lab。
4. 用户否定某活动时不得选择它。若只否定一种活动，可选择同一句中明确请求的另一种活动。
5. 不确定、泛泛的“来个东西”、普通问题一律 CONVERSATION_ONLY。
6. evidence_span 必须逐字来自当前用户消息，是表达活动请求或普通交谈性质的最短证据；不得引用上下文。
7. DEMONSTRATE 要求用户明确想看 DARYA 在黑板上画出的视觉解释。评论一张图、自己想画、提到“画/图/关系/流程”等普通用法，都不是请求演示。
8. 若近期对话里存在正在进行的视觉演示，“把那一步突出”“把另一个对比放旁边”属于继续 DEMONSTRATE；上下文只用于确认是同一张图，不能凭空触发。
9. “你写在黑板上讲”“用板书说”“边说边画”都是 DEMONSTRATE；说“别写黑板”“黑板擦了”不是。

边界示例（理解活动目的，不做字符串匹配）：
- 在当前学习对话里“帮我整理一下这个”是在请求可保留的整理材料，选 HANDOUT。
- “那我具体怎么做”是在请求可执行步骤，选 PRACTICAL_GUIDE。
- 编程上下文中的“轮到我上手了”是在请求亲自实现，选 CODE_LAB；非编程上下文不选 Code Lab。
- “给我来个东西”没有说明活动目的，选 CONVERSATION_ONLY。
- “这个关系我想象不出来，你画一下”是在请求 DARYA 进行视觉演示，选 DEMONSTRATE。
- “这张图是什么意思”是在问现有内容，选 CONVERSATION_ONLY。

只输出一个 JSON 对象，字段必须完整：
{{"outcome":"CODE_LAB|ASSESSMENT|PRACTICAL_GUIDE|HANDOUT|PRESENTATION|MINDMAP|VIDEO|STORYBOOK|DEMONSTRATE|CONVERSATION_ONLY","desired_activity":"HANDS_ON_PROGRAMMING|KNOWLEDGE_VERIFICATION|STEP_BY_STEP_ACTION|DURABLE_MATERIAL|PRESENTATION_CREATION|MINDMAP_CREATION|VIDEO_CREATION|STORYBOOK_CREATION|VISUAL_DEMONSTRATION|CONVERSATION","confidence":0.0,"reason_code":"snake_case","explicit_activity_request":false,"wants_self_attempt":false,"wants_explanation":false,"evidence_span":"来自当前消息的原文"}}

当前用户消息：{json.dumps(base.user_goal, ensure_ascii=False)}
领域语境：{json.dumps(base.domain_context, ensure_ascii=False)}
近期对话：{json.dumps(list(base.recent_conversation_context), ensure_ascii=False)}
Active Route：{json.dumps(base.active_route_context, ensure_ascii=False)}
确定性否定约束：{json.dumps(list(base.negative_constraints), ensure_ascii=False)}
"""


def _json_object(raw: Any) -> Mapping[str, Any] | None:
    if isinstance(raw, Mapping) and "content" in raw:
        raw = raw["content"]
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _conversation(
    base: CapabilityIntentDecision, *, reason: str, confidence: float,
    source: str, evidence_span: str = "", explicit: bool = False,
    wants_explanation: bool = False,
) -> CapabilityIntentDecision:
    confidence = max(0.0, min(1.0, confidence))
    return CapabilityIntentDecision(
        **{
            **base.evidence(),
            "desired_activity": "CONVERSATION",
            "outcome": "CONVERSATION_ONLY",
            "wants_explanation": wants_explanation,
            "confidence": confidence,
            "confidence_band": "HIGH" if confidence >= .78 else "LOW",
            "reason_code": reason,
            "source": source,
            "evidence_span": evidence_span,
            "explicit_activity_request": explicit,
        }
    )


def _reconcile_model(base: CapabilityIntentDecision, value: Mapping[str, Any]) -> CapabilityIntentDecision | None:
    required = {
        "outcome", "desired_activity", "confidence", "reason_code",
        "explicit_activity_request", "wants_self_attempt", "wants_explanation",
        "evidence_span",
    }
    missing = required - set(value)
    if missing:
        _logger.info(
            "[intent] model reply missing keys=%s -> regex fallback", sorted(missing)
        )
        return None
    # Extra keys used to discard the whole decision. Models routinely add a
    # "reason" or an echoed field, and throwing away an otherwise valid
    # classification silently downgrades the turn to the regex path -- which is
    # both wrong and invisible in production. Keep the decision, note the noise.
    extra = set(value) - required
    if extra:
        _logger.info("[intent] model reply has extra keys=%s (ignored)", sorted(extra))
    outcome = str(value.get("outcome") or "")
    activity = str(value.get("desired_activity") or "")
    reason = str(value.get("reason_code") or "").lower()
    span = str(value.get("evidence_span") or "").strip()
    explicit = value.get("explicit_activity_request") is True
    self_attempt = value.get("wants_self_attempt") is True
    wants_explanation = value.get("wants_explanation") is True
    try:
        confidence = max(0.0, min(1.0, float(value.get("confidence"))))
    except (TypeError, ValueError):
        return None
    if outcome not in CAPABILITY_OUTCOMES or activity != ACTIVITY_BY_OUTCOME[outcome]:
        return None
    if not re.fullmatch(r"[a-z0-9_\-]{1,80}", reason):
        return None
    if not span or _compact(span) not in _compact(base.user_goal):
        return None

    constraints = set(base.negative_constraints)
    if "NO_ARTIFACT" in constraints:
        return _conversation(
            base, reason="explicit_no_artifact", confidence=max(.98, confidence),
            source="structured_model_guarded", evidence_span=span,
            wants_explanation=wants_explanation,
        )
    if outcome == "CODE_LAB" and "NO_CODE_LAB" in constraints:
        return _conversation(
            base, reason="explicit_no_code_lab", confidence=max(.95, confidence),
            source="structured_model_guarded", evidence_span=span,
        )
    if outcome == "ASSESSMENT" and "NO_ASSESSMENT" in constraints:
        return _conversation(
            base, reason="explicit_no_assessment", confidence=max(.95, confidence),
            source="structured_model_guarded", evidence_span=span,
        )
    if outcome == "DEMONSTRATE" and "NO_DEMONSTRATION" in constraints:
        return _conversation(
            base, reason="explicit_no_demonstration", confidence=max(.98, confidence),
            source="structured_model_guarded", evidence_span=span,
            wants_explanation=True,
        )
    if "EXPLANATION_ONLY" in constraints and outcome != "CONVERSATION_ONLY":
        return _conversation(
            base, reason="explicit_explanation_only", confidence=max(.95, confidence),
            source="structured_model_guarded", evidence_span=span,
            wants_explanation=True,
        )

    if outcome == "CONVERSATION_ONLY":
        return _conversation(
            base, reason=reason, confidence=confidence,
            source="structured_model", evidence_span=span,
            explicit=explicit, wants_explanation=wants_explanation,
        )

    programming_context = " ".join([
        base.user_goal, base.domain_context, *base.recent_conversation_context,
    ])
    # A model may correctly resolve a short first-person turn-taking ellipsis
    # from the programming context while marking it as context-dependent rather
    # than lexically explicit.  That is eligible only for Code Lab and only
    # when the same structured decision also asserts self-attempt at very high
    # confidence.  The Active Route itself is deliberately excluded here.
    context_resolved_code_request = (
        outcome == "CODE_LAB" and self_attempt and confidence >= .88
        and _programming_context(programming_context)
    )
    if (not explicit and not context_resolved_code_request) or confidence < .78:
        return _conversation(
            base, reason="activity_not_explicit_or_confident", confidence=confidence,
            source="structured_model_guarded", evidence_span=span,
            explicit=explicit, wants_explanation=wants_explanation,
        )
    if outcome == "CODE_LAB":
        if not self_attempt or not _programming_context(programming_context):
            return _conversation(
                base, reason="code_lab_prerequisite_missing", confidence=confidence,
                source="structured_model_guarded", evidence_span=span,
                explicit=explicit, wants_explanation=wants_explanation,
            )

    return CapabilityIntentDecision(
        **{
            **base.evidence(),
            "desired_activity": activity,
            "outcome": outcome,
            "wants_self_attempt": self_attempt,
            "wants_explanation": wants_explanation,
            "wants_assessment": outcome == "ASSESSMENT",
            "wants_durable_material": outcome == "HANDOUT",
            "wants_step_by_step_action": outcome == "PRACTICAL_GUIDE",
            "confidence": confidence,
            "confidence_band": "HIGH",
            "reason_code": reason,
            "source": "structured_model",
            "evidence_span": span,
            "explicit_activity_request": explicit,
        }
    )


async def _structured_model_decision(base: CapabilityIntentDecision, *, llm: Any) -> CapabilityIntentDecision | None:
    try:
        raw = await llm.chat(
            messages=[{"role": "user", "content": _model_prompt(base)}],
            temperature=0.0,
            max_tokens=260,
            response_format={"type": "json_object"},
            use_cache=False,
            write_cache=False,
        )
    except Exception:
        return None
    value = _json_object(raw)
    return _reconcile_model(base, value) if value is not None else None


def _named_capability_fallback(base: CapabilityIntentDecision) -> CapabilityIntentDecision:
    """Degraded compatibility for exact product names, never paraphrases."""
    names = {
        "CODE_LAB": ("code lab", "代码练习"),
        "ASSESSMENT": ("assessment", "测评", "测验", "小测"),
        "PRACTICAL_GUIDE": ("practical guide", "实操指南"),
        "HANDOUT": ("handout", "讲义"),
        "PRESENTATION": ("ppt", "幻灯片", "演示文稿"),
        "MINDMAP": ("mindmap", "思维导图", "脑图"),
        "VIDEO": ("教学视频", "学习视频"),
        "STORYBOOK": ("教学绘本", "视觉绘本"),
    }
    text = base.user_goal.lower()
    for outcome, labels in names.items():
        label = next((item for item in labels if item in text), None)
        if label is None:
            continue
        if not _named_request_has_creation_action(base, outcome):
            return _conversation(
                base, reason="named_capability_mentioned_without_action", confidence=.9,
                source="named_capability_fallback", evidence_span=label,
            )
        if "NO_ARTIFACT" in base.negative_constraints \
                or outcome == "CODE_LAB" and "NO_CODE_LAB" in base.negative_constraints \
                or outcome == "ASSESSMENT" and "NO_ASSESSMENT" in base.negative_constraints:
            return _conversation(
                base, reason="named_capability_explicitly_vetoed", confidence=.99,
                source="named_capability_fallback", evidence_span=label,
            )
        if outcome == "CODE_LAB" and not _programming_context(
            " ".join([base.user_goal, base.domain_context, *base.recent_conversation_context])
        ):
            return _conversation(
                base, reason="named_code_lab_without_programming_context", confidence=.92,
                source="named_capability_fallback", evidence_span=label,
            )
        return CapabilityIntentDecision(
            **{
                **base.evidence(),
                "desired_activity": ACTIVITY_BY_OUTCOME[outcome],
                "outcome": outcome,
                "wants_self_attempt": outcome == "CODE_LAB",
                "wants_assessment": outcome == "ASSESSMENT",
                "wants_durable_material": outcome == "HANDOUT",
                "wants_step_by_step_action": outcome == "PRACTICAL_GUIDE",
                "confidence": .9,
                "confidence_band": "HIGH",
                "reason_code": "exact_named_capability",
                "source": "named_capability_fallback",
                "evidence_span": label,
                "explicit_activity_request": True,
            }
        )
    return _conversation(
        base, reason="semantic_model_unavailable", confidence=.0,
        source="semantic_safe_fallback",
    )


def _named_request_has_creation_action(base: CapabilityIntentDecision, outcome: str) -> bool:
    """Exact product names still need an explicit creation/open action."""
    text = _compact(base.user_goal)
    if outcome == "CODE_LAB":
        return bool(re.search(r"(?:open|run|use|打开|进入|使用|给我|请)", text, re.I))
    return bool(re.search(
        r"(?:做|生成|制作|整理|准备|请|帮我|给我|create|make|generate)",
        text,
        re.I,
    ))


def _natural_capability_fallback_v2(base: CapabilityIntentDecision) -> CapabilityIntentDecision:
    """Resolve high-signal action/object requests without a model."""
    text = _compact(base.user_goal)
    full_context = " ".join([base.user_goal, base.domain_context, *base.recent_conversation_context])
    patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("ASSESSMENT", (
            r"(?:给我|帮我|请|直接)?(?:出|来|做|生成|安排)(?:一套|几道|一组|一份)?[^，。！!?]{0,24}(?:真题|模拟题|练习题|测试题|题目)",
            r"(?:测一下我|测测我|考考我|给我测|做个小测)",
        )),
        ("PRESENTATION", (
            r"(?:帮我|请|给我|直接)?(?:做|生成|制作|整理|准备)(?:一个|一份|一套)?[^，。！!?]{0,24}(?:汇报|答辩|演示文稿|演示|演讲)",
        )),
        ("MINDMAP", (
            r"(?:帮我|请|给我|直接)?(?:做|生成|画|整理)(?:一张|一个|一份)?[^，。！!?]{0,24}(?:思维导图|脑图|概念图)",
        )),
        ("VIDEO", (
            r"(?:帮我|请|给我|直接)?(?:做|生成|制作|录)(?:一段|一个|一份)?[^，。！!?]{0,24}(?:教学视频|讲解视频|课程视频|视频)",
        )),
        ("STORYBOOK", (
            r"(?:帮我|请|给我|直接)?(?:做|生成|制作)(?:一本|一个|一份)?[^，。！!?]{0,24}(?:教学绘本|视觉绘本|绘本|故事书)",
        )),
        ("DEMONSTRATE", (
            r"(?:你)?(?:画一下|画出来|演示一下|可视化展示|把[^，。！!?]{0,18}(?:画出来|演示出来))",
            r"(?:用|在|拿)?(?:黑板|板书)[^，。！!?]{0,14}(?:讲|写|画|说|演示|推|做)",
            r"(?:你)?(?:边[^，。！!?]{0,6}边)[^，。！!?]{0,6}(?:画|写)",
        )),
        ("HANDOUT", (
            r"(?:帮我|请|给我|直接)?(?:整理|生成|做|制作|汇总|归纳)(?:一份|一个|一套)?[^，。！!?]{0,28}(?:资料|材料|讲义|知识卡|速查表|复习包|总结|手册)",
            r"(?:整理成|做成)(?:一份|一个|一套)?[^，。！!?]{0,28}(?:能保存|可保存|资料|材料)",
        )),
        ("PRACTICAL_GUIDE", (
            r"(?:给我|帮我|请|直接)?(?:制定|安排|规划|做)(?:一份|一个|一套)?[^，。！!?]{0,28}(?:计划|路线|步骤|清单|行动方案)",
            r"(?:我|现在)?(?:该|要|应该)怎么做|一步一步带我|告诉我下一步",
        )),
        ("CODE_LAB", (
            r"(?:我来|让我|带我|一起)?(?:写|敲|实现|补|跑)(?:个|一下|起来)?(?:代码|程序|函数|实现)?",
        )),
    )
    for outcome, candidates in patterns:
        if outcome == "CODE_LAB" and not _programming_context(full_context):
            continue
        # "我自己写板书" contains 写, but nobody is asking for code: the user is
        # claiming the pen, which is the opposite of a capability request.
        if outcome == "CODE_LAB" and re.search(r"(?:自己|我)(?:来|先)?(?:写|画)(?:黑板|板书|图|一下)", text):
            continue
        if outcome == "ASSESSMENT" and "NO_ASSESSMENT" in base.negative_constraints:
            continue
        if outcome == "CODE_LAB" and "NO_CODE_LAB" in base.negative_constraints:
            continue
        if outcome == "DEMONSTRATE" and "NO_DEMONSTRATION" in base.negative_constraints:
            continue
        evidence = next((match.group(0) for pattern in candidates if (match := re.search(pattern, text, re.I))), None)
        if not evidence:
            continue
        return CapabilityIntentDecision(
            **{
                **base.evidence(),
                "desired_activity": ACTIVITY_BY_OUTCOME[outcome],
                "outcome": outcome,
                "wants_self_attempt": outcome == "CODE_LAB",
                "wants_assessment": outcome == "ASSESSMENT",
                "wants_durable_material": outcome == "HANDOUT",
                "wants_step_by_step_action": outcome == "PRACTICAL_GUIDE",
                "confidence": .84,
                "confidence_band": "HIGH",
                "reason_code": "natural_action_object_request",
                "source": "natural_language_fallback",
                "evidence_span": evidence,
                "explicit_activity_request": True,
            }
        )
    return _conversation(base, reason="semantic_model_unavailable", confidence=.0, source="semantic_safe_fallback")


async def decide_capability_intent(
    message: str,
    *,
    domain_context: str = "",
    active_route_context: str = "",
    recent_conversation_context: Iterable[str] = (),
    llm: Any | None = None,
    use_model: bool = True,
) -> CapabilityIntentDecision:
    """Return a bounded activity decision; never execute a capability."""
    recent = tuple(
        str(item or "") for item in recent_conversation_context
        if str(item or "").strip()
    )[-6:]
    base = _base(
        message,
        domain_context=domain_context,
        active_route_context=active_route_context,
        recent_conversation_context=recent,
    )
    if "NO_ARTIFACT" in base.negative_constraints:
        return _conversation(
            base, reason="explicit_no_artifact", confidence=.99,
            source="deterministic_user_control",
        )
    if use_model:
        if llm is None:
            try:
                from services.llm_service_real import get_llm_service
                llm = await get_llm_service()
            except Exception:
                llm = None
        if llm is not None:
            resolved = await _structured_model_decision(base, llm=llm)
            if resolved is not None:
                if resolved.outcome != "CONVERSATION_ONLY":
                    return resolved
                # Only a direct request may repair a model false negative.
                # Mentioning an activity ("写代码的人很厉害") is not a request.
                if not re.search(r"(?:^|[。！？；])\s*(?:请|给我|帮我|替我|直接|生成|制作|做个|出几道|来个)", base.user_goal):
                    return resolved
                # A model may conservatively call an unmistakable action/object
                # request ordinary conversation.  Preserve model-primary
                # routing, but do not let that erase a bounded, explicit request
                # such as "给我一个小测" or "生成一张思维导图".
                named = _named_capability_fallback(base)
                if named.outcome != "CONVERSATION_ONLY":
                    return named
                natural = _natural_capability_fallback_v2(base)
                if natural.outcome != "CONVERSATION_ONLY":
                    return natural
                return resolved
    named = _named_capability_fallback(base)
    if named.outcome != "CONVERSATION_ONLY":
        return named
    return _natural_capability_fallback_v2(base)
