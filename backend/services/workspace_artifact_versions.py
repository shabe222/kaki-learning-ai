"""Durable immutable versions and one public projection for GeneratedItem artifacts.

GeneratedItem remains the stable artifact identity and compatibility projection. Every
immutable child version is stored in GeneratedItemVersion. This module is the only
place that maps raw_data into the browser-facing ArtifactPublicProjection.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GeneratedItem, GeneratedItemVersion


class ArtifactVersionError(ValueError):
    code = "ARTIFACT_VERSION_ERROR"
    status_code = 409


class ArtifactVersionNotFound(ArtifactVersionError):
    code = "ARTIFACT_NOT_FOUND"
    status_code = 404


class ArtifactVersionConflict(ArtifactVersionError):
    code = "ARTIFACT_VERSION_CONFLICT"
    status_code = 409


class ArtifactOperationConflict(ArtifactVersionError):
    code = "ARTIFACT_OPERATION_CONFLICT"
    status_code = 409


_FORBIDDEN_KEYS = {
    "prompt", "system_prompt", "provider_prompt", "provider_response", "raw_response",
    "api_key", "access_token", "token", "secret", "password", "private_key",
    "authorization", "authorization_header", "bearer_token", "credential", "credentials",
    "private_result", "private_envelope", "authenticity_envelope", "rationale",
    "correction_actor", "stack", "stacktrace", "traceback", "internal_error",
    "reservation_token", "idempotency_key", "request_id",
}
_COMMON_PUBLIC_KEYS = {
    "kind", "thread_id", "status", "quality_status", "generation_status",
    "graph_id", "graph_revision", "graph_node_id", "graph_node_external_id",
    "graph_node_title", "resource_plan_id", "resource_plan_external_id",
    "resource_plan_continuity_key", "resource_plan_title", "export_formats",
    "source_citations", "outline", "summary", "language", "source", "last_run",
    "run", "tree_data", "mindmap", "tree", "slides", "questions", "delivery_mode",
    "duration_minutes", "slide_count", "progress", "interaction_results",
    "learning_goal", "level", "presentation_style", "classroom_id",
    "video_url", "transcript", "provider", "provider_source", "generation_ms",
    "audio_url", "segments", "source_artifact_id",
    "duration_sec", "source_material_ids", "key_points", "model", "dry_run",
    "error_code", "safe_error", "status_updated_at",
    "canonical_blocks", "canonical_purpose", "canonical_personalization",
    "canonical_source_refs", "accessibility", "interaction_policy", "evidence_policy",
}
_KIND_BY_TYPE = {
    "workspace_lecture": "lecture",
    "workspace_question": "question",
    "workspace_code": "code",
    "workspace_mindmap": "mindmap",
    "workspace_presentation": "presentation",
    "workspace_interactive_lesson": "interactive_lesson",
    "workspace_video": "video",
    "study_picture_book": "picture_book",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _safe_value(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        clean = value[:120000]
        # Public values are value-filtered as well as key-filtered. Host paths,
        # file URIs, signed query credentials, and secret-looking assignments
        # never belong in a browser projection.
        clean = re.sub(r"(?i)[A-Z]:\\(?:Users|Windows|Program Files)\\[^\s\"']+", "[private-path]", clean)
        clean = re.sub(r"(?i)file://[^\s\"']+", "[private-path]", clean)
        clean = re.sub(
            r"(?i)([?&](?:token|access_token|api_key|signature|x-amz-signature)=)[^&#\s]+",
            r"\1[redacted]", clean,
        )
        clean = re.sub(
            r"(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*)[^,}\s]+",
            r"\1[redacted]", clean,
        )
        clean = re.sub(r"(?i)\bbearer\s+[a-z0-9._~+\-/=]+", "Bearer [redacted]", clean)
        return clean
    if isinstance(value, list):
        return [_safe_value(item, depth + 1) for item in value[:500]]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = str(key)
            if clean_key.lower() in _FORBIDDEN_KEYS:
                continue
            result[clean_key] = _safe_value(item, depth + 1)
        return result
    return str(value)[:4000]


def artifact_kind(item: GeneratedItem) -> str:
    return _KIND_BY_TYPE.get(item.type, str(item.type or "artifact").replace("workspace_", ""))


def _public_slides(value: Any) -> list[dict[str, Any]]:
    """Project interactive slides without leaking server answer facts."""
    slides: list[dict[str, Any]] = []
    for raw_slide in list(value or [])[:200]:
        if not isinstance(raw_slide, Mapping):
            continue
        slide = {
            str(key): _safe_value(item)
            for key, item in raw_slide.items()
            if str(key) != "interaction"
        }
        interaction = raw_slide.get("interaction")
        if isinstance(interaction, Mapping):
            slide["interaction"] = {
                str(key): _safe_value(item)
                for key, item in interaction.items()
                if str(key).lower() not in {
                    "correct_index", "correct_answer", "answer", "solution", "explanation",
                }
            }
        slides.append(slide)
    return slides


def public_data_from_item(item: GeneratedItem, source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(source if source is not None else (item.raw_data or {}))
    selected = {key: raw[key] for key in _COMMON_PUBLIC_KEYS if key in raw}
    selected["kind"] = str(selected.get("kind") or artifact_kind(item))
    selected["status"] = str(
        selected.get("status") or selected.get("quality_status") or selected.get("generation_status") or "generated"
    )
    selected["export_formats"] = list(selected.get("export_formats") or [])[:20]
    if "slides" in selected:
        selected["slides"] = _public_slides(selected.get("slides"))
    return _safe_value(selected)


def _version_content_digest(title: str, chapter: str | None, content: str | None, public_data: Mapping[str, Any]) -> str:
    return _sha(_canonical({
        "title": title,
        "chapter": chapter or "",
        "content": content or "",
        "public_data": public_data,
    }))


def _operation_identity(user_id: int, artifact_id: int, idempotency_key: str) -> str:
    return _sha(f"artifact-version-operation-v1\0{user_id}\0{artifact_id}\0{idempotency_key}")


def _request_guard(
    *, expected_version: int, title: str, chapter: str | None, content: str | None,
    public_data: Mapping[str, Any],
) -> str:
    return _sha(_canonical({
        "expected_version": expected_version,
        "title": title,
        "chapter": chapter or "",
        "content": content or "",
        "public_data": public_data,
    }))


async def get_owned_artifact(db: AsyncSession, *, user_id: int, artifact_id: int) -> GeneratedItem:
    item = await db.scalar(select(GeneratedItem).where(
        GeneratedItem.id == artifact_id,
        GeneratedItem.user_id == user_id,
    ))
    if item is None:
        raise ArtifactVersionNotFound("学习产物不存在或无权访问")
    return item


async def ensure_initial_version(db: AsyncSession, item: GeneratedItem) -> GeneratedItemVersion:
    """Create v1 from the unchanged compatibility projection exactly once."""
    if item.current_version_id:
        pointed = await db.scalar(select(GeneratedItemVersion).where(
            GeneratedItemVersion.id == item.current_version_id,
            GeneratedItemVersion.artifact_id == item.id,
        ))
        if pointed is not None:
            return pointed

    existing = await db.scalar(select(GeneratedItemVersion).where(
        GeneratedItemVersion.artifact_id == item.id,
        GeneratedItemVersion.version_number == 1,
    ))
    if existing is None:
        public_data = public_data_from_item(item)
        operation = _sha(f"artifact-version-migration-v1\0{item.id}")
        guard = _request_guard(
            expected_version=0, title=item.title, chapter=item.chapter,
            content=item.content, public_data=public_data,
        )
        existing = GeneratedItemVersion(
            artifact_id=item.id,
            version_number=1,
            parent_version_id=None,
            title_snapshot=item.title,
            chapter_snapshot=item.chapter,
            content_snapshot=item.content or "",
            public_data_snapshot=public_data,
            private_data_snapshot=_safe_value(dict(item.raw_data or {})),
            content_digest=_version_content_digest(item.title, item.chapter, item.content, public_data),
            status=str(public_data.get("status") or "generated"),
            created_by_operation=operation,
            operation_guard=guard,
        )
        db.add(existing)
        try:
            await db.flush()
        except Exception:
            # A concurrent first-read may have inserted v1. Let the caller's outer
            # transaction recover safely rather than producing a duplicate.
            await db.rollback()
            item = await get_owned_artifact(db, user_id=item.user_id, artifact_id=item.id)
            existing = await db.scalar(select(GeneratedItemVersion).where(
                GeneratedItemVersion.artifact_id == item.id,
                GeneratedItemVersion.version_number == 1,
            ))
            if existing is None:
                raise

    item.current_version_id = existing.id
    item.current_version_number = max(int(item.current_version_number or 0), 1)
    metadata = dict(item.raw_data or {})
    metadata.update({
        "version": item.current_version_number,
        "version_id": existing.id,
        "current_version_id": existing.id,
    })
    item.raw_data = metadata
    item.updated_at = item.updated_at or item.created_at or _utcnow()
    await db.flush()
    return existing


async def _all_versions(db: AsyncSession, artifact_id: int) -> list[GeneratedItemVersion]:
    return list((await db.execute(select(GeneratedItemVersion).where(
        GeneratedItemVersion.artifact_id == artifact_id,
    ).order_by(GeneratedItemVersion.version_number.asc()))).scalars().all())


async def get_version(
    db: AsyncSession, *, item: GeneratedItem, version_number: int | None = None,
    version_id: int | None = None,
) -> GeneratedItemVersion:
    current = await ensure_initial_version(db, item)
    if version_id is not None:
        row = await db.scalar(select(GeneratedItemVersion).where(
            GeneratedItemVersion.id == version_id,
            GeneratedItemVersion.artifact_id == item.id,
        ))
    elif version_number is not None:
        row = await db.scalar(select(GeneratedItemVersion).where(
            GeneratedItemVersion.artifact_id == item.id,
            GeneratedItemVersion.version_number == version_number,
        ))
    else:
        row = current
    if row is None:
        raise ArtifactVersionNotFound("学习产物版本不存在或无权访问")
    return row


def _iso(value: Any) -> str:
    return value.isoformat() if value else ""


async def build_public_projection(
    db: AsyncSession, *, user_id: int, artifact_id: int,
    version_number: int | None = None, version_id: int | None = None,
) -> dict[str, Any]:
    item = await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    version = await get_version(db, item=item, version_number=version_number, version_id=version_id)
    # Server-default timestamps may be expired after an INSERT/flush under async
    # SQLAlchemy. Refresh explicitly so public projection never triggers implicit IO.
    await db.refresh(item)
    await db.refresh(version)
    versions = await _all_versions(db, item.id)
    index = next((i for i, row in enumerate(versions) if row.id == version.id), 0)
    previous = versions[index - 1] if index > 0 else None
    following = versions[index + 1] if index + 1 < len(versions) else None
    data = _safe_value(dict(version.public_data_snapshot or {}))
    # Versions snapshotted before a public key was whitelisted (e.g. audio_url)
    # keep that value in the private snapshot; backfill the read projection with
    # exactly the already-public whitelist so old versions render fully.
    if version.private_data_snapshot:
        private = version.private_data_snapshot if isinstance(version.private_data_snapshot, Mapping) else {}
        for key in _COMMON_PUBLIC_KEYS:
            if key not in data and key in private:
                data[key] = _safe_value(private[key])
    # Compatibility for videos completed before the lifecycle published a new
    # immutable ready version: the media locator is an already-public runtime
    # field on the same owned artifact. Backfill only the current video view;
    # historical version content and its digest remain untouched.
    if item.type == "workspace_video" and version.id == item.current_version_id:
        current_video = public_data_from_item(item)
        if str(current_video.get("status") or "") == "ready":
            for key in (
                "video_url", "transcript", "duration_sec", "key_points",
                "provider", "model", "dry_run", "status_updated_at",
            ):
                if key in current_video:
                    data[key] = current_video[key]
    render_content = str(_safe_value(version.content_snapshot or "") or "")
    try:
        from services.canonical_artifacts import build_canonical_artifact
        canonical = await build_canonical_artifact(db, item=item, version=version)
        canonical_error = None
    except Exception:
        # Legacy content must remain readable even when a malformed historical
        # payload cannot satisfy the canonical validator.
        canonical = None
        canonical_error = "CANONICAL_PROJECTION_UNAVAILABLE"
    graph_node_id = data.get("graph_node_id") or data.get("graph_node_external_id")
    resource_plan_id = data.get("resource_plan_id") or data.get("resource_plan_external_id")
    return {
        "artifact_id": item.id,
        "version_id": version.id,
        "version_number": version.version_number,
        "version": version.version_number,
        "kind": str(data.get("kind") or artifact_kind(item)),
        "type": item.type,
        "title": version.title_snapshot,
        "topic": version.chapter_snapshot or "",
        "graph_id": data.get("graph_id"),
        "graph_revision": data.get("graph_revision"),
        "graph_node_id": graph_node_id,
        "graph_node_title": data.get("graph_node_title"),
        "resource_plan_id": resource_plan_id,
        "resource_plan_continuity_key": data.get("resource_plan_continuity_key"),
        "thread_id": data.get("thread_id"),
        "status": version.status,
        "content_digest": f"sha256:{version.content_digest}",
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at or version.created_at),
        "version_created_at": _iso(version.created_at),
        "available_versions": [
            {
                "version_id": row.id,
                "version_number": row.version_number,
                "title": row.title_snapshot,
                "status": row.status,
                "created_at": _iso(row.created_at),
                "is_current": row.id == item.current_version_id,
                "is_viewing": row.id == version.id,
            }
            for row in versions
        ],
        "export_formats": list(data.get("export_formats") or []),
        "previous_version_ref": (
            {"version_id": previous.id, "version_number": previous.version_number} if previous else None
        ),
        "next_version_ref": (
            {"version_id": following.id, "version_number": following.version_number} if following else None
        ),
        "current_version_ref": {
            "version_id": item.current_version_id,
            "version_number": int(item.current_version_number or version.version_number),
        },
        "render_payload": {
            "content": render_content,
            "data": _safe_value(data),
        },
        "canonical": canonical,
        "canonical_error": canonical_error,
        # Compatibility fields used by existing pages. They are projections, not raw_data.
        "content": render_content,
        "raw_data": data,
    }


async def create_new_version(
    db: AsyncSession, *, user_id: int, artifact_id: int, expected_version: int,
    idempotency_key: str, title: str | None = None, chapter: str | None = None,
    content: str | None = None, public_data: Mapping[str, Any] | None = None,
    private_data: Mapping[str, Any] | None = None,
    status: str = "generated",
) -> tuple[GeneratedItemVersion, bool]:
    """Atomically create one immutable version and update the compatibility projection.

    Returns (version, replayed). The raw idempotency key is never persisted.
    """
    if not idempotency_key or len(idempotency_key) > 240:
        raise ArtifactOperationConflict("缺少有效的幂等身份")
    item = await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    current = await ensure_initial_version(db, item)
    expected_version = int(expected_version)
    next_title = (title if title is not None else current.title_snapshot or item.title).strip()[:200]
    if not next_title:
        raise ArtifactVersionConflict("新版本标题不能为空")
    next_chapter = chapter if chapter is not None else current.chapter_snapshot
    next_content = content if content is not None else current.content_snapshot
    merged_public = dict(current.public_data_snapshot or public_data_from_item(item))
    if public_data:
        merged_public.update(public_data_from_item(item, public_data))
    merged_public = public_data_from_item(item, merged_public)
    merged_public["status"] = str(status or merged_public.get("status") or "generated")[:30]

    operation = _operation_identity(user_id, artifact_id, idempotency_key)
    guard = _request_guard(
        expected_version=expected_version, title=next_title, chapter=next_chapter,
        content=next_content, public_data=merged_public,
    )
    existing = await db.scalar(select(GeneratedItemVersion).where(
        GeneratedItemVersion.artifact_id == artifact_id,
        GeneratedItemVersion.created_by_operation == operation,
    ))
    if existing is not None:
        if existing.operation_guard != guard:
            raise ArtifactOperationConflict("相同幂等身份对应了不同修改内容")
        return existing, True

    current_number = int(item.current_version_number or current.version_number or 1)
    if expected_version != current_number:
        raise ArtifactVersionConflict(f"版本已更新；当前为 v{current_number}，请基于最新版本重试")
    next_number = current_number + 1

    claim = await db.execute(update(GeneratedItem).where(
        GeneratedItem.id == artifact_id,
        GeneratedItem.user_id == user_id,
        GeneratedItem.current_version_number == current_number,
    ).values(current_version_number=next_number, updated_at=_utcnow()).execution_options(synchronize_session=False))
    if claim.rowcount != 1:
        concurrent = await db.scalar(select(GeneratedItemVersion).where(
            GeneratedItemVersion.artifact_id == artifact_id,
            GeneratedItemVersion.created_by_operation == operation,
        ))
        if concurrent is not None and concurrent.operation_guard == guard:
            return concurrent, True
        raise ArtifactVersionConflict("版本已被另一项修改推进，请刷新后重试")

    row = GeneratedItemVersion(
        artifact_id=artifact_id,
        version_number=next_number,
        parent_version_id=current.id,
        title_snapshot=next_title,
        chapter_snapshot=next_chapter,
        content_snapshot=next_content or "",
        public_data_snapshot=merged_public,
        private_data_snapshot=_safe_value(
            dict(private_data) if private_data is not None else dict(current.private_data_snapshot or {})
        ),
        content_digest=_version_content_digest(next_title, next_chapter, next_content, merged_public),
        status=str(status or "generated")[:30],
        created_by_operation=operation,
        operation_guard=guard,
    )
    db.add(row)
    await db.flush()

    metadata = dict(item.raw_data or {})
    metadata.update(merged_public)
    metadata.update({
        "version": next_number,
        "version_id": row.id,
        "current_version_id": row.id,
    })
    item.current_version_number = next_number
    item.current_version_id = row.id
    item.title = next_title
    item.chapter = next_chapter
    item.content = next_content or ""
    item.raw_data = metadata
    item.updated_at = _utcnow()
    await db.flush()
    return row, False


async def compare_versions(
    db: AsyncSession, *, user_id: int, artifact_id: int,
    from_version: int, to_version: int,
) -> dict[str, Any]:
    item = await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    left = await get_version(db, item=item, version_number=from_version)
    right = await get_version(db, item=item, version_number=to_version)
    left_lines = (left.content_snapshot or "").splitlines()
    right_lines = (right.content_snapshot or "").splitlines()
    matcher = difflib.SequenceMatcher(a=left_lines, b=right_lines, autojunk=False)
    changes: list[dict[str, Any]] = []
    counts = {"equal": 0, "added": 0, "deleted": 0, "modified": 0}
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        public_tag = {"insert": "added", "delete": "deleted", "replace": "modified", "equal": "equal"}[tag]
        counts[public_tag] += max(a2 - a1, b2 - b1)
        changes.append({
            "kind": public_tag,
            "from_start": a1 + 1,
            "from_end": a2,
            "to_start": b1 + 1,
            "to_end": b2,
            "from_lines": left_lines[a1:a2],
            "to_lines": right_lines[b1:b2],
        })
    return {
        "artifact_id": artifact_id,
        "from_version": {"version_id": left.id, "version_number": left.version_number, "title": left.title_snapshot},
        "to_version": {"version_id": right.id, "version_number": right.version_number, "title": right.title_snapshot},
        "summary": counts,
        "changes": changes,
    }
