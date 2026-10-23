"""LLM semantic router: structured understanding only, never execution."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Optional

from pydantic import ValidationError

from services.workspace_artifacts import artifact_context
from services.workspace_task_contracts import (
    AgentTask,
    AgentType,
    ArtifactOperation,
    SemanticAction,
    SemanticRoute,
    TaskState,
)


logger = logging.getLogger(__name__)


_CHINESE_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _parse_question_count(value: str) -> Optional[int]:
    text = str(value or "").strip()
    if text.isdigit():
        count = int(text)
    elif text == "十":
        count = 10
    elif "十" in text:
        left, right = text.split("十", 1)
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        units = _CHINESE_DIGITS.get(right, 0) if right else 0
        count = tens * 10 + units
    else:
        count = _CHINESE_DIGITS.get(text, 0)
    return count if 1 <= count <= 20 else None


def _parse_literal_number(value: str) -> Optional[int]:
    """Parse a short literal number without applying an agent-specific limit."""
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, right = text.split("十", 1)
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        units = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + units
    return _CHINESE_DIGITS.get(text)


def _explicit_exam_slots(message: str) -> dict[str, Any]:
    """Recover only unambiguous exam parameters literally present in this turn.

    This is not an intent router and never invents defaults.  It is a guardrail
    after semantic routing so an LLM omission cannot make the UI ask for a
    count/type/difficulty the user already supplied.
    """
    text = str(message or "").strip()
    slots: dict[str, Any] = {}
    count_match = re.search(r"([0-9]{1,2}|[零一二两三四五六七八九十]{1,3})\s*道", text)
    if count_match:
        count = _parse_question_count(count_match.group(1))
        if count is not None:
            slots["question_count"] = count

    # Multiple choice is deliberately not coerced to single choice because the
    # current exam adapter does not advertise a multiple-choice capability.
    if re.search(r"(?:混合题|各种题型|题型(?:不限|都可以|随意))", text):
        slots["question_types"] = [
            "single_choice", "true_false", "fill_blank", "short_answer",
        ]
    elif re.search(r"(?:单项选择|单选|(?<!多)选择)题", text):
        slots["question_types"] = ["single_choice"]
    elif re.search(r"判断题", text):
        slots["question_types"] = ["true_false"]
    elif re.search(r"填空题", text):
        slots["question_types"] = ["fill_blank"]
    elif re.search(r"简答题", text):
        slots["question_types"] = ["short_answer"]

    if re.search(r"(?:基础|简单|入门)(?:的)?(?:单选|选择|判断|填空|简答)?题", text):
        slots["difficulty"] = "basic"
    elif re.search(r"(?:困难|高难|进阶|高级)(?:的)?(?:单选|选择|判断|填空|简答)?题", text):
        slots["difficulty"] = "advanced"
    elif re.search(r"(?:中等|适中)(?:难度)?(?:的)?(?:单选|选择|判断|填空|简答)?题", text):
        slots["difficulty"] = "intermediate"
    return slots


def _explicit_presentation_slots(message: str) -> dict[str, Any]:
    """Recover only literal PPT/classroom parameters from the current turn."""
    text = str(message or "").strip()
    slots: dict[str, Any] = {}
    if re.search(r"互动课堂|小课堂|微课|上课|边(?:看|学|听).{0,8}边?答题|讲.{0,8}答题", text):
        slots["delivery_mode"] = "interactive_lesson"
    elif re.search(r"PPT|幻灯片|演示文稿|课件", text, re.I):
        slots["delivery_mode"] = "presentation_only"
    duration_match = re.search(r"([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*分钟", text)
    if duration_match:
        duration = _parse_literal_number(duration_match.group(1))
        if duration is None or not 1 <= duration <= 60:
            raise ValueError("互动课堂时长需要在 1 到 60 分钟之间。")
        slots["duration_minutes"] = duration
    slide_match = re.search(r"([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*页(?:左右|上下|以内|大约)?", text)
    if slide_match:
        slide_count = _parse_question_count(slide_match.group(1))
        if slide_count is not None:
            slots["slide_count"] = slide_count
    if "基础概念" in text or re.search(r"先讲基础|从基础(?:开始|讲起)|基础入门|入门(?:内容|知识|概念)?", text):
        slots["learning_goal"] = "基础概念"
    elif "代码实现" in text:
        slots["learning_goal"] = "代码实现"
    return slots


def _explicit_video_slots(message: str) -> dict[str, Any]:
    """Recover literal standalone-video parameters without deciding intent."""
    text = str(message or "").strip()
    slots: dict[str, Any] = {}
    seconds = re.search(r"([0-9]{1,3}|[一二两三四五六七八九十]{1,3})\s*秒", text)
    minutes = re.search(r"([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*分钟", text)
    duration = None
    if seconds:
        duration = _parse_literal_number(seconds.group(1))
    elif minutes:
        literal = _parse_literal_number(minutes.group(1))
        duration = literal * 60 if literal is not None else None
    if duration is not None:
        if not 15 <= duration <= 600:
            raise ValueError("教学视频时长需要在 15 秒到 10 分钟之间。")
        slots["duration_sec"] = duration
    return slots


def _standalone_video_request(message: str) -> bool:
    text = str(message or "").strip()
    if re.search(r"互动课堂|小课堂|微课|上课|边(?:看|学|听).{0,8}边?答题|讲.{0,8}答题", text):
        return False
    return bool(re.search(r"(?:生成|制作|做|创建|来|给我).{0,16}(?:教学|讲解|学习)?视频|(?:教学|讲解|学习)视频", text))


def _standalone_lecture_request(message: str) -> bool:
    """Recognise only explicit creation language for a saved handout."""
    text = re.sub(r"\s+", "", str(message or ""))
    if not text or _looks_like_discussion(text):
        return False
    return bool(re.search(
        r"(?:生成|制作|创建|写|整理成|给我|来一份).{0,28}(?:讲义|学习文档|复习文档)"
        r"|(?:讲义|学习文档|复习文档).{0,20}(?:生成|制作|创建|保存)",
        text,
    ))


def _reconcile_explicit_slots(message: str, route: SemanticRoute) -> SemanticRoute:
    task_actions = {
        SemanticAction.NEW_TASK,
        SemanticAction.SUPPLEMENT,
        SemanticAction.REVISE,
        SemanticAction.REPLACE,
    }
    # A direct request to create a handout is an observable state-changing
    # command.  The model may otherwise downgrade material-grounded wording to
    # ordinary chat; keep topic extraction conservative and let the task state
    # machine ask for any missing topic.
    if _standalone_lecture_request(message):
        allowed = {
            "topic", "purpose", "detail_level", "sections", "export_formats",
            "source_artifact_id", "source_artifact_kind",
        }
        updates = {key: value for key, value in dict(route.slot_updates).items() if key in allowed}
        route = route.model_copy(update={
            "action": route.action if route.action in task_actions else SemanticAction.NEW_TASK,
            "agent_type": AgentType.LECTURE,
            "artifact_operation": None,
            "target_artifact_id": None,
            "slot_updates": updates,
        })
    # A standalone video request is a first-class VIDEO task.  Interactive
    # classroom language remains PRESENTATION; this only corrects the historical
    # compatibility rule that treated every occurrence of “视频” as a classroom.
    if route.action in task_actions and _standalone_video_request(message):
        updates = dict(route.slot_updates)
        allowed = {"topic", "duration_sec", "instruction"}
        updates = {key: value for key, value in updates.items() if key in allowed}
        updates.update(_explicit_video_slots(message))
        route = route.model_copy(update={
            "agent_type": AgentType.VIDEO,
            "slot_updates": updates,
        })
    # Cross-field semantic consistency comes before literal guardrails.  The
    # model may understand "边看边答题" correctly (include_interactions=true)
    # while naming the generic lecture agent.  Those presentation-only slots
    # are stronger evidence than the mismatched agent label and must not be
    # forwarded to the lecture adapter, where they would become a user-facing
    # 422.
    semantic_updates = dict(route.slot_updates)
    interactive_signal = (
        semantic_updates.get("delivery_mode") == "interactive_lesson"
        or semantic_updates.get("include_interactions") is True
    )
    if (
        route.action in task_actions
        and route.agent_type in {AgentType.LECTURE, AgentType.VIDEO, AgentType.PRESENTATION}
        and interactive_signal
    ):
        if route.agent_type == AgentType.LECTURE:
            semantic_updates = _lecture_updates_for_presentation(semantic_updates)
        semantic_updates["delivery_mode"] = "interactive_lesson"
        route = route.model_copy(update={
            "agent_type": AgentType.PRESENTATION,
            "slot_updates": semantic_updates,
        })

    presentation_explicit: dict[str, Any] = {}
    if (
        route.action in task_actions
        and route.agent_type in {AgentType.PRESENTATION, AgentType.VIDEO, AgentType.LECTURE}
    ):
        presentation_explicit = _explicit_presentation_slots(message)
    if (
        presentation_explicit.get("delivery_mode")
        and route.action in task_actions
        and route.agent_type in {AgentType.PRESENTATION, AgentType.VIDEO, AgentType.LECTURE}
        and not (
            route.agent_type == AgentType.LECTURE
            and re.search(r"讲义|文档", str(message or ""))
        )
    ):
        updates = dict(route.slot_updates)
        if route.agent_type == AgentType.LECTURE:
            updates = _lecture_updates_for_presentation(updates)
        for name in ("duration_minutes", "slide_count"):
            if name not in presentation_explicit and updates.get(name) not in (None, ""):
                updates.pop(name, None)
        updates.update(presentation_explicit)
        updates = _normalise_presentation_goal(updates)
        return route.model_copy(update={
            "agent_type": AgentType.PRESENTATION,
            "slot_updates": updates,
        })
    if (
        route.agent_type == AgentType.PRESENTATION
        and route.action in task_actions
    ):
        updates = dict(route.slot_updates)
        for name in ("duration_minutes", "slide_count"):
            if name not in presentation_explicit and updates.get(name) not in (None, ""):
                updates.pop(name, None)
        updates.update(presentation_explicit)
        updates = _normalise_presentation_goal(updates)
        return route.model_copy(update={"slot_updates": updates})
    if route.agent_type == AgentType.VIDEO and route.action in task_actions:
        updates = dict(route.slot_updates)
        allowed = {"topic", "duration_sec", "instruction"}
        updates = {key: value for key, value in updates.items() if key in allowed}
        updates.update(_explicit_video_slots(message))
        return route.model_copy(update={"slot_updates": updates})
    if (
        route.agent_type != AgentType.EXAM
        or route.action not in task_actions
    ):
        return route
    updates = dict(route.slot_updates)
    explicit = _explicit_exam_slots(message)
    # Count, type and difficulty have finite, directly observable syntax.  If
    # this turn does not mention one of them, discard any value guessed by the
    # LLM; on supplement/revision the persisted task keeps its previous value.
    # This makes "练习题" remain type-agnostic instead of silently becoming a
    # mixed paper, while still allowing the model to understand the topic.
    for name in ("question_count", "question_types", "difficulty"):
        if name not in explicit:
            updates.pop(name, None)
    # Literal values in the user's current message have precedence over a
    # router hallucination such as a hard-coded count of five.
    updates.update(explicit)
    return route.model_copy(update={"slot_updates": updates})


def _has_explicit_artifact_mutation(message: str) -> bool:
    """Return true only when this turn clearly asks to change saved content.

    A recent artifact is passive context.  Comparative teaching requests such
    as "讲详细一点" or "简单一点" therefore remain questions unless the user
    names a concrete edit or enters through the explicit edit UI context.
    """
    text = re.sub(r"\s+", "", str(message or ""))
    if re.search(
        r"(?:把|将).{0,80}(?:修改|改成|改为|改写|重写|更新|替换|删除|删掉|移除|"
        r"增加|添加|补充|写进|写入|插入|展开|合并|调整)",
        text,
    ):
        return True
    if re.search(
        r"(?:修改|编辑|改写|重写|更新)(?:这份|这个|这段|刚才的|上一个|当前的)?"
        r"(?:讲义|代码|程序|思维导图|导图|PPT|课件|互动课堂|课堂|文档|产物)",
        text,
    ):
        return True
    return bool(re.search(
        r"(?:在|往).{0,60}(?:讲义|代码|程序|思维导图|导图|PPT|课件|互动课堂|课堂|文档)"
        r"(?:里|中|上).{0,40}(?:增加|添加|加上|补充|写入|写进|删除|删掉|移除|替换)",
        text,
    ))


def _looks_like_discussion(message: str) -> bool:
    """Detect questions, evaluations and feelings that must not mutate state."""
    text = re.sub(r"\s+", "", str(message or ""))
    return bool(re.search(
        r"[？?]|为什么|什么意思|怎么(?:样|回事)|会不会|能不能|是否|好不好|怪不怪|"
        r"你觉得|你认为|我(?:有点)?担心|含金量|评价一下|怎么看|靠谱吗",
        text,
    ))


def _has_explicit_task_execution(message: str) -> bool:
    """Require observable execution language before entering an agent path."""
    text = re.sub(r"\s+", "", str(message or ""))
    english = re.sub(r"\s+", " ", str(message or "").strip().lower())
    if not text:
        return False
    if re.search(
        r"\b(?:please\s+)?(?:create|generate|make|build|update|extend|revise|merge|"
        r"replace|run|export|open|search|find|confirm|start|continue|cancel)\b",
        english,
    ):
        return True
    if re.search(r"\b(?:i\s+(?:want|would\s+like)\s+to\s+learn|help\s+me\s+learn|teach\s+me)\b", english):
        return True
    if re.fullmatch(r"(?:(?:好|好的|可以|确认|开始|现在开始|继续|取消|停止|算了|没问题)(?:吧|呀|啊|呢|了)?[。！!，,、\s]*)+", text):
        return True
    if re.search(
        r"(?:帮我|给我|请你?|我要|我想要|想让你|能不能帮我|麻烦你).{0,18}"
        r"(?:生成|制作|创建|写|出|做|搜索|查找|检索|运行|导出|打开|修改|删除|取消|重新生成)",
        text,
    ):
        return True
    if re.search(
        r"(?:把|将).{0,100}(?:运行|导出|打开|修改|改成|改为|改写|重写|更新|"
        r"替换|删除|删掉|移除|增加|添加|补充|写入|重新生成)",
        text,
    ):
        return True
    if re.search(
        r"(?:我想|我要|我只要|给我|请).{0,100}(?:互动课堂|小课堂|微课|课件|PPT|"
        r"幻灯片|演示文稿|边看边答题|边学边答题)",
        text,
    ):
        return True
    if re.search(r"(?:我要|我想|想要|帮我|带我|教我)(?:开始)?(?:学习|学会|入门|掌握).{1,100}", text):
        return True
    if re.search(
        r"^(?:根据|基于|用).{0,80}(?:生成|制作|创建|出|搜索|查找|检索|整理成)",
        text,
    ):
        return True
    if re.search(
        r"^(?:生成|制作|创建|搜索|查找|检索|运行|导出|打开|修改|删除|取消|重新生成)",
        text,
    ):
        return True
    # Practice/self-quiz requests are explicit state-change asks even when the
    # topic comes from conversational context ("练一下", "考考我", "出几道题").
    # The LLM route still has to agree this is a task; this gate only stops
    # vetoing observable practice verbs because they lack a imperative prefix.
    if re.search(
        r"(?:考考我|考(?:一?下|一?考)我|测测我|测(?:一?下|验)我|"
        r"(?:出|做|练|来|生成|搞|整).{0,6}(?:几\s*(?:道|个|组)\s*)?(?:题|测验|试卷)|"
        r"练(?:习)?一?下|自测一?下|小测一?下)",
        text,
    ):
        return True
    return bool(re.search(
        r"(?:来一?个|想看一?个).{0,30}(?:互动课堂|微课|PPT|幻灯片|演示文稿|"
        r"讲义|题目|试题|思维导图|代码示例)",
        text,
    ))


def _is_explicit_pending_task_reply(
    message: str, route: SemanticRoute, current_task: Optional[AgentTask],
) -> bool:
    """Allow concise answers to the task's last slot question, not topic switches."""
    if current_task is None or not route.slot_updates or _looks_like_discussion(message):
        return False
    if current_task.state not in {
        TaskState.COLLECTING_REQUIREMENTS,
        TaskState.AWAITING_CONFIRMATION,
    }:
        return False
    return len(str(message or "").strip()) <= 160


