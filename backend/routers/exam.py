import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from agents.learning_evaluator import EvaluationContractError
from database.models import AnswerRecord, ExamPaper, GeneratedItem, WorkspaceLearningEvent
from services.exam_service import (
    EXAM_TYPE_CONFIG,
    check_practice_answer,
    create_exam,
    exam_mode_contract,
    get_exam,
    get_user_exams,
    public_exam_questions,
    submit_exam,
)
from services.learning_evaluator_integration import EvaluationAccessDeniedError, auto_consume_exam_submission_event
from services.profile_service import save_answer_records
from utils.auth import get_current_user_id, require_self

router = APIRouter(prefix="/api/exam", tags=["exam"])
logger = logging.getLogger(__name__)


class CreateExamRequest(BaseModel):
    user_id: int
    exam_type: str
    chapter_ids: List[str]
    difficulty: Optional[str] = None  # 全部 / 简单 / 中等 / 困难
    question_types: Optional[List[str]] = None  # 单选题 / 多选题 / 判断题 / 填空题 / 简答题 / 编程题
    count: Optional[int] = None  # 题目数量 30-80
    source_kind: Optional[str] = None
    specification: Optional[str] = None


class SubmitExamRequest(BaseModel):
    answers: List
    time_spent_sec: int = 0


class CheckAnswerRequest(BaseModel):
    question_index: int
    answer: object = None


@router.get("/types")
async def list_exam_types():
    return {
        "success": True,
        "data": {
            key: {
                "label": value["label"],
                "time_min": value["time_min"],
                "feedback_policy": value.get("feedback_policy", "after_submit"),
                "source_policy": value.get("source_policy", "selected_scope"),
            }
            for key, value in EXAM_TYPE_CONFIG.items()
        },
    }


