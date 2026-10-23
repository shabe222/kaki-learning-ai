"""DARYA Phase 3.0 bounded Current Perspective and action policy.

The engine selects from context that upstream systems have already authorized
and ranked.  It does not load a whole profile, persist transient inference as
long-term memory, or expose private reasoning.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DaryaCurrentPerspectiveSnapshot


PerspectiveAction = Literal["ANSWER", "PREPARE", "SEARCH", "CLARIFY", "WARN"]

_VAGUE_QUALITY = re.compile(r"感觉.{0,8}(?:不对|怪|差)|说不出来|哪里不对|不太对|不够好|doesn.t feel right", re.I)
_PRESENTATION = re.compile(r"PPT|幻灯片|演示|slide|presentation", re.I)
_CORRECTION = re.compile(r"(?:不是|不对)[，,。\s]*(?:我)?(?:真正)?(?:担心|在意|要解决)(?:的)?(?:是)?[：:\s]*(.+)", re.I)
_HIGH_CONSEQUENCE = re.compile(r"辞职|签约|贷款|投资|投钱|买房|法律|起诉|手术|用药|退学|drop out|resign|invest|loan|surgery", re.I)
_MISSING_TARGET = re.compile(r"这个|那个|它|这家公司|这个选择|这件事|this (?:company|offer|choice)", re.I)
_BLACKBOARD_REQUEST = re.compile(r"放到黑板|写到黑板|画在黑板|put (?:it )?on the blackboard", re.I)
_WARN = re.compile(r"泄露|密钥|token|密码|不可逆|违法|诈骗|secret|credential|irreversible|fraud", re.I)
_PREPARE = re.compile(r"先做|草稿|初稿|V0|整理|改|分析|检查|给我一个|prepare|draft|revise|analy[sz]e", re.I)
_UNDERSPECIFIED_CAREER_LEARNING_GOAL = re.compile(
    r"(?:学|把).{0,24}(?:Python|编程).{0,24}(?:找工作|就业|求职|转行)", re.I,
)
_LEARNING_STAGE_SIGNAL = re.compile(
    r"零基础|刚开始|初学|基础程序|完整项目|独立项目|做过项目|工作经验|开发经验|"
    r"beginner|intermediate|advanced|project experience", re.I,
)


def capability_availability() -> dict[str, dict[str, Any]]:
    """The Tide/DARYA callable surface, not capabilities elsewhere in the app."""
    return {
        "web_search": {"available": True, "contract": "active_evidence_retrieval"},
        "artifact_edit": {"available": True, "contract": "artifact_action_workbench"},
        "voice_input": {"available": True, "contract": "voice_input_v1"},
        "document_ingestion": {"available": True, "contract": "document_context_v1"},
        "long_term_memory": {"available": True, "contract": "explicit_memory_v1"},
        "response_generation": {"available": True, "contract": "darya_chat_provider"},
        "handout_generation": {"available": True, "contract": "conversation_capability_action"},
        "assessment_generation": {"available": True, "contract": "tiered_assessment_v1"},
        "code_lab_generation": {"available": True, "contract": "personalized_code_lab_v1"},
        "presentation_generation": {"available": True, "contract": "workspace_presentation_v1"},
        "mindmap_generation": {"available": True, "contract": "workspace_mindmap_v1"},
        "storybook_generation": {"available": True, "contract": "darya_storybook_component"},
        "blackboard": {"available": True, "contract": "demonstration_surface_v1"},
        "image_generation": {"available": False, "contract": None},
        "video_generation": {"available": True, "contract": "workspace_video_job_v1"},
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plain(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _observed(value: Any, source: dict[str, Any]) -> dict[str, Any]:
    return {"value": value, "status": "OBSERVED", "source": source}


def _inferred(
    value: str,
    *,
    supporting_context: list[dict[str, Any]],
    confidence: float,
    consequence_if_wrong: Literal["LOW", "MEDIUM", "HIGH"],
    confirmation_needed: bool,
) -> dict[str, Any]:
    return {
        "value": value,
        "status": "INFERRED",
        "supporting_context": supporting_context[:4],
        "confidence": confidence,
        "freshness": "CURRENT_TURN",
        "consequence_if_wrong": consequence_if_wrong,
        "confirmation_needed": confirmation_needed,
    }


def _memory_refs(memories: Sequence[Any]) -> list[dict[str, Any]]:
    rows = []
    for item in memories:
        if isinstance(item, dict):
            row = item
        else:
            row = {
                "id": getattr(item, "id", None),
                "type": getattr(item, "memory_type", None),
                "topic": getattr(item, "topic", None),
                "content": getattr(item, "content", None),
                "epistemic_status": getattr(item, "epistemic_status", None),
            }
        rows.append({
            "id": row.get("id"),
            "type": row.get("type") or row.get("memory_type"),
            "topic": row.get("topic"),
            "content": _plain(row.get("content"), 300),
            "status": "OBSERVED",
            "source": {"type": "long_term_memory", "id": str(row.get("id"))},
        })
    return rows[:4]


def _field_value(context: dict[str, Any], key: str) -> Any:
    value = (context or {}).get(key)
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def build_current_perspective_from_model(
    *,
    cognition: Any,
    message: str,
    message_id: str,
    shared_perspective: dict[str, Any] | None = None,
    workspace_object: dict[str, Any] | None = None,
    artifact_context: dict[str, Any] | None = None,
    relevant_memories: Sequence[Any] = (),
    relevant_user_context: Sequence[dict[str, Any]] = (),
    evidence_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a model-owned semantic decision into the bounded perspective schema."""
    payload = cognition.public_dict() if hasattr(cognition, "public_dict") else cognition
    payload = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    help_state = payload.get("help_opportunity") if isinstance(payload.get("help_opportunity"), dict) else {}
    brief = payload.get("work_brief") if isinstance(payload.get("work_brief"), dict) else {}
    research = payload.get("research_task") if isinstance(payload.get("research_task"), dict) else {}
    interaction = payload.get("interaction") if isinstance(payload.get("interaction"), dict) else {}
    shared = copy.deepcopy(shared_perspective or {})
    workspace = copy.deepcopy(workspace_object or {})
    artifact = copy.deepcopy(artifact_context or {})
    source = {"type": "message", "id": message_id}
    explicit_need = _plain(message, 2000)
    goal = _plain(help_state.get("true_goal") or brief.get("goal") or _field_value(shared, "goal") or explicit_need, 300)
    stage = _plain(brief.get("stage") or _field_value(shared, "stage") or "当前轮", 120)
    obstacle = _plain(help_state.get("main_obstacle"), 300)
    unknown = _plain(help_state.get("highest_value_unknown"), 240)
    next_move = _plain(
        help_state.get("best_immediate_deliverable")
        or help_state.get("private_input_needed")
        or "直接回应用户当前需要。",
        500,
    )
    model_source = str(payload.get("source") or "")
    risk_level = str(interaction.get("risk_level") or "NONE").upper()
    mode = str(interaction.get("mode") or "ORIENT").upper()
    research_needed = research.get("needed") is True
    capability = str(payload.get("recommended_capability") or "NONE").upper()
    if model_source != "structured_model":
        action: PerspectiveAction = "ANSWER"
    elif risk_level == "HIGH":
        action = "WARN"
    elif research_needed:
        action = "SEARCH"
    elif mode == "CLARIFY" and help_state.get("private_input_needed"):
        action = "CLARIFY"
    elif capability != "NONE" or mode == "EXECUTE":
        action = "PREPARE"
    else:
        action = "ANSWER"
    if evidence_bundle:
        action = "ANSWER" if risk_level != "HIGH" else "WARN"

    likely_need = _inferred(
        goal,
        supporting_context=[{"type": "model_support", "id": message_id}],
        confidence=float(payload.get("confidence") or 0.0),
        consequence_if_wrong="HIGH" if risk_level == "HIGH" else "MEDIUM",
        confirmation_needed=action == "CLARIFY",
    ) if goal else None
    bottleneck = _inferred(
        obstacle,
        supporting_context=[{"type": "model_support", "id": message_id}],
        confidence=float(payload.get("confidence") or 0.0),
        consequence_if_wrong="HIGH" if risk_level == "HIGH" else "MEDIUM",
        confirmation_needed=action == "CLARIFY",
    ) if obstacle else None
    work_object = {
        key: workspace.get(key)
        for key in ("id", "title", "purpose", "status", "next_action")
        if workspace.get(key) not in (None, "", [])
    }
    if artifact:
        work_object["artifact"] = {
            key: artifact.get(key)
            for key in ("artifact_id", "version_id", "artifact_kind", "title")
            if artifact.get(key) not in (None, "", [])
        }
    sources = list((evidence_bundle or {}).get("sources") or [])
    memories = _memory_refs(relevant_memories)
    user_context = [copy.deepcopy(item) for item in relevant_user_context[:4]]
    return {
        "contract_version": "darya_current_perspective_v1",
        "semantic_authority": "cognition_orchestration_v2",
        "explicit_user_need": _observed(explicit_need, source),
        "likely_underlying_need": likely_need,
        "current_goal": _observed(goal, {"type": "model_support", "id": message_id}),
        "current_stage": _observed(stage, {"type": "model_support", "id": message_id}),
        "relevant_user_context": user_context,
        "relevant_memory": memories,
        "current_work_object": work_object,
        "important_external_state": {
            "search_required": research_needed,
            "evidence_status": (evidence_bundle or {}).get("status"),
        },
        "primary_bottleneck": bottleneck,
        "current_risks": [bottleneck] if action in {"WARN", "CLARIFY"} and bottleneck else [],
        "recommended_next_move": next_move,
        "uncertainty": _observed(unknown, source) if unknown else None,
        "confidence": float(payload.get("confidence") or 0.0),
        "evidence_references": [
            {"id": item.get("source_id"), "url": item.get("url"), "source_type": item.get("source_type")}
            for item in sources[:4]
            if isinstance(item, dict)
        ],
        "clarification_required": action == "CLARIFY",
        "clarification_question": _plain(help_state.get("private_input_needed"), 240) or None,
        "action": action,
        "capabilities": capability_availability(),
        "repair": None,
        "selected_context_counts": {
            "long_term_memory": len(memories),
            "user_context": len(user_context),
            "workspace_object": 1 if work_object else 0,
            "evidence": len(sources),
        },
        "goal_collaboration": None,
        "updated_at": _now(),
    }


