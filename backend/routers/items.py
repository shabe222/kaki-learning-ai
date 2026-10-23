from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import GeneratedItem
from services.workspace_artifact_versions import (
    ArtifactVersionError, build_public_projection, compare_versions,
    create_new_version, ensure_initial_version, get_owned_artifact,
    public_data_from_item,
)
from schemas.canonical_artifacts import (
    CreateArtifactReviewRequest,
    CreateCanonicalHandoutRequest,
    CreatePracticeTransformRequest,
    CreateReviewedRevisionRequest,
)
from services.canonical_artifacts import (
    CanonicalArtifactError,
    build_canonical_artifact,
    create_canonical_handout,
    create_practice_transform,
    create_review,
    create_reviewed_revision,
    list_lineage,
    list_reviews,
)
from utils.auth import get_current_user_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/items", tags=["generated-items"])


class SaveItemRequest(BaseModel):
    type: str
    title: str
    chapter: Optional[str] = ""
    content: Optional[str] = ""
    raw_data: Optional[dict] = None
    plan_id: Optional[int] = None
    task_id: Optional[int] = None


class UpdateItemRequest(BaseModel):
    title: Optional[str] = None
    chapter: Optional[str] = None
    content: Optional[str] = None
    raw_data: Optional[dict] = None


class CreateItemVersionRequest(BaseModel):
    expected_version: int
    idempotency_key: str
    title: Optional[str] = None
    chapter: Optional[str] = None
    content: Optional[str] = None
    public_data: Optional[dict[str, Any]] = None
    status: str = "generated"