def _apply_execution_gate(
    message: str, route: SemanticRoute, current_task: Optional[AgentTask],
) -> SemanticRoute:
    """Keep chat as the default unless this turn has explicit state-change evidence."""
    confirmation_text = re.sub(r"[\s。！!，,、]+", "", str(message or ""))
    explicit_pending_confirmation = bool(
        current_task is not None
        and current_task.state == TaskState.AWAITING_CONFIRMATION
        and re.fullmatch(
            r"(?:好|好的|可以|没问题|确认|开始|现在开始|继续)"
            r"(?:生成|执行|制作)?(?:并)?(?:保存)?(?:吧|呀|啊)?",
            confirmation_text,
        )
    )
    if explicit_pending_confirmation:
        return route.model_copy(update={
            "action": SemanticAction.CONFIRM,
            "agent_type": current_task.agent_type,
            "artifact_operation": None,
            "target_artifact_id": None,
            "slot_updates": {},
            "confidence": 1.0,
            "reason_code": "explicit_pending_confirmation",
        })
    if route.action in {SemanticAction.QUESTION, SemanticAction.NORMAL_CHAT}:
        return route
    explicit_execution = _has_explicit_task_execution(message)
    discussion = _looks_like_discussion(message)
    if route.action == SemanticAction.ARTIFACT_ACTION:
        operation_is_explicit = (
            route.artifact_operation == ArtifactOperation.REVISE
            and _has_explicit_artifact_mutation(message)
        ) or (
            route.artifact_operation in {
                ArtifactOperation.RUN, ArtifactOperation.EXPORT, ArtifactOperation.OPEN,
            }
            and explicit_execution
        )
        if operation_is_explicit:
            return route
    elif route.action in {SemanticAction.CONFIRM, SemanticAction.CANCEL}:
        if current_task is not None and explicit_execution and not discussion:
            return route
    elif route.action in {SemanticAction.SUPPLEMENT, SemanticAction.REVISE}:
        if (explicit_execution and not discussion) or _is_explicit_pending_task_reply(message, route, current_task):
            return route
    elif route.action in {SemanticAction.NEW_TASK, SemanticAction.REPLACE}:
        if explicit_execution and not (
            discussion and not re.search(r"(?:帮我|给我|请|我要|想要|想让你)", str(message or ""))
        ):
            return route
    fallback_action = SemanticAction.QUESTION if discussion else SemanticAction.NORMAL_CHAT
    return route.model_copy(update={
        "action": fallback_action,
        "agent_type": None,
        "artifact_operation": None,
        "target_artifact_id": None,
        "slot_updates": {},
        "reason_code": "no_explicit_state_change",
    })


