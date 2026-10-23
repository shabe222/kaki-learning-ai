from __future__ import annotations

from typing import Any

from .enums import Domain, InteractionKind, TaskRole, TeachingMode
from .frame_selector import select_narrative_frame
from .models import (
    BossTaskContracts,
    NarrativeConcept,
    NarrativeGoals,
    NarrativeMisconception,
    NarrativeRequest,
    PublicTaskContract,
    RouteTaskContracts,
)


class NarrativeAdapterError(ValueError):
    pass


def _required_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NarrativeAdapterError(f"{path} must be an object")
    return value


def _required_list(value: Any, path: str, *, size: int | None = None) -> list[Any]:
    if not isinstance(value, list):
        raise NarrativeAdapterError(f"{path} must be a list")
    if size is not None and len(value) != size:
        raise NarrativeAdapterError(f"{path} must contain exactly {size} items")
    return value


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NarrativeAdapterError(f"{path} must be a non-empty string")
    return value.strip()


def _interaction(value: Any, path: str) -> InteractionKind:
    try:
        return InteractionKind(str(value))
    except ValueError as error:
        raise NarrativeAdapterError(f"invalid interaction kind at {path}: {value!r}") from error


def _public_task(raw: Any, role: TaskRole, path: str) -> PublicTaskContract:
    item = _required_dict(raw, path)
    return PublicTaskContract(
        task_role=role,
        interaction_kind=_interaction(item.get("interaction_kind"), f"{path}.interaction_kind"),
        public_prompt=_required_text(item.get("question"), f"{path}.question"),
    )


def _goal_map(preview: dict[str, Any]) -> NarrativeGoals:
    goals = _required_list(preview.get("goals"), "plan.preview.goals", size=3)
    mapped: dict[str, str] = {}
    for index, raw in enumerate(goals):
        item = _required_dict(raw, f"plan.preview.goals[{index}]")
        kind = _required_text(item.get("kind"), f"plan.preview.goals[{index}].kind")
        mapped[kind] = _required_text(item.get("text"), f"plan.preview.goals[{index}].text")
    missing = [key for key in ("understand", "distinguish", "transfer") if key not in mapped]
    if missing:
        raise NarrativeAdapterError(f"missing preview goals: {missing}")
    return NarrativeGoals(**mapped)


def build_narrative_request(
    planner_private_result: dict[str, Any],
    *,
    teaching_mode: str | TeachingMode,
    plan_id: str,
    plan_revision: int,
    request_id: str | None = None,
    focus_adjustment: str | None = None,
) -> NarrativeRequest:
    """Build a strict, answer-free narrative request from the frozen Planner result.

    This adapter intentionally extracts only public task prompts and interaction kinds.
    Correct options, accepted groups, hints, rubrics, rationales, and remediation
    answers never enter the narrative model request.
    """
    root = _required_dict(planner_private_result, "planner_private_result")
    if root.get("status") != "ready":
        raise NarrativeAdapterError("planner result must be ready")
    plan = _required_dict(root.get("plan"), "planner_private_result.plan")
    metadata = _required_dict(root.get("metadata"), "planner_private_result.metadata")

    preview = _required_dict(plan.get("preview"), "plan.preview")
    core_concepts = _required_list(plan.get("core_concepts"), "plan.core_concepts")
    misconceptions = _required_list(plan.get("misconceptions"), "plan.misconceptions")
    if not core_concepts:
        raise NarrativeAdapterError("plan.core_concepts must not be empty")
    if not misconceptions:
        raise NarrativeAdapterError("plan.misconceptions must not be empty")
    core = _required_dict(core_concepts[0], "plan.core_concepts[0]")
    misconception = _required_dict(misconceptions[0], "plan.misconceptions[0]")
    challenges = _required_dict(plan.get("challenges"), "plan.challenges")

    route_tasks = RouteTaskContracts(
        understanding_trial=_public_task(
            challenges.get("understanding_trial"), TaskRole.UNDERSTANDING_TRIAL, "plan.challenges.understanding_trial"
        ),
        practice_trial=_public_task(
            challenges.get("practice_trial"), TaskRole.PRACTICE_TRIAL, "plan.challenges.practice_trial"
        ),
        remedy=_public_task(challenges.get("remedy"), TaskRole.REMEDY, "plan.challenges.remedy"),
        understanding_interleave=_public_task(
            challenges.get("understanding_interleave"),
            TaskRole.UNDERSTANDING_INTERLEAVE,
            "plan.challenges.understanding_interleave",
        ),
        practice_interleave=_public_task(
            challenges.get("practice_interleave"),
            TaskRole.PRACTICE_INTERLEAVE,
            "plan.challenges.practice_interleave",
        ),
    )

    boss_items = _required_list(challenges.get("boss"), "plan.challenges.boss", size=3)
    boss_tasks: dict[str, PublicTaskContract] = {}
    expected = [
        ("understand", TaskRole.BOSS_UNDERSTAND),
        ("distinguish", TaskRole.BOSS_DISTINGUISH),
        ("transfer", TaskRole.BOSS_TRANSFER),
    ]
    for index, (kind, role) in enumerate(expected):
        stage = _required_dict(boss_items[index], f"plan.challenges.boss[{index}]")
        if stage.get("kind") != kind:
            raise NarrativeAdapterError(
                f"boss stage order mismatch at index {index}: expected {kind}, got {stage.get('kind')!r}"
            )
        boss_tasks[kind] = _public_task(stage.get("task"), role, f"plan.challenges.boss[{index}].task")

    try:
        mode = teaching_mode if isinstance(teaching_mode, TeachingMode) else TeachingMode(teaching_mode)
        domain = Domain(_required_text(plan.get("domain"), "plan.domain"))
    except ValueError as error:
        raise NarrativeAdapterError(str(error)) from error

    frame = select_narrative_frame(mode, route_tasks)
    draft_revision = int(metadata.get("draft_revision") or plan_revision)
    adjustment_count = int(metadata.get("adjustment_count") or max(0, draft_revision - 1))

    return NarrativeRequest(
        request_id=request_id or f"narrative:{plan_id}:{plan_revision}",
        plan_id=plan_id,
        plan_revision=plan_revision,
        source_plan_fingerprint=metadata.get("content_fingerprint"),
        topic=_required_text(plan.get("topic"), "plan.topic"),
        domain=domain,
        preview_theme=_required_text(preview.get("theme"), "plan.preview.theme"),
        goals=_goal_map(preview),
        teaching_mode=mode,
        core_concept=NarrativeConcept(
            name=_required_text(core.get("name"), "plan.core_concepts[0].name"),
            explanation=_required_text(core.get("explanation"), "plan.core_concepts[0].explanation"),
            applicability=_required_text(core.get("applicability"), "plan.core_concepts[0].applicability"),
        ),
        misconception=NarrativeMisconception(
            mistaken_claim=_required_text(misconception.get("mistaken_claim"), "plan.misconceptions[0].mistaken_claim"),
            correction=_required_text(misconception.get("correction"), "plan.misconceptions[0].correction"),
            diagnostic_cue=_required_text(misconception.get("diagnostic_cue"), "plan.misconceptions[0].diagnostic_cue"),
        ),
        route_tasks=route_tasks,
        boss_tasks=BossTaskContracts(**boss_tasks),
        frame=frame,
        focus_adjustment=focus_adjustment,
        draft_revision=draft_revision,
        adjustment_count=adjustment_count,
    )
