"""Study resources — flashcards (FSRS review), mistake books, audio lessons.

All generation is skill-driven LLM work over owned sources; every endpoint
fails honestly (no placeholder content) when material or provider is missing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime, timezone

from database.dependencies import get_db
from database.models import GeneratedItem, TideSharedPerspectiveSnapshot
from services.audio_lesson_service import AudioLessonError, generate_audio_lesson
from services.flashcard_service import (
    FlashcardError, due_cards, generate_flashcard_deck, review_card,
)
from services.mistake_book_service import MistakeBookError, generate_mistake_book
from services.picture_book_service import PictureBookError, save_picture_book
from services.skill_library import provenance_projection
from utils.auth import get_current_user_id

router = APIRouter(prefix="/api/study", tags=["study-resources"])


def _artifact_payload(item: GeneratedItem) -> dict:
    metadata = dict(item.raw_data or {})
    return {
        "artifact_id": item.id,
        "type": item.type,
        "title": item.title,
        "content": metadata.get("content_preview") or None,
        "kind": metadata.get("kind"),
        "audio_url": metadata.get("audio_url"),
        "duration_sec": metadata.get("duration_sec"),
        "generation_provenance": metadata.get("generation_provenance"),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


class DeckRequest(BaseModel):
    source_artifact_id: Optional[int] = None
    topic: str = Field(default="", max_length=200)
    thread_id: str = Field(default="", max_length=100)


class ReviewRequest(BaseModel):
    card_id: str = Field(min_length=1, max_length=20)
    rating: int = Field(ge=1, le=4)


class MistakeBookRequest(BaseModel):
    exam_id: int
    thread_id: str = Field(default="", max_length=100)


class AudioLessonRequest(BaseModel):
    source_artifact_id: Optional[int] = None
    topic: str = Field(default="", max_length=200)
    thread_id: str = Field(default="", max_length=100)


class PictureBookRequest(BaseModel):
    """The storybook component's own payload, forwarded unchanged."""

    book: dict = Field(default_factory=dict)
    thread_id: str = Field(default="", max_length=100)


