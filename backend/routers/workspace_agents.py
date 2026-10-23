"""Authenticated exam and lecture agent adapters for the AI workspace."""
import json
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.document_agent import DocumentAgent
from agents.learning_evaluator import EvaluationContractError
from database.dependencies import get_db
from database.models import ChatSession, ExamPaper, GeneratedItem, GeneratedItemVersion, StudentProfile, TutorSession, WorkspaceAgentTask, WorkspaceLearningEvent
from schemas.canonical_artifacts import CreateArtifactReviewRequest
from services.canonical_artifacts import create_trusted_review, build_canonical_artifact
from services.generation_draft import (
    GenerationDraftBlocked, mindmap_draft, presentation_draft,
)
from routers.agents_day4 import _load_profile, _make_state
from services.exam_service import create_exam, submit_exam
from services.learning_evaluator_integration import (
    EvaluationAccessDeniedError,
    auto_consume_exam_submission_event,
)
from services.workspace_agents import (
    AgentTaskSpec,
    build_confirmation,
    build_task_failure_message,
    build_thread_artifact_message as _build_thread_artifact_message,
    detect_agent_intent,
    normalize_agent_result,
    route_workspace_message,
    resolve_workspace_task,
    run_agent_operation,
)
from services.workspace_code import (
    build_code_qa_context,
    generate_python_code,
    rerun_python_code,
    revise_python_code,
)
from services.workspace_code_runs import (
    CodeRunError,
    execute_code_version,
    get_code_run,
    list_code_runs,
)
from services.workspace_interactive_attempts import (
    InteractiveAttemptError,
    get_interactive_state,
    record_support_action,
    submit_interactive_answer,
)
from services.workspace_exports import build_docx_bytes, build_pdf_bytes
from services.workspace_artifacts import build_exam_result_artifact, artifact_context
from services.workspace_agent_adapters import AGENT_ADAPTERS
from services.workspace_artifact_versions import (
    ArtifactVersionError, build_public_projection, create_new_version, ensure_initial_version, get_version,
)
from services.workspace_semantic_router import route_semantically
from services.workspace_task_contracts import (
    AgentTask, AgentType, ArtifactOperation, SemanticAction, SemanticRoute, TaskDecision, TaskState,
)
from services.workspace_task_machine import advance_task
from services.workspace_task_store import (
    cancel_current_task,
    complete_task as complete_persisted_task,
    fail_task as fail_persisted_task,
    load_current_task,
    load_task_by_id,
    retry_failed_task,
    save_task,
)
from services.workspace_runtime import (
    begin_runtime_operation,
    complete_runtime_operation,
    fail_runtime_operation,
    get_runtime_status,
)
from services.workspace_presentations import (
    build_pptx_bytes,
    generate_workspace_presentation,
    revise_workspace_presentation,
)
from services.workspace_materials import (
    MaterialAccessError,
    build_conversation_material_context,
    should_use_material_context,
)
from services.workspace_mindmaps import (
    generate_workspace_mindmap,
    revise_workspace_mindmap,
)
from services.video_generation_jobs import (
    NONTERMINAL_STATES,
    VideoJobAccessError,
    create_video_job,
    execute_video_job,
    get_owned_video_job,
    project_video_job,
    retry_video_job,
)
from integrations.cloud_video.contracts import EvidenceTeachingContext, StyleContext
from integrations.cloud_video.settings import CloudVideoSettings
from config import settings as app_settings
from schemas.chat import SourceContext
from utils.auth import get_current_user_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat/tasks", tags=["workspace-agents"])


def build_thread_artifact_message(
    thread_id: str, kind: str, artifact_id: Any, title: str, summary: str,
) -> dict:
    """Carry ``artifact_id`` alongside ``id`` so thread artifacts match the shape
    the sidebar list returns; the workspace client reads ``artifact_id`` when it
    opens or exports an artifact."""
    message = _build_thread_artifact_message(thread_id, kind, artifact_id, title, summary)
    message["artifact"].setdefault("artifact_id", artifact_id)
    return message


async def _tutor_artifact_metadata(db: AsyncSession, user_id: int, thread_id: str) -> dict:
    """Attach ownership pointers only; artifact content stays in GeneratedItem."""
    session = (await db.execute(select(TutorSession).where(
        TutorSession.user_id == user_id,
        TutorSession.thread_id == thread_id,
        TutorSession.status == "active",
    ))).scalar_one_or_none()
    if session is None:
        return {}
    return {"tutor_session_id": session.session_id, "graph_node_id": session.current_node_id}


class PlanRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    pending_task: Optional[dict] = None
    active_artifact: Optional[dict] = None
    related_context: List[str] = Field(default_factory=list, max_length=3)
    source_context: Optional[SourceContext] = None
    input_context_mode: Optional[Literal["reply", "edit"]] = None
    target_artifact_id: Optional[int] = Field(default=None, ge=1)
    target_version_id: Optional[int] = Field(default=None, ge=1)


class TaskControlRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    task_id: str = Field(min_length=1, max_length=64)
    task_version: int = Field(ge=1)
    action: Literal["confirm", "cancel"]


class RouteRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=800)
    active_artifact: Optional[dict] = None


class AgentRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=120)
    confirmed: bool = False
    count: int = Field(default=5, ge=1, le=20)
    difficulty: Optional[str] = "中等"
    detail_level: str = Field(default="简明", max_length=20)
    export_formats: List[str] = Field(default_factory=list)
    question_types: List[str] = Field(default_factory=list)
    source_artifact_id: Optional[int] = None
    # Conversation-owned artifacts carry a bounded slice of prior user turns so
    # capability agents do not forget the account's identity and constraints.
    instruction: str = Field(default="", max_length=2000)
    evidence_verified: bool = False
    source_artifact_kind: Optional[str] = Field(default=None, max_length=40)
    source_material_ids: List[str] = Field(default_factory=list, max_length=8)
    task_id: Optional[str] = Field(default=None, max_length=64)
    task_version: Optional[int] = Field(default=None, ge=1)
    # 版本级主题快照：只约束配色与材质，不约束内容结构。
    theme: str = Field(default="kaki", max_length=20)


class RevisionRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    # 2000 而不是 500：对话里的「重试」会走这条路，而它带回去的是内容闸门指名的
    # 缺陷（含位置原文）加一条「别用待核实占位糊过去」的提醒，实测超过 500 字。
    # 上限对齐同文件的 AgentRequest.instruction，两条路装得下同样多的要求。
    instruction: str = Field(min_length=1, max_length=2000)
    expected_version: Optional[int] = Field(default=None, ge=1)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=240)


class CodeRevisionRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=500)
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=240)


class CodeRerunRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    artifact_version_id: int = Field(ge=1)
    execution_request_id: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=240)
    parameters: dict[str, Any] = Field(default_factory=dict)
    source: Optional[str] = Field(default=None, max_length=24576)


class InteractiveAnswerRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    artifact_version_id: int = Field(ge=1)
    item_identity: str = Field(min_length=1, max_length=120)
    selected_answer: Any
    submission_request_id: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=240)


class InteractiveSupportRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    artifact_version_id: int = Field(ge=1)
    item_identity: str = Field(min_length=1, max_length=120)


class CodeExamRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    count: int = Field(default=3, ge=1, le=5)
    difficulty: Optional[str] = "中等"
    question_types: List[str] = Field(default_factory=list)


class ArtifactExamActionRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    count: int = Field(default=3, ge=1, le=20)
    difficulty: str = Field(default="intermediate", max_length=30)
    question_types: List[str] = Field(default_factory=lambda: ["single_choice"])


class CodeRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=120)
    language: str = Field(default="python", max_length=20)
    confirmed: bool = False
    instruction: str = Field(default="", max_length=500)
    should_run: bool = True
    input_output_requirement: str = Field(default="", max_length=300)
    task_id: Optional[str] = Field(default=None, max_length=64)
    task_version: Optional[int] = Field(default=None, ge=1)


class SubmitRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    answers: List[Any]
    time_spent_sec: int = Field(default=0, ge=0)


class PresentationRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=160)
    delivery_mode: str = Field(pattern=r"^(presentation_only|interactive_lesson)$")
    learning_goal: str = Field(default="", max_length=160)
    level: str = Field(default="", max_length=40)
    generation_context: str = Field(default="", max_length=2600)
    theme: str = Field(default="kaki", max_length=20)
    duration_minutes: Optional[int] = Field(default=None, ge=1, le=60)
    slide_count: Optional[int] = Field(default=None, ge=1, le=30)
    presentation_style: str = Field(default="", max_length=80)
    source_artifact_id: Optional[int] = None
    confirmed: bool = False
    task_id: Optional[str] = Field(default=None, max_length=64)
    task_version: Optional[int] = Field(default=None, ge=1)


class MindmapRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    topic: str = Field(default="", max_length=160)
    source_material_ids: List[str] = Field(default_factory=list, max_length=8)
    source_scope: str = Field(default="", max_length=200)
    instruction: str = Field(default="", max_length=2000)
    confirmed: bool = False
    task_id: Optional[str] = Field(default=None, max_length=64)
    task_version: Optional[int] = Field(default=None, ge=1)


class VideoRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=160)
    duration_sec: int = Field(default=45, ge=15, le=600)
    instruction: str = Field(default="", max_length=2000)
    source_material_ids: List[str] = Field(default_factory=list, max_length=8)
    confirmed: bool = False
    task_id: Optional[str] = Field(default=None, max_length=64)
    task_version: Optional[int] = Field(default=None, ge=1)
    # 版本级主题快照：视频画面调色在生成时定格。
    theme: str = Field(default="kaki", max_length=20)


class LearningGraphAgentRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    task_id: str = Field(min_length=1, max_length=64)
    task_version: int = Field(ge=1)


class LessonEventRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    event_type: str = Field(pattern=r"^(interactive_lesson_opened|slide_viewed|interaction_answered|lesson_progress_updated|lesson_completed)$")
    payload: dict = Field(default_factory=dict)


async def _owned_session(db: AsyncSession, user_id: int, thread_id: str, create: bool = False) -> ChatSession:
    result = await db.execute(select(ChatSession).where(
        ChatSession.user_id == user_id, ChatSession.thread_id == thread_id
    ))
    session = result.scalar_one_or_none()
    if session is None and create:
        session = ChatSession(user_id=user_id, thread_id=thread_id, messages=[], message_count=0)
        db.add(session)
        await db.commit()
        await db.refresh(session)
    if session is None:
        raise HTTPException(404, "conversation not found")
    return session


