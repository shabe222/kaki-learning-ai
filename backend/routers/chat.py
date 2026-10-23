"""对话路由 - chat / chat_stream / chat_quick / chat history"""

import json
from typing import Any, Mapping
import re
import asyncio
import contextlib
import logging
import uuid
from dataclasses import replace
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select

import database.connection as db_conn
from config import settings
from database.models import ChatSession, ExamPaper, GeneratedItem, TutorSession
from database.dependencies import get_db
from graph.runtime import execute_workflow
from schemas.chat import ChatRequest
from services.cache_service import CacheService
from services.idempotency import get_idempotency_guard

# 幂等重放缓存的上限：超过就不缓存正文，只记住「这一轮已完成」。
_SSE_CACHE_LIMIT = 512 * 1024


async def _reap_task(task: "asyncio.Task[Any] | None") -> None:
    """把一个还活着的后台任务收干净——取消，并且等它真的退出。

    `await task` 是这句话的一半：只 cancel 不等，任务可能还在它的下一跳里跑。
    """
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(BaseException):
        await task


@contextlib.asynccontextmanager
async def _task_must_not_outlive_this_block(task: "asyncio.Task[Any]"):
    """保证 task 不会活过这个 with 块——正常结束、抛异常、生成器被关闭，都一样。

    这条保证是为了数据库。下面 capability 那个任务是从请求作用域的会话上长出来的：
    会话由 `async with ... as db` 持有，块一退就释放。如果任务还在跑，它就会在已关闭的
    会话上继续用那条连接（SQLAlchemy 报 non-checked-in connection），而它当时若正处在一个
    写事务里，SQLite 的写锁也不会释放——之后整个库对所有人都是 database is locked。
    2026-09-20 的真人测试实测到了这个形状：一轮之后所有写接口连续 500 了七分钟。
    """
    try:
        yield task
    except BaseException:
        await _reap_task(task)
        raise

# Streaming chat must use the real provider factory.  The legacy LLMService
# factory is retained for non-streaming agent workflows and has no chat_stream.
from services.llm_service_real import get_llm_service, get_runtime_llm_service
from services.profile_service import load_existing_profile, save_profile
from services.normalized_learner_state import get_normalized_learner_state

# 学习者类型权重的用户可读标签；键与 PreferenceTaxonomy 一致。
_LEARNER_TYPE_LABELS = {
    "visual": "图示优先", "video": "视频讲解", "reading": "阅读理解", "document": "讲义资料",
    "practice": "练习驱动", "code": "代码实践", "example": "例题引导", "step_by_step": "逐步讲解",
    "concise": "简洁表达", "detailed": "详细说明",
}
from services.profile_onboarding import PROFILE_ONBOARDING_MODE
from services.chat_service import (
    append_chat_message,
    get_thread_style,
    load_chat_history,
    save_chat_session,
    set_thread_style,
)
from services.chat_context import ChatContextBuilder, resolve_style
from services.conversation_capability_actions import (
    build_planned_action_directive,
    natural_clarification_question,
    plan_conversation_capability,
)
from services.artifact_generation_context import build_artifact_generation_context, has_reviewed_evidence
from services.memory_service import (
    detect_memory_candidate,
    list_confirmed_memories,
    persist_memory_action,
)
from services.impression_service import (
    build_relevant_impression_context,
    list_user_impressions,
)
from services.workspace_runtime import (
    begin_runtime_operation,
    complete_runtime_operation,
    fail_runtime_operation,
)
from services.workspace_materials import (
    MaterialAccessError,
    build_conversation_material_context,
    should_retrieve_material_context,
    should_use_material_context,
)
from services.workspace_references import (
    ReferenceAccessError,
    ResolvedWorkspaceReferences,
    attach_trusted_selection_context,
    build_reference_question_context,
    resolve_workspace_references,
)
from services.tutor_orchestrator import (
    DEFAULT_GRAPH_CATALOG,
    build_tutor_chat_directive,
    ensure_tutor_plan_alignment,
    recent_voice_session_summary,
)
from services.tutor_core import decide_tutor_action, resolve_tutor_context
from services.darya_universal_tutor import build_universal_directive_from_message
from services.resource_service import save_resources
from services.tide_interaction import (
    build_explicit_response_directive,
    build_tide_system_directive,
    decide_tide_interaction,
    tide_decision_from_model_support,
    detect_explicit_response_constraint,
    enforce_explicit_response_constraint,
    enforce_tide_response_contract,
)
from services.hallucination_guard import available_channel_names, review as review_hallucination
from services.turn_search import resolve_turn_search
from services.prompt_ledger import PromptLedger, dump as dump_prompt
from services.memory_ledger import MemoryCandidate, remember as remember_memory
from services.tide_search_evidence import (
    SearchContext,
    build_evidence_bundle,
    build_evidence_directive,
    enforce_evidence_response,
    search_recovery_result,
)
from services.darya_search_grounding import (
    build_search_grounding,
    build_search_grounding_directive,
    calibrate_bundle_for_search_plan,
    decide_search_plan,
    search_plan_from_model,
)
from services.darya_goal_collaboration import (
    GoalCollaborationPlan,
    build_goal_collaboration_directive,
    plan_goal_collaboration,
)
from services.darya_cognition_frame import (
    build_cognition_frame,
    build_cognition_frame_directive,
)
from services.darya_cognition_orchestrator_v2 import (
    build_orchestration_directive,
    evaluate_orchestration_result,
    plan_cognition_orchestration_v2,
    proactive_semantic_route,
)
from services.darya_model_support import (
    build_model_support_directive,
    build_model_support_packet,
)
from services.darya_cognition_evaluator import (
    evaluate_cognition_result,
    needs_cognition_repair,
)
from services.darya_safety_support import child_violence_safety_response
from services.tide_shared_perspective import (
    current_artifact_target,
    decision_context as tide_perspective_context,
    get_latest_snapshot as get_tide_perspective,
    persist_candidate_update as persist_tide_perspective_candidate,
    persist_goal_collaboration_update,
    public_snapshot as public_tide_perspective,
)
from services.tide_workspace_object import (
    build_workspace_object_directive,
    current_artifact_target as workspace_object_artifact_target,
    get_current_workspace_object,
    public_workspace_object,
    sync_workspace_object_from_perspective,
    workspace_object_context,
)
from services.tide_artifact_action import (
    ArtifactActionError,
    artifact_action_context,
    build_artifact_action_directive,
    create_artifact_action_proposal,
    get_latest_artifact_action,
    is_artifact_improvement_request,
    public_artifact_action,
)
from services.tide_user_context import (
    build_user_context_directive,
    public_claim as public_user_context_claim,
    select_relevant_confirmed_context,
)
from services.tide_long_term_memory import (
    LEARNING_PREFERENCE_TOPICS,
    build_explicit_candidate,
    build_memory_directive,
    memory_snapshot as long_term_memory_snapshot,
    persist_explicit_memory,
    persist_observed_world_model_claims,
    public_memory as public_long_term_memory,
    is_memory_inspection,
    select_relevant_memories,
)
from services.darya_perspective_engine import (
    build_current_perspective,
    build_current_perspective_from_model,
    build_perspective_directive,
    enforce_capability_boundaries,
    get_latest_perspective as get_darya_current_perspective,
    persist_current_perspective,
    public_perspective as public_darya_current_perspective,
)
from services.darya_action_execution import (
    build_action_execution_directive,
    enforce_action_execution_response,
    plan_action_execution,
)
from services.darya_learning_loop import (
    get_learning_route_state,
    is_learning_loop_message,
    run_learning_turn,
)
from services.darya_teaching_stage import control_teaching_stage, explicit_topic_switch_from_message, stage_action_from_message
from services.darya_learning_resources import requested_teaching_form
from services.darya_domain_registry import get_domain_pack
from services.proactive_tutor import (
    defer_tide_opportunities_for_control,
    emit_tide_learning_opportunity,
)
from services.darya_domain_resolver import (
    build_domain_disambiguation_question,
    resolve_learning_domain,
)
from services.darya_search_decision import (
    decide_search_need,
    build_search_context_from_conversation,
)
from utils.text import extract_assistant_summary
from utils.auth import get_current_user_id, require_self

logger = logging.getLogger(__name__)
router = APIRouter()


def _workspace_stream_error(request_id: str, stage: str) -> dict:
    return {
        "type": "error",
        "code": "PROVIDER_CONNECTION_FAILED",
        "message": "AI 服务暂时无法连接，请稍后重试。",
        "request_id": request_id,
        "stage": stage,
        "retryable": True,
    }


# =========================================================
# 17.docx: 模式识别 + 风格识别 + 动态 system prompt
# =========================================================
# 关键原则：
# 1. 默认 mode = chat，默认 style = concise
# 2. 不因为消息中含"学习 / 视频 / 题目"单个词就路由到 ask_tutor
# 3. 必须有明确"详细 / 展开 / 答疑 / 讲"等强信号，或 ask_tutor 完整句意才切
# 4. 任务模式（generate_video / generate_ppt / generate_exam / summarize_material /
#    create_learning_plan）必须有"给我做 / 帮我生成 / 根据…出题"等明确动作词才触发

_TASK_KEYWORDS = {
    "generate_video": [
        r"给我做.{0,4}视频",
        r"给我生成.{0,4}视频",
        r"帮我做.{0,4}视频",
        r"帮我生成.{0,4}视频",
        r"做.{0,4}学习视频",
        r"做.{0,4}教学视频",
        r"录.{0,4}视频",
        r"做个.{0,4}视频",
        r"生成.{0,4}视频",
    ],
    "generate_ppt": [
        r"给我做.{0,4}PPT",
        r"给我做.{0,4}ppt",
        r"帮我生成.{0,4}PPT",
        r"做.{0,4}PPT",
        r"生成.{0,4}PPT",
        r"做.{0,4}课件",
        r"做.{0,4}幻灯片",
    ],
    "generate_exam": [
        r"给我出.{0,4}题",
        r"给我出一套",
        r"帮我出题",
        r"根据.{0,12}出题",
        r"出.{0,4}试卷",
        r"出.{0,4}考题",
        r"做.{0,4}一套题",
        r"出题",
    ],
    "summarize_material": [
        r"帮我总结.{0,8}资料",
        r"帮我总结一下",
        r"总结.{0,8}这份.{0,4}资料",
        r"总结.{0,8}这篇",
        r"总结.{0,8}这章",
        r"归纳.{0,8}这份",
    ],
    "create_learning_plan": [
        r"给我制定.{0,4}学习计划",
        r"帮我制定.{0,4}学习计划",
        r"帮我规划.{0,4}学习",
        r"做.{0,4}学习计划",
        r"规划.{0,4}学习路径",
    ],
}

_ASK_TUTOR_KEYWORDS = [
    r"详细讲解",
    r"详细讲",
    r"详细说",
    r"详细解释",
    r"详细分析",
    r"深入讲解",
    r"深入讲",
    r"深入说",
    r"深入分析",
    r"深入解释",
    r"帮我分析",
    r"分析一下.{0,6}错题",
    r"分析.{0,4}错题",
    r"答疑",
    r"讲讲",
    r"展开讲",
    r"展开说说",
    r"展开一下",
    r"讲清楚",
    r"说清楚",
]

_CONCISE_KEYWORDS = [
    r"你一句我一句",
    r"你说一句我说一句",
    r"像聊天一样",
    r"随便聊",
    r"闲聊",
    r"随便说",
    r"简单聊",
    r"简单点",
    r"简单一点",
    r"短一点",
    r"简短点",
    r"简短一点",
    r"说得短点",
    r"说的简单点",
    r"说人话",
    r"别那么长",
    r"别太正式",
    r"说人话点",
]

_DETAILED_KEYWORDS = [
    r"详细点",
    r"详细一点",
    r"详细些",
    r"详细一点再",
    r"展开",
    r"展开讲",
    r"展开说说",
    r"展开一下",
    r"细讲",
    r"细说",
    r"完整讲",
    r"完整说说",
    r"系统讲",
    r"系统说说",
]


def _infer_mode_from_message(message: str) -> str:
    """默认 chat；只在用户明确触发任务模式时才返回对应 mode。"""
    if not message:
        return "chat"
    text = str(message).strip()
    for mode, patterns in _TASK_KEYWORDS.items():
        for pat in patterns:
            if re.search(pat, text):
                return mode
    for pat in _ASK_TUTOR_KEYWORDS:
        if re.search(pat, text):
            return "ask_tutor"
    return "chat"


def _infer_style_from_message(message: str) -> str:
    """默认 concise；只有在用户明确说"详细"才切 detailed。"""
    if not message:
        return "concise"
    text = str(message).strip()
    # concise 强信号先判定
    for pat in _CONCISE_KEYWORDS:
        if re.search(pat, text):
            return "concise"
    # detailed 强信号次之
    for pat in _DETAILED_KEYWORDS:
        if re.search(pat, text):
            return "detailed"
    return "concise"


def _build_chat_system_prompt(mode: str, style: str) -> str:
    """根据 mode + style 拼 system_prompt。"""
    base_identity = (
        "你是「柿小吉」，一个专业、克制、可执行的 AI 学习助教。"
        "默认行为是自然聊天：像朋友一样说话，少用专业术语，少列条目。"
    )
    if mode == "ask_tutor":
        if style == "detailed":
            return (
                base_identity + "用户明确请求详细讲解本道题 / 知识点。"
                "按以下结构回答：\n"
                "1) 直接回答结论（一句话）\n"
                "2) 原理解释（结合机器学习学习路径或学生当前基础）\n"
                "3) 举例说明 + 必要时给 Python 代码\n"
                "4) 常见误区\n"
                "5) 两个下一步行动\n"
                "总长度 500-800 字，允许使用 Markdown 标题、列表、代码块、公式。"
            )
        # ask_tutor 但 style 不是 detailed：也按详细走（用户都明确说答疑了）
        return (
            base_identity + "用户要求讲题 / 答疑。"
            "给出结构化讲解：先结论，再原理，再举例，最后下一步行动。"
            "总长度 300-500 字，使用 Markdown。"
        )
    if mode in {
        "generate_video",
        "generate_ppt",
        "generate_exam",
        "summarize_material",
        "create_learning_plan",
    }:
        return (
            base_identity + "用户想生成学习产物，但可能缺少必要参数。"
            "不要立刻承诺已生成或输出假成功。"
            "用 1-3 句简短问句确认主题 / 时长 / 风格等最少必要信息，"
            "再让用户确认后进入生成流程。"
        )
    # 默认：chat
    if style == "concise":
        return (
            base_identity + "用户希望简短、自然地聊天。"
            "1. 用 1-3 句话回答，禁用一级 / 二级标题；"
            "2. 不主动列一、二、三、四或要点清单；"
            "3. 不输出原理解释、总结、建议，除非用户明确要求；"
            "4. 像朋友一样回应，问一句答一句。"
        )
    if style == "normal":
        return (
            base_identity + "用户希望得到正常长度的回答。"
            "可以用简短段落（1-3 段），必要时用 1-2 个要点。"
            "总长度 80-220 字。"
        )
    # detailed（但 mode 还是 chat，说明是普通聊天但用户要详细点）
    return (
        base_identity + "用户希望稍微详细一点的回答，但仍保持聊天风格。"
        "可以用 2-4 个段落或要点，总长度 200-400 字。"
    )


