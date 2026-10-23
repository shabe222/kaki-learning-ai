from __future__ import annotations

import hashlib
import json

from .enums import AdventureState, InteractionKind, NarrativeGrammar, TaskRole
from .models import (
    BossBinding,
    BossStageBinding,
    GoalBinding,
    InterleaveBinding,
    MethodBinding,
    NarrativeBlueprint,
    NarrativeRequest,
    NarrativeRuntimePackage,
    NarrativeStateBindings,
    PublicDestination,
    RuntimeMetadata,
    ScrollBinding,
    SupplyBinding,
    TaskReference,
    TreasuryBinding,
    TrialBinding,
    ValidationIssue,
)
from .scene_catalog import SCENES, scene


STATE_ORDER = [
    AdventureState.GOAL,
    AdventureState.SCROLL,
    AdventureState.METHOD,
    AdventureState.TRIAL,
    AdventureState.SUPPLY,
    AdventureState.INTERLEAVE,
    AdventureState.BOSS,
    AdventureState.TREASURY,
]


def _task_reference(path: str, task) -> TaskReference:
    return TaskReference(
        path=path,
        task_role=task.task_role,
        interaction_kind=task.interaction_kind,
        public_prompt=task.public_prompt,
    )


def _destination_reason(grammar: NarrativeGrammar, scene_id: str) -> str:
    primary_reason = {
        NarrativeGrammar.DISCOVER_RULE: "适合从线索、解释与对照中发现当前知识规律",
        NarrativeGrammar.REPAIR_MECHANISM: "适合通过操作、步骤和结果反馈验证当前机制",
        NarrativeGrammar.EXPOSE_ILLUSION: "适合并排辨析相似说法，揭开当前误区",
        NarrativeGrammar.COMPLETE_MISSING_LINK: "适合寻找缺失条件，补全证据和推理缺口",
        NarrativeGrammar.RECONSTRUCT_CHAIN: "适合重建顺序、过程与因果链条",
        NarrativeGrammar.TRANSFER_ACROSS_BORDER: "适合把已确认的规则带入新的情境",
    }[grammar]
    return primary_reason if scene_id != "camp" else "用于确认目标并重新选择路线"


def _third_destination(request: NarrativeRequest, selected: set[str]) -> str | None:
    mode_priority = {
        "concept_building": ["mechanism_workshop", "echo_archive"],
        "misconception_distinction": ["echo_archive", "mechanism_workshop"],
        "transfer_challenge": ["mechanism_workshop", "echo_archive"],
        "scaffolded_practice": ["echo_archive", "mirror_lake"],
        "mechanism_linking": ["echo_archive", "mirror_lake"],
    }[request.teaching_mode.value]
    kinds = {
        request.route_tasks.understanding_trial.interaction_kind,
        request.route_tasks.practice_trial.interaction_kind,
        request.route_tasks.understanding_interleave.interaction_kind,
        request.route_tasks.practice_interleave.interaction_kind,
    }
    task_priority: list[str] = []
    if request.teaching_mode.value == "transfer_challenge" and InteractionKind.APPLICATION in kinds:
        task_priority.append("transfer_bridge")
    if kinds & {InteractionKind.COMPARE, InteractionKind.COUNTEREXAMPLE, InteractionKind.FIND_ERROR}:
        task_priority.append("mirror_lake")
    if kinds & {InteractionKind.CODE_TASK, InteractionKind.ORDERING, InteractionKind.STEP_COMPLETION}:
        task_priority.append("mechanism_workshop")
    for candidate in [*mode_priority, *task_priority, "echo_archive", "mist_valley"]:
        if candidate not in selected:
            return candidate
    return None


def _destinations(request: NarrativeRequest) -> list[PublicDestination]:
    frame = request.frame
    ids = [frame.primary_scene_id, frame.alternate_scene_id]
    third = _third_destination(request, set(ids))
    if third:
        ids.append(third)
    result: list[PublicDestination] = []
    for scene_id in ids[:3]:
        definition = scene(scene_id)
        result.append(PublicDestination(
            scene_id=scene_id,
            display_name=definition.display_name,
            reason=_destination_reason(frame.grammar, scene_id),
        ))
    return result


