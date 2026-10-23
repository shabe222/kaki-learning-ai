"""Persistence boundary for server-owned workspace task state."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import WorkspaceAgentTask
from services.workspace_task_contracts import AgentTask, AgentType, TaskState


_ACTIVE_STATES = {
    TaskState.PLANNED.value,
    TaskState.COLLECTING_REQUIREMENTS.value,
    TaskState.AWAITING_CONFIRMATION.value,
    TaskState.CONFIRMED.value,
    TaskState.QUEUED.value,
    TaskState.RUNNING.value,
    TaskState.RETRYING.value,
}


def _retry_count(execution_key: str | None) -> int:
    marker = ":retry:"
    text = str(execution_key or "")
    if marker not in text:
        return 0
    try:
        return max(0, int(text.rsplit(marker, 1)[1]))
    except ValueError:
        return 0


def _from_row(row: WorkspaceAgentTask) -> AgentTask:
    return AgentTask(
        task_id=row.task_id,
        user_id=row.user_id,
        thread_id=row.thread_id,
        agent_type=AgentType(row.agent_type),
        slots=dict(row.slots or {}),
        state=TaskState(row.state),
        version=row.version,
        confirmed_version=row.confirmed_version,
        execution_key=row.execution_key,
        artifact_id=row.artifact_id,
        error_code=row.error_code,
        conversation_id=row.thread_id,
        resource_type=row.agent_type,
        retry_count=_retry_count(row.execution_key),
        failure_reason=row.error_code,
        source_artifact_id=(row.slots or {}).get("source_artifact_id"),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def load_current_task(
    db: AsyncSession, user_id: int, thread_id: str, *, include_terminal: bool = True,
) -> Optional[AgentTask]:
    query = select(WorkspaceAgentTask).where(
        WorkspaceAgentTask.user_id == user_id,
        WorkspaceAgentTask.thread_id == thread_id,
    )
    if not include_terminal:
        query = query.where(WorkspaceAgentTask.state.in_(_ACTIVE_STATES))
    query = query.order_by(desc(WorkspaceAgentTask.id)).limit(1)
    result = await db.execute(query)
    row = result.scalar_one_or_none()
    return _from_row(row) if row else None


async def load_task_by_id(
    db: AsyncSession, user_id: int, thread_id: str, task_id: str,
) -> Optional[AgentTask]:
    """Load one task inside the authenticated user/thread boundary."""
    result = await db.execute(select(WorkspaceAgentTask).where(
        WorkspaceAgentTask.task_id == task_id,
        WorkspaceAgentTask.user_id == user_id,
        WorkspaceAgentTask.thread_id == thread_id,
    ))
    row = result.scalar_one_or_none()
    return _from_row(row) if row else None


async def save_task(db: AsyncSession, task: AgentTask) -> AgentTask:
    task.updated_at = datetime.utcnow()
    result = await db.execute(select(WorkspaceAgentTask).where(
        WorkspaceAgentTask.task_id == task.task_id,
        WorkspaceAgentTask.user_id == task.user_id,
        WorkspaceAgentTask.thread_id == task.thread_id,
    ))
    row = result.scalar_one_or_none()
    if row is None:
        row = WorkspaceAgentTask(task_id=task.task_id, user_id=task.user_id, thread_id=task.thread_id)
        db.add(row)
    row.agent_type = task.agent_type.value
    row.state = task.state.value
    row.slots = dict(task.slots)
    row.version = task.version
    row.confirmed_version = task.confirmed_version
    row.execution_key = task.execution_key
    row.artifact_id = task.artifact_id
    row.error_code = task.error_code
    await db.commit()
    await db.refresh(row)
    return _from_row(row)


async def cancel_current_task(db: AsyncSession, user_id: int, thread_id: str) -> Optional[AgentTask]:
    task = await load_current_task(db, user_id, thread_id, include_terminal=False)
    if task is None:
        return None
    task.state = TaskState.CANCELLED
    return await save_task(db, task)


async def complete_task(db: AsyncSession, task: AgentTask, *, artifact_id: int) -> AgentTask:
    task = task.model_copy(deep=True)
    task.state = TaskState.COMPLETED
    task.artifact_id = int(artifact_id)
    task.error_code = None
    task.failure_reason = None
    return await save_task(db, task)


async def fail_task(db: AsyncSession, task: AgentTask, *, error_code: str) -> AgentTask:
    task = task.model_copy(deep=True)
    task.state = TaskState.FAILED
    task.error_code = str(error_code)[:80]
    task.failure_reason = task.error_code
    return await save_task(db, task)


async def retry_failed_task(db: AsyncSession, task: AgentTask) -> AgentTask:
    """Retry confirmed slots under a fresh durable execution identity."""
    if task.state != TaskState.FAILED or task.confirmed_version != task.version:
        raise ValueError("only a confirmed failed task can be retried")
    task = task.model_copy(deep=True)
    base_key = f"{task.task_id}:{task.version}"
    retry_prefix = f"{base_key}:retry:"
    retry_number = 1
    current_key = str(task.execution_key or "")
    if current_key.startswith(retry_prefix):
        try:
            retry_number = int(current_key[len(retry_prefix):]) + 1
        except ValueError:
            retry_number = 1
    task.execution_key = f"{retry_prefix}{retry_number}"
    task.retry_count = retry_number
    task.state = TaskState.RUNNING
    task.error_code = None
    return await save_task(db, task)
