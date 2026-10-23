from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agents.learner_state import LearnerStateAnalyzer, LearnerStateRequest
from agents.learner_state.llm_protocol import UnavailableLLM


@dataclass(frozen=True)
class LearnerStateProviderStatus:
    ready: bool
    provider: str
    mode: str
    configured_model_id: str | None
    reason_code: str
    reason: str


def inspect_learner_state_provider(settings_obj: Any) -> LearnerStateProviderStatus:
    mode = str(getattr(settings_obj, "MODE", "mock") or "mock").strip().lower()
    provider = str(getattr(settings_obj, "LLM_PROVIDER", "mock") or "mock").strip().lower()
    configured_model_id = str(getattr(settings_obj, "MODEL", "") or "").strip() or None
    if mode != "production":
        return LearnerStateProviderStatus(
            False, provider, mode, configured_model_id,
            "non_production_mode", f"learner-state provider disabled in mode={mode or 'unset'}",
        )
    credential_fields = {
        "deepseek": "DEEPSEEK_API_KEY",
        "spark": "XUNFEI_API_PASSWORD",
        "xinghuo": "XUNFEI_API_PASSWORD",
    }
    credential_field = credential_fields.get(provider)
    if credential_field is None:
        return LearnerStateProviderStatus(
            False, provider, mode, configured_model_id,
            "unsupported_provider", f"unsupported learner-state provider: {provider or 'unset'}",
        )
    credential = str(getattr(settings_obj, credential_field, "") or "").strip()
    if not credential:
        return LearnerStateProviderStatus(
            False, provider, mode, configured_model_id,
            "credential_missing", f"credential is missing for learner-state provider: {provider}",
        )
    return LearnerStateProviderStatus(
        True, provider, mode, configured_model_id, "ready", "learner-state provider configured",
    )


async def build_learner_state_analyzer(
    *,
    settings_obj: Any | None = None,
    llm_factory: Callable[[], Awaitable[Any]] | None = None,
) -> LearnerStateAnalyzer:
    try:
        if settings_obj is None:
            from config import settings as settings_obj
        status = inspect_learner_state_provider(settings_obj)
    except Exception as error:
        return LearnerStateAnalyzer(
            UnavailableLLM(f"provider configuration unavailable: {type(error).__name__}")
        )
    if not status.ready:
        return LearnerStateAnalyzer(
            UnavailableLLM(status.reason), configured_model_id=status.configured_model_id
        )
    if llm_factory is None:
        from services.llm_service_real import get_llm_service
        factory = get_llm_service
    else:
        factory = llm_factory
    try:
        llm = await factory()
    except Exception as error:
        return LearnerStateAnalyzer(
            UnavailableLLM(f"provider client unavailable: {type(error).__name__}"),
            configured_model_id=status.configured_model_id,
        )
    return LearnerStateAnalyzer(llm, configured_model_id=status.configured_model_id)


async def analyze_learner_state(
    payload: dict[str, Any],
    *,
    settings_obj: Any | None = None,
    llm_factory: Callable[[], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    request = LearnerStateRequest.model_validate(payload)
    analyzer = await build_learner_state_analyzer(settings_obj=settings_obj, llm_factory=llm_factory)
    return (await analyzer.analyze(request)).model_dump(mode="json")
