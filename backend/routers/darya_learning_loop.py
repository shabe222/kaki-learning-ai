"""Authenticated inspection of DARYA learning-node and adaptive-route state."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import ChatSession, GraphResourcePlan, LearningGraph, WorkspaceLearningEvent
from services.darya_active_route import EVIDENCE_TRIGGERS, build_active_route_projection
from services.darya_learning_loop import (
    get_learning_route_state,
    get_learning_state,
    get_learning_states,
    public_latest_learning_state,
)
from services.darya_teaching_stage import teaching_stage_projection
from utils.auth import get_current_user_id


router = APIRouter(prefix="/api/tide/learning-loop", tags=["darya-learning-loop"])


@router.get("/{thread_id}")
async def get_thread_learning_loop(
    thread_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    owned = (await db.execute(select(ChatSession.id).where(
        ChatSession.user_id == current_user_id,
        ChatSession.thread_id == thread_id,
    ).limit(1))).scalar_one_or_none()
    if owned is None:
        raise HTTPException(status_code=404, detail="这段对话不存在或不可用。")
    row = await get_learning_state(db, user_id=current_user_id, thread_id=thread_id)
    rows = await get_learning_states(db, user_id=current_user_id, thread_id=thread_id)
    route_row = await get_learning_route_state(db, user_id=current_user_id, thread_id=thread_id)
    snapshot = public_latest_learning_state(
        row,
        thread_id=thread_id,
        route_row=route_row,
        rows=rows,
    )
    evidence_plan_row = (await db.execute(
        select(GraphResourcePlan)
        .join(LearningGraph, LearningGraph.id == GraphResourcePlan.graph_id)
        .where(
            LearningGraph.user_id == current_user_id,
            GraphResourcePlan.thread_id == thread_id,
            GraphResourcePlan.trigger.in_(tuple(EVIDENCE_TRIGGERS)),
        )
        .order_by(GraphResourcePlan.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    evidence_event_row = None
    if evidence_plan_row is not None:
        source_ref = str((evidence_plan_row.generation_spec or {}).get("source_evidence_event") or "")
        if source_ref.startswith("workspace-event-") and source_ref.removeprefix("workspace-event-").isdigit():
            evidence_event_row = (await db.execute(select(WorkspaceLearningEvent).where(
                WorkspaceLearningEvent.id == int(source_ref.removeprefix("workspace-event-")),
                WorkspaceLearningEvent.user_id == current_user_id,
                WorkspaceLearningEvent.thread_id == thread_id,
            ))).scalar_one_or_none()
    plan_public = None if evidence_plan_row is None else {
        "id": evidence_plan_row.id,
        "trigger": evidence_plan_row.trigger,
        "resource_type": evidence_plan_row.resource_type,
        "purpose": evidence_plan_row.purpose,
        "created_at": evidence_plan_row.created_at,
    }
    event_public = None if evidence_event_row is None else {
        "id": evidence_event_row.id,
        "event_type": evidence_event_row.event_type,
        "payload": {"correct": bool((evidence_event_row.payload or {}).get("correct"))},
        "created_at": evidence_event_row.created_at,
    }
    snapshot["active_route"] = build_active_route_projection(
        snapshot,
        evidence_plan=plan_public,
        evidence_event=event_public,
    )
    snapshot["teaching_stage"] = teaching_stage_projection(route_row, rows)
    return {
        "success": True,
        "data": snapshot,
    }
