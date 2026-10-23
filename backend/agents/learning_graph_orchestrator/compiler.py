from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from .calibrator import normalized_meaning
from .enums import NodeStatus, ResourcePlanStatus, Severity, UpdateMode
from .identity import (
    resolve_existing_node_identity,
    resolve_existing_resource_identity,
    resolve_existing_unit_identity,
)
from .models import (
    CandidateBlueprint,
    ChangeSet,
    KnowledgeEdge,
    KnowledgeNode,
    LearningGraphBlueprint,
    LearningGraphRequest,
    LearningUnit,
    ResourcePlan,
    ValidationReport,
)
from .validators import validate_candidate_contract


class CandidateContractError(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        codes = [x.code for x in report.issues if x.severity == Severity.ERROR]
        super().__init__(f"candidate contract rejected: {codes}")


def stable_id(prefix: str, namespace: str, semantic_key: str) -> str:
    raw = f"{namespace}|{prefix}|{semantic_key}".encode()
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:16]}"


def _canonical(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _node_changed(old, new: KnowledgeNode) -> bool:
    comparable = {
        "title": old.title,
        "summary": old.summary,
        "node_type": old.node_type.value,
        "learning_objectives": old.learning_objectives,
        "key_concepts": old.key_concepts,
        "mastery_evidence": old.mastery_evidence,
        "difficulty_band": old.difficulty_band,
        "estimated_effort_minutes": old.estimated_effort_minutes,
        "source_refs": old.source_refs,
    }
    current = new.model_dump(
        mode="json",
        exclude={
            "node_id",
            "resource_plan_ids",
            "status",
            "unit_id",
            "continuity_key",
        },
    )
    return _canonical(comparable) != _canonical(current)


def _unit_changed(old, new: LearningUnit) -> bool:
    comparable = {
        "title": old.title,
        "summary": old.summary,
        "unit_order": old.unit_order,
        "node_ids": sorted(old.node_ids),
    }
    current = {
        "title": new.title,
        "summary": new.summary,
        "unit_order": new.unit_order,
        "node_ids": sorted(new.node_ids),
    }
    return _canonical(comparable) != _canonical(current)


def _resource_changed(old, new: ResourcePlan) -> bool:
    comparable = {
        "purpose": old.purpose,
        "generation_spec": old.generation_spec,
        "required_inputs": old.required_inputs,
        "trigger": old.trigger.value,
        "priority": old.priority,
    }
    current = {
        "purpose": new.purpose,
        "generation_spec": new.generation_spec,
        "required_inputs": new.required_inputs,
        "trigger": new.trigger.value,
        "priority": new.priority,
    }
    return _canonical(comparable) != _canonical(current)


def _node_from_old(old, *, status: NodeStatus | None = None) -> KnowledgeNode:
    return KnowledgeNode(
        node_id=old.node_id,
        continuity_key=old.continuity_key or normalized_meaning(old.title) or old.node_id,
        title=old.title,
        summary=old.summary,
        node_type=old.node_type,
        learning_objectives=list(old.learning_objectives),
        key_concepts=list(old.key_concepts),
        mastery_evidence=list(old.mastery_evidence),
        difficulty_band=old.difficulty_band,
        estimated_effort_minutes=old.estimated_effort_minutes,
        source_refs=list(old.source_refs),
        resource_plan_ids=list(old.resource_plan_ids),
        status=status or old.status,
        unit_id=old.unit_id,
    )


def _resource_from_old(old) -> ResourcePlan:
    return ResourcePlan(
        resource_plan_id=old.resource_plan_id,
        continuity_key=old.continuity_key
        or normalized_meaning(f"{old.resource_type.value}:{old.purpose}")
        or old.resource_plan_id,
        node_id=old.node_id,
        resource_type=old.resource_type,
        purpose=old.purpose,
        generation_spec=dict(old.generation_spec),
        required_inputs=list(old.required_inputs),
        trigger=old.trigger,
        status=old.status,
        priority=old.priority,
        artifact_id=old.artifact_id,
        private_annotations=dict(old.private_annotations),
    )


def _old_node_is_replaced(request: LearningGraphRequest, old) -> bool:
    return bool(set(old.source_refs).intersection(request.replaced_source_ref_ids))


def compile_blueprint(
    request: LearningGraphRequest,
    candidate: CandidateBlueprint,
    *,
    fallback_used: bool = False,
) -> LearningGraphBlueprint:
    candidate_report = validate_candidate_contract(request, candidate)
    if not candidate_report.valid:
        raise CandidateContractError(candidate_report)

    existing = request.existing_graph_summary
    graph_id = (
        (existing.graph_id if existing else request.graph_id)
        or stable_id("graph", request.request_id, request.user_goal)
    )
    revision = existing.graph_revision + 1 if existing else 1
    namespace = graph_id
    changes = ChangeSet()

    old_node_by_id = {}
    if existing:
        old_node_by_id = {old.node_id: old for old in existing.nodes}

    key_to_id: dict[str, str] = {}
    compiled_nodes: list[KnowledgeNode] = []
    represented_old: set[str] = set()
    node_by_id: dict[str, KnowledgeNode] = {}
    needs_review = fallback_used or any(
        x.severity == Severity.REVIEW_REQUIRED for x in candidate_report.issues
    )

    for candidate_node in candidate.nodes:
        continuity = (
            candidate_node.continuity_key
            or normalized_meaning(candidate_node.title)
            or candidate_node.key
        )
        old = (
            resolve_existing_node_identity(existing, candidate_node).matched
            if existing
            else None
        )
        node_id = old.node_id if old else stable_id("node", namespace, continuity)
        key_to_id[candidate_node.key] = node_id
        if old:
            represented_old.add(old.node_id)

        preserve_old_contract = bool(
            old
            and (
                old.status in {NodeStatus.COMPLETED, NodeStatus.LEARNING}
                or (
                    request.update_mode == UpdateMode.REPLACE_SOURCE
                    and not _old_node_is_replaced(request, old)
                )
            )
        )
        if preserve_old_contract:
            node = _node_from_old(old)
            node.continuity_key = continuity
        else:
            status = NodeStatus(candidate_node.status)
            if old and old.status in {
                NodeStatus.GENERATED,
                NodeStatus.NEEDS_REVIEW,
                NodeStatus.FAILED,
            }:
                status = old.status
            node = KnowledgeNode(
                node_id=node_id,
                continuity_key=continuity,
                title=candidate_node.title,
                summary=candidate_node.summary,
                node_type=candidate_node.node_type,
                learning_objectives=list(candidate_node.learning_objectives),
                key_concepts=list(candidate_node.key_concepts),
                mastery_evidence=list(candidate_node.mastery_evidence),
                difficulty_band=candidate_node.difficulty_band,
                estimated_effort_minutes=candidate_node.estimated_effort_minutes,
                source_refs=list(candidate_node.source_ref_ids),
                resource_plan_ids=[],
                status=status,
                unit_id=None,
            )

        compiled_nodes.append(node)
        node_by_id[node.node_id] = node
        if old:
            if not preserve_old_contract and _node_changed(old, node):
                changes.updated_nodes.append(node_id)
        else:
            changes.added_nodes.append(node_id)

    if existing:
        for old in existing.nodes:
            if old.node_id in represented_old:
                continue
            preserve = False
            if request.update_mode in {UpdateMode.EXTEND, UpdateMode.MERGE}:
                preserve = True
            elif old.status in {NodeStatus.COMPLETED, NodeStatus.LEARNING}:
                preserve = True
            elif request.update_mode == UpdateMode.REPLACE_SOURCE:
                preserve = not _old_node_is_replaced(request, old)

            status = old.status if preserve else NodeStatus.SUPERSEDED
            node = _node_from_old(old, status=status)
            compiled_nodes.append(node)
            node_by_id[node.node_id] = node
            if status == NodeStatus.SUPERSEDED:
                changes.superseded_nodes.append(old.node_id)
            if (
                status in {NodeStatus.COMPLETED, NodeStatus.LEARNING}
                and not (
                    node.learning_objectives
                    and node.key_concepts
                    and node.mastery_evidence
                )
            ):
                needs_review = True

    # Stable unit identities: explicit continuity key -> normalized title -> durable ID.
    old_unit_by_id = {}
    old_membership: dict[str, str] = {}
    if existing:
        for old_unit in existing.learning_units:
            old_unit_by_id[old_unit.unit_id] = old_unit
            for node_id in old_unit.node_ids:
                old_membership[node_id] = old_unit.unit_id

    units_by_id: dict[str, LearningUnit] = {}
    candidate_unit_old_id: dict[str, str | None] = {}
    assigned: set[str] = set()
    for candidate_unit in sorted(
        candidate.learning_units,
        key=lambda value: (value.unit_order, value.key),
    ):
        continuity = (
            candidate_unit.continuity_key
            or normalized_meaning(candidate_unit.title)
            or candidate_unit.key
        )
        old_unit = (
            resolve_existing_unit_identity(existing, candidate_unit).matched
            if existing
            else None
        )
        unit_id = (
            old_unit.unit_id
            if old_unit
            else stable_id("unit", namespace, continuity)
        )
        node_ids = [key_to_id[key] for key in candidate_unit.node_keys]
        unit = LearningUnit(
            unit_id=unit_id,
            continuity_key=continuity,
            title=candidate_unit.title,
            summary=candidate_unit.summary,
            node_ids=list(node_ids),
            unit_order=candidate_unit.unit_order,
        )
        units_by_id[unit_id] = unit
        candidate_unit_old_id[unit_id] = old_unit.unit_id if old_unit else None
        for node_id in node_ids:
            assigned.add(node_id)
            node_by_id[node_id].unit_id = unit_id
        if old_unit is None:
            changes.added_units.append(unit_id)

    if existing:
        for old_unit in existing.learning_units:
            remaining = [
                node_id
                for node_id in old_unit.node_ids
                if node_id in node_by_id and node_id not in assigned
            ]
            if old_unit.unit_id in units_by_id:
                target = units_by_id[old_unit.unit_id]
                target.node_ids.extend(remaining)
            elif remaining:
                target = LearningUnit(
                    unit_id=old_unit.unit_id,
                    continuity_key=old_unit.continuity_key
                    or normalized_meaning(old_unit.title)
                    or old_unit.unit_id,
                    title=old_unit.title,
                    summary=old_unit.summary,
                    node_ids=list(remaining),
                    unit_order=old_unit.unit_order,
                )
                units_by_id[old_unit.unit_id] = target
            else:
                target = None
                changes.superseded_units.append(old_unit.unit_id)

            for node_id in remaining:
                assigned.add(node_id)
                node_by_id[node_id].unit_id = old_unit.unit_id

            if target and all(
                node_by_id[node_id].status == NodeStatus.SUPERSEDED
                for node_id in target.node_ids
            ):
                if old_unit.unit_id not in changes.superseded_units:
                    changes.superseded_units.append(old_unit.unit_id)

    # Defensive guard: candidate validation plus existing validation should make this impossible.
    unassigned = [node_id for node_id in node_by_id if node_id not in assigned]
    if unassigned:
        raise CandidateContractError(
            ValidationReport(
                valid=False,
                issues=[
                    # Local import avoided; this is a compiler invariant rather than repairable model output.
                    {
                        "code": "NODE_NOT_ASSIGNED_TO_UNIT",
                        "severity": "error",
                        "path": "$.learning_units",
                        "message": f"compiled nodes lost unit membership: {unassigned}",
                        "repairable": False,
                    }
                ],
            )
        )

    # Reconcile final unit node lists and update tracking after preserved nodes are appended.
    for unit in units_by_id.values():
        unit.node_ids = sorted(set(unit.node_ids))
        old_unit = old_unit_by_id.get(unit.unit_id)
        if old_unit and _unit_changed(old_unit, unit):
            changes.updated_units.append(unit.unit_id)

    # Resource history is immutable unless the same stable identity is explicitly matched.
    resources: list[ResourcePlan] = []
    represented_resource_ids: set[str] = set()
    plan_by_node: dict[str, list[str]] = defaultdict(list)

    def record_preserved(plan: ResourcePlan) -> None:
        if plan.status == ResourcePlanStatus.GENERATING:
            changes.preserved_generating_resource_plans.append(plan.resource_plan_id)
        elif plan.status == ResourcePlanStatus.GENERATED:
            changes.preserved_generated_resource_plans.append(plan.resource_plan_id)
        elif plan.status == ResourcePlanStatus.FAILED:
            changes.preserved_failed_resource_plans.append(plan.resource_plan_id)

    for candidate_plan in candidate.resource_plans:
        node_id = key_to_id[candidate_plan.node_key]
        continuity = (
            candidate_plan.continuity_key
            or normalized_meaning(f"{candidate_plan.resource_type.value}:{candidate_plan.purpose}")
            or candidate_plan.key
        )
        old_plan = None
        if existing:
            resolution = resolve_existing_resource_identity(existing, candidate_plan, node_id)
            old_plan = resolution.continuity_match if candidate_plan.continuity_key else resolution.semantic_match

        semantic = f"{node_id}|{candidate_plan.resource_type.value}|{continuity}"
        resource_id = old_plan.resource_plan_id if old_plan else stable_id("resource", namespace, semantic)
        if existing and old_plan is None and any(plan.resource_plan_id == resource_id for plan in existing.resource_plans):
            resource_id = stable_id("resource", namespace, f"new|{revision}|{semantic}|{candidate_plan.key}")

        # generating/failed are execution history and must not be rewritten by graph orchestration.
        if old_plan and old_plan.status in {ResourcePlanStatus.GENERATING, ResourcePlanStatus.FAILED}:
            plan = _resource_from_old(old_plan)
            record_preserved(plan)
        else:
            status = ResourcePlanStatus.PLANNED
            artifact_id = None
            private_annotations = dict(old_plan.private_annotations) if old_plan else {}
            if old_plan and old_plan.status == ResourcePlanStatus.GENERATED:
                status = ResourcePlanStatus.GENERATED
                artifact_id = old_plan.artifact_id
            plan = ResourcePlan(
                resource_plan_id=resource_id,
                continuity_key=(old_plan.continuity_key if old_plan and old_plan.continuity_key else continuity),
                node_id=node_id,
                resource_type=candidate_plan.resource_type,
                purpose=candidate_plan.purpose,
                generation_spec=dict(candidate_plan.generation_spec),
                required_inputs=list(candidate_plan.required_inputs),
                trigger=candidate_plan.trigger,
                status=status,
                priority=candidate_plan.priority,
                artifact_id=artifact_id,
                private_annotations=private_annotations,
            )
            if old_plan and old_plan.status == ResourcePlanStatus.GENERATED:
                record_preserved(plan)

        resources.append(plan)
        represented_resource_ids.add(resource_id)
        plan_by_node[node_id].append(resource_id)
        if old_plan:
            if _resource_changed(old_plan, plan):
                changes.updated_resource_plans.append(resource_id)
        else:
            changes.added_resource_plans.append(resource_id)

    if existing:
        for old_plan in existing.resource_plans:
            if old_plan.resource_plan_id in represented_resource_ids:
                continue
            node = node_by_id.get(old_plan.node_id)
            historical = old_plan.status in {
                ResourcePlanStatus.GENERATING,
                ResourcePlanStatus.GENERATED,
                ResourcePlanStatus.FAILED,
                ResourcePlanStatus.SUPERSEDED,
            }
            preserve = historical
            if old_plan.status == ResourcePlanStatus.PLANNED:
                if request.update_mode in {UpdateMode.EXTEND, UpdateMode.MERGE}:
                    preserve = True
                elif request.update_mode == UpdateMode.REPLACE_SOURCE:
                    old_node = old_node_by_id.get(old_plan.node_id)
                    preserve = bool(old_node and not _old_node_is_replaced(request, old_node))
                else:
                    preserve = False

            # Historical execution/artifact records survive even when their node was superseded.
            if node is None:
                preserve = False
            elif node.status == NodeStatus.SUPERSEDED and old_plan.status == ResourcePlanStatus.PLANNED:
                preserve = False

            if preserve:
                preserved = _resource_from_old(old_plan)
                resources.append(preserved)
                plan_by_node[preserved.node_id].append(preserved.resource_plan_id)
                record_preserved(preserved)
            else:
                changes.removed_resource_plans.append(old_plan.resource_plan_id)

    for node in compiled_nodes:
        node.resource_plan_ids = sorted(set(plan_by_node.get(node.node_id, [])))

    # Edge semantics differ by update mode; no unknown candidate references can reach here.
    old_edges_by_signature = {}
    if existing:
        for old_edge in existing.edges:
            old_edges_by_signature[
                (
                    old_edge.source_node_id,
                    old_edge.target_node_id,
                    old_edge.edge_type.value,
                )
            ] = old_edge

    edges: list[KnowledgeEdge] = []
    new_signatures = set()
    for candidate_edge in candidate.edges:
        source = key_to_id[candidate_edge.source_key]
        target = key_to_id[candidate_edge.target_key]
        signature = (source, target, candidate_edge.edge_type.value)
        new_signatures.add(signature)
        old_edge = old_edges_by_signature.get(signature)
        edge_id = (
            old_edge.edge_id
            if old_edge
            else stable_id("edge", namespace, "|".join(signature))
        )
        edges.append(
            KnowledgeEdge(
                edge_id=edge_id,
                source_node_id=source,
                target_node_id=target,
                edge_type=candidate_edge.edge_type,
                reason=candidate_edge.reason,
            )
        )
        if old_edge is None:
            changes.added_edges.append(edge_id)

    if existing:
        for signature, old_edge in old_edges_by_signature.items():
            if signature in new_signatures:
                continue
            endpoints = [node_by_id.get(signature[0]), node_by_id.get(signature[1])]
            endpoints_exist = all(endpoints)
            preserve = request.update_mode in {UpdateMode.EXTEND, UpdateMode.MERGE}
            if request.update_mode == UpdateMode.REPLACE_SOURCE:
                old_source = old_node_by_id.get(signature[0])
                old_target = old_node_by_id.get(signature[1])
                affected = bool(
                    (old_source and _old_node_is_replaced(request, old_source))
                    or (old_target and _old_node_is_replaced(request, old_target))
                )
                preserve = not affected
            if any(
                node is not None and node.status == NodeStatus.SUPERSEDED
                for node in endpoints
            ):
                preserve = False
            if endpoints_exist and preserve:
                edges.append(KnowledgeEdge(**old_edge.model_dump()))
            else:
                changes.removed_edges.append(old_edge.edge_id)

    changes.added_units = sorted(set(changes.added_units))
    changes.updated_units = sorted(set(changes.updated_units))
    changes.superseded_units = sorted(set(changes.superseded_units))
    changes.added_nodes = sorted(set(changes.added_nodes))
    changes.updated_nodes = sorted(set(changes.updated_nodes))
    changes.superseded_nodes = sorted(set(changes.superseded_nodes))
    changes.added_edges = sorted(set(changes.added_edges))
    changes.removed_edges = sorted(set(changes.removed_edges))
    changes.added_resource_plans = sorted(set(changes.added_resource_plans))
    changes.updated_resource_plans = sorted(set(changes.updated_resource_plans))
    changes.removed_resource_plans = sorted(set(changes.removed_resource_plans))
    changes.preserved_generating_resource_plans = sorted(
        set(changes.preserved_generating_resource_plans)
    )
    changes.preserved_generated_resource_plans = sorted(
        set(changes.preserved_generated_resource_plans)
    )
    changes.preserved_failed_resource_plans = sorted(
        set(changes.preserved_failed_resource_plans)
    )
    changes.resource_plan_changes = sorted(
        set(
            changes.added_resource_plans
            + changes.updated_resource_plans
            + changes.removed_resource_plans
        )
    )

    return LearningGraphBlueprint(
        graph_id=graph_id,
        graph_revision=revision,
        graph_title=candidate.graph_title,
        graph_summary=candidate.graph_summary,
        learning_units=sorted(
            units_by_id.values(), key=lambda value: (value.unit_order, value.unit_id)
        ),
        nodes=sorted(compiled_nodes, key=lambda value: value.node_id),
        edges=sorted(edges, key=lambda value: value.edge_id),
        resource_plans=sorted(resources, key=lambda value: value.resource_plan_id),
        validation_hints=list(candidate.validation_hints),
        continuity_keys=sorted(set(candidate.continuity_keys)),
        changes=changes,
        status="draft",
        fallback_used=fallback_used,
        needs_review=needs_review,
    )
