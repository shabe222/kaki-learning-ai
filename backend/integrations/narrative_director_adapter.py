from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agents.narrative_director import (
    NarrativeDirector,
    NarrativeProviderUnconfiguredError,
    NarrativePublicView,
    UnavailableLLM,
    build_narrative_request,
    public_payload,
)


PRIVATE_BLUEPRINT_KEY = "_narrative_blueprint"
PRIVATE_RUNTIME_KEY = "_narrative_runtime"
PUBLIC_VIEW_KEY = "narrative_public"
METADATA_KEY = "narrative_metadata"


@dataclass(frozen=True)
class NarrativeProviderStatus:
    ready: bool
    provider: str
    mode: str
    reason_code: str
    reason: str


def inspect_narrative_provider(settings_obj: Any) -> NarrativeProviderStatus:
    mode = str(getattr(settings_obj, "MODE", "mock") or "mock").strip().lower()
    provider = str(getattr(settings_obj, "LLM_PROVIDER", "mock") or "mock").strip().lower()
    if mode != "production":
        return NarrativeProviderStatus(
            False, provider, mode, "non_production_mode", f"narrative provider disabled in mode={mode or 'unset'}"
        )
    credential_fields = {
        "deepseek": "DEEPSEEK_API_KEY",
        "spark": "XUNFEI_API_PASSWORD",
        "xinghuo": "XUNFEI_API_PASSWORD",
    }
    credential_field = credential_fields.get(provider)
    if credential_field is None:
        return NarrativeProviderStatus(
            False, provider, mode, "unsupported_provider", f"unsupported narrative provider: {provider or 'unset'}"
        )
    credential = str(getattr(settings_obj, credential_field, "") or "").strip()
    if not credential:
        return NarrativeProviderStatus(
            False, provider, mode, "credential_missing", f"credential is missing for narrative provider: {provider}"
        )
    return NarrativeProviderStatus(True, provider, mode, "ready", "narrative provider configured")


async def build_narrative_director(
    *,
    settings_obj: Any | None = None,
    llm_factory: Callable[[], Awaitable[Any]] | None = None,
) -> NarrativeDirector:
    try:
        if settings_obj is None:
            from config import settings as settings_obj
        status = inspect_narrative_provider(settings_obj)
    except Exception as error:
        return NarrativeDirector(UnavailableLLM(f"provider configuration unavailable: {type(error).__name__}"))
    if not status.ready:
        return NarrativeDirector(UnavailableLLM(status.reason))
    if llm_factory is None:
        from services.llm_service_real import get_llm_service
        factory = get_llm_service
    else:
        factory = llm_factory
    try:
        llm = await factory()
    except Exception as error:
        return NarrativeDirector(UnavailableLLM(f"provider client unavailable: {type(error).__name__}"))
    return NarrativeDirector(llm)


async def prepare_narrative_for_plan(
    planner_private_result: dict[str, Any],
    *,
    teaching_mode: str,
    plan_id: str,
    plan_revision: int,
    focus_adjustment: str | None = None,
    director_factory: Callable[[], Awaitable[NarrativeDirector]] | None = None,
) -> dict[str, Any]:
    request = build_narrative_request(
        planner_private_result,
        teaching_mode=teaching_mode,
        plan_id=plan_id,
        plan_revision=plan_revision,
        focus_adjustment=focus_adjustment,
    )
    factory = director_factory or build_narrative_director
    director = await factory()
    result = await director.generate(request)
    return {
        PRIVATE_BLUEPRINT_KEY: result.blueprint.model_dump(mode="json"),
        PRIVATE_RUNTIME_KEY: result.runtime.model_dump(mode="json"),
        PUBLIC_VIEW_KEY: public_payload(result.public_view),
        METADATA_KEY: result.runtime.metadata.model_dump(mode="json"),
    }


def narrative_public_from_slots(slots: dict[str, Any]) -> dict[str, Any] | None:
    """Return only a schema-validated public view; corrupted or over-broad slot data fails closed."""
    value = slots.get(PUBLIC_VIEW_KEY)
    if not isinstance(value, dict):
        return None
    try:
        view = NarrativePublicView.model_validate(value)
        return public_payload(view)
    except Exception:
        return None


def copy_narrative_to_run_slots(plan_slots: dict[str, Any], run_slots: dict[str, Any]) -> None:
    """Freeze the prepared narrative into a run without invoking the model again."""
    for key in (PRIVATE_BLUEPRINT_KEY, PRIVATE_RUNTIME_KEY, PUBLIC_VIEW_KEY, METADATA_KEY):
        value = plan_slots.get(key)
        if value is not None:
            run_slots[key] = value
