from __future__ import annotations

import re

from .enums import ResourcePlanStatus
from .models import (
    LearningGraphBlueprint,
    LearningGraphPublicView,
    PublicKnowledgeEdge,
    PublicKnowledgeNode,
    PublicLearningUnit,
    PublicResourcePlan,
)

PUBLIC_KEYS = {
    "graph_id",
    "graph_revision",
    "graph_title",
    "graph_summary",
    "learning_units",
    "nodes",
    "edges",
    "resource_plans",
    "status",
}
_SAFE_REF = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def _safe_source_refs(values: list[str]) -> list[str]:
    # Only opaque reference IDs are public. Paths, URLs, queries, and metadata are excluded.
    return sorted({value for value in values if _SAFE_REF.fullmatch(value)})


def build_public_view(graph: LearningGraphBlueprint) -> LearningGraphPublicView:
    units = [
        PublicLearningUnit(
            unit_id=unit.unit_id,
            title=unit.title,
            summary=unit.summary,
            node_ids=list(unit.node_ids),
            unit_order=unit.unit_order,
        )
        for unit in graph.learning_units
    ]
    nodes = [
        PublicKnowledgeNode(
            node_id=node.node_id,
            title=node.title,
            summary=node.summary,
            node_type=node.node_type,
            learning_objectives=list(node.learning_objectives),
            key_concepts=list(node.key_concepts),
            mastery_evidence=list(node.mastery_evidence),
            difficulty_band=node.difficulty_band,
            estimated_effort_minutes=node.estimated_effort_minutes,
            source_refs=_safe_source_refs(node.source_refs),
            resource_plan_ids=list(node.resource_plan_ids),
            status=node.status,
            unit_id=node.unit_id,
        )
        for node in graph.nodes
    ]
    edges = [
        PublicKnowledgeEdge(
            edge_id=edge.edge_id,
            source_node_id=edge.source_node_id,
            target_node_id=edge.target_node_id,
            edge_type=edge.edge_type,
            reason=edge.reason,
        )
        for edge in graph.edges
    ]
    resources = [
        PublicResourcePlan(
            resource_plan_id=plan.resource_plan_id,
            node_id=plan.node_id,
            resource_type=plan.resource_type,
            purpose=plan.purpose,
            trigger=plan.trigger,
            status=plan.status,
            priority=plan.priority,
            artifact_id=(
                plan.artifact_id
                if plan.status == ResourcePlanStatus.GENERATED
                and plan.artifact_id is not None
                else None
            ),
        )
        for plan in graph.resource_plans
    ]
    return LearningGraphPublicView(
        graph_id=graph.graph_id,
        graph_revision=graph.graph_revision,
        graph_title=graph.graph_title,
        graph_summary=graph.graph_summary,
        learning_units=units,
        nodes=nodes,
        edges=edges,
        resource_plans=resources,
        status=graph.status,
    )


def public_payload(view: LearningGraphPublicView) -> dict:
    payload = view.model_dump(mode="json", exclude_none=True)
    if set(payload) != PUBLIC_KEYS:
        raise AssertionError("public whitelist changed")
    return payload
