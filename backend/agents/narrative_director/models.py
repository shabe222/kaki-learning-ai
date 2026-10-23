from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    AdventureState,
    CompanionRole,
    Domain,
    InteractionKind,
    NarrativeGrammar,
    OppositionForm,
    Severity,
    TaskRole,
    TeachingMode,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NarrativeGoals(StrictModel):
    understand: str = Field(min_length=4, max_length=80)
    distinguish: str = Field(min_length=4, max_length=80)
    transfer: str = Field(min_length=4, max_length=80)


class NarrativeConcept(StrictModel):
    name: str = Field(min_length=1, max_length=60)
    explanation: str = Field(min_length=8, max_length=300)
    applicability: str = Field(min_length=4, max_length=180)


class NarrativeMisconception(StrictModel):
    mistaken_claim: str = Field(min_length=4, max_length=180)
    correction: str = Field(min_length=8, max_length=260)
    diagnostic_cue: str = Field(min_length=4, max_length=180)


class PublicTaskContract(StrictModel):
    task_role: TaskRole
    interaction_kind: InteractionKind
    public_prompt: str = Field(min_length=6, max_length=300)


class RouteTaskContracts(StrictModel):
    understanding_trial: PublicTaskContract
    practice_trial: PublicTaskContract
    remedy: PublicTaskContract
    understanding_interleave: PublicTaskContract
    practice_interleave: PublicTaskContract


class BossTaskContracts(StrictModel):
    understand: PublicTaskContract
    distinguish: PublicTaskContract
    transfer: PublicTaskContract


class SceneDefinition(StrictModel):
    scene_id: str = Field(min_length=2, max_length=40)
    display_name: str = Field(min_length=2, max_length=40)
    learning_affordances: list[str] = Field(min_length=1, max_length=12)
    interactive_objects: list[str] = Field(min_length=1, max_length=12)
    supports_anomaly: bool = True
    supports_restoration: bool = True


class CompanionPolicy(StrictModel):
    opening_role: CompanionRole
    trial_role: CompanionRole
    failure_role: CompanionRole
    reflection_role: CompanionRole


class NarrativeFrame(StrictModel):
    frame_version: Literal["0.2"] = "0.2"
    frame_id: str = Field(min_length=16, max_length=64)
    grammar: NarrativeGrammar
    primary_scene_id: str = Field(min_length=2, max_length=40)
    primary_scene_name: str = Field(min_length=2, max_length=40)
    alternate_scene_id: str = Field(min_length=2, max_length=40)
    alternate_scene_name: str = Field(min_length=2, max_length=40)
    allowed_objects: list[str] = Field(min_length=2, max_length=16)
    allowed_opposition_forms: list[OppositionForm] = Field(min_length=1, max_length=3)
    companion_policy: CompanionPolicy
    decision_trace: list[str] = Field(min_length=1, max_length=16)


class NarrativeRequest(StrictModel):
    request_version: Literal["0.2"] = "0.2"
    request_id: str = Field(min_length=8, max_length=100)
    plan_id: str = Field(min_length=6, max_length=100)
    plan_revision: int = Field(ge=1, le=99)
    source_plan_fingerprint: str | None = Field(default=None, max_length=100)

    topic: str = Field(min_length=2, max_length=120)
    domain: Domain
    preview_theme: str = Field(min_length=2, max_length=64)
    goals: NarrativeGoals
    teaching_mode: TeachingMode
    core_concept: NarrativeConcept
    misconception: NarrativeMisconception
    route_tasks: RouteTaskContracts
    boss_tasks: BossTaskContracts
    frame: NarrativeFrame

    focus_adjustment: str | None = Field(default=None, max_length=500)
    draft_revision: int = Field(ge=1, le=3)
    adjustment_count: int = Field(ge=0, le=2)
    locale: str = Field(default="zh-CN", max_length=12)

    @model_validator(mode="after")
    def validate_revision(self):
        if self.draft_revision != self.adjustment_count + 1:
            raise ValueError("draft_revision must equal adjustment_count + 1")
        return self


# --------------------------- model-owned compact blueprint ---------------------------


class WorldMetaphor(StrictModel):
    world_rule: str = Field(min_length=12, max_length=140)
    alignment_note: str = Field(min_length=12, max_length=200)


class WorldAnomaly(StrictModel):
    affected_object: str = Field(min_length=1, max_length=24)
    visible_change: str = Field(min_length=12, max_length=160)
    local_stakes: str = Field(min_length=8, max_length=120)


class RouteFrame(StrictModel):
    action_frame: str = Field(min_length=10, max_length=140)
    success_response: str = Field(min_length=8, max_length=120)
    failure_signal: str = Field(min_length=8, max_length=120)


class RouteFrames(StrictModel):
    understanding: RouteFrame
    practice: RouteFrame


class OppositionEmbodiment(StrictModel):
    form: OppositionForm
    appearance: str = Field(min_length=8, max_length=120)
    behaviour: str = Field(min_length=8, max_length=140)
    transformation: str = Field(min_length=8, max_length=120)


class SupplyBeats(StrictModel):
    success_respite: str = Field(min_length=8, max_length=120)
    remediation_reframe: str = Field(min_length=8, max_length=140)


class NarrativeBeats(StrictModel):
    opening: str = Field(min_length=10, max_length=140)
    rule_reveal: str = Field(min_length=10, max_length=140)
    first_trial: str = Field(min_length=10, max_length=140)
    supply: SupplyBeats
    interleave_turn: str = Field(min_length=10, max_length=140)
    boss_reveal: str = Field(min_length=10, max_length=140)
    resolution: str = Field(min_length=10, max_length=140)


