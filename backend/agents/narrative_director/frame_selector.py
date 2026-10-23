from __future__ import annotations

import hashlib
import json

from .enums import CompanionRole, InteractionKind, NarrativeGrammar, TeachingMode
from .models import CompanionPolicy, NarrativeFrame, RouteTaskContracts
from .scene_catalog import GRAMMAR_OPPOSITION_FORMS, GRAMMAR_SCENES, scene


_MODE_BASE_SCORES: dict[TeachingMode, dict[NarrativeGrammar, int]] = {
    TeachingMode.CONCEPT_BUILDING: {
        NarrativeGrammar.DISCOVER_RULE: 60,
        NarrativeGrammar.REPAIR_MECHANISM: 30,
    },
    TeachingMode.MISCONCEPTION_DISTINCTION: {
        NarrativeGrammar.EXPOSE_ILLUSION: 60,
        NarrativeGrammar.COMPLETE_MISSING_LINK: 40,
    },
    TeachingMode.TRANSFER_CHALLENGE: {
        NarrativeGrammar.TRANSFER_ACROSS_BORDER: 60,
        NarrativeGrammar.DISCOVER_RULE: 25,
        NarrativeGrammar.REPAIR_MECHANISM: 20,
    },
    TeachingMode.SCAFFOLDED_PRACTICE: {
        NarrativeGrammar.REPAIR_MECHANISM: 55,
        NarrativeGrammar.COMPLETE_MISSING_LINK: 45,
        NarrativeGrammar.RECONSTRUCT_CHAIN: 35,
    },
    TeachingMode.MECHANISM_LINKING: {
        NarrativeGrammar.REPAIR_MECHANISM: 55,
        NarrativeGrammar.DISCOVER_RULE: 45,
        NarrativeGrammar.RECONSTRUCT_CHAIN: 35,
    },
}


_TASK_SCORES: dict[InteractionKind, dict[NarrativeGrammar, int]] = {
    InteractionKind.EXPLAIN: {NarrativeGrammar.DISCOVER_RULE: 8},
    InteractionKind.ORDERING: {NarrativeGrammar.RECONSTRUCT_CHAIN: 16, NarrativeGrammar.REPAIR_MECHANISM: 4},
    InteractionKind.STEP_COMPLETION: {NarrativeGrammar.COMPLETE_MISSING_LINK: 14, NarrativeGrammar.REPAIR_MECHANISM: 8},
    InteractionKind.COMPARE: {NarrativeGrammar.EXPOSE_ILLUSION: 16, NarrativeGrammar.DISCOVER_RULE: 4},
    InteractionKind.FIND_ERROR: {NarrativeGrammar.EXPOSE_ILLUSION: 14, NarrativeGrammar.COMPLETE_MISSING_LINK: 10},
    InteractionKind.COUNTEREXAMPLE: {NarrativeGrammar.EXPOSE_ILLUSION: 18},
    InteractionKind.APPLICATION: {NarrativeGrammar.TRANSFER_ACROSS_BORDER: 16, NarrativeGrammar.REPAIR_MECHANISM: 3},
    InteractionKind.CODE_TASK: {NarrativeGrammar.REPAIR_MECHANISM: 18, NarrativeGrammar.RECONSTRUCT_CHAIN: 10},
    InteractionKind.REWRITE: {NarrativeGrammar.RECONSTRUCT_CHAIN: 14, NarrativeGrammar.COMPLETE_MISSING_LINK: 4},
    InteractionKind.OPEN_RESPONSE: {NarrativeGrammar.DISCOVER_RULE: 6},
    InteractionKind.MULTIPLE_CHOICE: {},
}


_TIE_BREAK = [
    NarrativeGrammar.DISCOVER_RULE,
    NarrativeGrammar.REPAIR_MECHANISM,
    NarrativeGrammar.EXPOSE_ILLUSION,
    NarrativeGrammar.COMPLETE_MISSING_LINK,
    NarrativeGrammar.RECONSTRUCT_CHAIN,
    NarrativeGrammar.TRANSFER_ACROSS_BORDER,
]


