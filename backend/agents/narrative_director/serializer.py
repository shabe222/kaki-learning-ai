from __future__ import annotations

from .models import (
    NarrativeBlueprint,
    NarrativePublicView,
    NarrativeRuntimePackage,
    PublicOpposition,
    PublicRoutes,
    PublicStateCopy,
    PublicWorld,
)


PUBLIC_TOP_LEVEL_KEYS = {
    "schema_version",
    "frame_id",
    "world",
    "recommended_destinations",
    "routes",
    "opposition",
    "state_copy",
    "continuity_keys",
}


def build_public_view(
    runtime: NarrativeRuntimePackage,
    blueprint: NarrativeBlueprint,
) -> NarrativePublicView:
    bindings = runtime.bindings
    return NarrativePublicView(
        frame_id=runtime.frame.frame_id,
        world=PublicWorld(
            scene_id=runtime.frame.primary_scene_id,
            scene_name=runtime.frame.primary_scene_name,
            world_rule=bindings.scroll.world_rule,
            anomaly=bindings.goal.visible_change,
            local_stakes=bindings.goal.local_stakes,
        ),
        recommended_destinations=runtime.recommended_destinations,
        routes=PublicRoutes(
            understanding=bindings.method.understanding,
            practice=bindings.method.practice,
        ),
        opposition=PublicOpposition(
            form=blueprint.opposition_embodiment.form,
            appearance=blueprint.opposition_embodiment.appearance,
            behaviour=blueprint.opposition_embodiment.behaviour,
            mistaken_claim=runtime.misconception_claim,
            transformation=blueprint.opposition_embodiment.transformation,
        ),
        state_copy=PublicStateCopy(
            opening=bindings.goal.opening,
            rule_reveal=bindings.scroll.rule_reveal,
            first_trial=bindings.trial.first_trial,
            supply_success=bindings.supply.success_respite,
            supply_remediation=bindings.supply.remediation_reframe,
            interleave=bindings.interleave.interleave_turn,
            boss_reveal=bindings.boss.reveal,
            resolution=bindings.treasury.resolution,
        ),
        continuity_keys=blueprint.continuity_keys,
    )


def public_payload(view: NarrativePublicView) -> dict:
    payload = view.model_dump(mode="json")
    if set(payload) != PUBLIC_TOP_LEVEL_KEYS:
        raise AssertionError("public narrative payload whitelist changed unexpectedly")
    return payload