async def keep_proposed_memories(
    db, *, user_id: int, thread_id: str, message_id: str, message: str, proposals
) -> list[str]:
    """Persist what the model proposed to keep, one refusal at a time.

    Returns a short note per memory actually kept (or refused), so the turn log
    shows what the ledger did and why.
    """
    notes: list[str] = []
    for proposal in list(proposals or ())[:6]:
        try:
            result = await remember_memory(
                db,
                user_id=user_id,
                thread_id=thread_id,
                message_id=message_id,
                candidate=MemoryCandidate(
                    kind=str(proposal.get("kind") or ""),
                    topic=str(proposal.get("topic") or ""),
                    content=str(proposal.get("content") or ""),
                    evidence_span=str(proposal.get("evidence_span") or ""),
                    confidence=0.7,
                ),
                current_message=message,
            )
        except Exception:
            logger.warning("memory ledger write failed for proposal", exc_info=True)
            continue
        notes.append(f"{proposal.get('kind')}:{result.reason}")
    return notes

def _board_title_of(action: Mapping[str, Any]) -> str:
    """The board's own title, if this action draws one."""
    demonstration = action.get("demonstration")
    if not isinstance(demonstration, Mapping):
        return ""
    intent = demonstration.get("current_intent")
    if not isinstance(intent, Mapping):
        return ""
    return str(intent.get("title") or "").strip()[:40]


def _progress_after_orchestration(cognition: object) -> str:
    """What the turn decided, in one line the learner can read.

    Every branch here is a decision the orchestrator has already made; saying it
    costs nothing and turns a blank wait into a sentence.
    """
    turn_mode = str(getattr(cognition, "turn_mode", "") or "")
    capability = str(getattr(cognition, "recommended_capability", "") or "")
    research = getattr(cognition, "research_task", None)
    if research is not None and bool(getattr(research, "needed", False)):
        return "这件事得先查一下，我这就去"
    labels = {
        "HANDOUT": "这轮给你整理一份能留下的资料",
        "PRACTICAL_GUIDE": "这轮给你一份照着做的步骤",
        "ASSESSMENT": "这轮出几道题看看你到哪了",
        "CODE_LAB": "这轮让你自己上手写一段",
        "PRESENTATION": "这轮给你做一份 PPT",
        "MINDMAP": "这轮给你画一张思维导图",
        "VIDEO": "这轮给你做一段教学视频",
        "STORYBOOK": "这轮给你做一本教学绘本",
        "DEMONSTRATE": "这轮我用黑板讲，边说边画",
    }
    if capability in labels:
        return labels[capability]
    if turn_mode == "CONVERSATION":
        return "这轮就聊两句，不用动那些东西"
    return "想清楚了，现在说给你"


def _progress_event(stage: str, label: str, **extra: object) -> str:
    """One SSE frame saying what the turn is doing right now.

    Measured before this existed: a blackboard turn sent nothing at all for
    7,245ms and then delivered twelve events inside five milliseconds. The whole
    wait was spent reading the learner's world model, deciding the turn and
    laying out the board -- plenty was happening, none of it visible. These
    frames are that work, said out loud.
    """
    payload = {"type": "progress", "stage": str(stage), "label": str(label)}
    payload.update(extra)
    return "data: " + json.dumps(payload, ensure_ascii=False) + chr(10) + chr(10)


def _build_workspace_system_prompt() -> str:
    """Backward-compatible entrypoint for the trusted default workspace prompt."""
    return ChatContextBuilder().build_system_prompt([], "normal")


@router.get("/api/chat/history/{user_id}")
async def get_chat_history(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    thread_id: str = None,
    db: AsyncSession = Depends(get_db),
):
    """获取对话历史"""
    require_self(user_id, current_user_id)
    query = select(ChatSession).where(ChatSession.user_id == user_id)
    query = query.where(
        or_(ChatSession.mode.is_(None), ChatSession.mode != PROFILE_ONBOARDING_MODE)
    )
    if thread_id:
        query = query.where(ChatSession.thread_id == thread_id)
    query = query.order_by(ChatSession.last_message_at.desc()).limit(20)

    result = await db.execute(query)
    sessions = result.scalars().all()
    from services.proactive_tutor import conversation_summaries

    proactive = await conversation_summaries(db, user_id=user_id)

    return {
        "success": True,
        "data": [
            {
                "id": s.id,
                "thread_id": s.thread_id,
                "messages": s.messages,
                "message_count": s.message_count,
                "response_style": s.response_style,
                "mode": s.mode,
                "created_at": str(s.created_at),
                "last_message_at": str(s.last_message_at),
                "updated_at": str(s.last_message_at),
                "unread_count": int(
                    proactive.get(s.thread_id, {}).get("unread_count") or 0
                ),
                "latest_proactive_message": proactive.get(s.thread_id, {}).get(
                    "latest_proactive_message"
                ),
                "title": next(
                    (
                        str(m.get("content", ""))[:32]
                        for m in (s.messages or [])
                        if m.get("role") == "user" and m.get("content")
                    ),
                    "新对话",
                ),
            }
            for s in sessions
        ],
    }