@router.post("/save", response_model=dict)
async def save_item(
    req: SaveItemRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = GeneratedItem(
        user_id=current_user_id,
        plan_id=req.plan_id,
        task_id=req.task_id,
        type=req.type,
        title=req.title,
        chapter=req.chapter,
        content=req.content,
        raw_data=req.raw_data,
    )
    db.add(item)
    await db.flush()
    version = await ensure_initial_version(db, item)
    await db.commit()
    await db.refresh(item)
    return {"success": True, "id": item.id, "version_id": version.id, "version_number": 1}


@router.get("/list")
async def list_items(
    q: str = None,
    type: str = None,
    prefix: str = None,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(GeneratedItem).where(GeneratedItem.user_id == current_user_id)
    if q and q.strip():
        stmt = stmt.where(GeneratedItem.title.like(f"%{q.strip()}%"))
    if type and type.strip():
        stmt = stmt.where(GeneratedItem.type == type.strip())
    if prefix and prefix.strip():
        stmt = stmt.where(GeneratedItem.type.like(f"{prefix.strip()}%"))

    result = await db.execute(stmt.order_by(desc(GeneratedItem.created_at)).limit(100))
    items = result.scalars().all()
    return {
        "success": True,
        "data": [
            {
                "id": item.id,
                "type": item.type,
                "title": item.title,
                "chapter": item.chapter or "",
                "content": str(public_data_from_item(item, {"summary": item.content or ""}).get("summary") or ""),
                "raw_data": public_data_from_item(item),
                "version_id": item.current_version_id or (item.raw_data or {}).get("version_id"),
                "version_number": int(item.current_version_number or (item.raw_data or {}).get("version") or 1),
                "created_at": item.created_at.isoformat() if item.created_at else "",
            }
            for item in items
        ],
    }


def _canonical_http_error(exc: Exception) -> HTTPException:
    status = int(getattr(exc, "status_code", 409))
    code = str(getattr(exc, "code", "CANONICAL_ARTIFACT_ERROR"))
    return HTTPException(status_code=status, detail={"code": code, "message": str(exc)})


@router.post("/canonical/handouts")
async def save_canonical_handout(
    req: CreateCanonicalHandoutRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Create one PC02 handout identity and eager immutable V1 atomically."""
    try:
        item, version, replayed = await create_canonical_handout(
            db, user_id=current_user_id, request=req,
        )
        canonical = await build_canonical_artifact(db, item=item, version=version)
        await db.commit()
        return {"success": True, "data": canonical, "idempotent_replay": replayed}
    except (CanonicalArtifactError, ArtifactVersionError) as exc:
        await db.rollback()
        raise _canonical_http_error(exc) from exc


@router.post("/{item_id}/reviews")
async def review_item_version(
    item_id: int,
    req: CreateArtifactReviewRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        row, replayed = await create_review(
            db, user_id=current_user_id, artifact_id=item_id, request=req,
        )
        await db.commit()
        reviews = await list_reviews(db, user_id=current_user_id, artifact_id=item_id)
        return {
            "success": True,
            "data": next(review for review in reviews if review["review_id"] == row.review_id),
            "idempotent_replay": replayed,
        }
    except (CanonicalArtifactError, ArtifactVersionError) as exc:
        await db.rollback()
        raise _canonical_http_error(exc) from exc


@router.get("/{item_id}/reviews")
async def get_item_reviews(
    item_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {"success": True, "data": await list_reviews(db, user_id=current_user_id, artifact_id=item_id)}
    except (CanonicalArtifactError, ArtifactVersionError) as exc:
        raise _canonical_http_error(exc) from exc


@router.post("/{item_id}/reviewed-revisions")
async def revise_reviewed_item(
    item_id: int,
    req: CreateReviewedRevisionRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        row, replayed = await create_reviewed_revision(
            db, user_id=current_user_id, artifact_id=item_id, request=req,
        )
        item = await get_owned_artifact(db, user_id=current_user_id, artifact_id=item_id)
        canonical = await build_canonical_artifact(db, item=item, version=row)
        await db.commit()
        return {"success": True, "data": canonical, "idempotent_replay": replayed}
    except (CanonicalArtifactError, ArtifactVersionError) as exc:
        await db.rollback()
        raise _canonical_http_error(exc) from exc


@router.post("/{item_id}/transforms/practice")
async def transform_item_to_practice(
    item_id: int,
    req: CreatePracticeTransformRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        target, version, edge, replayed = await create_practice_transform(
            db, user_id=current_user_id, source_artifact_id=item_id, request=req,
        )
        canonical = await build_canonical_artifact(db, item=target, version=version)
        await db.commit()
        return {
            "success": True, "data": canonical,
            "lineage_edge_id": edge.lineage_edge_id, "idempotent_replay": replayed,
        }
    except (CanonicalArtifactError, ArtifactVersionError) as exc:
        await db.rollback()
        raise _canonical_http_error(exc) from exc


@router.get("/{item_id}/lineage")
async def get_item_lineage(
    item_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {"success": True, "data": await list_lineage(db, user_id=current_user_id, artifact_id=item_id)}
    except (CanonicalArtifactError, ArtifactVersionError) as exc:
        raise _canonical_http_error(exc) from exc


@router.get("/{item_id}")
async def get_item(
    item_id: int,
    version: int | None = None,
    version_id: int | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """One authenticated public projection for every Artifact Shell entry."""
    try:
        data = await build_public_projection(
            db, user_id=current_user_id, artifact_id=item_id,
            version_number=version, version_id=version_id,
        )
        await db.commit()  # persists an idempotent v1 migration on first read
        return {"success": True, "data": data}
    except ArtifactVersionError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/{item_id}/versions")
async def list_item_versions(
    item_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await build_public_projection(db, user_id=current_user_id, artifact_id=item_id)
        await db.commit()
        return {"success": True, "data": data["available_versions"]}
    except ArtifactVersionError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/{item_id}/versions/{version_number}")
async def get_item_version(
    item_id: int,
    version_number: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await build_public_projection(
            db, user_id=current_user_id, artifact_id=item_id, version_number=version_number,
        )
        await db.commit()
        return {"success": True, "data": data}
    except ArtifactVersionError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/{item_id}/versions")
async def create_item_version(
    item_id: int,
    req: CreateItemVersionRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        row, replayed = await create_new_version(
            db, user_id=current_user_id, artifact_id=item_id,
            expected_version=req.expected_version, idempotency_key=req.idempotency_key,
            title=req.title, chapter=req.chapter, content=req.content,
            public_data=req.public_data, status=req.status,
        )
        await db.commit()
        data = await build_public_projection(
            db, user_id=current_user_id, artifact_id=item_id, version_id=row.id,
        )
        return {"success": True, "data": data, "idempotent_replay": replayed}
    except ArtifactVersionError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc
    except Exception as exc:
        await db.rollback()
        logger.exception("artifact version save failed artifact=%s user=%s", item_id, current_user_id)
        raise HTTPException(status_code=503, detail={"code": "ARTIFACT_VERSION_SAVE_FAILED", "message": "新版本保存失败，原版本保持不变"}) from exc


@router.get("/{item_id}/compare")
async def compare_item_versions(
    item_id: int,
    from_version: int,
    to_version: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await compare_versions(
            db, user_id=current_user_id, artifact_id=item_id,
            from_version=from_version, to_version=to_version,
        )
        await db.commit()
        return {"success": True, "data": data}
    except ArtifactVersionError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.delete("/{item_id}")
async def delete_item(
    item_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(GeneratedItem).where(
            GeneratedItem.id == item_id,
            GeneratedItem.user_id == current_user_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")

    await db.delete(item)
    await db.commit()
    return {"success": True}


@router.patch("/{item_id}", response_model=dict)
async def update_item(
    item_id: int,
    req: UpdateItemRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if not any(
        [
            req.title is not None,
            req.chapter is not None,
            req.content is not None,
            req.raw_data is not None,
        ]
    ):
        raise HTTPException(status_code=400, detail="no fields to update")
    if req.title is not None and (not req.title.strip() or len(req.title) > 200):
        raise HTTPException(status_code=400, detail="invalid title")

    result = await db.execute(
        select(GeneratedItem).where(
            GeneratedItem.id == item_id,
            GeneratedItem.user_id == current_user_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")

    # Legacy PATCH remains compatible but now creates an immutable child version.
    # New clients should use POST /{item_id}/versions with an explicit expected_version.
    current = await ensure_initial_version(db, item)
    body_identity = {
        "title": req.title, "chapter": req.chapter, "content": req.content, "raw_data": req.raw_data,
        "base_version": current.version_number,
    }
    import hashlib, json
    compatibility_key = "legacy-patch:" + hashlib.sha256(
        json.dumps(body_identity, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    try:
        row, replayed = await create_new_version(
            db, user_id=current_user_id, artifact_id=item_id,
            expected_version=current.version_number, idempotency_key=compatibility_key,
            title=req.title, chapter=req.chapter, content=req.content,
            public_data=req.raw_data,
        )
        await db.commit()
        return {
            "success": True, "id": item.id, "title": row.title_snapshot,
            "version_id": row.id, "version_number": row.version_number,
            "idempotent_replay": replayed,
        }
    except ArtifactVersionError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc
