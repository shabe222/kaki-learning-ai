"""Durable server-judged attempts for workspace interactive lesson artifacts."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    GeneratedItem,
    GeneratedItemVersion,
    InteractiveQuestionAttempt,
    WorkspaceLearningEvent,
)
from services.workspace_artifact_versions import ArtifactVersionNotFound, get_owned_artifact, get_version


class InteractiveAttemptError(ValueError):
    code = "INTERACTIVE_ATTEMPT_ERROR"
    status_code = 409


class InteractiveAttemptNotFound(InteractiveAttemptError):
    code = "INTERACTIVE_ATTEMPT_NOT_FOUND"
    status_code = 404


class InteractiveAttemptConflict(InteractiveAttemptError):
    code = "INTERACTIVE_ATTEMPT_CONFLICT"
    status_code = 409


class InteractiveAttemptInvalid(InteractiveAttemptError):
    code = "INTERACTIVE_ATTEMPT_INVALID"
    status_code = 422


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _submission_digest(user_id: int, artifact_id: int, version_id: int, item_identity: str, key: str) -> str:
    return _sha(f"interactive-attempt-v1\0{user_id}\0{artifact_id}\0{version_id}\0{item_identity}\0{key}")


def _guard(*, request_id: str, selected_answer: Any, thread_id: str) -> str:
    return _sha(_canonical({
        "submission_request_id": request_id,
        "selected_answer": selected_answer,
        "thread_id": thread_id,
    }))


def _item_index(item_identity: str) -> int:
    value = str(item_identity or "").strip().lower()
    if value.startswith("slide-"):
        value = value[6:]
    try:
        return int(value)
    except ValueError as exc:
        raise InteractiveAttemptInvalid("题目身份无效") from exc


def _attempt_public(row: InteractiveQuestionAttempt) -> dict[str, Any]:
    return {
        "attempt_id": row.id,
        "artifact_id": row.artifact_id,
        "artifact_version_id": row.artifact_version_id,
        "item_identity": row.item_identity,
        "attempt_number": row.attempt_number,
        "selected_answer": row.selected_answer_public,
        "correct": bool(row.correct),
        "hint_used": bool(row.hint_used),
        "solution_viewed": bool(row.solution_viewed),
        "status": row.status,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else "",
        "learning_event_ref": f"workspace-event-{row.event_id}" if row.event_id else None,
    }


async def _owned_interactive_version(
    db: AsyncSession, *, user_id: int, artifact_id: int, artifact_version_id: int, thread_id: str | None = None,
) -> tuple[GeneratedItem, GeneratedItemVersion, dict[str, Any], list[dict[str, Any]]]:
    try:
        item = await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    except ArtifactVersionNotFound as exc:
        raise InteractiveAttemptNotFound("互动课堂不存在或无权访问") from exc
    if item.type != "workspace_interactive_lesson":
        raise InteractiveAttemptInvalid("当前资源不支持正式互动作答")
    try:
        version = await get_version(db, item=item, version_id=artifact_version_id)
    except ArtifactVersionNotFound as exc:
        raise InteractiveAttemptNotFound("互动课堂版本不存在或无权访问") from exc
    data = dict(version.public_data_snapshot or {})
    private_data = dict(version.private_data_snapshot or {})
    if str(data.get("delivery_mode") or "") != "interactive_lesson":
        raise InteractiveAttemptInvalid("当前资源不支持正式互动作答")
    owned_thread = str(data.get("thread_id") or (item.raw_data or {}).get("thread_id") or "")
    if thread_id is not None and owned_thread and owned_thread != thread_id:
        raise InteractiveAttemptNotFound("互动课堂不属于当前会话")
    slides = list(private_data.get("slides") or [])
    if not slides:
        # Compatibility projection may hold the current slides outside the immutable v1 snapshot.
        slides = list((item.raw_data or {}).get("slides") or [])
    return item, version, data, slides


async def record_support_action(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    artifact_id: int,
    artifact_version_id: int,
    item_identity: str,
    action: str,
) -> dict[str, Any]:
    if action not in {"hint_requested", "solution_viewed"}:
        raise InteractiveAttemptInvalid("不支持的学习帮助动作")
    item, version, _, slides = await _owned_interactive_version(
        db, user_id=user_id, artifact_id=artifact_id,
        artifact_version_id=artifact_version_id, thread_id=thread_id,
    )
    index = _item_index(item_identity)
    if index < 0 or index >= len(slides) or not (slides[index].get("interaction") or {}):
        raise InteractiveAttemptInvalid("当前步骤没有可作答题目")
    latest_attempt_number = int(await db.scalar(select(func.max(InteractiveQuestionAttempt.attempt_number)).where(
        InteractiveQuestionAttempt.user_id == user_id,
        InteractiveQuestionAttempt.artifact_version_id == version.id,
        InteractiveQuestionAttempt.item_identity == item_identity,
    )) or 0)
    if action == "solution_viewed" and latest_attempt_number < 1:
        raise InteractiveAttemptInvalid("提交答案后才能查看完整解析")
    applies_to_attempt_number = latest_attempt_number + 1
    existing_support = list((await db.execute(select(WorkspaceLearningEvent).where(
        WorkspaceLearningEvent.user_id == user_id,
        WorkspaceLearningEvent.artifact_id == artifact_id,
        WorkspaceLearningEvent.event_type == f"interaction_{action}",
    ))).scalars().all())
    for existing in existing_support:
        payload = dict(existing.payload or {})
        if int(payload.get("artifact_version_id") or 0) != version.id:
            continue
        if str(payload.get("item_identity") or "") != item_identity:
            continue
        if int(payload.get("applies_to_attempt_number") or 0) != applies_to_attempt_number:
            continue
        interaction = dict(slides[index].get("interaction") or {})
        if action == "hint_requested":
            hint = str(interaction.get("hint") or "先找出题目在比较什么，再逐项排除不符合条件的选项。")
            return {"action": action, "hint": hint[:800], "event_ref": f"workspace-event-{existing.id}", "idempotent_replay": True}
        explanation = str(interaction.get("explanation") or "提交答案后再查看完整解析。")
        return {"action": action, "explanation": explanation[:2000], "event_ref": f"workspace-event-{existing.id}", "idempotent_replay": True}
    event = WorkspaceLearningEvent(
        user_id=user_id,
        thread_id=thread_id,
        artifact_id=artifact_id,
        event_type=f"interaction_{action}",
        payload={
            "artifact_version_id": version.id,
            "artifact_version_number": version.version_number,
            "item_identity": item_identity,
            "slide_index": index,
            # Support used before the next answer belongs to that next immutable attempt.
            "applies_to_attempt_number": applies_to_attempt_number,
            "after_attempt_number": latest_attempt_number,
        },
    )
    db.add(event)
    await db.flush()
    interaction = dict(slides[index].get("interaction") or {})
    if action == "hint_requested":
        hint = str(interaction.get("hint") or "先找出题目在比较什么，再逐项排除不符合条件的选项。")
        return {"action": action, "hint": hint[:800], "event_ref": f"workspace-event-{event.id}"}
    explanation = str(interaction.get("explanation") or "提交答案后再查看完整解析。")
    return {"action": action, "explanation": explanation[:2000], "event_ref": f"workspace-event-{event.id}"}


async def _support_flags(
    db: AsyncSession, *, user_id: int, artifact_id: int, version_id: int, item_identity: str,
    attempt_number: int,
) -> tuple[bool, bool]:
    rows = list((await db.execute(select(WorkspaceLearningEvent).where(
        WorkspaceLearningEvent.user_id == user_id,
        WorkspaceLearningEvent.artifact_id == artifact_id,
        WorkspaceLearningEvent.event_type.in_(("interaction_hint_requested", "interaction_solution_viewed")),
    ))).scalars().all())
    hint = False
    solution = False
    for row in rows:
        payload = dict(row.payload or {})
        if int(payload.get("artifact_version_id") or 0) != version_id:
            continue
        if str(payload.get("item_identity") or "") != item_identity:
            continue
        applies_to = int(payload.get("applies_to_attempt_number") or attempt_number)
        if applies_to != attempt_number:
            continue
        hint = hint or row.event_type == "interaction_hint_requested"
        solution = solution or row.event_type == "interaction_solution_viewed"
    return hint, solution


async def submit_interactive_answer(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    artifact_id: int,
    artifact_version_id: int,
    item_identity: str,
    selected_answer: Any,
    submission_request_id: str,
    idempotency_key: str,
) -> tuple[dict[str, Any], bool]:
    if selected_answer is None or selected_answer == "":
        raise InteractiveAttemptInvalid("请先选择或填写答案")
    if not submission_request_id or len(submission_request_id) > 120:
        raise InteractiveAttemptInvalid("缺少有效的提交请求身份")
    if not idempotency_key or len(idempotency_key) > 240:
        raise InteractiveAttemptInvalid("缺少有效的提交幂等身份")
    item, version, data, slides = await _owned_interactive_version(
        db, user_id=user_id, artifact_id=artifact_id,
        artifact_version_id=artifact_version_id, thread_id=thread_id,
    )
    index = _item_index(item_identity)
    if index < 0 or index >= len(slides):
        raise InteractiveAttemptInvalid("题目不存在或版本已经变化")
    interaction = dict(slides[index].get("interaction") or {})
    if not interaction:
        raise InteractiveAttemptInvalid("当前步骤没有可作答题目")
    options = list(interaction.get("options") or [])
    try:
        selected_index = int(selected_answer)
    except (TypeError, ValueError) as exc:
        raise InteractiveAttemptInvalid("当前题目需要选择一个选项") from exc
    if selected_index < 0 or (options and selected_index >= len(options)):
        raise InteractiveAttemptInvalid("选择的答案超出当前题目范围")

    digest = _submission_digest(user_id, artifact_id, version.id, item_identity, idempotency_key)
    guard = _guard(request_id=submission_request_id, selected_answer=selected_index, thread_id=thread_id)
    existing = await db.scalar(select(InteractiveQuestionAttempt).where(
        InteractiveQuestionAttempt.artifact_version_id == version.id,
        InteractiveQuestionAttempt.item_identity == item_identity,
        InteractiveQuestionAttempt.submission_key_digest == digest,
    ))
    if existing is not None:
        if existing.request_guard != guard:
            raise InteractiveAttemptConflict("相同幂等身份对应了不同答案")
        return await build_attempt_response(db, row=existing, slides=slides, version=version), True

    correct_index = int(interaction.get("correct_index") or 0)
    correct = selected_index == correct_index
    attempt_number = int(await db.scalar(select(func.max(InteractiveQuestionAttempt.attempt_number)).where(
        InteractiveQuestionAttempt.user_id == user_id,
        InteractiveQuestionAttempt.artifact_version_id == version.id,
        InteractiveQuestionAttempt.item_identity == item_identity,
    )) or 0) + 1
    hint_used, solution_viewed = await _support_flags(
        db, user_id=user_id, artifact_id=artifact_id,
        version_id=version.id, item_identity=item_identity, attempt_number=attempt_number,
    )
    attempt = InteractiveQuestionAttempt(
        user_id=user_id,
        thread_id=thread_id,
        artifact_id=artifact_id,
        artifact_version_id=version.id,
        item_identity=item_identity,
        attempt_number=attempt_number,
        submission_key_digest=digest,
        request_guard=guard,
        selected_answer_public=selected_index,
        correct=correct,
        hint_used=hint_used,
        solution_viewed=solution_viewed,
        status="SUBMITTED",
        submitted_at=_now(),
    )
    # Unique constraints protect both sequential attempt numbering and replay identity.
    # A collision fails before the immutable learning event is written.
    db.add(attempt)
    await db.flush()

    payload = {
        "attempt_id": attempt.id,
        "attempt_number": attempt.attempt_number,
        "artifact_version_id": version.id,
        "artifact_version_number": version.version_number,
        "item_identity": item_identity,
        "slide_index": index,
        "selected_index": selected_index,
        "correct": correct,
        "correct_index": correct_index,
        "hint_used": hint_used,
        "solution_viewed": solution_viewed,
        "graph_id": data.get("graph_id"),
        "graph_node_external_id": data.get("graph_node_external_id") or data.get("graph_node_id"),
        "resource_plan_id": data.get("resource_plan_id") or data.get("resource_plan_external_id"),
    }
    event = WorkspaceLearningEvent(
        user_id=user_id,
        thread_id=thread_id,
        artifact_id=item.id,
        event_type="interaction_answered",
        payload=payload,
    )
    db.add(event)
    await db.flush()
    attempt.event_id = event.id
    await db.flush()
    return await build_attempt_response(db, row=attempt, slides=slides, version=version), False


def _lesson_semantics(*, slides: list[dict[str, Any]], answered: set[str], current_index: int, raw_progress: dict[str, Any]) -> dict[str, Any]:
    total_steps = len(slides)
    interactive_total = sum(1 for slide in slides if slide.get("interaction"))
    viewed = {int(value) for value in (raw_progress.get("viewed_slide_indexes") or []) if str(value).lstrip("-").isdigit()}
    lesson_viewing_completed = bool(
        total_steps
        and bool(raw_progress.get("completed"))
        and all(index in viewed for index in range(total_steps))
    )
    answered_count = len(answered)
    all_attempted = bool(interactive_total and answered_count >= interactive_total)
    return {
        "current_step": min(max(int(current_index) + 1, 1), max(total_steps, 1)),
        "total_steps": total_steps,
        "answered_interactive_items": answered_count,
        "total_interactive_items": interactive_total,
        "all_interactive_items_attempted": all_attempted,
        "lesson_viewing_completed": lesson_viewing_completed,
    }


async def build_attempt_response(
    db: AsyncSession, *, row: InteractiveQuestionAttempt, slides: list[dict[str, Any]], version: GeneratedItemVersion,
) -> dict[str, Any]:
    index = _item_index(row.item_identity)
    interaction = dict(slides[index].get("interaction") or {}) if 0 <= index < len(slides) else {}
    attempts = list((await db.execute(select(InteractiveQuestionAttempt).where(
        InteractiveQuestionAttempt.user_id == row.user_id,
        InteractiveQuestionAttempt.artifact_version_id == row.artifact_version_id,
    ).order_by(InteractiveQuestionAttempt.submitted_at.asc(), InteractiveQuestionAttempt.id.asc()))).scalars().all())
    answered_items = {attempt.item_identity for attempt in attempts}
    item = await db.get(GeneratedItem, row.artifact_id)
    raw_progress = dict(((item.raw_data or {}).get("progress") or {}) if item else {})
    progress = _lesson_semantics(
        slides=slides, answered=answered_items, current_index=index, raw_progress=raw_progress,
    )
    return {
        "attempt": _attempt_public(row),
        "result": {
            "correct": bool(row.correct),
            "explanation_available": bool(interaction.get("explanation")),
            "retry_allowed": True,
            "explanation": str(interaction.get("explanation") or "")[:2000] if row.solution_viewed else None,
        },
        "progress": progress,
        "learning_event_ref": f"workspace-event-{row.event_id}" if row.event_id else None,
        "evaluation_status": "READY_FOR_EVALUATION",
        "completion_eligibility": None,
        "artifact_version_id": version.id,
        "artifact_version_number": version.version_number,
    }


async def get_interactive_state(
    db: AsyncSession,
    *,
    user_id: int,
    artifact_id: int,
    artifact_version_id: int,
    thread_id: str | None = None,
) -> dict[str, Any]:
    item, version, _, slides = await _owned_interactive_version(
        db, user_id=user_id, artifact_id=artifact_id,
        artifact_version_id=artifact_version_id, thread_id=thread_id,
    )
    rows = list((await db.execute(select(InteractiveQuestionAttempt).where(
        InteractiveQuestionAttempt.user_id == user_id,
        InteractiveQuestionAttempt.artifact_id == artifact_id,
        InteractiveQuestionAttempt.artifact_version_id == version.id,
    ).order_by(InteractiveQuestionAttempt.submitted_at.asc(), InteractiveQuestionAttempt.id.asc()))).scalars().all())
    interactive_indexes = [index for index, slide in enumerate(slides) if slide.get("interaction")]
    support_rows = list((await db.execute(select(WorkspaceLearningEvent).where(
        WorkspaceLearningEvent.user_id == user_id,
        WorkspaceLearningEvent.artifact_id == artifact_id,
        WorkspaceLearningEvent.event_type.in_(("interaction_hint_requested", "interaction_solution_viewed")),
    ).order_by(WorkspaceLearningEvent.id.asc()))).scalars().all())
    support_by_item: dict[str, dict[str, Any]] = {}
    for event in support_rows:
        payload = dict(event.payload or {})
        if int(payload.get("artifact_version_id") or 0) != version.id:
            continue
        identity = str(payload.get("item_identity") or "")
        if not identity:
            continue
        state = support_by_item.setdefault(identity, {"hint_requested": False, "solution_viewed": False})
        if event.event_type == "interaction_hint_requested":
            state["hint_requested"] = True
        elif event.event_type == "interaction_solution_viewed":
            state["solution_viewed"] = True
        state["last_event_ref"] = f"workspace-event-{event.id}"
    answered = {row.item_identity for row in rows}
    current_index = interactive_indexes[0] if interactive_indexes else 0
    if rows:
        current_index = min(_item_index(rows[-1].item_identity) + 1, max(len(slides) - 1, 0))
    raw_progress = dict((item.raw_data or {}).get("progress") or {})
    progress = _lesson_semantics(
        slides=slides, answered=answered, current_index=current_index, raw_progress=raw_progress,
    )
    return {
        "artifact_id": artifact_id,
        "artifact_version_id": version.id,
        "artifact_version_number": version.version_number,
        "current_item": f"slide-{current_index}",
        "attempts": [_attempt_public(row) for row in rows],
        "support_by_item": support_by_item,
        "progress": progress,
        "retry_allowed": True,
        "evaluation_status": "READY_FOR_EVALUATION" if rows else "NOT_EVALUATED",
        "completion_eligibility": None,
    }
