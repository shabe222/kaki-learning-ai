"""Authenticated AI-impression endpoints; separate from explicit memories."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import AIImpression
from services.impression_service import (
    IMPRESSION_SCHEMA_VERSION,
    contains_sensitive,
    list_user_impressions,
    refresh_user_impressions,
)
from services.tide_long_term_memory import build_explicit_candidate, persist_explicit_memory
from utils.auth import get_current_user_id


router = APIRouter()


class ImpressionFeedbackRequest(BaseModel):
    action: Literal["accurate", "inaccurate", "modify"]
    summary: Optional[str] = Field(default=None, min_length=2, max_length=240)


def serialize_impression(item: AIImpression):
    return {
        "id": item.id,
        "impression_key": item.impression_key,
        "summary": item.summary,
        "dimension": item.dimension,
        "confidence": item.confidence,
        "scope": item.scope,
        "evidence_count": item.evidence_count,
        "evidence_refs": item.evidence_refs or [],
        "first_observed_at": str(item.first_observed_at),
        "last_observed_at": str(item.last_observed_at),
        "status": item.status,
        "user_feedback": item.user_feedback,
        "source_type": item.source_type,
        "confirmed_by_user": bool(item.confirmed_by_user),
        "profile_mapping_key": item.profile_mapping_key,
        "schema_version": item.schema_version,
        "created_at": str(item.created_at),
        "updated_at": str(item.updated_at),
    }


async def _owned(db: AsyncSession, user_id: int, impression_id: int) -> AIImpression:
    result = await db.execute(select(AIImpression).where(
        AIImpression.id == impression_id,
        AIImpression.user_id == user_id,
    ))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Impression not found")
    return item


@router.get("/api/chat/impressions")
async def list_impressions(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    items = await list_user_impressions(db, current_user_id)
    # Existing users receive one bounded backfill when extraction rules advance.
    # New messages are observed incrementally during durable chat persistence.
    if items and any((item.schema_version or 0) < IMPRESSION_SCHEMA_VERSION for item in items):
        await refresh_user_impressions(db, current_user_id)
        items = await list_user_impressions(db, current_user_id)
    return {"success": True, "data": [serialize_impression(item) for item in items]}


@router.patch("/api/chat/impressions/{impression_id}")
async def update_impression(
    impression_id: int,
    payload: ImpressionFeedbackRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned(db, current_user_id, impression_id)
    now = datetime.utcnow()
    if payload.action == "inaccurate":
        item.status = "rejected"
        item.confirmed_by_user = False
        item.user_feedback = {"action": "inaccurate", "at": now.isoformat()}
    else:
        if payload.action == "modify":
            summary = (payload.summary or "").strip()
            if not summary:
                raise HTTPException(status_code=422, detail="Modified summary is required")
            if contains_sensitive(summary):
                raise HTTPException(status_code=422, detail="Sensitive attributes cannot be saved as an impression")
            candidate = build_explicit_candidate(summary)
            if candidate is not None:
                # A correction that maps to an existing canonical memory family
                # converges on that authority. The obsolete inference is rejected,
                # so its old semantic key cannot continue to drive behavior.
                await persist_explicit_memory(
                    db,
                    user_id=current_user_id,
                    thread_id=f"impression-correction:{item.id}",
                    source_message_id=f"impression-feedback:{item.id}:{now.isoformat()}",
                    message=summary,
                )
                item.status = "rejected"
                item.confirmed_by_user = False
                item.user_feedback = {
                    "action": "modified_into_canonical_memory",
                    "at": now.isoformat(),
                }
            else:
                # Preserve the user's corrected wording for inspection without
                # retaining the inference key/evidence that encoded the rejected X.
                item.impression_key = f"user_corrected.{item.id}"
                item.summary = summary
                item.status = "confirmed_by_user"
                item.confirmed_by_user = True
                item.confidence = 1.0
                item.source_type = "user_confirmed"
                item.profile_mapping_key = None
                item.evidence_refs = []
                item.evidence_count = 0
                item.user_feedback = {"action": payload.action, "at": now.isoformat()}
        else:
            item.status = "confirmed_by_user"
            item.confirmed_by_user = True
            item.confidence = 1.0
            item.source_type = "user_confirmed"
            item.user_feedback = {"action": payload.action, "at": now.isoformat()}
    item.updated_at = now
    await db.commit()
    await db.refresh(item)
    return {"success": True, "data": serialize_impression(item)}


@router.delete("/api/chat/impressions/{impression_id}")
async def delete_impression(
    impression_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    item = await _owned(db, current_user_id, impression_id)
    await db.delete(item)
    await db.commit()
    return {"success": True, "data": {"id": impression_id, "deleted": True}}