def _reconcile_explicit_learning_graph_update(
    message: str,
    route: SemanticRoute,
    *,
    active_artifact: Optional[dict],
    artifact_candidates: Iterable[dict],
) -> SemanticRoute:
    """Recover an explicit graph update when the semantic model calls it chat.

    This guardrail recognizes only a named learning/knowledge graph plus an
    unambiguous mutation verb. Graph identity is intentionally not copied into
    semantic slots; the owned persisted conversation remains the sole identity
    authority in the workspace endpoint.
    """
    text = str(message or "").strip()
    if not text:
        return route
    graph_noun = r"(?:learning\s+(?:graph|map)|knowledge\s+graph|学习图谱|学习图|知识图谱)"
    mode_patterns = (
        (
            "REPLACE_SOURCE",
            rf"(?:replace|swap|change).{{0,40}}(?:source|material).{{0,80}}{graph_noun}|"
            rf"{graph_noun}.{{0,80}}(?:replace|swap|change).{{0,40}}(?:source|material)|"
            rf"(?:替换|更换).{{0,30}}(?:来源|资料|材料).{{0,60}}{graph_noun}|"
            rf"{graph_noun}.{{0,60}}(?:替换|更换).{{0,30}}(?:来源|资料|材料)",
        ),
        (
            "EXTEND",
            rf"(?:extend|expand|add\s+to).{{0,80}}{graph_noun}|{graph_noun}.{{0,80}}(?:extend|expand)|"
            rf"(?:扩展|拓展|补充).{{0,60}}{graph_noun}|{graph_noun}.{{0,60}}(?:扩展|拓展|补充)",
        ),
        (
            "REVISE",
            rf"(?:revise|update|modify|edit).{{0,80}}{graph_noun}|{graph_noun}.{{0,80}}(?:revise|update|modify|edit)|"
            rf"(?:修订|修改|更新|调整).{{0,60}}{graph_noun}|{graph_noun}.{{0,60}}(?:修订|修改|更新|调整)",
        ),
        (
            "MERGE",
            rf"(?:merge|combine).{{0,80}}{graph_noun}|{graph_noun}.{{0,80}}(?:merge|combine)|"
            rf"(?:合并|融合).{{0,60}}{graph_noun}|{graph_noun}.{{0,60}}(?:合并|融合)",
        ),
    )
    update_mode = next(
        (mode for mode, pattern in mode_patterns if re.search(pattern, text, re.I | re.S)),
        None,
    )
    if update_mode is None:
        return route

    candidates: list[dict] = []
    if isinstance(active_artifact, dict):
        candidates.append(active_artifact)
    candidates.extend(item for item in artifact_candidates if isinstance(item, dict))
    graph_artifact = next(
        (item for item in candidates if item.get("kind") == "learning_graph"),
        None,
    )
    updates = dict(route.slot_updates) if route.agent_type == AgentType.LEARNING_GRAPH else {}
    updates["user_goal"] = text[:2000]
    updates["update_mode"] = update_mode
    if graph_artifact is not None and not updates.get("domain"):
        domain = str(graph_artifact.get("domain") or "").strip()
        if domain:
            updates["domain"] = domain[:160]
    return SemanticRoute(
        action=SemanticAction.NEW_TASK,
        agent_type=AgentType.LEARNING_GRAPH,
        artifact_operation=None,
        target_artifact_id=None,
        slot_updates=updates,
        confidence=1.0,
        reason_code="explicit_learning_graph_update",
    )