def _boss_intents(blueprint: NarrativeBlueprint) -> list[str]:
    key = blueprint.continuity_keys[0]
    return [
        f"第一层封印要求玩家解释规则，{key}据此显露稳定结构。",
        f"第二层封印把错误主张与正确条件并排呈现，要求玩家完成辨析。",
        f"第三层封印改变情境外观，要求玩家带着规则完成迁移。",
    ]


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compile_runtime(
    request: NarrativeRequest,
    blueprint: NarrativeBlueprint,
    *,
    generation_mode: str,
    model: str | None,
    tokens_used: int,
    raw_attempts: int,
    warnings: list[str],
    diagnostics: list[str],
    prompt_hash: str,
    schema_hash: str,
    validation_issues: list[ValidationIssue],
) -> NarrativeRuntimePackage:
    frame = request.frame
    boss_intents = _boss_intents(blueprint)
    boss_tasks = [
        ("understand", "解释", request.boss_tasks.understand, "boss.understand", boss_intents[0]),
        ("distinguish", "辨析", request.boss_tasks.distinguish, "boss.distinguish", boss_intents[1]),
        ("transfer", "迁移", request.boss_tasks.transfer, "boss.transfer", boss_intents[2]),
    ]

    bindings = NarrativeStateBindings(
        goal=GoalBinding(
            opening=blueprint.beats.opening,
            visible_change=blueprint.world_anomaly.visible_change,
            local_stakes=blueprint.world_anomaly.local_stakes,
        ),
        scroll=ScrollBinding(
            scene_id=frame.primary_scene_id,
            scene_name=frame.primary_scene_name,
            world_rule=blueprint.world_metaphor.world_rule,
            rule_reveal=blueprint.beats.rule_reveal,
        ),
        method=MethodBinding(
            understanding=blueprint.route_frames.understanding,
            practice=blueprint.route_frames.practice,
            companion_policy=frame.companion_policy,
        ),
        trial=TrialBinding(
            first_trial=blueprint.beats.first_trial,
            understanding_task=_task_reference(
                "understanding.trial", request.route_tasks.understanding_trial
            ),
            practice_task=_task_reference(
                "practice.trial", request.route_tasks.practice_trial
            ),
        ),
        supply=SupplyBinding(
            success_respite=blueprint.beats.supply.success_respite,
            remediation_reframe=blueprint.beats.supply.remediation_reframe,
            remedy_task=_task_reference("remedy", request.route_tasks.remedy),
            failure_companion_role=frame.companion_policy.failure_role,
        ),
        interleave=InterleaveBinding(
            interleave_turn=blueprint.beats.interleave_turn,
            understanding_task=_task_reference(
                "understanding.interleave", request.route_tasks.understanding_interleave
            ),
            practice_task=_task_reference(
                "practice.interleave", request.route_tasks.practice_interleave
            ),
        ),
        boss=BossBinding(
            reveal=blueprint.beats.boss_reveal,
            stages=[
                BossStageBinding(
                    stage=stage,
                    label=label,
                    task=_task_reference(path, task),
                    narrative_intent=intent,
                )
                for stage, label, task, path, intent in boss_tasks
            ],
        ),
        treasury=TreasuryBinding(
            resolution=blueprint.beats.resolution,
            opposition_transformation=blueprint.opposition_embodiment.transformation,
        ),
    )

    mode = generation_mode if generation_mode in {"model", "repair", "fallback"} else "fallback"
    return NarrativeRuntimePackage(
        plan_id=request.plan_id,
        plan_revision=request.plan_revision,
        source_plan_fingerprint=request.source_plan_fingerprint,
        blueprint_hash=_canonical_hash(blueprint.model_dump(mode="json")),
        frame=frame,
        state_order=STATE_ORDER,
        recommended_destinations=_destinations(request),
        bindings=bindings,
        misconception_claim=request.misconception.mistaken_claim,
        validation_issues=validation_issues,
        metadata=RuntimeMetadata(
            generation_mode=mode,
            model=model,
            tokens_used=tokens_used,
            raw_attempts=raw_attempts,
            warnings=list(dict.fromkeys(warnings))[:20],
            diagnostics=list(dict.fromkeys(diagnostics))[:20],
            prompt_hash=prompt_hash,
            schema_hash=schema_hash,
        ),
    )
