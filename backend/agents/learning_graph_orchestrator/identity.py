from __future__ import annotations

from dataclasses import dataclass

from .calibrator import normalized_meaning
from .enums import NodeStatus, ResourcePlanStatus
from .models import (
    CandidateKnowledgeNode,
    CandidateLearningUnit,
    CandidateResourcePlan,
    ExistingGraphSummary,
    ExistingNodeSummary,
    ExistingResourcePlanSummary,
    ExistingUnitSummary,
)


@dataclass(frozen=True)
class IdentityResolution:
    continuity_match: object | None
    semantic_match: object | None

    @property
    def conflict(self) -> bool:
        if self.continuity_match is None or self.semantic_match is None:
            return False
        return _identity(self.continuity_match) != _identity(self.semantic_match)

    @property
    def matched(self):
        return self.continuity_match or self.semantic_match


def _identity(value) -> str:
    if isinstance(value, ExistingResourcePlanSummary):
        return value.resource_plan_id
    if isinstance(value, ExistingUnitSummary):
        return value.unit_id
    if isinstance(value, ExistingNodeSummary):
        return value.node_id
    raise TypeError(f"unsupported identity value: {type(value)!r}")


def _active_node(node: ExistingNodeSummary) -> bool:
    return node.status != NodeStatus.SUPERSEDED


def _active_unit_ids(existing: ExistingGraphSummary) -> set[str]:
    active_nodes = {node.node_id for node in existing.nodes if _active_node(node)}
    return {
        unit.unit_id
        for unit in existing.learning_units
        if any(node_id in active_nodes for node_id in unit.node_ids)
    }


def resolve_existing_node_identity(
    existing: ExistingGraphSummary,
    candidate: CandidateKnowledgeNode,
) -> IdentityResolution:
    by_cont = {
        node.continuity_key: node
        for node in existing.nodes
        if node.continuity_key
    }
    by_title = {
        normalized_meaning(node.title): node
        for node in existing.nodes
        if _active_node(node)
    }
    continuity_match = (
        by_cont.get(candidate.continuity_key)
        if candidate.continuity_key
        else None
    )
    semantic_match = by_title.get(normalized_meaning(candidate.title))
    return IdentityResolution(continuity_match, semantic_match)


def resolve_existing_unit_identity(
    existing: ExistingGraphSummary,
    candidate: CandidateLearningUnit,
) -> IdentityResolution:
    active_ids = _active_unit_ids(existing)
    by_cont = {
        unit.continuity_key: unit
        for unit in existing.learning_units
        if unit.continuity_key
    }
    by_title = {
        normalized_meaning(unit.title): unit
        for unit in existing.learning_units
        if unit.unit_id in active_ids
    }
    continuity_match = (
        by_cont.get(candidate.continuity_key)
        if candidate.continuity_key
        else None
    )
    semantic_match = by_title.get(normalized_meaning(candidate.title))
    return IdentityResolution(continuity_match, semantic_match)


def resolve_existing_resource_identity(
    existing: ExistingGraphSummary,
    candidate: CandidateResourcePlan,
    node_id: str,
) -> IdentityResolution:
    by_cont = {
        (plan.node_id, plan.continuity_key): plan
        for plan in existing.resource_plans
        if plan.continuity_key and plan.status != ResourcePlanStatus.SUPERSEDED
    }
    semantic_candidates: dict[tuple[str, str, str], list[ExistingResourcePlanSummary]] = {}
    for plan in existing.resource_plans:
        if plan.status == ResourcePlanStatus.SUPERSEDED:
            continue
        key = (
            plan.node_id,
            plan.resource_type.value,
            normalized_meaning(plan.purpose),
        )
        semantic_candidates.setdefault(key, []).append(plan)

    continuity_match = (
        by_cont.get((node_id, candidate.continuity_key))
        if candidate.continuity_key
        else None
    )
    matches = semantic_candidates.get(
        (node_id, candidate.resource_type.value, normalized_meaning(candidate.purpose)),
        [],
    )
    semantic_match = matches[0] if len(matches) == 1 else None
    return IdentityResolution(continuity_match, semantic_match)