def _lecture_updates_for_presentation(updates: dict[str, Any]) -> dict[str, Any]:
    """Translate/drop only known lecture-owned slots during type correction."""
    converted = dict(updates)
    purpose = str(converted.pop("purpose", "") or "").strip()
    if purpose and not converted.get("learning_goal"):
        converted["learning_goal"] = purpose
    # These fields belong to the lecture adapter.  PPTX export is inherent;
    # the others cannot be forwarded without changing their meaning.
    for key in ("detail_level", "sections", "export_formats", "source_artifact_kind"):
        converted.pop(key, None)
    return converted


def _normalise_presentation_goal(updates: dict[str, Any]) -> dict[str, Any]:
    converted = dict(updates)
    goal = re.sub(r"\s+", "", str(converted.get("learning_goal") or ""))
    if goal and re.fullmatch(
        r"(?:[0-9一二两三四五六七八九十]+分钟)?(?:内)?(?:快速|短时|高效)?学习(?:一下)?",
        goal,
    ):
        converted.pop("learning_goal", None)
    return converted


def _reconcile_trusted_lecture_source(
    route: SemanticRoute,
    active_artifact: Optional[dict],
) -> SemanticRoute:
    """Never let the semantic model invent an artifact source identity.

    Uploaded materials are attached separately through the verified
    ``source_context`` path.  ``source_artifact_kind`` and
    ``source_artifact_id`` are valid only when they can be replaced by the
    currently active, server-projected artifact.
    """
    if route.agent_type != AgentType.LECTURE:
        return route
    updates = dict(route.slot_updates)
    if "source_artifact_kind" not in updates and "source_artifact_id" not in updates:
        return route
    trusted_kind = str((active_artifact or {}).get("kind") or "").strip()
    trusted_id = (active_artifact or {}).get("artifact_id")
    if trusted_kind in {"lecture", "exam", "exam_result", "code"} and trusted_id:
        updates["source_artifact_kind"] = trusted_kind
        updates["source_artifact_id"] = int(trusted_id)
    else:
        updates.pop("source_artifact_kind", None)
        updates.pop("source_artifact_id", None)
    return route.model_copy(update={"slot_updates": updates})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def build_semantic_prompt(
    *, message: str, current_task: Optional[AgentTask],
    active_artifact: Optional[dict], related_context: Optional[Iterable[str]] = None,
    artifact_candidates: Optional[Iterable[dict]] = None,
) -> str:
    task_summary = None
    if current_task:
        safe_slot_names = {
            AgentType.EXAM: {"topic", "question_count", "question_types", "difficulty"},
            AgentType.LECTURE: {"topic", "purpose", "detail_level", "sections", "export_formats"},
            AgentType.CODE: {"language", "topic", "goal", "input_output_requirement", "run_after_generation"},
            AgentType.PRESENTATION: {"topic", "delivery_mode", "learning_goal", "level", "duration_minutes", "slide_count", "presentation_style", "include_interactions"},
            AgentType.MINDMAP: {"topic", "source_scope", "purpose", "instruction"},
            AgentType.VIDEO: {"topic", "duration_sec", "instruction"},
            AgentType.SEARCH: {"query", "source_scope", "limit"},
            AgentType.LEARNING_GRAPH: {"user_goal", "domain", "update_mode"},
        }.get(current_task.agent_type, set())
        task_summary = {
            "agent_type": current_task.agent_type.value,
            "state": current_task.state.value,
            "slots": {
                key: value for key, value in current_task.slots.items()
                if key in safe_slot_names and value not in (None, "", [])
            },
        }
    artifact_summary = json.loads(artifact_context(active_artifact)) if active_artifact else None
    candidate_summaries = [
        json.loads(artifact_context(item))
        for item in list(artifact_candidates or [])[:5]
        if isinstance(item, dict)
    ]
    related = [str(item)[:240] for item in list(related_context or [])[:3]]
    return f"""你是 AI 学习工作区的语义路由器，只理解这一轮表达，不执行任务、不修改数据库、不生成聊天回答。

只输出一个 JSON 对象，字段必须严格为：
{{"action":"new_task|supplement|revise|confirm|cancel|replace|question|normal_chat|artifact_action","agent_type":"exam|lecture|code|presentation|video|mindmap|search|learning_graph|null","artifact_operation":"run|revise|export|open|null","slot_updates":{{}},"confidence":0.0,"reason_code":"snake_case"}}

判断规则：
1. new_task 是新建任务；supplement 是补充缺失参数；revise 是修改当前参数；confirm 是明确同意执行；cancel 是清除当前任务；replace 是换成另一类智能体任务。
2. question 是询问当前任务、当前产物或能力，不能执行且不能清除 pending task；normal_chat 是与任务无关的普通聊天。
3. 普通聊天是默认通道。只有明确执行、修改、确认、取消或回答刚刚所问参数时才进入任务；低置信度一律回普通聊天，不得用任务澄清阻断用户。
4. 只把用户明确表达的内容写入 slot_updates，不用默认值覆盖用户要求。
5. exam slots: topic, question_count, question_types(single_choice|true_false|fill_blank|short_answer), difficulty(basic|intermediate|advanced|adaptive), source_artifact_id, additional_requirements。
   用户说“练习题、题目、测验、考试、出题”是在请求 exam；“基于/根据（这份/刚才的）讲义、文档、代码、导图 出题/出练习/出测验”同样是请求 exam 且必须绑定来源产物 source_artifact_id/source_artifact_kind，绝不能路由到 lecture 或 code；只有明确要求代码、程序、编程示例或运行代码时才是 code，不能因为出现“生成”就路由到 code。
   “基础/简单/入门”“中等”“困难/进阶”是 difficulty 修饰语，不属于 topic；例如“3 道玉石基础判断题”应拆为 topic=玉石、question_count=3、question_types=[true_false]、difficulty=basic。
   一句话已经给齐 topic、question_count、question_types 时必须一次全部提取，后端将直接复述确认，不能再次追问已明确的参数。
   用户说“基于这份讲义出题”“根据刚才的代码出练习”“围绕这份文档生成测验”等（来源产物 + 出题/练习/测验动作）时，必须路由到 exam：topic 取该产物的学习主题，并填写 source_artifact_id 与 source_artifact_kind 绑定来源；这里的“讲义/文档/代码”是出题来源，不是生成目标，绝不能路由到 lecture 或 code。
6. lecture slots: topic, purpose, detail_level, sections, export_formats, source_artifact_id, source_artifact_kind。
7. code slots: language, topic, goal, input_output_requirement, run_after_generation。语言未说明时不得自行填写 Python。
8. presentation 是 PPT/互动课堂智能体。slots: topic, delivery_mode(presentation_only|interactive_lesson), learning_goal, level, duration_minutes, slide_count(1-30), presentation_style, include_interactions, source_artifact_id。
   明确说“PPT/幻灯片/演示文稿”时 delivery_mode=presentation_only；说“互动课堂/微课/上课/边学边答题”时 delivery_mode=interactive_lesson。
9. video 是独立教学视频智能体。slots: topic, duration_sec(15-600), instruction。明确要求“生成/制作教学视频、讲解视频、学习视频”时使用 video；如果用户要求互动课堂、微课、边看边答题则仍使用 presentation。
10. mindmap 是思维导图智能体。slots: topic, source_scope, purpose, instruction。source_material_ids 由后端根据当前会话资料绑定，模型不得编造 ID。
10. search 是权威资料搜索智能体。slots: query, source_scope(official_first|academic|all), limit。用户明确要求“搜索、查找、检索权威资料/高校课程/论文/前沿研究”时才调用；普通知识问答不能调用。默认 official_first，明确要求论文或前沿研究时使用 academic。
11. learning_graph 是正式学习安排编排能力。slots: user_goal, domain, update_mode(CREATE|EXTEND|REVISE|REPLACE_SOURCE|MERGE)。source_material_ids、graph_id、graph_revision 和 replaced_source_ref_ids 只能由后端已验证上下文补入，模型不得编造。
   用户表达明确、可执行的持续学习意图时使用 new_task + learning_graph，例如“我要学习 KNN”“帮我学会二次函数”“带我入门机器学习”“I want to learn calculus”。user_goal 保留用户目标，domain 填写能从目标可靠判断的学科领域，update_mode=CREATE；这类请求是在请系统安排学习，不是普通问答，也不是要求直接生成讲义。
   用户只问一个知识问题、要求解释或举例时仍走 question/normal_chat。已有学习安排的更新，只有明确要求“更新/扩展/修订/合并学习图谱或学习安排”时才使用对应 update_mode。
   只制作 PPT 时 topic 已明确即可确认；互动课堂缺少 learning_goal 或 duration_minutes 时只追问缺失项，不能重复追问用户已给参数。
10. 用户说“这份讲义”“刚才的代码”“我的错题”等指代时，只能在活动产物匹配时填写 source_artifact_id 和 source_artifact_kind。
11. 活动产物为 exam_result 且用户要求根据错题生成讲义时，来源必须绑定该产物；不要把“一个讲述我错题”当成标题。topic 可以留空，由讲义适配器生成中性标题。
12. 当前任务等待确认时，疑问句仍可能是 question，不能机械当作确认或参数修改。
13. 对已有产物执行运行、修改、导出或打开时使用 artifact_action，并填写 artifact_operation；不要创建新任务。解释、追问或教学说明仍是 question，不能当作 artifact_action。
14. target_artifact_id 由后端依据活动产物或当前会话最近的同类候选产物绑定，模型不得输出这个字段。存在唯一匹配候选时可使用 artifact_action；有多个歧义候选或没有匹配候选时降低置信度以便澄清。
15. 当前活动产物和最近产物都只是被动上下文，不能因为它存在就推断用户要修改它。“再讲详细一点”“简单解释一下”“举个例子”“为什么这样写”默认是 question。只有用户明确要求把内容写进、改成、删除、替换、更新已有产物时才是 artifact_action/revise。
16. 评价、讨论、解释、情绪表达和话题切换都是 question/normal_chat；”这个视频做出来会不会很假”是在聊视频，不是修改或生成视频。无法证明用户要改变后台状态时必须选择聊天。
17. 用户要求基于刚才的聊天、讲解或通话内容出题、练习或测验时（包括只给出练习动作、主题需要从上下文推断的表达），属于 new_task + exam：topic 从”少量相关上下文”中提取其中可辨识的学习主题，不得凭空编造；question_count、question_types、difficulty 只在用户本轮明确说出时填写。只要上下文里存在可辨识的学习主题，这就是明确的任务请求，confidence 应给出 0.85 以上；上下文里没有任何可辨识学习主题时才降低置信度。

当前任务摘要：{_json(task_summary)}
当前活动产物摘要：{_json(artifact_summary)}
当前会话最近产物候选：{_json(candidate_summaries)}
少量相关上下文：{_json(related)}
用户当前消息：{_json(str(message or '').strip())}
"""


