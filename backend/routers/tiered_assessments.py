from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from services.darya_learning_loop import get_learning_route_state, get_learning_state
from services.tiered_assessments import create_tiered_assessment, get_assessment_result, replan_after_assessment
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/tiered-assessments", tags=["tiered-assessments"])


class GenerateTieredAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=100)
    concept_key: str = Field(min_length=3, max_length=120)
    graph_id: str = Field(min_length=1, max_length=100)
    node_id: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=240)


class ReplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_version_id: int = Field(ge=1)
    item_identity: str = Field(min_length=3, max_length=120)


@router.post("/generate")
async def generate_tiered_assessment(
    req: GenerateTieredAssessmentRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    state = await get_learning_state(db, user_id=user_id, thread_id=req.thread_id, concept_key=req.concept_key)
    route = await get_learning_route_state(db, user_id=user_id, thread_id=req.thread_id)
    if state is None or route is None:
        raise HTTPException(409, {"code": "LEARNER_CONTEXT_UNAVAILABLE", "message": "当前学习状态不足，不能伪造分层测评。"})
    try:
        artifact, plan, replayed = await create_tiered_assessment(
            db, user_id=user_id, thread_id=req.thread_id, graph_ref=req.graph_id, node_ref=req.node_id,
            state=state.__dict__, route=route.__dict__, idempotency_key=req.idempotency_key,
        )
        await db.commit()
        return {"success": True, "data": artifact, "plan": plan, "idempotent_replay": replayed}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(409, {"code": str(exc), "message": "测评计划与当前学习状态不一致，请刷新后重试。"}) from exc


@router.post("/{artifact_id}/replan")
async def replan_tiered_assessment(
    artifact_id: int, req: ReplanRequest,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    try:
        result = await replan_after_assessment(
            db, user_id=user_id, artifact_id=artifact_id,
            artifact_version_id=req.artifact_version_id, item_identity=req.item_identity,
        )
        await db.commit()
        return {"success": True, "data": result}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(409, {"code": str(exc), "message": "需要先完成有效作答，才能更新下一资源。"}) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(503, {"code": "ASSESSMENT_REPLAN_FAILED", "message": "作答已保存；下一资源暂未更新，可稍后重试。"}) from exc


@router.get("/{artifact_id}/result")
async def tiered_assessment_result(
    artifact_id: int, artifact_version_id: int, item_identity: str,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    return {"success": True, "data": await get_assessment_result(
        db, user_id=user_id, artifact_id=artifact_id,
        artifact_version_id=artifact_version_id, item_identity=item_identity,
    )}


@router.get("/{artifact_id}/item-statistics")
async def tiered_assessment_item_statistics(
    artifact_id: int, artifact_version_id: int | None = None,
    user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db),
):
    """Difficulty and discrimination measured from real first attempts.

    This is the quality loop the blind reviewer cannot run: it knows whether a
    question is sound, not whether it is the right difficulty or whether it
    separates the learners who know the idea from the ones who don't.
    """
    from sqlalchemy import select

    from database.models import GeneratedItem
    from services.item_statistics import statistics_for_artifact

    item = await db.scalar(select(GeneratedItem).where(
        GeneratedItem.id == artifact_id, GeneratedItem.user_id == user_id,
    ))
    if item is None:
        raise HTTPException(404, {"code": "ASSESSMENT_NOT_FOUND", "message": "这份检验不存在或不属于你。"})
    return {"success": True, "data": await statistics_for_artifact(
        db, artifact_id=artifact_id, version_id=artifact_version_id,
    )}