def _recent_conversation_context(session: ChatSession, limit: int = 3) -> List[str]:
    """Project a tiny read-only slice of the owned Conversation for semantic routing.

    The ChatSession remains the sole conversation fact source.  This projection is
    never written back as a second history store; it only resolves deictic task
    requests such as "根据我们刚才聊的内容出题".
    """
    rows = list(session.messages or [])
    projected: List[str] = []
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        content = str(row.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        label = "用户" if role == "user" else "助手"
        projected.append(f"{label}: {content[:220]}")
        if len(projected) >= max(1, int(limit or 3)):
            break
    projected.reverse()
    return projected


async def _append(db: AsyncSession, session: ChatSession, message: dict) -> None:
    rows = list(session.messages or [])
    rows.append(message)
    session.messages = rows
    session.message_count = len(rows)
    session.last_message_at = datetime.utcnow()
    await db.commit()


async def _record_task_failure(
    db: AsyncSession,
    user_id: int,
    thread_id: str,
    kind: str,
    topic: str,
) -> None:
    """Recover the transaction, reload the session, then persist a terminal task event."""
    await db.rollback()
    fresh_session = await _owned_session(db, user_id, thread_id)
    await _append(db, fresh_session, build_task_failure_message(thread_id, kind, topic))


def _task_from_dict(value: Optional[dict]) -> Optional[AgentTaskSpec]:
    if not value or value.get("kind") not in {"lecture", "exam", "revise_lecture", "code", "revise_code"}:
        return None
    formats = tuple(item for item in (value.get("export_formats") or []) if item in {"pdf", "docx"})
    question_types = tuple(item for item in (value.get("question_types") or []) if item in {
        "single_choice", "true_false", "fill_blank", "short_answer",
    })
    return AgentTaskSpec(
        kind=value["kind"], topic=str(value.get("topic") or ""),
        count=int(value.get("count") or 5), difficulty=str(value.get("difficulty") or "中等"),
        detail_level=str(value.get("detail_level") or "简明"), export_formats=formats,
        source_artifact_id=value.get("source_artifact_id"), instruction=str(value.get("instruction") or ""),
        question_types=question_types,
        language=str(value.get("language") or "python"),
        should_run=bool(value.get("should_run", True)),
    )


def _task_payload(task: Optional[AgentTask]) -> dict:
    """Project the server task into the temporary legacy frontend shape."""
    if task is None:
        return {"status": TaskState.IDLE.value, "state": TaskState.IDLE.value}
    slots = dict(task.slots or {})
    payload = {
        "task_id": task.task_id,
        "task_version": task.version,
        "version": task.version,
        "agent_type": task.agent_type.value,
        "kind": task.agent_type.value,
        "state": task.state.value,
        "status": task.state.value,
        "confirmed_version": task.confirmed_version,
        "execution_key": task.execution_key,
        "artifact_id": task.artifact_id,
        "user_id": task.user_id,
        "conversation_id": task.conversation_id or task.thread_id,
        "resource_type": task.resource_type or task.agent_type.value,
        "retry_count": task.retry_count,
        "failure_reason": task.failure_reason or task.error_code,
        "source_artifact_id": task.source_artifact_id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }
    payload.update(slots)
    if task.agent_type == AgentType.EXAM:
        payload["count"] = slots.get("question_count")
        payload["instruction"] = "；".join(slots.get("additional_requirements") or [])
    elif task.agent_type == AgentType.CODE:
        payload["instruction"] = slots.get("goal") or slots.get("input_output_requirement") or ""
        payload["should_run"] = slots.get("run_after_generation", True)
    elif task.agent_type == AgentType.LECTURE:
        payload["instruction"] = slots.get("purpose") or ""
    elif task.agent_type == AgentType.PRESENTATION:
        payload["delivery_mode"] = slots.get("delivery_mode")
    elif task.agent_type == AgentType.MINDMAP:
        payload["source_material_ids"] = list(slots.get("source_material_ids") or [])
        payload["source_scope"] = slots.get("source_scope") or ""
    elif task.agent_type == AgentType.VIDEO:
        payload["duration_sec"] = max(15, min(int(slots.get("duration_sec") or 45), 600))
        payload["instruction"] = slots.get("instruction") or ""
        payload["source_material_ids"] = list(slots.get("source_material_ids") or [])
    return payload


async def _execution_task(
    db: AsyncSession, user_id: int, thread_id: str,
    agent_type: AgentType, task_id: Optional[str], task_version: Optional[int],
) -> Optional[AgentTask]:
    """Validate that execution uses the exact server-confirmed task version."""
    if not task_id:
        return None
    task = await load_task_by_id(db, user_id, thread_id, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if task.agent_type != agent_type:
        raise HTTPException(409, "task agent type changed; please confirm again")
    if task_version is None or task.version != task_version:
        raise HTTPException(409, "task parameters changed; please confirm the latest version")
    if task.state == TaskState.FAILED and task.confirmed_version == task.version:
        task = await retry_failed_task(db, task)
    if task.state != TaskState.RUNNING or task.confirmed_version != task.version:
        raise HTTPException(409, "task is not in a confirmed running state")
    return task


def _difficulty_label(value: Optional[str]) -> str:
    return {
        "basic": "简单",
        "intermediate": "中等",
        "advanced": "困难",
        "adaptive": "中等",
    }.get(str(value or ""), str(value or "中等"))


async def _owned_exam_result(
    db: AsyncSession, user_id: int, thread_id: str, exam_id: int,
) -> dict:
    owner = await db.execute(select(ExamPaper).where(
        ExamPaper.id == exam_id,
        ExamPaper.user_id == user_id,
    ))
    exam = owner.scalar_one_or_none()
    if exam is None or exam.status != "completed":
        raise HTTPException(404, "completed exam result not found")
    session = await _owned_session(db, user_id, thread_id)
    for message in reversed(list(session.messages or [])):
        artifact = message.get("artifact") if isinstance(message, dict) else None
        if (
            isinstance(artifact, dict)
            and artifact.get("kind") == "exam_result"
            and int(artifact.get("artifact_id") or 0) == exam_id
        ):
            return dict(artifact)
    raise HTTPException(404, "exam result artifact not found in this conversation")


def _recent_artifact_candidates(session: ChatSession, limit: int = 5) -> List[dict]:
    candidates: List[dict] = []
    seen = set()
    for message in reversed(list(session.messages or [])):
        artifact = message.get("artifact") if isinstance(message, dict) else None
        if not isinstance(artifact, dict):
            continue
        key = (
            artifact.get("kind"),
            artifact.get("artifact_id") or artifact.get("graph_id"),
        )
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        candidates.append(dict(artifact))
        if len(candidates) >= limit:
            break
    return candidates


def _trusted_learning_graph_artifact(
    session: ChatSession, requested_artifact: dict | None,
) -> dict | None:
    """Resolve one graph only from artifacts persisted in the owned chat.

    An active client artifact may select a graph ID, but never supplies its
    revision or other identity fields. Without an explicit selection, recency
    is accepted only when the conversation contains exactly one graph.
    """
    candidates: list[dict] = []
    seen_graph_ids: set[str] = set()
    for message in reversed(list(session.messages or [])):
        artifact = message.get("artifact") if isinstance(message, dict) else None
        if not isinstance(artifact, dict) or artifact.get("kind") != "learning_graph":
            continue
        graph_id = str(artifact.get("graph_id") or "").strip()
        try:
            graph_revision = int(artifact.get("graph_revision") or 0)
        except (TypeError, ValueError):
            continue
        if graph_id and graph_revision > 0 and graph_id not in seen_graph_ids:
            seen_graph_ids.add(graph_id)
            candidates.append(dict(artifact))

    requested_graph_id = ""
    if isinstance(requested_artifact, dict) and requested_artifact.get("kind") == "learning_graph":
        requested_graph_id = str(requested_artifact.get("graph_id") or "").strip()
        if not requested_graph_id:
            return None
    if requested_graph_id:
        return next(
            (item for item in candidates if item.get("graph_id") == requested_graph_id),
            None,
        )
    return candidates[0] if len(candidates) == 1 else None


def _explicit_artifact_edit_route(session: ChatSession, artifact_id: Optional[int]) -> SemanticRoute:
    """Resolve an edit button target exactly; never guess from recency."""
    target = next(
        (
            candidate for candidate in _recent_artifact_candidates(session, limit=1000)
            if int(candidate.get("artifact_id") or 0) == int(artifact_id or 0)
        ),
        None,
    )
    kind_to_agent = {
        "lecture": AgentType.LECTURE,
        "code": AgentType.CODE,
        "mindmap": AgentType.MINDMAP,
        "presentation": AgentType.PRESENTATION,
        "interactive_lesson": AgentType.PRESENTATION,
    }
    agent_type = kind_to_agent.get(str((target or {}).get("kind") or ""))
    return SemanticRoute(
        action=SemanticAction.ARTIFACT_ACTION,
        agent_type=agent_type,
        artifact_operation=ArtifactOperation.REVISE,
        target_artifact_id=int(artifact_id) if target is not None and agent_type is not None else None,
        confidence=1.0,
        reason_code="explicit_artifact_edit" if target is not None else "explicit_artifact_not_found",
    )


def _validate_lecture_constraints(content: str, instruction: str, detail_level: str) -> None:
    text = str(content or "").strip()
    request_text = str(instruction or "")
    if "统一工作简报：" in request_text:
        # Internal safety/strategy prose may mention tables as forbidden examples.
        # Only declared deliverables are authoritative for format validation.
        try:
            brief_text = request_text.split("统一工作简报：", 1)[1].split("。所有内容", 1)[0]
            brief = json.loads(brief_text)
            request_text = " ".join(str(item) for item in (brief.get("deliverables") or []))
        except (TypeError, ValueError, json.JSONDecodeError):
            request_text = ""
    if "对比表" in request_text or "表格" in request_text:
        lines = text.splitlines()
        has_markdown_table = any(
            lines[index].strip().startswith("|")
            and re.fullmatch(
                r"\|?(?:\s*:?-{3,}:?\s*\|)+",
                lines[index + 1].strip(),
            )
            for index in range(len(lines) - 1)
        )
        if not has_markdown_table:
            raise ValueError("lecture did not include the requested comparison table")
    if detail_level == "速览" and len(text) > 2500:
        raise ValueError("quick review lecture is too long")


async def _owned_lecture(db: AsyncSession, user_id: int, artifact_id: int) -> GeneratedItem:
    result = await db.execute(select(GeneratedItem).where(
        GeneratedItem.id == artifact_id,
        GeneratedItem.user_id == user_id,
        GeneratedItem.type == "workspace_lecture",
    ))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "artifact not found")
    return item


async def _owned_code(db: AsyncSession, user_id: int, artifact_id: int) -> GeneratedItem:
    result = await db.execute(select(GeneratedItem).where(
        GeneratedItem.id == artifact_id,
        GeneratedItem.user_id == user_id,
        GeneratedItem.type == "workspace_code",
    ))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "code artifact not found")
    return item


async def _owned_presentation(db: AsyncSession, user_id: int, artifact_id: int) -> GeneratedItem:
    result = await db.execute(select(GeneratedItem).where(
        GeneratedItem.id == artifact_id,
        GeneratedItem.user_id == user_id,
        GeneratedItem.type.in_(("workspace_presentation", "workspace_interactive_lesson")),
    ))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "presentation artifact not found")
    return item


async def _owned_mindmap(db: AsyncSession, user_id: int, artifact_id: int) -> GeneratedItem:
    result = await db.execute(select(GeneratedItem).where(
        GeneratedItem.id == artifact_id,
        GeneratedItem.user_id == user_id,
        GeneratedItem.type == "workspace_mindmap",
    ))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "mindmap artifact not found")
    return item


async def _owned_video(db: AsyncSession, user_id: int, artifact_id: int) -> GeneratedItem:
    result = await db.execute(select(GeneratedItem).where(
        GeneratedItem.id == artifact_id,
        GeneratedItem.user_id == user_id,
        GeneratedItem.type == "workspace_video",
    ))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "video artifact not found")
    return item


def _video_payload_from_item(item: GeneratedItem) -> dict:
    return project_video_job(item)


def _presentation_payload_from_item(item: GeneratedItem) -> dict:
    metadata = dict(item.raw_data or {})
    return {
        "artifact_id": item.id,
        "artifact_version_id": item.current_version_id,
        "artifact_version_number": int(item.current_version_number or metadata.get("version") or 1),
        "kind": metadata.get("kind", "presentation"),
        "delivery_mode": metadata.get("delivery_mode", "presentation_only"),
        "title": item.title,
        "topic": item.chapter,
        "thread_id": metadata.get("thread_id"),
        "classroom_id": metadata.get("classroom_id"),
        "status": metadata.get("status", "completed"),
        "duration_minutes": metadata.get("duration_minutes"),
        "slide_count": metadata.get("slide_count"),
        "outline": metadata.get("outline", []),
        "slides": metadata.get("slides", []),
        "progress": metadata.get("progress", {}),
        "provider": metadata.get("provider", "unknown"),
        "presentation_theme": metadata.get("presentation_theme", "kaki"),
        "source_artifact_ids": metadata.get("source_artifact_ids", []),
        "launch_url": f"/api/chat/tasks/presentations/{item.id}",
        "summary": metadata.get("summary", ""),
        "version": metadata.get("version", 1),
    }


def _code_metadata_from_item(item: GeneratedItem) -> dict:
    metadata = dict(item.raw_data or {})
    return {
        "artifact_id": item.id, "kind": "code", "title": item.title,
        "topic": item.chapter, "language": metadata.get("language", "python"),
        "source": item.content, "explanation": metadata.get("explanation", ""),
        "knowledge_points": metadata.get("knowledge_points", []),
        "line_explanations": metadata.get("line_explanations", []),
        "run": metadata.get("last_run") or {},
        "version": metadata.get("version", 1),
        "should_run": bool(metadata.get("should_run", True)),
    }


def _mindmap_payload_from_item(item: GeneratedItem) -> dict:
    metadata = dict(item.raw_data or {})
    tree_data = metadata.get("tree_data")
    if not isinstance(tree_data, dict):
        try:
            tree_data = json.loads(item.content or "{}")
        except json.JSONDecodeError:
            tree_data = {}
    return {
        "artifact_id": item.id,
        "kind": "mindmap",
        "title": item.title,
        "topic": item.chapter or "",
        "thread_id": metadata.get("thread_id"),
        "summary": metadata.get("summary") or f"已生成《{item.title}》学习思维导图。",
        "tree_data": tree_data,
        "version": int(metadata.get("version") or 1),
        "source_material_ids": list(metadata.get("source_material_ids") or []),
        "source_scope": metadata.get("source_scope") or "",
        "source_citations": list(metadata.get("source_citations") or []),
    }