def parse_semantic_route(raw: Any) -> SemanticRoute:
    if isinstance(raw, dict):
        value = raw
    else:
        text = str(raw or "").strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S | re.I)
        if fenced:
            text = fenced.group(1).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise ValueError("semantic router returned invalid JSON") from exc
            try:
                value = json.loads(match.group(0))
            except json.JSONDecodeError as nested:
                raise ValueError("semantic router returned invalid JSON") from nested
    if isinstance(value, dict):
        value = _normalise_model_enum_aliases(value)
    try:
        return SemanticRoute.model_validate(value)
    except ValidationError as exc:
        raise ValueError("semantic router output failed schema validation") from exc


def _normalise_model_enum_aliases(value: dict[str, Any]) -> dict[str, Any]:
    """Normalise harmless model vocabulary drift, never unknown fields.

    The validated contract remains strict: this only translates well-known
    synonyms for enum/diagnostic fields.  Extra keys and unsupported values are
    still rejected by ``SemanticRoute``.
    """
    normalised = dict(value)
    action = str(normalised.get("action") or "").strip().lower().replace("-", "_")
    action_aliases = {
        "capability": "question", "capability_query": "question",
        "query": "question", "ask": "question",
        "explain": "question", "explanation": "question",
        "chat": "normal_chat", "conversation": "normal_chat",
        "new": "new_task", "create": "new_task", "generate": "new_task",
        "update": "revise", "modify": "revise",
        "approve": "confirm", "start": "confirm", "proceed": "confirm",
        "abort": "cancel", "stop": "cancel",
        "switch": "replace", "provide_details": "supplement",
        "artifact_operation": "artifact_action", "operate_artifact": "artifact_action",
    }
    if action in action_aliases:
        normalised["action"] = action_aliases[action]

    raw_agent = normalised.get("agent_type")
    if raw_agent is not None:
        agent = str(raw_agent).strip().lower().replace("-", "_")
        if agent in {"", "null", "none"}:
            normalised["agent_type"] = None
            agent = ""
        if agent.endswith("_agent"):
            agent = agent[:-6]
        agent_aliases = {
            "ppt": "presentation", "powerpoint": "presentation",
            "slides": "presentation", "slide_deck": "presentation",
            "interactive_lesson": "presentation", "classroom": "presentation",
            "question": "exam", "quiz": "exam", "test": "exam",
            "document": "lecture", "handout": "lecture",
            "coding": "code", "programming": "code",
        }
        if agent in agent_aliases:
            normalised["agent_type"] = agent_aliases[agent]

    raw_operation = normalised.get("artifact_operation")
    if raw_operation is not None:
        operation = str(raw_operation).strip().lower().replace("-", "_")
        if operation in {"", "null", "none"}:
            normalised["artifact_operation"] = None

    reason_code = str(normalised.get("reason_code") or "").strip().lower()
    if reason_code and not re.fullmatch(r"[a-z0-9_\-]{1,80}", reason_code):
        normalised["reason_code"] = "model_route"
    return normalised