@router.post("/api/chat/conversations")
async def create_conversation(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Persist an empty conversation before its first provider request."""
    thread_id = f"workspace_{uuid.uuid4()}"
    session = ChatSession(
        user_id=current_user_id, thread_id=thread_id, messages=[], message_count=0
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {
        "success": True,
        "data": {
            "thread_id": session.thread_id,
            "title": "新对话",
            "mode": session.mode,
            "created_at": str(session.created_at),
            "updated_at": str(session.last_message_at),
            "message_count": 0,
        },
    }


@router.post("/api/chat")
async def chat(
    request: ChatRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """非流式 AI 对话（保存到数据库）"""
    guard = get_idempotency_guard()
    gate = await guard.begin(
        user_id=current_user_id, message_id=request.message_id, retry=request.retry
    )
    if gate.state == "cached" and gate.payload is not None:
        # 同一 message_id 的重复投递（双击 / 超时重试）不该再买一次模型调用。
        return JSONResponse(json.loads(gate.payload))
    if gate.state == "in_flight":
        raise HTTPException(status_code=409, detail="这一轮对话正在处理中，请勿重复发送")
    if gate.state == "cached":
        # 上次结果太大没有缓存正文，但确实已经完成过：拒绝重复计费。
        raise HTTPException(status_code=409, detail="这一轮对话已经处理过，请勿重复发送")
    try:
        # 强制账户隔离：忽略 request.user_id，使用 token 中的 user_id
        if request.user_id is not None and request.user_id != current_user_id:
            raise HTTPException(status_code=403, detail="无权使用其他用户身份")
        request_user_id = current_user_id

        # 资料上下文：验证 material_ids 所有权 + 拼接内容
        material_context = ""
        source_material_ids = []
        if request.material_ids:
            (
                source_material_ids,
                material_context,
            ) = await _validate_and_build_material_context(
                db, current_user_id, request.material_ids
            )

        # 1. 合并前端历史 + DB历史
        frontend_history = [
            {"role": m.role, "content": m.content} for m in request.history
        ]
        db_history = (
            await load_chat_history(db, request_user_id, request.thread_id)
            if request_user_id
            else []
        )
        # DB历史优先（更全），去重后合并
        merged_history = db_history if db_history else frontend_history

        # 2. 加载已有画像
        existing_profile = None
        if request_user_id:
            existing_profile = await load_existing_profile(db, request_user_id)

        # 3. 执行工作流
        # 关键修复：之前 final_state = event 会把每个节点的输出覆盖，
        # 只剩 summary_node 的 {final_summary, current_agent}，导致 tutor_response / document 丢失。
        # 现在用累加器把每个节点写入的字段都合并，最后一个非 None 的值生效。
        final_outputs: dict = {}  # 累加所有节点输出（顶层字段的最新值）
        last_event: dict = None  # 保留最后一个事件（用于 save_resources / 日志）
        last_node_name: str = ""
        async for event in execute_workflow(
            user_query=request.message,
            chat_history=merged_history,
            thread_id=request.thread_id,
            existing_profile=existing_profile,
            explicit_intent=request.intent,
            material_ids=request.material_ids,
            question_count=request.question_count or 5,
            material_context=material_context,
        ):
            if not event:
                continue
            last_event = event
            for node_name, node_output in event.items():
                last_node_name = node_name
                if isinstance(node_output, dict):
                    for key, value in node_output.items():
                        # 只在 value 非空时覆盖（None/空字符串保留之前的）
                        if value is None:
                            continue
                        if isinstance(value, str) and not value.strip():
                            continue
                        if isinstance(value, (list, dict)) and len(value) == 0:
                            continue
                        final_outputs[key] = value

        # 兜底 1：工作流没产生 tutor_response 时，把**确实已经有内容**的产物转成可读回答。
        # 这里只做「已有内容 → 用户可读文本」的翻译，绝不凭空声称有内容：
        # 以前只要 document 带个标题就会回「已为你准备讲解文档《X》，请查看完整内容」，
        # 而那份文档当时可能根本没有正文。
        if not final_outputs.get("tutor_response"):
            doc = final_outputs.get("document") if isinstance(final_outputs.get("document"), dict) else {}
            doc_content = str(doc.get("content") or "").strip()
            doc_title = str(doc.get("title") or "").strip()
            if doc_content:
                final_outputs["tutor_response"] = (
                    f"《{doc_title or '讲解文档'}》\n\n{doc_content}"
                )
            else:
                parts = []
                summary = (final_outputs.get("final_summary") or "").strip()
                if summary:
                    parts.append(summary)
                counts = []
                questions = final_outputs.get("questions")
                if isinstance(questions, (list, tuple)) and len(questions):
                    counts.append(f"{len(questions)} 道练习题")
                code_examples = final_outputs.get("code_examples")
                if isinstance(code_examples, (list, tuple)) and len(code_examples):
                    counts.append(f"{len(code_examples)} 个代码案例")
                learning_path = final_outputs.get("learning_path")
                if isinstance(learning_path, (list, tuple)) and len(learning_path):
                    counts.append(f"{len(learning_path)} 步学习路径")
                mindmap = final_outputs.get("mindmap")
                has_mindmap = (
                    isinstance(mindmap, dict) and bool(str(mindmap.get("content") or "").strip())
                ) or (isinstance(mindmap, str) and bool(mindmap.strip()))
                if has_mindmap:
                    counts.append("1 份思维导图")
                if counts:
                    parts.append(
                        "已为你生成：" + "、".join(counts) + "，请在下方资源列表中查看完整内容。"
                    )
                elif doc_title:
                    # 文档条目存在但没有正文：如实说明，不假装已经准备好。
                    parts.append(
                        f"《{doc_title}》这次没有生成出正文，可以重试一次，或换个说法再问我。"
                    )
                if parts:
                    final_outputs["tutor_response"] = "\n\n".join(parts)
                elif final_outputs.get("errors"):
                    final_outputs["tutor_response"] = "这次没能生成可用内容，可以重试一次。"

        # 兜底 2：summary 字段返回给前端使用 — 如果 final_summary 不够友好，tutor_response 优先作为 summary
        if not final_outputs.get("final_summary") and final_outputs.get(
            "tutor_response"
        ):
            final_outputs["final_summary"] = final_outputs["tutor_response"]

        # 兼容旧调用方：node_output 仍指向最后事件（save_resources 需要）
        node_output = (last_event or {}).get(last_node_name, {})

        # 保存画像到数据库 + Redis缓存
        if request_user_id and final_outputs.get("student_profile"):
            profile_data = final_outputs["student_profile"]
            await save_profile(db, request_user_id, profile_data)
            await CacheService.cache_profile(request_user_id, profile_data)

        # 保存学习资源到数据库
        if request_user_id:
            await save_resources(db, request_user_id, request.thread_id, final_outputs)

        # 保存对话会话
        # 关键修复：之前用 extract_assistant_summary(node_output) 只能拿到 32 字符 emoji 摘要，
        # 导致历史恢复时用户看到"📄 已生成讲解文档"而不是真实答案。
        # 现在优先用 tutor_response（真实答案），超长时截断保留头部。
        if request_user_id:
            tutor_text = (final_outputs.get("tutor_response") or "").strip()
            if tutor_text:
                if len(tutor_text) > 600:
                    summary = tutor_text[:600].rstrip() + "…"
                else:
                    summary = tutor_text
            else:
                summary = extract_assistant_summary(final_outputs)
            await save_chat_session(
                db,
                request_user_id,
                request.thread_id,
                merged_history,
                request.message,
                summary,
            )

        # 创建真实考试（create_exam=true 且有题目时）
        exam_id = None
        if request.create_exam and final_outputs.get("questions"):
            questions = final_outputs["questions"]
            exam_id = await _create_exam_from_questions(
                db, request_user_id, questions, source_material_ids
            )

        payload = {
            "success": True,
            "data": {
                "profile": final_outputs.get("student_profile"),
                "document": final_outputs.get("document"),
                "questions": final_outputs.get("questions"),
                "mindmap": final_outputs.get("mindmap"),
                "code_examples": final_outputs.get("code_examples"),
                "learning_path": final_outputs.get("learning_path"),
                "tutor_response": final_outputs.get("tutor_response"),
                "summary": final_outputs.get("final_summary"),
                "execution_log": final_outputs.get("execution_log", []),
                "errors": final_outputs.get("errors", []),
                "intent": final_outputs.get("user_intent"),
                "last_agent": last_node_name,
                "exam_id": exam_id,
                "source_material_ids": source_material_ids
                if source_material_ids
                else None,
            },
        }
        # 记录这一轮的最终结果，供同一 message_id 的重复投递重放（不再二次计费）。
        await guard.complete(
            user_id=current_user_id,
            message_id=request.message_id,
            payload=json.dumps(payload, ensure_ascii=False),
        )
        return payload

    except HTTPException:
        # 鉴权/业务异常向上抛，保持原状态码（不被外层 500 吞）
        await guard.release(user_id=current_user_id, message_id=request.message_id)
        raise
    except Exception as e:
        logger.error(f"Chat API error: {str(e)}")
        await guard.release(user_id=current_user_id, message_id=request.message_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/chat/stream")
async def chat_stream(
    request: ChatRequest, current_user_id: int = Depends(get_current_user_id)
):
    """Phase-one workspace stream: direct real-provider SSE only."""
    return await chat_quick(request, current_user_id=current_user_id)

    """Legacy multi-agent stream retained below for existing route compatibility."""
    # 17.docx 验收 fallback：langgraph 缺失 → 直接代理到 chat_quick 的流
    try:
        from graph import workflow as _wf_probe  # noqa: F401
    except Exception as _wf_err:
        logger.warning(
            f"[Stream] langgraph workflow 不可用,fallback 到 chat_quick: {_wf_err}"
        )
        # 复用 chat_quick 的事件生成器
        quick_request = ChatRequest(
            message=request.message,
            history=request.history,
            thread_id=request.thread_id,
            user_id=request.user_id,
            intent=request.intent,
            material_ids=request.material_ids,
            question_count=request.question_count,
            create_exam=request.create_exam,
            mode=request.mode,
            style=request.style,
            previous_tutor_response=request.previous_tutor_response,
            question_context=request.question_context,
        )

        async def _proxy_gen():
            async for chunk in chat_quick(
                quick_request, current_user_id=current_user_id
            ):
                # chat_quick 在我们的修复中现在返回 StreamingResponse，但当其作为 generator 调用时(参数不匹配)
                # 走 fallback：直接重新实现快速 LLM 流
                yield chunk

        # 由于 chat_quick 现在返回 StreamingResponse，直接返回其结果
        return await chat_quick(quick_request, current_user_id=current_user_id)

    async def event_generator():
        request_id = str(uuid.uuid4())
        try:
            # P0 修复：账户隔离
            if request.user_id is not None and request.user_id != current_user_id:
                yield f"data: {json.dumps({'type': 'error', 'message': '无权使用其他用户身份'}, ensure_ascii=False)}\n\n"
                return
            request_user_id = current_user_id
            message_id = request.message_id or str(uuid.uuid4())
            assistant_message_id = str(uuid.uuid4())
            server_history = []
            confirmed_memories = []
            trusted_question_context = None
            current_thread_style = None
            memory_loaded = False
            material_context = None
            source_material_ids = []
            source_citations = []
            resolved_refs = ResolvedWorkspaceReferences()
            if db_conn._async_session_factory:
                async with db_conn._async_session_factory() as db:
                    current_thread_style = await get_thread_style(
                        db, request_user_id, request.thread_id
                    )
                    confirmed_memories = await list_confirmed_memories(
                        db, request_user_id
                    )
                    memory_loaded = True
                    confirmed_style = next(
                        (
                            item.value
                            for item in confirmed_memories
                            if item.key == "response_style"
                        ),
                        None,
                    )
                    style_decision = resolve_style(
                        request.message, current_thread_style, confirmed_style
                    )
                    if request.style in {"concise", "normal", "detailed"}:
                        style_decision = type(style_decision)(
                            request.style, style_decision.next_thread_style
                        )
                    await set_thread_style(
                        db,
                        request_user_id,
                        request.thread_id,
                        style_decision.next_thread_style,
                    )
                    try:
                        resolved_refs = await resolve_workspace_references(
                            db,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                            attachment_refs=request.attachment_refs,
                            reply_to_message_id=request.reply_to_message_id,
                            target_artifact_id=request.target_artifact_id,
                            target_version_id=request.target_version_id,
                        )
                    except ReferenceAccessError as exc:
                        yield f"data: {json.dumps({'type': 'error', 'code': 'REFERENCE_NOT_AVAILABLE', 'message': str(exc), 'retryable': False}, ensure_ascii=False)}\n\n"
                        return
                    if not request.retry:
                        await append_chat_message(
                            db,
                            request_user_id,
                            request.thread_id,
                            "user",
                            request.message,
                            message_id,
                            metadata={
                                "attachment_refs": resolved_refs.attachment_refs,
                                "reply_to_message_id": request.reply_to_message_id,
                                "target_artifact_id": request.target_artifact_id,
                                "target_version_id": request.target_version_id,
                            },
                        )
                    server_history = await load_chat_history(
                        db, request_user_id, request.thread_id
                    )
            else:
                style_decision = resolve_style(request.message, None, None)

            # 1. 准备上下文：合并前端历史 + DB历史
            frontend_history = [
                {"role": m.role, "content": m.content} for m in request.history
            ]

            db_history = []
            existing_profile = None
            if db_conn._async_session_factory and request_user_id:
                async with db_conn._async_session_factory() as db:
                    # 加载数据库历史
                    db_history = await load_chat_history(
                        db, request_user_id, request.thread_id
                    )
                    # 加载已有画像（Redis缓存 → DB）
                    existing_profile = await load_existing_profile(db, request_user_id)

            # DB历史优先（更完整），否则用前端历史
            merged_history = db_history if db_history else frontend_history

            logger.info(
                f"[Stream] user_id={request_user_id} thread={request.thread_id} "
                f"history_len={len(merged_history)} has_profile={existing_profile is not None}"
            )

            # 2. 执行工作流（注入已有画像 → 优化而非重建）
            if db_conn._async_session_factory and request_user_id:
                async with db_conn._async_session_factory() as db:
                    last_state = {}
                    async for event in execute_workflow(
                        user_query=request.message,
                        chat_history=merged_history,
                        thread_id=request.thread_id,
                        existing_profile=existing_profile,
                    ):
                        if not event:
                            continue
                        node_name = list(event.keys())[0]
                        node_output = event.get(node_name, {})
                        last_state = node_output

                        yield f"data: {json.dumps({'type': 'agent_start', 'agent': node_name}, ensure_ascii=False)}\n\n"

                        # 流式推送内容片段
                        if isinstance(node_output, dict) and node_output:
                            # 17.docx 验收：把 summary_node 的 tutor_response / final_summary 作为 tutor_response agent_content 推给前端
                            # 兼容旧 workflow 节点名：tutor_response / final_summary / summary 多种 key
                            tutor_text = (
                                node_output.get("tutor_response")
                                or node_output.get("final_summary")
                                or node_output.get("summary")
                                or ""
                            )
                            if tutor_text:
                                yield (
                                    "data: "
                                    + json.dumps(
                                        {
                                            "type": "agent_content",
                                            "agent": node_name,
                                            "resource_type": "tutor_response",
                                            "preview": str(tutor_text)[:2000],
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n\n"
                                )

                            if node_output.get("student_profile"):
                                yield f"data: {json.dumps({'type': 'agent_content', 'agent': node_name, 'resource_type': 'profile', 'preview': json.dumps(node_output['student_profile'], ensure_ascii=False)[:300]}, ensure_ascii=False)}\n\n"

                            for key in [
                                "document",
                                "questions",
                                "mindmap",
                                "code_examples",
                            ]:
                                val = node_output.get(key)
                                if val:
                                    yield f"data: {json.dumps({'type': 'agent_content', 'agent': node_name, 'resource_type': key, 'preview': str(val)[:200]}, ensure_ascii=False)}\n\n"

                            if node_output.get("video_result"):
                                v = node_output["video_result"]
                                dur = v.get("duration", 0)
                                res = v.get("resolution", "720p")
                                vu = v.get("url", "")
                                yield (
                                    "data: "
                                    + json.dumps(
                                        {
                                            "type": "agent_content",
                                            "agent": node_name,
                                            "resource_type": "video",
                                            "preview": "教学视频已生成 ({}s, {})".format(
                                                dur, res
                                            ),
                                            "video_url": vu,
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n\n"
                                )

                        yield f"data: {json.dumps({'type': 'agent_complete', 'agent': node_name, 'status': 'success'}, ensure_ascii=False)}\n\n"

                        await asyncio.sleep(0.1)

                    # 3. 持久化：画像 → DB + Redis，对话 → DB
                    if last_state.get("student_profile"):
                        await save_profile(
                            db, request_user_id, last_state["student_profile"]
                        )
                        await CacheService.cache_profile(
                            request_user_id, last_state["student_profile"]
                        )
                        logger.info(
                            f"[Stream] 画像已保存+缓存 user_id={request_user_id}"
                        )

                    summary = extract_assistant_summary(last_state)
                    await save_chat_session(
                        db,
                        request_user_id,
                        request.thread_id,
                        merged_history,
                        request.message,
                        summary,
                    )
                    logger.info(
                        f"[Stream] 会话已保存 user_id={request_user_id} thread={request.thread_id}"
                    )

            else:
                # 无 user_id，只运行工作流不存数据库
                async for event in execute_workflow(
                    user_query=request.message,
                    chat_history=merged_history,
                    thread_id=request.thread_id,
                    existing_profile=existing_profile,
                ):
                    if not event:
                        continue
                    node_name = list(event.keys())[0]

                    yield f"data: {json.dumps({'type': 'agent_start', 'agent': node_name}, ensure_ascii=False)}\n\n"

                    yield f"data: {json.dumps({'type': 'agent_complete', 'agent': node_name, 'status': 'success'}, ensure_ascii=False)}\n\n"

                    await asyncio.sleep(0.1)

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            logger.error(f"Stream error: {str(e)}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.post("/api/chat/quick")
async def chat_quick(
    request: ChatRequest, current_user_id: int = Depends(get_current_user_id)
):
    """演示专走 · 30 秒内流式 AI 答疑（单 tutor_agent，演示救场用）

    17.docx: 默认走自然聊天 chat + concise 模式；
    用户在消息里明确说"详细讲解 / 答疑"才会切到 ask_tutor + detailed。
    """
    guard = get_idempotency_guard()
    gate = await guard.begin(
        user_id=current_user_id, message_id=request.message_id, retry=request.retry
    )
    if gate.state == "cached":
        if gate.payload is not None:
            # 同一 message_id 的重复投递：重放上次的 SSE，字节级一致，不再计费。
            async def _replay():
                yield gate.payload
            return StreamingResponse(_replay(), media_type="text/event-stream")
        raise HTTPException(status_code=409, detail="这一轮对话已经处理过，请勿重复发送")
    if gate.state == "in_flight":
        raise HTTPException(status_code=409, detail="这一轮对话正在处理中，请勿重复发送")

    def preview_to_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in ("content", "text", "answer", "message"):
                if value.get(key):
                    return str(value[key])
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    async def event_generator():
        request_id = str(uuid.uuid4())
        try:
            # P0 修复：账户隔离
            if request.user_id is not None and request.user_id != current_user_id:
                yield f"data: {json.dumps({'type': 'error', 'message': '无权使用其他用户身份'}, ensure_ascii=False)}\n\n"
                return
            request_user_id = current_user_id
            yield _progress_event('reading', '正在读你这句话')
            is_tide = request.experience == "tide_v1"
            darya_immediate_safety = (
                child_violence_safety_response(request.message) if is_tide else None
            )
            explicit_response_constraint = (
                detect_explicit_response_constraint(request.message)
                if is_tide
                else None
            )
            tide_decision = None
            tide_perspective = None
            tide_perspective_values = {}
            tide_perspective_payload = None
            tide_workspace_object = None
            tide_workspace_object_values = {}
            tide_workspace_object_payload = None
            tide_artifact_action = None
            tide_artifact_action_values = {}
            tide_artifact_action_payload = None
            relevant_user_context = []
            relevant_user_context_payload = []
            relevant_long_term_memories = []
            relevant_long_term_memory_payload = []
            account_long_term_memory_payload = []
            account_impression_payload = []
            continuity_brief_payload = None
            saved_long_term_memory = None
            darya_previous_perspective = None
            darya_current_perspective_state = None
            darya_current_perspective_payload = None
            darya_action_execution = None
            darya_learning_loop_result = None
            darya_domain_disambiguation = None
            darya_search_plan = None
            darya_search_grounding = None
            darya_goal_collaboration = None
            darya_cognition_frame = None
            darya_cognition_v2 = None
            darya_cognition_evaluation = None
            darya_model_support_packet = None
            non_tide_evidence_bundle = None
            non_tide_search_grounding = None
            conversation_capability_action = None
            conversation_clarification = None
            message_id = request.message_id or str(uuid.uuid4())
            assistant_message_id = str(uuid.uuid4())
            server_history = []
            confirmed_memories = []
            relevant_impressions = []
            saved_memory_action = None
            existing_profile = None
            begin_runtime_operation(
                request_user_id, request.thread_id, "chat_connecting"
            )
            trusted_question_context = None
            current_thread_style = None
            memory_loaded = False
            material_context = None
            source_material_ids = []
            source_citations = []
            resolved_refs = ResolvedWorkspaceReferences()
            tutor_directive = ""
            universal_directive = ""
            existing_profile = None
            normalized_learner_state = None
            if db_conn._async_session_factory:
                async with db_conn._async_session_factory() as db:
                    if not is_tide:
                        existing_profile = await load_existing_profile(
                            db, request_user_id
                        )
                        try:
                            normalized_learner_state = (
                                await get_normalized_learner_state(db, request_user_id)
                            ).model_dump(mode="json")
                        except Exception as exc:
                            logger.warning(
                                "normalized learner state unavailable for chat user=%s: %s",
                                request_user_id,
                                type(exc).__name__,
                            )
                    current_thread_style = await get_thread_style(
                        db, request_user_id, request.thread_id
                    )
                    if not is_tide:
                        confirmed_memories = await list_confirmed_memories(
                            db, request_user_id
                        )
                        memory_loaded = True
                    confirmed_style = next(
                        (
                            item.value
                            for item in confirmed_memories
                            if item.key == "response_style"
                        ),
                        None,
                    )
                    style_decision = resolve_style(
                        request.message, current_thread_style, confirmed_style
                    )
                    if request.style in {"concise", "normal", "detailed"}:
                        style_decision = type(style_decision)(
                            request.style, request.style, "current_thread"
                        )
                    await set_thread_style(
                        db,
                        request_user_id,
                        request.thread_id,
                        style_decision.next_thread_style,
                    )
                    if is_tide:
                        darya_previous_perspective = (
                            await get_darya_current_perspective(
                                db,
                                user_id=request_user_id,
                                thread_id=request.thread_id,
                            )
                        )
                        tide_perspective = await get_tide_perspective(
                            db,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                        )
                        tide_workspace_object = await get_current_workspace_object(
                            db,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                        )
                        tide_artifact_action = await get_latest_artifact_action(
                            db,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                        )
                        relevant_user_context = await select_relevant_confirmed_context(
                            db,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                            message=request.message,
                        )
                        relevant_user_context_payload = [
                            public_user_context_claim(item)
                            for item in relevant_user_context
                        ]
                        relevant_long_term_memories = await select_relevant_memories(
                            db,
                            user_id=request_user_id,
                            message=request.message,
                            mark_used=False,
                        )
                        relevant_long_term_memory_payload = [
                            public_long_term_memory(item)
                            for item in relevant_long_term_memories
                        ]
                        # Candidates are not facts used in this turn. Do not feed
                        # every account memory to the task planner.
                        account_long_term_memory_payload = relevant_long_term_memory_payload
                        try:
                            from services.darya_librarian import build_continuity_brief
                            continuity_brief_payload = await build_continuity_brief(
                                db,
                                user_id=request_user_id,
                                thread_id=request.thread_id,
                                message=request.message,
                            )
                        except Exception as brief_exc:
                            logger.warning(
                                "DARYA continuity brief unavailable user=%s thread=%s: %s",
                                request_user_id,
                                request.thread_id,
                                type(brief_exc).__name__,
                            )
                        account_impressions = await list_user_impressions(
                            db,
                            request_user_id,
                        )
                        account_impression_payload = [
                            {
                                "id": item.id,
                                "impression_key": item.impression_key,
                                "dimension": item.dimension,
                                "summary": item.summary,
                                "confidence": item.confidence,
                                "scope": item.scope,
                                "status": item.status,
                                "confirmed_by_user": bool(item.confirmed_by_user),
                                "source_type": item.source_type,
                            }
                            for item in account_impressions
                            if item.status not in {"rejected", "uncertain"}
                        ][:24]
                    # Workspace Object is the living current-work reference and is
                    # advanced after a confirmed Artifact Action. Shared Perspective
                    # can legitimately lag behind that immutable version transition.
                    fallback_artifact_id, fallback_version_id = (
                        workspace_object_artifact_target(
                            tide_workspace_object,
                        )
                    )
                    if fallback_artifact_id is None:
                        fallback_artifact_id, fallback_version_id = (
                            current_artifact_target(tide_perspective)
                        )
                    target_artifact_id = (
                        request.target_artifact_id
                        if request.target_artifact_id is not None
                        else fallback_artifact_id
                    )
                    target_version_id = (
                        request.target_version_id
                        if request.target_artifact_id is not None
                        else fallback_version_id
                    )
                    try:
                        resolved_refs = await resolve_workspace_references(
                            db,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                            attachment_refs=request.attachment_refs,
                            reply_to_message_id=request.reply_to_message_id,
                            target_artifact_id=target_artifact_id,
                            target_version_id=target_version_id,
                        )
                    except ReferenceAccessError as exc:
                        yield f"data: {json.dumps({'type': 'error', 'code': 'REFERENCE_NOT_AVAILABLE', 'message': str(exc), 'retryable': False}, ensure_ascii=False)}\n\n"
                        return
                    if not request.retry:
                        await append_chat_message(
                            db,
                            request_user_id,
                            request.thread_id,
                            "user",
                            request.message,
                            message_id,
                            metadata={
                                "attachment_refs": resolved_refs.attachment_refs,
                                "reply_to_message_id": request.reply_to_message_id,
                                "target_artifact_id": request.target_artifact_id,
                                "target_version_id": request.target_version_id,
                                "interaction_control": (
                                    explicit_response_constraint.public_context()
                                    if explicit_response_constraint
                                    else None
                                ),
                            },
                        )
                        if (
                            explicit_response_constraint is not None
                            and explicit_response_constraint.suppress_proactive
                        ):
                            await defer_tide_opportunities_for_control(
                                db,
                                user_id=request_user_id,
                                conversation_id=request.thread_id,
                            )
                    server_history = await load_chat_history(
                        db, request_user_id, request.thread_id
                    )
                    if is_tide:
                        darya_model_support_packet = build_model_support_packet(
                            continuity_brief=continuity_brief_payload,
                            recent_history=server_history,
                        )
                    if (
                        is_tide
                        and not request.retry
                        and not (
                            explicit_response_constraint is not None
                            and explicit_response_constraint.suppress_proactive
                        )
                    ):
                        # 编排器是整个回合里最长的一次调用。它现在是流式的，所以
                        # 等待期间可以每隔一会儿说一句"还在写"—— 不是转圈动画，是
                        # 真的有字在回来（见 _stream_planner 的 on_token）。
                        _planner_pulse = {"chars": 0}

                        def _count_planner_token(chars: int, _pulse=_planner_pulse) -> None:
                            _pulse["chars"] = chars

                        _planner_task = asyncio.create_task(
                            plan_cognition_orchestration_v2(
                                request.message,
                                recent_history=server_history,
                                user_context=relevant_user_context_payload,
                                memories=account_long_term_memory_payload,
                                impressions=account_impression_payload,
                                continuity_brief=continuity_brief_payload,
                                on_token=_count_planner_token,
                            )
                        )
                        while not _planner_task.done():
                            await asyncio.sleep(0.75)
                            if _planner_task.done():
                                break
                            if _planner_pulse["chars"]:
                                yield _progress_event('thinking', '在想这件事')
                        darya_cognition_v2 = await _planner_task
                        # Explicit learner controls own the stage lifecycle. The
                        # model still chooses the teaching content, but a valid
                        # "end" request cannot remain a paused stage merely
                        # because its free-form classification said PAUSED.
                        explicit_stage_action = stage_action_from_message(request.message)
                        if darya_cognition_v2 is not None and darya_cognition_v2.source == "structured_model":
                            if explicit_stage_action == "end":
                                darya_cognition_v2 = replace(
                                    darya_cognition_v2,
                                    turn_mode="STOPPED",
                                    goal_lifecycle="COMPLETED",
                                    goal_completion_reason="用户明确结束本段教学；不代表已经掌握。",
                                )
                            elif explicit_stage_action == "pause":
                                darya_cognition_v2 = replace(
                                    darya_cognition_v2,
                                    turn_mode="PAUSED",
                                    goal_lifecycle="PAUSED",
                                    goal_completion_reason="",
                                )
                            elif explicit_stage_action == "resume":
                                darya_cognition_v2 = replace(
                                    darya_cognition_v2,
                                    turn_mode="LEARNING",
                                    goal_lifecycle="ACTIVE",
                                    goal_completion_reason="",
                                )
                        if darya_cognition_v2 is not None:
                            yield _progress_event('decided', _progress_after_orchestration(darya_cognition_v2))
                        if darya_cognition_v2.source == "structured_model" and not is_memory_inspection(request.message):
                            from services.artifact_generation_context import selected_profile_values
                            selected_facts = selected_profile_values(darya_cognition_v2.public_dict())
                            relevant_long_term_memories = [
                                item for item in relevant_long_term_memories
                                if str(item.content or "").strip() in selected_facts
                            ]
                            relevant_long_term_memory_payload = [
                                public_long_term_memory(item) for item in relevant_long_term_memories
                            ]
                        observed_memories = await persist_observed_world_model_claims(
                            db,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                            source_message_id=message_id,
                            current_message=request.message,
                            claims=[
                                claim.__dict__
                                for claim in darya_cognition_v2.world_model.claims
                                if claim.source_type == "CURRENT_MESSAGE"
                            ],
                        )
                        if observed_memories:
                            await db.commit()
                        # What the model judged worth carrying forward, checked
                        # one by one by the ledger: quote must be the learner's
                        # own words, nothing sensitive or identifying.
                        kept_memories = await keep_proposed_memories(
                            db,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                            message_id=message_id,
                            message=request.message,
                            proposals=darya_cognition_v2.memories_to_keep,
                        )
                        if kept_memories:
                            await db.commit()
                            logger.info(
                                "[ledger] user=%s kept=%s", request_user_id, kept_memories
                            )
                        goal_payload = darya_cognition_v2.goal_plan_payload()
                        for tuple_key in (
                            "observed_user_facts", "constraints", "missing_information",
                            "avoid_actions", "support_options", "decision_criteria",
                            "assistant_commitments",
                        ):
                            goal_payload[tuple_key] = tuple(goal_payload.get(tuple_key) or ())
                        darya_goal_collaboration = GoalCollaborationPlan(**goal_payload)
                        if darya_immediate_safety is not None:
                            darya_cognition_v2 = None
                            darya_goal_collaboration = GoalCollaborationPlan(
                                active=True,
                                goal="先确保人身安全，再逐步提高语文成绩",
                                observed_user_facts=({
                                    "fact": "用户明确表示因成绩被家长打且害怕",
                                    "evidence_span": "妈妈因为这个打了我",
                                },),
                                constraints=("安全确认优先于学习规划",),
                                missing_information=("当前是否安全、是否受伤",),
                                clarification_question="你现在安全吗，身边有没有一位你信任的大人？",
                                external_research_needed=False,
                                recommended_capability="NONE",
                                useful_first_move=(
                                    "先去有其他可靠大人的地方，向班主任、信任的老师、"
                                    "亲戚或邻居说明自己被打和害怕。"
                                ),
                                priority="SAFETY",
                                risk_level="URGENT",
                                protected_need="人身安全与获得可靠成年人支持",
                                avoid_actions=("不建议孩子单独与可能继续施加伤害的人争论或只向其求助",),
                                support_options=("班主任或信任的老师", "可靠亲戚或邻居", "110 或 12355"),
                                confidence=1.0,
                                source="deterministic_child_safety",
                            )
                    if not request.retry:
                        cognition_semantic = (
                            proactive_semantic_route(darya_cognition_v2)
                            if darya_cognition_v2 is not None
                            else None
                        )
                        v2_capability_recommendation = (
                            "NONE" if darya_cognition_v2 is not None else None
                        )
                        if darya_cognition_v2 is not None and darya_cognition_v2.source == "structured_model":
                            recommended = str(darya_cognition_v2.recommended_capability or "")
                            # The model's public work brief now owns semantic
                            # tool selection.  Execution still passes through
                            # the existing capability authority and receipt
                            # checks; do not require a second rigid action-graph
                            # parser before an explicit real artifact can run.
                            v2_capability_recommendation = recommended or "NONE"
                        # Look things up *before* producing anything, so a board or
                        # any other inline production can cite what was just
                        # fetched instead of being drawn first and grounded later.
                        early_search = None
                        if is_tide and darya_cognition_v2 is not None:
                            try:
                                # 和下面 search_context 用同一套检索式：编排器写在
                                # research_task.questions[].query 里的查询词优先。
                                # 这条早检索是模型研究任务触发的主路径，用另一个规划器的
                                # research_query（它没给就退回正则改写用户原话）会让模型
                                # 自己写好的检索式在最早、也最要紧的一次检索里被丢掉。
                                early_model_queries = [
                                    str(item.query or "").strip()
                                    for item in getattr(
                                        getattr(darya_cognition_v2, "research_task", None),
                                        "questions",
                                        (),
                                    ) or ()
                                    if str(item.query or "").strip()
                                ]
                                early_search_context = SearchContext(
                                    message=request.message,
                                    query_override=(
                                        early_model_queries[0]
                                        if early_model_queries
                                        else (
                                            darya_goal_collaboration.research_query
                                            if darya_goal_collaboration is not None
                                            and darya_goal_collaboration.external_research_needed
                                            else ""
                                        )
                                    ),
                                    planned_queries=early_model_queries[1:],
                                    shared_perspective=tide_perspective_values,
                                    workspace_object=tide_workspace_object_values,
                                    artifact_context=trusted_question_context or {},
                                )
                                early_domain_resolution = resolve_learning_domain(
                                    message=request.message
                                )
                                early_learning_state = None
                                if db_conn._async_session_factory:
                                    try:
                                        async with db_conn._async_session_factory() as probe_db:
                                            early_learning_state = await get_learning_route_state(
                                                probe_db,
                                                user_id=request_user_id,
                                                thread_id=request.thread_id,
                                            )
                                    except Exception as probe_exc:
                                        logger.warning(
                                            "early learning probe unavailable: %s",
                                            type(probe_exc).__name__,
                                        )
                                if (
                                    bool(getattr(darya_cognition_v2.research_task, 'needed', False))
                                    and darya_immediate_safety is None
                                    and not (
                                        explicit_response_constraint is not None
                                        and explicit_response_constraint.suppress_proactive
                                    )
                                ):
                                    yield f"data: {json.dumps({'type': 'search_status', 'status': 'checking', 'message': '正在核对资料'}, ensure_ascii=False)}\n\n"

                                early_search = await resolve_turn_search(
                                    message=request.message,
                                    cognition=darya_cognition_v2,
                                    domain_resolution=early_domain_resolution,
                                    learning_state=early_learning_state,
                                    search_context=early_search_context,
                                    immediate_safety=darya_immediate_safety,
                                    disambiguation=darya_domain_disambiguation,
                                    suppressed=bool(
                                        explicit_response_constraint is not None
                                        and explicit_response_constraint.suppress_proactive
                                    ),
                                )
                                logger.info(
                                    "DARYA early search user=%s thread=%s v2=%s %s",
                                    request_user_id,
                                    request.thread_id,
                                    darya_cognition_v2.source,
                                    early_search.describe(),
                                )
                            except Exception as early_exc:
                                logger.warning(
                                    "early search unavailable user=%s thread=%s: %s",
                                    request_user_id,
                                    request.thread_id,
                                    type(early_exc).__name__,
                                )
                                early_search = None

                        # Laying the board out is the second long silence of a
                        # turn. Same treatment as the planner: run it as a task
                        # and let the model's own output keep the line alive.
                        _board_pulse = {"chars": 0}

                        def _count_board_token(chars: int, _pulse=_board_pulse) -> None:
                            _pulse["chars"] += chars

                        _capability_task = asyncio.create_task(plan_conversation_capability(
                            db,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                            message=request.message,
                            message_id=message_id,
                            assistant_message_id=assistant_message_id,
                            semantic=cognition_semantic,
                            recent_history=server_history,
                            v2_recommended=v2_capability_recommendation,
                            # Handed in rather than attached afterwards: a
                            # capability that generates inline (the board
                            # does) otherwise runs before the brief exists
                            # and never sees it.
                            work_brief=(
                                darya_cognition_v2.public_dict()["work_brief"]
                                if darya_cognition_v2 is not None
                                else None
                            ),
                            profile=existing_profile,
                            memories=confirmed_memories,
                            impressions=relevant_impressions,
                            # Fresh facts, fetched before this call rather
                            # than after it, so a board can cite them.
                            evidence=(
                                early_search.evidence_dict()
                                if early_search is not None
                                else None
                            ),
                            on_token=_count_board_token,
                        ))
                        # 这个任务拿着请求作用域的 db（上面那个 async with 开的会话），
                        # 而等待期间我们还要向客户端 yield。客户端一断，生成器在这里被关闭，
                        # 会话随即释放——所以任务绝不能活得比这个生成器久。见
                        # _task_must_not_outlive_this_block 里的因果说明。
                        async with _task_must_not_outlive_this_block(_capability_task) as _capability:
                            while not _capability.done():
                                await asyncio.sleep(0.75)
                                if _capability.done():
                                    break
                                if _board_pulse["chars"]:
                                    yield _progress_event('drawing', '正在排版板书')
                            conversation_capability_action = await _capability
                        if (
                            conversation_capability_action is not None
                            and darya_cognition_v2 is not None
                        ):
                            request_payload = conversation_capability_action.setdefault(
                                "request_payload", {}
                            )
                            request_payload["work_brief"] = (
                                darya_cognition_v2.public_dict()["work_brief"]
                            )
                            brief = request_payload["work_brief"]
                            if request_payload.get("goal_assessment"):
                                brief["recent_context"] = [
                                    str(row.get("content") or "")[:1200]
                                    for row in server_history[-12:] if isinstance(row, dict)
                                ]
                            deliverables = list(brief.get("deliverables") or [])
                            request_payload["topic"] = (
                                str(deliverables[0])[:80]
                                if deliverables
                                else str(brief.get("goal") or request.message)[:80]
                            )
                            if conversation_capability_action.get("kind") == "PRESENTATION":
                                request_payload["learning_goal"] = str(
                                    brief.get("goal") or request.message
                                )[:160]
                            learner_type_note = ""
                            try:
                                _learner_state = await get_normalized_learner_state(db, request_user_id)
                                _weights = {}
                                for _ev in (_learner_state.preference_evidence or []):
                                    if getattr(_ev, "polarity", "supporting") != "supporting":
                                        continue
                                    _pref = str(getattr(_ev, "preference", "") or "")
                                    _conf = float(getattr(_ev, "confidence", 0) or 0)
                                    if _pref and _conf > 0:
                                        _weights[_pref] = max(_weights.get(_pref, 0.0), _conf)
                                _top = sorted(_weights.items(), key=lambda item: item[1], reverse=True)[:3]
                                if _top:
                                    learner_type_note = (
                                        "学习者类型权重（按证据置信度）："
                                        + "、".join(f"{_LEARNER_TYPE_LABELS.get(p, p)} {c:.2f}" for p, c in _top)
                                        + "。生成时按权重调整讲解、示例与练习的配比；与本轮用户明确要求冲突时以用户要求为准。"
                                    )
                            except Exception:
                                logger.debug("learner type weights unavailable user=%s", request_user_id)
                            brief_instruction = (
                                learner_type_note
                                + "统一工作简报："
                                + json.dumps(brief, ensure_ascii=False)
                                + "。所有内容必须服务该简报，不得重新猜测用户身份。"
                            )
                            request_payload["instruction"] = (
                                brief_instruction
                                + "先完成简报中的主要认知劳动并交付可直接使用的成果；"
                                + "不得把搜索、整理、比较或形成首稿全部交回用户。"
                                + "缺少私人数据时交付明确标注的第一版（可修改），私人数据只用于校正下一版。"
                                + "第一版必须有已经写完的具体正文、脚本、比较或练习；不得用多个待填字段、空白表格或通用模板冒充成果。"
                                + "缺少主题等私人选择时，选择一个低风险可替换假设并明确标注，只在最后问一个最影响下一版的问题。"
                                + "不得猜测用户可投入时间，不得为了格式写第一天/第二天模板或任意工时。"
                                + "不得编造政策、市场、平台、数量、分数、用户能力或效果数据；"
                                + "没有真实来源时保持未核实，没有真实回执时不得声称完成。"
                            )
                    if is_tide:
                        try:
                            # Foreground model understanding owns semantic goal
                            # updates.  Do not run the former regex/keyword
                            # candidate builder in parallel and then overwrite
                            # the model's judgment.
                            tide_perspective = await get_tide_perspective(
                                db,
                                user_id=request_user_id,
                                thread_id=request.thread_id,
                            )
                            if darya_goal_collaboration is not None:
                                tide_perspective = await persist_goal_collaboration_update(
                                    db,
                                    user_id=request_user_id,
                                    thread_id=request.thread_id,
                                    plan=darya_goal_collaboration.public_dict(),
                                    message_id=message_id,
                                )
                            tide_workspace_object = (
                                await sync_workspace_object_from_perspective(
                                    db,
                                    user_id=request_user_id,
                                    thread_id=request.thread_id,
                                    perspective=tide_perspective,
                                )
                            )
                            await db.commit()
                            if tide_perspective is not None:
                                await db.refresh(tide_perspective)
                            if tide_workspace_object is not None:
                                await db.refresh(tide_workspace_object)
                            tide_perspective_values = tide_perspective_context(
                                tide_perspective
                            )
                            tide_workspace_object_values = workspace_object_context(
                                tide_workspace_object
                            )
                            tide_workspace_object_payload = public_workspace_object(
                                tide_workspace_object
                            )
                            tide_perspective_payload = public_tide_perspective(
                                tide_perspective,
                                thread_id=request.thread_id,
                                workspace_object_id=(
                                    tide_workspace_object.id
                                    if tide_workspace_object
                                    else None
                                ),
                            )
                        except Exception as perspective_exc:
                            await db.rollback()
                            logger.warning(
                                "Tide Shared Perspective update unavailable user=%s thread=%s: %s",
                                request_user_id,
                                request.thread_id,
                                type(perspective_exc).__name__,
                            )
                        if (
                            tide_workspace_object is not None
                            and is_artifact_improvement_request(request.message)
                        ):
                            try:
                                tide_artifact_action = (
                                    await create_artifact_action_proposal(
                                        db,
                                        user_id=request_user_id,
                                        thread_id=request.thread_id,
                                        instruction=request.message,
                                        request_key=message_id,
                                    )
                                )
                                await db.commit()
                                if tide_artifact_action is not None:
                                    await db.refresh(tide_artifact_action)
                            except ArtifactActionError as action_exc:
                                await db.rollback()
                                logger.info(
                                    "Tide Artifact Action not created user=%s thread=%s code=%s",
                                    request_user_id,
                                    request.thread_id,
                                    action_exc.code,
                                )
                            except Exception as action_exc:
                                await db.rollback()
                                logger.warning(
                                    "Tide Artifact Action unavailable user=%s thread=%s: %s",
                                    request_user_id,
                                    request.thread_id,
                                    type(action_exc).__name__,
                                )
                        tide_artifact_action_values = artifact_action_context(
                            tide_artifact_action
                        )
                        tide_artifact_action_payload = public_artifact_action(
                            tide_artifact_action
                        )
                    tutor_session = (
                        await db.execute(
                            select(TutorSession).where(
                                TutorSession.user_id == request_user_id,
                                TutorSession.thread_id == request.thread_id,
                                TutorSession.status == "active",
                            )
                        )
                    ).scalar_one_or_none()
                    if tutor_session is not None and not is_tide:
                        tutor_session = await ensure_tutor_plan_alignment(
                            db,
                            catalog=DEFAULT_GRAPH_CATALOG,
                            session=tutor_session,
                        )
                        early_source_policy = (
                            request.source_context.policy
                            if request.source_context
                            else "auto"
                        )
                        tutor_material_ids = resolved_refs.material_ids or (
                            request.source_context.material_ids
                            if request.source_context
                            else []
                        )
                        try:
                            tutor_context = await resolve_tutor_context(
                                db,
                                user_id=request_user_id,
                                thread_id=request.thread_id,
                                session=tutor_session,
                                active_artifact_id=(
                                    resolved_refs.artifact_context or {}
                                ).get("artifact_id"),
                                requested_material_ids=tutor_material_ids,
                                use_material_context=early_source_policy != "exclude",
                            )
                        except PermissionError as exc:
                            yield f"data: {json.dumps({'type': 'error', 'code': 'TUTOR_CONTEXT_NOT_AVAILABLE', 'message': str(exc), 'retryable': False}, ensure_ascii=False)}\n\n"
                            return
                        tutor_decision = decide_tutor_action(
                            tutor_context, request.message
                        )
                        tutor_session.next_action = tutor_decision.public_payload()
                        await db.commit()
                        await db.refresh(tutor_session)
                        voice_session_summary = await recent_voice_session_summary(
                            db,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                        )
                        tutor_directive = build_tutor_chat_directive(
                            tutor_session,
                            request.message,
                            context=tutor_context,
                            decision=tutor_decision,
                            voice_session_summary=voice_session_summary,
                        )
                        server_history = await load_chat_history(
                            db, request_user_id, request.thread_id
                        )
                    # Universal verify+method path: pack-independent directive
                    # only when no TutorSession owns this thread AND we're not on
                    # the Tide surface. On the tutor path the orchestrator has
                    # already injected its own directive via build_tutor_chat_directive.
                    if tutor_session is None and not is_tide and universal_directive == "":
                        try:
                            universal_directive = await build_universal_directive_from_message(
                                user_message=request.message,
                                history=server_history or [],
                                profile=existing_profile,
                                learner_state=normalized_learner_state,
                            )
                        except Exception as universal_exc:  # noqa: BLE001
                            logger.warning(
                                "Universal directive build failed user=%s thread=%s: %s",
                                request_user_id,
                                request.thread_id,
                                type(universal_exc).__name__,
                            )
                            universal_directive = ""
                    canonical_learning_candidate = (
                        build_explicit_candidate(request.message)
                        if not request.retry
                        else None
                    )
                    writes_canonical_learning_preference = bool(
                        canonical_learning_candidate
                        and canonical_learning_candidate.get("topic")
                        in LEARNING_PREFERENCE_TOPICS
                    )
                    if (
                        not request.retry
                        and not is_tide
                        and not writes_canonical_learning_preference
                    ):
                        recent_before_action = (
                            server_history[:-1] if server_history else []
                        )
                        saved_memory_action = await persist_memory_action(
                            db=db,
                            user_id=request_user_id,
                            message=request.message,
                            recent_messages=recent_before_action,
                            thread_id=request.thread_id,
                            source_message_id=message_id,
                        )
                        if saved_memory_action:
                            confirmed_memories = await list_confirmed_memories(
                                db, request_user_id
                            )
                    if not request.retry and (
                        is_tide or writes_canonical_learning_preference
                    ):
                        saved_long_term_memory = await persist_explicit_memory(
                            db,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                            source_message_id=message_id,
                            message=request.message,
                        )
                        if saved_long_term_memory is not None and is_tide:
                            relevant_long_term_memories = [saved_long_term_memory]
                            relevant_long_term_memory_payload = [
                                public_long_term_memory(saved_long_term_memory)
                            ]
                    if not is_tide:
                        impressions = await list_user_impressions(db, request_user_id)
                        relevant_impressions = build_relevant_impression_context(
                            impressions, request.message
                        )
                    source_policy = (
                        request.source_context.policy
                        if request.source_context
                        else "auto"
                    )
                    requested_material_ids = resolved_refs.material_ids or (
                        request.source_context.material_ids
                        if request.source_context
                        else []
                    )
                    # `exclude` is an explicit user boundary and must dominate even
                    # when stale UI state still carries material IDs. Explicit IDs
                    # otherwise opt into retrieval, while `auto` still requires a
                    # material-reference utterance when no IDs were selected.
                    should_retrieve_materials = should_retrieve_material_context(
                        request.message, source_policy, requested_material_ids
                    )
                    if should_retrieve_materials:
                        try:
                            retrieved = await build_conversation_material_context(
                                db,
                                user_id=request_user_id,
                                thread_id=request.thread_id,
                                query=request.message,
                                requested_material_ids=requested_material_ids or None,
                                scope=request.source_context.scope
                                if request.source_context
                                else None,
                            )
                        except MaterialAccessError as exc:
                            yield f"data: {json.dumps({'type': 'error', 'code': 'MATERIAL_NOT_AVAILABLE', 'message': str(exc), 'retryable': False}, ensure_ascii=False)}\n\n"
                            return
                        material_context = retrieved.text or None
                        source_material_ids = retrieved.material_ids
                        source_citations = retrieved.citations
                    try:
                        trusted_question_context = attach_trusted_selection_context(
                            build_reference_question_context(resolved_refs),
                            resolved_refs,
                            request.selection_context,
                        )
                    except ReferenceAccessError as exc:
                        yield f"data: {json.dumps({'type': 'error', 'code': 'REFERENCE_NOT_AVAILABLE', 'message': str(exc), 'retryable': False}, ensure_ascii=False)}\n\n"
                        return
                    requested_exam_id = (
                        request.question_context.get("exam_id")
                        if isinstance(request.question_context, dict)
                        else None
                    )
                    if (
                        requested_exam_id
                        and not trusted_question_context
                        and not resolved_refs.material_ids
                    ):
                        exam_result = await db.execute(
                            select(ExamPaper).where(
                                ExamPaper.id == int(requested_exam_id),
                                ExamPaper.user_id == request_user_id,
                            )
                        )
                        active_exam = exam_result.scalar_one_or_none()
                        if active_exam is not None:
                            questions = []
                            for question in active_exam.questions or []:
                                item = {
                                    "id": question.get("id"),
                                    "type": question.get("type"),
                                    "content": question.get("content")
                                    or question.get("title"),
                                    "options": question.get("options") or [],
                                    "points": question.get("points"),
                                }
                                if active_exam.status == "completed":
                                    item["correct_answer"] = question.get("answer")
                                    item["explanation"] = question.get("explanation")
                                questions.append(item)
                            trusted_question_context = {
                                "exam_id": active_exam.id,
                                "status": active_exam.status,
                                "questions": questions,
                                "answers": active_exam.answers
                                if active_exam.status == "completed"
                                else None,
                                "score": active_exam.score
                                if active_exam.status == "completed"
                                else None,
                                "total_score": active_exam.total_score,
                            }
                    requested_artifact_id = (
                        request.question_context.get("artifact_id")
                        if isinstance(request.question_context, dict)
                        else None
                    )
                    if (
                        requested_artifact_id
                        and not trusted_question_context
                        and not resolved_refs.material_ids
                    ):
                        artifact_result = await db.execute(
                            select(GeneratedItem).where(
                                GeneratedItem.id == int(requested_artifact_id),
                                GeneratedItem.user_id == request_user_id,
                                GeneratedItem.type.in_(
                                    (
                                        "workspace_presentation",
                                        "workspace_interactive_lesson",
                                        "workspace_code",
                                    )
                                ),
                            )
                        )
                        active_item = artifact_result.scalar_one_or_none()
                        metadata = (
                            dict(active_item.raw_data or {}) if active_item else {}
                        )
                        if (
                            active_item is not None
                            and str(metadata.get("thread_id")) == request.thread_id
                        ):
                            if active_item.type == "workspace_code":
                                trusted_question_context = {
                                    "artifact_id": active_item.id,
                                    "artifact_kind": "code",
                                    "title": active_item.title,
                                    "topic": active_item.chapter,
                                    "language": metadata.get("language", "python"),
                                    "source": str(active_item.content or "")[:12000],
                                    "last_run": metadata.get("last_run") or {},
                                }
                            else:
                                slides = []
                                for index, slide in enumerate(
                                    (metadata.get("slides") or [])[:16]
                                ):
                                    slides.append(
                                        {
                                            "part": index + 1,
                                            "title": str(slide.get("title") or "")[
                                                :200
                                            ],
                                            "body": str(slide.get("body") or "")[:800],
                                            "items": list(slide.get("items") or [])[:8],
                                            "narration": str(
                                                slide.get("narration") or ""
                                            )[:1200],
                                        }
                                    )
                                trusted_question_context = {
                                    "artifact_id": active_item.id,
                                    "artifact_kind": metadata.get("kind"),
                                    "title": active_item.title,
                                    "topic": active_item.chapter,
                                    "current_slide": (
                                        metadata.get("progress") or {}
                                    ).get("current_slide", 0),
                                    "slides": slides,
                                }
            else:
                style_decision = resolve_style(request.message, None, None)

                # Non-Tide search decision for grounded answers on current facts
                non_tide_search_decision = None
                if not request.retry:
                    search_decision = decide_search_need(
                        request.message,
                        context_history=server_history[:-1] if server_history else [],
                        domain_pack_available=False,  # Non-Tide doesn't use Domain Packs
                        existing_profile=existing_profile,
                    )
                    non_tide_search_decision = search_decision

                    if search_decision.should_search:
                        yield f"data: {json.dumps({'type': 'search_status', 'status': 'searching', 'message': '正在查找资料'}, ensure_ascii=False)}\n\n"
                        try:
                            search_ctx = build_search_context_from_conversation(
                                request.message,
                                existing_profile=existing_profile,
                                recent_history=server_history[-5:]
                                if server_history
                                else [],
                            )
                            non_tide_evidence_bundle = await build_evidence_bundle(
                                search_ctx,
                                search_intent=(True, search_decision.reason),
                            )
                            # Build search grounding for system directive
                            from services.darya_search_grounding import SearchPlan

                            synthetic_plan = SearchPlan(
                                decision="SEARCH_REQUIRED"
                                if search_decision.should_search
                                else "SEARCH_NOT_NEEDED",
                                reason=search_decision.reason,
                                trigger=search_decision.trigger,
                                domain_status="NOT_LEARNING",
                                installed_domain_id=None,
                            )
                            non_tide_search_grounding = build_search_grounding(
                                synthetic_plan,
                                non_tide_evidence_bundle,
                            )
                            yield f"data: {json.dumps({'type': 'search_status', 'status': 'complete', 'sources_found': len(non_tide_evidence_bundle.sources)}, ensure_ascii=False)}\n\n"
                        except Exception as search_exc:
                            logger.warning(
                                "Non-Tide search failed user=%s thread=%s: %s",
                                request_user_id,
                                request.thread_id,
                                type(search_exc).__name__,
                            )
                            yield f"data: {json.dumps({'type': 'search_status', 'status': 'failed', 'message': '搜索暂时不可用'}, ensure_ascii=False)}\n\n"

            if is_tide:
                # 检索式由计划它的人给。编排器已经在 research_task.questions[].query 里
                # 写好了这一轮要核对什么、用什么查询词；以前这里只从另一个规划器
                # （darya_goal_collaboration）取 research_query，它没给就退回把用户原话
                # 正则改写一遍当查询词。2026-09-23 实测的后果：模型写的是
                # 「告白气球 吉他谱 简化版 C调 和弦 新手」，发出去的是
                # 「在县城摆早点摊 卖包子豆浆 一天流水大概六七百块」，0 来源、
                # UNVERIFIED，模型只能把每条事实标成待核对。模型做对了，管道把它扔了。
                _model_research_queries = (
                    [
                        str(item.query or "").strip()
                        for item in getattr(
                            getattr(darya_cognition_v2, "research_task", None),
                            "questions",
                            (),
                        ) or ()
                        if str(item.query or "").strip()
                    ]
                    if darya_cognition_v2 is not None
                    else []
                )
                search_context = SearchContext(
                    message=request.message,
                    query_override=(
                        _model_research_queries[0]
                        if _model_research_queries
                        else (
                            darya_goal_collaboration.research_query
                            if darya_goal_collaboration is not None
                            and darya_goal_collaboration.external_research_needed
                            else ""
                        )
                    ),
                    planned_queries=_model_research_queries[1:],
                    shared_perspective=tide_perspective_values,
                    workspace_object=tide_workspace_object_values,
                    artifact_context=trusted_question_context or {},
                )
                initial_domain_resolution = resolve_learning_domain(
                    message=request.message
                )
                existing_learning_state = None
                if db_conn._async_session_factory:
                    try:
                        async with (
                            db_conn._async_session_factory() as learning_probe_db
                        ):
                            existing_learning_state = await get_learning_route_state(
                                learning_probe_db,
                                user_id=request_user_id,
                                thread_id=request.thread_id,
                            )
                    except Exception as learning_probe_exc:
                        logger.warning(
                            "DARYA learning-loop probe unavailable user=%s thread=%s: %s",
                            request_user_id,
                            request.thread_id,
                            type(learning_probe_exc).__name__,
                        )
                route_for_turn = existing_learning_state
                if route_for_turn is not None and explicit_topic_switch_from_message(request.message):
                    route_for_turn = None
                if route_for_turn is not None:
                    quoted_topic = re.search(r"「([^」]{1,120})」", request.message)
                    route_pack = get_domain_pack(route_for_turn.domain_id)
                    route_titles = (
                        [route_pack.title, *(node.title for node in route_pack.nodes.values())]
                        if route_pack is not None else []
                    )
                    quoted_domain = (
                        resolve_learning_domain(message=quoted_topic.group(1)).pack
                        if quoted_topic is not None else None
                    )
                    quoted_matches_route = quoted_topic is not None and (
                        (quoted_domain is not None and quoted_domain.domain_id == route_for_turn.domain_id)
                        or any(
                            quoted_topic.group(1) in title or title in quoted_topic.group(1)
                            for title in route_titles
                        )
                    )
                    if (
                        initial_domain_resolution.pack is not None
                        and initial_domain_resolution.pack.domain_id != route_for_turn.domain_id
                    ) or (
                        quoted_topic is not None
                        and not quoted_matches_route
                    ):
                        route_for_turn = None
                stage_action = stage_action_from_message(request.message) if route_for_turn else None
                if stage_action and db_conn._async_session_factory:
                    try:
                        async with db_conn._async_session_factory() as stage_db:
                            await control_teaching_stage(
                                stage_db, user_id=request_user_id,
                                thread_id=request.thread_id, action=stage_action,
                                message_id=message_id,
                            )
                            await stage_db.commit()
                    except Exception as stage_exc:
                        logger.warning(
                            "DARYA teaching-stage control unavailable user=%s thread=%s: %s",
                            request_user_id, request.thread_id, type(stage_exc).__name__,
                            exc_info=True,
                        )
                installed_domain_id = (
                    initial_domain_resolution.pack.domain_id
                    if initial_domain_resolution.pack is not None
                    else (route_for_turn.domain_id if route_for_turn is not None else None)
                )
                if early_search is not None:
                    # Already decided before the turn produced anything.
                    darya_search_plan = early_search.plan
                elif darya_cognition_v2 is not None:
                    darya_search_plan = search_plan_from_model(
                        darya_cognition_v2,
                        domain_status=initial_domain_resolution.status,
                        installed_domain_id=installed_domain_id,
                    )
                else:
                    darya_search_plan = decide_search_plan(
                        message=request.message,
                        domain_status=initial_domain_resolution.status,
                        installed_domain_id=installed_domain_id,
                        domain_confidence=initial_domain_resolution.confidence,
                    )
                logger.info(
                    "DARYA search authority user=%s thread=%s v2=%s decision=%s trigger=%s",
                    request_user_id,
                    request.thread_id,
                    (darya_cognition_v2.source if darya_cognition_v2 is not None else "absent"),
                    darya_search_plan.decision,
                    darya_search_plan.trigger,
                )
                search_context.preferred_urls = list(darya_search_plan.preferred_urls)
                explicit_teaching_move = bool(
                    route_for_turn is not None
                    and (requested_teaching_form(request.message) or stage_action == "resume")
                )
                suppress_learning = bool(
                    explicit_response_constraint is not None
                    and explicit_response_constraint.suppress_learning
                )
                suppress_learning = suppress_learning or bool(
                    darya_cognition_v2 is not None
                    and darya_cognition_v2.source == "structured_model"
                    and darya_cognition_v2.turn_mode in {"CONVERSATION", "PAUSED", "STOPPED"}
                    and not explicit_teaching_move
                )
                suppress_learning = suppress_learning or stage_action in {"pause", "end"}
                if darya_cognition_v2 is not None:
                    learning_loop_candidate = bool(
                        (explicit_teaching_move or (
                            darya_cognition_v2.source == "structured_model"
                            and darya_cognition_v2.turn_mode == "LEARNING"
                        ))
                        and (
                            initial_domain_resolution.pack is not None
                            or explicit_teaching_move
                            or (route_for_turn is not None and is_learning_loop_message(
                                request.message,
                                has_existing_state=True,
                                current_domain_id=route_for_turn.domain_id,
                            ))
                        )
                        and not suppress_learning
                        and not darya_search_plan.should_search
                        and (
                            conversation_capability_action is None
                            or (
                                explicit_teaching_move
                                and conversation_capability_action.get("kind") == "DEMONSTRATE"
                            )
                        )
                    )
                else:
                    learning_loop_candidate = is_learning_loop_message(
                        request.message,
                        has_existing_state=route_for_turn is not None,
                        current_domain_id=(
                            route_for_turn.domain_id
                            if route_for_turn
                            else None
                        ),
                    )
                if (
                    darya_cognition_v2 is not None
                    and darya_cognition_v2.source == "structured_model"
                    and darya_cognition_v2.turn_mode == "LEARNING"
                    and initial_domain_resolution.pack is None
                    and not learning_loop_candidate
                    and conversation_capability_action is None
                    and not suppress_learning
                ):
                    # The installed packs own deep node/evidence routing. For an
                    # outside topic, reuse the existing pack-independent teaching
                    # method and checkable-claim directive with a bounded budget.
                    try:
                        universal_directive = await asyncio.wait_for(
                            build_universal_directive_from_message(
                                user_message=request.message,
                                history=server_history or [],
                                profile=existing_profile,
                                learner_state=normalized_learner_state,
                            ),
                            timeout=8.0,
                        )
                    except Exception as universal_exc:
                        logger.warning(
                            "DARYA Tide universal teaching directive unavailable user=%s thread=%s: %s",
                            request_user_id, request.thread_id, type(universal_exc).__name__,
                        )
                if (
                    darya_cognition_v2 is None
                    and not suppress_learning
                    and route_for_turn is None
                    and initial_domain_resolution.status == "AMBIGUOUS"
                ):
                    question = build_domain_disambiguation_question(
                        initial_domain_resolution
                    )
                    darya_domain_disambiguation = {
                        **initial_domain_resolution.public_dict(),
                        "question": question,
                        "learning_state_created": False,
                    }
                search_needed = bool(
                    darya_search_plan.should_search
                    and darya_domain_disambiguation is None
                    and darya_immediate_safety is None
                )
                perspective_builder = (
                    build_current_perspective_from_model
                    if darya_cognition_v2 is not None
                    else build_current_perspective
                )
                perspective_kwargs = dict(
                    message=request.message,
                    message_id=message_id,
                    shared_perspective=tide_perspective_values,
                    workspace_object=tide_workspace_object_values,
                    artifact_context=trusted_question_context
                    or resolved_refs.artifact_context
                    or {},
                    relevant_memories=relevant_long_term_memories,
                    relevant_user_context=relevant_user_context_payload,
                )
                if darya_cognition_v2 is not None:
                    perspective_kwargs["cognition"] = darya_cognition_v2
                else:
                    perspective_kwargs.update(
                        search_required=search_needed,
                        previous_state=(darya_previous_perspective.state if darya_previous_perspective else None),
                        goal_collaboration=(darya_goal_collaboration.public_dict() if darya_goal_collaboration is not None else None),
                    )
                darya_current_perspective_state = perspective_builder(**perspective_kwargs)
                darya_action_execution = plan_action_execution(
                    darya_current_perspective_state,
                    artifact_action=tide_artifact_action_values or None,
                )
                tide_decision = (
                    tide_decision_from_model_support(
                        darya_cognition_v2,
                        shared_perspective=tide_perspective_values,
                    )
                    if darya_cognition_v2 is not None
                    else decide_tide_interaction(
                        request.message,
                        shared_perspective=tide_perspective_values,
                        current_perspective=darya_current_perspective_state,
                    )
                )
            tide_evidence_bundle = None
            if is_tide:
                if search_needed:
                    if early_search is not None and early_search.searched:
                        # Fetched before anything was produced; reusing it keeps
                        # one web call per turn instead of two.
                        tide_evidence_bundle = early_search.bundle
                        darya_search_grounding = dict(early_search.grounding or {})
                    else:
                        yield f"data: {json.dumps({'type': 'search_status', 'status': 'checking', 'message': '正在核对资料'}, ensure_ascii=False)}\n\n"
                        tide_evidence_bundle = await build_evidence_bundle(
                            search_context,
                            search_intent=(True, darya_search_plan.reason),
                        )
                        tide_evidence_bundle = calibrate_bundle_for_search_plan(
                            darya_search_plan,
                            tide_evidence_bundle,
                        )
                        darya_search_grounding = build_search_grounding(
                            darya_search_plan,
                            tide_evidence_bundle,
                        )
                    perspective_evidence_kwargs = dict(
                        message=request.message,
                        message_id=message_id,
                        shared_perspective=tide_perspective_values,
                        workspace_object=tide_workspace_object_values,
                        artifact_context=trusted_question_context or resolved_refs.artifact_context or {},
                        relevant_memories=relevant_long_term_memories,
                        relevant_user_context=relevant_user_context_payload,
                        evidence_bundle=tide_evidence_bundle.public_dict(),
                    )
                    if darya_cognition_v2 is not None:
                        perspective_evidence_kwargs["cognition"] = darya_cognition_v2
                        darya_current_perspective_state = build_current_perspective_from_model(
                            **perspective_evidence_kwargs
                        )
                    else:
                        perspective_evidence_kwargs.update(
                            search_required=True,
                            previous_state=(darya_previous_perspective.state if darya_previous_perspective else None),
                            goal_collaboration=(darya_goal_collaboration.public_dict() if darya_goal_collaboration is not None else None),
                        )
                        darya_current_perspective_state = build_current_perspective(
                            **perspective_evidence_kwargs
                        )
                    tide_decision = replace(
                        tide_decision,
                        proactive_level=2,
                        sidecar=True,
                        sidecar_type="evidence",
                        sidecar_title="为什么这条值得信",
                    )
                elif darya_search_plan is not None:
                    darya_search_grounding = build_search_grounding(
                        darya_search_plan, None
                    )
                if (
                    darya_current_perspective_state is not None
                    and darya_action_execution is not None
                ):
                    darya_current_perspective_state["selected_action"] = (
                        darya_action_execution.selected_action
                    )
                    darya_current_perspective_state["action_execution"] = (
                        darya_action_execution.public_dict()
                    )
                darya_cognition_frame = build_cognition_frame(
                    request.message,
                    goal_plan=darya_goal_collaboration,
                    capability_action=conversation_capability_action,
                    search_plan=darya_search_plan,
                    current_perspective=darya_current_perspective_state,
                    evidence_bundle=(
                        tide_evidence_bundle.public_dict()
                        if tide_evidence_bundle is not None
                        else None
                    ),
                )
                if (
                    db_conn._async_session_factory
                    and not suppress_learning
                    and learning_loop_candidate
                ):
                    try:
                        async with db_conn._async_session_factory() as learning_db:
                            darya_learning_loop_result = await run_learning_turn(
                                learning_db,
                                user_id=request_user_id,
                                thread_id=request.thread_id,
                                message=request.message,
                                message_id=message_id,
                                perspective=darya_current_perspective_state or {},
                                relevant_memories=relevant_long_term_memories,
                                response_llm=(
                                    await get_llm_service()
                                    if darya_cognition_v2 is not None
                                    else None
                                ),
                                work_brief=(
                                    (darya_cognition_v2.public_dict().get("work_brief") or {})
                                    if darya_cognition_v2 is not None
                                    else {}
                                ),
                            )
                            if darya_learning_loop_result is not None:
                                await learning_db.commit()
                                try:
                                    await emit_tide_learning_opportunity(
                                        learning_db,
                                        user_id=request_user_id,
                                        thread_id=request.thread_id,
                                        learning_result=darya_learning_loop_result,
                                    )
                                except Exception as opportunity_exc:
                                    logger.warning(
                                        "DARYA proactive opportunity unavailable user=%s thread=%s: %s",
                                        request_user_id,
                                        request.thread_id,
                                        type(opportunity_exc).__name__,
                                    )
                    except Exception as learning_exc:
                        logger.warning(
                            "DARYA learning loop unavailable user=%s thread=%s: %s",
                            request_user_id,
                            request.thread_id,
                            type(learning_exc).__name__,
                            exc_info=True,
                        )
                        darya_learning_loop_result = None
                if (
                    darya_learning_loop_result is not None
                    and darya_current_perspective_state is not None
                ):
                    learning_roles = darya_learning_loop_result.get("roles") or {}
                    resource_orchestration = (
                        darya_learning_loop_result.get("resource_orchestration") or {}
                    )
                    learning_resource = resource_orchestration.get("resource") or {}
                    resource_fit = resource_orchestration.get("fit") or {}
                    darya_current_perspective_state["learning_loop"] = {
                        "contract_version": darya_learning_loop_result.get(
                            "contract_version"
                        ),
                        "cycle": darya_learning_loop_result.get("cycle"),
                        "current_node": (
                            (darya_learning_loop_result.get("route") or {}).get("after")
                            or {}
                        ).get("current_node"),
                        "next_node": (
                            (darya_learning_loop_result.get("route") or {}).get("after")
                            or {}
                        ).get("next_node"),
                        "route_movement": (
                            (darya_learning_loop_result.get("route") or {}).get("after")
                            or {}
                        ).get("movement"),
                        "diagnosis_confidence": (
                            learning_roles.get("diagnosis") or {}
                        ).get("confidence"),
                        "decision": (learning_roles.get("decision") or {}).get(
                            "action"
                        ),
                        "scaffold_level": (learning_roles.get("decision") or {}).get(
                            "scaffold_level"
                        ),
                        "resource_type": learning_resource.get("resource_type"),
                        "resource_fit_level": resource_fit.get("level"),
                        "resource_fit_correctable": resource_fit.get("correctable"),
                        "learner_state_revision": (
                            (darya_learning_loop_result.get("learner_state") or {}).get(
                                "after"
                            )
                            or {}
                        ).get("revision"),
                    }
                if (
                    db_conn._async_session_factory
                    and darya_current_perspective_state is not None
                ):
                    try:
                        async with db_conn._async_session_factory() as perspective_db:
                            current_row = await persist_current_perspective(
                                perspective_db,
                                user_id=request_user_id,
                                thread_id=request.thread_id,
                                message_id=message_id,
                                state=darya_current_perspective_state,
                            )
                            await perspective_db.commit()
                            await perspective_db.refresh(current_row)
                            darya_current_perspective_payload = (
                                public_darya_current_perspective(
                                    current_row,
                                    thread_id=request.thread_id,
                                )
                            )
                    except Exception as perspective_exc:
                        logger.warning(
                            "DARYA Current Perspective persistence unavailable user=%s thread=%s: %s",
                            request_user_id,
                            request.thread_id,
                            type(perspective_exc).__name__,
                        )
            conversation_clarification = natural_clarification_question(
                request.message,
                selected_context=json.dumps(
                    {
                        "profile": existing_profile or {},
                        "learner_state": normalized_learner_state or {},
                    },
                    ensure_ascii=False,
                ),
            )
            yield f"data: {json.dumps({'type': 'context', 'memory_loaded': memory_loaded, 'memory_keys': [item.key for item in confirmed_memories], 'effective_response_style': style_decision.request_style, 'memory_source': style_decision.source, 'source_material_ids': source_material_ids, 'source_citations': source_citations, 'relevant_user_context_count': len(relevant_user_context_payload), 'relevant_long_term_memory_count': len(relevant_long_term_memory_payload)}, ensure_ascii=False)}\n\n"
            if darya_cognition_v2 is not None:
                yield f"data: {json.dumps({'type': 'cognition_orchestration', 'orchestration': darya_cognition_v2.public_dict()}, ensure_ascii=False)}\n\n"
            if conversation_capability_action is not None and darya_cognition_v2 is not None:
                # Search finishes after capability planning. Bind its reviewed
                # result to the same action before the UI receives or saves it.
                action_payload = conversation_capability_action.setdefault("request_payload", {})
                artifact_evidence = tide_evidence_bundle.public_dict() if tide_evidence_bundle is not None else None
                action_payload["generation_evidence_verified"] = has_reviewed_evidence(artifact_evidence)
                action_payload["generation_context"] = (
                    build_artifact_generation_context(
                        user_message=request.message,
                        cognition=darya_cognition_v2.public_dict(),
                        evidence=artifact_evidence,
                    )
                )
            if conversation_capability_action is not None:
                _board_title = _board_title_of(conversation_capability_action)
                if _board_title:
                    # 板子要一笔一笔写出来，先把讲的题目说出去，别让人干等。
                    yield _progress_event('writing', '正在写板书：' + _board_title)
                yield f"data: {json.dumps({'type': 'conversation_action', 'action': conversation_capability_action}, ensure_ascii=False)}\n\n"

            # Non-Tide search evidence output
            if non_tide_evidence_bundle is not None:
                yield f"data: {json.dumps({'type': 'evidence_bundle', 'bundle': non_tide_evidence_bundle.public_dict()}, ensure_ascii=False)}\n\n"
            if non_tide_search_grounding is not None:
                yield f"data: {json.dumps({'type': 'search_grounding', 'assistant_message_id': assistant_message_id, 'grounding': non_tide_search_grounding}, ensure_ascii=False)}\n\n"

            if tide_decision:
                yield f"data: {json.dumps({'type': 'tide_context', **tide_decision.public_context()}, ensure_ascii=False)}\n\n"
                if darya_action_execution is not None:
                    yield f"data: {json.dumps({'type': 'action_execution', 'execution': darya_action_execution.public_dict()}, ensure_ascii=False)}\n\n"
                if darya_current_perspective_payload is not None:
                    yield f"data: {json.dumps({'type': 'current_perspective', 'snapshot': darya_current_perspective_payload}, ensure_ascii=False)}\n\n"
                if tide_perspective_payload is not None:
                    yield f"data: {json.dumps({'type': 'shared_perspective', 'snapshot': tide_perspective_payload}, ensure_ascii=False)}\n\n"
                if tide_workspace_object_payload is not None:
                    yield f"data: {json.dumps({'type': 'workspace_object', 'object': tide_workspace_object_payload}, ensure_ascii=False)}\n\n"
                if tide_artifact_action_payload is not None:
                    yield f"data: {json.dumps({'type': 'artifact_action', 'action': tide_artifact_action_payload}, ensure_ascii=False)}\n\n"
                if tide_evidence_bundle is not None:
                    evidence_payload = tide_evidence_bundle.public_dict()
                    if (
                        darya_goal_collaboration is not None
                        and darya_goal_collaboration.active
                        and tide_evidence_bundle.review_disposition == "CALIBRATED_FALLBACK"
                    ):
                        # Related-but-irrelevant hits are retained in server
                        # telemetry, but must not be presented as user guidance.
                        evidence_payload["sources"] = []
                    yield f"data: {json.dumps({'type': 'tide_evidence', 'bundle': evidence_payload}, ensure_ascii=False)}\n\n"
                hide_weak_goal_grounding = bool(
                    darya_goal_collaboration is not None
                    and darya_goal_collaboration.active
                    and tide_evidence_bundle is not None
                    and tide_evidence_bundle.review_disposition == "CALIBRATED_FALLBACK"
                )
                if darya_search_grounding is not None and not hide_weak_goal_grounding:
                    yield f"data: {json.dumps({'type': 'search_grounding', 'assistant_message_id': assistant_message_id, 'grounding': darya_search_grounding}, ensure_ascii=False)}\n\n"
                if relevant_user_context_payload:
                    yield f"data: {json.dumps({'type': 'user_context_used', 'claims': relevant_user_context_payload}, ensure_ascii=False)}\n\n"
                if is_tide:
                    memory_reason = "saved" if saved_long_term_memory is not None else (
                        "selected" if relevant_long_term_memory_payload else "none_selected"
                    )
                    yield f"data: {json.dumps({'type': 'long_term_memory_used', 'snapshot': {'scope': 'LONG_TERM_PERSONAL', 'reason': memory_reason, 'memories': relevant_long_term_memory_payload}}, ensure_ascii=False)}\n\n"
                if darya_learning_loop_result is not None:
                    yield f"data: {json.dumps({'type': 'learning_loop', 'snapshot': darya_learning_loop_result}, ensure_ascii=False)}\n\n"
                if darya_domain_disambiguation is not None:
                    yield f"data: {json.dumps({'type': 'domain_disambiguation', 'snapshot': darya_domain_disambiguation}, ensure_ascii=False)}\n\n"
                if explicit_response_constraint is not None:
                    yield f"data: {json.dumps({'type': 'conversation_control', 'control': explicit_response_constraint.public_context()}, ensure_ascii=False)}\n\n"

            # 1. 意图标记（毫秒级，不调 LLM）
            yield f"data: {json.dumps({'type': 'agent_start', 'agent': 'intent_router'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'agent_complete', 'agent': 'intent_router', 'status': 'success'}, ensure_ascii=False)}\n\n"

            # 2. tutor_agent 流式答疑
            yield f"data: {json.dumps({'type': 'agent_start', 'agent': 'tutor_response'}, ensure_ascii=False)}\n\n"

            context_history = (
                server_history[:-1]
                if server_history
                else [
                    {"role": m.role, "content": m.content}
                    for m in (request.history or [])
                ]
            )
            full_text = ""
            used_stream = False
            # 资料卡和本轮答复是两件事，不能互相顶替。
            #
            # 2026-09-23 实测的新账号旅程：他描述完整个处境并说「你直接告诉我怎么弄吧」，
            # orchestrator 顺手规划了一份实操指南，于是那条分支生效、模型根本没跑——
            # 他连续两轮收到同一句写死的台词（23 字），第三轮才拿到真正能照做的步骤。
            # 也就是说，用户越是带着真事来、越需要材料，越拿不到回答。
            #
            # 现在这里不再有任何顶替：资料卡照旧由下面的 conversation_action 事件发出
            # （它有自己的渲染路径），本轮回答一律由模型写出来。
            #
            # 为什么连兜底都不留：这条链上原来还留了一句写死的台词，等模型没产出时顶上。
            # 产品里的检查和工具都是为了让模型把话说出来，不是替它说话。模型真的一句话都
            # 没产出，那是调用链上的缺陷，应该**报出来**（下面的 RuntimeError → 前端错误态），
            # 而不是用一句固定的话假装这一轮答过了。
            if conversation_clarification is not None:
                full_text = conversation_clarification
                used_stream = True
                begin_runtime_operation(
                    request_user_id, request.thread_id, "chat_streaming"
                )
                yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'tutor_response', 'preview': full_text}, ensure_ascii=False)}\n\n"
            elif (
                explicit_response_constraint is not None
                and explicit_response_constraint.deterministic_response is not None
            ):
                full_text = explicit_response_constraint.deterministic_response
                used_stream = True
                begin_runtime_operation(
                    request_user_id, request.thread_id, "chat_streaming"
                )
                yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'tutor_response', 'preview': full_text}, ensure_ascii=False)}\n\n"
            elif saved_long_term_memory is not None:
                full_text = "记住了。以后遇到相关问题，我会直接按这个来。"
                used_stream = True
                begin_runtime_operation(
                    request_user_id, request.thread_id, "chat_streaming"
                )
                yield f"data: {json.dumps({'type': 'long_term_memory_saved', 'snapshot': long_term_memory_snapshot([saved_long_term_memory], reason='saved')}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'tutor_response', 'preview': full_text}, ensure_ascii=False)}\n\n"
            elif saved_memory_action:
                full_text = saved_memory_action["confirmation"]
                used_stream = True
                begin_runtime_operation(
                    request_user_id, request.thread_id, "chat_streaming"
                )
                saved_payload = [
                    {
                        "id": item.id,
                        "key": candidate["key"],
                        "value": candidate["value"],
                        "category": candidate["category"],
                        "priority": candidate["priority"],
                        "source_thread_id": candidate["source_thread_id"],
                        "source_message_id": candidate["source_message_id"],
                    }
                    for item, candidate in zip(
                        saved_memory_action["items"], saved_memory_action["candidates"]
                    )
                ]
                yield f"data: {json.dumps({'type': 'memory_saved', 'memories': saved_payload}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'tutor_response', 'preview': full_text}, ensure_ascii=False)}\n\n"
            elif darya_domain_disambiguation is not None:
                full_text = str(darya_domain_disambiguation["question"])
                used_stream = True
                begin_runtime_operation(
                    request_user_id, request.thread_id, "chat_streaming"
                )
                yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'tutor_response', 'preview': full_text}, ensure_ascii=False)}\n\n"
            elif darya_learning_loop_result is not None:
                companion = (darya_learning_loop_result.get("roles") or {}).get(
                    "companion"
                ) or {}
                full_text = str(companion.get("user_response") or "").strip()
                if not full_text:
                    raise RuntimeError(
                        "Learning loop completed without a Companion response"
                    )
                used_stream = True
                begin_runtime_operation(
                    request_user_id, request.thread_id, "chat_streaming"
                )
                yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'tutor_response', 'preview': full_text}, ensure_ascii=False)}\n\n"
            else:
                context_builder = ChatContextBuilder()
                # A str subclass: every `+` below records itself, so the 18
                # appends stay untouched and this turn can still say what the
                # model was handed without opening 14 modules.
                system_prompt = PromptLedger(
                    context_builder.build_system_prompt(
                        confirmed_memories,
                        style_decision.request_style,
                        relevant_impressions=relevant_impressions,
                        profile=existing_profile,
                        learner_state=normalized_learner_state,
                        speech=bool(getattr(request, "speech", False)),
                    )
                )
                if tutor_directive:
                    system_prompt = system_prompt + "\n\n" + tutor_directive
                if universal_directive:
                    system_prompt = system_prompt + "\n\n" + universal_directive
                if is_tide and re.search(r"自己答|独立作答|先让我答|不要给答案|先别给答案", request.message):
                    system_prompt += (
                        "\n\n本轮用户要先独立作答。可以讲解另一道示例题，但对本轮要他作答的题目，"
                        "只给题干和作答要求；在收到其答案前，不给标准答案、关键分类结论，"
                        "也不给足以直接推出本题答案的提示。收到作答后再判断并调整下一步。"
                    )

                # Add non-Tide search evidence to system prompt
                if non_tide_evidence_bundle is not None:
                    system_prompt = (
                        system_prompt
                        + "\n\n"
                        + build_evidence_directive(non_tide_evidence_bundle)
                    )
                if non_tide_search_grounding is not None:
                    system_prompt = (
                        system_prompt
                        + "\n\n"
                        + build_search_grounding_directive(non_tide_search_grounding)
                    )

                if tide_decision:
                    system_prompt = (
                        system_prompt
                        + "\n\n"
                        + build_tide_system_directive(
                            tide_decision,
                            shared_perspective=tide_perspective_values,
                        )
                    )
                    workspace_directive = build_workspace_object_directive(
                        tide_workspace_object_values,
                    )
                    if workspace_directive:
                        system_prompt = system_prompt + "\n\n" + workspace_directive
                    action_directive = build_artifact_action_directive(
                        tide_artifact_action_values,
                    )
                    if action_directive:
                        system_prompt = system_prompt + "\n\n" + action_directive
                    planned_action_directive = build_planned_action_directive(
                        conversation_capability_action,
                    )
                    if planned_action_directive:
                        system_prompt = (
                            system_prompt + "\n\n" + planned_action_directive
                        )
                    if tide_evidence_bundle is not None:
                        system_prompt = (
                            system_prompt
                            + "\n\n"
                            + build_evidence_directive(tide_evidence_bundle)
                        )
                    if darya_search_grounding is not None:
                        system_prompt = (
                            system_prompt
                            + "\n\n"
                            + build_search_grounding_directive(
                                darya_search_grounding,
                            )
                        )
                    user_context_directive = build_user_context_directive(
                        relevant_user_context
                    )
                    if user_context_directive:
                        system_prompt = system_prompt + "\n\n" + user_context_directive
                    long_term_memory_directive = build_memory_directive(
                        relevant_long_term_memories
                    )
                    if long_term_memory_directive:
                        system_prompt = (
                            system_prompt + "\n\n" + long_term_memory_directive
                        )
                    if darya_current_perspective_state is not None:
                        system_prompt = (
                            system_prompt
                            + "\n\n"
                            + build_perspective_directive(
                                darya_current_perspective_state,
                            )
                        )
                    model_support_directive = build_model_support_directive(
                        darya_model_support_packet
                    )
                    if model_support_directive:
                        system_prompt = system_prompt + "\n\n" + model_support_directive
                    if darya_goal_collaboration is not None:
                        goal_directive = build_goal_collaboration_directive(
                            darya_goal_collaboration
                        )
                        if goal_directive:
                            system_prompt = system_prompt + "\n\n" + goal_directive
                    if darya_action_execution is not None:
                        system_prompt = (
                            system_prompt
                            + "\n\n"
                            + build_action_execution_directive(
                                darya_action_execution,
                            )
                        )
                    if darya_cognition_frame is not None:
                        system_prompt = (
                            system_prompt
                            + "\n\n"
                            + build_cognition_frame_directive(
                                darya_cognition_frame,
                            )
                        )
                    if darya_cognition_v2 is not None:
                        system_prompt = (
                            system_prompt
                            + "\n\n"
                            + build_orchestration_directive(
                                darya_cognition_v2,
                            )
                        )
                    if (
                        tide_evidence_bundle is not None
                        and tide_evidence_bundle.status == "UNVERIFIED"
                        and darya_goal_collaboration is not None
                        and darya_goal_collaboration.active
                    ):
                        system_prompt = (
                            system_prompt
                            + "\n\n无可用外部证据时的执行要求（本段是最终约束）："
                            "不要让检索失败占据开头，也不要停止工作。涉及会变化的政策、资格、机构、"
                            "岗位、院校科目、名额、比例或数字，只能列为待核对项，不能把它们写成已知事实、"
                            "用户优势或推荐前提。先用用户已明确的事实和稳定的一般方法交付临时可用结果。"
                            "如果目标包含计划、选择、怎么开始或做出来，必须给出至少三个有时间边界的步骤、"
                            "每步的完成信号和一个可回退的调整点；不能只列比较维度、未来承诺或待办。"
                            "把 decision_criteria 应用成条件式判断，例如在 A 条件下倾向 X、在 B 条件下倾向 Y，"
                            "不得在关键基础未知时给绝对排名、首选或劝退。"
                            "assistant_commitments 中的核对工作由 DARYA 持有，只把确实需要用户提供的一个变量"
                            "放在回答末尾追问；追问前必须已经交付一版可执行方案。"
                            "Sidecar 应是去重后的工作对象，中心只给判断与下一步，不要把同一段答案复制两遍。"
                        )
                    if explicit_response_constraint is not None:
                        system_prompt = (
                            system_prompt
                            + "\n\n"
                            + build_explicit_response_directive(
                                explicit_response_constraint,
                            )
                        )
                logger.info("[prompt] user=%s %s", request_user_id, system_prompt.report())
                dumped = dump_prompt(
                    str(system_prompt),
                    request_id=request.message_id or request.thread_id,
                    extra="[turn] " + json.dumps(
                        {
                            "thread_id": request.thread_id,
                            "message": str(request.message or "")[:200],
                            "capability": (
                                conversation_capability_action.get("kind")
                                if conversation_capability_action else None
                            ),
                            "tide_mode": getattr(tide_decision, "mode", None),
                            "memories": len(confirmed_memories or ()),
                            "impressions": len(relevant_impressions or ()),
                            "evidence": (
                                tide_evidence_bundle.status
                                if tide_evidence_bundle is not None else None
                            ),
                        },
                        ensure_ascii=False,
                    ),
                )
                if dumped is not None:
                    logger.info("[prompt] dumped to %s", dumped)

                messages = context_builder.build_messages(
                    context_history,
                    request.message,
                    question_context=trusted_question_context,
                    material_context=material_context,
                    material_policy=(
                        request.source_context.policy
                        if request.source_context
                        else "auto"
                    ),
                )
                if darya_immediate_safety is not None:
                    full_text = darya_immediate_safety
                    begin_runtime_operation(
                        request_user_id, request.thread_id, "chat_streaming"
                    )
                    used_stream = True
                    yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'safety_support', 'preview': full_text}, ensure_ascii=False)}\n\n"
                else:
                    llm = await get_runtime_llm_service(request.runtime_llm_config)
                    defer_goal_stream = bool(
                        tide_evidence_bundle is not None
                        and tide_evidence_bundle.status == "UNVERIFIED"
                        and darya_goal_collaboration is not None
                        and darya_goal_collaboration.active
                    )

                    # 优先用流式接口（每收到一段 chunk 立刻推送）
                    if not hasattr(llm, "chat_stream"):
                        raise RuntimeError(
                            "Configured provider does not support streaming"
                        )
                    try:
                        async for chunk in llm.chat_stream(
                            messages, system_prompt=system_prompt
                        ):
                            text_chunk = preview_to_text(chunk)
                            if text_chunk:
                                if not used_stream:
                                    begin_runtime_operation(
                                        request_user_id,
                                        request.thread_id,
                                        "chat_streaming",
                                    )
                                full_text += text_chunk
                                used_stream = True
                                if not defer_goal_stream:
                                    yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'tutor_response', 'preview': text_chunk}, ensure_ascii=False)}\n\n"
                    except Exception as stream_err:
                        logger.warning("Answer stream failed after search: %s", type(stream_err).__name__)
                        available_bundle = tide_evidence_bundle if is_tide else non_tide_evidence_bundle
                        if available_bundle is None:
                            raise
                        full_text = search_recovery_result(
                            available_bundle,
                            first_move=str(getattr(darya_goal_collaboration, "useful_first_move", "") or ""),
                        )
                        if not used_stream:
                            begin_runtime_operation(request_user_id, request.thread_id, "chat_streaming")
                        used_stream = True
                        yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'tide_replacement', 'preview': full_text}, ensure_ascii=False)}\n\n"

                if not used_stream:
                    available_bundle = tide_evidence_bundle if is_tide else non_tide_evidence_bundle
                    if available_bundle is None:
                        raise RuntimeError("Provider stream completed without content")
                    full_text = search_recovery_result(
                        available_bundle,
                        first_move=str(getattr(darya_goal_collaboration, "useful_first_move", "") or ""),
                    )
                    begin_runtime_operation(request_user_id, request.thread_id, "chat_streaming")
                    used_stream = True
                    yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'tide_replacement', 'preview': full_text}, ensure_ascii=False)}\n\n"

                if (
                    'defer_goal_stream' in locals()
                    and defer_goal_stream
                    and darya_cognition_frame is not None
                    and darya_cognition_v2 is None
                    and needs_cognition_repair(darya_cognition_frame, full_text)
                ):
                    repair_prompt = (
                        system_prompt
                        + "\n\n上一版草稿未通过执行质量检查。请从当前用户消息重新作答，不要解释检查过程，"
                        "也不要复制上一版。必须在缺少外部证据时仍交付一版可执行方案："
                        "至少三个带时间边界的步骤；每步写清完成信号；至少一个可回退调整点；"
                        "用条件式比较而不是首选、次选、别碰等绝对结论；"
                        "不得把‘稳定一般知识’扩张成需求量、竞争强弱、提分速度、专业优势等市场结论；"
                        "用户未说明可用时间时，不强塞每天若干小时或固定任务数量，先给可缩放的最小启动版；"
                        "不得向用户讲述内部生成失败、重试、降级、能力绕行或卡片/流程切换；"
                        "有资源生成意图时不得用一段聊天正文冒充已保存资源，也不得声称绕开资源流程；"
                        "会变化的外部事实只列为 DARYA 后续核对项；最后只问一个高价值问题。"
                    )
                    repaired = await llm.chat(
                        messages,
                        system_prompt=repair_prompt,
                        temperature=0.2,
                        max_tokens=2200,
                    )
                    repaired_text = preview_to_text(repaired)
                    if repaired_text.strip():
                        full_text = repaired_text

                if tide_decision:
                    contracted_text = enforce_tide_response_contract(
                        full_text, tide_decision
                    )
                    if darya_current_perspective_state is not None:
                        contracted_text = enforce_capability_boundaries(
                            contracted_text,
                            darya_current_perspective_state,
                        )
                    if tide_evidence_bundle is not None:
                        contracted_text = enforce_evidence_response(
                            contracted_text,
                            tide_evidence_bundle,
                            goal_context=(
                                darya_goal_collaboration.public_dict()
                                if darya_goal_collaboration is not None
                                else None
                            ),
                        )
                    action_completion = None
                    if darya_action_execution is not None:
                        contracted_text, action_completion = (
                            enforce_action_execution_response(
                                contracted_text,
                                darya_action_execution,
                            )
                        )
                    if contracted_text != full_text or (
                        'defer_goal_stream' in locals() and defer_goal_stream
                    ):
                        full_text = contracted_text
                        yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'tide_replacement', 'preview': full_text}, ensure_ascii=False)}\n\n"
                    if action_completion is not None:
                        yield f"data: {json.dumps({'type': 'action_execution_complete', 'execution': action_completion}, ensure_ascii=False)}\n\n"

            if explicit_response_constraint is not None:
                controlled_text = enforce_explicit_response_constraint(
                    full_text,
                    explicit_response_constraint,
                )
                if controlled_text != full_text:
                    full_text = controlled_text
                    yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'tutor_response', 'resource_type': 'tide_replacement', 'preview': full_text}, ensure_ascii=False)}\n\n"

            if darya_cognition_v2 is not None:
                darya_cognition_evaluation = evaluate_orchestration_result(
                    darya_cognition_v2,
                    full_text,
                    evidence_bundle=tide_evidence_bundle,
                    capability_action=conversation_capability_action,
                )
            elif darya_cognition_frame is not None:
                darya_cognition_evaluation = evaluate_cognition_result(
                    darya_cognition_frame,
                    full_text,
                    action_execution=darya_action_execution,
                    evidence_bundle=tide_evidence_bundle,
                )

            # Read the finished answer back before it is stored: numbers with no
            # source, "已经生成" with no receipt, "你已经掌握了" with no judged
            # attempt. Findings are recorded and shown, not silently applied --
            # a guard you cannot see firing is a guard you cannot trust.
            hallucination_review = review_hallucination(
                full_text,
                evidence=(
                    tide_evidence_bundle.public_dict()
                    if tide_evidence_bundle is not None
                    else None
                ),
                receipts=(
                    [conversation_capability_action]
                    if conversation_capability_action is not None
                    and conversation_capability_action.get("status") == "ready"
                    else []
                ),
                available_capabilities=available_channel_names(),
                judged_attempts=(
                    1
                    if conversation_capability_action is not None
                    and (conversation_capability_action.get("trace") or [])
                    else 0
                ),
                learner_message=request.message,
            )
            if not hallucination_review.ok:
                logger.warning(
                    "[hallucination] user=%s findings=%s",
                    request_user_id,
                    hallucination_review.as_dict()["findings"],
                )
                yield f"data: {json.dumps({'type': 'hallucination_review', 'review': hallucination_review.as_dict()}, ensure_ascii=False)}\n\n"

            yield f"data: {json.dumps({'type': 'agent_complete', 'agent': 'tutor_response', 'status': 'success'}, ensure_ascii=False)}\n\n"

            # 3. 保存到数据库（如果有 user_id）
            if db_conn._async_session_factory and request_user_id:
                try:
                    async with db_conn._async_session_factory() as db:
                        assistant_metadata = {}
                        if not hallucination_review.ok:
                            assistant_metadata["hallucination_review"] = (
                                hallucination_review.as_dict()
                            )
                        if conversation_capability_action is not None:
                            assistant_metadata.update(
                                {
                                    "conversation_action": conversation_capability_action,
                                    "orchestration_trace": list(
                                        conversation_capability_action.get("trace")
                                        or []
                                    ),
                                }
                            )
                        if darya_search_grounding is not None:
                            assistant_metadata["search_evidence"] = (
                                darya_search_grounding
                            )
                        # Add non-Tide search evidence to metadata
                        if non_tide_search_grounding is not None:
                            assistant_metadata["search_evidence"] = (
                                non_tide_search_grounding
                            )
                        if darya_cognition_frame is not None:
                            assistant_metadata["cognition_frame"] = darya_cognition_frame.public_dict()
                        if darya_cognition_v2 is not None:
                            assistant_metadata["cognition_orchestration"] = darya_cognition_v2.public_dict()
                        if darya_learning_loop_result is not None:
                            assistant_metadata["learning_loop_domain_id"] = darya_learning_loop_result.get("domain_id")
                        elif is_tide and stage_action and route_for_turn is not None:
                            assistant_metadata["learning_loop_domain_id"] = route_for_turn.domain_id
                        if is_tide and universal_directive:
                            method_match = re.search(r"采用的教法：([^（\n]+)", universal_directive)
                            if method_match:
                                assistant_metadata["teaching_method_label"] = method_match.group(1).strip()
                        if darya_cognition_evaluation is not None:
                            assistant_metadata["cognition_evaluation"] = darya_cognition_evaluation
                        await append_chat_message(
                            db,
                            request_user_id,
                            request.thread_id,
                            "assistant",
                            full_text,
                            assistant_message_id,
                            metadata=assistant_metadata or None,
                        )
                        # Continuity Brain: the Librarian consolidates the
                        # timeline in its own session after the turn lands.
                        from services.darya_librarian import run_librarian_pass_detached
                        run_librarian_pass_detached(
                            db_conn._async_session_factory,
                            user_id=request_user_id,
                            thread_id=request.thread_id,
                        )
                except Exception as save_err:
                    logger.warning(f"[Quick] 保存会话失败（不影响返回）: {save_err}")

            if not saved_memory_action and not is_tide:
                candidate = detect_memory_candidate(
                    request.message, request.thread_id, message_id
                )
                if candidate:
                    yield f"data: {json.dumps({'type': 'memory_candidate', 'candidate': candidate}, ensure_ascii=False)}\n\n"
            complete_runtime_operation(request_user_id, request.thread_id)
            yield f"data: {json.dumps({'type': 'done', 'request_id': request_id, 'assistant_message_id': assistant_message_id, 'source_material_ids': source_material_ids})}\n\n"

        except Exception as exc:
            fail_runtime_operation(
                current_user_id, request.thread_id, "PROVIDER_CONNECTION_FAILED"
            )
            logger.error(
                "Quick chat failure request_id=%s type=%s",
                request_id,
                type(exc).__name__,
                exc_info=True,
            )
            public_error = _workspace_stream_error(request_id, "provider_stream")
            yield f"data: {json.dumps(public_error, ensure_ascii=False)}\n\n"

    async def _recorded_events():
        """边转发边累积，供同一 message_id 的重复投递重放。

        用 finally 收口：客户端中断（用户点「停止」）或异常时释放闸门，
        否则这个幂等键会一直停在 in_flight，直到 TTL 过期。
        """
        chunks: list[str] = []
        size = 0
        finished = False
        try:
            async for chunk in event_generator():
                if size <= _SSE_CACHE_LIMIT:
                    chunks.append(chunk)
                    size += len(chunk)
                yield chunk
            finished = True
        finally:
            if finished:
                await guard.complete(
                    user_id=current_user_id,
                    message_id=request.message_id,
                    payload="".join(chunks) if chunks and size <= _SSE_CACHE_LIMIT else None,
                )
            else:
                await guard.release(user_id=current_user_id, message_id=request.message_id)

    return StreamingResponse(
        _recorded_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
            "X-AI-Provider": str(settings.LLM_PROVIDER),
            "X-AI-Operation": "chat_connecting",
            "X-AI-State": "running",
        },
    )


# ===== 资料验证与考试创建辅助函数 =====


async def _validate_and_build_material_context(
    db: AsyncSession, user_id: int, material_ids: list
) -> tuple:
    """
    验证 material_ids 所有权，拼接资料内容上下文。

    返回: (source_material_ids, material_context)
    抛出: HTTPException 400/403/422
    """
    from database.models import LearningMaterial

    if not material_ids:
        return [], ""

    valid_ids = []
    context_parts = []
    max_chars = 30000
    total_chars = 0

    for mid in material_ids:
        result = await db.execute(
            select(LearningMaterial).where(LearningMaterial.id == mid)
        )
        material = result.scalar_one_or_none()

        if not material:
            raise HTTPException(status_code=400, detail=f"资料 ID={mid} 不存在")
        if material.user_id != user_id:
            raise HTTPException(status_code=422, detail=f"无权访问资料 ID={mid}")

        # 检查是否为当前有效资料（已解析、可用）
        if not material.is_current:
            raise HTTPException(
                status_code=422, detail=f"资料 ID={mid} 已被更新替换，请使用最新版本"
            )

        valid_ids.append(mid)

        # 拼接资料内容
        content = material.content
        if isinstance(content, dict):
            doc = content.get("document", {})
            if isinstance(doc, dict):
                title = doc.get("title", "")
                text = doc.get("content", "")
                if title or text:
                    chunk = f"【{title}】\n{text}\n\n"
                    if total_chars + len(chunk) <= max_chars:
                        context_parts.append(chunk)
                        total_chars += len(chunk)
                    else:
                        remaining = max_chars - total_chars
                        if remaining > 100:
                            context_parts.append(chunk[:remaining] + "…")
                        break

    logger.info(
        f"[MaterialContext] user={user_id} ids={valid_ids} "
        f"chars={total_chars}/{max_chars}"
    )
    return valid_ids, "".join(context_parts)


async def _create_exam_from_questions(
    db: AsyncSession, user_id: int, questions: list, source_material_ids: list = None
) -> int:
    """
    将已生成的题目直接保存为真实 ExamPaper，不调用LLM。

    返回: exam_id
    """
    from database.models import ExamPaper

    # 规范化题目格式
    normalized_questions = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        normalized = dict(q)
        if not normalized.get("id"):
            normalized["id"] = f"q{i + 1}"
        if not normalized.get("points"):
            normalized["points"] = 10
        normalized_questions.append(normalized)

    if not normalized_questions:
        raise HTTPException(status_code=400, detail="没有有效的题目可创建考试")

    total_score = sum(q.get("points", q.get("score", 10)) for q in normalized_questions)

    exam = ExamPaper(
        user_id=user_id,
        exam_type="quiz",
        chapter_ids=source_material_ids or ["material_quiz"],
        questions=normalized_questions,
        status="draft",
        total_score=total_score,
        time_limit_min=30,
    )
    db.add(exam)
    await db.commit()
    await db.refresh(exam)

    logger.info(
        f"[CreateExam] user={user_id} exam_id={exam.id} "
        f"questions={len(normalized_questions)} total_score={total_score}"
    )
    return exam.id
