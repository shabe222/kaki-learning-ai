"""笔记服务 — CRUD、搜索、关联、版本、AI 辅助"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from database.models import UserNote, NoteLink, NoteRevision, NoteAISuggestion

logger = logging.getLogger(__name__)


# ===== Suggestion Type Canonical Registry =====
# 所有 action_type 必须先规范化为 canonical type，再决定 apply_mode。
# 前端 kind、DB 历史值、中文别名统一在此映射。

_CANONICAL_TYPE: dict[str, str] = {
    # --- 前端 kind → canonical ---
    "expand":          "expand",
    "polish":          "polish",
    "structure":       "structure",
    "summarize":       "summarize",
    "extract":         "extract_knowledge",
    "check":           "check_understanding",
    "next":            "next_step_advice",
    "questions":       "generate_exercises",
    "outline":         "outline",
    # --- 中文别名 ---
    "偏差分析":          "check_understanding",
    "下一步建议":         "next_step_advice",
    # --- 历史 DB 脏数据兼容 ---
    "exercise":         "generate_exercises",
    "understand":       "check_understanding",
    "next_step":        "next_step_advice",
    # --- 已规范的 canonical 自映射 ---
    "extract_knowledge":    "extract_knowledge",
    "check_understanding":  "check_understanding",
    "generate_exercises":   "generate_exercises",
    "extract_questions":    "extract_questions",
    "mindmap_plan":         "mindmap_plan",
    "next_step_advice":     "next_step_advice",
}

# 仅 keyed by canonical type — 不在 _CANONICAL_TYPE 中的类型永远进不来
_APPLY_MODE_BY_CANONICAL: dict[str, str] = {
    "expand":                "replace_content",
    "polish":                "replace_content",
    "structure":             "replace_content",
    "summarize":             "append_content",
    "extract_knowledge":     "append_content",
    "outline":               "append_content",
    "check_understanding":   "display_only",
    "next_step_advice":      "display_only",
    "extract_questions":     "display_only",
    "mindmap_plan":          "display_only",
    "generate_exercises":    "artifact_only",
}

# 未知类型的安全默认值 — 绝不默认为 replace_content
_FALLBACK_APPLY_MODE = "display_only"


def normalize_action_type(raw: str) -> str:
    """将任意 action_type 输入规范化为 canonical type。未知输入返回原值以便调用方处理。"""
    if not raw:
        return raw
    key = raw.strip().lower() if raw.isascii() else raw.strip()
    return _CANONICAL_TYPE.get(key, raw)


def get_apply_mode(action_type: str) -> str:
    """根据 action_type 返回 apply_mode。先规范化再查表。未知类型返回 display_only。"""
    canonical = normalize_action_type(action_type)
    return _APPLY_MODE_BY_CANONICAL.get(canonical, _FALLBACK_APPLY_MODE)


# ===== CRUD =====

async def create_note(
    db: AsyncSession, user_id: int, title: str, content: str = "",
    graph_node_id: int | None = None, graph_id: int | None = None,
    artifact_ids: list[int] | None = None,
    tags: list[str] | None = None, category: str = "general",
) -> UserNote:
    note = UserNote(
        user_id=user_id, title=title, content=content,
        plain_text=content, graph_node_id=graph_node_id, graph_id=graph_id,
        artifact_ids=artifact_ids or [], tags=tags or [], category=category,
        word_count=len(content), last_saved_at=datetime.utcnow(),
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    logger.info("[notes] created note_id=%s user_id=%s", note.id, user_id)
    return note


async def list_notes(
    db: AsyncSession, user_id: int,
    category: str | None = None, tag: str | None = None, search: str | None = None,
    graph_node_id: int | None = None, graph_id: int | None = None,
    pinned_only: bool = False, favorite_only: bool = False,
    status: str = "active", limit: int = 50,
) -> list[dict]:
    query = select(UserNote).where(UserNote.user_id == user_id, UserNote.status == status)
    if category:
        query = query.where(UserNote.category == category)
    if graph_node_id is not None:
        query = query.where(UserNote.graph_node_id == graph_node_id)
    if graph_id is not None:
        query = query.where(UserNote.graph_id == graph_id)
    if pinned_only:
        query = query.where(UserNote.is_pinned == True)
    if favorite_only:
        query = query.where(UserNote.is_favorite == True)
    if search:
        pattern = f"%{search}%"
        query = query.where(or_(UserNote.title.ilike(pattern), UserNote.content.ilike(pattern)))
    if tag:
        query = query.where(UserNote.tags.contains([tag]))

    query = query.order_by(desc(UserNote.is_pinned), desc(UserNote.updated_at)).limit(limit)
    rows = (await db.execute(query)).scalars().all()
    return [_note_dict(r) for r in rows]


async def get_note(db: AsyncSession, note_id: int, user_id: int) -> dict | None:
    result = await db.execute(
        select(UserNote).where(UserNote.id == note_id, UserNote.user_id == user_id)
    )
    note = result.scalar_one_or_none()
    if not note: return None
    data = _note_dict(note)
    data["links"] = await _get_links(db, note_id)
    data["revisions"] = await _get_revisions(db, note_id)
    data["suggestions"] = await _get_suggestions(db, note_id)
    return data


async def update_note(
    db: AsyncSession, note_id: int, user_id: int, updates: dict
) -> UserNote | None:
    result = await db.execute(
        select(UserNote).where(UserNote.id == note_id, UserNote.user_id == user_id)
    )
    note = result.scalar_one_or_none()
    if not note: return None

    # Conflict detection: if client sent updated_at, check match
    client_ts = updates.pop("_client_updated_at", None)
    if client_ts and note.updated_at:
        db_ts = note.updated_at.isoformat() if hasattr(note.updated_at, 'isoformat') else str(note.updated_at)
        if client_ts != db_ts:
            raise ConflictError(f"Version conflict: client={client_ts[:19]}, db={db_ts[:19]}")

    allowed = {"title", "content", "plain_text", "category", "tags", "is_pinned", "is_favorite",
               "status", "graph_node_id", "graph_id", "artifact_ids", "content_format", "summary"}
    for key, value in updates.items():
        if key in allowed and value is not None:
            if key == "content":
                note.word_count = len(value)
                note.plain_text = value
            if key in ("tags", "artifact_ids"):
                flag_modified(note, key)
            setattr(note, key, value)

    note.last_saved_at = datetime.utcnow()
    await db.commit()
    await db.refresh(note)
    return note


class ConflictError(Exception): pass


async def delete_note(db: AsyncSession, note_id: int, user_id: int) -> bool:
    result = await db.execute(
        select(UserNote).where(UserNote.id == note_id, UserNote.user_id == user_id)
    )
    note = result.scalar_one_or_none()
    if not note: return False
    note.status = "deleted"
    await db.commit()
    return True


async def restore_note(db: AsyncSession, note_id: int, user_id: int) -> bool:
    result = await db.execute(
        select(UserNote).where(UserNote.id == note_id, UserNote.user_id == user_id)
    )
    note = result.scalar_one_or_none()
    if not note: return False
    note.status = "active"
    await db.commit()
    return True


async def batch_delete(db: AsyncSession, ids: list[int], user_id: int) -> int:
    count = 0
    for nid in ids:
        r = await db.execute(select(UserNote).where(UserNote.id == nid, UserNote.user_id == user_id))
        note = r.scalar_one_or_none()
        if note:
            note.status = "deleted"
            count += 1
    await db.commit()
    return count


async def get_note_categories(db: AsyncSession, user_id: int) -> list[dict]:
    rows = (await db.execute(
        select(UserNote.category, func.count(UserNote.id))
        .where(UserNote.user_id == user_id, UserNote.status == "active")
        .group_by(UserNote.category)
    )).all()
    return [{"category": r[0], "count": r[1]} for r in rows]


async def get_all_tags(db: AsyncSession, user_id: int) -> list[str]:
    rows = (await db.execute(
        select(UserNote.tags).where(UserNote.user_id == user_id, UserNote.status == "active")
    )).scalars().all()
    all_tags = set()
    for tags_json in rows:
        for t in (tags_json or []): all_tags.add(t)
    return sorted(all_tags)


# ===== NoteLink =====

async def add_link(db: AsyncSession, note_id: int, user_id: int, link_type: str, target_id: int, target_title: str = "") -> NoteLink:
    link = NoteLink(note_id=note_id, user_id=user_id, link_type=link_type, target_id=target_id, target_title=target_title)
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


async def remove_link(db: AsyncSession, link_id: int, user_id: int) -> bool:
    r = await db.execute(select(NoteLink).where(NoteLink.id == link_id, NoteLink.user_id == user_id))
    link = r.scalar_one_or_none()
    if not link: return False
    await db.delete(link)
    await db.commit()
    return True


async def _get_links(db: AsyncSession, note_id: int) -> list[dict]:
    rows = (await db.execute(select(NoteLink).where(NoteLink.note_id == note_id))).scalars().all()
    return [{"id": r.id, "link_type": r.link_type, "target_id": r.target_id, "target_title": r.target_title, "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


# ===== NoteRevision =====

async def create_revision(db: AsyncSession, note_id: int, user_id: int, change_source: str = "user", suggestion_id: int | None = None, force: bool = False) -> NoteRevision | None:
    """仅在实质变化时创建版本。force=True 时跳过去重，始终创建。"""
    r_note = await db.execute(select(UserNote).where(UserNote.id == note_id, UserNote.user_id == user_id))
    note = r_note.scalar_one_or_none()
    if not note: return None

    r_last = await db.execute(
        select(NoteRevision).where(NoteRevision.note_id == note_id).order_by(desc(NoteRevision.revision_number)).limit(1)
    )
    last = r_last.scalar_one_or_none()
    rev_num = (last.revision_number + 1) if last else 1

    # Skip if content unchanged (unless forced)
    if not force and last and last.content_snapshot == (note.content or ""):
        return None

    rev = NoteRevision(
        note_id=note_id, user_id=user_id, revision_number=rev_num,
        title_snapshot=note.title, content_snapshot=note.content or "",
        change_source=change_source, suggestion_id=suggestion_id,
    )
    db.add(rev)
    await db.commit()
    await db.refresh(rev)
    return rev


async def restore_revision(db: AsyncSession, revision_id: int, user_id: int) -> dict:
    r_rev = await db.execute(select(NoteRevision).where(NoteRevision.id == revision_id, NoteRevision.user_id == user_id))
    rev = r_rev.scalar_one_or_none()
    if not rev: raise ValueError("Revision not found")

    r_note = await db.execute(select(UserNote).where(UserNote.id == rev.note_id, UserNote.user_id == user_id))
    note = r_note.scalar_one_or_none()
    if not note: raise ValueError("Note not found")

    target_hash = _hash(rev.content_snapshot or "")
    current_hash = _hash(note.content or "")
    if target_hash == current_hash:
        raise ValueError("当前内容已与目标版本一致，无需恢复")

    # Save pre-restore snapshot
    pre_rev = await create_revision(db, note.id, user_id, change_source="pre_restore")

    # Restore
    note.title = rev.title_snapshot
    note.content = rev.content_snapshot
    note.plain_text = rev.content_snapshot
    note.word_count = len(rev.content_snapshot or "")
    note.last_saved_at = datetime.utcnow()
    await db.commit()

    # Save post-restore snapshot
    rest_rev = await create_revision(db, note.id, user_id, change_source="restore")
    await db.refresh(note)

    return {
        "note": _note_dict(note),
        "restored_from_revision_id": rev.id,
        "revision_number": rev.revision_number,
        "pre_restore_revision_id": pre_rev.id if pre_rev else None,
        "restore_revision_id": rest_rev.id if rest_rev else None,
        "content_hash": _hash(note.content or ""),
    }


async def _get_revisions(db: AsyncSession, note_id: int) -> list[dict]:
    rows = (await db.execute(
        select(NoteRevision).where(NoteRevision.note_id == note_id).order_by(desc(NoteRevision.revision_number)).limit(20)
    )).scalars().all()
    return [{"id": r.id, "revision_number": r.revision_number, "change_source": r.change_source,
             "title": r.title_snapshot[:60] if r.title_snapshot else "",
             "content_preview": (r.content_snapshot or "")[:120],
             "content_len": len(r.content_snapshot or ""),
             "word_count": len((r.content_snapshot or "").split()) if r.content_snapshot else 0,
             "content_hash": _hash(r.content_snapshot or ""),
             "suggestion_id": r.suggestion_id,
             "can_restore": True,
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


async def get_revision_content(db: AsyncSession, revision_id: int, user_id: int) -> dict | None:
    r = await db.execute(select(NoteRevision).where(NoteRevision.id == revision_id, NoteRevision.user_id == user_id))
    rev = r.scalar_one_or_none()
    if not rev: return None
    return {"id": rev.id, "revision_number": rev.revision_number,
            "title": rev.title_snapshot, "content": rev.content_snapshot,
            "change_source": rev.change_source, "content_hash": _hash(rev.content_snapshot or ""),
            "suggestion_id": rev.suggestion_id,
            "created_at": rev.created_at.isoformat() if rev.created_at else None}


# ===== AI Suggestions =====

async def create_ai_suggestion(
    db: AsyncSession, note_id: int, user_id: int, action_type: str,
) -> NoteAISuggestion:
    canonical = normalize_action_type(action_type)
    note = await db.scalar(select(UserNote).where(
        UserNote.id == note_id, UserNote.user_id == user_id,
    ))
    if note is None:
        raise ValueError("Note not found")
    latest_revision = await db.scalar(
        select(NoteRevision.revision_number)
        .where(NoteRevision.note_id == note_id, NoteRevision.user_id == user_id)
        .order_by(desc(NoteRevision.revision_number))
        .limit(1)
    )
    sug = NoteAISuggestion(
        note_id=note_id, user_id=user_id,
        action_type=canonical,
        apply_mode=get_apply_mode(canonical),
        source_revision=int(latest_revision or 0),
        source_content_hash=_hash(note.content or ""),
    )
    db.add(sug)
    await db.commit()
    await db.refresh(sug)
    return sug


async def run_ai_suggestion(db: AsyncSession, suggestion_id: int, user_id: int) -> NoteAISuggestion:
    """执行真实 AI 调用并保存建议"""
    r = await db.execute(
        select(NoteAISuggestion).where(NoteAISuggestion.id == suggestion_id, NoteAISuggestion.user_id == user_id)
    )
    sug = r.scalar_one_or_none()
    if not sug: raise ValueError("Suggestion not found")

    r_note = await db.execute(select(UserNote).where(UserNote.id == sug.note_id, UserNote.user_id == user_id))
    note = r_note.scalar_one_or_none()
    if not note: raise ValueError("Note not found")

    try:
        from services.llm_service_real import get_llm_service
        llm = await get_llm_service()
        sug.provider = "deepseek"
        sug.model = llm.config.model if hasattr(llm, 'config') else "unknown"
        sug.apply_mode = get_apply_mode(sug.action_type)
    except Exception as e:
        sug.status = "failed"
        sug.suggestion_content = f"LLM init error: {e}"
        await db.commit()
        return sug

    from services.skill_library import skill_prompt_prefix
    prompt = skill_prompt_prefix("note-refinement", max_chars_per_skill=2000) + _ai_prompt(
        action_type=sug.action_type, title=note.title, content=note.content or "",
    )
    try:
        result = await llm.chat(messages=[{"role": "user", "content": prompt}], temperature=0.5, max_tokens=2000)
        content = result.get("content", "") if isinstance(result, dict) else str(result)
        tokens = result.get("tokens_used", 0) if isinstance(result, dict) else 0

        sug.suggestion_content = content
        sug.tokens_used = tokens
        sug.status = "ready"

        # 结构化分析
        if sug.action_type in ("check_understanding", "偏差分析"):
            sug.structured_result = _parse_understanding_analysis(content)
    except Exception as e:
        sug.status = "failed"
        sug.suggestion_content = f"Generation error: {e}"

    await db.commit()
    await db.refresh(sug)
    return sug


async def apply_suggestion(
    db: AsyncSession,
    suggestion_id: int,
    user_id: int,
    accept_content: str | None = None,
    *,
    expected_note_id: int | None = None,
) -> dict:
    """应用 AI 建议。根据 apply_mode 执行不同的应用策略。"""
    r = await db.execute(
        select(NoteAISuggestion).where(NoteAISuggestion.id == suggestion_id, NoteAISuggestion.user_id == user_id)
    )
    sug = r.scalar_one_or_none()
    if not sug: raise ValueError("Suggestion not found")
    if expected_note_id is not None and sug.note_id != expected_note_id:
        raise ValueError("SUGGESTION_NOTE_MISMATCH")
    if sug.status == "applied": raise ValueError("该建议已经应用")
    if sug.status == "undone": raise ValueError("该建议已撤销，无法再次应用")
    if not sug.suggestion_content: raise ValueError("建议内容为空，无法应用")

    r_note = await db.execute(select(UserNote).where(UserNote.id == sug.note_id, UserNote.user_id == user_id))
    note = r_note.scalar_one_or_none()
    if not note: raise ValueError("Note not found")

    apply_mode = get_apply_mode(sug.action_type)
    sug.apply_mode = apply_mode

    old_hash = _hash(note.content or "")
    if sug.source_content_hash and sug.source_content_hash != old_hash:
        raise ValueError("SUGGESTION_SOURCE_CHANGED")
    sug.source_content_hash = old_hash

    # --- Validate apply_mode ---
    if apply_mode == "display_only":
        raise ValueError("SUGGESTION_NOT_APPLICABLE_TO_CONTENT")
    if apply_mode == "artifact_only":
        raise ValueError("ARTIFACT_GENERATION_REQUIRED")

    # --- Save pre-apply snapshot (force: always save, even if content same as last revision) ---
    pre_rev = await create_revision(db, note.id, user_id, change_source="pre_ai_apply", force=True)
    sug.pre_apply_revision_id = pre_rev.id if pre_rev else None

    if apply_mode == "replace_content":
        new_content = accept_content or sug.suggestion_content
        note.content = new_content
        note.plain_text = new_content
    elif apply_mode == "append_content":
        append_text = accept_content or sug.suggestion_content
        separator = "\n\n---\n\n"
        note.content = (note.content or "") + separator + append_text
        note.plain_text = note.content
    else:
        raise ValueError(f"Unknown apply_mode: {apply_mode}")

    note.word_count = len(note.content or "")
    note.last_saved_at = datetime.utcnow()

    # --- Save post-apply snapshot ---
    post_rev = await create_revision(db, note.id, user_id, change_source="ai_applied", suggestion_id=sug.id)
    new_hash = _hash(note.content or "")

    sug.status = "applied"
    sug.applied_at = datetime.utcnow()
    sug.applied_revision_id = post_rev.id if post_rev else None
    sug.applied_content_hash = new_hash
    await db.commit()
    await db.refresh(note)
    await db.refresh(sug)

    return {
        "note": _note_dict(note),
        "suggestion_id": sug.id,
        "status": "applied",
        "apply_mode": apply_mode,
        "pre_apply_revision_id": pre_rev.id if pre_rev else None,
        "applied_revision_id": post_rev.id if post_rev else None,
        "provider": getattr(sug, 'provider', None) or "deepseek",
        "model": getattr(sug, 'model', None) or "unknown",
        "source_content_hash": old_hash,
        "applied_content_hash": new_hash,
    }


def _hash(s: str) -> str:
    import hashlib
    return hashlib.md5(s.encode()).hexdigest()[:16]


async def dismiss_suggestion(db: AsyncSession, suggestion_id: int, user_id: int) -> bool:
    r = await db.execute(
        select(NoteAISuggestion).where(NoteAISuggestion.id == suggestion_id, NoteAISuggestion.user_id == user_id)
    )
    sug = r.scalar_one_or_none()
    if not sug: return False
    sug.status = "dismissed"
    await db.commit()
    return True


async def undo_ai_application(db: AsyncSession, note_id: int, user_id: int, suggestion_id: int | None = None) -> dict:
    """撤销最近一次 AI 应用。

    查找最近一条 status='applied' 且 undone_at IS NULL 的建议，
    验证当前笔记 hash 与 applied_content_hash 一致（无用户后续编辑），
    恢复到 pre_apply_revision 的内容。
    """
    r_note = await db.execute(select(UserNote).where(UserNote.id == note_id, UserNote.user_id == user_id))
    note = r_note.scalar_one_or_none()
    if not note:
        raise ValueError("Note not found")

    # 1. Find the most recent applied, not-yet-undone suggestion
    if suggestion_id:
        r_sug = await db.execute(
            select(NoteAISuggestion).where(
                NoteAISuggestion.id == suggestion_id,
                NoteAISuggestion.note_id == note_id,
                NoteAISuggestion.user_id == user_id,
            )
        )
    else:
        r_sug = await db.execute(
            select(NoteAISuggestion).where(
                NoteAISuggestion.note_id == note_id,
                NoteAISuggestion.user_id == user_id,
                NoteAISuggestion.status == "applied",
                NoteAISuggestion.undone_at.is_(None),
            ).order_by(desc(NoteAISuggestion.applied_at)).limit(1)
        )
    sug = r_sug.scalar_one_or_none()

    if not sug:
        raise ValueError("没有可撤销的 AI 修改")

    # 2. Check if already undone
    if sug.status == "undone":
        current_hash = _hash(note.content or "")
        pre_rev = None
        if sug.pre_apply_revision_id:
            r_pre = await db.execute(select(NoteRevision).where(NoteRevision.id == sug.pre_apply_revision_id))
            pre_rev = r_pre.scalar_one_or_none()

        if pre_rev and current_hash == _hash(pre_rev.content_snapshot or ""):
            raise ValueError("AI 修改已经撤销，当前内容即为原文")
        else:
            # Data inconsistency: suggestion says undone but content doesn't match
            logger.error(
                f"AI_UNDO_STATE_INCONSISTENT: suggestion={sug.id}, "
                f"suggestion_status={sug.status}, current_hash={current_hash}, "
                f"pre_apply_rev_id={sug.pre_apply_revision_id}"
            )
            raise ValueError("AI_UNDO_STATE_INCONSISTENT")

    # 3. Verify the note hasn't been edited since AI was applied
    current_hash = _hash(note.content or "")
    if sug.applied_content_hash and current_hash != sug.applied_content_hash:
        raise ValueError("NOTE_CHANGED_AFTER_AI_APPLY")

    # 4. Find pre_apply revision
    pre_rev = None
    if sug.pre_apply_revision_id:
        r_pre = await db.execute(select(NoteRevision).where(NoteRevision.id == sug.pre_apply_revision_id))
        pre_rev = r_pre.scalar_one_or_none()

    if not pre_rev:
        raise ValueError("无法找到 AI 应用前的版本，撤销失败")

    # 5. Save current state as pre-undo snapshot
    pre_undo_rev = await create_revision(db, note.id, user_id, change_source="pre_ai_undo")

    # 6. Restore pre_apply content
    note.content = pre_rev.content_snapshot
    note.plain_text = pre_rev.content_snapshot
    note.title = pre_rev.title_snapshot
    note.word_count = len(pre_rev.content_snapshot or "")
    note.last_saved_at = datetime.utcnow()
    await db.commit()

    # 7. Save undo revision
    undo_rev = await create_revision(db, note.id, user_id, change_source="ai_undo")
    restored_hash = _hash(note.content or "")

    # 8. Mark suggestion as undone
    sug.status = "undone"
    sug.undone_at = datetime.utcnow()
    sug.undo_revision_id = undo_rev.id if undo_rev else None
    await db.commit()
    await db.refresh(note)
    await db.refresh(sug)

    return {
        "status": "undone",
        "suggestion_id": sug.id,
        "pre_ai_undo_revision_id": pre_undo_rev.id if pre_undo_rev else None,
        "ai_undo_revision_id": undo_rev.id if undo_rev else None,
        "restored_from_revision_id": pre_rev.id,
        "note": _note_dict(note),
        "content_hash": restored_hash,
    }


async def _get_suggestions(db: AsyncSession, note_id: int) -> list[dict]:
    rows = (await db.execute(
        select(NoteAISuggestion).where(NoteAISuggestion.note_id == note_id).order_by(desc(NoteAISuggestion.created_at)).limit(20)
    )).scalars().all()
    return [_sug_dict(r) for r in rows]


def _note_dict(n: UserNote) -> dict:
    return {
        "id": n.id, "user_id": n.user_id, "title": n.title, "content": n.content,
        "plain_text": n.plain_text, "graph_node_id": n.graph_node_id, "graph_id": n.graph_id,
        "material_ids": n.material_ids or [], "artifact_ids": n.artifact_ids or [],
        "tags": n.tags or [], "category": n.category,
        "is_pinned": n.is_pinned, "is_favorite": n.is_favorite,
        "word_count": n.word_count, "content_format": getattr(n, 'content_format', 'plain'),
        "summary": getattr(n, 'summary', None),
        "status": getattr(n, 'status', 'active'),
        "ai_suggestions": n.ai_suggestions or [],
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "updated_at": n.updated_at.isoformat() if n.updated_at else None,
        "last_saved_at": n.last_saved_at.isoformat() if n.last_saved_at else None,
    }


def _sug_dict(s: NoteAISuggestion) -> dict:
    return {
        "id": s.id, "note_id": s.note_id, "action_type": s.action_type,
        "apply_mode": getattr(s, 'apply_mode', None) or get_apply_mode(s.action_type),
        "status": s.status, "suggestion_content": s.suggestion_content,
        "structured_result": s.structured_result,
        "provider": getattr(s, 'provider', None) or "deepseek",
        "model": getattr(s, 'model', None) or "unknown",
        "tokens_used": s.tokens_used,
        "source_revision": s.source_revision,
        "source_content_hash": getattr(s, 'source_content_hash', None),
        "pre_apply_revision_id": getattr(s, 'pre_apply_revision_id', None),
        "applied_revision_id": getattr(s, 'applied_revision_id', None),
        "applied_content_hash": getattr(s, 'applied_content_hash', None),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "applied_at": s.applied_at.isoformat() if s.applied_at else None,
        "undone_at": s.undone_at.isoformat() if getattr(s, 'undone_at', None) else None,
        "undo_revision_id": getattr(s, 'undo_revision_id', None),
    }


def _ai_prompt(action_type: str, title: str, content: str) -> str:
    base = f"笔记标题：{title}\n笔记内容：\n{content[:5000]}\n\n"
    prompts = {
        "expand": base + "请扩写这篇笔记，补充细节和深度，保留原意和语气。",
        "polish": base + "请润色这篇笔记，优化表达和结构，保持原意。输出润色后的完整全文。",
        "structure": base + "请对这篇笔记进行结构化整理，用清晰的层次重新组织。输出结构化的完整全文。",
        "summarize": base + "请为这篇笔记生成一个简洁的摘要（100-200字）。",
        "extract_knowledge": base + "请提取笔记中的核心知识点和关键概念，以列表形式输出。",
        "extract_questions": base + "请提取笔记中提到的尚未解决的问题或疑惑。",
        "check_understanding": base + "请评价这篇笔记中的理解是否准确：（1）正确的部分；（2）不够准确的部分；（3）可能遗漏的知识点；（4）需要核实的表述；（5）置信度。",
        "next_step_advice": base + "基于这篇笔记的内容，推荐下一步学习方向和具体资源类型。",
        "generate_exercises": base + "根据笔记核心概念生成3道针对性练习题（含答案和解析）。",
        "outline": base + "将笔记内容转化为复习提纲，突出考点和重点。",
        "mindmap_plan": base + "为笔记内容生成一个思维导图大纲，用缩进结构表示层级关系。",
    }
    prompt = prompts.get(action_type)
    if prompt is not None:
        return prompt
    logger.warning(f"[notes] Unknown action_type={action_type!r}, falling back to polish prompt")
    return prompts["polish"]


def _parse_understanding_analysis(content: str) -> dict:
    return {
        "correct": [],
        "inaccurate": [],
        "missing": [],
        "needs_verification": [],
        "confidence": "medium",
        "source": "ai_analysis",
        "raw": content[:3000],
    }
