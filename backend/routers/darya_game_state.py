from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from schemas.darya_game_state import CanonicalGameStateUpdateRequest
from services.darya_game_state import (
    InvalidGameStateError,
    StaleGameStateError,
    get_or_create_game_state,
    project_game_state,
    replace_game_state,
)
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/darya-game", tags=["darya-game-state"])


@router.get("/state")
async def current_game_state(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    row = await get_or_create_game_state(db, user_id)
    return {"success": True, "data": project_game_state(row)}


@router.put("/state")
async def update_game_state(
    request: CanonicalGameStateUpdateRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await replace_game_state(
            db,
            user_id,
            base_revision=request.base_revision,
            current_scene=request.current_scene,
            story_state=request.story_state,
            presentation_checkpoint=request.presentation_checkpoint,
        )
    except StaleGameStateError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_GAME_STATE_REVISION",
                "expected_revision": error.expected_revision,
                "actual_revision": error.actual_revision,
            },
        ) from error
    except InvalidGameStateError as error:
        raise HTTPException(status_code=422, detail={"code": "INVALID_GAME_STATE", "message": str(error)}) from error
    except ValueError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    return {"success": True, "data": project_game_state(row)}