@router.post("/create")
async def api_create_exam(
    req: CreateExamRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if req.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="cannot create exam for another user")

    try:
        exam = await create_exam(
            db,
            req.user_id,
            req.exam_type,
            req.chapter_ids,
            difficulty=req.difficulty,
            question_types=req.question_types,
            count=req.count,
            source_kind=req.source_kind,
            specification=req.specification,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return {
        "success": True,
        "data": {
            "id": exam.id,
            "exam_type": exam.exam_type,
            "chapter_ids": exam.chapter_ids,
            "questions": public_exam_questions(exam.questions),
            "time_limit_min": exam.time_limit_min,
            "total_score": exam.total_score,
            "status": exam.status,
            "mode_contract": dict((exam.profile_snapshot or {}).get("exam_mode_contract") or {}),
        },
    }


@router.get("/{exam_id}")
async def api_get_exam(
    exam_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    exam = await get_exam(db, exam_id)
    if not exam or exam.user_id != current_user_id:
        raise HTTPException(404, "exam not found")

    response = {
        "id": exam.id,
        "exam_type": exam.exam_type,
        "chapter_ids": exam.chapter_ids,
        "questions": public_exam_questions(exam.questions),
        "time_limit_min": exam.time_limit_min,
        "total_score": exam.total_score,
        "status": exam.status,
        "mode_contract": dict((exam.profile_snapshot or {}).get("exam_mode_contract") or exam_mode_contract(exam.exam_type)),
    }
    if exam.status == "completed":
        response["answers"] = exam.answers
        response["score"] = exam.score
        response["time_spent_sec"] = exam.time_spent_sec
        response["grading"] = list((exam.profile_snapshot or {}).get("last_grading") or [])
        response["learning_update"] = dict((exam.profile_snapshot or {}).get("last_learning_update") or {})
    return {"success": True, "data": response}


@router.post("/{exam_id}/check")
async def api_check_practice_answer(
    exam_id: int,
    req: CheckAnswerRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    exam = await get_exam(db, exam_id)
    if not exam or exam.user_id != current_user_id:
        raise HTTPException(404, "exam not found")
    try:
        result = await check_practice_answer(exam, req.question_index, req.answer)
    except ValueError:
        raise HTTPException(409, "当前模式需统一交卷后查看反馈")
    except IndexError:
        raise HTTPException(404, "question not found")
    return {"success": True, "data": result}


@router.post("/{exam_id}/submit")
async def api_submit_exam(
    exam_id: int,
    req: SubmitExamRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    owner = await db.execute(select(ExamPaper.user_id).where(ExamPaper.id == exam_id))
    exam_owner_id = owner.scalar_one_or_none()
    if exam_owner_id is None or exam_owner_id != current_user_id:
        raise HTTPException(404, "exam not found")

    result = await submit_exam(db, exam_id, req.answers, req.time_spent_sec)
    if "error" in result:
        raise HTTPException(404, result["error"])
    if result.get("already_submitted"):
        # Replay of the stored verdict: no new answer records, no new learning
        # events, no second billed grading pass.
        return result

    answers_with_topic = [
        {
            "question_id": str(item.get("question_id", "")),
            "topic": item.get("topic", ""),
            "correct": bool(item.get("correct", False)),
            "time_spent_sec": float(item.get("time_spent_sec", req.time_spent_sec or 0)),
        }
        for item in result.get("grading", [])
    ]
    await save_answer_records(db, current_user_id, answers_with_topic, "exam")

    snapshot = dict((await get_exam(db, exam_id)).profile_snapshot or {})
    target = dict(snapshot.get("evidence_context") or {})
    artifact_id = snapshot.get("evidence_artifact_id")
    evaluation = {"evaluation_status": "not_applicable", "evaluation_revision": None}
    if target.get("graph_id") and target.get("graph_node_external_id") and artifact_id:
        owned_artifact = await db.scalar(select(GeneratedItem).where(
            GeneratedItem.id == int(artifact_id), GeneratedItem.user_id == current_user_id,
        ))
        if owned_artifact is not None:
            existing_event = None
            rows = list((await db.execute(select(WorkspaceLearningEvent).where(
                WorkspaceLearningEvent.user_id == current_user_id,
                WorkspaceLearningEvent.thread_id == f"exam-center-{exam_id}",
                WorkspaceLearningEvent.event_type == "exam_submitted",
            ).order_by(WorkspaceLearningEvent.id.desc()).limit(5))).scalars().all())
            for row in rows:
                if int((row.payload or {}).get("exam_id") or 0) == exam_id:
                    existing_event = row
                    break
            event = existing_event or WorkspaceLearningEvent(
                user_id=current_user_id,
                thread_id=f"exam-center-{exam_id}",
                artifact_id=int(artifact_id),
                event_type="exam_submitted",
                payload={
                    "exam_id": exam_id,
                    "score": result.get("score"),
                    "total_score": result.get("total_score"),
                    "question_count": len(result.get("grading") or []),
                    "correct_count": sum(1 for item in result.get("grading") or [] if item.get("correct")),
                    "graph_id": target["graph_id"],
                    "graph_node_external_id": target["graph_node_external_id"],
                    "evidence_source": "exam_center_submission",
                },
            )
            if existing_event is None:
                db.add(event)
                await db.commit()
                await db.refresh(event)
            try:
                evaluation = await auto_consume_exam_submission_event(
                    db, user_id=current_user_id, workspace_event_id=event.id,
                )
            except (EvaluationContractError, EvaluationAccessDeniedError, ValueError) as exc:
                logger.info("exam center evaluator pending user=%s exam=%s type=%s", current_user_id, exam_id, type(exc).__name__)
                evaluation = {"evaluation_status": "evaluation_pending", "evaluation_revision": None}
            except Exception:
                logger.exception("exam center evaluator unavailable user=%s exam=%s", current_user_id, exam_id)
                evaluation = {"evaluation_status": "evaluation_pending", "evaluation_revision": None}
    result["learning_update"] = evaluation
    completed_exam = await get_exam(db, exam_id)
    completed_snapshot = dict(completed_exam.profile_snapshot or {})
    completed_exam.profile_snapshot = {**completed_snapshot, "last_learning_update": evaluation}
    await db.commit()

    return {"success": True, "data": result}


@router.get("/list/{user_id}")
async def api_list_exams(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    exam_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    require_self(user_id, current_user_id)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="invalid user_id")

    exams = await get_user_exams(db, user_id, exam_type)
    return {
        "success": True,
        "data": [
            {
                "id": exam.id,
                "exam_type": exam.exam_type,
                "chapter_ids": exam.chapter_ids,
                "score": exam.score,
                "total_score": exam.total_score,
                "status": exam.status,
                "time_limit_min": exam.time_limit_min,
                "time_spent_sec": exam.time_spent_sec or 0,
                "question_count": len(exam.questions or []),
                "created_at": str(exam.created_at),
                "completed_at": str(exam.completed_at) if exam.completed_at else None,
            }
            for exam in exams
        ],
    }


@router.get("/wrong-questions/{user_id}")
async def api_wrong_questions(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    require_self(user_id, current_user_id)
    result = await db.execute(
        select(AnswerRecord)
        .where(AnswerRecord.user_id == user_id, AnswerRecord.correct == False)
        .order_by(AnswerRecord.created_at.desc())
        .limit(limit)
    )
    records = result.scalars().all()

    by_topic = {}
    for record in records:
        topic = record.topic or "uncategorized"
        by_topic.setdefault(topic, []).append(
            {
                "question_id": record.question_id,
                "time_spent_sec": record.time_spent_sec,
                "source": record.source,
                "created_at": record.created_at.isoformat() if record.created_at else "",
            }
        )

    return {"success": True, "data": {"total": len(records), "by_topic": by_topic}}
