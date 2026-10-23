"""笔记路由 — CRUD + 关联 + 版本 + AI 辅助"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import UserNote
from services.note_service import (
    ConflictError, add_link, apply_suggestion, batch_delete,
    create_ai_suggestion, create_note, create_revision, delete_note,
    dismiss_suggestion, get_all_tags, get_note, get_note_categories,
    get_revision_content, list_notes, remove_link, restore_note,
    restore_revision, run_ai_suggestion, undo_ai_application, update_note,
)
from utils.auth import get_current_user_id

router = APIRouter(prefix="/api/notes", tags=["notes"])


class CreateNoteRequest(BaseModel):
    title: str
    content: str = ""
    graph_node_id: Optional[int] = None
    graph_id: Optional[int] = None
    artifact_ids: Optional[list[int]] = None
    tags: list[str] = []
    category: str = "general"


class UpdateNoteRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    plain_text: Optional[str] = None
    graph_node_id: Optional[int] = None
    graph_id: Optional[int] = None
    material_ids: Optional[list] = None
    artifact_ids: Optional[list] = None
    tags: Optional[list[str]] = None
    category: Optional[str] = None
    is_pinned: Optional[bool] = None
    is_favorite: Optional[bool] = None
    status: Optional[str] = None
    content_format: Optional[str] = None
    _client_updated_at: Optional[str] = None


class BatchDeleteRequest(BaseModel):
    ids: list[int]


class LinkRequest(BaseModel):
    link_type: str
    target_id: int
    target_title: str = ""


class ApplySuggestionRequest(BaseModel):
    accept_content: Optional[str] = None


# ===== CRUD =====

@router.get("")
async def list_user_notes(
    category: Optional[str] = None, tag: Optional[str] = None,
    search: Optional[str] = None, graph_node_id: Optional[int] = None,
    graph_id: Optional[int] = None, pinned: bool = False, favorite: bool = False,
    status: str = "active",
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    notes = await list_notes(db, current_user_id, category=category, tag=tag,
        search=search, graph_node_id=graph_node_id, graph_id=graph_id,
        pinned_only=pinned, favorite_only=favorite, status=status)
    return {"success": True, "data": notes}


@router.post("")
async def create_new_note(
    req: CreateNoteRequest, current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    note = await create_note(db, current_user_id, title=req.title, content=req.content,
        graph_node_id=req.graph_node_id, graph_id=req.graph_id,
        artifact_ids=req.artifact_ids, tags=req.tags, category=req.category)
    return {"success": True, "data": {"id": note.id, "title": note.title}}


@router.get("/categories")
async def list_categories(current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    cats = await get_note_categories(db, current_user_id)
    return {"success": True, "data": cats}


@router.get("/tags")
async def list_tags(current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    tags = await get_all_tags(db, current_user_id)
    return {"success": True, "data": tags}


@router.get("/{note_id}")
async def get_note_detail(note_id: int, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    note = await get_note(db, note_id, current_user_id)
    if not note: raise HTTPException(404, "Note not found")
    return {"success": True, "data": note}


@router.put("/{note_id}")
async def update_note_info(note_id: int, req: UpdateNoteRequest, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    updates = {k: v for k, v in req.dict().items() if v is not None}
    try:
        note = await update_note(db, note_id, current_user_id, updates)
    except ConflictError as e:
        raise HTTPException(409, str(e))
    if not note: raise HTTPException(404, "Note not found")
    return {"success": True, "data": {"id": note.id, "updated_at": note.updated_at.isoformat() if note.updated_at else None}}


@router.delete("/{note_id}")
async def delete_note_endpoint(note_id: int, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    ok = await delete_note(db, note_id, current_user_id)
    if not ok: raise HTTPException(404, "Note not found")
    return {"success": True, "message": "Note deleted"}


@router.post("/{note_id}/restore")
async def restore_note_endpoint(note_id: int, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    ok = await restore_note(db, note_id, current_user_id)
    if not ok: raise HTTPException(404, "Note not found")
    return {"success": True, "message": "Note restored"}


@router.post("/batch-delete")
async def batch_delete_notes(req: BatchDeleteRequest, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    count = await batch_delete(db, req.ids, current_user_id)
    return {"success": True, "data": {"deleted": count}}


# ===== Links =====

@router.post("/{note_id}/links")
async def create_link(note_id: int, req: LinkRequest, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    link = await add_link(db, note_id, current_user_id, req.link_type, req.target_id, req.target_title)
    return {"success": True, "data": {"id": link.id, "link_type": link.link_type, "target_id": link.target_id}}


@router.delete("/{note_id}/links/{link_id}")
async def delete_link(note_id: int, link_id: int, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    ok = await remove_link(db, link_id, current_user_id)
    if not ok: raise HTTPException(404, "Link not found")
    return {"success": True}


# ===== Revisions =====

@router.get("/{note_id}/revisions")
async def list_revisions(note_id: int, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """获取版本历史列表"""
    from services.note_service import _get_revisions as _list_revs
    note_check = await db.execute(sa_select(UserNote).where(UserNote.id == note_id, UserNote.user_id == current_user_id))
    if not note_check.scalar_one_or_none():
        raise HTTPException(404, "Note not found")
    revs = await _list_revs(db, note_id)
    return {"success": True, "data": revs}


@router.post("/{note_id}/revisions")
async def save_revision(note_id: int, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    rev = await create_revision(db, note_id, current_user_id, change_source="user")
    if not rev: return {"success": True, "data": None}
    return {"success": True, "data": {"id": rev.id, "revision_number": rev.revision_number}}


@router.get("/{note_id}/revisions/{revision_id}")
async def get_revision(note_id: int, revision_id: int, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    rev = await get_revision_content(db, revision_id, current_user_id)
    if not rev: raise HTTPException(404, "Revision not found")
    return {"success": True, "data": rev}


@router.post("/{note_id}/revisions/{revision_id}/restore")
async def restore_revision_endpoint(note_id: int, revision_id: int, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    try:
        result = await restore_revision(db, revision_id, current_user_id)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ===== AI =====

@router.post("/{note_id}/ai-suggestions")
async def request_ai_suggestion(note_id: int, action_type: str = Query(...), current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    sug = await create_ai_suggestion(db, note_id, current_user_id, action_type)
    # Async: run AI in background
    sug = await run_ai_suggestion(db, sug.id, current_user_id)
    return {"success": True, "data": {"id": sug.id, "note_id": sug.note_id, "note_version": sug.source_revision, "status": sug.status, "action_type": sug.action_type, "apply_mode": getattr(sug, 'apply_mode', None), "suggestion_content": sug.suggestion_content, "structured_result": sug.structured_result, "tokens_used": sug.tokens_used, "provider": getattr(sug, 'provider', None), "model": getattr(sug, 'model', None)}}


@router.post("/{note_id}/ai-suggestions/{suggestion_id}/apply")
async def apply_ai_suggestion(note_id: int, suggestion_id: int, req: ApplySuggestionRequest = ApplySuggestionRequest(), current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    try:
        result = await apply_suggestion(
            db, suggestion_id, current_user_id, req.accept_content,
            expected_note_id=note_id,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        msg = str(e)
        if msg == "SUGGESTION_NOT_APPLICABLE_TO_CONTENT":
            raise HTTPException(400, detail={"code": "SUGGESTION_NOT_APPLICABLE_TO_CONTENT", "message": "此建议仅供查看，不能应用到笔记正文"})
        if msg == "ARTIFACT_GENERATION_REQUIRED":
            raise HTTPException(400, detail={"code": "ARTIFACT_GENERATION_REQUIRED", "message": "此建议需要生成独立产物，不能直接应用到笔记正文"})
        if msg == "SUGGESTION_NOTE_MISMATCH":
            raise HTTPException(409, detail={"code": "SUGGESTION_NOTE_MISMATCH", "message": "这条建议不属于当前笔记，请重新生成。"})
        if msg == "SUGGESTION_SOURCE_CHANGED":
            raise HTTPException(409, detail={"code": "SUGGESTION_SOURCE_CHANGED", "message": "笔记内容已变化，请重新生成建议。"})
        raise HTTPException(400, msg)


@router.post("/{note_id}/ai-suggestions/{suggestion_id}/dismiss")
async def dismiss_ai_suggestion(note_id: int, suggestion_id: int, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    ok = await dismiss_suggestion(db, suggestion_id, current_user_id)
    if not ok: raise HTTPException(404, "Suggestion not found")
    return {"success": True}


@router.post("/{note_id}/undo-ai")
async def undo_ai_endpoint(note_id: int, suggestion_id: int | None = Query(None), current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    try:
        result = await undo_ai_application(db, note_id, current_user_id, suggestion_id)
        return {"success": True, "data": result}
    except ValueError as e:
        msg = str(e)
        if msg == "NOTE_CHANGED_AFTER_AI_APPLY":
            raise HTTPException(409, detail={
                "code": "NOTE_CHANGED_AFTER_AI_APPLY",
                "message": "AI 建议应用后笔记已有新的修改。为避免丢失内容，请通过版本历史恢复。",
                "retryable": False,
            })
        if msg == "AI_UNDO_STATE_INCONSISTENT":
            raise HTTPException(409, detail={
                "code": "AI_UNDO_STATE_INCONSISTENT",
                "message": "数据状态不一致，请联系支持。",
                "retryable": False,
            })
        raise HTTPException(400, msg)
