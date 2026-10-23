"""Shared, typed-enough artifact projections for cross-agent reuse."""
from __future__ import annotations

import json
from typing import Any, Iterable


def build_exam_result_artifact(
    *, exam_id: int, topic: str, score: float, total_score: float,
    grading: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    grading_rows = [dict(row) for row in (grading or [])]
    wrong_questions = []
    knowledge_points = []
    for row in grading_rows:
        if row.get("correct") is True:
            continue
        knowledge_point = str(row.get("topic") or "").strip()
        if knowledge_point and knowledge_point not in knowledge_points:
            knowledge_points.append(knowledge_point)
        wrong_questions.append({
            "question_number": int(row.get("question_index") or 0) + 1,
            "question_id": str(row.get("question_id") or ""),
            "question": str(row.get("question") or ""),
            "user_answer": str(row.get("answer") or ""),
            "correct_answer": str(row.get("correct_answer") or ""),
            "knowledge_point": knowledge_point,
            "explanation": str(row.get("explanation") or row.get("feedback") or ""),
            "earned": float(row.get("earned") or 0),
            "total": float(row.get("total") or 0),
        })
    return {
        "artifact_id": int(exam_id),
        "kind": "exam_result",
        "title": "练习结果",
        "topic": str(topic or "").strip(),
        "score": float(score or 0),
        "total_score": float(total_score or 0),
        "grading": grading_rows,
        "wrong_questions": wrong_questions,
        "wrong_question_count": len(wrong_questions),
        "knowledge_points": knowledge_points,
        "status": "completed",
        "version": 1,
    }


def artifact_context(artifact: dict[str, Any]) -> str:
    """Return bounded factual context; never include prompts or hidden reasoning."""
    kind = str(artifact.get("kind") or "")
    if kind == "exam_result":
        payload = {
            "artifact_id": artifact.get("artifact_id"),
            "kind": kind,
            "topic": artifact.get("topic"),
            "score": artifact.get("score"),
            "total_score": artifact.get("total_score"),
            "wrong_questions": list(artifact.get("wrong_questions") or [])[:20],
            "knowledge_points": list(artifact.get("knowledge_points") or [])[:30],
        }
    elif kind == "learning_graph":
        payload = {
            "kind": kind,
            "graph_id": artifact.get("graph_id"),
            "graph_revision": artifact.get("graph_revision"),
            "title": artifact.get("title"),
            "summary": artifact.get("summary"),
            "user_goal": artifact.get("user_goal"),
            "domain": artifact.get("domain"),
            "source_ref_ids": list(artifact.get("source_ref_ids") or [])[:40],
        }
    else:
        payload = {
            "artifact_id": artifact.get("artifact_id"),
            "kind": kind,
            "title": artifact.get("title"),
            "topic": artifact.get("topic"),
            "summary": artifact.get("summary"),
        }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:12000]