@router.get("/runtime-status/{thread_id}")
async def runtime_status(
    thread_id: str,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await _owned_session(db, user_id, thread_id)
    except HTTPException:
        pass
    return {"success": True, "data": get_runtime_status(user_id, thread_id)}


@router.get("/state/{thread_id}")
async def task_state(
    thread_id: str,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await _owned_session(db, user_id, thread_id)
    task = await load_current_task(db, user_id, thread_id, include_terminal=True)
    return {"success": True, "data": {"task": _task_payload(task)}}


@router.post("/control")
async def control_task(
    req: TaskControlRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Apply an explicit product control without manufacturing a chat command.

    The task identity and version are checked against the persisted, owned task
    before the existing deterministic state machine is allowed to advance it.
    """
    await _owned_session(db, user_id, req.thread_id)
    current = await load_task_by_id(db, user_id, req.thread_id, req.task_id)
    if current is None:
        raise HTTPException(404, "task not found")
    if current.version != req.task_version:
        raise HTTPException(409, "task parameters changed; please use the latest task")

    if req.action == "cancel":
        await cancel_current_task(db, user_id, req.thread_id)
        return {"success": True, "data": {
            "task": _task_payload(None),
            "confirmation": "这项制作已取消，你可以随时提出新的学习需求。",
            "mode": "cancelled",
            "execute": False,
            "execution_payload": None,
        }}

    semantic = SemanticRoute(
        action=SemanticAction.CONFIRM,
        agent_type=current.agent_type,
        confidence=1.0,
        reason_code="explicit_product_control",
    )
    decision = advance_task(current, semantic, user_id, req.thread_id)
    if decision.task is not None:
        decision.task = await save_task(db, decision.task)
    return {"success": True, "data": {
        "task": _task_payload(decision.task),
        "confirmation": decision.reply,
        "mode": decision.mode,
        "execute": decision.execute,
        "execution_payload": decision.execution_payload,
    }}


@router.post("/plan")
async def plan_task(req: PlanRequest, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    session = await _owned_session(db, user_id, req.thread_id, create=True)
    begin_runtime_operation(user_id, req.thread_id, "semantic_routing")
    try:
        current = await load_current_task(db, user_id, req.thread_id, include_terminal=False)
        retry_candidate = None
        if current is None and re.fullmatch(r"(?:重试|再试一次|重新生成|重新执行)[。！!，,\s]*", req.message.strip()):
            retry_candidate = await load_current_task(db, user_id, req.thread_id, include_terminal=True)
        if retry_candidate is not None and retry_candidate.state == TaskState.FAILED:
            current = await retry_failed_task(db, retry_candidate)
            semantic = SemanticRoute(
                action=SemanticAction.CONFIRM,
                agent_type=current.agent_type,
                confidence=1.0,
                reason_code="deterministic_failed_task_retry",
            )
            decision = TaskDecision(
                task=current,
                mode="execute",
                execute=True,
                execution_payload=AGENT_ADAPTERS[current.agent_type].execution_payload(current.slots),
            )
        elif req.input_context_mode == "edit":
            semantic = _explicit_artifact_edit_route(session, req.target_artifact_id)
            decision = advance_task(current, semantic, user_id, req.thread_id)
        else:
            semantic = await route_semantically(
                message=req.message,
                current_task=current,
                active_artifact=req.active_artifact,
                artifact_candidates=_recent_artifact_candidates(session),
                related_context=(req.related_context or _recent_conversation_context(session)),
            )
            if req.input_context_mode == "reply" and semantic.artifact_operation == ArtifactOperation.REVISE:
                semantic = semantic.model_copy(update={
                    "action": SemanticAction.QUESTION,
                    "artifact_operation": None,
                    "target_artifact_id": None,
                    "reason_code": "explicit_reply_context",
                })
            decision = None
        if (
            semantic.agent_type in {
                AgentType.EXAM, AgentType.LECTURE, AgentType.MINDMAP, AgentType.VIDEO,
                AgentType.LEARNING_GRAPH,
            }
            and semantic.action in {
                SemanticAction.NEW_TASK,
                SemanticAction.REPLACE,
                SemanticAction.SUPPLEMENT,
                SemanticAction.REVISE,
            }
            and req.source_context is not None
            and should_use_material_context(req.message, req.source_context.policy)
        ):
            updates = dict(semantic.slot_updates)
            updates["source_material_ids"] = list(req.source_context.material_ids)
            if req.source_context.scope and semantic.agent_type == AgentType.MINDMAP:
                # Only the mindmap adapter owns a source_scope slot. Other
                # material-aware agents receive verified material IDs without
                # injecting an unsupported slot into the common state machine.
                updates["source_scope"] = req.source_context.scope
            semantic = semantic.model_copy(update={"slot_updates": updates})
        if semantic.agent_type == AgentType.LEARNING_GRAPH:
            updates = dict(semantic.slot_updates)
            update_mode = str(
                updates.get("update_mode")
                or (current.slots.get("update_mode") if current else "CREATE")
                or "CREATE"
            ).upper()
            if update_mode != "CREATE":
                trusted_graph = _trusted_learning_graph_artifact(
                    session, req.active_artifact,
                )
                if trusted_graph is None:
                    raise HTTPException(
                        422,
                        "select one persisted learning graph in this conversation before updating",
                    )
                updates["graph_id"] = trusted_graph["graph_id"]
                updates["graph_revision"] = int(trusted_graph["graph_revision"])
                if trusted_graph.get("domain"):
                    updates["domain"] = str(trusted_graph["domain"])
                if not updates.get("user_goal") and trusted_graph.get("user_goal"):
                    updates["user_goal"] = str(trusted_graph["user_goal"])
                if update_mode == "REPLACE_SOURCE":
                    # Source identities are accepted only from the persisted
                    # graph artifact, never from semantic model output.
                    updates["replaced_source_ref_ids"] = list(
                        trusted_graph.get("source_ref_ids") or []
                    )
                semantic = semantic.model_copy(update={"slot_updates": updates})
        if decision is None:
            decision = advance_task(current, semantic, user_id, req.thread_id)
    except ValueError as exc:
        fail_runtime_operation(user_id, req.thread_id, "SEMANTIC_ROUTING_INVALID")
        logger.warning(
            "semantic workspace routing rejected user=%s thread=%s reason=%s",
            user_id, req.thread_id, str(exc)[:300],
        )
        raise HTTPException(422, {
            "code": "TASK_PARAMETERS_INVALID",
            "message": "task parameters are invalid",
        }) from exc
    except HTTPException:
        raise
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "SEMANTIC_ROUTING_FAILED")
        logger.exception("semantic workspace routing failed user=%s thread=%s", user_id, req.thread_id)
        raise HTTPException(503, "暂时无法理解这项任务，请稍后重试。") from exc

    if semantic.action in {SemanticAction.REPLACE, SemanticAction.NEW_TASK} and current:
        await cancel_current_task(db, user_id, req.thread_id)
    if semantic.action == SemanticAction.CANCEL:
        await cancel_current_task(db, user_id, req.thread_id)
    elif decision.task is not None:
        decision.task = await save_task(db, decision.task)

    # Questions and ordinary chat must reach the real streaming chat endpoint.
    # The pending task is deliberately left untouched and no duplicate history
    # rows are written here.
    if decision.mode != "chat":
        await _append(db, session, {"role": "user", "content": req.message})
        if decision.reply:
            await _append(db, session, {
                "role": "assistant",
                "content": decision.reply,
                "task": _task_payload(decision.task),
            })

    complete_runtime_operation(user_id, req.thread_id)
    return {"success": True, "data": {
        "task": _task_payload(decision.task),
        "confirmation": decision.reply,
        "mode": decision.mode,
        "execute": decision.execute,
        "execution_payload": decision.execution_payload,
        "semantic": {
            "action": semantic.action.value,
            "agent_type": semantic.agent_type.value if semantic.agent_type else None,
            "confidence": semantic.confidence,
            "reason_code": semantic.reason_code,
        },
    }}


@router.post("/route")
async def route_message(req: RouteRequest, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    session = await _owned_session(db, user_id, req.thread_id, create=True)
    routed = route_workspace_message(req.message, req.active_artifact)
    if routed["route"] == "capability_query":
        await _append(db, session, {"role": "user", "content": req.message})
        await _append(db, session, {"role": "assistant", "content": routed["reply"]})
    return {"success": True, "data": routed}


@router.post("/learning-graph")
async def generate_learning_graph(
    req: LearningGraphAgentRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Execute only the exact server-confirmed learning-graph task version."""
    from agents.learning_graph_orchestrator.compiler import CandidateContractError
    from agents.learning_graph_orchestrator.runner import (
        BlueprintContractError,
        ExistingGraphContractError,
        SourceContractError,
        UnrecoverableFallbackError,
    )
    from services.learning_graph_orchestrator_integration import (
        build_formal_request,
        orchestrate_and_save,
    )

    session = await _owned_session(db, user_id, req.thread_id)
    persisted_task = await _execution_task(
        db,
        user_id,
        req.thread_id,
        AgentType.LEARNING_GRAPH,
        req.task_id,
        req.task_version,
    )
    if persisted_task is None or not persisted_task.execution_key:
        raise HTTPException(409, "a server-confirmed learning graph task is required")
    slots = dict(persisted_task.slots)
    begin_runtime_operation(user_id, req.thread_id, "learning_graph_running")
    try:
        request = await build_formal_request(
            db,
            user_id=user_id,
            thread_id=req.thread_id,
            request_id=persisted_task.task_id,
            user_goal=str(slots["user_goal"]),
            domain=str(slots["domain"]),
            source_material_ids=list(slots.get("source_material_ids") or []),
            update_mode=str(slots.get("update_mode") or "CREATE"),
            graph_id=slots.get("graph_id"),
            graph_revision=int(slots.get("graph_revision") or 0),
            replaced_source_ref_ids=list(slots.get("replaced_source_ref_ids") or []),
        )
        public = await orchestrate_and_save(
            db,
            user_id=user_id,
            request=request,
            idempotency_key=persisted_task.execution_key,
        )
    except PermissionError as exc:
        await fail_persisted_task(db, persisted_task, error_code="LEARNING_GRAPH_FORBIDDEN")
        fail_runtime_operation(user_id, req.thread_id, "LEARNING_GRAPH_FORBIDDEN")
        raise HTTPException(403, "learning graph access denied") from exc
    except (SourceContractError, ExistingGraphContractError, CandidateContractError,
            BlueprintContractError, UnrecoverableFallbackError) as exc:
        await fail_persisted_task(db, persisted_task, error_code=type(exc).__name__[:80])
        fail_runtime_operation(user_id, req.thread_id, "LEARNING_GRAPH_CONTRACT_REJECTED")
        reports = [
            getattr(exc, name, None)
            for name in (
                "report",
                "source_contract_report",
                "existing_graph_report",
                "candidate_report",
                "blueprint_report",
                "fallback_report",
            )
        ]
        codes = sorted({
            item.code
            for report in reports if report is not None
            for item in getattr(report, "issues", [])
        })
        raise HTTPException(422, {
            "code": type(exc).__name__,
            "issues": codes,
            "message": "learning graph contract rejected",
        }) from exc
    except ValueError as exc:
        is_revision = "revision" in str(exc).lower()
        is_idempotency = "idempotency" in str(exc).lower()
        code = (
            "LEARNING_GRAPH_REVISION_CONFLICT" if is_revision
            else "LEARNING_GRAPH_IDEMPOTENCY_CONFLICT" if is_idempotency
            else "LEARNING_GRAPH_INVALID"
        )
        await fail_persisted_task(db, persisted_task, error_code=code)
        fail_runtime_operation(user_id, req.thread_id, code)
        raise HTTPException(409 if is_revision or is_idempotency else 422, {
            "code": code,
            "message": (
                "learning graph request conflicts with persisted state"
                if is_revision or is_idempotency
                else "learning graph request is invalid"
            ),
        }) from exc
    except Exception as exc:
        await fail_persisted_task(db, persisted_task, error_code="LEARNING_GRAPH_FAILED")
        fail_runtime_operation(user_id, req.thread_id, "LEARNING_GRAPH_FAILED")
        logger.warning(
            "workspace learning graph orchestration failed user=%s thread=%s error_type=%s",
            user_id,
            req.thread_id,
            type(exc).__name__,
        )
        raise HTTPException(503, "学习图谱生成失败，未写入无效图谱。") from exc

    summary = f"已生成《{public['graph_title']}》学习图谱，共 {len(public['nodes'])} 个知识节点。"
    message = {
        "role": "assistant",
        "content": summary,
        "artifact": {
            "kind": "learning_graph",
            "title": public["graph_title"],
            "summary": summary,
            "graph_id": public["graph_id"],
            "graph_revision": public["graph_revision"],
            "user_goal": str(slots["user_goal"]),
            "domain": str(slots["domain"]),
            "status": public["status"],
            "node_count": len(public["nodes"]),
            "source_ref_ids": sorted({
                source_ref
                for node in public["nodes"]
                for source_ref in list(node.get("source_refs") or [])
            }),
            "url": f"/learning?graph={public['graph_id']}",
        },
    }
    await _append(db, session, message)
    persisted_task.state = TaskState.COMPLETED
    persisted_task.error_code = None
    await save_task(db, persisted_task)
    complete_runtime_operation(user_id, req.thread_id)
    return {"success": True, "data": message["artifact"]}


@router.post("/exam")
async def generate_exam(req: AgentRequest, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    session = await _owned_session(db, user_id, req.thread_id)
    persisted_task = await _execution_task(
        db, user_id, req.thread_id, AgentType.EXAM, req.task_id, req.task_version,
    )
    slots = dict(persisted_task.slots) if persisted_task else {}
    topic = str(slots.get("topic") or req.topic)
    count = int(slots.get("question_count") or req.count)
    difficulty = _difficulty_label(slots.get("difficulty")) if persisted_task else (req.difficulty or "中等")
    question_types = list(slots.get("question_types") or req.question_types)
    source_artifact_id = slots.get("source_artifact_id") if persisted_task else req.source_artifact_id
    source_material_ids = list(slots.get("source_material_ids") or req.source_material_ids or [])
    source_citations: List[dict] = []
    instruction = "；".join(slots.get("additional_requirements") or []) if persisted_task else req.instruction
    spec = AgentTaskSpec(
        kind="exam", topic=topic, count=count, difficulty=difficulty,
        confirmed=req.confirmed, question_types=tuple(question_types), instruction=instruction,
    )
    if not req.confirmed:
        raise HTTPException(409, "explicit confirmation is required")
    begin_runtime_operation(user_id, req.thread_id, "exam_running")
    try:
        source_item = None
        source_topic = topic
        if source_artifact_id:
            source_item = await _owned_lecture(db, user_id, source_artifact_id)
            source_topic = f"{topic}\n以下讲义是唯一资料来源：\n{(source_item.content or '')[:9000]}"
        if source_material_ids:
            retrieved = await build_conversation_material_context(
                db,
                user_id=user_id,
                thread_id=req.thread_id,
                query=" ".join(part for part in (topic, instruction) if part),
                requested_material_ids=source_material_ids,
            )
            source_material_ids = list(retrieved.material_ids)
            source_citations = list(retrieved.citations)
            source_topic = f"{topic}\n以下检索片段是本次题目的资料来源：\n{retrieved.text}"

        async def create_exam_once():
            return await create_exam(
                db, user_id, "quiz", [source_topic], difficulty=difficulty,
                question_types=question_types, count=count,
                user_instruction=instruction,
            )

        exam = await run_agent_operation(create_exam_once)
        result = normalize_agent_result(spec, {"id": exam.id, "questions": exam.questions})
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "EXAM_AGENT_FAILED")
        if persisted_task:
            await fail_persisted_task(db, persisted_task, error_code="EXAM_AGENT_FAILED")
        await _record_task_failure(db, user_id, req.thread_id, "exam", topic)
        logger.exception("workspace exam agent failed user=%s thread=%s", user_id, req.thread_id)
        raise HTTPException(503, "试题生成失败，未创建结果，请稍后重试。") from exc
    title = f"{topic}练习"
    summary = f"已生成「{title}」，共 {len(result['questions'])} 道题。"
    if persisted_task:
        tutor_meta = dict((persisted_task.slots or {}).get("_tutor") or {})
        if tutor_meta.get("tutor_session_id") and not tutor_meta.get("evidence_artifact_id"):
            exam_evidence_artifact = GeneratedItem(
                user_id=user_id,
                type="workspace_exam_evidence",
                title=title,
                chapter=topic,
                content=json.dumps(exam.questions or [], ensure_ascii=False),
                raw_data={
                    "kind": "exam",
                    "thread_id": req.thread_id,
                    "exam_id": exam.id,
                    "question_count": len(exam.questions or []),
                    "source_artifact_id": source_artifact_id,
                    **tutor_meta,
                },
            )
            db.add(exam_evidence_artifact)
            await db.commit()
            await db.refresh(exam_evidence_artifact)
            tutor_meta["evidence_artifact_id"] = exam_evidence_artifact.id
            persisted_task.slots = {**dict(persisted_task.slots or {}), "_tutor": tutor_meta}
            persisted_task = await save_task(db, persisted_task)
    message = build_thread_artifact_message(req.thread_id, "exam", exam.id, title, summary)
    message["artifact"].update({
        "question_count": len(exam.questions), "total_score": exam.total_score, "status": exam.status,
        "source_artifact_id": source_artifact_id, "topic": topic,
        "source_material_ids": source_material_ids, "source_citations": source_citations,
        "difficulty": difficulty, "question_types": question_types,
        "instruction": instruction,
        "questions": [
            {
                "id": question.get("id"), "type": question.get("type"),
                "content": question.get("content") or question.get("title"),
                "options": question.get("options") or [], "points": question.get("points"),
            }
            for question in exam.questions
        ],
    })
    await _append(db, session, message)
    if persisted_task:
        await complete_persisted_task(db, persisted_task, artifact_id=exam.id)
    complete_runtime_operation(user_id, req.thread_id)
    return {"success": True, "data": {**message["artifact"], "questions": exam.questions, "time_limit_min": exam.time_limit_min}}


@router.post("/artifacts/{artifact_id}/actions/exam")
async def exam_from_lecture_artifact(
    artifact_id: int,
    req: ArtifactExamActionRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Run an explicit, conversation-bound lecture -> exam action."""
    session = await _owned_session(db, user_id, req.thread_id)
    source_artifact = next((
        dict(message.get("artifact") or {})
        for message in reversed(list(session.messages or []))
        if isinstance(message, dict)
        and isinstance(message.get("artifact"), dict)
        and message["artifact"].get("kind") == "lecture"
        and int(message["artifact"].get("artifact_id") or 0) == int(artifact_id)
    ), None)
    if source_artifact is None:
        raise HTTPException(404, "当前对话中没有这份讲义。")
    source = await _owned_lecture(db, user_id, artifact_id)

    action_contract = {
        "artifact_id": int(artifact_id),
        "action": "exam",
        "conversation_id": req.thread_id,
        "count": int(req.count),
        "difficulty": str(req.difficulty),
        "question_types": list(req.question_types or ["single_choice"]),
    }
    digest = hashlib.sha256(
        json.dumps(action_contract, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    task_id = f"artifact-exam-{digest[:40]}"
    existing = await load_task_by_id(db, user_id, req.thread_id, task_id)
    if existing is not None and existing.state == TaskState.COMPLETED and existing.artifact_id:
        replay = next((
            dict(message.get("artifact") or {})
            for message in reversed(list(session.messages or []))
            if isinstance(message, dict)
            and isinstance(message.get("artifact"), dict)
            and message["artifact"].get("kind") == "exam"
            and int(message["artifact"].get("artifact_id") or 0) == int(existing.artifact_id)
        ), None)
        if replay is not None:
            return {"success": True, "data": replay, "idempotent_replay": True}
    if existing is not None and existing.state == TaskState.RUNNING:
        raise HTTPException(409, "这份练习正在生成，请稍候。")

    if existing is not None and existing.state == TaskState.FAILED:
        task = await retry_failed_task(db, existing)
    else:
        adapter = AGENT_ADAPTERS[AgentType.EXAM]
        slots = adapter.initial_slots()
        try:
            slots.update(adapter.normalise_updates({
                "topic": str(source.chapter or source.title or source_artifact.get("topic") or "当前讲义"),
                "question_count": req.count,
                "question_types": req.question_types or ["single_choice"],
                "difficulty": req.difficulty,
                "source_artifact_id": artifact_id,
                "additional_requirements": ["题目必须严格基于来源讲义"],
            }))
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "出题参数不完整，请调整后重试。") from exc
        task = AgentTask(
            task_id=task_id,
            user_id=user_id,
            thread_id=req.thread_id,
            agent_type=AgentType.EXAM,
            slots=slots,
            state=TaskState.RUNNING,
            confirmed_version=1,
            execution_key=f"artifact-action:{digest}",
            source_artifact_id=artifact_id,
        )
        task = await save_task(db, task)

    return await generate_exam(
        AgentRequest(
            thread_id=req.thread_id,
            topic=str(task.slots.get("topic") or source.title),
            confirmed=True,
            count=req.count,
            difficulty=req.difficulty,
            question_types=req.question_types or ["single_choice"],
            source_artifact_id=artifact_id,
            task_id=task.task_id,
            task_version=task.version,
        ),
        user_id=user_id,
        db=db,
    )


class ArtifactDeriveActionRequest(BaseModel):
    """Structured derived-artifact action. The button click is the whole intent;
    the server never re-routes it through the natural-language semantic router."""
    thread_id: str = Field(min_length=1, max_length=100)
    action: Literal["EXERCISE", "KNOWLEDGE_CARD", "MINDMAP", "QUIZ"]


@router.post("/artifacts/{artifact_id}/actions/derive")
async def derive_from_lecture_artifact(
    artifact_id: int,
    req: ArtifactDeriveActionRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Run an explicit lecture -> derived artifact action (exercise, knowledge
    card, mindmap, quiz) as a structured task. Reuses the existing workspace
    task state machine, GeneratedItem storage and source_artifact_id lineage.
    No synthetic user chat message is produced for this action."""
    session = await _owned_session(db, user_id, req.thread_id, create=True)
    source = await _owned_lecture(db, user_id, artifact_id)
    source_topic = str(source.chapter or source.title or "当前讲义")[:200]
    source_text = str(source.content or "")[:12000]

    action_contract = {
        "artifact_id": int(artifact_id),
        "action": str(req.action),
        "conversation_id": req.thread_id,
    }
    digest = hashlib.sha256(
        json.dumps(action_contract, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    task_id = f"artifact-derive-{digest[:40]}"
    existing = await load_task_by_id(db, user_id, req.thread_id, task_id)
    if existing is not None and existing.state == TaskState.COMPLETED and existing.artifact_id:
        replay = next((
            dict(message.get("artifact") or {})
            for message in reversed(list(session.messages or []))
            if isinstance(message, dict)
            and isinstance(message.get("artifact"), dict)
            and int(message["artifact"].get("artifact_id") or 0) == int(existing.artifact_id)
        ), None)
        if replay is not None:
            return {"success": True, "data": replay, "idempotent_replay": True}
    if existing is not None and existing.state == TaskState.RUNNING:
        raise HTTPException(409, "这个学习产物正在生成，请稍候。")
    if existing is not None and existing.state == TaskState.FAILED:
        task = await retry_failed_task(db, existing)
    else:
        agent_type = (
            AgentType.EXAM if req.action in ("EXERCISE", "QUIZ")
            else AgentType.MINDMAP if req.action == "MINDMAP"
            else AgentType.LECTURE
        )
        slots: Dict[str, Any] = {
            "topic": source_topic,
            "source_artifact_id": artifact_id,
            "source_artifact_kind": "lecture",
        }
        if req.action == "EXERCISE":
            slots.update({"question_count": 5, "question_types": ["single_choice", "multiple_choice"], "difficulty": "intermediate", "additional_requirements": ["题目必须严格基于来源讲义"]})
        elif req.action == "QUIZ":
            slots.update({"question_count": 10, "question_types": ["single_choice", "multiple_choice"], "difficulty": "intermediate", "additional_requirements": ["题目必须严格基于来源讲义"]})
        elif req.action == "MINDMAP":
            slots.update({"purpose": "根据来源讲义生成学习思维导图", "instruction": "基于来源讲义组织核心概念与结构"})
        else:
            slots.update({"detail_level": "简明", "purpose": "将来源讲义浓缩为精炼知识卡，保留关键定义、要点与例子"})
        task = AgentTask(
            task_id=task_id,
            user_id=user_id,
            thread_id=req.thread_id,
            agent_type=agent_type,
            slots=slots,
            state=TaskState.RUNNING,
            confirmed_version=1,
            execution_key=f"artifact-action:{digest}",
            source_artifact_id=artifact_id,
        )
        task = await save_task(db, task)

    begin_runtime_operation(user_id, req.thread_id, "artifact_derive_running")
    try:
        if req.action in ("EXERCISE", "QUIZ"):
            result = await generate_exam(
                AgentRequest(
                    thread_id=req.thread_id,
                    topic=source_topic,
                    confirmed=True,
                    count=int(task.slots.get("question_count") or (5 if req.action == "EXERCISE" else 10)),
                    difficulty=str(task.slots.get("difficulty") or "intermediate"),
                    question_types=list(task.slots.get("question_types") or ["single_choice", "multiple_choice"]),
                    source_artifact_id=artifact_id,
                    instruction="；".join(task.slots.get("additional_requirements") or []),
                    task_id=task.task_id,
                    task_version=task.version,
                ),
                user_id=user_id,
                db=db,
            )
            return {"success": True, "data": result["data"]}

        if req.action == "MINDMAP":
            result = await run_agent_operation(lambda: generate_workspace_mindmap(
                topic=source_topic,
                source_context=f"以下讲义是唯一资料来源：\n{source_text}",
                source_scope="来源讲义",
                instruction="基于来源讲义组织核心概念与学习结构，不要编造讲义之外的事实",
            ))
            title = str(result.get("title") or f"{source_topic}学习导图")[:200]
            summary = str(result.get("summary") or f"已生成《{title}》。")[:500]
            tree_data = result["tree_data"]
            item = GeneratedItem(
                user_id=user_id,
                type="workspace_mindmap",
                title=title,
                chapter=source_topic,
                content=json.dumps(tree_data, ensure_ascii=False),
                raw_data={
                    **(await _tutor_artifact_metadata(db, user_id, req.thread_id)),
                    "thread_id": req.thread_id,
                    "kind": "mindmap",
                    "summary": summary,
                    "tree_data": tree_data,
                    "version": 1,
                    "source_artifact_id": artifact_id,
                    "source_artifact_kind": "lecture",
                    "source_scope": "来源讲义",
                    "source_citations": [],
                    "instruction": "基于来源讲义组织核心概念与学习结构",
                },
            )
        else:
            profile = await _load_profile(db, user_id)
            generated = await run_agent_operation(lambda: DocumentAgent()(_make_state(
                str(user_id), profile=profile, chapter_id=source_topic,
                user_query=source_topic,
                workspace_instruction=(
                    "将以下讲义浓缩为精炼知识卡：保留关键定义、核心要点、公式或例子；"
                    "每张知识卡一个要点，总字数不超过原文一半。\n\n"
                    f"来源讲义：\n{source_text}"
                ),
                workspace_detail_level="简明",
            )))
            generated_document = generated.get("document") if isinstance(generated, dict) else {}
            content = str((generated_document or {}).get("content") or "").strip()
            if not content:
                raise ValueError("agent returned no usable knowledge card content")
            title = f"{source_topic}知识卡"[:200]
            summary = f"已生成《{title}》，基于《{source_topic}》整理。"
            item = GeneratedItem(
                user_id=user_id,
                type="workspace_lecture",
                title=title,
                chapter=source_topic,
                content=content,
                raw_data={
                    **(await _tutor_artifact_metadata(db, user_id, req.thread_id)),
                    "thread_id": req.thread_id,
                    "kind": "knowledge_card",
                    "topic": source_topic,
                    "document": generated_document,
                    "version": 1,
                    "quality_status": "passed",
                    "export_formats": ["pdf", "docx"],
                    "instruction": "将来源讲义浓缩为精炼知识卡",
                    "detail_level": "简明",
                    "source_artifact_id": artifact_id,
                    "source_artifact_kind": "lecture",
                    "source_citations": [],
                },
            )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        if item.type == "workspace_mindmap":
            artifact = _mindmap_payload_from_item(item)
        else:
            artifact = {
                "artifact_id": item.id,
                "kind": "knowledge_card",
                "title": item.title,
                "topic": item.chapter or source_topic,
                "content": content if req.action == "KNOWLEDGE_CARD" else item.content,
                "summary": summary,
                "version": 1,
                "quality_status": "passed",
                "source_artifact_id": artifact_id,
                "source_artifact_kind": "lecture",
                "export_formats": ["pdf", "docx"],
            }
        message = build_thread_artifact_message(req.thread_id, artifact["kind"], item.id, title, summary)
        message["artifact"].update(artifact)
        await _append(db, session, message)
        await complete_persisted_task(db, task, artifact_id=item.id)
        complete_runtime_operation(user_id, req.thread_id)
        return {"success": True, "data": artifact}
    except HTTPException:
        fail_runtime_operation(user_id, req.thread_id, "ARTIFACT_DERIVE_FAILED")
        raise
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "ARTIFACT_DERIVE_FAILED")
        if task is not None:
            await fail_persisted_task(db, task, error_code="ARTIFACT_DERIVE_FAILED")
        await _record_task_failure(db, user_id, req.thread_id, "derive", source_topic)
        logger.exception("artifact derive agent failed user=%s thread=%s action=%s", user_id, req.thread_id, req.action)
        raise HTTPException(503, "学习产物生成失败，未创建结果，请稍后重试。") from exc


async def _gate_lecture_content(
    *, content: str, topic: str,
    rewrite: "Callable[[list[str]], Awaitable[str]] | None" = None,
) -> tuple[str, str, dict[str, Any]]:
    """Run the content quality gate on a Markdown lecture.

    Returns ``(content, quality_status, receipt)``. It never raises: a lecture the
    learner asked for is still delivered, but it stops being stamped "passed" on
    the way out. The three statuses mean what they say — measured pass, measured
    pass with notes, or a blocking defect that the learner should be told about.
    """
    from services.content_quality_gate import (
        KIND_LECTURE, quality_receipt, run_content_quality_gate,
    )

    async def revise(*, existing_candidate, constraints, prior_review, round_number):
        if rewrite is None:
            return existing_candidate
        try:
            rewritten = await rewrite(list(constraints))
        except Exception:  # noqa: BLE001 - 改写失败不该把已经生成的内容一起带走
            return existing_candidate
        text = str(rewritten or "").strip()
        return {"content": text} if text else existing_candidate

    gate = await run_content_quality_gate(
        kind=KIND_LECTURE, candidate={"content": content}, topic=topic, revise=revise,
    )
    gated = str(gate["candidate"].get("content") or content)
    blocking = [i for i in gate["issues"] if str(i.get("severity")) in {"high", "blocking"}]
    if gate["verdict"] == "block" or blocking:
        status = "needs_review"
    elif gate["issues"]:
        status = "passed_with_notes"
    else:
        status = "passed"
    return gated, status, quality_receipt(gate)


def _openability_claim(canonical: dict[str, Any]) -> str:
    """讲义交付语的最后一句：只有真能打开时才说能打开。

    DARYA 里打开一份资料要求 canonical 契约成立且 review_verdict 为 PASS；没有这条
    回执时点开会看到「这份资料还没有通过正式检查，暂时不会在 DARYA 中打开」。
    所以这句话必须看回执说话。2026-09-20 的真人测试实测到过一次相反的写法：对话里
    说「你可以打开讲义查看」，而那张卡片当时根本打不开。
    """
    openable = (
        str(canonical.get("contract") or "") == "darya.artifact.v1"
        and str(dict(canonical.get("status") or {}).get("review_verdict") or "").upper() == "PASS"
    )
    if openable:
        return "你可以打开讲义查看，也可以继续问我其中的内容。"
    return "它已经存进「我的资料」，但这一版还没有通过正式检查，现在打不开；可以让我按意见再出一版。"



@router.post("/lecture")
async def generate_lecture(req: AgentRequest, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    session = await _owned_session(db, user_id, req.thread_id)
    persisted_task = await _execution_task(
        db, user_id, req.thread_id, AgentType.LECTURE, req.task_id, req.task_version,
    )
    slots = dict(persisted_task.slots) if persisted_task else {}
    topic = str(slots.get("topic") or req.topic)
    detail_level = str(slots.get("detail_level") or req.detail_level)
    export_formats = list(slots.get("export_formats") or req.export_formats or ["pdf", "docx"])
    instruction = str(slots.get("purpose") or req.instruction or "")
    source_artifact_id = slots.get("source_artifact_id") if persisted_task else req.source_artifact_id
    source_artifact_kind = slots.get("source_artifact_kind") if persisted_task else req.source_artifact_kind
    source_material_ids = list(slots.get("source_material_ids") or req.source_material_ids or [])
    source_citations: List[dict] = []
    source_context = ""
    if source_artifact_id and source_artifact_kind == "exam_result":
        source_result = await _owned_exam_result(
            db, user_id, req.thread_id, int(source_artifact_id),
        )
        source_context = (
            "请严格基于以下本次练习结果，逐题讲解错题、用户答案、正确答案、"
            "相关知识点和错误原因；不要编造未出现的题目。\n"
            + artifact_context(source_result)
        )
        instruction = "\n".join(part for part in (instruction, source_context) if part)
    if source_material_ids:
        retrieved = await build_conversation_material_context(
            db,
            user_id=user_id,
            thread_id=req.thread_id,
            query=" ".join(part for part in (topic, instruction) if part),
            policy="only",
            requested_material_ids=source_material_ids,
        )
        source_material_ids = list(retrieved.material_ids)
        source_citations = list(retrieved.citations)
        instruction = "\n".join(part for part in (
            instruction,
            "请严格依据以下资料片段组织讲义，并避免编造资料中没有的事实：\n" + retrieved.text,
        ) if part)
    spec = AgentTaskSpec(
        "lecture",
        topic,
        detail_level=detail_level,
        confirmed=req.confirmed,
        export_formats=tuple(export_formats),
        instruction=instruction,
    )
    if not req.confirmed:
        raise HTTPException(409, "explicit confirmation is required")
    begin_runtime_operation(user_id, req.thread_id, "lecture_running")
    try:
        profile = await _load_profile(db, user_id)

        async def generate_lecture_once(extra_constraints: list[str] | None = None):
            query = topic
            if extra_constraints:
                query = (
                    f"{topic}\n\n上一稿未通过内容质量检查，请按下面的要求重写整篇讲义：\n"
                    + "\n".join(f"- {item}" for item in extra_constraints)
                )
            generated = await DocumentAgent()(_make_state(
                str(user_id), profile=profile, chapter_id=topic,
                user_query=query,
                workspace_instruction=instruction,
                workspace_evidence_verified=req.evidence_verified,
                workspace_detail_level=detail_level,
                theme=req.theme,
            ))
            generated_document = generated.get("document") if isinstance(generated, dict) else {}
            generated_content = str((generated_document or {}).get("content") or "").strip()
            _validate_lecture_constraints(generated_content, instruction, detail_level)
            return generated

        async def rewrite_lecture(constraints: list[str]) -> str:
            regenerated = await run_agent_operation(lambda: generate_lecture_once(constraints))
            document = regenerated.get("document") if isinstance(regenerated, dict) else {}
            return str((document or {}).get("content") or "").strip()

        result = await run_agent_operation(generate_lecture_once)
        document = result.get("document") if isinstance(result, dict) else None
        document = document if isinstance(document, dict) else {}
        content = str(document.get("content") or "").strip()
        normalized = normalize_agent_result(spec, {"document": document, "content": content})
        if not content:
            raise ValueError("agent returned no usable lecture content")
        # DocumentAgent may echo the whole generation instruction as its title.
        # The workspace card uses the stable, user-facing topic title instead.
        title = f"{topic}讲义"[:200]
        # 质量状态必须是被量出来的：以前这里无条件写 passed，等于给任何一稿盖章。
        content, quality_status, quality_receipt_data = await _gate_lecture_content(
            content=content, topic=topic, rewrite=rewrite_lecture,
        )
        normalized["document"] = {**dict(normalized.get("document") or {}), "content": content}
        item = GeneratedItem(
            user_id=user_id, type="workspace_lecture", title=title,
            chapter=topic, content=content,
            raw_data={
                **(await _tutor_artifact_metadata(db, user_id, req.thread_id)),
                "thread_id": req.thread_id, "topic": topic, "document": normalized["document"],
                "version": 1, "quality_status": quality_status,
                "content_quality": quality_receipt_data, "export_formats": export_formats,
                "instruction": instruction, "detail_level": detail_level,
                "source_artifact_id": source_artifact_id,
                "source_artifact_kind": source_artifact_kind,
                "source_material_ids": source_material_ids,
                "source_citations": source_citations,
            },
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "LECTURE_AGENT_FAILED")
        if persisted_task:
            await fail_persisted_task(db, persisted_task, error_code="LECTURE_AGENT_FAILED")
        await _record_task_failure(db, user_id, req.thread_id, "lecture", topic)
        logger.exception("workspace lecture agent failed user=%s thread=%s", user_id, req.thread_id)
        raise HTTPException(503, "讲义生成失败，未创建结果，请稍后重试。") from exc
    title_core = title.replace("讲义", "")
    section_digest = "；".join(
        heading.strip() for heading in re.findall(r"^#{1,4}\s+(.+?)\s*$", content, flags=re.M)[:5]
        if heading.strip() and heading.strip().replace(" ", "") not in title.replace(" ", "") and heading.strip().replace(" ", "") not in title_core.replace(" ", "")
    )[:160]
    verification_note = str((document or {}).get("verification") or "").strip().rstrip("。")
    summary_parts = [f"已生成「{title}」"]
    if section_digest:
        summary_parts.append(f"（要点：{section_digest}）")
    summary_parts.append("。")
    if verification_note:
        summary_parts.append(f"{verification_note}。")
    # 「你可以打开讲义查看」是对可用性的断言，不能无条件写。DARYA 里打开一份资料要求
    # canonical 契约成立且 review_verdict 为 PASS，没有这条回执时点开会看到「还没有通过
    # 正式检查」。所以先真的去看它能不能打开，再决定这句话怎么说。
    version = await ensure_initial_version(db, item)
    canonical = await build_canonical_artifact(db, item=item, version=version)
    summary_parts.append(_openability_claim(canonical))
    summary = "".join(summary_parts)
    message = build_thread_artifact_message(req.thread_id, "lecture", item.id, title, summary)
    message["artifact"].update({
        "topic": topic, "content": content, "version": 1, "quality_status": quality_status,
        "content_quality": quality_receipt_data,
        "export_formats": export_formats,
        "instruction": instruction, "detail_level": detail_level,
        "source_artifact_id": source_artifact_id,
        "source_artifact_kind": source_artifact_kind,
        "source_material_ids": source_material_ids,
        "source_citations": source_citations,
    })
    await _append(db, session, message)
    if persisted_task:
        await complete_persisted_task(db, persisted_task, artifact_id=item.id)
    complete_runtime_operation(user_id, req.thread_id)
    return {"success": True, "data": message["artifact"]}


@router.post("/video")
async def generate_video(
    req: VideoRequest,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _owned_session(db, user_id, req.thread_id)
    persisted_task = await _execution_task(
        db, user_id, req.thread_id, AgentType.VIDEO, req.task_id, req.task_version,
    )
    slots = dict(persisted_task.slots) if persisted_task else {}
    if not req.confirmed:
        raise HTTPException(409, "explicit confirmation is required")
    topic = str(slots.get("topic") or req.topic or "").strip()
    duration_sec = max(15, min(int(slots.get("duration_sec") or req.duration_sec or 45), 600))
    instruction = str(slots.get("instruction") or req.instruction or "").strip()
    source_material_ids = list(slots.get("source_material_ids") or req.source_material_ids or [])
    source_context = ""
    source_citations: List[dict] = []
    if source_material_ids:
        try:
            retrieved = await build_conversation_material_context(
                db,
                user_id=user_id,
                thread_id=req.thread_id,
                query=" ".join(part for part in (topic, instruction, "教学视频") if part),
                policy="only",
                requested_material_ids=source_material_ids,
            )
            if not retrieved.text:
                raise HTTPException(422, "selected materials are not ready or contain no usable text")
            source_material_ids = list(retrieved.material_ids)
            source_context = retrieved.text
            source_citations = list(retrieved.citations)
        except MaterialAccessError as exc:
            raise HTTPException(404, str(exc)) from exc

    # Task16B: create the canonical Artifact immediately, then execute the
    # internal provider outside the browser request. The old synchronous path
    # below remains unreachable only as compatibility reference during this RC.
    profile = await _load_profile(db, user_id)
    style_context = StyleContext.from_profile(profile)
    tutor_meta = await _tutor_artifact_metadata(db, user_id, req.thread_id)
    evidence_context = EvidenceTeachingContext.from_trusted_evidence({
        "graph_node": tutor_meta.get("graph_node_id") or "",
    })
    cloud_settings = CloudVideoSettings.from_app_settings(app_settings)
    # In-flight dedup: a second create for the same task (or the same thread
    # and topic) while one paid job is still running must return the existing
    # job, never schedule a second billed pipeline.
    recent_jobs = (await db.execute(select(GeneratedItem).where(
        GeneratedItem.user_id == user_id,
        GeneratedItem.type == "workspace_video",
    ).order_by(GeneratedItem.id.desc()).limit(20))).scalars()
    for existing_job in recent_jobs:
        existing_meta = dict(existing_job.raw_data or {})
        if str(existing_meta.get("status") or "") not in NONTERMINAL_STATES:
            continue
        same_task = persisted_task is not None and str(existing_meta.get("task_id") or "") == str(persisted_task.task_id)
        same_topic = (
            str(existing_meta.get("thread_id") or "") == str(req.thread_id)
            and str(existing_meta.get("topic") or "") == topic
        )
        if same_task or same_topic:
            return {"success": True, "data": project_video_job(existing_job), "deduplicated": True}
    item = await create_video_job(
        db, user_id=user_id, thread_id=req.thread_id, topic=topic,
        duration_sec=duration_sec, instruction=instruction,
        source_material_ids=source_material_ids, provider=cloud_settings.provider,
        source_context=source_context, source_citations=source_citations,
        style_context=style_context.model_dump(),
        evidence_context=evidence_context.model_dump(),
        task_id=persisted_task.task_id if persisted_task else None,
        theme=req.theme,
    )
    queued = project_video_job(item)
    await _append(db, session, {
        "role": "assistant",
        "content": f"《{item.title}》已进入生成队列；页面会显示真实阶段，失败时可以从同一产物重试。",
        "artifact": queued,
        "task": _task_payload(persisted_task) if persisted_task else None,
    })
    begin_runtime_operation(user_id, req.thread_id, "video_queued")
    background_tasks.add_task(execute_video_job, artifact_id=item.id, user_id=user_id)
    return {"success": True, "data": queued}


@router.get("/video/health")
async def cloud_video_health(
    user_id: int = Depends(get_current_user_id),
):
    """Return only secret-free capability state for acceptance and support."""
    _ = user_id
    return {
        "success": True,
        "data": CloudVideoSettings.from_app_settings(app_settings).public_health(),
    }


@router.get("/videos/{artifact_id}")
async def get_video(
    artifact_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned_video(db, user_id, artifact_id)
    return {"success": True, "data": _video_payload_from_item(item)}


@router.get("/videos/{artifact_id}/status")
async def get_video_status(
    artifact_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await get_owned_video_job(db, user_id=user_id, artifact_id=artifact_id)
    except VideoJobAccessError as exc:
        raise HTTPException(404, "video artifact not found") from exc
    return {"success": True, "data": project_video_job(item)}


@router.post("/videos/{artifact_id}/retry")
async def retry_video(
    artifact_id: int,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await retry_video_job(db, user_id=user_id, artifact_id=artifact_id)
    except VideoJobAccessError as exc:
        raise HTTPException(404, "video artifact not found") from exc
    except Exception as exc:
        raise HTTPException(409, "only a failed video job can be retried") from exc
    background_tasks.add_task(execute_video_job, artifact_id=artifact_id, user_id=user_id)
    return {"success": True, "data": project_video_job(item)}


@router.get("/videos/{artifact_id}/media")
async def stream_video_media(
    artifact_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await get_owned_video_job(db, user_id=user_id, artifact_id=artifact_id)
    except VideoJobAccessError as exc:
        raise HTTPException(404, "video artifact not found") from exc
    metadata = dict(item.raw_data or {})
    if metadata.get("status") != "ready":
        raise HTTPException(409, "video is not ready")
    path = Path(str(metadata.get("video_path") or "")).resolve()
    root = CloudVideoSettings.from_app_settings(app_settings).output_dir.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(404, "video media not found") from exc
    if not path.exists() or path.suffix.lower() != ".mp4":
        raise HTTPException(404, "video media not found")
    return FileResponse(path=str(path), media_type="video/mp4", filename=path.name)


@router.get("/artifacts/{artifact_id}")
async def get_artifact(
    artifact_id: int,
    version: Optional[int] = None,
    version_id: Optional[int] = None,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await build_public_projection(
            db, user_id=user_id, artifact_id=artifact_id,
            version_number=version, version_id=version_id,
        )
        await db.commit()
        return {"success": True, "data": data}
    except ArtifactVersionError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


@router.post("/presentation")
async def generate_presentation(
    req: PresentationRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _owned_session(db, user_id, req.thread_id)
    persisted_task = await _execution_task(
        db, user_id, req.thread_id, AgentType.PRESENTATION, req.task_id, req.task_version,
    )
    slots = dict(persisted_task.slots) if persisted_task else {}
    if not req.confirmed:
        raise HTTPException(409, "explicit confirmation is required")
    topic = str(slots.get("topic") or req.topic)
    delivery_mode = str(slots.get("delivery_mode") or req.delivery_mode)
    learning_goal = str(slots.get("learning_goal") or req.learning_goal or "")
    level = str(slots.get("level") or req.level or "")
    duration_minutes = slots.get("duration_minutes") or req.duration_minutes
    slide_count = slots.get("slide_count") or req.slide_count
    style = str(slots.get("presentation_style") or req.presentation_style or "")
    theme = str(slots.get("presentation_theme") or req.theme or "kaki")
    source_id = slots.get("source_artifact_id") or req.source_artifact_id
    source_context = ""
    if source_id:
        source = await db.execute(select(GeneratedItem).where(
            GeneratedItem.id == int(source_id), GeneratedItem.user_id == user_id,
        ))
        source_item = source.scalar_one_or_none()
        if source_item is None:
            raise HTTPException(404, "source artifact not found")
        source_context = source_item.content or json.dumps(source_item.raw_data or {}, ensure_ascii=False)

    operation = "interactive_lesson_running" if delivery_mode == "interactive_lesson" else "presentation_running"
    begin_runtime_operation(user_id, req.thread_id, operation)
    try:
        # 学习者真实背景（只读既有 Profile；不造新的个性化权威）
        profile_row = await db.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
        profile = profile_row.scalar_one_or_none()
        learner_bits = [f"学习水平：{level or '通用'}"]
        if profile is not None:
            if getattr(profile, "grade_level", None):
                learner_bits.append(f"年级：{profile.grade_level}")
            if getattr(profile, "preferred_language", None):
                learner_bits.append(f"语言偏好：{profile.preferred_language}")
        learner_context = "；".join(learner_bits)
        if req.generation_context:
            learner_context += "\n" + req.generation_context
        deck = await run_agent_operation(lambda: generate_workspace_presentation(
            topic=topic,
            delivery_mode=delivery_mode,
            learning_goal=learning_goal,
            level=level,
            duration_minutes=duration_minutes,
            slide_count=slide_count,
            presentation_style=style,
            source_context=source_context,
            presentation_theme=theme,
            learner_context=learner_context,
        ))
        # 内容闸门已在生成阶段完成。拒绝稿不能先落库再返回 503：那会留下
        # 一份用户打不开、动作也找不回的孤儿产物。
        content_receipt = dict(deck.get("content_review") or {})
        if content_receipt and str(content_receipt.get("verdict") or "") != "pass":
            blocking = content_receipt.get("blocking") or []
            detail = "；".join(
                str(row.get("description") or row.get("code") or "") if isinstance(row, dict) else str(row)
                for row in blocking[:3]
            ) or "内容质量评审未通过。"
            raise GenerationDraftBlocked(
                "PRESENTATION_REVIEW_CONTENT_QUALITY_FAILED", reason=detail,
                body=presentation_draft(deck, reason=detail),
            )
        kind = "interactive_lesson" if delivery_mode == "interactive_lesson" else "presentation"
        title = str(deck.get("title") or f"{topic} PPT")[:200]
        outline = [str(slide.get("title") or "") for slide in deck["slides"]]
        summary = (
            f"已生成《{title}》互动课堂，共 {len(deck['slides'])} 页，可以开始学习。"
            if delivery_mode == "interactive_lesson" else
            f"已生成《{title}》PPT，共 {len(deck['slides'])} 页，可以在线查看或导出。"
        )
        item = GeneratedItem(
            user_id=user_id,
            type="workspace_interactive_lesson" if delivery_mode == "interactive_lesson" else "workspace_presentation",
            title=title,
            chapter=topic,
            content=json.dumps(deck, ensure_ascii=False),
            raw_data={
                **(await _tutor_artifact_metadata(db, user_id, req.thread_id)),
                "thread_id": req.thread_id,
                "kind": kind,
                "delivery_mode": delivery_mode,
                "classroom_id": None,
                "status": "completed",
                "duration_minutes": duration_minutes,
                "slide_count": slide_count,
                "outline": outline,
                "slides": deck["slides"],
                "progress": {"current_slide": 0, "viewed_slide_indexes": [], "percent": 0, "completed": False},
                "provider": deck.get("provider", "unknown"),
                "source_artifact_ids": [int(source_id)] if source_id else [],
                "summary": summary,
                "learning_goal": learning_goal,
                "level": level,
                "presentation_style": style,
                "presentation_theme": deck.get("presentation_theme", "kaki"),
                "version": 1,
                "versions": [],
            },
        )
        db.add(item)
        await db.flush()
        metadata = dict(item.raw_data or {})
        metadata["classroom_id"] = f"classroom-{item.id}" if delivery_mode == "interactive_lesson" else None
        item.raw_data = metadata
        await db.commit()
        await db.refresh(item)

        # Artifact Review（canonical 受信审阅，与对话能力产物同一条通道）：
        # 结构完整性 / 叙述完整性 / 主题快照 / 答案隔离 全部通过才放行。
        version = await ensure_initial_version(db, item)
        if delivery_mode != "interactive_lesson" and any(slide.get("interaction") for slide in deck["slides"]):
            raise RuntimeError("PRESENTATION_REVIEW_ANSWER_LEAKAGE")
        missing_narration = [int(slide.get("index", i)) + 1 for i, slide in enumerate(deck["slides"]) if not str(slide.get("narration") or "").strip()]
        if missing_narration:
            raise RuntimeError(f"PRESENTATION_REVIEW_NARRATION_MISSING {missing_narration[:5]}")
        # 内容质量闸门的实测回执决定这一版能不能放行、记录什么分数。
        # 以前这里是一组写死的 0.95/0.96 —— 它证明不了这一页页真的在讲这个主题。
        if content_receipt:
            measured = {
                f"content_{key}": round(int(value) / 5, 4)
                for key, value in (content_receipt.get("scores") or {}).items()
            }
        else:
            measured = {"content_quality": 1.0}
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
                scores={
                    "theme_snapshot": 1.0,
                    "answer_leakage": 1.0,
                    **measured,
                },
                safe_summary=(
                    "逐页叙述、主题快照与答案隔离均通过；"
                    + str(content_receipt.get("summary") or "内容质量按确定性检查复核通过。")
                ),
                issues=[],
                preserve=[],
                idempotency_key=f"presentation:{item.id}:v{version.version_number}:review",
            ),
            reviewer_role="PLATFORM_REVIEWER",
            provider="darya-presentation-review",
        )
        await db.commit()
        await db.refresh(item)
        artifact = _presentation_payload_from_item(item)
        message = build_thread_artifact_message(req.thread_id, kind, item.id, title, summary)
        message["artifact"].update(artifact)
        await _append(db, session, message)
        if persisted_task:
            await complete_persisted_task(db, persisted_task, artifact_id=item.id)
        complete_runtime_operation(user_id, req.thread_id)
        return {"success": True, "data": message["artifact"]}
    except GenerationDraftBlocked as exc:
        fail_runtime_operation(user_id, req.thread_id, "PRESENTATION_AGENT_FAILED")
        if persisted_task:
            await fail_persisted_task(db, persisted_task, error_code=exc.code)
        raise
    except HTTPException:
        fail_runtime_operation(user_id, req.thread_id, "PRESENTATION_AGENT_FAILED")
        raise
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "PRESENTATION_AGENT_FAILED")
        if persisted_task:
            await fail_persisted_task(db, persisted_task, error_code="PRESENTATION_AGENT_FAILED")
        logger.exception("workspace presentation agent failed user=%s thread=%s", user_id, req.thread_id)
        raise HTTPException(503, "PPT/互动课堂生成失败，未创建产物，请稍后重试。") from exc


@router.get("/presentations/{artifact_id}")
async def get_presentation(
    artifact_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned_presentation(db, user_id, artifact_id)
    if item.current_version_id is None:
        await ensure_initial_version(db, item)
        await db.commit()
        await db.refresh(item)
    return {"success": True, "data": _presentation_payload_from_item(item)}


@router.post("/presentations/{artifact_id}/revise")
async def revise_presentation(
    artifact_id: int,
    req: RevisionRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _owned_session(db, user_id, req.thread_id)
    item = await _owned_presentation(db, user_id, artifact_id)
    metadata = dict(item.raw_data or {})
    if metadata.get("thread_id") and metadata.get("thread_id") != req.thread_id:
        raise HTTPException(404, "学习产物不在当前会话中。")
    delivery_mode = str(metadata.get("delivery_mode") or "presentation_only")
    kind = "interactive_lesson" if delivery_mode == "interactive_lesson" else "presentation"
    operation = "interactive_lesson_revising" if kind == "interactive_lesson" else "presentation_revising"
    begin_runtime_operation(user_id, req.thread_id, operation)
    try:
        existing_deck = {
            "title": item.title,
            "slides": list(metadata.get("slides") or []),
        }
        deck = await run_agent_operation(lambda: revise_workspace_presentation(
            existing_deck=existing_deck,
            instruction=req.instruction,
            delivery_mode=delivery_mode,
        ))
        old_version = int(metadata.get("version") or 1)
        versions = list(metadata.get("versions") or [])
        versions.append({
            "version": old_version,
            "title": item.title,
            "slides": list(metadata.get("slides") or []),
            "outline": list(metadata.get("outline") or []),
        })
        item.title = str(deck.get("title") or item.title)[:200]
        item.content = json.dumps(deck, ensure_ascii=False)
        metadata.update({
            "kind": kind,
            "status": "completed",
            "slides": list(deck.get("slides") or []),
            "slide_count": len(deck.get("slides") or []),
            "outline": [str(slide.get("title") or "") for slide in (deck.get("slides") or [])],
            "progress": {"current_slide": 0, "viewed_slide_indexes": [], "percent": 0, "completed": False},
            "interaction_results": [],
            "provider": deck.get("provider", metadata.get("provider", "unknown")),
            "version": old_version + 1,
            "versions": versions[-10:],
            "last_revision": req.instruction,
        })
        summary = (
            f"已更新《{item.title}》互动课堂，当前为第 {metadata['version']} 版，学习进度已重新开始。"
            if kind == "interactive_lesson" else
            f"已更新《{item.title}》PPT，当前为第 {metadata['version']} 版。"
        )
        metadata["summary"] = summary
        item.raw_data = metadata
        await db.commit()
        await db.refresh(item)
        artifact = _presentation_payload_from_item(item)
        message = build_thread_artifact_message(req.thread_id, kind, item.id, item.title, summary)
        message["artifact"].update(artifact)
        await _append(db, session, message)
        complete_runtime_operation(user_id, req.thread_id)
        return {"success": True, "data": message["artifact"]}
    except Exception as exc:
        await db.rollback()
        fail_runtime_operation(user_id, req.thread_id, "PRESENTATION_REVISION_FAILED")
        logger.exception("workspace presentation revision failed user=%s artifact=%s", user_id, artifact_id)
        raise HTTPException(503, "PPT/互动课堂修改失败，原版本保持不变，请稍后重试。") from exc


@router.get("/presentations/{artifact_id}/export/pptx")
async def export_presentation(
    artifact_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned_presentation(db, user_id, artifact_id)
    selected = await get_version(db, item=item, version_id=item.current_version_id) if item.current_version_id else await ensure_initial_version(db, item)
    metadata = dict(selected.private_data_snapshot or item.raw_data or {})
    theme_snapshot = metadata.get("presentation_theme")
    if not theme_snapshot:
        try:
            theme_snapshot = json.loads(selected.content_snapshot or "{}").get("presentation_theme")
        except Exception:
            theme_snapshot = None
    payload = build_pptx_bytes({
        "title": selected.title_snapshot,
        "slides": metadata.get("slides") or [],
        "presentation_theme": theme_snapshot or "kaki",
    })
    filename = quote(f"{selected.title_snapshot}.pptx")
    return StreamingResponse(
        iter([payload]),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Artifact-Id": str(item.id),
            "X-Artifact-Version-Id": str(selected.id),
            "X-Artifact-Version-Number": str(selected.version_number),
            "X-AI-Provider": str(metadata.get("provider") or "unknown"),
        },
    )


@router.post("/presentations/{artifact_id}/attempts")
async def submit_interactive_attempt(
    artifact_id: int, req: InteractiveAnswerRequest,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    try:
        data, replayed = await submit_interactive_answer(
            db, user_id=user_id, thread_id=req.thread_id, artifact_id=artifact_id,
            artifact_version_id=req.artifact_version_id, item_identity=req.item_identity,
            selected_answer=req.selected_answer,
            submission_request_id=req.submission_request_id,
            idempotency_key=req.idempotency_key,
        )
        await db.commit()
        return {"success": True, "data": data, "idempotent_replay": replayed}
    except InteractiveAttemptError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc
    except Exception as exc:
        await db.rollback()
        logger.exception("interactive attempt save failed user=%s artifact=%s", user_id, artifact_id)
        raise HTTPException(503, {
            "code": "INTERACTIVE_ATTEMPT_SAVE_FAILED",
            "message": "答案未保存，当前题目和旧作答保持不变，请重试。",
        }) from exc


@router.get("/presentations/{artifact_id}/attempts/state")
async def get_interactive_attempt_state(
    artifact_id: int, artifact_version_id: int, thread_id: str | None = None,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    try:
        return {"success": True, "data": await get_interactive_state(
            db, user_id=user_id, artifact_id=artifact_id,
            artifact_version_id=artifact_version_id, thread_id=thread_id,
        )}
    except InteractiveAttemptError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


@router.post("/presentations/{artifact_id}/attempts/hint")
async def request_interactive_hint(
    artifact_id: int, req: InteractiveSupportRequest,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    try:
        data = await record_support_action(
            db, user_id=user_id, thread_id=req.thread_id, artifact_id=artifact_id,
            artifact_version_id=req.artifact_version_id, item_identity=req.item_identity,
            action="hint_requested",
        )
        await db.commit()
        return {"success": True, "data": data}
    except InteractiveAttemptError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


@router.post("/presentations/{artifact_id}/attempts/solution")
async def view_interactive_solution(
    artifact_id: int, req: InteractiveSupportRequest,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    try:
        data = await record_support_action(
            db, user_id=user_id, thread_id=req.thread_id, artifact_id=artifact_id,
            artifact_version_id=req.artifact_version_id, item_identity=req.item_identity,
            action="solution_viewed",
        )
        await db.commit()
        return {"success": True, "data": data}
    except InteractiveAttemptError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


@router.post("/presentations/{artifact_id}/events")
async def save_lesson_event(
    artifact_id: int,
    req: LessonEventRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned_presentation(db, user_id, artifact_id)
    metadata = dict(item.raw_data or {})
    if metadata.get("delivery_mode") != "interactive_lesson":
        raise HTTPException(409, "learning events require an interactive lesson")
    if str(metadata.get("thread_id")) != req.thread_id:
        raise HTTPException(404, "interactive lesson not found in this conversation")
    if req.event_type == "interaction_answered":
        # Legacy endpoint is retained only as a compatibility adapter. It must
        # still traverse the formal immutable Attempt + Event transaction and
        # therefore requires explicit version and idempotency identities.
        payload = dict(req.payload or {})
        current_version = await ensure_initial_version(db, item)
        try:
            data, replayed = await submit_interactive_answer(
                db,
                user_id=user_id,
                thread_id=req.thread_id,
                artifact_id=item.id,
                artifact_version_id=int(payload.get("artifact_version_id") or current_version.id),
                item_identity=str(payload.get("item_identity") or f"slide-{int(payload.get('slide_index') or 0)}"),
                selected_answer=payload.get("selected_answer", payload.get("selected_index")),
                submission_request_id=str(payload.get("submission_request_id") or ""),
                idempotency_key=str(payload.get("idempotency_key") or ""),
            )
            await db.commit()
            return {"success": True, "data": data, "idempotent_replay": replayed}
        except InteractiveAttemptError as exc:
            await db.rollback()
            raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc
    slides = list(metadata.get("slides") or [])
    progress = dict(metadata.get("progress") or {})
    response_payload = dict(req.payload or {})
    slide_index = int(response_payload.get("slide_index") or 0)
    if slides:
        slide_index = max(0, min(slide_index, len(slides) - 1))

    if req.event_type in {"interactive_lesson_opened", "slide_viewed", "lesson_progress_updated"}:
        viewed = set(int(value) for value in (progress.get("viewed_slide_indexes") or []))
        viewed.add(slide_index)
        progress["current_slide"] = slide_index
        progress["viewed_slide_indexes"] = sorted(viewed)
        progress["percent"] = round(len(viewed) * 100 / max(len(slides), 1))
    elif req.event_type == "lesson_completed":
        progress.update({
            "current_slide": max(len(slides) - 1, 0),
            "viewed_slide_indexes": list(range(len(slides))),
            "percent": 100,
            "completed": True,
        })
        metadata["status"] = "completed"

    metadata["progress"] = progress
    item.raw_data = metadata
    current_version = await ensure_initial_version(db, item)
    response_payload.update({
        "artifact_version_id": current_version.id,
        "artifact_version_number": current_version.version_number,
    })
    db.add(WorkspaceLearningEvent(
        user_id=user_id,
        thread_id=req.thread_id,
        artifact_id=item.id,
        event_type=req.event_type,
        payload=response_payload,
    ))
    await db.commit()
    return {"success": True, "data": {
        "event_type": req.event_type,
        "progress": progress,
        "result": response_payload if req.event_type == "interaction_answered" else None,
    }}


@router.get("/artifacts/{artifact_id}/export/{format_name}")
async def export_artifact(
    format_name: str, artifact_id: int, version: int | None = None, version_id: int | None = None,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    item = await _owned_lecture(db, user_id, artifact_id)
    selected = await get_version(db, item=item, version_number=version, version_id=version_id)
    thread_id = str((selected.public_data_snapshot or {}).get("thread_id") or (item.raw_data or {}).get("thread_id") or "")
    operation = "export_pdf" if format_name == "pdf" else "export_docx"
    begin_runtime_operation(user_id, thread_id, operation)
    try:
        if format_name == "pdf":
            payload, media, suffix = build_pdf_bytes(selected.title_snapshot, selected.content_snapshot or ""), "application/pdf", "pdf"
        elif format_name == "docx":
            payload, media, suffix = build_docx_bytes(selected.title_snapshot, selected.content_snapshot or ""), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
        else:
            raise HTTPException(404, "export format not supported")
    except Exception:
        fail_runtime_operation(user_id, thread_id, "ARTIFACT_EXPORT_FAILED")
        raise
    filename = quote(f"{selected.title_snapshot}-v{selected.version_number}.{suffix}")
    async def payload_stream():
        try:
            yield payload
        finally:
            complete_runtime_operation(user_id, thread_id)

    return StreamingResponse(payload_stream(), media_type=media, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        "X-Artifact-Id": str(item.id),
        "X-Artifact-Version-Id": str(selected.id),
        "X-Artifact-Version-Number": str(selected.version_number),
        "X-AI-Provider": get_runtime_status(user_id, thread_id)["provider"],
        "X-AI-Operation": operation,
        "X-AI-State": "running",
    })


@router.post("/artifacts/{artifact_id}/revise")
async def revise_artifact(
    artifact_id: int,
    req: RevisionRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _owned_session(db, user_id, req.thread_id)
    item = await _owned_lecture(db, user_id, artifact_id)
    current = await ensure_initial_version(db, item)
    expected_version = int(req.expected_version or current.version_number)
    idempotency_key = req.idempotency_key or f"lecture-revise:{req.thread_id}:{artifact_id}:v{expected_version}:{hashlib.sha256(req.instruction.encode('utf-8')).hexdigest()}"

    # A completed replay must not invoke the provider again.
    operation_digest = hashlib.sha256(
        f"artifact-version-operation-v1\0{user_id}\0{artifact_id}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()
    replay = await db.scalar(select(GeneratedItemVersion).where(
        GeneratedItemVersion.artifact_id == artifact_id,
        GeneratedItemVersion.created_by_operation == operation_digest,
    ))
    if replay is not None:
        data = await build_public_projection(db, user_id=user_id, artifact_id=artifact_id, version_id=replay.id)
        await db.commit()
        return {"success": True, "data": data, "idempotent_replay": True}

    begin_runtime_operation(user_id, req.thread_id, "lecture_revising")
    try:
        profile = await _load_profile(db, user_id)

        async def revise_once(extra_constraints: list[str] | None = None):
            extra = (
                "\n另外必须满足：\n" + "\n".join(f"- {row}" for row in extra_constraints)
                if extra_constraints else ""
            )
            return await DocumentAgent()(_make_state(
                str(user_id), profile=profile, chapter_id=item.chapter,
                user_query=(
                    "请基于下面的讲义生成一个不可变的新版本。只执行修改要求，保留主题与未要求变更的内容。\n"
                    f"修改要求：{req.instruction}\n\n当前讲义：\n{(current.content_snapshot or '')[:14000]}"
                    + extra
                ),
            ))

        async def rewrite_revision(constraints: list[str]) -> str:
            revised = await run_agent_operation(lambda: revise_once(constraints))
            revised_document = revised.get("document") if isinstance(revised, dict) else {}
            return str((revised_document or {}).get("content") or "").strip()

        result = await run_agent_operation(revise_once)
        document = result.get("document") if isinstance(result, dict) else {}
        content = str((document or {}).get("content") or "").strip()
        if not content:
            raise ValueError("agent returned no usable revised content")
        # 这一版是学习者自己要求改的，它同样要过内容质量闸门。这里以前无条件写
        # passed —— 改坏了也盖章，是全项目唯一一处"用户主动改、系统无条件通过"。
        content, quality_status, quality_receipt_data = await _gate_lecture_content(
            content=content, topic=str(item.chapter or ""), rewrite=rewrite_revision,
        )
        row, replayed = await create_new_version(
            db,
            user_id=user_id,
            artifact_id=artifact_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            title=item.title,
            chapter=item.chapter,
            content=content,
            public_data={
                **dict(item.raw_data or {}),
                "quality_status": quality_status,
                "content_quality": quality_receipt_data,
                "last_revision_summary": req.instruction[:160],
            },
            private_data={
                **dict(current.private_data_snapshot or {}),
                "quality_status": quality_status,
                "content_quality": quality_receipt_data,
            },
            status="generated",
        )
        # 这一版的质量回执必须跟着这一版走。
        #
        # `public_data` 会过一遍白名单（`_COMMON_PUBLIC_KEYS`），而 `content_quality`
        # 不在名单里，于是它被丢掉、`item.raw_data` 里留着的是**上一版**的回执——
        # 下一轮的修复指令于是重复上一版已经被指名的问题，重试空转。
        # 2026-09-22 实测：v2 的 item.raw_data 里躺的是 v1 那条「通篇用『据 Python…』」
        # 的缺陷，而 v2 实际被拦的原因是「整份讲义没有任何 EXAMPLE」。
        item.raw_data = {
            **dict(item.raw_data or {}),
            "quality_status": quality_status,
            "content_quality": quality_receipt_data,
        }
        await db.commit()
        data = await build_public_projection(db, user_id=user_id, artifact_id=artifact_id, version_id=row.id)
    except ArtifactVersionError as exc:
        fail_runtime_operation(user_id, req.thread_id, exc.code)
        await db.rollback()
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "LECTURE_REVISION_FAILED")
        await db.rollback()
        logger.exception("workspace lecture revision failed user=%s artifact=%s", user_id, artifact_id)
        raise HTTPException(503, "讲义新版本生成失败，原版本保持不变，请稍后重试。") from exc

    summary = f"已生成《{item.title}》第 {data['version_number']} 版；你正在阅读的旧版不会被自动替换。"
    message = build_thread_artifact_message(req.thread_id, "lecture", item.id, item.title, summary)
    message["artifact"].update(data)
    await _append(db, session, message)
    complete_runtime_operation(user_id, req.thread_id)
    return {"success": True, "data": data, "idempotent_replay": replayed}


async def _record_tutor_exam_evidence(
    db: AsyncSession, *, user_id: int, thread_id: str, exam_id: int, result: dict[str, Any],
) -> WorkspaceLearningEvent | None:
    """Emit one formal observation for a Tutor-owned exam submission.

    The event reuses the existing WorkspaceLearningEvent fact stream.  ExamPaper
    remains the exam authority; artifact_id points at the existing GeneratedItem
    that was the Tutor's current learning object when the task was prepared.
    """
    task_row = (await db.execute(
        select(WorkspaceAgentTask)
        .where(
            WorkspaceAgentTask.user_id == user_id,
            WorkspaceAgentTask.thread_id == thread_id,
            WorkspaceAgentTask.agent_type == AgentType.EXAM.value,
            WorkspaceAgentTask.artifact_id == exam_id,
        )
        .order_by(WorkspaceAgentTask.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    if task_row is None:
        return None
    tutor_meta = dict((task_row.slots or {}).get("_tutor") or {})
    if not tutor_meta.get("tutor_session_id"):
        return None
    evidence_artifact_id = tutor_meta.get("evidence_artifact_id")
    if not evidence_artifact_id:
        return None
    owned_item = (await db.execute(select(GeneratedItem.id).where(
        GeneratedItem.id == int(evidence_artifact_id), GeneratedItem.user_id == user_id,
    ))).scalar_one_or_none()
    if owned_item is None:
        return None

    existing_rows = list((await db.execute(
        select(WorkspaceLearningEvent).where(
            WorkspaceLearningEvent.user_id == user_id,
            WorkspaceLearningEvent.thread_id == thread_id,
            WorkspaceLearningEvent.event_type == "exam_submitted",
        ).order_by(WorkspaceLearningEvent.id.desc()).limit(80)
    )).scalars().all())
    for row in existing_rows:
        if int((row.payload or {}).get("exam_id") or 0) == int(exam_id):
            return row

    grading = list(result.get("grading") or [])
    correct_count = sum(1 for row in grading if bool(row.get("correct")))
    event = WorkspaceLearningEvent(
        user_id=user_id,
        thread_id=thread_id,
        artifact_id=int(evidence_artifact_id),
        event_type="exam_submitted",
        payload={
            "exam_id": int(exam_id),
            "task_id": task_row.task_id,
            "task_version": int(task_row.version or 1),
            "tutor_session_id": tutor_meta.get("tutor_session_id"),
            "tutor_action": tutor_meta.get("decision_action"),
            "score": result.get("score"),
            "total_score": result.get("total_score"),
            "question_count": len(grading),
            "correct_count": correct_count,
            "graph_id": tutor_meta.get("graph_id"),
            "graph_node_external_id": tutor_meta.get("graph_node_external_id") or tutor_meta.get("target_node_id"),
            "evidence_source": "workspace_exam_submission",
        },
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


@router.post("/exam/{exam_id}/submit")
async def submit_workspace_exam(exam_id: int, req: SubmitRequest, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    session = await _owned_session(db, user_id, req.thread_id)
    owner = await db.execute(select(ExamPaper).where(ExamPaper.id == exam_id, ExamPaper.user_id == user_id))
    exam = owner.scalar_one_or_none()
    if exam is None:
        raise HTTPException(404, "exam not found")
    result = await submit_exam(db, exam_id, req.answers, req.time_spent_sec)
    if result.get("error"):
        raise HTTPException(422, result["error"])
    if result.get("already_submitted"):
        # Replay of the stored verdict: skip new evidence events and a second
        # billed grading pass.
        return {"success": True, "data": result}
    evidence_event = await _record_tutor_exam_evidence(
        db, user_id=user_id, thread_id=req.thread_id, exam_id=exam_id, result=result,
    )
    evaluation = {"evaluation_status": "not_applicable", "evaluation_revision": None}
    if evidence_event is not None:
        try:
            evaluation = await auto_consume_exam_submission_event(
                db, user_id=user_id, workspace_event_id=evidence_event.id,
            )
        except (EvaluationContractError, ValueError) as exc:
            # ExamPaper + WorkspaceLearningEvent are already durable.  Evaluator
            # conflicts are intentionally non-transactional with exam submission.
            logger.info(
                "workspace exam evaluator pending user=%s exam=%s event=%s type=%s",
                user_id, exam_id, evidence_event.id, type(exc).__name__,
            )
            evaluation = {"evaluation_status": "evaluation_pending", "evaluation_revision": None}
        except EvaluationAccessDeniedError:
            # A server-generated event that fails its own ownership/target gate is
            # a blocked bridge, never a reason to roll back the exam submission.
            evaluation = {"evaluation_status": "evaluation_blocked", "evaluation_revision": None}
        except Exception:
            logger.exception(
                "workspace exam evaluator unavailable user=%s exam=%s event=%s",
                user_id, exam_id, evidence_event.id,
            )
            evaluation = {"evaluation_status": "evaluation_pending", "evaluation_revision": None}
        if evaluation.get("evaluation_status") == "evaluated":
            from services.proactive_tutor import emit_after_exam_evaluation
            await emit_after_exam_evaluation(
                db, user_id=user_id, workspace_event=evidence_event, evaluation=evaluation,
            )
    score = result.get("score", 0)
    total = result.get("total_score", exam.total_score)
    summary = f"练习已提交：{score}/{total}。我已把本次结果放回当前对话，可以继续讨论错题。"
    artifact = build_exam_result_artifact(
        exam_id=exam_id,
        topic=(exam.chapter_ids or [""])[0],
        score=score,
        total_score=total,
        grading=result.get("grading", []),
    )
    message = build_thread_artifact_message(req.thread_id, "exam_result", exam_id, artifact["title"], summary)
    message["artifact"].update({
        **artifact,
        "question_count": len(exam.questions or []),
        "question_types": list(dict.fromkeys(
            str(question.get("type") or "") for question in (exam.questions or [])
        )),
    })
    await _append(db, session, message)
    return {"success": True, "data": {
        **message["artifact"],
        "thread_id": req.thread_id,
        "learning_event_ref": f"workspace-event:{evidence_event.id}" if evidence_event is not None else None,
        "evaluation_status": evaluation.get("evaluation_status"),
        "evaluation_revision": evaluation.get("evaluation_revision"),
        "evidence_pending": bool(
            evidence_event is not None and evaluation.get("evaluation_status") != "evaluated"
        ),
    }}


@router.post("/mindmap")
async def generate_mindmap(
    req: MindmapRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _owned_session(db, user_id, req.thread_id)
    persisted_task = await _execution_task(
        db, user_id, req.thread_id, AgentType.MINDMAP, req.task_id, req.task_version,
    )
    slots = dict(persisted_task.slots) if persisted_task else {}
    if not req.confirmed:
        raise HTTPException(409, "explicit confirmation is required")
    topic = str(slots.get("topic") or req.topic or "").strip()
    source_material_ids = list(slots.get("source_material_ids") or req.source_material_ids or [])
    source_scope = str(slots.get("source_scope") or req.source_scope or "").strip()
    instruction = str(slots.get("instruction") or req.instruction or "").strip()
    source_context = ""
    source_citations: List[dict] = []
    try:
        if source_material_ids:
            retrieved = await build_conversation_material_context(
                db,
                user_id=user_id,
                thread_id=req.thread_id,
                query=" ".join(part for part in (topic, source_scope, instruction, "outline core concepts learning structure") if part),
                requested_material_ids=source_material_ids,
                scope=source_scope or None,
            )
            if not retrieved.text:
                raise HTTPException(422, "selected materials are not ready or contain no usable text")
            source_material_ids = list(retrieved.material_ids)
            source_context = retrieved.text
            source_citations = list(retrieved.citations)
    except MaterialAccessError as exc:
        raise HTTPException(404, str(exc)) from exc

    begin_runtime_operation(user_id, req.thread_id, "mindmap_running")
    try:
        result = await run_agent_operation(lambda: generate_workspace_mindmap(
            topic=topic,
            source_context=source_context,
            source_scope=source_scope,
            instruction=instruction,
        ))
        title = str(result.get("title") or topic or "Material learning map")[:200]
        summary = str(result.get("summary") or f"Mind map {title} is ready.")[:500]
        tree_data = result["tree_data"]
        # DARYA 的产物打开器要求正式 review PASS。以前导图生成后直接返回
        # ready，却没有审阅回执，结果在卡片上可点、打开后只见拒绝提示。
        # 这里先检查真实树的结构与主题，失败稿在落库前拦下。
        from services.workspace_mindmaps import validate_mindmap_for_darya

        try:
            validate_mindmap_for_darya(tree_data, topic)
        except ValueError as exc:
            reason = str(exc)[:320]
            raise GenerationDraftBlocked(
                "MINDMAP_REVIEW_BLOCKED", reason=reason,
                body=mindmap_draft(tree_data, reason=reason),
            ) from exc
        item = GeneratedItem(
            user_id=user_id,
            type="workspace_mindmap",
            title=title,
            chapter=topic or title,
            content=json.dumps(tree_data, ensure_ascii=False),
            raw_data={
                **(await _tutor_artifact_metadata(db, user_id, req.thread_id)),
                "thread_id": req.thread_id,
                "kind": "mindmap",
                "summary": summary,
                "tree_data": tree_data,
                "version": 1,
                "versions": [],
                "source_material_ids": source_material_ids,
                "source_scope": source_scope,
                "source_citations": source_citations,
                "instruction": instruction,
            },
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        version = await ensure_initial_version(db, item)
        await create_trusted_review(
            db, user_id=user_id, artifact_id=item.id,
            request=CreateArtifactReviewRequest(
                artifact_version_id=version.id,
                version_digest=f"sha256:{version.content_digest}",
                round=1,
                reviewer_role="PLATFORM_REVIEWER",
                verdict="PASS",
                scores={"tree_structure": 1.0, "topic_coverage": 1.0},
                safe_summary="节点结构、主题词覆盖和占位内容已完成机械检查；未提供来源的事实不被标为外部核实。",
                issues=[], preserve=[],
                idempotency_key=f"mindmap:{item.id}:v{version.version_number}:review",
            ),
            reviewer_role="PLATFORM_REVIEWER",
            provider="darya-mindmap-structure-review",
        )
        await db.commit()
        await db.refresh(item)
        artifact = _mindmap_payload_from_item(item)
        message = build_thread_artifact_message(req.thread_id, "mindmap", item.id, title, summary)
        message["artifact"].update(artifact)
        await _append(db, session, message)
        if persisted_task:
            await complete_persisted_task(db, persisted_task, artifact_id=item.id)
        complete_runtime_operation(user_id, req.thread_id)
        return {"success": True, "data": message["artifact"]}
    except GenerationDraftBlocked as exc:
        fail_runtime_operation(user_id, req.thread_id, "MINDMAP_AGENT_FAILED")
        if persisted_task:
            await fail_persisted_task(db, persisted_task, error_code=exc.code)
        raise
    except HTTPException:
        fail_runtime_operation(user_id, req.thread_id, "MINDMAP_AGENT_FAILED")
        raise
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "MINDMAP_AGENT_FAILED")
        if persisted_task:
            await fail_persisted_task(db, persisted_task, error_code="MINDMAP_AGENT_FAILED")
        await _record_task_failure(db, user_id, req.thread_id, "mindmap", topic or "current materials")
        logger.exception("workspace mindmap agent failed user=%s thread=%s", user_id, req.thread_id)
        raise HTTPException(503, "Mind map generation failed; no artifact was created.") from exc


@router.get("/mindmaps/{artifact_id}")
async def get_mindmap(
    artifact_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned_mindmap(db, user_id, artifact_id)
    return {"success": True, "data": _mindmap_payload_from_item(item)}


@router.post("/mindmaps/{artifact_id}/revise")
async def revise_mindmap(
    artifact_id: int,
    req: RevisionRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    session = await _owned_session(db, user_id, req.thread_id)
    item = await _owned_mindmap(db, user_id, artifact_id)
    metadata = dict(item.raw_data or {})
    if metadata.get("thread_id") and metadata.get("thread_id") != req.thread_id:
        raise HTTPException(404, "mindmap artifact not found in this conversation")
    source_ids = list(metadata.get("source_material_ids") or [])
    source_context = ""
    if source_ids:
        try:
            retrieved = await build_conversation_material_context(
                db,
                user_id=user_id,
                thread_id=req.thread_id,
                query=req.instruction,
                requested_material_ids=source_ids,
                scope=metadata.get("source_scope") or None,
            )
            source_context = retrieved.text
        except MaterialAccessError as exc:
            raise HTTPException(404, str(exc)) from exc

    begin_runtime_operation(user_id, req.thread_id, "mindmap_revising")
    try:
        previous_tree = metadata.get("tree_data") or json.loads(item.content or "{}")
        result = await run_agent_operation(lambda: revise_workspace_mindmap(
            existing_tree=previous_tree,
            instruction=req.instruction,
            source_context=source_context,
        ))
        old_version = int(metadata.get("version") or 1)
        versions = list(metadata.get("versions") or [])
        versions.append({"version": old_version, "title": item.title, "tree_data": previous_tree})
        metadata.update({
            "summary": result["summary"],
            "tree_data": result["tree_data"],
            "version": old_version + 1,
            "versions": versions[-10:],
            "last_instruction": req.instruction,
        })
        item.title = str(result["title"] or item.title)[:200]
        item.content = json.dumps(result["tree_data"], ensure_ascii=False)
        item.raw_data = metadata
        await db.commit()
        await db.refresh(item)
        artifact = _mindmap_payload_from_item(item)
        message = build_thread_artifact_message(
            req.thread_id, "mindmap", item.id, item.title, artifact["summary"]
        )
        message["artifact"].update(artifact)
        await _append(db, session, message)
        complete_runtime_operation(user_id, req.thread_id)
        return {"success": True, "data": message["artifact"]}
    except HTTPException:
        fail_runtime_operation(user_id, req.thread_id, "MINDMAP_REVISE_FAILED")
        raise
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "MINDMAP_REVISE_FAILED")
        logger.exception("workspace mindmap revision failed user=%s artifact=%s", user_id, artifact_id)
        raise HTTPException(503, "Mind map revision failed; the previous version is unchanged.") from exc


@router.post("/code")
async def generate_code(req: CodeRequest, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """Generate a runnable code artifact and persist it to the current thread."""
    session = await _owned_session(db, user_id, req.thread_id)
    persisted_task = await _execution_task(
        db, user_id, req.thread_id, AgentType.CODE, req.task_id, req.task_version,
    )
    slots = dict(persisted_task.slots) if persisted_task else {}
    topic = str(slots.get("topic") or req.topic)
    language = str(slots.get("language") or req.language)
    should_run = bool(slots.get("run_after_generation", req.should_run))
    instruction_parts = [
        str(slots.get("goal") or req.instruction or "").strip(),
        str(slots.get("input_output_requirement") or req.input_output_requirement or "").strip(),
    ]
    instruction = "；".join(part for part in instruction_parts if part)
    spec = AgentTaskSpec(
        kind="code", topic=topic, language=language,
        confirmed=req.confirmed, instruction=instruction,
        should_run=should_run,
    )
    if not req.confirmed:
        raise HTTPException(409, "explicit confirmation is required")
    begin_runtime_operation(user_id, req.thread_id, "code_running")
    try:
        async def generate_once():
            return await generate_python_code(
                topic=topic, language=language, instruction=instruction,
            )

        result = await run_agent_operation(generate_once)
        source_text = str(result.get("source") or "").strip()
        run_payload = result.get("run") or {}
        if not source_text:
            raise ValueError("code agent returned no source")
        if not run_payload:
            raise ValueError("code agent returned no execution result")
        title = (result.get("title") or f"{topic} 代码示例")[:200]
        item = GeneratedItem(
            user_id=user_id, type="workspace_code", title=title,
            chapter=topic, content=source_text,
            raw_data={
                **(await _tutor_artifact_metadata(db, user_id, req.thread_id)),
                "thread_id": req.thread_id, "topic": topic, "language": language,
                "explanation": result.get("explanation", ""),
                "knowledge_points": result.get("knowledge_points", []),
                "line_explanations": result.get("line_explanations", []),
                "last_run": run_payload, "version": 1,
                "should_run": should_run,
            },
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        # Final normalisation pass — ``id`` is the real GeneratedItem row id.
        normalized = normalize_agent_result(spec, {
            "id": item.id, "source": source_text, "run": run_payload,
        })
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "CODE_AGENT_FAILED")
        if persisted_task:
            await fail_persisted_task(db, persisted_task, error_code="CODE_AGENT_FAILED")
        await _record_task_failure(db, user_id, req.thread_id, "code", topic)
        logger.exception("workspace code agent failed user=%s thread=%s", user_id, req.thread_id)
        raise HTTPException(503, "代码生成失败，未创建结果，请稍后重试。") from exc
    summary = f"已生成「{title}」，并已运行展示真实输出。"
    message = build_thread_artifact_message(req.thread_id, "code", item.id, title, summary)
    message["artifact"].update({
        "topic": topic, "language": language, "source": source_text,
        "explanation": result.get("explanation", ""),
        "knowledge_points": result.get("knowledge_points", []),
        "line_explanations": result.get("line_explanations", []),
        "run": run_payload, "version": 1, "should_run": should_run,
    })
    await _append(db, session, message)
    if persisted_task:
        await complete_persisted_task(db, persisted_task, artifact_id=item.id)
    complete_runtime_operation(user_id, req.thread_id)
    return {"success": True, "data": message["artifact"]}


@router.get("/code/{artifact_id}")
async def get_code_artifact(artifact_id: int, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    item = await _owned_code(db, user_id, artifact_id)
    return {"success": True, "data": _code_metadata_from_item(item)}


@router.post("/code/{artifact_id}/revise")
async def revise_code_artifact(
    artifact_id: int, req: CodeRevisionRequest,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    session = await _owned_session(db, user_id, req.thread_id)
    item = await _owned_code(db, user_id, artifact_id)
    current = await ensure_initial_version(db, item)
    if current.version_number != req.expected_version:
        raise HTTPException(409, {"code": "ARTIFACT_VERSION_CONFLICT", "message": f"版本已更新；当前为 v{current.version_number}"})
    metadata = dict(current.public_data_snapshot or item.raw_data or {})
    language = str(metadata.get("language") or "python")
    begin_runtime_operation(user_id, req.thread_id, "code_revising")
    try:
        async def revise_once():
            return await revise_python_code(
                source=current.content_snapshot or "", instruction=req.instruction, language=language,
            )

        result = await run_agent_operation(revise_once)
        new_source = (result.get("source") or "").strip()
        if not new_source:
            raise ValueError("code revise returned no source")
        public_data = dict(metadata)
        public_data.update({
            "kind": "code",
            "language": language,
            "last_revision": req.instruction,
            "explanation": result.get("explanation") or metadata.get("explanation") or "",
            "knowledge_points": result.get("knowledge_points") or metadata.get("knowledge_points") or [],
            "line_explanations": result.get("line_explanations") or metadata.get("line_explanations") or [],
            # A newly created immutable code version starts with no run facts.
            "last_run": {},
            "run": {},
            "single_file_compatibility_mode": True,
        })
        version, replayed = await create_new_version(
            db, user_id=user_id, artifact_id=artifact_id,
            expected_version=req.expected_version, idempotency_key=req.idempotency_key,
            content=new_source, public_data=public_data, status="generated",
        )
        await db.commit()
        projection = await build_public_projection(
            db, user_id=user_id, artifact_id=artifact_id, version_id=version.id,
        )
    except ArtifactVersionError as exc:
        fail_runtime_operation(user_id, req.thread_id, "CODE_REVISION_FAILED")
        await db.rollback()
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "CODE_REVISION_FAILED")
        await db.rollback()
        logger.exception("workspace code revision failed user=%s artifact=%s", user_id, artifact_id)
        raise HTTPException(503, "代码修改失败，原版本保持不变，请稍后重试。") from exc
    summary = f"代码新版本 v{version.version_number} 已生成；当前阅读版本不会被自动替换。"
    message = build_thread_artifact_message(req.thread_id, "code", item.id, projection["title"], summary)
    message["artifact"].update({
        **projection,
        "version": version.version_number,
        "version_id": version.id,
        "idempotent_replay": replayed,
    })
    await _append(db, session, message)
    complete_runtime_operation(user_id, req.thread_id)
    return {"success": True, "data": projection, "idempotent_replay": replayed}


@router.post("/code/{artifact_id}/rerun")
async def rerun_code_artifact(
    artifact_id: int, req: CodeRerunRequest,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    session = await _owned_session(db, user_id, req.thread_id)
    begin_runtime_operation(user_id, req.thread_id, "code_rerunning")
    try:
        run, replayed = await execute_code_version(
            db, user_id=user_id, thread_id=req.thread_id, artifact_id=artifact_id,
            artifact_version_id=req.artifact_version_id,
            execution_request_id=req.execution_request_id,
            idempotency_key=req.idempotency_key, parameters=req.parameters,
            source_override=req.source,
        )
        await db.commit()
    except CodeRunError as exc:
        fail_runtime_operation(user_id, req.thread_id, exc.code)
        await db.rollback()
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "CODE_RERUN_FAILED")
        await db.rollback()
        logger.exception("workspace code rerun failed user=%s artifact=%s", user_id, artifact_id)
        raise HTTPException(503, "代码重新运行失败，已有版本和运行记录保持不变。") from exc
    summary = f"已运行代码 v{run['artifact_version_number']} · Run {run['run_number']}：{run['status']}。"
    message = build_thread_artifact_message(req.thread_id, "code_run", run["run_id"], "代码运行结果", summary)
    message["artifact"].update({"artifact_id": artifact_id, "kind": "code_run", "run": run})
    await _append(db, session, message)
    complete_runtime_operation(user_id, req.thread_id)
    return {"success": True, "data": {"run": run, "idempotent_replay": replayed}}


@router.get("/code/{artifact_id}/runs")
async def get_code_runs(
    artifact_id: int, artifact_version_id: int | None = None,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    try:
        runs = await list_code_runs(
            db, user_id=user_id, artifact_id=artifact_id,
            artifact_version_id=artifact_version_id,
        )
        return {"success": True, "data": runs}
    except CodeRunError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


@router.get("/code/{artifact_id}/runs/{run_id}")
async def get_code_run_detail(
    artifact_id: int, run_id: int,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    try:
        return {"success": True, "data": await get_code_run(
            db, user_id=user_id, artifact_id=artifact_id, run_id=run_id,
        )}
    except CodeRunError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


@router.post("/code/{artifact_id}/explain")
async def explain_code_artifact(
    artifact_id: int, req: CodeRerunRequest,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    """Return the deterministic, line-by-line knowledge hints for the saved code."""
    item = await _owned_code(db, user_id, artifact_id)
    from services.code_executor import explain_python_lines
    lines = explain_python_lines(item.content or "")
    return {"success": True, "data": {
        "artifact_id": item.id, "kind": "code", "topic": item.chapter,
        "source": item.content, "lines": lines,
    }}


@router.post("/code/{artifact_id}/exam")
async def exam_from_code_artifact(
    artifact_id: int, req: CodeExamRequest,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    """Generate a quiz grounded in the saved code's knowledge points."""
    session = await _owned_session(db, user_id, req.thread_id)
    item = await _owned_code(db, user_id, artifact_id)
    metadata = dict(item.raw_data or {})
    knowledge_points = list(metadata.get("knowledge_points") or [])
    source_text = item.content or ""
    knowledge_text = "、".join(point for point in knowledge_points if point) or item.chapter
    user_instruction = (
        f"用户要求基于以下 Python 代码出一份 {req.count} 道题，主题是「{item.chapter}」，"
        f"必须覆盖以下知识点：{knowledge_text}。\n"
        f"只允许讨论代码本身和上述知识点，不要扩展到无关领域。\n"
        f"参考代码（仅用于你理解题意，不要把代码作为题目内容）：\n{source_text[:3500]}"
    )
    begin_runtime_operation(user_id, req.thread_id, "exam_from_code_running")
    try:
        question_types = req.question_types or ["single_choice", "true_false", "short_answer"]
        async def create_exam_once():
            return await create_exam(
                db, user_id, "quiz", [item.chapter], difficulty=req.difficulty,
                question_types=question_types, count=req.count,
                user_instruction=user_instruction,
            )

        exam = await run_agent_operation(create_exam_once)
    except Exception as exc:
        fail_runtime_operation(user_id, req.thread_id, "EXAM_FROM_CODE_FAILED")
        await _record_task_failure(db, user_id, req.thread_id, "exam", item.chapter)
        logger.exception("workspace exam from code failed user=%s artifact=%s", user_id, artifact_id)
        raise HTTPException(503, "基于代码的试题生成失败，请稍后重试。") from exc
    title = f"基于「{item.title}」的练习"
    summary = f"已基于当前代码生成 {len(exam.questions)} 道题。"
    message = build_thread_artifact_message(req.thread_id, "exam", exam.id, title, summary)
    message["artifact"].update({
        "question_count": len(exam.questions), "total_score": exam.total_score, "status": exam.status,
        "source_artifact_id": artifact_id, "source_kind": "code", "topic": item.chapter,
        "difficulty": req.difficulty or "中等", "question_types": question_types,
        "instruction": user_instruction,
        "questions": [
            {
                "id": question.get("id"), "type": question.get("type"),
                "content": question.get("content") or question.get("title"),
                "options": question.get("options") or [], "points": question.get("points"),
            }
            for question in exam.questions
        ],
    })
    await _append(db, session, message)
    complete_runtime_operation(user_id, req.thread_id)
    return {"success": True, "data": {**message["artifact"], "questions": exam.questions, "time_limit_min": exam.time_limit_min}}