_COMPANION_POLICIES: dict[TeachingMode, CompanionPolicy] = {
    TeachingMode.CONCEPT_BUILDING: CompanionPolicy(
        opening_role=CompanionRole.CO_OBSERVER,
        trial_role=CompanionRole.ONE_STEP_DEMONSTRATOR,
        failure_role=CompanionRole.ONE_STEP_DEMONSTRATOR,
        reflection_role=CompanionRole.REFLECTION_PARTNER,
    ),
    TeachingMode.MISCONCEPTION_DISTINCTION: CompanionPolicy(
        opening_role=CompanionRole.CO_OBSERVER,
        trial_role=CompanionRole.CONTRAST_CUE_GIVER,
        failure_role=CompanionRole.CONTRAST_CUE_GIVER,
        reflection_role=CompanionRole.REFLECTION_PARTNER,
    ),
    TeachingMode.TRANSFER_CHALLENGE: CompanionPolicy(
        opening_role=CompanionRole.QUIET_COMPANION,
        trial_role=CompanionRole.QUIET_COMPANION,
        failure_role=CompanionRole.FAILURE_ONLY_GUIDE,
        reflection_role=CompanionRole.REFLECTION_PARTNER,
    ),
    TeachingMode.SCAFFOLDED_PRACTICE: CompanionPolicy(
        opening_role=CompanionRole.CO_OBSERVER,
        trial_role=CompanionRole.ONE_STEP_DEMONSTRATOR,
        failure_role=CompanionRole.FAILURE_ONLY_GUIDE,
        reflection_role=CompanionRole.REFLECTION_PARTNER,
    ),
    TeachingMode.MECHANISM_LINKING: CompanionPolicy(
        opening_role=CompanionRole.CO_OBSERVER,
        trial_role=CompanionRole.CONTRAST_CUE_GIVER,
        failure_role=CompanionRole.FAILURE_ONLY_GUIDE,
        reflection_role=CompanionRole.REFLECTION_PARTNER,
    ),
}


def _task_kinds(tasks: RouteTaskContracts) -> list[InteractionKind]:
    return [
        tasks.understanding_trial.interaction_kind,
        tasks.practice_trial.interaction_kind,
        tasks.remedy.interaction_kind,
        tasks.understanding_interleave.interaction_kind,
        tasks.practice_interleave.interaction_kind,
    ]


def select_narrative_frame(teaching_mode: TeachingMode, tasks: RouteTaskContracts) -> NarrativeFrame:
    scores = {grammar: -10_000 for grammar in NarrativeGrammar}
    trace: list[str] = [f"teaching_mode={teaching_mode.value}"]

    for grammar, score in _MODE_BASE_SCORES[teaching_mode].items():
        scores[grammar] = score
        trace.append(f"mode_base:{grammar.value}+{score}")

    kinds = _task_kinds(tasks)
    has_application = InteractionKind.APPLICATION in kinds
    for kind in kinds:
        for grammar, score in _TASK_SCORES[kind].items():
            if scores[grammar] <= -10_000:
                continue
            scores[grammar] += score
            trace.append(f"task:{kind.value}:{grammar.value}+{score}")

    if teaching_mode == TeachingMode.TRANSFER_CHALLENGE and not has_application:
        scores[NarrativeGrammar.TRANSFER_ACROSS_BORDER] = -10_000
        trace.append("transfer_gate:disabled_without_application")

    ranked = sorted(
        (grammar for grammar in NarrativeGrammar if scores[grammar] > -10_000),
        key=lambda grammar: (-scores[grammar], _TIE_BREAK.index(grammar)),
    )
    if not ranked:
        ranked = [NarrativeGrammar.DISCOVER_RULE]
        trace.append("emergency_default=DISCOVER_RULE")

    grammar = ranked[0]
    primary_id, alternate_id = GRAMMAR_SCENES[grammar]
    primary = scene(primary_id)
    alternate = scene(alternate_id)
    allowed_objects: list[str] = []
    for value in [*primary.interactive_objects, *alternate.interactive_objects]:
        if value not in allowed_objects:
            allowed_objects.append(value)

    raw = {
        "mode": teaching_mode.value,
        "grammar": grammar.value,
        "primary": primary_id,
        "alternate": alternate_id,
        "tasks": [kind.value for kind in kinds],
    }
    frame_id = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    trace.append(f"selected={grammar.value}:{scores[grammar]}")
    trace.append(f"scene={primary_id}/{alternate_id}")

    return NarrativeFrame(
        frame_id=frame_id,
        grammar=grammar,
        primary_scene_id=primary.scene_id,
        primary_scene_name=primary.display_name,
        alternate_scene_id=alternate.scene_id,
        alternate_scene_name=alternate.display_name,
        allowed_objects=allowed_objects[:16],
        allowed_opposition_forms=GRAMMAR_OPPOSITION_FORMS[grammar],
        companion_policy=_COMPANION_POLICIES[teaching_mode],
        decision_trace=trace[:16],
    )