def _unwrap_llm_content(raw: Any) -> Any:
    if isinstance(raw, dict) and "content" in raw:
        return raw["content"]
    return raw


def _build_schema_repair_prompt(*, message: str, raw: Any) -> str:
    return f"""下面是语义路由器的一次输出。它没有通过严格 schema 校验。
只修复格式、枚举值和字段类型，不得改变原本识别出的用户意图，不得执行任务，也不要回答用户。
只输出一个 JSON 对象，且只能包含这五个字段：
{{"action":"new_task|supplement|revise|confirm|cancel|replace|question|normal_chat|artifact_action","agent_type":"exam|lecture|code|presentation|video|mindmap|search|learning_graph|null","artifact_operation":"run|revise|export|open|null","slot_updates":{{}},"confidence":0.0,"reason_code":"snake_case"}}

用户原话：{_json(str(message or '').strip())}
待修复输出：{_json(str(raw or '')[:2000])}
"""


async def _recover_missing_presentation_topic(
    *, message: str, route: SemanticRoute, llm,
) -> SemanticRoute:
    if not (
        route.agent_type == AgentType.PRESENTATION
        and route.action in {
            SemanticAction.NEW_TASK,
            SemanticAction.SUPPLEMENT,
            SemanticAction.REVISE,
            SemanticAction.REPLACE,
        }
        and not str(route.slot_updates.get("topic") or "").strip()
    ):
        return route
    prompt = f"""你只负责从用户原话中提取一个明确出现的学习主题，不做任务分类、不补写默认主题。
只输出 JSON：{{"topic":"明确主题"}}；如果原话没有主题，输出 {{"topic":null}}。
不要把 PPT、课件、互动课堂、视频、五分钟、基础、简洁等形式或要求当成主题。
示例："给我来个能边学边答题的队列小课堂" -> {{"topic":"队列"}}
用户原话：{_json(str(message or '').strip())}
只提取用户明确说出的学习主题。"""
    try:
        raw = _unwrap_llm_content(await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=80,
        ))
        text = str(raw or "").strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S | re.I)
        if fenced:
            text = fenced.group(1).strip()
        value = json.loads(text)
        if not isinstance(value, dict) or set(value) != {"topic"}:
            return route
        topic = str(value.get("topic") or "").strip()[:160]
        if not topic or topic.lower() in {"ppt", "课件", "互动课堂", "视频", "演示文稿"}:
            return route
        updates = dict(route.slot_updates)
        updates["topic"] = topic
        return route.model_copy(update={"slot_updates": updates})
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning("presentation topic recovery returned invalid output")
        return route