def build_current_perspective(
    *,
    message: str,
    message_id: str,
    shared_perspective: dict[str, Any] | None = None,
    workspace_object: dict[str, Any] | None = None,
    artifact_context: dict[str, Any] | None = None,
    relevant_memories: Sequence[Any] = (),
    relevant_user_context: Sequence[dict[str, Any]] = (),
    search_required: bool = False,
    evidence_bundle: dict[str, Any] | None = None,
    previous_state: dict[str, Any] | None = None,
    goal_collaboration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the smallest useful WORLD + ME + NOW decision state."""
    text = _plain(message, 2000)
    source = {"type": "message", "id": message_id}
    shared = copy.deepcopy(shared_perspective or {})
    workspace = copy.deepcopy(workspace_object or {})
    artifact = copy.deepcopy(artifact_context or {})
    memories = _memory_refs(relevant_memories)
    user_context = [copy.deepcopy(item) for item in relevant_user_context[:4]]
    selected_context_text = _plain(
        json.dumps({"memory": memories, "user_context": user_context}, ensure_ascii=False),
        4000,
    )
    capabilities = capability_availability()

    correction = _CORRECTION.search(text)
    corrected_need = _plain(correction.group(1), 240).rstrip("。！？!?.,，") if correction else ""
    explicit_need = corrected_need or text
    goal = _plain(_field_value(shared, "goal") or workspace.get("purpose") or explicit_need, 300)
    stage = _plain(_field_value(shared, "stage") or workspace.get("status") or "当前判断", 120)
    work_object = {}
    if workspace:
        work_object = {
            key: workspace.get(key)
            for key in ("id", "title", "purpose", "status", "next_action")
            if workspace.get(key) not in (None, "", [])
        }
    if artifact:
        work_object["artifact"] = {
            key: artifact.get(key)
            for key in ("artifact_id", "version_id", "artifact_kind", "title")
            if artifact.get(key) not in (None, "", [])
        }

    support = [{"type": "message", "id": message_id, "excerpt": text[:180]}]
    likely_need = None
    bottleneck = None
    uncertainty = None
    clarification = None
    action: PerspectiveAction = "ANSWER"
    recommended = "直接回答用户当前明确提出的问题。"

    goal_plan = copy.deepcopy(goal_collaboration or {})
    if corrected_need:
        likely_need = _observed(corrected_need, source)
        bottleneck = _observed(corrected_need, source)
        recommended = f"按用户刚刚修正的重点推进：{corrected_need}"
        action = "PREPARE" if (_PREPARE.search(text) or work_object) else "ANSWER"
    elif goal_plan.get("active"):
        planned_goal = _plain(goal_plan.get("goal") or goal, 300)
        risk_level = str(goal_plan.get("risk_level") or "NONE").upper()
        priority = str(goal_plan.get("priority") or "GENERAL").upper()
        protected_need = _plain(goal_plan.get("protected_need"), 300)
        likely_need = _inferred(
            (
                f"当前必须先保护：{protected_need}"
                if risk_level in {"CONCERN", "URGENT"} and protected_need
                else f"用户正在推进：{planned_goal}"
            ),
            supporting_context=support,
            confidence=float(goal_plan.get("confidence") or .6),
            consequence_if_wrong="HIGH" if risk_level in {"CONCERN", "URGENT"} else "MEDIUM",
            confirmation_needed=False,
        )
        missing = list(goal_plan.get("missing_information") or [])[:3]
        if risk_level in {"CONCERN", "URGENT"}:
            bottleneck = _observed(
                protected_need or "当前处境存在需要先降低的现实风险。",
                source,
            )
        else:
            bottleneck = _observed(
                "仍需补齐会改变建议的关键信息：" + "、".join(str(item) for item in missing)
                if missing else "当前信息足以先推进一个可验证步骤。",
                source,
            )
        clarification = _plain(goal_plan.get("clarification_question"), 240) or None
        recommended = _plain(goal_plan.get("useful_first_move"), 500) or "先推进一个可验证的下一步。"
        action = (
            "WARN"
            if risk_level in {"CONCERN", "URGENT"} or priority == "SAFETY"
            else "SEARCH" if goal_plan.get("external_research_needed") else "PREPARE"
        )
    elif (
        _UNDERSPECIFIED_CAREER_LEARNING_GOAL.search(text)
        and not _LEARNING_STAGE_SIGNAL.search(f"{text} {selected_context_text}")
    ):
        likely_need = _inferred(
            "用户想把 Python 学习落到求职，但合适起点取决于当前实践阶段。",
            supporting_context=support,
            confidence=0.9,
            consequence_if_wrong="MEDIUM",
            confirmation_needed=True,
        )
        bottleneck = _observed("缺少会改变学习起点的当前项目经验。", source)
        clarification = "你现在更接近“能写基础程序”，还是已经独立做过一个完整项目？"
        uncertainty = _observed("当前实践阶段尚未确认。", source)
        recommended = clarification
        action = "CLARIFY"
    elif _BLACKBOARD_REQUEST.search(text) and not capabilities["blackboard"]["available"]:
        likely_need = _inferred(
            "用户需要一个可查看、可继续使用的可视化结果，而不只是口头说明。",
            supporting_context=support,
            confidence=0.88,
            consequence_if_wrong="LOW",
            confirmation_needed=False,
        )
        bottleneck = _observed("当前对话没有可调用的 Blackboard 能力。", {"type": "capability_state", "id": "blackboard"})
        recommended = "坦诚说明边界，并提供当前真实可用的 Sidecar/工作对象替代。"
        action = "ANSWER"
    elif _VAGUE_QUALITY.search(text):
        if _PRESENTATION.search(text) or artifact:
            value = "问题可能不是单纯“不好看”，而是信息主次没有立起来，用户一眼不知道先看哪里。"
            next_move = "先检查当前版本最抢注意力的三处，并给出一个可执行的 V0 调整顺序。"
        else:
            value = "当前瓶颈可能不是缺少更多想法，而是还没有找到最影响结果的那一个判断标准。"
            next_move = "先用现有内容找出最影响结果的一处，并准备一个可修正的 V0。"
        likely_need = _inferred(value, supporting_context=support, confidence=0.78, consequence_if_wrong="LOW", confirmation_needed=False)
        bottleneck = copy.deepcopy(likely_need)
        recommended = next_move
        action = "PREPARE"
    elif _WARN.search(text):
        likely_need = _inferred("先避免高风险或不可逆后果，再继续原任务。", supporting_context=support, confidence=0.9, consequence_if_wrong="HIGH", confirmation_needed=False)
        bottleneck = copy.deepcopy(likely_need)
        recommended = "先明确指出风险边界，再给安全的下一步。"
        action = "WARN"
    elif _HIGH_CONSEQUENCE.search(text) and _MISSING_TARGET.search(text) and len(text) < 80:
        likely_need = _inferred("用户需要的是一项高后果决定，而不是泛泛建议。", supporting_context=support, confidence=0.72, consequence_if_wrong="HIGH", confirmation_needed=True)
        bottleneck = _inferred("关键对象和约束不足，继续判断可能把建议导向错误行动。", supporting_context=support, confidence=0.66, consequence_if_wrong="HIGH", confirmation_needed=True)
        clarification = "你说的具体对象是什么，以及你现在必须守住的一个底线是什么？"
        uncertainty = _observed("缺少会实质改变决定的对象与底线。", source)
        recommended = clarification
        action = "CLARIFY"
    elif search_required:
        likely_need = _inferred("这个判断依赖当前外部事实，需要先核实世界状态再给个性化结论。", supporting_context=support, confidence=0.92, consequence_if_wrong="HIGH", confirmation_needed=False)
        bottleneck = _observed("当前外部证据尚未进入上下文。", {"type": "evidence_state", "id": "pending"})
        recommended = "调用现有 Active Evidence Retrieval，再结合相关用户背景给出结论。"
        action = "SEARCH"
    elif work_object and (_PREPARE.search(text) or _VAGUE_QUALITY.search(text)):
        likely_need = _inferred("用户希望 DARYA 先在现有工作对象上推进，而不是先收集一整套需求。", supporting_context=support, confidence=0.86, consequence_if_wrong="LOW", confirmation_needed=False)
        bottleneck = _observed("已有可工作的对象，继续追问会延迟可逆的 V0。", {"type": "workspace_object", "id": str(work_object.get("id") or "current")})
        recommended = "先基于当前对象准备一个可修改的 V0，再邀请用户纠偏。"
        action = "PREPARE"

    if evidence_bundle:
        status = evidence_bundle.get("status")
        sources = evidence_bundle.get("sources") or []
        goal_risk = str(goal_plan.get("risk_level") or "NONE").upper()
        if goal_risk not in {"CONCERN", "URGENT"}:
            recommended = "结合已选中的相关用户背景与本轮外部证据，给出带边界的个性化结论。"
            action = "ANSWER"
        bottleneck = _observed(
            "外部证据已检索；结论必须受证据状态约束。",
            {"type": "evidence_bundle", "id": evidence_bundle.get("query"), "status": status},
        )
    else:
        sources = []

    previous_inference = (previous_state or {}).get("primary_bottleneck")
    repair = None
    if corrected_need and isinstance(previous_inference, dict):
        repair = {
            "status": "CORRECTED_CURRENT_PERSPECTIVE",
            "previous_value": previous_inference.get("value"),
            "replacement": corrected_need,
            "persisted_to_long_term_memory": False,
        }

    return {
        "contract_version": "darya_current_perspective_v1",
        "explicit_user_need": _observed(explicit_need, source),
        "likely_underlying_need": likely_need,
        "current_goal": _observed(goal, {"type": "selected_context", "id": "goal"}),
        "current_stage": _observed(stage, {"type": "selected_context", "id": "stage"}),
        "relevant_user_context": user_context,
        "relevant_memory": memories,
        "current_work_object": work_object,
        "important_external_state": {
            "search_required": search_required,
            "evidence_status": (evidence_bundle or {}).get("status"),
        },
        "primary_bottleneck": bottleneck,
        "current_risks": [bottleneck] if action in {"WARN", "CLARIFY"} and bottleneck else [],
        "recommended_next_move": recommended,
        "uncertainty": uncertainty,
        "confidence": (likely_need or {}).get("confidence", 1.0) if likely_need else 1.0,
        "evidence_references": [
            {"id": item.get("source_id"), "url": item.get("url"), "source_type": item.get("source_type")}
            for item in sources[:4]
        ],
        "clarification_required": action == "CLARIFY",
        "clarification_question": clarification,
        "action": action,
        "capabilities": capabilities,
        "repair": repair,
        "selected_context_counts": {
            "long_term_memory": len(memories),
            "user_context": len(user_context),
            "workspace_object": 1 if work_object else 0,
            "evidence": len(sources),
        },
        "goal_collaboration": goal_plan if goal_plan.get("active") else None,
        "updated_at": _now(),
    }


async def get_latest_perspective(
    db: AsyncSession, *, user_id: int, thread_id: str,
) -> DaryaCurrentPerspectiveSnapshot | None:
    return (await db.execute(
        select(DaryaCurrentPerspectiveSnapshot)
        .where(
            DaryaCurrentPerspectiveSnapshot.user_id == user_id,
            DaryaCurrentPerspectiveSnapshot.thread_id == thread_id,
        )
        .order_by(desc(DaryaCurrentPerspectiveSnapshot.revision))
        .limit(1)
    )).scalar_one_or_none()


async def persist_current_perspective(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    message_id: str,
    state: dict[str, Any],
) -> DaryaCurrentPerspectiveSnapshot:
    current = await get_latest_perspective(db, user_id=user_id, thread_id=thread_id)
    row = DaryaCurrentPerspectiveSnapshot(
        user_id=user_id,
        thread_id=thread_id,
        revision=(current.revision + 1) if current else 1,
        parent_snapshot_id=current.id if current else None,
        source_message_id=message_id,
        state=copy.deepcopy(state),
    )
    db.add(row)
    await db.flush()
    return row


def public_perspective(snapshot: DaryaCurrentPerspectiveSnapshot | None, *, thread_id: str) -> dict[str, Any]:
    if snapshot is None:
        return {"thread_id": thread_id, "revision": 0, "state": {}, "updated_at": None}
    return {
        "thread_id": snapshot.thread_id,
        "revision": snapshot.revision,
        "state": copy.deepcopy(snapshot.state or {}),
        "updated_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


def build_perspective_directive(state: dict[str, Any]) -> str:
    """Bounded provider guidance; never asks the model to reveal reasoning."""
    selected = {
        key: state.get(key)
        for key in (
            "explicit_user_need", "likely_underlying_need", "primary_bottleneck",
            "recommended_next_move", "uncertainty", "action", "clarification_question",
            "relevant_memory", "relevant_user_context", "important_external_state",
            "repair",
            "goal_collaboration",
        )
    }
    return (
        "DARYA 当前视角（仅用于本轮决策，不向用户展示字段或推理过程）：\n"
        + json.dumps(selected, ensure_ascii=False)
        + "\nOBSERVED 可以作为已知上下文；INFERRED 必须用‘我暂时判断/更可能/看起来’等校准语言，不能说成用户事实。"
        "只使用 relevant_memory 中列出的记忆，不能补入其他记忆。"
        "若 repair.status=CORRECTED_CURRENT_PERSPECTIVE，直接按用户的新重点转向；"
        "不要说‘其实你之前真正担心的就是这个’，不要把纠正包装成自己早已看穿。"
        "没有证据支持的示例数字必须写成 XX、占位值或明确的假设，不能伪装成真实市场数据。"
        "若 action=PREPARE，先产出可用 V0；若 action=CLARIFY，只问 clarification_question；"
        "若 action=SEARCH，结论必须等待并服从证据契约。"
        "当前 Tide 对话的 Blackboard 不可调用：不得声称已经放到黑板、画到黑板或操作黑板；"
        "用户要求时，坦诚说明并改用真实可用的 Sidecar/工作对象。"
    )


def enforce_capability_boundaries(raw: str, state: dict[str, Any]) -> str:
    explicit = ((state.get("explicit_user_need") or {}).get("value") or "")
    if _BLACKBOARD_REQUEST.search(str(explicit)) and not state.get("capabilities", {}).get("blackboard", {}).get("available"):
        return (
            "[tide_center]\n"
            "当前这段对话还没有可调用的黑板，我不能假装操作成功。"
            "我可以把内容整理成右侧可继续修改的工作对象，或者先在这里给你一版清楚的结构。\n"
            "[/tide_center]"
        )
    return raw