@router.get("/today")
async def study_today(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """今日学习面板：把系统积累的学习状态一次性浮出来。

    薄感的反面——学生打开第一眼看到的是"系统记得我"：
    到期的记忆卡、待复盘的错题、进行中的目标、最近的产物。
    """
    now = datetime.now(timezone.utc)
    from fsrs import Card

    due_total = 0
    due_decks = []
    decks = (await db.execute(select(GeneratedItem).where(
        GeneratedItem.user_id == current_user_id,
        GeneratedItem.type == "study_flashcard_deck",
    ).order_by(GeneratedItem.updated_at.desc()).limit(10))).scalars()
    for deck in decks:
        metadata = dict(deck.raw_data or {})
        state_map = dict(metadata.get("fsrs") or {})
        due = 0
        for state in state_map.values():
            if not isinstance(state, dict):
                continue
            try:
                card = Card.from_dict(state)
            except Exception:
                continue
            if card.due <= now:
                due += 1
        if due > 0:
            due_decks.append({"artifact_id": deck.id, "title": deck.title, "due": due})
            due_total += due
    due_decks.sort(key=lambda item: -item["due"])

    mistake_books_open = 0
    mistake_variants = 0
    mistake_rows = (await db.execute(select(GeneratedItem).where(
        GeneratedItem.user_id == current_user_id,
        GeneratedItem.type == "personalized_mistake_book",
    ).order_by(GeneratedItem.updated_at.desc()).limit(5))).scalars()
    latest_mistake_id = None
    for book in mistake_rows:
        try:
            content = json.loads(book.content or "{}")
        except json.JSONDecodeError:
            continue
        count = len(content.get("mistakes") or [])
        if count > 0:
            mistake_books_open += 1
            mistake_variants += sum(len(m.get("variants") or []) for m in content.get("mistakes", []))
            if latest_mistake_id is None:
                latest_mistake_id = book.id

    goal_snapshot = (await db.execute(select(TideSharedPerspectiveSnapshot).where(
        TideSharedPerspectiveSnapshot.user_id == current_user_id,
    ).order_by(TideSharedPerspectiveSnapshot.id.desc()).limit(1))).scalars().first()
    active_goal = None
    parallel_count = 0
    if goal_snapshot is not None:
        fields = dict(goal_snapshot.fields or {})
        goal_field = fields.get("goal")
        if isinstance(goal_field, dict) and str(goal_field.get("value") or "").strip():
            active_goal = {
                "text": str(goal_field.get("value"))[:160],
                "kind": str((fields.get("goal_kind") or {}).get("value") or "task"),
                "thread_id": goal_snapshot.thread_id,
            }
            parallel = (fields.get("parallel_goals") or {}).get("value")
            if isinstance(parallel, list):
                parallel_count = len(parallel)

    recent = (await db.execute(select(GeneratedItem).where(
        GeneratedItem.user_id == current_user_id,
        GeneratedItem.type.in_([
            "workspace_lecture", "workspace_presentation", "workspace_video",
            "study_audio_lesson", "workspace_mindmap", "personalized_handout",
        ]),
    ).order_by(GeneratedItem.updated_at.desc()).limit(3))).scalars()
    recent_artifacts = [
        {"artifact_id": item.id, "type": item.type, "title": item.title}
        for item in recent
    ]

    return {"success": True, "data": {
        "due_review": {"total": due_total, "decks": due_decks[:3]},
        "mistake_review": {"books": mistake_books_open, "variants": mistake_variants, "latest_artifact_id": latest_mistake_id},
        "active_goal": active_goal,
        "parallel_goal_count": parallel_count,
        "recent_artifacts": recent_artifacts,
        "date": now.date().isoformat(),
    }}


@router.get("/skills")
async def list_skills(current_user_id: int = Depends(get_current_user_id)):
    """Inspectable provenance of every installed generation skill."""
    _ = current_user_id
    return {"success": True, "data": provenance_projection()}


@router.post("/flashcards")
async def create_flashcards(
    req: DeckRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await generate_flashcard_deck(
            db, user_id=current_user_id, thread_id=req.thread_id,
            source_artifact_id=req.source_artifact_id, topic=req.topic,
        )
    except FlashcardError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"success": True, "data": _artifact_payload(item)}


@router.get("/flashcards/{artifact_id}/due")
async def get_due_cards(
    artifact_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(GeneratedItem, artifact_id)
    if item is None or item.user_id != current_user_id or item.type != "study_flashcard_deck":
        raise HTTPException(404, "记忆卡组不存在或无权访问。")
    queue = due_cards(item, now=datetime.now(timezone.utc))
    return {"success": True, "data": {"artifact_id": artifact_id, "due": queue}}


@router.post("/flashcards/{artifact_id}/review")
async def review(
    artifact_id: int,
    req: ReviewRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(GeneratedItem, artifact_id)
    if item is None or item.user_id != current_user_id or item.type != "study_flashcard_deck":
        raise HTTPException(404, "记忆卡组不存在或无权访问。")
    try:
        result = review_card(item, card_id=req.card_id, rating=req.rating)
    except FlashcardError as exc:
        raise HTTPException(422, str(exc)) from exc
    metadata = dict(item.raw_data or {})
    metadata.update(result["raw_metadata"])
    item.raw_data = metadata
    item.updated_at = datetime.utcnow()

    # 一次复习以前什么记录都不留：卡片按自己的时钟走，学习循环永远看不到它。
    # 现在记成一条学习事件 —— 但如实标注这是**自评**，不是服务端判定的作答。
    # 自己给自己打分和机器判一道题不是同一种证据，不能混为一谈，更不能直接写掌握度。
    from database.models import WorkspaceLearningEvent

    event = WorkspaceLearningEvent(
        user_id=current_user_id,
        thread_id=str(metadata.get("thread_id") or "")[:100] or "study-resources",
        artifact_id=item.id,
        event_type="retrieval_practice_reviewed",
        payload={
            "card_id": str(req.card_id),
            "rating": int(req.rating),
            "due": result["due"],
            "stability": result["stability"],
            "difficulty": result["difficulty"],
            "evidence_kind": "SELF_REPORTED_RETRIEVAL",
            "server_judged": False,
            "node_refs": [
                str(ref) for ref in (metadata.get("knowledge_refs") or metadata.get("node_refs") or [])
            ][:8],
        },
    )
    db.add(event)
    await db.flush()
    await db.commit()
    return {"success": True, "data": {
        "card": result["card"], "due": result["due"],
        "stability": result["stability"], "difficulty": result["difficulty"],
        "evidence_event_id": event.id,
        "evidence_kind": "SELF_REPORTED_RETRIEVAL",
    }}


@router.post("/mistake-books")
async def create_mistake_book(
    req: MistakeBookRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await generate_mistake_book(
            db, user_id=current_user_id, exam_id=req.exam_id, thread_id=req.thread_id,
        )
    except MistakeBookError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"success": True, "data": _artifact_payload(item)}


@router.post("/audio-lessons")
async def create_audio_lesson(
    req: AudioLessonRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await generate_audio_lesson(
            db, user_id=current_user_id, thread_id=req.thread_id,
            source_artifact_id=req.source_artifact_id, topic=req.topic,
        )
    except AudioLessonError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"success": True, "data": _artifact_payload(item)}


@router.post("/picture-books")
async def create_picture_book(
    req: PictureBookRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Persist a book the storybook component generated in the browser.

    The component owns storyboard and illustration work; this endpoint only
    owns the durable artifact, so a finished book can be reopened after its
    overlay closes.
    """
    try:
        item = await save_picture_book(
            db, user_id=current_user_id, payload=req.book, thread_id=req.thread_id,
        )
    except PictureBookError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"success": True, "data": _artifact_payload(item)}
