"""Positive-whitelist Learning Evaluator API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.learning_evaluator.evidence_validator import EvaluationContractError
from database.dependencies import get_db
from services.learning_evaluator_integration import (
    EvaluationAccessDeniedError,
    evaluate_graph_node,
    get_latest_public_evaluation,
    get_public_evaluation_history,
)
from utils.auth import get_current_user_id

router = APIRouter(prefix="/api/learning-evaluations", tags=["learning-evaluations"])


class EvaluateNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=4, max_length=100)
    idempotency_key: str = Field(min_length=4, max_length=140)
    expected_evaluation_revision: int | None = Field(default=None, ge=0)


def _public_contract_error(exc: EvaluationContractError) -> HTTPException:
    issues = [str(item.code) for item in exc.report.issues]
    conflict_codes = {
        "IDEMPOTENCY_BASE_REVISION_CONFLICT",
        "IDEMPOTENCY_KEY_REUSE_CONFLICT",
        "REQUEST_ID_REUSE_CONFLICT",
        "EVALUATION_REVISION_CONFLICT",
        "EVALUATION_OPERATION_IN_PROGRESS",
    }
    code = next((value for value in issues if value in conflict_codes), "EVALUATION_CONTRACT_REJECTED")
    return HTTPException(
        409 if code in conflict_codes else 422,
        {"code": code, "message": "learning evaluation request was safely rejected"},
    )


@router.post("/graphs/{graph_id}/nodes/{node_id}/evaluate")
async def evaluate_node(
    graph_id: str,
    node_id: str,
    req: EvaluateNodeRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await evaluate_graph_node(
            db,
            user_id=user_id,
            graph_ref=graph_id,
            node_ref=node_id,
            request_id=req.request_id,
            idempotency_key=req.idempotency_key,
            expected_evaluation_revision=req.expected_evaluation_revision,
        )
        return {"success": True, "data": data}
    except EvaluationAccessDeniedError as exc:
        raise HTTPException(404, "learning evaluation target not found") from exc
    except EvaluationContractError as exc:
        raise _public_contract_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(409, {"code": "EVALUATION_CONFLICT", "message": "learning evaluation state changed; refresh and retry"}) from exc
    except Exception as exc:
        # Internal exception text, storage paths, provider messages and digests are private.
        raise HTTPException(503, {"code": "EVALUATION_EXECUTION_FAILED", "message": "learning evaluation is temporarily unavailable"}) from exc


@router.get("/graphs/{graph_id}/nodes/{node_id}/latest")
async def latest_node_evaluation(
    graph_id: str,
    node_id: str,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {"success": True, "data": await get_latest_public_evaluation(
            db, user_id=user_id, graph_ref=graph_id, node_ref=node_id,
        )}
    except EvaluationAccessDeniedError as exc:
        raise HTTPException(404, "learning evaluation target not found") from exc


@router.get("/graphs/{graph_id}/nodes/{node_id}/history")
async def node_evaluation_history(
    graph_id: str,
    node_id: str,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {"success": True, "data": await get_public_evaluation_history(
            db, user_id=user_id, graph_ref=graph_id, node_ref=node_id,
        )}
    except EvaluationAccessDeniedError as exc:
        raise HTTPException(404, "learning evaluation target not found") from exc
