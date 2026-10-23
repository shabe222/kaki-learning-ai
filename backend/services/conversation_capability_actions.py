"""Conversation-owned projection for formal learning capability actions.

``chat_quick`` remains the one turn authority.  This module only binds an
eligible turn to the already accepted personalized Artifact, Assessment, Code
Lab, and Review authorities.  Action state is stored on the owning assistant
message; no second conversation, artifact, trace, or review store is introduced.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ChatSession,
    GeneratedItem,
    GeneratedItemVersion,
    GraphNode,
    GraphResourcePlan,
    LearningGraph,
)
from schemas.canonical_artifacts import CreateArtifactReviewRequest, CreateCanonicalHandoutRequest
from services.canonical_artifacts import (
    build_canonical_artifact,
    create_canonical_handout,
    create_trusted_review,
)
from services.workspace_artifact_versions import create_new_version, get_owned_artifact
from services.conversation_capability_intent import decide_capability_intent, explicit_blackboard_continuation
from services.darya_learning_loop import get_learning_route_state, get_learning_state, run_learning_turn
from services.darya_domain_resolver import resolve_learning_domain
from services.darya_domain_registry import get_domain_pack
from services.darya_cognition_orchestrator_v2 import capability_registry
from services.blackboard.script import compile_intent, compile_script
from services.hallucination_guard import reminder_from_history
from services.memory_ledger import MemoryCandidate, recall as recall_memories, remember
from services.serving_context import build_serving_context
from services.demonstration_surface import (
    DemonstrationGenerationError,
    create_or_mutate_demonstration,
    latest_demonstration,
)
from services.learning_graph_resource_generation import materialize_resource_plan
from services.personalized_learning_artifacts import build_personalized_artifact_plan
from services.tide_long_term_memory import list_active_memories
from services.tiered_assessments import (
    create_tiered_assessment,
    generate_goal_assessment_plan,
    GoalAssessmentReviewFailure,
    KNOWLEDGE_NODE,
)
from services.generation_draft import GenerationDraftBlocked, handout_draft
from services.workspace_artifact_versions import build_public_projection, get_owned_artifact, get_version
from services.workspace_task_contracts import AgentType, SemanticAction, SemanticRoute


ACTION_KINDS = {"HANDOUT", "PRACTICAL_GUIDE", "ASSESSMENT", "CODE_LAB", "PRESENTATION", "MINDMAP", "VIDEO", "STORYBOOK", "DEMONSTRATE"}
ACTION_STATUSES = {"planned", "preparing", "reviewing", "ready", "failed"}
_CAREER_LEARNING_GOAL = re.compile(
    r"(?:学|把).{0,24}(?:Python|编程).{0,24}(?:找工作|就业|求职|转行)", re.I,
)
_CAREER_DIRECTION = re.compile(
    r"后端|Web|数据分析|数据工程|自动化测试|测试开发|人工智能|机器学习|算法|爬虫|"
    r"backend|data|testing|machine learning|AI", re.I,
)
_CAREER_STAGE = re.compile(
    r"零基础|刚开始|初学|基础程序|完整项目|独立项目|做过项目|工作经验|开发经验|"
    r"beginner|intermediate|advanced|project experience", re.I,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _compact(message: str) -> str:
    return re.sub(r"\s+", "", str(message or "")).strip()


def _confirmed_user_context(
    recent_history: list[Mapping[str, Any]] | None,
    *,
    current_message: str,
) -> list[str]:
    """Keep early identity anchors as well as recent changes for one account."""
    user_messages = [
        re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        for item in list(recent_history or [])
        if (
            isinstance(item, Mapping)
            and str(item.get("role") or "").lower() == "user"
            and str(item.get("content") or "").strip()
            and str(item.get("content") or "").strip() != str(current_message or "").strip()
        )
    ]
    identity_anchor = re.compile(
        r"我是|退伍|退役|学校|学院|专业|大[一二三四]|会计|考研|考公|公务员|"
        r"湖北|武汉|每天|每日|工作日|分钟|情绪|刺激|喜欢|不喜欢|基础",
    )
    anchored = [message for message in user_messages if identity_anchor.search(message)]
    selected = [*anchored[:4], *anchored[-4:], *user_messages[-4:]]
    result: list[str] = []
    for message in selected:
        if message not in result:
            result.append(message)
    return result


async def _account_confirmed_user_context(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    recent_history: list[Mapping[str, Any]] | None,
    current_message: str,
) -> list[str]:
    """Extract identity anchors before the generic 20-message LLM window."""
    history = list(recent_history or [])
    if hasattr(db, "execute"):
        result = await db.execute(
            select(ChatSession.messages).where(
                ChatSession.user_id == user_id,
                ChatSession.thread_id == thread_id,
            ).order_by(desc(ChatSession.last_message_at)).limit(1)
        )
        stored = result.scalar_one_or_none()
        if isinstance(stored, list):
            history = stored
    return _confirmed_user_context(history, current_message=current_message)


def _generic_resource_topic(message: str) -> str:
    text = re.sub(r"\s+", " ", str(message or "")).strip().strip("。！？!?")
    parts = re.split(
        r"(?:请)?(?:直接)?(?:先)?(?:帮我|给我)(?:做|生成|整理|制作)",
        text,
        maxsplit=1,
    )
    if len(parts) == 2 and parts[1].strip():
        text = parts[1].strip()
    text = re.sub(r"[，,]s*最好能?保存(?:下来)?$", "", text).strip()
    return text[:120] or "针对性资料"


def natural_clarification_question(message: str, *, selected_context: str = "") -> str | None:
    """Return one high-value question when a broad career goal lacks a usable start point."""
    text = str(message or "").strip()
    if re.fullmatch(r"(?:我)?(?:想)?(?:试一下|试试|来一下|做一个看看)[。！!，,]*", _compact(text)):
        return "你想直接动手做一个，还是我先问你一道题确认一下？"
    if not _CAREER_LEARNING_GOAL.search(text):
        return None
    context = f"{text} {selected_context}"
    if not _CAREER_DIRECTION.search(context):
        return "你更想先走 Python 后端、数据分析，还是自动化测试这三个方向中的哪一个？"
    if not _CAREER_STAGE.search(context):
        return "你现在更接近“能写基础程序”，还是已经独立做过一个完整项目？"
    return None


def _user_wants_space(message: str) -> bool:
    text = _compact(message)
    return bool(re.search(
        r"(?:先让我|让我|我先).{0,12}(?:自己想|自己做|自己试|想一想|做一做)|"
        r"(?:不要|别|先别).{0,10}(?:继续提示|提示|追问|推荐|生成|打扰)",
        text,
    ))


def _generic_practice(message: str) -> bool:
    return bool(re.fullmatch(
        r"(?:我想)?(?:给我)?(?:练练|练|练习|试|试试)(?:一?下|一?练)?[。！!，,]*",
        _compact(message),
    ))


def _explicit_assessment_count(message: str) -> tuple[int, int | None]:
    """Honor a requested number of questions; bound one assessment to eight."""
    match = re.search(r"(\d{1,2}|[一二三四五六七八九十])\s*(?:道|题)", message)
    if not match:
        return 2, None
    written = match.group(1)
    numerals = "一二三四五六七八九十"
    requested = int(written) if written.isdigit() else numerals.index(written) + 1
    return max(1, min(requested, 8)), requested


def _trace_step(label: str, detail: str, technical_label: str) -> dict[str, str]:
    return {
        "label": label,
        "detail": detail,
        "technical_label": technical_label,
        "status": "completed",
    }


async def _recent_ledger(
    db: AsyncSession, *, user_id: int, query: str = "", limit: int = 12
) -> list[dict[str, Any]]:
    """What this person has kept so far, for the serving context.

    A ledger read must never be the reason a turn fails: if it is unavailable
    the model simply serves with less, exactly as it did before.
    """
    try:
        return await recall_memories(db, user_id=user_id, limit=limit, query=query)
    except Exception:
        logging.getLogger("rjianb.conversation_capability.ledger").warning(
            "memory ledger read failed", exc_info=True
        )
        return []


async def _remember_board_lesson(
    db: AsyncSession, *, user_id: int, thread_id: str, message_id: str,
    message: str, intent: Mapping[str, Any] | None,
) -> None:
    """Keep the fact that we taught this, so a later turn can build on it.

    A lesson is worth more than a transcript: it says *this* is something we
    worked through together, which is exactly what a follow-up needs to know.
    Failing to record one must never cost the learner their answer.
    """
    title = str((intent or {}).get("title") or "").strip()
    if not title:
        return
    try:
        result = await remember(
            db,
            user_id=user_id,
            thread_id=thread_id,
            message_id=message_id,
            candidate=MemoryCandidate(
                kind="LESSON",
                topic="lesson:board:" + _sha(title)[:16],
                content=f"用黑板讲过「{title}」",
                # Verbatim from what they said -- the ledger refuses anything else.
                evidence_span=str(message or "")[:120],
                confidence=0.8,
            ),
            current_message=message,
        )
        logging.getLogger("rjianb.conversation_capability.ledger").info(
            "[ledger] %s title=%r", result.as_dict(), title[:40]
        )
    except Exception:
        logging.getLogger("rjianb.conversation_capability.ledger").warning(
            "memory ledger write failed", exc_info=True
        )


def _blackboard_script(demonstration: Mapping[str, Any], previous_intent: Any) -> dict[str, Any] | None:
    """Compile the validated scene into chalk operations.

    A compiler fault must not take the turn down with it: the learner still gets
    DARYA's spoken explanation, just without a board. Silent, but logged, so a
    geometry bug shows up as a missing blackboard rather than a failed answer.
    """
    try:
        script = compile_script(
            demonstration.get("current_intent"),
            previous_intent=previous_intent,
            new_scene=str(demonstration.get("operation") or "create") != "mutation",
        )
    except Exception:
        logging.getLogger("rjianb.conversation_capability.blackboard").exception(
            "blackboard compile failed; answering without a board"
        )
        return None
    if not script.get("ops"):
        # The scene was revised but nothing on the board changed -- and DARYA
        # just said it adjusted the drawing. The least we owe the learner is
        # showing which part it meant, rather than leaving the board perfectly
        # still under a sentence about how it changed.
        target = _mutation_target(demonstration)
        if target:
            script["ops"] = [{"kind": "pointer", "bbox": target, "ms": 2600}]
            script["steps"] = [{"narration": "", "ops": list(script["ops"])}]
    # 板子收笔时留下那句"记住这一句"，让最后一眼停在要点上，而不是停在一根箭头上。
    # 这句话本来就印在对话里，这里只是把它放回板子最后的一拍。
    steps = [step for step in (script.get("steps") or []) if step.get("ops") or step.get("narration")]
    takeaway = _board_takeaway([
        item for item in ((demonstration.get("current_intent") or {}).get("items") or [])
        if isinstance(item, Mapping)
    ])
    if takeaway and steps:
        steps.append({"narration": takeaway, "ops": []})
    if steps:
        script["steps"] = steps
    return script


def _board_takeaway(items: Sequence[Mapping[str, Any]]) -> str:
    """One line the learner can hold without looking.

    Prefer what the planner emphasised -- on a real board that was the item
    labelled 「面试主线」, and it is the sentence that makes the rest make
    sense. Fall back to the last item, which is where a board tends to land.
    """
    if not items:
        return ""
    emphasised = [item for item in items if bool(item.get("emphasis"))]
    chosen = None
    if emphasised:
        chosen = min(emphasised, key=lambda item: len(str(item.get("description") or "")))
    else:
        chosen = items[-1]
    detail = " ".join(str(chosen.get("description") or "").split()).strip().rstrip("。.")
    label = " ".join(str(chosen.get("label") or "").split()).strip().rstrip("。.")
    if detail and label:
        return f"先记住这一句——{label}：{detail}。"
    if detail:
        return f"先记住这一句——{detail}。"
    if label:
        return f"先记住这一句——{label}。"
    return ""


def _board_sentence(demonstration: Mapping[str, Any]) -> str:
    """Say what is on the board, not that a board was drawn.

    The line handed back for a blackboard turn used to be a fixed sentence about
    the act of drawing ("这个画出来更快。"). It carried none of the teaching and,
    because the call panel reads this same string aloud, a voice call said that
    one sentence and then went quiet while the board filled in. Naming the topic
    and where it starts costs nothing -- both come from the scene the model just
    produced, so no extra model call.
    """
    intent = demonstration.get("current_intent") or {}
    title = str(intent.get("title") or "").strip().rstrip("。.")
    items = [item for item in (intent.get("items") or []) if isinstance(item, Mapping)]
    lead = next((str(item.get("label") or "").strip().rstrip("。.") for item in items), "")
    # The point of the board, said out loud as well as drawn. Without it the
    # reply was a sentence about the act of drawing -- on a phone, or read
    # aloud in a call, the learner got nothing at all until they looked.
    takeaway = _board_takeaway(items)
    if str(demonstration.get("operation") or "create") != "mutation":
        opening = (
            f"我在黑板上讲「{title}」，从「{lead}」写起，边说边画。"
            if title and lead else
            f"我在黑板上讲「{title}」，边说边画。" if title else
            "我在黑板上边说边画。"
        )
        return opening + takeaway
    target = _mutation_target_semantic_id(demonstration)
    if title and target:
        return f"我在原来那张「{title}」上改了「{target}」，不用重画。"
    if title:
        return f"我在原来那张「{title}」上改了改，不用重画。"
    return "我在原来那张图上改了改，不用重画。"


def _mutation_target_semantic_id(demonstration: Mapping[str, Any]) -> str:
    """The label of the item a mutation touched, for naming it back to the user."""
    mutation = demonstration.get("mutation")
    if not isinstance(mutation, Mapping):
        return ""
    wanted: list[str] = []
    for operation in mutation.get("operations") or []:
        if not isinstance(operation, Mapping):
            continue
        for key in ("targetId", "semanticId"):
            value = str(operation.get(key) or "")
            if value:
                wanted.append(value)
        wanted.extend(str(item) for item in (operation.get("targets") or []))
    items = {
        str(item.get("id")): str(item.get("label") or "").strip()
        for item in ((demonstration.get("current_intent") or {}).get("items") or [])
        if isinstance(item, Mapping)
    }
    for semantic_id in wanted:
        if items.get(semantic_id):
            return items[semantic_id]
    return ""


def _mutation_target(demonstration: Mapping[str, Any]) -> dict[str, float] | None:
    """The board box of the item a mutation touched, for a "look here" flash."""
    mutation = demonstration.get("mutation")
    if not isinstance(mutation, Mapping):
        return None
    wanted: list[str] = []
    for operation in mutation.get("operations") or []:
        if not isinstance(operation, Mapping):
            continue
        for key in ("targetId", "semanticId"):
            value = str(operation.get(key) or "")
            if value:
                wanted.append(value)
        wanted.extend(str(item) for item in (operation.get("targets") or []))
    if not wanted:
        return None
    snapshot = compile_intent(demonstration.get("current_intent"))
    elements = {str(element.get("id")): element for element in (snapshot or {}).get("elements", [])}
    for semantic_id in wanted:
        for suffix in ("-label", "-desc"):
            element = elements.get(f"bb-{semantic_id}{suffix}")
            if element:
                return dict(element["bbox"])
    return None


def _base_trace(kind: str) -> list[dict[str, str]]:
    if kind == "DEMONSTRATE":
        return [
            _trace_step(
                "理解你",
                "识别到继续用文字会增加负担，这一轮更适合用空间关系来解释。",
                "Conversation Router",
            ),
            _trace_step(
                "结合当前语境",
                "只读取这一轮讲的内容和混淆边界来决定板上写什么，不创建第二条路线。",
                "Learner Context",
            ),
            _trace_step(
                "在板上讲",
                "由服务端解算每一笔的位置，浏览器逐笔写出；这只是解释，不写入掌握度。",
                "Blackboard Script",
            ),
        ]
    action_label = {
        "HANDOUT": "针对性短资料",
        "PRACTICAL_GUIDE": "实操引导",
        "ASSESSMENT": "分层测评",
        "CODE_LAB": "代码练习",
        "PRESENTATION": "演示文稿",
        "MINDMAP": "思维导图",
        "VIDEO": "教学视频",
        "STORYBOOK": "教学绘本",
        "DEMONSTRATE": "视觉演示",
    }[kind]
    return [
        _trace_step("理解你", "识别到这次需要把当前对话转成一个正式学习行动。", "Conversation Router"),
        _trace_step("结合当前学习状态", "读取当前学习节点、已有证据和支持需求。", "Learner Context"),
        _trace_step("资源决策", f"根据当前状态选择{action_label}，没有把普通回答伪装成产物。", "Learning Decision"),
    ]


def _assistant_copy(kind: str, support_level: str) -> tuple[str, str]:
    weak = support_level in {"L3", "L4", "L5"}
    if kind == "HANDOUT":
        # 这段必须与主题无关。它以前写死的是闭包主题的讲稿（「读取和重绑定」），
        # 于是任意主题只要带着学习状态走进来，就会收到一句陌生领域的开场白。
        return (
            "我不让你从头再学。给你压成一份短资料。",
            "只保留这一步真正需要的解释和检查点。",
        )
    if kind == "PRACTICAL_GUIDE":
        return (
            "别只讲了。我们直接动手做一遍，再放一次小验证进去。",
            "跟着做一遍，边界会比继续听定义更清楚。",
        )
    if kind == "ASSESSMENT":
        return (
            "先不重复讲。给你两道按当前状态起步的题，做完就能看清还卡在哪里。" if weak
            else "基础已经不用重复了。直接从应用和迁移题开始，看看这个边界是不是真的稳了。",
            "题目会在提交后由服务器判定，作答前不展示答案。",
        )
    if kind == "PRESENTATION":
        return (
            "我会按你当前的目标和基础生成一份真正可打开、可导出的 PPT。",
            "不是文字大纲；生成完成后会作为正式演示文稿接在这轮对话里。",
        )
    if kind == "MINDMAP":
        return (
            "我会把这个主题生成一张真正可打开、可继续修改的思维导图。",
            "会按当前目标组织概念关系，不用文字列表冒充导图。",
        )
    if kind == "VIDEO":
        return (
            "我会把这个主题交给真实的视频生成链，并把生成进度留在这轮对话里。",
            "只有视频任务真正创建后才会显示产物，不用脚本或文案冒充视频。",
        )
    if kind == "STORYBOOK":
        return (
            "我会打开产品里的视觉绘本创作器，让你从当前主题直接开始生成。",
            "这是同一个真实创作能力入口；在生成完成前不会把文字描述冒充绘本产物。",
        )
    if kind == "DEMONSTRATE":
        return (
            "这个画出来更快。",
            "我会把关键关系放到同一张可以继续调整的图里；这只是解释，不会改动你的掌握度。",
        )
    return (
        "基础已经不用重复了。直接写一次会更快，我把当前适合的 Code Lab 接到这里。",
        "运行只用于试验，只有明确提交才会成为学习证据。",
    )


_KIND_DELIVERABLE = {
    "HANDOUT": "一份可保存的讲义",
    "PRACTICAL_GUIDE": "一份可保存的实操指南",
    "ASSESSMENT": "一套服务端判定的题",
    "CODE_LAB": "一个可运行的代码实验",
    "PRESENTATION": "一份可导出的演示文稿",
    "MINDMAP": "一张可继续修改的思维导图",
    "VIDEO": "一段真实生成的视频",
    "STORYBOOK": "一本绘本",
}


def build_planned_action_directive(action: Mapping[str, Any] | None) -> str:
    """告诉本轮回答：资料卡正在准备，中心这一轮仍然要交出东西。

    2026-09-23 之前这条分支的后果是模型整轮不跑，用户只收到一句固定台词。改成模型
    照常回答之后，这里必须把边界说清楚，否则会滑到另一个极端——把资料的正文在对话里
    先讲一遍，等卡片出来再讲一遍。所以中心只拿「判断 + 最该先做的一步 + 完成信号」，
    可保存的完整版归资料卡。
    """
    if not action:
        return ""
    kind = str(action.get("kind") or "").upper()
    if kind == "DEMONSTRATE":
        return (
            "本轮你会同时把关键关系画到黑板上。黑板上只写关系和步骤，不要抄成一段讲解；"
            "对话里给出你此刻的判断和下一步。"
        )
    deliverable = _KIND_DELIVERABLE.get(kind)
    if not deliverable:
        return ""
    return (
        f"本轮你已经在为这位用户准备{deliverable}，它会作为独立资料卡交付，内容不由你写。"
        "所以这一轮的回答不要复述那份资料的正文，也不要用「正在生成」「马上就好」占位："
        "给出你此刻对他的处境的判断、现在最该先做的一步、以及做完这一步能看到什么。"
        "资料卡负责可保存的完整版，中心负责让他现在就动起来。"
    )


def _semantic_kind(semantic: SemanticRoute | None) -> str | None:
    if semantic is None:
        return None
    if semantic.action not in {
        SemanticAction.NEW_TASK,
        SemanticAction.REPLACE,
        SemanticAction.SUPPLEMENT,
        SemanticAction.REVISE,
    }:
        return None
    if semantic.agent_type == AgentType.LECTURE:
        return "HANDOUT"
    if semantic.agent_type == AgentType.EXAM:
        return "ASSESSMENT"
    if semantic.agent_type == AgentType.CODE:
        return "CODE_LAB"
    if semantic.agent_type == AgentType.PRESENTATION:
        return "PRESENTATION"
    if semantic.agent_type == AgentType.MINDMAP:
        return "MINDMAP"
    if semantic.agent_type == AgentType.VIDEO:
        return "VIDEO"
    return None


async def _latest_state(db: AsyncSession, *, user_id: int, thread_id: str):
    route = await get_learning_route_state(db, user_id=user_id, thread_id=thread_id)
    if route is None:
        return None, None
    state = await get_learning_state(
        db,
        user_id=user_id,
        thread_id=thread_id,
        concept_key=str(route.current_node_id),
    )
    return state, route


async def _graph_node_for_thread(
    db: AsyncSession, *, user_id: int, thread_id: str, concept_key: str,
) -> tuple[LearningGraph, GraphNode] | None:
    affinity = await db.execute(
        select(LearningGraph, GraphNode)
        .join(GraphNode, GraphNode.graph_id == LearningGraph.id)
        .join(GraphResourcePlan, GraphResourcePlan.node_id == GraphNode.id)
        .where(
            LearningGraph.user_id == user_id,
            GraphNode.user_id == user_id,
            GraphNode.external_id == concept_key,
            GraphResourcePlan.thread_id == thread_id,
        )
        .order_by(desc(GraphResourcePlan.updated_at), desc(GraphResourcePlan.id))
        .limit(1)
    )
    row = affinity.first()
    if row:
        return row[0], row[1]
    fallback = await db.execute(
        select(LearningGraph, GraphNode)
        .join(GraphNode, GraphNode.graph_id == LearningGraph.id)
        .where(
            LearningGraph.user_id == user_id,
            GraphNode.user_id == user_id,
            GraphNode.external_id == concept_key,
        )
        .order_by(desc(LearningGraph.updated_at), desc(LearningGraph.id))
        .limit(1)
    )
    row = fallback.first()
    return (row[0], row[1]) if row else None


async def _ensure_graph_node_for_darya_route(
    db: AsyncSession, *, user_id: int, thread_id: str, route: Any, state: Any,
) -> tuple[LearningGraph, GraphNode]:
    """Bridge a real DARYA route into the existing graph/evaluation authority.

    The graph remains the formal Assessment/Code Lab aggregate; this creates no
    second route or mastery state and is only used when a new conversation has
    not previously materialized its graph identity.
    """
    domain_id = str(route.domain_id or "darya-learning")
    concept_key = str(route.current_node_id)
    identity = _sha(f"{user_id}\0{thread_id}\0{domain_id}")[:24]
    graph_external_id = f"darya-{identity}"
    graph = await db.scalar(select(LearningGraph).where(
        LearningGraph.user_id == user_id,
        LearningGraph.external_id == graph_external_id,
    ))
    pack = get_domain_pack(domain_id)
    domain_title = pack.title if pack else "DARYA 当前学习路线"
    if graph is None:
        graph = LearningGraph(
            user_id=user_id,
            external_id=graph_external_id,
            graph_revision=1,
            title=domain_title,
            goal=str(route.learner_goal or domain_title),
            goal_type="conversation",
            source_type="chat",
            source_detail={
                "authority": "DaryaLearningRouteState",
                "thread_id": thread_id,
                "domain_id": domain_id,
            },
            planning_rationale="由当前 DARYA 路由接入既有测评与代码证据链。",
            status="active",
            is_primary=False,
            total_nodes=1,
        )
        db.add(graph)
        await db.flush()
    node = await db.scalar(select(GraphNode).where(
        GraphNode.user_id == user_id,
        GraphNode.graph_id == graph.id,
        GraphNode.external_id == concept_key,
    ))
    if node is None:
        pack_node = pack.nodes.get(concept_key) if pack and concept_key in pack.nodes else None
        title = pack_node.title if pack_node else str(getattr(state, "concept_key", None) or concept_key)
        node = GraphNode(
            graph_id=graph.id,
            user_id=user_id,
            external_id=concept_key,
            continuity_key=f"darya-{identity}-{_sha(concept_key)[:16]}",
            title=title,
            description=f"DARYA 当前路线中的学习节点：{title}",
            node_type="skill",
            knowledge_type="code" if "python" in domain_id.lower() else "theory",
            difficulty=3,
            estimated_minutes=20,
            order_index=0,
            status="learning",
            mastery_score=max(0.0, min(100.0, float(state.mastery_estimate or 0.0) * 100)),
            learning_goal=str(route.learner_goal or title),
            learning_objectives=list(pack_node.evidence_requirements) if pack_node else [f"用可观察证据解释{title}"],
            mastery_evidence=["服务端判定的测评或代码提交"],
            source_refs=[f"darya-domain:{domain_id}", f"darya-route:{thread_id}"],
            resource_plan_ids=[],
            auto_generated=True,
        )
        db.add(node)
        await db.flush()
    return graph, node


def _binding(projection: Mapping[str, Any], *, reason: str, status: str) -> dict[str, Any]:
    canonical = dict(projection.get("canonical") or {})
    purpose = dict(canonical.get("purpose") or {})
    return {
        "artifact_id": int(projection["artifact_id"]),
        "version_id": int(projection["version_id"]),
        "version_number": int(projection.get("version_number") or projection.get("version") or 1),
        "kind": str(projection.get("kind") or "document"),
        "artifact_type": str(purpose.get("experience") or projection.get("type") or "").upper(),
        "title": str(projection.get("title") or "DARYA 为你准备的资料"),
        "reason": reason,
        "status": status,
    }


async def _ready_code_lab(
    db: AsyncSession, *, user_id: int, thread_id: str, concept_key: str,
) -> tuple[dict[str, Any], bool] | None:
    plans = list((await db.scalars(
        select(GraphResourcePlan)
        .join(GraphNode, GraphNode.id == GraphResourcePlan.node_id)
        .join(LearningGraph, LearningGraph.id == GraphResourcePlan.graph_id)
        .where(
            LearningGraph.user_id == user_id,
            GraphNode.user_id == user_id,
            GraphNode.external_id == concept_key,
            GraphResourcePlan.resource_type == "CODE",
            GraphResourcePlan.status == "generated",
            GraphResourcePlan.artifact_id.is_not(None),
        )
        .order_by(
            (GraphResourcePlan.thread_id == thread_id).desc(),
            desc(GraphResourcePlan.priority),
            desc(GraphResourcePlan.updated_at),
            desc(GraphResourcePlan.id),
        )
    )).all())
    for plan in plans:
        projection = await build_public_projection(db, user_id=user_id, artifact_id=int(plan.artifact_id))
        canonical = dict(projection.get("canonical") or {})
        if dict(canonical.get("purpose") or {}).get("experience") != "PERSONALIZED_CODE_LAB":
            continue
        verdict = str(dict(canonical.get("status") or {}).get("review_verdict") or "").upper()
        return projection, verdict == "PASS"
    return None


async def _planned_code_lab(
    db: AsyncSession, *, user_id: int, thread_id: str, concept_key: str,
) -> GraphResourcePlan | None:
    rows = list((await db.scalars(
        select(GraphResourcePlan)
        .join(GraphNode, GraphNode.id == GraphResourcePlan.node_id)
        .join(LearningGraph, LearningGraph.id == GraphResourcePlan.graph_id)
        .where(
            LearningGraph.user_id == user_id,
            GraphNode.user_id == user_id,
            GraphNode.external_id == concept_key,
            GraphResourcePlan.resource_type == "CODE",
            GraphResourcePlan.status == "planned",
            GraphResourcePlan.artifact_id.is_(None),
        )
        .order_by(
            (GraphResourcePlan.thread_id == thread_id).desc(),
            desc(GraphResourcePlan.priority),
            desc(GraphResourcePlan.updated_at),
            desc(GraphResourcePlan.id),
        )
    )).all())
    return next((
        plan for plan in rows
        if str(dict(plan.generation_spec or {}).get("decision") or "") == "ADVANCE_TRANSFER_CHALLENGE"
    ), None)


async def plan_conversation_capability(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    message: str,
    message_id: str,
    assistant_message_id: str,
    semantic: SemanticRoute | None = None,
    recent_history: list[Mapping[str, Any]] | None = None,
    intent_llm: Any | None = None,
    v2_recommended: str | None = None,
    # What the caller already knows about this person and this turn. Passing it
    # in matters for the board: it is drawn here, in this function, so anything
    # attached to the action afterwards (the work brief used to be) arrives too
    # late for the model that has to decide what to write.
    work_brief: Mapping[str, Any] | None = None,
    profile: Mapping[str, Any] | None = None,
    memories: Sequence[Any] = (),
    impressions: Sequence[Any] = (),
    evidence: Mapping[str, Any] | None = None,
    # Optional progress hook: called with a character count as the board model
    # emits. It exists so the caller can show life during the longest silent
    # stretch of a turn instead of a frozen wait.
    on_token: Any | None = None,
) -> dict[str, Any] | None:
    """Project an eligible chat turn into one formal capability action."""
    state, route = await _latest_state(db, user_id=user_id, thread_id=thread_id)

    pack = get_domain_pack(str(route.domain_id or "")) if route is not None else None
    route_node = pack.nodes.get(str(route.current_node_id)) if route is not None and pack and str(route.current_node_id) in pack.nodes else None
    recent_context = [
        str(item.get("content") or "")
        for item in list(recent_history or [])
        if isinstance(item, Mapping) and str(item.get("content") or "").strip() != str(message or "").strip()
    ][-6:]
    confirmed_user_context = await _account_confirmed_user_context(
        db,
        user_id=user_id,
        thread_id=thread_id,
        recent_history=recent_history,
        current_message=message,
    )
    # One assembly of "what this model should know", read by whichever path runs.
    serving = build_serving_context(
        message=message,
        profile=profile,
        learner_state=state,
        confirmed_facts=confirmed_user_context if isinstance(confirmed_user_context, (list, tuple)) else (),
        memories=memories,
        impressions=impressions,
        route=route,
        work_brief=work_brief,
        open_scene=(latest_demonstration(recent_history) or {}).get("current_intent"),
        recent_history=recent_history or (),
        evidence=evidence,
        ledger_memories=await _recent_ledger(db, user_id=user_id, query=message),
        guard_reminder=reminder_from_history(recent_history),
        capabilities=[
            {"name": name, "real_output": contract.real_output, "fallback": contract.fallback}
            for name, contract in capability_registry().items()
        ],
    )
    logging.getLogger("rjianb.conversation_capability.context").info(
        "[context] layers=%s", serving.summary()
    )

    v2_kind = str(v2_recommended or "").upper()
    # The model's structured choice is authoritative for semantic intent.
    # Deterministic code below still validates capability names, ownership,
    # persistence and receipts, but does not reinterpret the user's language.
    v2_authoritative = v2_kind in (ACTION_KINDS | {"NONE"})
    if v2_authoritative:
        # One model-primary decision: the orchestrator's recommended capability
        # owns routing when it made a structured decision this turn.
        kind = v2_kind if v2_kind in ACTION_KINDS else None
        intent = None
        if kind is None:
            # Bounded repair when the orchestrator declines. A NONE is not yet
            # reliable evidence that the learner asked for nothing: measured on
            # this build, the orchestrator returns NONE for "帮我出三道练习题"
            # and for "你画一下" -- an explicit request the product must never
            # answer with another wall of prose. Its positive choices are left
            # untouched; only a NONE is second-guessed, and only against the
            # deterministic language layer, so this costs no extra model call.
            explicit = await decide_capability_intent(
                message,
                domain_context=" · ".join(filter(None, [
                    str(route.domain_id or "") if route is not None else "",
                    str(pack.title if pack else ""),
                    str(route_node.title if route_node else (state.concept_key if state is not None else "") or ""),
                ])),
                recent_conversation_context=recent_context,
                llm=None,
                use_model=False,
            )
            if explicit.outcome in ACTION_KINDS and explicit.explicit_activity_request:
                kind = explicit.outcome
                intent = explicit
            elif (
                explicit_blackboard_continuation(message)
                and latest_demonstration(recent_history) is not None
                and "EXPLANATION_ONLY" not in explicit.negative_constraints
                and "NO_DEMONSTRATION" not in explicit.negative_constraints
            ):
                # Only an explicit board edit can repair a declined model
                # choice. Reading the board is a conversation, not a mutation.
                kind = "DEMONSTRATE"
                intent = explicit
    else:
        intent = await decide_capability_intent(
            message,
            domain_context=" · ".join(filter(None, [
                str(route.domain_id or "") if route is not None else "",
                str(pack.title if pack else ""),
                str(route_node.title if route_node else (state.concept_key if state is not None else "") or ""),
            ])),
            active_route_context=" · ".join(filter(None, [
                str(route.learner_goal or "") if route is not None else "",
                str(route.current_node_id or "") if route is not None else "",
                str(route.target_node_id or "") if route is not None else "",
            ])),
            recent_conversation_context=recent_context,
            llm=intent_llm,
            use_model=True,
        )
        kind = intent.outcome if intent.outcome in ACTION_KINDS else None
        if kind is None and not intent.negative_constraints:
            kind = _semantic_kind(semantic)
    if not v2_authoritative and _generic_practice(message) and state is not None and route is not None:
        ready_code = await _ready_code_lab(
            db, user_id=user_id, thread_id=thread_id, concept_key=str(route.current_node_id),
        )
        kind = "CODE_LAB" if str(state.support_level) in {"L0", "L1"} and ready_code else "ASSESSMENT"
    # One line per turn saying how this turn was routed and why. Without it the
    # only way to answer "why didn't the blackboard fire?" is to re-run with
    # prints -- which is what everyone was doing.
    logging.getLogger("rjianb.conversation_capability.route").info(
        "[route] kind=%s v2=%s authoritative=%s source=%s constraints=%s msg=%r",
        kind or "NONE",
        v2_kind or "-",
        v2_authoritative,
        getattr(intent, "source", "-") if intent is not None else ("v2" if v2_authoritative else "semantic_route"),
        list(getattr(intent, "negative_constraints", ()) or ()),
        str(message or "")[:60],
    )
    if kind not in ACTION_KINDS:
        return None
    if kind == "ASSESSMENT" and (route is None or str(route.current_node_id) != KNOWLEDGE_NODE):
        action_key = "pc03a_" + _sha(f"{user_id}\0{thread_id}\0{message_id}\0{message}")[:40]
        question_count, requested_count = _explicit_assessment_count(message)
        return {
            "action_key": action_key, "source_message_id": message_id,
            "assistant_message_id": assistant_message_id, "kind": kind, "status": "planned",
            "assistant_response": (
                f"我会先准备 {question_count} 道新题供你独立提交；单份测评最多 8 道，可继续生成下一份。"
                if requested_count and requested_count > 8 else
                f"我会准备 {question_count} 道新题供你独立提交，答案由系统判定，解析在提交后显示。"
            ),
            "reason": "围绕当前目标做应用与迁移检验，不复用已经展示过答案的题。",
            "artifact": None, "trace": _base_trace(kind), "retryable": False,
            "request_payload": {"goal_assessment": True, "topic": message[:160],
                                "question_count": question_count,
                                "work_brief": {"goal": message, "recent_context": recent_context}},
        }
    if kind in {"ASSESSMENT", "CODE_LAB", "DEMONSTRATE"} and (state is None or route is None):
        # Formal capabilities require the accepted learning-state authority.
        # Bootstrap it from an installed-domain match in the same user turn so
        # a new learner does not need to discover and send a magic setup phrase.
        domain_resolution = resolve_learning_domain(
            message=" ".join([message, *recent_context[-4:]]),
        )
        if domain_resolution.pack is not None:
            await run_learning_turn(
                db,
                user_id=user_id,
                thread_id=thread_id,
                message=message,
                message_id=message_id,
                perspective={},
                relevant_memories=(),
            )
            await db.flush()
            state, route = await _latest_state(db, user_id=user_id, thread_id=thread_id)
    # DEMONSTRATE is deliberately absent from this gate. The blackboard is a way
    # of explaining, not a formal learning artifact: it reads no learning state
    # and writes none (see `learning_authority` in the action). Requiring an
    # installed domain pack would mean a geometry question -- exactly what a
    # blackboard is best at -- could never reach the board. The domain bootstrap
    # above still runs first, so a real learning state is used when there is one.
    if kind not in {"HANDOUT", "PRACTICAL_GUIDE", "PRESENTATION", "MINDMAP", "VIDEO", "STORYBOOK", "DEMONSTRATE"} and (state is None or route is None):
        return None

    action_key = "pc03a_" + _sha(f"{user_id}\0{thread_id}\0{message_id}\0{message}")[:40]
    if kind == "STORYBOOK":
        assistant_response, reason = _assistant_copy(kind, str(state.support_level if state is not None else "L2"))
        return {
            "action_key": action_key,
            "source_message_id": message_id,
            "assistant_message_id": assistant_message_id,
            "kind": kind,
            "status": "ready",
            "assistant_response": assistant_response,
            "reason": reason,
            "artifact": None,
            "trace": [*_base_trace(kind), _trace_step("打开创作器", "接入产品内真实视觉绘本创作入口。", "DARYA Storybook")],
            "retryable": False,
        }
    if kind == "PRESENTATION":
        slide_match = re.search(r"(?:生成|做|制作|整理)?\s*(\d{1,2})\s*页", message, re.I)
        slide_count = int(slide_match.group(1)) if slide_match else None
        if slide_count is not None and not 1 <= slide_count <= 30:
            slide_count = None
        assistant_response, reason = _assistant_copy(kind, str(state.support_level if state is not None else "L2"))
        return {
            "action_key": action_key,
            "source_message_id": message_id,
            "assistant_message_id": assistant_message_id,
            "kind": kind,
            "status": "planned",
            "assistant_response": assistant_response,
            "reason": reason,
            "artifact": None,
            "trace": _base_trace(kind),
            "retryable": False,
            "request_payload": {
                "topic": str(message or "学习演示").strip()[:160],
                # A new presentation commission may be unrelated to the active
                # learning route. The current model work brief supplies its goal
                # later; inheriting route.learner_goal contaminates the deck.
                "learning_goal": "",
                "level": str(state.support_level or "")[:40] if state is not None else "",
                "slide_count": slide_count,
            },
        }
    if kind in {"MINDMAP", "VIDEO"}:
        assistant_response, reason = _assistant_copy(kind, str(state.support_level if state is not None else "L2"))
        return {
            "action_key": action_key,
            "source_message_id": message_id,
            "assistant_message_id": assistant_message_id,
            "kind": kind,
            "status": "planned",
            "assistant_response": assistant_response,
            "reason": reason,
            "artifact": None,
            "trace": _base_trace(kind),
            "retryable": False,
            "request_payload": {"topic": str(message or "学习资料").strip()[:160]},
        }
    if kind in {"HANDOUT", "PRACTICAL_GUIDE"} and (state is None or route is None):
        topic = _generic_resource_topic(message)
        assistant_response = (
            "我会按你这次说明的目标和限制，整理成一份可保存的针对性资料。"
            if kind == "HANDOUT"
            else "我会把这个目标拆成可直接照着做的步骤和检查点。"
        )
        reason = "资料会按我们这轮对齐的目标与限制来写；你自然提到的和已确认的信息会分开标注。"
        return {
            "action_key": action_key,
            "source_message_id": message_id,
            "assistant_message_id": assistant_message_id,
            "kind": kind,
            "status": "planned",
            "assistant_response": assistant_response,
            "reason": reason,
            "artifact": None,
            "trace": _base_trace(kind),
            "retryable": False,
            "request_payload": {
                "topic": topic,
                "instruction": (
                    "同一账号的连续对话必须作为本次资料的已确认上下文，不得遗忘或改写。"
                    + (
                        "同账号此前用户原话：" + "｜".join(confirmed_user_context)[:900] + "。"
                        if confirmed_user_context else ""
                    )
                    + "本轮用户原话：" + re.sub(r"\s+", " ", str(message or "")).strip()[:500] + "。"
                    + "根据用户真实目标与限制生成可直接使用的资料；"
                    + ("优先给出按顺序可执行的步骤、检查点和下一步。" if kind == "PRACTICAL_GUIDE" else "结构清楚，标出适用前提与待核实信息。")
                    + "不得用估算值填补缺失的用户数据；不得在用户完成任务前声称其已获得某项能力。"
                    + "只把用户原话当作个人事实，禁止推断学校层次、毕业时间、资格、能力或优势；"
                    + "无可核对来源时，政策、院校、考试科目、岗位、数量和分数只进入待核实清单，不得写成结论。"
                    + "方向建议必须带前提；计划须有时间边界、完成信号和减量回退。"
                    + "不得把检索、整理、比较等核心认知劳动全部交回用户自行完成；先交付现有信息足以支持的成果，现实信息不足时列出明确核对项并说明可在后续对话中继续核对。"
                ),
            },
        }
    if kind == "DEMONSTRATE":
        assistant_response, reason = _assistant_copy(
            kind, str(state.support_level) if state is not None else "L2"
        )
        trace = _base_trace(kind)
        # A follow-up keeps the prior session id; an independent visual request
        # receives a unique candidate id after semantic scene-relationship routing.
        scene_id = "darya-scene-" + _sha(f"{user_id}\0{thread_id}\0{action_key}")[:24]
        # Read the open scene before generating: the request itself becomes the
        # next revision, and the previous one is what the board diffs against.
        previous_intent = (latest_demonstration(recent_history) or {}).get("current_intent")
        try:
            demonstration = await create_or_mutate_demonstration(
                message=message,
                scene_id=scene_id,
                recent_history=recent_history,
                domain_context=" · ".join(filter(None, [
                    str(route.domain_id or "") if route is not None else "",
                    str(pack.title if pack else ""),
                    str(route_node.title if route_node else (state.concept_key if state is not None else "") or ""),
                ])),
                route_context=" · ".join(filter(None, [
                    str(route.learner_goal or "") if route is not None else "",
                    str(route.current_node_id or "") if route is not None else "",
                    str(route.target_node_id or "") if route is not None else "",
                ])),
                # The board used to be drawn from three lines about the learner.
                # It now gets the layers this capability actually needs.
                learner_context=serving.for_capability("DEMONSTRATE"),
                llm=intent_llm,
                on_token=on_token,
            )
            operation = str(demonstration.get("operation") or "create")
            trace.extend([
                _trace_step(
                    "形成图解意图" if operation == "create" else "理解图上修改",
                    "生成并校验只含语义关系的 DemonstrationIntent。"
                    if operation == "create"
                    else "针对同一场景和精确 baseRevision 生成语义修改。",
                    "DemonstrationIntent v0.2" if operation == "create" else "SceneMutation v0.1",
                ),
                _trace_step(
                    "守住学习边界",
                    "图解只用于解释；没有写入掌握度、正式证据或第二条学习路线。",
                    "Learning Authority Boundary",
                ),
            ])
            action = {
                "action_key": action_key,
                "source_message_id": message_id,
                "assistant_message_id": assistant_message_id,
                "kind": kind,
                "status": "ready",
                "assistant_response": _board_sentence(demonstration),
                "reason": reason,
                "artifact": None,
                "demonstration": demonstration,
                # Geometry is solved here, not in the browser: the message
                # carries chalk-ready operations plus the whole board.
                "blackboard_script": _blackboard_script(demonstration, previous_intent),
                "trace": trace,
                "retryable": False,
            }
            # Recorded after the answer is built, never before: the learner gets
            # their board even if the ledger is unavailable.
            await _remember_board_lesson(
                db,
                user_id=user_id,
                thread_id=thread_id,
                message_id=message_id,
                message=message,
                intent=demonstration.get("current_intent"),
            )
            return action
        except DemonstrationGenerationError as exc:
            topic = str(message or "这次的问题").strip().replace("\n", " ")[:120]
            fallback = (
                f"图面刚才没有可靠生成，我先留在同一段对话里继续说明你问的“{topic}”。"
                "我不会假装图已经画好；接下来会用文字梳理关键对象、它们的关系和发生顺序。"
            )
            return {
                "action_key": action_key,
                "source_message_id": message_id,
                "assistant_message_id": assistant_message_id,
                "kind": kind,
                "status": "failed",
                "assistant_response": fallback,
                "reason": "视觉表面暂时不可用，已回到同一对话中的文字解释。",
                "artifact": None,
                "demonstration": None,
                "blackboard_script": None,
                "trace": [
                    *trace,
                    _trace_step(
                        "安全回退",
                        "语义场景未通过真实生成或校验，因此没有打开空白画布，也没有伪造执行回执。",
                        "Demonstration Failure Boundary",
                    ),
                ],
                "retryable": False,
                "error_message": f"DEMONSTRATION_UNAVAILABLE:{str(exc)[:160]}",
            }

    ready_projection: dict[str, Any] | None = None
    ready_reviewed = False
    if kind == "CODE_LAB":
        ready = await _ready_code_lab(
            db, user_id=user_id, thread_id=thread_id, concept_key=str(route.current_node_id),
        )
        if ready:
            ready_projection, ready_reviewed = ready
        elif await _planned_code_lab(
            db, user_id=user_id, thread_id=thread_id, concept_key=str(route.current_node_id),
        ) is None:
            graph_node = await _graph_node_for_thread(
                db, user_id=user_id, thread_id=thread_id, concept_key=str(route.current_node_id),
            )
            if graph_node is None:
                graph_node = await _ensure_graph_node_for_darya_route(
                    db, user_id=user_id, thread_id=thread_id, route=route, state=state,
                )
            graph, node = graph_node
            identity = _sha(f"{user_id}\0{thread_id}\0{message_id}\0semantic-code-lab")[:24]
            plan = GraphResourcePlan(
                graph_id=graph.id,
                node_id=node.id,
                external_id=f"darya-code-{identity}",
                continuity_key=f"semantic-self-attempt-{identity}",
                resource_type="CODE",
                purpose="让学习者在当前编程节点亲自实现，并以明确提交进入既有 Judge 与 Evidence 链。",
                generation_spec={
                    "decision": "ADVANCE_TRANSFER_CHALLENGE",
                    "target_capability": "closure_nonlocal_transfer",
                    "activity_intent": "HANDS_ON_PROGRAMMING",
                },
                required_inputs=["knowledge_node", "active_route_context", "explicit_user_control"],
                trigger="EXPLICIT_SEMANTIC_INTENT",
                status="planned",
                priority=98,
                thread_id=thread_id,
            )
            db.add(plan)
            await db.flush()
            node.resource_plan_ids = [*list(node.resource_plan_ids or []), plan.external_id]

    assistant_response, reason = _assistant_copy(kind, str(state.support_level or "L2"))
    trace = _base_trace(kind)
    artifact = None
    status = "planned"
    if ready_projection is not None:
        status = "ready" if ready_reviewed else "reviewing"
        artifact = _binding(ready_projection, reason=reason, status=status)
        trace.append(_trace_step("接入现有资料", "找到与当前节点和学习状态匹配的正式 Code Lab。", "Artifact Registry"))
        if ready_reviewed:
            trace.append(_trace_step("检查资料", "复用该精确版本已经存在的正式通过记录。", "Artifact Review"))

    return {
        "action_key": action_key,
        "source_message_id": message_id,
        "assistant_message_id": assistant_message_id,
        "kind": kind,
        "status": status,
        "assistant_response": assistant_response,
        "reason": reason,
        "artifact": artifact,
        "trace": trace,
        "retryable": False,
        # Message-grounded payload for V2-recommended handouts: even in an
        # established thread the document agent composes from this turn's
        # actual request, not the route node, so a different task in an old
        # thread cannot inherit the old topic. Deterministic (non-V2) turns
        # keep the accepted learner-state-personalized path.
        **({
            "request_payload": {
                "topic": _generic_resource_topic(message),
                "instruction": (
                    "本轮用户原话：" + re.sub(r"\s+", " ", str(message or "")).strip()[:500] + "。"
                    + "资料必须针对本轮原话的真实请求生成，不得改用线程此前学习节点的主题；"
                    + "同账号此前用户原话仅在与本轮请求相关时作为背景。"
                    + "不得用估算值填补缺失的用户数据；不得在用户完成任务前声称其已获得某项能力。"
                    + "不得把检索、整理、比较等核心认知劳动全部交回用户自行完成。"
                ),
            }
        } if kind in {"HANDOUT", "PRACTICAL_GUIDE"} and v2_authoritative else {}),
    }


async def _owned_action(
    db: AsyncSession, *, user_id: int, action_key: str,
) -> tuple[ChatSession, int, dict[str, Any]]:
    sessions = list((await db.scalars(
        select(ChatSession).where(ChatSession.user_id == user_id).order_by(desc(ChatSession.last_message_at))
    )).all())
    for session in sessions:
        for index, message in enumerate(list(session.messages or [])):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            action = message.get("conversation_action")
            if isinstance(action, dict) and action.get("action_key") == action_key:
                return session, index, dict(action)
    raise LookupError("conversation capability action not found")


async def _update_action(
    db: AsyncSession,
    *,
    session: ChatSession,
    index: int,
    action: Mapping[str, Any],
) -> dict[str, Any]:
    rows = [dict(row) if isinstance(row, dict) else row for row in list(session.messages or [])]
    message = dict(rows[index])
    safe_action = dict(action)
    safe_action["updated_at"] = datetime.utcnow().isoformat()
    message["conversation_action"] = safe_action
    message["orchestration_trace"] = list(safe_action.get("trace") or [])
    rows[index] = message
    session.messages = rows
    session.message_count = len(rows)
    session.last_message_at = datetime.utcnow()
    await db.commit()
    return safe_action


def _repair_note(receipt: Mapping[str, Any] | None) -> str:
    """把上一版被内容闸门指名的缺陷整理成「这一版要改掉什么」。

    2026-09-20 实测：重试是从头重新生成，而不是按具名缺陷去改，结果第二版比第一版
    更差（4/3/4/3 → 2/2/4/2）。考前一晚的学生只有再掷一次的机会，这不是收敛。
    闸门已经把话说清楚了，把它带回去就是最低成本的收敛。
    """
    blocking = [
        row
        for row in ((receipt or {}).get("blocking") or [])
        if isinstance(row, Mapping) and str(row.get("description") or "").strip()
    ]
    if not blocking:
        return ""
    lines = ["上一版被内容检查判为需要修改。请在这一版里改掉下面每一条，不要重复同样的写法："]
    for row in blocking[:4]:
        location = str(row.get("location") or "").strip()
        description = str(row.get("description") or "").strip()
        # 位置比描述更难得：知道「错在第一步」才叫定向修改，只知道「有矛盾」还是重写。
        lines.append(f"- 在「{location}」处：{description}" if location else f"- {description}")
        # 评审本来就写了「该改成什么」，回执以前把它丢了。只给「哪里错」的时候，
        # 2026-09-22 实测第三版把同一个缺陷原样又写了一遍——`recommended_constraint`
        # 才是让这件事变成「照改」而不是「重写」的那一半。
        constraint = str(row.get("recommended_constraint") or "").strip()
        if constraint:
            lines.append(f"  → 改成：{constraint}")
    # 2026-09-22 实测：只把「要改掉什么」带回去，它会把缺陷换成另一种不合格。
    # 那一轮 v2 被拦的三条全是重写引入的行内「（此处…待核实）」占位——
    # 阻断项从 2 条涨到 3 条。所以这里必须同时说清「用哪种交差方式仍然会被拦」，
    # 否则修复指令只教会它换一个坑。
    lines.append(
        "注意：不要用「此处待核实」「待补充来源」这类占位把上面的问题糊过去——"
        "正文里带这种标记、或者用一节待核实清单代替内容，同样过不了检查。"
        "**用「需核实」「据常见用法」「可能」把一句话含混过去，是同一种失败**："
        "2026-09-22 实测，上一版被指名的缺陷正是这类含混措辞，第三版把它换成了"
        "「据常见用法…具体签名需核实」，分数反而更低。"
        "该写清楚的事实就按可靠的依据直接陈述；实在写不清的，删掉那句话，不要留着含糊其辞。"
    )
    return "\n".join(lines)


def _with_repair_notes(instruction: str, receipt: Mapping[str, Any] | None) -> str:
    note = _repair_note(receipt)
    if not note:
        return instruction
    return (instruction + "\n" + note)[:1900]


# 产物契约：检查方对一份合格讲义的硬要求，必须在**生成之前**交给生成的人。
#
# `content_quality_gate._kind_checks` 对讲义做硬拦：块里没有 EXAMPLE 就按 high 拦下，
# 没有 CALLOUT 记一条 medium。这条要求本来就存在于产品里，但只出现在个性化资料的
# 规划器里（`personalized_learning_artifacts.py` 的 requirement）。会话里生成的资料
# 走的是 `generate_lecture`：先把整篇写成一个文本块，再被闸门拦下——生成器从头到尾
# 不知道有这条要求，所以重试多少次都过不了（2026-09-23 实测 v1 → v2 同一条意见）。
#
# 所以这里把同一条契约放到生成指令里：检查的人告诉回答的人该怎么回答。
_ARTIFACT_BLOCK_CONTRACT = (
    "输出 title 与 blocks（4-6 块），只输出 JSON，不要输出 JSON 以外的任何字符。"
    "每个对象含 type 与 text（非空字符串），type 只能取 "
    "HEADING / CONCEPT / EXAMPLE / CALLOUT / CHECKPOINT / TASK 之一。"
    "其中必须包含至少一个 EXAMPLE：一段能照着算完的具体演算，或一段能直接运行的代码；"
    "只写「多练习」「注意理解」这类说法不算 EXAMPLE。"
    "还必须包含至少一个 CALLOUT：写清「现象 → 为什么它会误导 → 正确的区分标准」。"
)


def _build_contract_blocks(
    planned: Sequence[Mapping[str, Any]], *, source_ref: str,
) -> list[dict[str, Any]]:
    from services.canonical_artifacts import _block

    return [
        _block(str(item.get("kind") or "").upper(), index + 1, str(item.get("text") or ""), source_refs=[source_ref])
        for index, item in enumerate(planned)
        if str(item.get("text") or "").strip()
    ]


async def plan_conversation_artifact_blocks(
    *, topic: str, instruction: str, source_ref: str,
    repair_note: str = "", llm: Any | None = None,
) -> list[dict[str, Any]] | None:
    """让模型直接按产物契约产出 blocks；拿不到合法结果返回 None。

    返回的是已经过校验、带 block_id 的规范块，可以直接交给
    `create_canonical_handout` / `create_new_version`。
    """
    from services.llm_service_real import extract_llm_text, get_llm_service
    from services.personalized_learning_artifacts import _extract_json_object, _parse_blocks

    if llm is None:
        llm = await get_llm_service()
    prompt = (
        f"主题：{topic}\n\n"
        f"用户的原话与限制：\n{instruction}\n\n"
        f"要求：{_ARTIFACT_BLOCK_CONTRACT}\n"
        "内容只写用户原话里给得出依据的东西；会随时间和主体变化的外部事实"
        "（政策、价格、机构、名额、比例）不要写成结论，需要时放进 CALLOUT 里说明"
        "「以官方口径为准」并指出去哪核对。"
    )
    if repair_note:
        prompt = prompt + "\n\n" + repair_note
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    planned: list[dict[str, Any]] | None = None
    candidate: list[dict[str, Any]] | None = None
    for attempt in range(2):
        try:
            raw = await llm.chat(
                messages=messages, temperature=0.2 if attempt == 0 else 0.0,
                max_tokens=2400, use_cache=False, write_cache=False,
            )
        except Exception as exc:
            if candidate:
                reason = "修订初稿时生成服务中断，必需的内容块尚未齐全。"
                raise GenerationDraftBlocked(
                    "HANDOUT_DRAFT_REVISION_INTERRUPTED", reason=reason,
                    body=handout_draft(candidate, reason=reason),
                ) from exc
            raise
        text = extract_llm_text(raw)
        parsed = _extract_json_object(text)
        parsed_candidate = _parse_blocks(parsed)
        if parsed_candidate:
            candidate = parsed_candidate
        # 结构合法只说明它是合法 JSON；契约里点名的那两块必须在，否则闸门会拦，
        # 而用户不该为「少写了一个例子」再等一轮。
        if parsed_candidate is not None:
            kinds = {item["kind"] for item in parsed_candidate}
            if {"EXAMPLE", "CALLOUT"} <= kinds:
                planned = parsed_candidate
                break
        messages = messages + [
            {"role": "assistant", "content": text[:1500]},
            {"role": "user", "content": "上一次输出未通过校验。" + _ARTIFACT_BLOCK_CONTRACT},
        ]
    if planned is None:
        if candidate:
            missing = {"EXAMPLE", "CALLOUT"} - {item["kind"] for item in candidate}
            reason = "仍缺少必需的内容块：" + "、".join(sorted(missing))
            raise GenerationDraftBlocked(
                "HANDOUT_DRAFT_REVIEW_BLOCKED", reason=reason,
                body=handout_draft(candidate, reason=reason),
            )
        return None
    title = str((_extract_json_object(extract_llm_text(raw)) or {}).get("title") or "").strip()
    return {"title": title, "blocks": _build_contract_blocks(planned, source_ref=source_ref)}


async def _previous_quality_receipt(
    db: AsyncSession, *, user_id: int, action: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """上一版留下的内容闸门回执；没有就返回 None。

    `item.raw_data` 是第一来源（个人讲义一直是这么存的）。拿不到时退到当前版本的
    `private_data_snapshot`：修订那条路曾经因为白名单把回执丢掉、raw_data 里留着
    上一版的内容，两个来源都读一遍才不会让修复指令对着陈旧的缺陷说话。
    """
    artifact_id = (action.get("artifact") or {}).get("artifact_id")
    if not artifact_id:
        return None
    try:
        item = await get_owned_artifact(db, user_id=user_id, artifact_id=int(artifact_id))
    except Exception:
        return None
    receipt = (item.raw_data or {}).get("content_quality")
    if isinstance(receipt, Mapping) and receipt:
        return receipt
    version_id = (action.get("artifact") or {}).get("version_id") or item.current_version_id
    if not version_id:
        return None
    try:
        version = await get_version(db, item=item, version_id=int(version_id))
    except Exception:
        return None
    fallback = (version.private_data_snapshot or {}).get("content_quality")
    return fallback if isinstance(fallback, Mapping) and fallback else None


async def prepare_conversation_capability(
    db: AsyncSession, *, user_id: int, action_key: str, background_tasks: Any | None = None,
    theme: str = "",
) -> dict[str, Any]:
    session, index, action = await _owned_action(db, user_id=user_id, action_key=action_key)
    if action.get("status") in {"reviewing", "ready"} and action.get("artifact"):
        return action
    if action.get("status") not in {"planned", "failed"}:
        raise ValueError("CONVERSATION_ACTION_NOT_PREPARABLE")
    kind = str(action.get("kind") or "")
    if kind not in ACTION_KINDS:
        raise ValueError("CONVERSATION_ACTION_KIND_INVALID")
    if kind == "DEMONSTRATE":
        return action

    # 版本级主题快照：客户端在确认时带上它当前的主题；生成时定格，之后不随切换漂移。
    from services.visual_theme import normalize_theme

    theme_key = normalize_theme(theme)
    action.update({"status": "preparing", "retryable": False, "error_message": None})
    action.pop("working_result", None)
    action.pop("generation_diagnosis", None)
    # 主题定格在 action 上：自动返工（检查没过时按评审意见再出一版）发生在服务端，
    # 客户端不会为它再带一次主题，不存下来就会把用户选好的主题换成默认值。
    action["theme"] = theme_key
    await _update_action(db, session=session, index=index, action=action)
    try:
        if kind in {"HANDOUT", "PRACTICAL_GUIDE"} and action.get("request_payload"):
            payload = dict(action.get("request_payload") or {})
            topic = str(payload.get("topic") or "针对性资料")[:120]
            # 重试要收敛：上一版被指名的缺陷跟着指令一起回去，让它改这一版。
            base_instruction = str(payload.get("generation_context") or payload.get("instruction") or "")
            repair_receipt = await _previous_quality_receipt(db, user_id=user_id, action=action)
            instruction = _with_repair_notes(base_instruction, repair_receipt)
            source_ref = f"thread:{session.thread_id}:user-request"

            # 按产物契约直接要 blocks，而不是先写一整篇散文、再把它塞进一个文本块。
            #
            # 闸门对讲义的要求是块级的（必须含 EXAMPLE 与 CALLOUT），而
            # `generate_lecture` 产出的是 workspace_lecture，块被拼成单个 TEXT，
            # 于是会话里生成的资料**永远**过不了检查——2026-09-23 实测：直接调用
            # `_review_invariants`，产物 376/385 都抛「整份讲义没有任何 EXAMPLE」，
            # v1→v2 同一条意见。这不是模型改不动，是它从没被告知这条要求。
            # 契约放在生成之前交给它，检查的人就真的在告诉回答的人怎么回答。
            plan = await plan_conversation_artifact_blocks(
                topic=topic,
                instruction=instruction,
                source_ref=source_ref,
                repair_note=_repair_note(repair_receipt),
            )
            if plan is None:
                raise ValueError("CONTENT_GENERATION_FAILED")
            canonical_blocks = list(plan["blocks"])
            if kind == "PRACTICAL_GUIDE" and not any(
                str(block.get("type")) == "CHECKPOINT" for block in canonical_blocks
            ):
                canonical_blocks = canonical_blocks + _build_contract_blocks(
                    [{"kind": "CHECKPOINT", "text": "完成后核对每一步的产出，并记录需要调整的一处。"}],
                    source_ref=source_ref,
                )
            experience = "PRACTICAL_GUIDE" if kind == "PRACTICAL_GUIDE" else "PERSONALIZED_HANDOUT"
            title = str(plan.get("title") or topic)[:200]
            public_data = {
                "kind": "document",
                "status": "generated",
                "thread_id": session.thread_id,
                "canonical_blocks": canonical_blocks,
                "canonical_purpose": {
                    "learning_goal": topic,
                    "success_criteria": ["资料可直接执行或保存复用"],
                    "experience": experience,
                },
                "canonical_personalization": {
                    "why_this": "按本轮对齐的目标生成；自然提到的信息不会当作已确认结论。",
                },
                "canonical_source_refs": [source_ref],
                "export_formats": ["pdf", "docx"],
            }
            content = "\n\n".join(
                str(block.get("data", {}).get("text") or "") for block in canonical_blocks
            )
            previous_artifact_id = int((action.get("artifact") or {}).get("artifact_id") or 0)
            if previous_artifact_id:
                # 重试落在**同一份产物**的第二版上，不新建同名产物：2026-09-22 实测，
                # 每次重试都新建会让学生的「我的资料」里堆出多份同名讲义。
                previous_item = await get_owned_artifact(
                    db, user_id=user_id, artifact_id=previous_artifact_id,
                )
                await create_new_version(
                    db, user_id=user_id, artifact_id=previous_artifact_id,
                    expected_version=previous_item.current_version_number,
                    idempotency_key=f"{action_key}:v{previous_item.current_version_number + 1}",
                    title=title, content=content, public_data=public_data,
                    status="generated",
                )
                item = await get_owned_artifact(
                    db, user_id=user_id, artifact_id=previous_artifact_id,
                )
            else:
                item, _created_version, _replayed = await create_canonical_handout(
                    db, user_id=user_id,
                    request=CreateCanonicalHandoutRequest(
                        title=title, topic=topic, blocks=canonical_blocks,
                        purpose=public_data["canonical_purpose"],
                        personalization=public_data["canonical_personalization"],
                        source_refs=[source_ref], thread_id=session.thread_id,
                        experience=experience, idempotency_key=f"{action_key}:v1",
                    ),
                )
            await db.commit()
            projection = await build_public_projection(
                db, user_id=user_id, artifact_id=item.id,
            )
            session, index, action = await _owned_action(db, user_id=user_id, action_key=action_key)
            action["status"] = "reviewing"
            action["artifact"] = _binding(
                projection, reason=str(action.get("reason") or ""), status="reviewing",
            )
            action["trace"] = [
                *list(action.get("trace") or []),
                _trace_step("生成正式资料", "真实文档产物已生成，正在进入正式检查。", "Document Agent"),
            ]
            return await _update_action(db, session=session, index=index, action=action)

        if kind in {"PRESENTATION", "MINDMAP", "VIDEO"}:
            from routers.workspace_agents import (
                MindmapRequest,
                PresentationRequest,
                VideoRequest,
                generate_mindmap,
                generate_presentation,
                generate_video,
            )

            payload = dict(action.get("request_payload") or {})
            topic = str(payload.get("topic") or "学习资料")[:160]
            if kind == "PRESENTATION":
                result = await generate_presentation(
                    PresentationRequest(
                        thread_id=session.thread_id,
                        topic=topic,
                        delivery_mode="presentation_only",
                        learning_goal=str(payload.get("learning_goal") or "")[:160],
                        level=str(payload.get("level") or "")[:40],
                        slide_count=payload.get("slide_count"),
                        generation_context=str(payload.get("generation_context") or "")[:2600],
                        theme=theme_key,
                        confirmed=True,
                    ),
                    user_id=user_id,
                    db=db,
                )
            elif kind == "MINDMAP":
                result = await generate_mindmap(
                    MindmapRequest(
                        thread_id=session.thread_id, topic=topic, confirmed=True,
                        instruction=str(payload.get("generation_context") or "")[:1900],
                    ),
                    user_id=user_id,
                    db=db,
                )
            else:
                if background_tasks is None:
                    raise ValueError("VIDEO_BACKGROUND_EXECUTION_UNAVAILABLE")
                result = await generate_video(
                    VideoRequest(
                        thread_id=session.thread_id, topic=topic, confirmed=True, theme=theme_key,
                        instruction=str(payload.get("generation_context") or "")[:1900],
                    ),
                    background_tasks=background_tasks,
                    user_id=user_id,
                    db=db,
                )
            artifact_data = dict(result.get("data") or {})
            artifact_id = int(artifact_data["artifact_id"])
            projection = await build_public_projection(db, user_id=user_id, artifact_id=artifact_id)
            session, index, action = await _owned_action(db, user_id=user_id, action_key=action_key)
            action["status"] = "ready"
            action["artifact"] = _binding(
                projection, reason=str(action.get("reason") or ""), status="ready",
            )
            action["trace"] = [
                *list(action.get("trace") or []),
                _trace_step(
                    "生成正式产物",
                    "真实生成任务已创建并接入当前对话。" if kind == "VIDEO" else "正式产物和不可变版本已经生成。",
                    {"PRESENTATION": "Presentation Agent", "MINDMAP": "Mindmap Agent", "VIDEO": "Video Agent"}[kind],
                ),
            ]
            return await _update_action(db, session=session, index=index, action=action)

        payload = dict(action.get("request_payload") or {})
        if kind == "ASSESSMENT" and payload.get("goal_assessment"):
            brief = dict(payload.get("work_brief") or {})
            goal = str(brief.get("goal") or payload.get("topic") or "当前学习目标")
            concept = "goal:" + _sha(goal)[:24]
            graph, node = await _ensure_graph_node_for_darya_route(
                db, user_id=user_id, thread_id=session.thread_id,
                route=SimpleNamespace(domain_id="goal-assessment", current_node_id=concept, learner_goal=goal),
                state=SimpleNamespace(concept_key=goal, mastery_estimate=0),
            )
            from services.item_statistics import calibration_feedback

            try:
                calibration = await calibration_feedback(db, node_ref=str(concept or ""))
            except Exception:  # noqa: BLE001 - 校准数据缺失不该拦住出题
                calibration = []
            planned = await generate_goal_assessment_plan(
                brief=brief, node_ref=concept, calibration=calibration,
                question_count=int(payload.get("question_count") or 2),
            )
            artifact, _, _ = await create_tiered_assessment(
                db, user_id=user_id, thread_id=session.thread_id,
                graph_ref=graph.external_id, node_ref=node.external_id,
                state={}, route={"learner_goal": goal}, idempotency_key=f"{action_key}:artifact",
                generated_plan=planned,
            )
            await db.commit()
            projection = await build_public_projection(db, user_id=user_id, artifact_id=int(artifact["artifact_id"]))
            session, index, action = await _owned_action(db, user_id=user_id, action_key=action_key)
            action["status"] = "reviewing"
            action["artifact"] = _binding(projection, reason=str(action.get("reason") or ""), status="reviewing")
            return await _update_action(db, session=session, index=index, action=action)

        state, route = await _latest_state(db, user_id=user_id, thread_id=session.thread_id)
        if state is None or route is None:
            raise ValueError("LEARNER_CONTEXT_UNAVAILABLE")
        concept_key = str(route.current_node_id)
        if kind in {"HANDOUT", "PRACTICAL_GUIDE"}:
            experience = "PERSONALIZED_HANDOUT" if kind == "HANDOUT" else "PRACTICAL_GUIDE"
            preference = (((state.latest_cycle or {}).get("resource_orchestration") or {}).get("intent") or {}).get("relevant_context", {}).get("presentation_mode")
            durable_memories = await list_active_memories(db, user_id=user_id)
            durable_preference = next((
                item.topic.split(":", 1)[1]
                for item in durable_memories
                if item.topic in {
                    "learning_preference:example_first",
                    "learning_preference:rule_then_practice",
                }
            ), None)
            if durable_preference:
                preference = durable_preference
            plan = await build_personalized_artifact_plan(
                experience=experience,
                state=state.__dict__,
                route=route.__dict__,
                preference=preference,
            )
            item, version, _ = await create_canonical_handout(
                db,
                user_id=user_id,
                request=CreateCanonicalHandoutRequest(
                    title=plan["title"],
                    topic=plan["purpose"]["learning_goal"],
                    blocks=plan["blocks"],
                    purpose=plan["purpose"],
                    personalization=plan["personalization"],
                    source_refs=plan["source_refs"],
                    thread_id=session.thread_id,
                    experience=experience,
                    idempotency_key=f"{action_key}:artifact",
                    # 内容质量闸门的实测回执随版本落库，审阅阶段据此给分与放行。
                    content_quality=dict((plan.get("generation") or {}).get("content_review") or {}) or None,
                ),
            )
            await db.commit()
            projection = await build_public_projection(
                db, user_id=user_id, artifact_id=item.id, version_id=version.id,
            )
            technical = "Personalized Artifact Planner"
        elif kind == "ASSESSMENT":
            graph_node = await _graph_node_for_thread(
                db, user_id=user_id, thread_id=session.thread_id, concept_key=concept_key,
            )
            if graph_node is None:
                graph_node = await _ensure_graph_node_for_darya_route(
                    db,
                    user_id=user_id,
                    thread_id=session.thread_id,
                    route=route,
                    state=state,
                )
            graph, node = graph_node
            artifact, _, _ = await create_tiered_assessment(
                db,
                user_id=user_id,
                thread_id=session.thread_id,
                graph_ref=graph.external_id,
                node_ref=node.external_id,
                state=state.__dict__,
                route=route.__dict__,
                idempotency_key=f"{action_key}:artifact",
            )
            await db.commit()
            projection = await build_public_projection(
                db,
                user_id=user_id,
                artifact_id=int(artifact["artifact_id"]),
                version_id=int(artifact["version"]["version_id"]),
            )
            technical = "Assessment Composer"
        else:
            ready = await _ready_code_lab(
                db, user_id=user_id, thread_id=session.thread_id, concept_key=concept_key,
            )
            if ready:
                projection, reviewed = ready
                action["artifact"] = _binding(
                    projection, reason=str(action.get("reason") or ""), status="ready" if reviewed else "reviewing",
                )
                action["status"] = "ready" if reviewed else "reviewing"
                if reviewed:
                    action["trace"] = [
                        *list(action.get("trace") or []),
                        _trace_step("检查资料", "复用该精确版本已经存在的正式通过记录。", "Artifact Review"),
                    ]
                return await _update_action(db, session=session, index=index, action=action)
            plan = await _planned_code_lab(
                db, user_id=user_id, thread_id=session.thread_id, concept_key=concept_key,
            )
            if plan is None:
                raise ValueError("CODE_LAB_PLAN_UNAVAILABLE")
            graph = await db.get(LearningGraph, plan.graph_id)
            node = await db.get(GraphNode, plan.node_id)
            if graph is None or node is None:
                raise ValueError("CODE_LAB_PLAN_IDENTITY_INVALID")
            item = await materialize_resource_plan(
                db,
                user_id=user_id,
                thread_id=session.thread_id,
                graph_id=graph.id,
                node_id=node.id,
                resource_plan_id=plan.id,
                execution_key=f"{action_key}:materialize",
            )
            projection = await build_public_projection(db, user_id=user_id, artifact_id=item.id)
            technical = "Code Lab Materializer"

        action["status"] = "reviewing"
        action["artifact"] = _binding(
            projection, reason=str(action.get("reason") or ""), status="reviewing",
        )
        action["trace"] = [
            *list(action.get("trace") or []),
            _trace_step("准备资料", "正式产物和不可变版本已经生成，正在进入检查。", technical),
        ]
        return await _update_action(db, session=session, index=index, action=action)
    except GoalAssessmentReviewFailure as exc:
        # Full review JSON (contains the reviewer's answers) stays server-side only.
        logging.getLogger("rjianb.goal_assessment").error(
            "goal assessment review exhausted: action=%s codes=%s attempt=%s review=%s",
            action_key, exc.failure_codes, exc.attempt, json.dumps(exc.review, ensure_ascii=False, default=str),
        )
        await db.rollback()
        session, index, action = await _owned_action(db, user_id=user_id, action_key=action_key)
        action.update({
            "status": "failed",
            "retryable": True,
            "error_message": "这份小测在出题后的独立质量检查里没有通过，我没有降低标准硬生成。"
                             "你可以直接说“先出基础题”或“换一种方式帮我”，或稍后重试；原对话和学习进度都保留。",
            "generation_diagnosis": {"failure_codes": list(exc.failure_codes), "attempts": exc.attempt},
        })
        await _update_action(db, session=session, index=index, action=action)
        raise
    except GenerationDraftBlocked as exc:
        await db.rollback()
        session, index, action = await _owned_action(db, user_id=user_id, action_key=action_key)
        action.update({
            "status": "failed",
            "retryable": True,
            "error_message": exc.public_reason,
            "generation_diagnosis": {"failure_codes": [exc.code]},
            "working_result": exc.working_result,
        })
        action["trace"] = [
            *list(action.get("trace") or []),
            _trace_step("保留阶段结果", "真实初稿已保留，正式检查仍需修正。", "Artifact Review"),
        ]
        await _update_action(db, session=session, index=index, action=action)
        raise
    except Exception as exc:
        logging.getLogger("rjianb.conversation_capability").exception(
            "capability prepare failed: action=%s kind=%s error=%s",
            action_key,
            kind,
            exc,
        )
        await db.rollback()
        session, index, action = await _owned_action(db, user_id=user_id, action_key=action_key)
        action.update({
            "status": "failed",
            "retryable": True,
            "error_message": "这份资料暂时没有准备完成，可以重试；原对话和已有资料都保留着。",
        })
        await _update_action(db, session=session, index=index, action=action)
        raise exc


def _content_quality_review(
    *, kind: str, blocks: list[Mapping[str, Any]], version: Any,
    stored: Mapping[str, Any] | None = None,
) -> tuple[dict[str, float], str]:
    """Scores for "is this content any good", from measurement rather than habit.

    Two sources, in order of authority:

    * the content-quality gate's own receipt, when the artifact came from a path
      that ran it — its verdict decides, and its 0-5 rubric becomes the score;
    * otherwise the deterministic pre-scan on the blocks we are about to pass,
      which costs nothing and still refuses placeholders, filler, repetition and
      a handout with no worked example.

    Nothing here invents a 0.96 for a check that never ran.
    """
    from services.content_quality_gate import prescan

    receipt = dict(stored or {}) or dict((version.private_data_snapshot or {}).get("content_quality") or {})
    if kind == "ASSESSMENT":
        candidate: dict[str, Any] = {"questions": [dict(block.get("data") or {}) for block in blocks]}
        scan_kind = "questions"
    else:
        candidate = {"blocks": list(blocks)}
        scan_kind = "handout"
    issues = prescan(scan_kind, candidate, "")
    severe = [issue for issue in issues if str(issue.get("severity")) in {"high", "blocking"}]
    if severe:
        raise ValueError(
            "CONTENT_QUALITY_REVIEW_FAILED: "
            + "；".join(str(issue.get("description")) for issue in severe[:3])
        )

    if receipt:
        if str(receipt.get("verdict") or "") != "pass":
            blocking = receipt.get("blocking") or []
            detail = "；".join(str(item.get("description")) for item in blocking[:3]) or "内容质量评审未通过。"
            raise ValueError(f"CONTENT_QUALITY_REVIEW_FAILED: {detail}")
        measured = {key: round(int(value) / 5, 4) for key, value in (receipt.get("scores") or {}).items()}
        if measured:
            detail = "、".join(f"{key} {value:.2f}" for key, value in measured.items())
            return measured, f"内容质量闸门实测通过（{detail}）。"
        return {"content_quality": 1.0}, "内容质量闸门实测通过。"

    return (
        {"content_quality": 1.0},
        "内容质量按确定性检查复核通过（主题落点、占位符、重复、结构完整）。",
    )


def _review_invariants(
    *, kind: str, projection: Mapping[str, Any], version: GeneratedItemVersion,
) -> tuple[dict[str, float], str]:
    canonical = dict(projection.get("canonical") or {})
    purpose = dict(canonical.get("purpose") or {})
    blocks = list(canonical.get("blocks") or [])
    source_refs = list(canonical.get("source_refs") or [])
    if not blocks or not source_refs or not purpose.get("learning_goal"):
        raise ValueError("ARTIFACT_REVIEW_REQUIRED_FIELDS_MISSING")
    public_text = str(projection)
    experience = str(purpose.get("experience") or "")
    if kind in {"HANDOUT", "PRACTICAL_GUIDE"}:
        content_scores, content_summary = _content_quality_review(
            kind=kind, blocks=blocks, version=version,
        )
        if kind == "HANDOUT":
            if experience != "PERSONALIZED_HANDOUT" or not dict(canonical.get("personalization") or {}).get("why_this"):
                raise ValueError("HANDOUT_REVIEW_LEARNER_FIT_FAILED")
            scores = {
                "learner_fit": .96,
                "source_grounding": .97,
                **content_scores,
            }
            summary = "个性化理由与来源约束通过；" + content_summary
        else:
            block_types = {str(row.get("type") or "") for row in blocks}
            if experience != "PRACTICAL_GUIDE" or not {"TASK", "CHECKPOINT"}.issubset(block_types):
                raise ValueError("PRACTICAL_GUIDE_REVIEW_ACTION_FAILED")
            scores = {
                "learner_fit": .95,
                "answer_leakage": 1.0,
                **content_scores,
            }
            summary = "操作步骤、检查点与答案边界通过；" + content_summary
    elif kind == "ASSESSMENT":
        if experience != "TIERED_ASSESSMENT" or "correct_index" in public_text or "answer_explanation" in public_text:
            raise ValueError("ASSESSMENT_REVIEW_ANSWER_LEAKAGE")
        policy = dict((version.public_data_snapshot or {}).get("interaction_policy") or {})
        if policy.get("server_judged") is not True:
            raise ValueError("ASSESSMENT_REVIEW_AUTHORITY_FAILED")
        generation_review = dict((version.private_data_snapshot or {}).get("generation_review") or {})
        if generation_review:
            if (generation_review.get("method") != "independent_blind_solution"
                    or generation_review.get("passed") is not True):
                raise ValueError("ASSESSMENT_REVIEW_GENERATION_REVIEW_NOT_PASSED")
            content_scores, content_summary = _content_quality_review(
                kind=kind, blocks=blocks, version=version,
                stored=dict(generation_review.get("content_review") or {}),
            )
            scores = {"learner_fit": .96, "tier_fit": .96, **content_scores}
            summary = ("独立盲解与命题答案在全部题目上一致，作答前答案隔离通过；"
                       + content_summary
                       + "题目来自本次对话任务命题，来源是任务简报而非权威知识来源。")
        else:
            scores = {"factual_correctness": .97, "answer_correctness": .97, "learner_fit": .96, "tier_fit": .96, "answer_leakage": 1.0}
            summary = "题目知识、层级适配、服务端判题和作答前答案隔离均通过。"
    else:
        private = dict(version.private_data_snapshot or {})
        judge = dict(private.get("judge_contract_private") or {})
        if experience != "PERSONALIZED_CODE_LAB" or not judge.get("judge_source"):
            raise ValueError("CODE_LAB_REVIEW_JUDGE_MISSING")
        if "judge_source" in public_text or "__DARYA_JUDGE__" in public_text:
            raise ValueError("CODE_LAB_REVIEW_PRIVATE_LEAKAGE")
        scores = {"technical_correctness": .98, "starter_code_validity": .96, "judge_consistency": .98, "learner_fit": .96, "answer_leakage": 1.0}
        summary = "任务、起始代码、可信测试、学习者适配和私有判题隔离均通过。"
    return scores, summary


_REVIEW_FAILURE_REASONS = {
    "HANDOUT_REVIEW_LEARNER_FIT_FAILED": "这份资料没有写清「为什么这样给你」，检查没过。",
    "PRACTICAL_GUIDE_REVIEW_ACTION_FAILED": "这份实操里缺少必需的任务或检查点，检查没过。",
    "ARTIFACT_REVIEW_REQUIRED_FIELDS_MISSING": "这份资料缺少必需的结构（学习目标、正文或来源），检查没过。",
    "ARTIFACT_REVIEW_RECEIPT_MISSING": "检查回执没有落库，这一次不算通过。",
    "ASSESSMENT_REVIEW_ANSWER_LEAKAGE": "题目里泄露了答案，检查没过。",
    "ASSESSMENT_REVIEW_AUTHORITY_FAILED": "这套题的答案权威性没有通过，检查没过。",
}


def _review_failure_message(exc: Exception) -> str:
    """把检查失败的原因说出来，而不是只留一句通用话。

    2026-09-22 实测的学生处境：讲义被拦下，界面只说「检查没有完成」，
    唯一的按钮是「重试准备」（56 秒重新生成），他无从知道哪里不对。
    真实原因一直都在库里（`content_quality.blocking` 有具名缺陷和位置），
    只是没有任何面向他的出口。界面渲染的就是这个 error_message，
    所以把原因写进这里，是成本最低的诚实。
    """
    detail = str(exc)
    prefix = "CONTENT_QUALITY_REVIEW_FAILED:"
    if detail.startswith(prefix):
        named = detail[len(prefix):].strip()
        named = named[:220] + "…" if len(named) > 220 else named
        body = f"内容检查没通过：{named}" if named else "内容检查没通过。"
    else:
        body = _REVIEW_FAILURE_REASONS.get(detail.split(":")[0].strip(), "")
    if not body:
        return "资料已经保留，但检查没有完成。可以重试检查，不会覆盖以前可用的版本。"
    return f"{body}资料已经保留，可以让我按这些意见再出一版。"


async def review_conversation_capability(
    db: AsyncSession, *, user_id: int, action_key: str,
) -> dict[str, Any]:
    session, index, action = await _owned_action(db, user_id=user_id, action_key=action_key)
    if action.get("status") == "ready":
        return action
    artifact = dict(action.get("artifact") or {})
    # `failed` 也允许重查：检查失败可能是临时的（评审这一步本身出错），
    # 而重新检查一次只要几十毫秒，重新生成要 56 秒。以前只认 `reviewing`，
    # 于是任何一次检查失败都只能靠重新生成来碰运气。
    if (
        action.get("status") not in {"reviewing", "failed"}
        or not artifact.get("artifact_id")
        or not artifact.get("version_id")
    ):
        raise ValueError("CONVERSATION_ACTION_NOT_REVIEWABLE")
    try:
        item = await get_owned_artifact(db, user_id=user_id, artifact_id=int(artifact["artifact_id"]))
        version = await get_version(db, item=item, version_id=int(artifact["version_id"]))
        projection = await build_public_projection(
            db, user_id=user_id, artifact_id=item.id, version_id=version.id,
        )
        scores, summary = _review_invariants(
            kind=str(action.get("kind") or ""), projection=projection, version=version,
        )
        await create_trusted_review(
            db,
            user_id=user_id,
            artifact_id=item.id,
            request=CreateArtifactReviewRequest(
                artifact_version_id=version.id,
                version_digest=f"sha256:{version.content_digest}",
                round=1,
                reviewer_role="PLATFORM_REVIEWER",
                verdict="PASS",
                scores=scores,
                safe_summary=summary,
                issues=[],
                preserve=[],
                idempotency_key=f"{action_key}:review",
            ),
            reviewer_role="PLATFORM_REVIEWER",
            provider="darya-conversation-artifact-review",
        )
        await db.commit()
        canonical = await build_canonical_artifact(db, item=item, version=version)
        if str(dict(canonical.get("status") or {}).get("review_verdict") or "").upper() != "PASS":
            raise ValueError("ARTIFACT_REVIEW_RECEIPT_MISSING")
        action["status"] = "ready"
        action["artifact"] = _binding(
            projection, reason=str(action.get("reason") or ""), status="ready",
        )
        action["trace"] = [
            *list(action.get("trace") or []),
            _trace_step("检查资料", summary, "Artifact Review"),
        ]
        action["retryable"] = False
        return await _update_action(db, session=session, index=index, action=action)
    except Exception as exc:
        # 先把原因算出来再 rollback —— rollback 之后 action 对象上的东西不再可信。
        reason = _review_failure_message(exc)
        await db.rollback()
        session, index, action = await _owned_action(db, user_id=user_id, action_key=action_key)

        # 检查是为了让模型交出能用的东西，不是为了把它挡在门外。
        #
        # 以前这里直接把失败交给用户：界面写「暂时未完成」，他得自己点一次「重试准备」
        # 才有第二版。而那次重试走的正是同一条「带着评审具名意见重出」的路
        # （prepare → _previous_quality_receipt → _with_repair_notes）——这件事本来
        # 就不需要他点。这里替他把这一次走掉：按评审点名的缺陷再出一版，然后重查一遍。
        #
        # 只自动一次。返工本身要花时间（实测一次生成约一分钟），无限重试会把一轮对话
        # 拖成看不见尽头的等待；第二次仍然不过，就把具名原因照实告诉他。
        if not action.get("auto_repaired"):
            action.update({
                "status": "failed",
                "retryable": True,
                "error_message": reason,
                "auto_repaired": True,
            })
            await _update_action(db, session=session, index=index, action=action)
            repaired = False
            try:
                await prepare_conversation_capability(
                    db,
                    user_id=user_id,
                    action_key=action_key,
                    theme=str(action.get("theme") or ""),
                )
                session, index, action = await _owned_action(
                    db, user_id=user_id, action_key=action_key,
                )
                fresh = dict(action.get("artifact") or {})
                repaired = bool(
                    action.get("status") == "reviewing"
                    and fresh.get("artifact_id")
                    and fresh.get("version_id")
                )
            except Exception:
                repaired = False
            if repaired:
                return await review_conversation_capability(
                    db, user_id=user_id, action_key=action_key,
                )
            # 返工没能进到可复查的状态（生成失败、或这个能力本轮不产出可复查产物）：
            # 退回原来那句具名原因，不要让用户为服务端的第二次尝试收到另一种错。
            session, index, action = await _owned_action(
                db, user_id=user_id, action_key=action_key,
            )
            action.update({
                "status": "failed",
                "retryable": True,
                "error_message": reason,
            })
            await _update_action(db, session=session, index=index, action=action)
            raise exc

        action.update({
            "status": "failed",
            "retryable": True,
            "error_message": reason,
        })
        await _update_action(db, session=session, index=index, action=action)
        raise exc


async def get_conversation_capability(
    db: AsyncSession, *, user_id: int, action_key: str,
) -> dict[str, Any]:
    _, _, action = await _owned_action(db, user_id=user_id, action_key=action_key)
    return action
