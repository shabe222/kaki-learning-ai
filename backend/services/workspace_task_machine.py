"""Deterministic lifecycle for every workspace agent task."""
from __future__ import annotations

from typing import Optional

from services.workspace_agent_adapters import AGENT_ADAPTERS
from services.workspace_task_contracts import (
    AgentTask,
    AgentType,
    ArtifactOperation,
    SemanticAction,
    SemanticRoute,
    TaskDecision,
    TaskState,
)


TASK_EXECUTION_MIN_CONFIDENCE = 0.82
TASK_CONTROL_MIN_CONFIDENCE = 0.88


def _minimum_confidence(action: SemanticAction) -> float:
    if action in {
        SemanticAction.SUPPLEMENT,
        SemanticAction.REVISE,
        SemanticAction.CONFIRM,
        SemanticAction.CANCEL,
        SemanticAction.REPLACE,
        SemanticAction.ARTIFACT_ACTION,
    }:
        return TASK_CONTROL_MIN_CONFIDENCE
    if action == SemanticAction.NEW_TASK:
        return TASK_EXECUTION_MIN_CONFIDENCE
    return 0.0


def _make_task(user_id: int, thread_id: str, agent_type: AgentType, updates: dict) -> AgentTask:
    adapter = AGENT_ADAPTERS.get(agent_type)
    if adapter is None:
        raise ValueError(f"agent is not enabled: {agent_type.value}")
    slots = adapter.initial_slots()
    slots.update(adapter.normalise_updates(updates))
    return AgentTask(user_id=user_id, thread_id=thread_id, agent_type=agent_type, slots=slots)


def _ready_decision(task: AgentTask) -> TaskDecision:
    adapter = AGENT_ADAPTERS[task.agent_type]
    missing = adapter.missing_slots(task.slots)
    if missing:
        task.state = TaskState.COLLECTING_REQUIREMENTS
        return TaskDecision(task=task, mode="task", reply=adapter.missing_question(missing))
    task.state = TaskState.AWAITING_CONFIRMATION
    return TaskDecision(task=task, mode="task", reply=adapter.confirmation(task.slots))


def advance_task(
    current: Optional[AgentTask],
    semantic: SemanticRoute,
    user_id: int,
    thread_id: str,
) -> TaskDecision:
    """Apply one semantic action without allowing the LLM to execute anything."""
    if semantic.confidence < _minimum_confidence(semantic.action):
        return TaskDecision(
            task=current.model_copy(deep=True) if current else None,
            mode="chat",
        )

    action = semantic.action
    if action in {SemanticAction.QUESTION, SemanticAction.NORMAL_CHAT}:
        return TaskDecision(task=current.model_copy(deep=True) if current else None, mode="chat")

    if action == SemanticAction.ARTIFACT_ACTION:
        if semantic.artifact_operation is None or semantic.target_artifact_id is None:
            return TaskDecision(
                task=current.model_copy(deep=True) if current else None,
                mode="clarify",
                reply="我还不能确定要操作哪一个产物，请先打开或明确指定它。",
            )
        allowed_operations = {
            AgentType.CODE: {ArtifactOperation.RUN, ArtifactOperation.REVISE},
            AgentType.LECTURE: {ArtifactOperation.REVISE},
            AgentType.MINDMAP: {ArtifactOperation.OPEN, ArtifactOperation.REVISE},
            AgentType.PRESENTATION: {ArtifactOperation.REVISE},
        }
        if semantic.agent_type not in allowed_operations or semantic.artifact_operation not in allowed_operations[semantic.agent_type]:
            return TaskDecision(
                task=current.model_copy(deep=True) if current else None,
                mode="clarify",
                reply="这个产物操作目前需要你再明确一下目标。",
            )
        return TaskDecision(
            task=current.model_copy(deep=True) if current else None,
            mode="artifact_action",
            execute=True,
            execution_payload={
                "action": semantic.artifact_operation.value,
                "artifact_kind": semantic.agent_type.value,
                "artifact_id": semantic.target_artifact_id,
            },
        )

    if action == SemanticAction.CANCEL:
        return TaskDecision(task=None, mode="cancelled", reply="好的，已取消当前任务。")

    if action in {SemanticAction.NEW_TASK, SemanticAction.REPLACE}:
        if semantic.agent_type is None:
            return TaskDecision(task=current.model_copy(deep=True) if current else None, mode="chat")
        return _ready_decision(_make_task(user_id, thread_id, semantic.agent_type, semantic.slot_updates))

    if current is None:
        return TaskDecision(task=None, mode="chat")

    task = current.model_copy(deep=True)
    if task.user_id != user_id or task.thread_id != thread_id:
        raise ValueError("task ownership mismatch")

    if action == SemanticAction.CONFIRM:
        if task.state in {TaskState.RUNNING, TaskState.COMPLETED}:
            return TaskDecision(task=task, mode="task", reply="该任务已经开始或完成，不会重复执行。")
        adapter = AGENT_ADAPTERS[task.agent_type]
        missing = adapter.missing_slots(task.slots)
        if missing:
            task.state = TaskState.COLLECTING_REQUIREMENTS
            return TaskDecision(task=task, mode="task", reply=adapter.missing_question(missing))
        task.state = TaskState.RUNNING
        task.confirmed_version = task.version
        task.execution_key = f"{task.task_id}:{task.version}"
        return TaskDecision(
            task=task,
            mode="execute",
            execute=True,
            execution_payload=adapter.execution_payload(task.slots),
        )

    if action not in {SemanticAction.SUPPLEMENT, SemanticAction.REVISE}:
        return TaskDecision(task=task, mode="chat")

    if semantic.agent_type is not None and semantic.agent_type != task.agent_type:
        replacement = _make_task(user_id, thread_id, semantic.agent_type, semantic.slot_updates)
        return _ready_decision(replacement)

    adapter = AGENT_ADAPTERS[task.agent_type]
    updates = adapter.normalise_updates(semantic.slot_updates)
    if not updates:
        return TaskDecision(task=task, mode="chat")
    task.slots.update(updates)
    task.version += 1
    task.confirmed_version = None
    task.execution_key = None
    return _ready_decision(task)
