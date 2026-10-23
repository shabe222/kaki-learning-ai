from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agents.adventure_planner import AdventureKnowledgePlanner, PlannerRequest, plan_to_existing_content
from agents.adventure_planner.llm_protocol import UnavailableLLM
from services.llm_service_real import get_llm_service


@dataclass(frozen=True)
class PlannerProviderStatus:
    ready: bool
    provider: str
    mode: str
    reason_code: str
    reason: str


def inspect_planner_provider(settings_obj: Any) -> PlannerProviderStatus:
    """Classify provider availability without performing network I/O or exposing credentials."""
    mode = str(getattr(settings_obj, "MODE", "mock") or "mock").strip().lower()
    provider = str(getattr(settings_obj, "LLM_PROVIDER", "mock") or "mock").strip().lower()

    if mode != "production":
        return PlannerProviderStatus(
            False,
            provider,
            mode,
            "non_production_mode",
            f"planner provider disabled in mode={mode or 'unset'}",
        )

    credential_fields = {
        "deepseek": "DEEPSEEK_API_KEY",
        "spark": "XUNFEI_API_PASSWORD",
        "xinghuo": "XUNFEI_API_PASSWORD",
    }
    credential_field = credential_fields.get(provider)
    if credential_field is None:
        return PlannerProviderStatus(
            False,
            provider,
            mode,
            "unsupported_provider",
            f"unsupported planner provider: {provider or 'unset'}",
        )
    credential = str(getattr(settings_obj, credential_field, "") or "").strip()
    if not credential:
        return PlannerProviderStatus(
            False,
            provider,
            mode,
            "credential_missing",
            f"credential is missing for planner provider: {provider}",
        )
    return PlannerProviderStatus(True, provider, mode, "ready", "planner provider configured")


async def build_adventure_planner(
    *,
    settings_obj: Any | None = None,
    llm_factory: Callable[[], Awaitable[Any]] | None = None,
) -> AdventureKnowledgePlanner:
    """Create a request-scoped planner and short-circuit unavailable providers immediately."""
    try:
        if settings_obj is None:
            from config import settings as settings_obj
        status = inspect_planner_provider(settings_obj)
    except Exception as error:
        return AdventureKnowledgePlanner(
            UnavailableLLM(f"provider configuration unavailable: {type(error).__name__}")
        )

    if not status.ready:
        return AdventureKnowledgePlanner(UnavailableLLM(status.reason))

    factory = llm_factory or get_llm_service
    try:
        llm = await factory()
    except Exception as error:
        return AdventureKnowledgePlanner(
            UnavailableLLM(f"provider client unavailable: {type(error).__name__}")
        )
    return AdventureKnowledgePlanner(llm)


async def prepare_adventure_knowledge_plan(payload: dict[str, Any]) -> dict[str, Any]:
    planner = await build_adventure_planner()
    request = PlannerRequest.model_validate(payload)
    result = await planner.generate(request)
    response = {
        "status": result.status,
        "clarification": result.clarification.model_dump(mode="json") if result.clarification else None,
        "preview": result.plan.preview.model_dump(mode="json") if result.plan else None,
        "metadata": result.metadata.model_dump(mode="json"),
    }
    # Keep the full plan server-side. Routers must never return this field to the browser.
    response["_private_result"] = result.model_dump(mode="json")
    return response


def private_result_to_adventure_content(private_result: dict[str, Any]) -> dict[str, Any]:
    from agents.adventure_planner.models import PlannerResult

    result = PlannerResult.model_validate(private_result)
    return plan_to_existing_content(result)
