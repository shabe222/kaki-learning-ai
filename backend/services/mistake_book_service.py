"""Personalized mistake book — exam errors turned into diagnosis + variants.

Generation is LLM-driven under the mistake-book-variants skill, grounded only
in the graded exam record (question, learner answer, correct answer, grading
feedback).  Nothing is invented about the learner beyond that record.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ExamPaper, GeneratedItem
from services.extract_llm_json import extract_json_object
from services.skill_library import get_skill, skill_directive
from services.workspace_artifact_versions import ensure_initial_version

logger = logging.getLogger(__name__)

ERROR_TYPES = {"概念误用", "程序性错误", "审题偏差", "计算疏漏", "未作答"}


class MistakeBookError(RuntimeError):
    code = "MISTAKE_BOOK_ERROR"


async def generate_mistake_book(
    db: AsyncSession, *, user_id: int, exam_id: int, thread_id: str = "",
    llm: Any | None = None,
) -> GeneratedItem:
    """Build one mistake book from a graded, owned exam paper."""
    exam = (await db.execute(select(ExamPaper).where(
        ExamPaper.id == int(exam_id), ExamPaper.user_id == int(user_id),
    ))).scalar_one_or_none()
    if exam is None:
        raise MistakeBookError("试卷不存在或无权访问。")
    if exam.status != "completed":
        raise MistakeBookError("试卷尚未提交判分，不能生成错题本。")
    grading = list(dict(exam.profile_snapshot or {}).get("last_grading") or [])
    wrong = [entry for entry in grading if not entry.get("correct")]
    if not wrong:
        raise MistakeBookError("本次考试全部答对，没有需要复盘的错题。")

    skill = get_skill("mistake-book-variants")
    if skill is None:
        raise MistakeBookError("mistake-book-variants 技能包缺失。")
    if llm is None:
        from config import settings
        if str(settings.LLM_PROVIDER).lower() == "mock":
            raise MistakeBookError("错题本生成需要配置真实模型（当前为 mock 模式）。")
        from services.llm_service_real import get_llm_service
        llm = await get_llm_service()

    inputs = []
    for entry in wrong:
        question_index = entry.get("question_index")
        inputs.append({
            "index": int(question_index) if question_index is not None else -1,
            "question": str(entry.get("question") or "")[:600],
            "learner_answer": str(entry.get("answer") or "")[:300],
            "correct_answer": str(entry.get("correct_answer") or "")[:300],
            "grading_feedback": str(entry.get("explanation") or "")[:300],
            "points": entry.get("total"),
        })
    prompt = {
        "skill": skill_directive([skill]),
        "mistakes": inputs,
        "requirement": "依据 skill 对每道错题给出诊断与 2 道变式题；输出 JSON。",
    }
    from services.llm_service_real import extract_llm_text
    raw = await llm.chat(
        messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        temperature=0.2, max_tokens=3000, use_cache=False, write_cache=False,
    )
    parsed = extract_json_object(extract_llm_text(raw))
    mistakes = _validated_mistakes((parsed or {}).get("mistakes"), {int(i["index"]) for i in inputs})
    content = {
        "title": f"错题复盘（{len(mistakes)} 题）",
        "exam_id": int(exam_id),
        "mistakes": mistakes,
    }
    item = GeneratedItem(
        user_id=int(user_id), type="personalized_mistake_book",
        title=content["title"][:200],
        chapter=str(exam.chapter_ids[0] if exam.chapter_ids else "")[:120] or None,
        content=json.dumps(content, ensure_ascii=False),
        raw_data={
            "kind": "mistake_book", "status": "ready", "thread_id": str(thread_id or ""),
            "exam_id": int(exam_id), "wrong_count": len(mistakes),
            "generation_provenance": {"mode": "skill_llm", "skills": ["mistake-book-variants"]},
        },
    )
    db.add(item)
    await db.flush()
    await ensure_initial_version(db, item)
    await db.commit()
    await db.refresh(item)
    return item


def _validated_mistakes(raw: Any, expected_indexes: set[int]) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise MistakeBookError("模型未返回有效的错题诊断。")
    seen: set[int] = set()
    mistakes: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise MistakeBookError("错题诊断格式无效。")
        raw_index = entry.get("index")
        index = int(raw_index) if raw_index is not None else -1
        if index not in expected_indexes or index in seen:
            raise MistakeBookError("错题诊断与考试记录不对应。")
        seen.add(index)
        error_type = str(entry.get("error_type") or "").strip()
        misconception = str(entry.get("misconception") or "").strip()
        remediation = str(entry.get("remediation") or "").strip()
        if not misconception or not remediation:
            raise MistakeBookError("错题诊断内容缺失。")
        variants: list[dict[str, Any]] = []
        raw_variants = entry.get("variants")
        if not isinstance(raw_variants, list) or not (1 <= len(raw_variants) <= 3):
            raise MistakeBookError("变式题数量无效。")
        for variant in raw_variants:
            if not isinstance(variant, dict):
                raise MistakeBookError("变式题格式无效。")
            question = str(variant.get("question") or "").strip()
            answer = str(variant.get("answer") or "").strip()
            if not question or not answer:
                raise MistakeBookError("变式题内容缺失。")
            options = [str(o)[:120] for o in (variant.get("options") or []) if str(o).strip()]
            variants.append({
                "type": str(variant.get("type") or "short_answer")[:30],
                "question": question[:500],
                "options": options,
                "answer": answer[:300],
                "explanation": str(variant.get("explanation") or "")[:300],
            })
        mistakes.append({
            "index": index,
            "error_type": error_type if error_type in ERROR_TYPES else "概念误用",
            "misconception": misconception[:300],
            "remediation": remediation[:300],
            "variants": variants,
        })
    return mistakes
