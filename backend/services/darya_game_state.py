"""Canonical DARYA game-state authority.

This service deliberately imports and mutates no profile, memory, graph,
learning-event, evaluator, active-route, artifact, or legacy-adventure model.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import CanonicalGameState


CONTRACT_VERSION = "darya_game_state_v1"
CONTENT_AUTHORITY_STATUS = "ACCEPTED_SCENE01_03_SCENE04_IMPLEMENTED"
MAX_STORY_STATE_BYTES = 128 * 1024
MAX_PRESENTATION_CHECKPOINT_BYTES = 32 * 1024


class StaleGameStateError(RuntimeError):
    def __init__(self, expected_revision: int, actual_revision: int):
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            f"stale game state revision: expected {expected_revision}, actual {actual_revision}"
        )


class InvalidGameStateError(ValueError):
    pass


def _json_size(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _all_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


def _validate_semantic_state(current_scene: str, story_state: dict[str, Any]) -> None:
    forbidden = (
        "mastery", "workspacelearningevent", "learningevaluation", "evaluator",
        "activeroute", "active_route", "harvest_adventure", "adventure_summary",
    )
    normalized_keys = {key.lower().replace("-", "").replace(" ", "") for key in _all_keys(story_state)}
    violation = next((key for key in normalized_keys if any(token in key for token in forbidden)), None)
    if violation:
        raise InvalidGameStateError("game state may not carry formal learning authority")

    if story_state.get("tangJiNameKnown") and not story_state.get("historicalIdentityClueFound"):
        raise InvalidGameStateError("Tang Ji name requires the historical identity clue")
    if story_state.get("cameraHistoricalLinkSuspected") and not story_state.get("tangJiNameKnown"):
        raise InvalidGameStateError("camera history link requires the legal identity reveal")
    if current_scene in {"scene02", "scene03", "scene04Ready", "scene04", "scene05Ready"} and not story_state.get("scene01Completed"):
        raise InvalidGameStateError("Scene02 requires Scene01 completion")
    if current_scene in {"scene03", "scene04Ready", "scene04", "scene05Ready"} and not story_state.get("cameraObservationConflictConfirmed"):
        raise InvalidGameStateError("Scene03 requires the camera/reality conflict")
    if current_scene in {"scene04Ready", "scene04", "scene05Ready"}:
        required = (
            "evacRequired117Seen", "evacConfirmed116Seen", "visitorFiveFourSeen",
            "historicalIdentityClueFound", "cameraHistoricalLinkSuspected",
            "humanTruthFragment01", "systemTruthFragment01", "scene04Ready",
        )
        if not all(story_state.get(key) for key in required):
            raise InvalidGameStateError("Scene04 readiness evidence chain is incomplete")
    if story_state.get("routeConflictObserved") and not all(story_state.get(key) for key in ("broadcastRoomInspected", "patchPanelInspected", "routeLedgerInspected")):
        raise InvalidGameStateError("Scene04 route conflict requires all three room clues")
    if story_state.get("daryaSupportReturned") and not story_state.get("daryaCognitiveMomentOpened"):
        raise InvalidGameStateError("Scene04 support return requires an opened cognitive moment")
    if current_scene == "scene05Ready" and not all(story_state.get(key) for key in ("routeConflictObserved", "daryaSupportReturned", "scene04InvestigationCompleted", "scene05Ready")):
        raise InvalidGameStateError("Scene05 readiness requires the completed Scene04 investigation")


def _validate_payload(current_scene: str, story_state: dict[str, Any], presentation_checkpoint: dict[str, Any]) -> None:
    if _json_size(story_state) > MAX_STORY_STATE_BYTES:
        raise ValueError("story_state exceeds the canonical 128 KiB limit")
    if _json_size(presentation_checkpoint) > MAX_PRESENTATION_CHECKPOINT_BYTES:
        raise ValueError("presentation_checkpoint exceeds the canonical 32 KiB limit")
    _validate_semantic_state(current_scene, story_state)


def project_game_state(row: CanonicalGameState) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "content_authority_status": CONTENT_AUTHORITY_STATUS,
        "user_id": row.user_id,
        "current_scene": row.current_scene,
        "story_state": dict(row.story_state or {}),
        "presentation_checkpoint": dict(row.presentation_checkpoint or {}),
        "revision": row.revision,
        "schema_version": row.schema_version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def get_or_create_game_state(db: AsyncSession, user_id: int) -> CanonicalGameState:
    result = await db.execute(select(CanonicalGameState).where(CanonicalGameState.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is not None:
        return row

    row = CanonicalGameState(user_id=user_id)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        result = await db.execute(select(CanonicalGameState).where(CanonicalGameState.user_id == user_id))
        return result.scalar_one()
    await db.refresh(row)
    return row


async def replace_game_state(
    db: AsyncSession,
    user_id: int,
    *,
    base_revision: int,
    current_scene: str,
    story_state: dict[str, Any],
    presentation_checkpoint: dict[str, Any],
) -> CanonicalGameState:
    _validate_payload(current_scene, story_state, presentation_checkpoint)
    await get_or_create_game_state(db, user_id)

    result = await db.execute(
        update(CanonicalGameState)
        .where(
            CanonicalGameState.user_id == user_id,
            CanonicalGameState.revision == base_revision,
        )
        .values(
            current_scene=current_scene,
            story_state=story_state,
            presentation_checkpoint=presentation_checkpoint,
            revision=CanonicalGameState.revision + 1,
        )
    )
    if result.rowcount != 1:
        await db.rollback()
        actual = await db.scalar(
            select(CanonicalGameState.revision).where(CanonicalGameState.user_id == user_id)
        )
        raise StaleGameStateError(base_revision, int(actual or 0))

    await db.commit()
    refreshed = await db.execute(select(CanonicalGameState).where(CanonicalGameState.user_id == user_id))
    return refreshed.scalar_one()