def deterministic_task_route(
    *,
    message: str,
    current_task: Optional[AgentTask] = None,
    active_artifact: Optional[dict] = None,
) -> Optional[SemanticRoute]:
    """Resolve explicit task controls before the semantic model is consulted."""
    text = " ".join(str(message or "").split()).strip()
    compact = re.sub(r"\s+", "", text)
    if not text:
        return None

    if current_task is not None and current_task.state in {
        TaskState.COLLECTING_REQUIREMENTS, TaskState.AWAITING_CONFIRMATION,
    }:
        if re.fullmatch(r"(?:取消|停止|算了|不要了)[。！!，,\s]*", compact):
            return SemanticRoute(action=SemanticAction.CANCEL, agent_type=current_task.agent_type, confidence=1.0, reason_code="deterministic_pending_cancel")
        if current_task.state == TaskState.AWAITING_CONFIRMATION and re.fullmatch(
            r"(?:好|好的|可以|确认|开始|现在开始|就这样|没问题)(?:生成|执行|制作)?(?:吧|呀|啊)?[。！!，,\s]*",
            compact,
        ):
            return SemanticRoute(action=SemanticAction.CONFIRM, agent_type=current_task.agent_type, confidence=1.0, reason_code="deterministic_pending_confirm")
        if current_task.agent_type == AgentType.EXAM:
            updates = _explicit_exam_slots(text)
            if not str(current_task.slots.get("topic") or "").strip() and not updates:
                if len(text) <= 80 and not re.fullmatch(r"(?:开始|继续|好|好的|可以|确认|就这样)", compact):
                    updates["topic"] = text.strip(" ，。！？,.!?")
            if updates:
                return SemanticRoute(action=SemanticAction.SUPPLEMENT, agent_type=AgentType.EXAM, slot_updates=updates, confidence=1.0, reason_code="deterministic_pending_exam_slots")

    exam_command = bool(re.search(r"(?:出|生成|创建|做|来|整|搞).{0,50}(?:试卷|考试|测验|练习题|题目|试题|考题)", compact))
    derived_exam = bool(re.search(r"(?:基于|根据|用).{0,50}(?:这个|这份|刚才|当前).{0,30}(?:出|生成|做).{0,20}(?:题|试卷|测验)", compact))
    if exam_command or derived_exam:
        updates = _explicit_exam_slots(text)
        topic_match = re.search(
            r"(?:出|生成|创建|做)(?:一份|一个|几道|点)?\s*([A-Za-z0-9\u4e00-\u9fff+#.\- ]{1,80}?)\s*(?:试卷|考试|测验|练习题|题目|试题|考题)",
            text,
        )
        if topic_match:
            topic = topic_match.group(1).strip(" 的，。")
            if topic:
                updates["topic"] = topic
        if isinstance(active_artifact, dict) and active_artifact.get("artifact_id") and (derived_exam or not updates.get("topic")):
            updates["source_artifact_id"] = int(active_artifact["artifact_id"])
            updates.setdefault("topic", str(active_artifact.get("topic") or active_artifact.get("title") or "当前内容"))
        return SemanticRoute(action=SemanticAction.NEW_TASK, agent_type=AgentType.EXAM, slot_updates=updates, confidence=1.0, reason_code="deterministic_exam_request")

    lecture_command = bool(re.search(r"(?:生成|创建|做|写|整理成|换成).{0,50}(?:讲义|学习资料|学习文档)", compact))
    if lecture_command:
        # 主题依赖上下文的讲义请求（"基于刚才上传的资料…"）在消息文本里往往没有主题，
        # 字面截取会把"简短""新的复习"这类修饰语当成主题，还会抢在语义模型之前短路、
        # 绕过可信来源守卫。这类请求交给语义路由；模型误判为聊天时有 standalone 守卫兜底。
        contextual_source = bool(re.search(
            r"(?:基于|根据|用|来自|参考|按照).{0,30}?(?:这份|这个|刚才|当前|上传|现有|上一篇)"
            r"|(?:基于|根据|用|来自|参考|按照).{0,30}?(?:资料|材料|文档|讲义|内容|文件)"
            r"|(?:刚才|当前|这份|这个|上传的).{0,20}?(?:资料|材料|文档|内容|讲义)",
            compact,
        ))
        topic_match = re.search(r"(?:生成|创建|做|写)(?:一份|一个)?\s*(.{1,80}?)\s*(?:讲义|学习资料|学习文档)", text)
        candidate_topic = topic_match.group(1).strip(" 的，。") if topic_match else ""
        qualifier_only = candidate_topic in {
            "简短", "详细", "完整", "系统", "深入", "简单", "快速", "清晰", "简要", "新的", "全新",
        }
        if contextual_source or qualifier_only:
            return None
        updates: dict[str, Any] = {}
        if candidate_topic:
            updates["topic"] = candidate_topic
        if isinstance(active_artifact, dict) and active_artifact.get("artifact_id") and not updates.get("topic"):
            updates.update({
                "topic": str(active_artifact.get("topic") or active_artifact.get("title") or "当前内容"),
                "source_artifact_id": int(active_artifact["artifact_id"]),
                "source_artifact_kind": str(active_artifact.get("kind") or "") or None,
            })
        return SemanticRoute(action=SemanticAction.NEW_TASK, agent_type=AgentType.LECTURE, slot_updates=updates, confidence=1.0, reason_code="deterministic_lecture_request")
    return None