class NarrativeBlueprint(StrictModel):
    blueprint_version: Literal["0.2"] = "0.2"
    world_metaphor: WorldMetaphor
    world_anomaly: WorldAnomaly
    route_frames: RouteFrames
    opposition_embodiment: OppositionEmbodiment
    beats: NarrativeBeats
    continuity_keys: list[str] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_keys(self):
        cleaned: list[str] = []
        for value in self.continuity_keys:
            value = value.strip()
            if not 2 <= len(value) <= 16:
                raise ValueError("continuity key length must be 2..16")
            if value not in cleaned:
                cleaned.append(value)
        if not cleaned:
            raise ValueError("at least one continuity key is required")
        self.continuity_keys = cleaned
        return self


# --------------------------- validation / runtime ---------------------------


class ValidationIssue(StrictModel):
    code: str = Field(min_length=3, max_length=80)
    severity: Severity
    path: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=3, max_length=500)
    repair_mode: Literal["none", "structural", "clean"] = "none"


class PublicDestination(StrictModel):
    scene_id: str
    display_name: str
    reason: str = Field(min_length=4, max_length=120)


class TaskReference(StrictModel):
    path: str = Field(min_length=3, max_length=80)
    task_role: TaskRole
    interaction_kind: InteractionKind
    public_prompt: str = Field(min_length=6, max_length=300)


class GoalBinding(StrictModel):
    opening: str
    visible_change: str
    local_stakes: str


class ScrollBinding(StrictModel):
    scene_id: str
    scene_name: str
    world_rule: str
    rule_reveal: str


class MethodBinding(StrictModel):
    understanding: RouteFrame
    practice: RouteFrame
    companion_policy: CompanionPolicy


class TrialBinding(StrictModel):
    first_trial: str
    understanding_task: TaskReference
    practice_task: TaskReference


class SupplyBinding(StrictModel):
    success_respite: str
    remediation_reframe: str
    remedy_task: TaskReference
    failure_companion_role: CompanionRole


class InterleaveBinding(StrictModel):
    interleave_turn: str
    understanding_task: TaskReference
    practice_task: TaskReference


class BossStageBinding(StrictModel):
    stage: Literal["understand", "distinguish", "transfer"]
    label: Literal["解释", "辨析", "迁移"]
    task: TaskReference
    narrative_intent: str


class BossBinding(StrictModel):
    scene_id: Literal["boss_core"] = "boss_core"
    scene_name: Literal["核心封印场"] = "核心封印场"
    reveal: str
    stages: list[BossStageBinding] = Field(min_length=3, max_length=3)


class TreasuryBinding(StrictModel):
    scene_id: Literal["harvest_treasury"] = "harvest_treasury"
    scene_name: Literal["丰穗宝库与烟火高台"] = "丰穗宝库与烟火高台"
    resolution: str
    opposition_transformation: str


class NarrativeStateBindings(StrictModel):
    goal: GoalBinding
    scroll: ScrollBinding
    method: MethodBinding
    trial: TrialBinding
    supply: SupplyBinding
    interleave: InterleaveBinding
    boss: BossBinding
    treasury: TreasuryBinding


class RuntimeMetadata(StrictModel):
    generation_mode: Literal["model", "repair", "fallback"]
    model: str | None = Field(default=None, max_length=100)
    tokens_used: int = Field(default=0, ge=0)
    raw_attempts: int = Field(default=0, ge=0, le=2)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    diagnostics: list[str] = Field(default_factory=list, max_length=20)
    prompt_hash: str = Field(min_length=16, max_length=64)
    schema_hash: str = Field(min_length=16, max_length=64)


class NarrativeRuntimePackage(StrictModel):
    package_version: Literal["0.2"] = "0.2"
    plan_id: str
    plan_revision: int
    source_plan_fingerprint: str | None = None
    blueprint_hash: str = Field(min_length=16, max_length=64)
    compiler_version: Literal["0.2.0"] = "0.2.0"
    frame: NarrativeFrame
    state_order: list[AdventureState] = Field(min_length=8, max_length=8)
    recommended_destinations: list[PublicDestination] = Field(min_length=2, max_length=3)
    bindings: NarrativeStateBindings
    misconception_claim: str
    validation_issues: list[ValidationIssue] = Field(default_factory=list, max_length=30)
    metadata: RuntimeMetadata


class PublicWorld(StrictModel):
    scene_id: str
    scene_name: str
    world_rule: str
    anomaly: str
    local_stakes: str


class PublicRoutes(StrictModel):
    understanding: RouteFrame
    practice: RouteFrame


class PublicOpposition(StrictModel):
    form: OppositionForm
    appearance: str
    behaviour: str
    mistaken_claim: str
    transformation: str


class PublicStateCopy(StrictModel):
    opening: str
    rule_reveal: str
    first_trial: str
    supply_success: str
    supply_remediation: str
    interleave: str
    boss_reveal: str
    resolution: str


class NarrativePublicView(StrictModel):
    schema_version: Literal["0.2"] = "0.2"
    frame_id: str
    world: PublicWorld
    recommended_destinations: list[PublicDestination] = Field(min_length=2, max_length=3)
    routes: PublicRoutes
    opposition: PublicOpposition
    state_copy: PublicStateCopy
    continuity_keys: list[str] = Field(min_length=1, max_length=3)


class DirectorResult(StrictModel):
    status: Literal["ready"] = "ready"
    blueprint: NarrativeBlueprint
    runtime: NarrativeRuntimePackage
    public_view: NarrativePublicView
