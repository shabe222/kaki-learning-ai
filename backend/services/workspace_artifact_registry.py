"""Unified read model for workspace artifacts without duplicating their data."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ChatSession, ExamPaper, GeneratedItem, TutorSession
from services.workspace_artifact_versions import build_public_projection


ArtifactScope = Literal["conversation", "tutor", "all"]

_GENERATED_KINDS = {
    "personalized_handout": "document",
    "practical_guide": "document",
    "workspace_lecture": "lecture",
    "workspace_code": "code",
    "workspace_mindmap": "mindmap",
    "workspace_presentation": "presentation",
    "workspace_interactive_lesson": "interactive_lesson",
    "workspace_question": "question",
    "workspace_video": "video",
    "study_flashcard_deck": "flashcards",
    "personalized_mistake_book": "mistake_book",
    "study_audio_lesson": "audio",
    "study_picture_book": "picture_book",
}
_FILTER_ALIASES = {
    "ppt": "presentation",
    "classroom": "interactive_lesson",
    "question": "exam",
    "handout": "handout",
    "guide": "guide",
    "assessment": "assessment",
}
_OPEN_ACTIONS = {
    "document": "open",
    "lecture": "open",
    "exam": "answer",
    "code": "inspect",
    "mindmap": "open",
    "presentation": "open",
    "interactive_lesson": "start",
    "question": "open",
    "video": "open",
    "flashcards": "open",
    "mistake_book": "open",
    "audio": "open",
    "picture_book": "read",
}


def _product_category(item_type: str, kind: str, metadata: dict) -> str:
    experience = str(dict(metadata.get("canonical_purpose") or {}).get("experience") or "").upper()
    if item_type == "personalized_handout" or experience == "PERSONALIZED_HANDOUT":
        return "handout"
    # 工作区讲义与个人化讲义同属「讲义」，不能落进 other 而从用户资料里消失
    if item_type == "workspace_lecture" or kind == "lecture":
        return "handout"
    if item_type == "practical_guide" or experience == "PRACTICAL_GUIDE":
        return "guide"
    if experience == "TIERED_ASSESSMENT" or kind in {"exam", "question"}:
        return "assessment"
    if experience == "PERSONALIZED_CODE_LAB" or kind == "code":
        return "code"
    if item_type == "study_flashcard_deck" or item_type == "personalized_mistake_book" or kind in {"flashcards", "mistake_book"}:
        return "assessment"
    if item_type == "workspace_mindmap" or kind == "mindmap":
        return "mindmap"
    if item_type in {"workspace_presentation", "workspace_interactive_lesson"} or kind in {"presentation", "interactive_lesson", "ppt"}:
        return "presentation"
    if item_type == "workspace_video" or kind == "video":
        return "video"
    if item_type == "study_audio_lesson" or kind == "audio":
        return "audio"
    if item_type == "study_picture_book" or kind == "picture_book":
        return "picture_book"
    return "other"


def _normalise_filter(value: Optional[str]) -> str:
    selected = str(value or "all").strip().lower()
    return _FILTER_ALIASES.get(selected, selected)


def _message_exam_references(chats: list[ChatSession]) -> dict[int, dict]:
    references: dict[int, dict] = {}
    for chat in chats:
        for message in list(chat.messages or []):
            artifact = message.get("artifact") if isinstance(message, dict) else None
            if not isinstance(artifact, dict) or artifact.get("kind") not in {"exam", "exam_result"}:
                continue
            artifact_id = int(artifact.get("artifact_id") or 0)
            if artifact_id:
                references[artifact_id] = {
                    "thread_id": chat.thread_id,
                    "title": artifact.get("title"),
                    "kind": "exam",
                    "source_material_ids": list(artifact.get("source_material_ids") or []),
                }
    return references


async def list_artifact_summaries(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: Optional[str] = None,
    tutor_session_id: Optional[str] = None,
    scope: ArtifactScope = "conversation",
    artifact_type: Optional[str] = None,
    limit: int = 60,
) -> list[dict]:
    """Return one user-owned list across GeneratedItem and ExamPaper."""
    selected_kind = _normalise_filter(artifact_type)
    effective_thread_id = str(thread_id or "")
    if scope == "tutor" and tutor_session_id:
        tutor = (await db.execute(select(TutorSession).where(
            TutorSession.user_id == user_id,
            TutorSession.session_id == tutor_session_id,
        ))).scalar_one_or_none()
        if tutor is None:
            return []
        effective_thread_id = tutor.thread_id

    chats_query = select(ChatSession).where(ChatSession.user_id == user_id)
    if scope in {"conversation", "tutor"}:
        if not effective_thread_id:
            return []
        chats_query = chats_query.where(ChatSession.thread_id == effective_thread_id)
    chats = list((await db.execute(chats_query)).scalars().all())
    exam_references = _message_exam_references(chats)

    rows = list((await db.execute(select(GeneratedItem).where(
        GeneratedItem.user_id == user_id,
        GeneratedItem.type.in_(tuple(_GENERATED_KINDS)),
    ))).scalars().all())
    result: list[dict] = []
    for item in rows:
        metadata = dict(item.raw_data or {})
        kind = _GENERATED_KINDS[item.type]
        projection = None
        if item.current_version_id:
            try:
                projection = await build_public_projection(
                    db, user_id=user_id, artifact_id=item.id,
                )
                metadata = dict(projection.get("raw_data") or metadata)
                kind = str(projection.get("kind") or kind)
            except Exception:
                projection = None
        category = _product_category(item.type, kind, metadata)
        if scope == "conversation" and str(metadata.get("thread_id") or "") != effective_thread_id:
            continue
        if scope == "tutor" and str(metadata.get("tutor_session_id") or "") != str(tutor_session_id or ""):
            continue
        if selected_kind != "all" and selected_kind not in {kind, category}:
            continue
        canonical = dict((projection or {}).get("canonical") or {})
        canonical_status = dict(canonical.get("status") or {})
        purpose = dict(canonical.get("purpose") or {})
        result.append({
            "artifact_id": item.id,
            "kind": kind,
            "category": category,
            "type": item.type,
            "title": str((projection or {}).get("title") or item.title),
            "topic": str((projection or {}).get("topic") or item.chapter or ""),
            "summary": str(purpose.get("learning_goal") or (projection or {}).get("topic") or item.chapter or ""),
            "thread_id": metadata.get("thread_id"),
            "tutor_session_id": metadata.get("tutor_session_id"),
            "graph_node_id": metadata.get("graph_node_id"),
            "version": int((projection or {}).get("version_number") or metadata.get("version") or 1),
            "status": str(canonical_status.get("canonical_status") or (projection or {}).get("status") or "generated"),
            "review_verdict": canonical_status.get("review_verdict"),
            "source_material_ids": list(metadata.get("source_material_ids") or []),
            # kind 可能来自 projection 覆盖，未必在 _OPEN_ACTIONS 里；缺图时退回通用打开动作，
            # 否则整个「我的资料」会因单个条目抛 KeyError 而 500。
            "open_action": _OPEN_ACTIONS.get(kind, "open"),
            "source_table": "generated_items",
            "created_at": item.created_at,
        })

    if selected_kind in {"all", "exam", "assessment"}:
        exams = list((await db.execute(select(ExamPaper).where(
            ExamPaper.user_id == user_id,
        ))).scalars().all())
        for exam in exams:
            reference = exam_references.get(exam.id)
            if scope in {"conversation", "tutor"} and reference is None:
                continue
            topics = [str(value).strip() for value in list(exam.chapter_ids or []) if str(value).strip()]
            topic = "、".join(topics[:2]) or "学习"
            result.append({
                "artifact_id": exam.id,
                "kind": "exam",
                "category": "assessment",
                "type": "workspace_exam",
                "title": (reference or {}).get("title") or f"{topic}练习题",
                "summary": f"{len(exam.questions or [])} 道题 · {exam.status}",
                "thread_id": (reference or {}).get("thread_id"),
                "tutor_session_id": tutor_session_id if scope == "tutor" else None,
                "graph_node_id": None,
                "version": 1,
                "source_material_ids": list((reference or {}).get("source_material_ids") or []),
                "open_action": _OPEN_ACTIONS["exam"],
                "source_table": "exam_papers",
                "created_at": exam.created_at,
            })

    result.sort(key=lambda row: row.get("created_at") or datetime.min, reverse=True)
    for row in result:
        row["created_at"] = str(row.get("created_at") or "")
    return result[:max(1, min(int(limit), 200))]
