from __future__ import annotations

from .calibrator import normalized_meaning
from .source_identity import validate_opaque_source_id
from .models import (
    ExistingGraphModelContext,
    LearningGraphModelInput,
    LearningGraphRequest,
    ModelEdgeContext,
    ModelGraphConstraints,
    ModelKnowledgeNodeContext,
    ModelLearningUnitContext,
    ModelResourcePlanContext,
    ModelSourceRef,
)



class ModelInputContractError(ValueError):
    """Raised when private-to-model projection cannot be formed without guessing."""


def _continuity(explicit: str | None, title: str, kind: str) -> str:
    value = explicit or normalized_meaning(title)
    if not value:
        raise ModelInputContractError(f"missing semantic continuity for {kind}")
    return value


def _safe_refs(values: list[str]) -> list[str]:
    invalid = [value for value in values if not validate_opaque_source_id(value)]
    if invalid:
        raise ModelInputContractError(f"unsafe source refs cannot enter model input: {invalid}")
    return sorted(set(values))


def build_existing_graph_model_context(
    request: LearningGraphRequest,
) -> ExistingGraphModelContext | None:
    existing = request.existing_graph_summary
    if existing is None:
        return None

    node_continuity = {
        node.node_id: _continuity(node.continuity_key, node.title, "node")
        for node in existing.nodes
    }
    unit_contexts = []
    for unit in existing.learning_units:
        try:
            member_keys = [node_continuity[node_id] for node_id in unit.node_ids]
        except KeyError as error:
            raise ModelInputContractError(
                f"unit references unknown node: {error.args[0]}"
            ) from error
        unit_contexts.append(
            ModelLearningUnitContext(
                continuity_key=_continuity(
                    unit.continuity_key, unit.title, "learning unit"
                ),
                title=unit.title,
                summary=unit.summary,
                node_continuity_keys=member_keys,
                unit_order=unit.unit_order,
            )
        )

    node_contexts = [
        ModelKnowledgeNodeContext(
            continuity_key=node_continuity[node.node_id],
            title=node.title,
            summary=node.summary,
            node_type=node.node_type,
            learning_objectives=list(node.learning_objectives),
            key_concepts=list(node.key_concepts),
            mastery_evidence=list(node.mastery_evidence),
            difficulty_band=node.difficulty_band,
            estimated_effort_minutes=node.estimated_effort_minutes,
            safe_source_ref_ids=_safe_refs(node.source_refs),
            status=node.status,
        )
        for node in existing.nodes
    ]

    edge_contexts = []
    for edge in existing.edges:
        try:
            source_key = node_continuity[edge.source_node_id]
            target_key = node_continuity[edge.target_node_id]
        except KeyError as error:
            raise ModelInputContractError(
                f"edge references unknown node: {error.args[0]}"
            ) from error
        edge_contexts.append(
            ModelEdgeContext(
                source_node_continuity_key=source_key,
                target_node_continuity_key=target_key,
                edge_type=edge.edge_type,
                reason=edge.reason,
            )
        )

    resource_contexts = []
    for plan in existing.resource_plans:
        if plan.node_id not in node_continuity:
            raise ModelInputContractError(
                f"resource plan references unknown node: {plan.node_id}"
            )
        continuity = plan.continuity_key or normalized_meaning(
            f"{plan.resource_type.value}:{plan.purpose}"
        )
        if not continuity:
            raise ModelInputContractError(
                f"resource plan lacks semantic continuity: {plan.resource_plan_id}"
            )
        resource_contexts.append(
            ModelResourcePlanContext(
                continuity_key=continuity,
                node_continuity_key=node_continuity[plan.node_id],
                resource_type=plan.resource_type,
                purpose=plan.purpose,
                trigger=plan.trigger,
                status=plan.status,
                materialized=plan.artifact_id is not None,
            )
        )

    return ExistingGraphModelContext(
        graph_revision=existing.graph_revision,
        graph_title=existing.graph_title,
        graph_summary=existing.graph_summary,
        learning_units=unit_contexts,
        nodes=node_contexts,
        edges=edge_contexts,
        resource_plans=resource_contexts,
    )


def build_model_input(request: LearningGraphRequest) -> LearningGraphModelInput:
    return LearningGraphModelInput(
        user_goal=request.user_goal,
        domain=request.domain,
        locale=request.locale,
        source_refs=[
            ModelSourceRef(
                ref_id=ref.ref_id,
                kind=ref.kind,
                title=ref.title,
                content_hash=ref.content_hash,
            )
            for ref in request.source_refs
        ],
        source_outline=[item.model_copy(deep=True) for item in request.source_outline],
        source_summaries=[item.model_copy(deep=True) for item in request.source_summaries],
        existing_graph=build_existing_graph_model_context(request),
        enabled_resource_types=[
            item.resource_type for item in request.resource_capabilities if item.enabled
        ],
        constraints=ModelGraphConstraints(
            min_nodes=request.constraints.min_nodes,
            max_nodes=request.constraints.max_nodes,
            max_edges=request.constraints.max_edges,
            max_depth=request.constraints.max_depth,
            max_resources_per_node=request.constraints.max_resources_per_node,
            requested_resource_types=list(request.constraints.requested_resource_types),
        ),
        update_mode=request.update_mode,
        replaced_source_ref_ids=list(request.replaced_source_ref_ids),
    )


def model_input_payload(request: LearningGraphRequest) -> dict:
    return build_model_input(request).model_dump(mode="json")
