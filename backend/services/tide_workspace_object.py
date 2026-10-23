"""Thread-bounded Workspace Object foundation for Tide Phase 2.2.

Workspace Objects describe the concrete thing being worked on.  They never
create files or artifacts and never read cross-thread memory or user profiles.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Literal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TideSharedPerspectiveSnapshot, TideWorkspaceObject


WorkspaceStatus = Literal["exploring", "planning", "creating", "reviewing", "completed"]
WorkspaceAction = Literal["confirm", "rename", "correct_purpose", "set_focus"]

WORKSPACE_STATUSES = {"exploring", "planning", "creating", "reviewing", "completed"}


class WorkspaceObjectError(ValueError):
    def __init__(self, message: str, *, code: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _clean_text(value: Any, max_length: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n。！？!?.,")[:max_length]


def _perspective_item(snapshot: TideSharedPerspectiveSnapshot | None, name: str) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    item = (snapshot.fields or {}).get(name)
    return item if isinstance(item, dict) else None


def _perspective_value(snapshot: TideSharedPerspectiveSnapshot | None, name: str) -> Any:
    item = _perspective_item(snapshot, name)
    return item.get("value") if item else None


def _status_from_stage(value: Any) -> WorkspaceStatus:
    stage = _clean_text(value, 240).lower()
    if re.search(r"完成|已交付|结束|completed|done|delivered", stage):
        return "completed"
    if re.search(r"评审|审阅|检查|修改|完善|收口|答辩|review|refin|polish", stage):
        return "reviewing"
    if re.search(r"制作|撰写|开发|实现|搭建|creating|writing|building|implement", stage):
        return "creating"
    if re.search(r"计划|规划|结构|大纲|planning|outline", stage):
        return "planning"
    return "exploring"


def _artifact_references(snapshot: TideSharedPerspectiveSnapshot | None) -> list[dict[str, Any]]:
    raw = _perspective_value(snapshot, "current_artifacts")
    if not isinstance(raw, list):
        return []
    refs: list[dict[str, Any]] = []
    for item in raw[:8]:
        if not isinstance(item, dict) or not item.get("artifact_id"):
            continue
        refs.append({
            key: item.get(key)
            for key in ("artifact_id", "version_id", "version_number", "kind", "title")
        })
    return refs


def _confirmed_decisions(snapshot: TideSharedPerspectiveSnapshot | None) -> list[str]:
    item = _perspective_item(snapshot, "confirmed_decisions")
    if not item or item.get("status") != "confirmed":
        return []
    raw = item.get("value")
    values = raw if isinstance(raw, list) else [raw]
    return [cleaned for value in values if (cleaned := _clean_text(value, 500))][:8]


async def get_current_workspace_object(
    db: AsyncSession, *, user_id: int, thread_id: str,
) -> TideWorkspaceObject | None:
    return (await db.execute(
        select(TideWorkspaceObject)
        .where(
            TideWorkspaceObject.user_id == user_id,
            TideWorkspaceObject.thread_id == thread_id,
            TideWorkspaceObject.is_current.is_(True),
        )
        .order_by(desc(TideWorkspaceObject.updated_at), desc(TideWorkspaceObject.id))
        .limit(1)
    )).scalar_one_or_none()


async def sync_workspace_object_from_perspective(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    perspective: TideSharedPerspectiveSnapshot | None,
) -> TideWorkspaceObject | None:
    """Create only from a confirmed goal, then conservatively sync live state."""
    current = await get_current_workspace_object(db, user_id=user_id, thread_id=thread_id)
    goal_item = _perspective_item(perspective, "goal")
    confirmed_goal = (
        _clean_text(goal_item.get("value"), 240)
        if goal_item and goal_item.get("status") == "confirmed"
        else ""
    )
    if current is None:
        if not perspective or not confirmed_goal:
            return None
        current = TideWorkspaceObject(
            user_id=user_id,
            thread_id=thread_id,
            title=confirmed_goal,
            purpose=confirmed_goal,
            status=_status_from_stage(_perspective_value(perspective, "stage")),
            related_artifact_references=_artifact_references(perspective),
            related_decisions=_confirmed_decisions(perspective),
            next_action=_clean_text(_perspective_value(perspective, "next_action"), 500) or None,
            is_current=True,
            is_confirmed=False,
            revision=1,
            source_perspective_revision=perspective.revision,
        )
        db.add(current)
        await db.flush()
        return current

    if perspective is None or perspective.revision <= current.source_perspective_revision:
        return current

    changed = False
    if not current.is_confirmed and confirmed_goal and confirmed_goal != current.title:
        current.title = confirmed_goal
        current.purpose = confirmed_goal
        changed = True

    status = _status_from_stage(_perspective_value(perspective, "stage"))
    if status != current.status:
        current.status = status
        changed = True

    artifacts = _artifact_references(perspective)
    if artifacts and artifacts != (current.related_artifact_references or []):
        current.related_artifact_references = artifacts
        changed = True

    decisions = _confirmed_decisions(perspective)
    if decisions and decisions != (current.related_decisions or []):
        current.related_decisions = decisions
        changed = True

    next_action = _clean_text(_perspective_value(perspective, "next_action"), 500) or None
    if next_action and next_action != current.next_action:
        current.next_action = next_action
        changed = True

    current.source_perspective_revision = perspective.revision
    if changed:
        current.revision += 1
        await db.flush()
    return current


async def apply_workspace_object_control(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    action: WorkspaceAction,
    expected_revision: int,
    value: Any = None,
) -> TideWorkspaceObject:
    current = await get_current_workspace_object(db, user_id=user_id, thread_id=thread_id)
    if current is None:
        raise WorkspaceObjectError("当前对话还没有可调整的工作对象。", code="OBJECT_NOT_FOUND", status_code=404)
    if current.revision != expected_revision:
        raise WorkspaceObjectError(
            "工作对象已发生变化，请刷新后再修改。",
            code="REVISION_CONFLICT",
            status_code=409,
        )

    if action == "confirm":
        current.is_confirmed = True
    elif action == "rename":
        cleaned = _clean_text(value, 240)
        if not cleaned:
            raise WorkspaceObjectError("名称不能为空。", code="VALUE_REQUIRED")
        current.title = cleaned
        current.is_confirmed = True
    elif action == "correct_purpose":
        cleaned = _clean_text(value, 500)
        if not cleaned:
            raise WorkspaceObjectError("目的不能为空。", code="VALUE_REQUIRED")
        current.purpose = cleaned
        current.is_confirmed = True
    elif action == "set_focus":
        cleaned = _clean_text(value, 500)
        if not cleaned:
            raise WorkspaceObjectError("当前焦点不能为空。", code="VALUE_REQUIRED")
        current.next_action = cleaned
    else:
        raise WorkspaceObjectError("未知的操作。", code="UNKNOWN_ACTION")

    current.revision += 1
    await db.flush()
    return current


def public_workspace_object(value: TideWorkspaceObject | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "id": value.id,
        "thread_id": value.thread_id,
        "title": value.title,
        "purpose": value.purpose,
        "status": value.status,
        "related_artifact_references": copy.deepcopy(value.related_artifact_references or []),
        "related_decisions": copy.deepcopy(value.related_decisions or []),
        "next_action": value.next_action,
        "is_confirmed": value.is_confirmed,
        "revision": value.revision,
        "source_perspective_revision": value.source_perspective_revision,
        "created_at": value.created_at.isoformat() if value.created_at else None,
        "updated_at": value.updated_at.isoformat() if value.updated_at else None,
    }


def workspace_object_context(value: TideWorkspaceObject | None) -> dict[str, Any]:
    public = public_workspace_object(value)
    if not public:
        return {}
    return {
        key: public[key]
        for key in (
            "id", "title", "purpose", "status", "related_artifact_references",
            "related_decisions", "next_action", "is_confirmed",
        )
    }


def build_workspace_object_directive(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    payload = json.dumps(context, ensure_ascii=False)
    return (
        "当前线程的 Workspace Object（共同工作的具体对象）如下：\n"
        f"{payload}\n"
        "回答时要承接该对象的目的、状态和下一步，而不是给脱离上下文的通用建议。"
        "不得虚构文件、产物版本、项目历史或未列出的决定；未确认的信息要明确为暂定判断。"
    )


def current_artifact_target(value: TideWorkspaceObject | None) -> tuple[int | None, int | None]:
    refs = value.related_artifact_references if value else []
    first = refs[0] if isinstance(refs, list) and refs else None
    if not isinstance(first, dict):
        return None, None
    try:
        artifact_id = int(first.get("artifact_id")) if first.get("artifact_id") else None
        version_id = int(first.get("version_id")) if first.get("version_id") else None
        return artifact_id, version_id
    except (TypeError, ValueError):
        return None, None
