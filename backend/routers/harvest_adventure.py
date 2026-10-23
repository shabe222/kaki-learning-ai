from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from schemas.harvest_adventure import (
    AdventureAdvanceRequest,
    AdventurePrepareAdjustRequest,
    AdventurePrepareRequest,
    AdventureRewardEquipRequest,
    AdventureRewardUnequipRequest,
    AdventureStartRequest,
)
from services.harvest_adventure import (
    adjust_prepared_adventure,
    advance_adventure,
    equip_adventure_reward,
    get_adventure_rewards,
    get_current_adventure,
    get_current_prepared_plan,
    prepare_adventure,
    start_adventure,
    unequip_adventure_reward,
)
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/adventure", tags=["harvest-adventure"])


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, PermissionError):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))


@router.get("/current")
async def current(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return {"success": True, "data": await get_current_adventure(db, user_id)}


@router.get("/prepare/current")
async def current_prepare(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return {"success": True, "data": await get_current_prepared_plan(db, user_id)}


@router.post("/prepare")
async def prepare(
    request: AdventurePrepareRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await prepare_adventure(
            db, user_id, request.topic_hint,
            explicit_confusion=request.explicit_confusion,
            thread_id=request.thread_id,
            source_ids=request.source_ids,
        )
    except (ValueError, PermissionError) as error:
        raise _http_error(error) from error
    return {"success": True, "data": data}


@router.post("/prepare/adjust")
async def prepare_adjust(
    request: AdventurePrepareAdjustRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await adjust_prepared_adventure(
            db, user_id, request.plan_id, request.focus_adjustment,
        )
    except (ValueError, LookupError, PermissionError) as error:
        raise _http_error(error) from error
    return {"success": True, "data": data}


@router.post("/start")
async def start(
    request: AdventureStartRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await start_adventure(
            db, user_id, request.topic, request.magic_school, request.source_ids,
            request.thread_id, request.source_type, request.force_new,
            prepared_plan_id=request.prepared_plan_id,
            identity_id=request.identity_id,
            talent_id=request.talent_id,
        )
    except (ValueError, PermissionError) as error:
        raise _http_error(error) from error
    return {"success": True, "data": data}


@router.post("/advance")
async def advance(
    request: AdventureAdvanceRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await advance_adventure(db, user_id, request.run_id, request.action, request.payload)
    except (ValueError, LookupError, PermissionError) as error:
        raise _http_error(error) from error
    return {"success": True, "data": data}


@router.get("/rewards")
async def rewards(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return {"success": True, "data": await get_adventure_rewards(db, user_id)}


@router.post("/rewards/equip")
async def equip_reward(
    request: AdventureRewardEquipRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await equip_adventure_reward(
            db,
            user_id,
            request.artifact_id,
            replace_confirmed=request.replace_confirmed,
        )
    except (LookupError, PermissionError) as error:
        raise _http_error(error) from error
    return {"success": True, "data": data}


@router.post("/rewards/unequip")
async def unequip_reward(
    request: AdventureRewardUnequipRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await unequip_adventure_reward(db, user_id, request.artifact_id)
    except (LookupError, PermissionError) as error:
        raise _http_error(error) from error
    return {"success": True, "data": data}