async def route_semantically(
    *, message: str, current_task: Optional[AgentTask] = None,
    active_artifact: Optional[dict] = None,
    artifact_candidates: Optional[Iterable[dict]] = None,
    related_context: Optional[Iterable[str]] = None,
    llm=None,
) -> SemanticRoute:
    artifact_candidates = list(artifact_candidates or [])
    deterministic = deterministic_task_route(
        message=message, current_task=current_task, active_artifact=active_artifact,
    )
    if deterministic is not None:
        return deterministic
    if llm is None:
        from services.llm_service_real import get_llm_service
        llm = await get_llm_service()
    prompt = build_semantic_prompt(
        message=message,
        current_task=current_task,
        active_artifact=active_artifact,
        artifact_candidates=artifact_candidates,
        related_context=related_context,
    )
    raw = await llm.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=350,
    )
    raw = _unwrap_llm_content(raw)
    try:
        route = parse_semantic_route(raw)
    except ValueError:
        logger.warning(
            "semantic router returned invalid structured output; attempting one schema repair raw=%r",
            str(raw or "")[:1000],
        )
        repaired = await llm.chat(
            messages=[{"role": "user", "content": _build_schema_repair_prompt(message=message, raw=raw)}],
            temperature=0.0,
            max_tokens=350,
        )
        repaired_raw = _unwrap_llm_content(repaired)
        try:
            route = parse_semantic_route(repaired_raw)
        except ValueError:
            logger.warning("semantic router schema repair remained invalid raw=%r", str(repaired_raw or "")[:1000])
            raise
    route = _reconcile_explicit_slots(message, route)
    if route.artifact_operation == ArtifactOperation.EXPLAIN:
        route = route.model_copy(update={
            "action": SemanticAction.QUESTION,
            "artifact_operation": None,
            "target_artifact_id": None,
        })
    explicit_mutation = _has_explicit_artifact_mutation(message)
    if (
        route.action == SemanticAction.ARTIFACT_ACTION
        and route.artifact_operation == ArtifactOperation.REVISE
        and not explicit_mutation
    ) or (
        route.action == SemanticAction.REVISE
        and current_task is None
        and active_artifact is not None
        and not explicit_mutation
    ):
        route = route.model_copy(update={
            "action": SemanticAction.QUESTION,
            "artifact_operation": None,
            "target_artifact_id": None,
        })
    route = _apply_execution_gate(message, route, current_task)
    route = _reconcile_explicit_learning_graph_update(
        message,
        route,
        active_artifact=active_artifact,
        artifact_candidates=artifact_candidates,
    )
    route = _reconcile_trusted_lecture_source(route, active_artifact)
    if route.action == SemanticAction.ARTIFACT_ACTION:
        candidates = []
        if isinstance(active_artifact, dict):
            candidates.append(active_artifact)
        candidates.extend(item for item in list(artifact_candidates or []) if isinstance(item, dict))
        expected_kind = route.agent_type.value if route.agent_type else None
        compatible_kinds = {expected_kind}
        if expected_kind == AgentType.PRESENTATION.value:
            compatible_kinds.add("interactive_lesson")
        target = next((
            item for item in candidates
            if item.get("kind") in compatible_kinds and item.get("artifact_id")
        ), None)
        if target is not None:
            route = route.model_copy(update={"target_artifact_id": int(target["artifact_id"])})
        else:
            route = route.model_copy(update={"target_artifact_id": None})
    elif route.target_artifact_id is not None:
        route = route.model_copy(update={"target_artifact_id": None})
    return await _recover_missing_presentation_topic(message=message, route=route, llm=llm)
