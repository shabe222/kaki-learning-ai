from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.learner_state import (
    ContextMode,
    LearnerStateSnapshot,
    build_fallback_blueprint,
    build_request_from_project_data,
    compile_snapshot,
    normalize_topic_key,
    private_storage_payload,
    to_adventure_planner_learner_state,
)
from agents.learner_state.normalizers import normalize_blueprint
from agents.learner_state.validators import validate_blueprint
from database.models import GeneratedItem, WorkspaceAgentTask
from integrations.learner_state_adapter import build_learner_state_analyzer


SLOT_KEY = "learner_state"
RUNTIME_KEY = "_learner_state_runtime"
SNAPSHOT_KEY = "learner_state_snapshot"
MAX_MODEL_CALLS_PER_RUN = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_from_payload(value: Any) -> LearnerStateSnapshot | None:
    if not isinstance(value, dict):
        return None
    try:
        return LearnerStateSnapshot.model_validate(value)
    except Exception:
        return None


def snapshot_from_slots(slots: dict[str, Any]) -> LearnerStateSnapshot | None:
    return snapshot_from_payload(slots.get(SLOT_KEY))


def planner_state_from_snapshot(snapshot: LearnerStateSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return to_adventure_planner_learner_state(snapshot)


def teaching_mode(snapshot: LearnerStateSnapshot | None) -> str:
    if snapshot is None or snapshot.topic_mastery.value in {"unknown", "emerging"}:
        return "concept_building"
    if snapshot.primary_barrier.category.value in {"concept_confusion", "representation_gap"}:
        return "misconception_distinction"
    if snapshot.transfer_readiness.value in {"ready", "demonstrated"}:
        return "transfer_challenge"
    if snapshot.support_level.value in {"worked_example", "step_by_step", "strong_scaffold"}:
        return "scaffolded_practice"
    return "mechanism_linking"


def _runtime(slots: dict[str, Any]) -> dict[str, Any]:
    value = dict(slots.get(RUNTIME_KEY) or {})
    value.setdefault("schema_version", "0.2")
    value.setdefault("model_calls", 0)
    value.setdefault("model_checkpoints", [])
    value.setdefault("hint_events", 0)
    return value


def record_hint_event(
    slots: dict[str, Any],
    *,
    node: str,
    hint: str,
    boss_stage: int | None = None,
) -> None:
    timeline = list(slots.get("timeline") or [])
    timeline.append({
        "node": str(node).upper(),
        "boss_stage": boss_stage,
        "decision": "hint",
        "result": "hint_used",
        "hint": str(hint or "")[:300],
        "at": _now(),
    })
    slots["timeline"] = timeline
    runtime = _runtime(slots)
    runtime["hint_events"] = int(runtime.get("hint_events") or 0) + 1
    runtime["last_trigger"] = "hint"
    runtime["updated_at"] = _now()
    slots[RUNTIME_KEY] = runtime


async def load_latest_topic_snapshot(
    db: AsyncSession,
    user_id: int,
    topic_name: str,
) -> LearnerStateSnapshot | None:
    topic_key = normalize_topic_key(topic_name)

    tasks = (await db.execute(
        select(WorkspaceAgentTask)
        .where(
            WorkspaceAgentTask.user_id == user_id,
            WorkspaceAgentTask.agent_type == "harvest_adventure",
        )
        .order_by(desc(WorkspaceAgentTask.id))
        .limit(12)
    )).scalars().all()
    for task in tasks:
        snapshot = snapshot_from_slots(dict(task.slots or {}))
        if snapshot and snapshot.topic_key == topic_key and snapshot.student_id == str(user_id):
            return snapshot

    items = (await db.execute(
        select(GeneratedItem)
        .where(
            GeneratedItem.user_id == user_id,
            GeneratedItem.type == "adventure_summary",
        )
        .order_by(desc(GeneratedItem.id))
        .limit(20)
    )).scalars().all()
    for item in items:
        raw_data = dict(item.raw_data or {})
        snapshot = snapshot_from_payload(raw_data.get(SNAPSHOT_KEY))
        if snapshot and snapshot.topic_key == topic_key and snapshot.student_id == str(user_id):
            return snapshot
    return None


def _context_mode(slots: dict[str, Any]) -> ContextMode:
    raw = str(slots.get("context_mode") or "relevant_recent")
    try:
        return ContextMode(raw)
    except ValueError:
        return ContextMode.RELEVANT_RECENT


def build_request_for_run(
    task: WorkspaceAgentTask,
    slots: dict[str, Any],
    *,
    attempted_node: str,
    boss_stage: int | None = None,
):
    archive = dict(slots.get("archive") or {})
    topic_name = str(archive.get("topic") or slots.get("topic_hint") or "当前学习主题")
    previous = snapshot_from_slots(slots)
    return build_request_from_project_data(
        student_id=task.user_id,
        topic_name=topic_name,
        explicit_confusion=slots.get("explicit_confusion"),
        timeline=list(slots.get("timeline") or []),
        boss_stage=boss_stage,
        context_mode=_context_mode(slots),
        previous_topic_state=previous,
        current_material_summary=f"拾光漫游节点 {attempted_node}：{topic_name}",
        request_id=f"{task.task_id}:{attempted_node}:{len(slots.get('timeline') or [])}",
    )


def _compile_deterministic(request, warning: str) -> LearnerStateSnapshot:
    blueprint = build_fallback_blueprint(request)
    blueprint, warnings = normalize_blueprint(blueprint, request)
    validate_blueprint(blueprint, request)
    return compile_snapshot(
        blueprint,
        request,
        source="fallback",
        warnings=[warning, *warnings],
    )


def _checkpoint_key(node: str, boss_stage: int | None) -> str | None:
    node = str(node).upper()
    if node == "TRIAL":
        return "TRIAL"
    if node == "INTERLEAVE":
        return "INTERLEAVE"
    if node == "BOSS" and boss_stage == 2:
        return "BOSS:2"
    return None


async def update_after_submit(
    task: WorkspaceAgentTask,
    slots: dict[str, Any],
    *,
    attempted_node: str,
    boss_stage: int | None = None,
    analyzer_factory=build_learner_state_analyzer,
) -> LearnerStateSnapshot:
    """Update private topic state without ever blocking the adventure state machine."""
    request = build_request_for_run(
        task, slots, attempted_node=attempted_node, boss_stage=boss_stage
    )
    runtime = _runtime(slots)
    checkpoint_key = _checkpoint_key(attempted_node, boss_stage)
    completed_checkpoints = {str(item) for item in (runtime.get("model_checkpoints") or [])}
    use_model = (
        checkpoint_key is not None
        and checkpoint_key not in completed_checkpoints
        and int(runtime.get("model_calls") or 0) < MAX_MODEL_CALLS_PER_RUN
    )

    if use_model:
        runtime["model_calls"] = int(runtime.get("model_calls") or 0) + 1
        completed_checkpoints.add(checkpoint_key)
        runtime["model_checkpoints"] = sorted(completed_checkpoints)
        try:
            analyzer = await analyzer_factory()
            result = await analyzer.analyze(request)
            snapshot = result.state
            runtime["last_metadata"] = {
                "generation_mode": result.metadata.generation_mode,
                "fallback_used": result.metadata.fallback_used,
                "warnings": result.metadata.warnings[:8],
                "finish_reason": result.metadata.finish_reason,
                "configured_model_id": result.metadata.configured_model_id,
                "provider_returned_model_id": result.metadata.provider_returned_model_id,
            }
        except Exception as error:
            snapshot = _compile_deterministic(
                request, f"learner_state_integration_error:{type(error).__name__}"
            )
            runtime["last_metadata"] = {
                "generation_mode": "fallback",
                "fallback_used": True,
                "warnings": ["learner_state_integration_error"],
            }
    else:
        snapshot = _compile_deterministic(request, "learner_state_deterministic_checkpoint")
        runtime["last_metadata"] = {
            "generation_mode": "deterministic",
            "fallback_used": False,
            "warnings": ["learner_state_deterministic_checkpoint"],
        }

    slots[SLOT_KEY] = private_storage_payload(snapshot)
    runtime["last_trigger"] = f"submit:{str(attempted_node).upper()}:{boss_stage if boss_stage is not None else 'na'}"
    runtime["last_state_id"] = snapshot.state_id
    runtime["teaching_mode"] = teaching_mode(snapshot)
    runtime["updated_at"] = snapshot.updated_at
    slots[RUNTIME_KEY] = runtime
    return snapshot


def initialize_run_state(
    slots: dict[str, Any],
    snapshot: LearnerStateSnapshot | None,
    *,
    plan_id: str | None = None,
    draft_revision: int | None = None,
) -> None:
    if snapshot is not None:
        slots[SLOT_KEY] = private_storage_payload(snapshot)
    runtime = _runtime(slots)
    runtime["plan_id"] = plan_id
    runtime["draft_revision"] = draft_revision
    runtime["teaching_mode"] = teaching_mode(snapshot)
    runtime["updated_at"] = _now()
    slots[RUNTIME_KEY] = runtime


def attach_private_snapshot(raw_data: dict[str, Any], slots: dict[str, Any]) -> None:
    snapshot = snapshot_from_slots(slots)
    if snapshot is not None:
        raw_data[SNAPSHOT_KEY] = private_storage_payload(snapshot)
