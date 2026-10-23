from __future__ import annotations

import re
from typing import Iterable

from .models import (
    BlueprintEnvelope,
    BlueprintTask,
    ClarificationReason,
    Domain,
    InteractionKind,
    LearningBlueprint,
    PlannerRequest,
    QuestionKind,
)
from .validators import FORBIDDEN_PREVIEW_TERMS


def _normalise(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _iter_tasks(blueprint: LearningBlueprint) -> Iterable[tuple[str, BlueprintTask]]:
    tasks = blueprint.tasks
    yield "understanding_trial", tasks.understanding_trial
    yield "distinction_trial", tasks.distinction_trial
    yield "remedy", tasks.remedy
    yield "transfer_interleave", tasks.transfer_interleave
    yield "boss_understand", tasks.boss_understand
    yield "boss_distinguish", tasks.boss_distinguish
    yield "boss_transfer", tasks.boss_transfer


def validate_blueprint(envelope: BlueprintEnvelope, request: PlannerRequest) -> list[str]:
    errors: list[str] = []
    unresolved_conflicts = [item for item in request.source_conflicts if not item.resolved]

    if envelope.status == "needs_clarification":
        if request.clarification_count >= 1:
            errors.append("clarification_count is already 1; a blueprint must be produced")
        clarification = envelope.clarification
        if clarification and len({_normalise(item) for item in clarification.options}) != 2:
            errors.append("clarification options must be distinct")
        if clarification and clarification.reason == ClarificationReason.SOURCE_CONFLICT and not unresolved_conflicts:
            errors.append("source_conflict clarification requires an unresolved source conflict")
        return errors

    blueprint = envelope.blueprint
    if blueprint is None:
        return ["ready result has no blueprint"]
    if unresolved_conflicts:
        errors.append("unresolved source conflict requires one clarification before producing a blueprint")

    goal_texts = [
        blueprint.goals.understand,
        blueprint.goals.distinguish,
        blueprint.goals.transfer,
    ]
    if len({_normalise(item) for item in goal_texts}) != 3:
        errors.append("blueprint goals must be distinct")
    for text in [blueprint.preview_theme, *goal_texts]:
        for term in FORBIDDEN_PREVIEW_TERMS:
            if term.lower() in text.lower():
                errors.append(f"blueprint preview contains backstage term: {term}")

    real_source_ids = {source.source_id for source in request.selected_sources}
    invented = [source_id for source_id in blueprint.source_grounding if source_id not in real_source_ids]
    if invented:
        errors.append(f"blueprint source_grounding contains unknown ids: {invented}")

    signatures: dict[str, int] = {}
    interactions: set[InteractionKind] = set()
    for name, task in _iter_tasks(blueprint):
        signature = _normalise(task.question)
        signatures[signature] = signatures.get(signature, 0) + 1
        interactions.add(task.interaction_kind)
        if task.kind == QuestionKind.MULTIPLE_CHOICE:
            options = [_normalise(item) for item in task.options or []]
            if len(options) != len(set(options)):
                errors.append(f"blueprint task {name} has duplicated options")
            if task.options and not task.options[task.correct_option or 0].strip():
                errors.append(f"blueprint task {name} has an empty correct option")
        else:
            for group in task.accepted_groups or []:
                if not 1 <= len(group) <= 6:
                    errors.append(f"blueprint task {name} accepted group must contain 1-6 terms")
                if any(not str(term).strip() for term in group):
                    errors.append(f"blueprint task {name} accepted group contains empty term")

    if any(count > 1 for count in signatures.values()):
        errors.append("blueprint task questions must not be exact duplicates")
    if len(interactions) < 2:
        errors.append("blueprint must use at least two different interaction forms")

    if blueprint.domain == Domain.MATH:
        expected = {
            InteractionKind.EXPLAIN,
            InteractionKind.STEP_COMPLETION,
            InteractionKind.COMPARE,
            InteractionKind.COUNTEREXAMPLE,
            InteractionKind.APPLICATION,
        }
        if not interactions.intersection(expected):
            errors.append("math blueprint requires explanation, comparison, step completion, counterexample or application")
    elif blueprint.domain == Domain.PROGRAMMING:
        if not interactions.intersection({InteractionKind.CODE_TASK, InteractionKind.FIND_ERROR}):
            errors.append("programming blueprint requires at least one code_task or find_error interaction")
    elif blueprint.domain == Domain.LANGUAGE:
        if not interactions.intersection(
            {InteractionKind.REWRITE, InteractionKind.COMPARE, InteractionKind.OPEN_RESPONSE}
        ):
            errors.append("language blueprint requires rewrite, compare or open_response interaction")
    elif blueprint.domain == Domain.OPEN:
        if InteractionKind.OPEN_RESPONSE not in interactions:
            errors.append("open blueprint requires at least one open_response interaction")

    return errors
