"""Decide whether this turn needs the web, and go and get it -- early.

The search used to run *after* the turn's productions were built. That was
invisible for a chat answer, because the answer is written last and could see
the result. It was wrong for anything produced inline: the blackboard is drawn
while the turn is still being planned, so a board about next year's exam dates
could not cite the dates that were fetched sixty lines later.

So the lookup happens before anything is produced. The decision itself is
unchanged -- the orchestrator's own `research_task` still owns it, and a
deterministic plan is only used when there is no structured judgement to read.

The result is returned, not stored: the caller passes it to whatever it is about
to build, and reuses it later in the turn instead of searching twice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from services.darya_search_grounding import (
    SearchPlan,
    build_search_grounding,
    calibrate_bundle_for_search_plan,
    decide_search_plan,
    search_plan_from_model,
)
from services.tide_search_evidence import SearchContext, build_evidence_bundle


@dataclass(frozen=True)
class TurnSearch:
    """What this turn decided about the web, and what came back."""

    plan: SearchPlan
    bundle: Any | None = None
    grounding: Mapping[str, Any] | None = None

    @property
    def searched(self) -> bool:
        return self.bundle is not None

    def evidence_dict(self) -> dict[str, Any] | None:
        """The bundle as the capability paths expect it."""
        if self.bundle is None:
            return None
        return self.bundle.public_dict() if hasattr(self.bundle, "public_dict") else dict(self.bundle)

    def describe(self) -> str:
        return f"{self.plan.decision}/{self.plan.trigger} searched={self.searched}"


def _plan_for(
    *,
    message: str,
    cognition: Any | None,
    domain_status: str,
    installed_domain_id: str | None,
    domain_confidence: float,
) -> SearchPlan:
    if cognition is not None:
        return search_plan_from_model(
            cognition,
            domain_status=domain_status,
            installed_domain_id=installed_domain_id,
        )
    return decide_search_plan(
        message=message,
        domain_status=domain_status,
        installed_domain_id=installed_domain_id,
        domain_confidence=domain_confidence,
    )


async def resolve_turn_search(
    *,
    message: str,
    cognition: Any | None,
    domain_resolution: Any,
    learning_state: Any | None,
    search_context: SearchContext,
    immediate_safety: Any | None = None,
    disambiguation: Any | None = None,
    suppressed: bool = False,
) -> TurnSearch:
    """Decide, then fetch. Returns the plan even when nothing was fetched."""
    installed_domain_id = (
        domain_resolution.pack.domain_id
        if getattr(domain_resolution, "pack", None) is not None
        else (getattr(learning_state, "domain_id", None) if learning_state is not None else None)
    )
    plan = _plan_for(
        message=message,
        cognition=cognition,
        domain_status=str(getattr(domain_resolution, "status", "") or ""),
        installed_domain_id=installed_domain_id,
        domain_confidence=float(getattr(domain_resolution, "confidence", 0.0) or 0.0),
    )
    search_context.preferred_urls = list(plan.preferred_urls)

    should_search = bool(
        plan.should_search
        and immediate_safety is None
        and disambiguation is None
        and not suppressed
    )
    if not should_search:
        return TurnSearch(plan=plan)

    bundle = await build_evidence_bundle(
        search_context,
        search_intent=(True, plan.reason),
    )
    bundle = calibrate_bundle_for_search_plan(plan, bundle)
    return TurnSearch(
        plan=plan,
        bundle=bundle,
        grounding=build_search_grounding(plan, bundle),
    )


def grounding_for_plan(plan: SearchPlan | None, bundle: Any | None = None) -> Mapping[str, Any] | None:
    """Grounding for the no-search case, so the panel still explains itself."""
    if plan is None:
        return None
    return build_search_grounding(plan, bundle)


def context_for(
    *,
    message: str,
    query_override: str,
    shared_perspective: Mapping[str, Any],
    workspace_object: Mapping[str, Any],
    artifact_context: Mapping[str, Any],
    relevant_memories: Sequence[Any] = (),
    relevant_user_context: Sequence[Any] = (),
) -> SearchContext:
    """One place to build the search context, so both call sites agree."""
    return SearchContext(
        message=message,
        query_override=query_override,
        shared_perspective=shared_perspective,
        workspace_object=workspace_object,
        artifact_context=artifact_context,
        relevant_memories=relevant_memories,
        relevant_user_context=relevant_user_context,
    )
