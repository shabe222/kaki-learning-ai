from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from .calibrator import normalized_meaning
from .enums import (
    EdgeType,
    NodeStatus,
    NodeType,
    ResourcePlanStatus,
    Severity,
    UpdateMode,
)
from .graph_algorithms import cycle_nodes, graph_depth, roots_and_terminals
from .identity import (
    resolve_existing_node_identity,
    resolve_existing_resource_identity,
    resolve_existing_unit_identity,
)
from .models import (
    CandidateBlueprint,
    LearningGraphBlueprint,
    LearningGraphRequest,
    ValidationIssue,
    ValidationReport,
)
from .registry import CapabilityRegistry
from .source_identity import looks_like_private_path_or_url, validate_opaque_source_id


def issue(code, severity, path, message, repairable=False):
    return ValidationIssue(
        code=code,
        severity=severity,
        path=path,
        message=message,
        repairable=repairable,
    )


def _report(issues: list[ValidationIssue], *, depth: int = 0, roots=None, terminals=None):
    return ValidationReport(
        valid=not any(x.severity == Severity.ERROR for x in issues),
        issues=issues,
        graph_depth=depth,
        root_node_ids=roots or [],
        terminal_node_ids=terminals or [],
    )


def validate_candidate_schema(value):
    try:
        return CandidateBlueprint.model_validate(value), []
    except Exception as error:
        return None, [
            issue("INVALID_SCHEMA", Severity.ERROR, "$", str(error)[:1000], True)
        ]


def _duplicates(values):
    counts = Counter(values)
    return sorted(k for k, count in counts.items() if count > 1)




def _prerequisite_cycle_ids(node_ids: set[str], edges) -> list[str]:
    adjacency = {node_id: [] for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        if edge.edge_type != EdgeType.PREREQUISITE:
            continue
        if edge.source_node_id in adjacency and edge.target_node_id in adjacency:
            adjacency[edge.source_node_id].append(edge.target_node_id)
            indegree[edge.target_node_id] += 1
    queue = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = []
    while queue:
        node_id = queue.pop(0)
        visited.append(node_id)
        for target in sorted(adjacency[node_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
                queue.sort()
    return sorted(node_ids.difference(visited))


def validate_source_contract(request: LearningGraphRequest) -> ValidationReport:
    """Validate all source identities and outline relationships before any model call."""
    out: list[ValidationIssue] = []
    refs = request.source_refs
    outlines = request.source_outline
    summaries = request.source_summaries
    ref_ids = [item.ref_id for item in refs]
    outline_ids = [item.outline_id for item in outlines]
    summary_ids = [item.summary_id for item in summaries]
    ref_set = set(ref_ids)
    outline_by_id = {item.outline_id: item for item in outlines}

    for code, path, values in [
        ("DUPLICATE_SOURCE_REF_ID", "$.source_refs", ref_ids),
        ("DUPLICATE_OUTLINE_ID", "$.source_outline", outline_ids),
        ("DUPLICATE_SUMMARY_ID", "$.source_summaries", summary_ids),
    ]:
        if dup := _duplicates(values):
            out.append(issue(code, Severity.ERROR, path, f"duplicate identifiers: {dup}", False))

    for index, ref in enumerate(refs):
        if not validate_opaque_source_id(ref.ref_id):
            out.append(issue("INVALID_SOURCE_REF_ID", Severity.ERROR, f"$.source_refs[{index}].ref_id", "source ref must be a conservative opaque ID, not a path or URL", False))
        if looks_like_private_path_or_url(ref.title):
            out.append(issue("PRIVATE_PATH_IN_SOURCE_TITLE", Severity.ERROR, f"$.source_refs[{index}].title", "source title must not contain an absolute path or URL", False))

    for index, item in enumerate(outlines):
        path = f"$.source_outline[{index}]"
        if not validate_opaque_source_id(item.outline_id):
            out.append(issue("INVALID_OUTLINE_ID", Severity.ERROR, f"{path}.outline_id", "outline_id must be an opaque ID", False))
        if looks_like_private_path_or_url(item.title):
            out.append(issue("PRIVATE_PATH_IN_SOURCE_TITLE", Severity.ERROR, f"{path}.title", "outline title must not contain an absolute path or URL", False))
        if item.parent_outline_id is not None:
            if not validate_opaque_source_id(item.parent_outline_id):
                out.append(issue("INVALID_OUTLINE_ID", Severity.ERROR, f"{path}.parent_outline_id", "parent outline ID must be opaque", False))
            if item.parent_outline_id == item.outline_id:
                out.append(issue("SELF_OUTLINE_PARENT", Severity.ERROR, f"{path}.parent_outline_id", "outline cannot be its own parent", False))
            parent = outline_by_id.get(item.parent_outline_id)
            if parent is None:
                out.append(issue("UNKNOWN_OUTLINE_PARENT", Severity.ERROR, f"{path}.parent_outline_id", "outline parent does not exist", False))
            elif parent.level >= item.level:
                out.append(issue("INVALID_OUTLINE_LEVEL", Severity.ERROR, f"{path}.level", "parent level must be lower than child level", False))
        if item.source_ref_id is not None:
            if not validate_opaque_source_id(item.source_ref_id):
                out.append(issue("INVALID_SOURCE_REF_ID", Severity.ERROR, f"{path}.source_ref_id", "outline source ref must be opaque", False))
            elif item.source_ref_id not in ref_set:
                out.append(issue("UNKNOWN_OUTLINE_SOURCE_REF", Severity.ERROR, f"{path}.source_ref_id", "outline references an unknown source", False))

    # Detect cycles independently from level validation so malformed historical payloads get a precise issue.
    for item in outlines:
        seen: set[str] = set()
        current = item
        while current.parent_outline_id is not None and current.parent_outline_id in outline_by_id:
            if current.outline_id in seen or current.parent_outline_id in seen:
                out.append(issue("OUTLINE_PARENT_CYCLE", Severity.ERROR, "$.source_outline", f"outline parent cycle includes {item.outline_id}", False))
                break
            seen.add(current.outline_id)
            current = outline_by_id[current.parent_outline_id]

    if outlines and not any(item.parent_outline_id is None for item in outlines):
        out.append(issue("UNKNOWN_OUTLINE_PARENT", Severity.ERROR, "$.source_outline", "source outline must contain at least one root item", False))

    for index, summary in enumerate(summaries):
        path = f"$.source_summaries[{index}]"
        if not validate_opaque_source_id(summary.summary_id):
            out.append(issue("INVALID_SUMMARY_ID", Severity.ERROR, f"{path}.summary_id", "summary_id must be an opaque ID", False))
        if not validate_opaque_source_id(summary.source_ref_id):
            out.append(issue("INVALID_SOURCE_REF_ID", Severity.ERROR, f"{path}.source_ref_id", "summary source ref must be opaque", False))
        elif summary.source_ref_id not in ref_set:
            out.append(issue("UNKNOWN_SUMMARY_SOURCE_REF", Severity.ERROR, f"{path}.source_ref_id", "summary references an unknown source", False))
        if summary.outline_id is not None:
            if not validate_opaque_source_id(summary.outline_id):
                out.append(issue("INVALID_OUTLINE_ID", Severity.ERROR, f"{path}.outline_id", "summary outline ID must be opaque", False))
            elif summary.outline_id not in outline_by_id:
                out.append(issue("UNKNOWN_SUMMARY_OUTLINE_ID", Severity.ERROR, f"{path}.outline_id", "summary references an unknown outline", False))

    existing_ref_ids: set[str] = set()
    if request.existing_graph_summary:
        for node_index, node in enumerate(request.existing_graph_summary.nodes):
            for ref_index, ref_id in enumerate(node.source_refs):
                if not validate_opaque_source_id(ref_id):
                    out.append(issue("INVALID_SOURCE_REF_ID", Severity.ERROR, f"$.existing_graph_summary.nodes[{node_index}].source_refs[{ref_index}]", "existing node source ref must be opaque", False))
                existing_ref_ids.add(ref_id)

    for index, ref_id in enumerate(request.replaced_source_ref_ids):
        if not validate_opaque_source_id(ref_id):
            out.append(issue("INVALID_REPLACED_SOURCE_REF", Severity.ERROR, f"$.replaced_source_ref_ids[{index}]", "replacement source ID must be opaque", False))
        elif ref_id not in ref_set and ref_id not in existing_ref_ids:
            out.append(issue("INVALID_REPLACED_SOURCE_REF", Severity.ERROR, f"$.replaced_source_ref_ids[{index}]", "replacement source ID does not exist in input or existing graph", False))

    return _report(out)


def validate_existing_graph_contract(
    request: LearningGraphRequest,
) -> ValidationReport:
    existing = request.existing_graph_summary
    if existing is None:
        return _report([])

    out: list[ValidationIssue] = []
    node_ids = [x.node_id for x in existing.nodes]
    unit_ids = [x.unit_id for x in existing.learning_units]
    edge_ids = [x.edge_id for x in existing.edges]
    resource_ids = [x.resource_plan_id for x in existing.resource_plans]
    node_set = set(node_ids)
    plan_by_id = {x.resource_plan_id: x for x in existing.resource_plans}

    for code, path, values in [
        ("DUPLICATE_NODE_ID", "$.existing_graph_summary.nodes", node_ids),
        ("DUPLICATE_UNIT_ID", "$.existing_graph_summary.learning_units", unit_ids),
        ("DUPLICATE_EDGE_ID", "$.existing_graph_summary.edges", edge_ids),
        ("DUPLICATE_RESOURCE_PLAN_ID", "$.existing_graph_summary.resource_plans", resource_ids),
    ]:
        if dup := _duplicates(values):
            out.append(issue(code, Severity.ERROR, path, f"duplicate ids: {dup}"))

    node_cont = [x.continuity_key for x in existing.nodes if x.continuity_key]
    unit_cont = [x.continuity_key for x in existing.learning_units if x.continuity_key]
    if dup := _duplicates(node_cont):
        out.append(issue("DUPLICATE_NODE_CONTINUITY_KEY", Severity.ERROR, "$.existing_graph_summary.nodes", f"duplicate continuity keys: {dup}"))
    if dup := _duplicates(unit_cont):
        out.append(issue("DUPLICATE_UNIT_CONTINUITY_KEY", Severity.ERROR, "$.existing_graph_summary.learning_units", f"duplicate continuity keys: {dup}"))

    scoped_plan_cont = [
        (plan.node_id, plan.continuity_key)
        for plan in existing.resource_plans
        if plan.continuity_key
    ]
    if dup := _duplicates(scoped_plan_cont):
        out.append(issue("DUPLICATE_RESOURCE_PLAN_CONTINUITY_KEY", Severity.ERROR, "$.existing_graph_summary.resource_plans", f"duplicate scoped continuity keys: {dup}"))

    active_nodes = [node for node in existing.nodes if node.status != NodeStatus.SUPERSEDED]
    active_node_titles = [normalized_meaning(node.title) for node in active_nodes]
    if dup := _duplicates(active_node_titles):
        out.append(issue("DUPLICATE_ACTIVE_NODE_MEANING", Severity.ERROR, "$.existing_graph_summary.nodes", f"duplicate active normalized titles: {dup}"))
    active_node_ids = {node.node_id for node in active_nodes}
    active_units = [
        unit for unit in existing.learning_units
        if any(node_id in active_node_ids for node_id in unit.node_ids)
    ]
    active_unit_titles = [normalized_meaning(unit.title) for unit in active_units]
    if dup := _duplicates(active_unit_titles):
        out.append(issue("DUPLICATE_ACTIVE_UNIT_MEANING", Severity.ERROR, "$.existing_graph_summary.learning_units", f"duplicate active normalized titles: {dup}"))

    assignments = Counter()
    plan_claims: dict[str, list[str]] = defaultdict(list)
    for index, unit in enumerate(existing.learning_units):
        for node_id in unit.node_ids:
            if node_id not in node_set:
                out.append(issue("UNKNOWN_NODE_REFERENCE", Severity.ERROR, f"$.existing_graph_summary.learning_units[{index}]", f"unit references unknown node: {node_id}"))
            assignments[node_id] += 1

    for node in existing.nodes:
        if assignments[node.node_id] != 1:
            out.append(issue("UNIT_NODE_MISMATCH", Severity.ERROR, "$.existing_graph_summary.learning_units", f"node must occur in exactly one unit: {node.node_id}"))
        incomplete = not (node.learning_objectives and node.key_concepts and node.mastery_evidence)
        if incomplete and node.status in {NodeStatus.COMPLETED, NodeStatus.LEARNING}:
            out.append(issue("EXISTING_NODE_CONTRACT_INCOMPLETE", Severity.REVIEW_REQUIRED, f"$.existing_graph_summary.nodes[{node.node_id}]", "historical completed/learning node has an incomplete academic contract; preserve it without fabrication", False))
        elif incomplete and node.status != NodeStatus.SUPERSEDED:
            out.append(issue("EXISTING_NODE_CONTRACT_INCOMPLETE", Severity.ERROR, f"$.existing_graph_summary.nodes[{node.node_id}]", "active existing node has an incomplete academic contract", False))

        for plan_id in node.resource_plan_ids:
            plan_claims[plan_id].append(node.node_id)
            plan = plan_by_id.get(plan_id)
            if plan is None:
                out.append(issue("RESOURCE_PLAN_NODE_LIST_MISMATCH", Severity.ERROR, f"$.existing_graph_summary.nodes[{node.node_id}].resource_plan_ids", f"node references unknown resource plan: {plan_id}"))
            elif plan.node_id != node.node_id:
                out.append(issue("RESOURCE_PLAN_NODE_LIST_MISMATCH", Severity.ERROR, f"$.existing_graph_summary.nodes[{node.node_id}].resource_plan_ids", f"resource plan {plan_id} belongs to {plan.node_id}, not {node.node_id}"))

    for plan_id, claimers in plan_claims.items():
        if len(set(claimers)) > 1:
            out.append(issue("RESOURCE_PLAN_NODE_LIST_MISMATCH", Severity.ERROR, "$.existing_graph_summary.nodes", f"resource plan is claimed by multiple nodes: {plan_id} -> {sorted(set(claimers))}"))

    edge_signatures = []
    prerequisite_edges = []
    for index, edge in enumerate(existing.edges):
        path = f"$.existing_graph_summary.edges[{index}]"
        signature = (edge.source_node_id, edge.target_node_id, edge.edge_type.value)
        edge_signatures.append(signature)
        if edge.source_node_id not in node_set or edge.target_node_id not in node_set:
            out.append(issue("UNKNOWN_NODE_REFERENCE", Severity.ERROR, path, "edge references unknown existing node"))
            continue
        if edge.source_node_id not in active_node_ids or edge.target_node_id not in active_node_ids:
            out.append(issue("INVALID_ACTIVE_EDGE_ENDPOINT", Severity.ERROR, path, "active edge endpoint is superseded"))
        if edge.edge_type == EdgeType.PREREQUISITE:
            prerequisite_edges.append(edge)
            if edge.source_node_id == edge.target_node_id:
                out.append(issue("SELF_DEPENDENCY", Severity.ERROR, path, "prerequisite self dependency is forbidden"))
    if dup := _duplicates(edge_signatures):
        out.append(issue("DUPLICATE_EDGE_SIGNATURE", Severity.ERROR, "$.existing_graph_summary.edges", f"duplicate edge signatures: {dup}"))
    cycle = _prerequisite_cycle_ids(active_node_ids, prerequisite_edges)
    if cycle:
        out.append(issue("PREREQUISITE_CYCLE", Severity.ERROR, "$.existing_graph_summary.edges", f"existing prerequisite cycle: {cycle}"))

    for index, plan in enumerate(existing.resource_plans):
        path = f"$.existing_graph_summary.resource_plans[{index}]"
        if plan.node_id not in node_set:
            out.append(issue("RESOURCE_NODE_MISMATCH", Severity.ERROR, path, "resource plan references unknown existing node"))
        if plan.resource_plan_id not in plan_claims or plan.node_id not in plan_claims[plan.resource_plan_id]:
            out.append(issue("RESOURCE_PLAN_NODE_LIST_MISMATCH", Severity.ERROR, path, "resource plan is not listed by its owning node"))

        artifact_invalid = False
        if plan.status == ResourcePlanStatus.GENERATED:
            artifact_invalid = plan.artifact_id is None
        elif plan.status in {
            ResourcePlanStatus.PLANNED,
            ResourcePlanStatus.GENERATING,
            ResourcePlanStatus.FAILED,
        }:
            artifact_invalid = plan.artifact_id is not None
        if artifact_invalid:
            out.append(issue("RESOURCE_ARTIFACT_STATUS_MISMATCH", Severity.ERROR, path, f"resource status {plan.status.value} conflicts with artifact_id"))

    return _report(out)


def validate_candidate_contract(
    request: LearningGraphRequest,
    candidate: CandidateBlueprint,
) -> ValidationReport:
    """Validate model-owned keys, references, and cross-revision identities before compile."""
    # Existing graph integrity is a separate preflight gate; candidate validation stays candidate-scoped.
    out: list[ValidationIssue] = []

    node_keys = [x.key for x in candidate.nodes]
    unit_keys = [x.key for x in candidate.learning_units]
    resource_keys = [x.key for x in candidate.resource_plans]
    node_set = set(node_keys)

    for code, path, values in [
        ("DUPLICATE_NODE_KEY", "$.nodes", node_keys),
        ("DUPLICATE_UNIT_KEY", "$.learning_units", unit_keys),
        ("DUPLICATE_RESOURCE_PLAN_KEY", "$.resource_plans", resource_keys),
    ]:
        if dup := _duplicates(values):
            out.append(issue(code, Severity.ERROR, path, f"duplicate candidate keys: {dup}", True))

    node_continuities = [x.continuity_key for x in candidate.nodes if x.continuity_key]
    unit_continuities = [x.continuity_key for x in candidate.learning_units if x.continuity_key]
    if dup := _duplicates(node_continuities):
        out.append(issue("DUPLICATE_NODE_CONTINUITY_KEY", Severity.ERROR, "$.nodes", f"duplicate continuity keys: {dup}", True))
    if dup := _duplicates(unit_continuities):
        out.append(issue("DUPLICATE_UNIT_CONTINUITY_KEY", Severity.ERROR, "$.learning_units", f"duplicate continuity keys: {dup}", True))

    assignments = Counter()
    for index, unit in enumerate(candidate.learning_units):
        for node_key in unit.node_keys:
            if node_key not in node_set:
                out.append(issue("UNKNOWN_UNIT_NODE_KEY", Severity.ERROR, f"$.learning_units[{index}].node_keys", f"unknown candidate node key: {node_key}", True))
            assignments[node_key] += 1
    for key in node_keys:
        if assignments[key] == 0:
            out.append(issue("NODE_NOT_ASSIGNED_TO_UNIT", Severity.ERROR, "$.learning_units", f"candidate node is not assigned to a unit: {key}", True))
        elif assignments[key] > 1:
            out.append(issue("NODE_ASSIGNED_TO_MULTIPLE_UNITS", Severity.ERROR, "$.learning_units", f"candidate node belongs to multiple units: {key}", True))

    edge_signatures = []
    for index, edge in enumerate(candidate.edges):
        if edge.source_key not in node_set:
            out.append(issue("UNKNOWN_EDGE_SOURCE_KEY", Severity.ERROR, f"$.edges[{index}].source_key", f"unknown source key: {edge.source_key}", True))
        if edge.target_key not in node_set:
            out.append(issue("UNKNOWN_EDGE_TARGET_KEY", Severity.ERROR, f"$.edges[{index}].target_key", f"unknown target key: {edge.target_key}", True))
        edge_signatures.append((edge.source_key, edge.target_key, edge.edge_type.value))
    if dup := _duplicates(edge_signatures):
        out.append(issue("DUPLICATE_EDGE", Severity.ERROR, "$.edges", f"duplicate candidate edges: {dup}", True))

    registry = CapabilityRegistry.from_items(request.resource_capabilities)
    for index, plan in enumerate(candidate.resource_plans):
        if plan.node_key not in node_set:
            out.append(issue("UNKNOWN_RESOURCE_NODE_KEY", Severity.ERROR, f"$.resource_plans[{index}].node_key", f"unknown resource node key: {plan.node_key}", True))
        if not registry.supports(plan.resource_type):
            out.append(issue("UNSUPPORTED_RESOURCE_TYPE", Severity.ERROR, f"$.resource_plans[{index}].resource_type", f"unsupported resource type: {plan.resource_type.value}", True))

    valid_refs = {x.ref_id for x in request.source_refs}
    node_resolution_by_key = {}
    existing = request.existing_graph_summary
    for index, node in enumerate(candidate.nodes):
        unknown_refs = set(node.source_ref_ids) - valid_refs
        if unknown_refs:
            out.append(issue("SOURCE_REFERENCE_MISSING", Severity.ERROR, f"$.nodes[{index}].source_ref_ids", f"unknown source refs: {sorted(unknown_refs)}", True))

        if existing is not None:
            resolution = resolve_existing_node_identity(existing, node)
            node_resolution_by_key[node.key] = resolution
            if resolution.conflict:
                out.append(issue("NODE_IDENTITY_CONFLICT", Severity.ERROR, f"$.nodes[{index}]", "continuity_key and active normalized title resolve to different existing nodes", False))
                if request.update_mode == UpdateMode.MERGE:
                    out.append(issue("MERGE_IDENTITY_CONFLICT", Severity.ERROR, f"$.nodes[{index}]", "merge node identity is forked", False))
            matched = resolution.matched
            if request.update_mode == UpdateMode.MERGE and matched and matched.node_type != node.node_type:
                out.append(issue("MERGE_NODE_CONFLICT", Severity.ERROR, f"$.nodes[{index}].node_type", "merge candidate conflicts with existing node_type", False))

        if request.update_mode == UpdateMode.REPLACE_SOURCE:
            resolution = node_resolution_by_key.get(node.key)
            old = resolution.matched if resolution else None
            affected_old = bool(old and set(old.source_refs).intersection(request.replaced_source_ref_ids))
            new_in_scope = bool(set(node.source_ref_ids).intersection(request.replaced_source_ref_ids))
            if old is None and not new_in_scope:
                out.append(issue("REPLACE_SOURCE_SCOPE_VIOLATION", Severity.ERROR, f"$.nodes[{index}]", "new replacement node must reference a replaced source", True))
            elif old is not None and not affected_old and not new_in_scope:
                pass

    if existing is not None:
        for index, unit in enumerate(candidate.learning_units):
            resolution = resolve_existing_unit_identity(existing, unit)
            if resolution.conflict:
                out.append(issue("UNIT_IDENTITY_CONFLICT", Severity.ERROR, f"$.learning_units[{index}]", "continuity_key and active normalized title resolve to different existing units", False))
                if request.update_mode == UpdateMode.MERGE:
                    out.append(issue("MERGE_UNIT_IDENTITY_CONFLICT", Severity.ERROR, f"$.learning_units[{index}]", "merge unit identity is forked", False))

        candidate_node_by_key = {node.key: node for node in candidate.nodes}
        for index, plan in enumerate(candidate.resource_plans):
            node_resolution = node_resolution_by_key.get(plan.node_key)
            matched_node = node_resolution.matched if node_resolution and not node_resolution.conflict else None
            if matched_node is None:
                continue
            resolution = resolve_existing_resource_identity(existing, plan, matched_node.node_id)
            if resolution.conflict:
                out.append(issue("RESOURCE_PLAN_IDENTITY_CONFLICT", Severity.ERROR, f"$.resource_plans[{index}]", "resource continuity_key and strict semantic identity resolve to different existing plans", False))

    return _report(out)

def _public_forbidden_key_issues(request, graph):
    # Import locally to keep the serializer independent from validation internals.
    from .serializer import build_public_view, public_payload

    payload = public_payload(build_public_view(graph))
    dumped = json.dumps(payload, ensure_ascii=False).lower()
    out = []
    for key in request.constraints.forbidden_public_keys:
        if re.search(rf'"{re.escape(key.lower())}"\s*:', dumped):
            out.append(
                issue(
                    "PRIVATE_DATA_LEAK",
                    Severity.ERROR,
                    "$",
                    f"private key leaked through public serializer: {key}",
                    False,
                )
            )
    return out


def validate_blueprint(
    request: LearningGraphRequest,
    graph: LearningGraphBlueprint,
) -> ValidationReport:
    out: list[ValidationIssue] = []
    ids = [n.node_id for n in graph.nodes]
    idset = set(ids)
    unit_ids = [u.unit_id for u in graph.learning_units]
    edge_ids = [e.edge_id for e in graph.edges]
    plan_ids = [r.resource_plan_id for r in graph.resource_plans]
    active_nodes = [n for n in graph.nodes if n.status != NodeStatus.SUPERSEDED]
    active_ids = [n.node_id for n in active_nodes]
    active_idset = set(active_ids)
    enabled = CapabilityRegistry.from_items(request.resource_capabilities)

    for code, path, values in [
        ("DUPLICATE_NODE_ID", "$.nodes", ids),
        ("DUPLICATE_UNIT_ID", "$.learning_units", unit_ids),
        ("DUPLICATE_EDGE_ID", "$.edges", edge_ids),
        ("DUPLICATE_RESOURCE_PLAN_ID", "$.resource_plans", plan_ids),
    ]:
        if dup := _duplicates(values):
            out.append(issue(code, Severity.ERROR, path, f"duplicate ids: {dup}", True))

    meanings = defaultdict(list)
    for node in active_nodes:
        meanings[normalized_meaning(node.title)].append(node.node_id)
    for key, values in meanings.items():
        if key and len(values) > 1:
            out.append(
                issue(
                    "DUPLICATE_NODE_MEANING",
                    Severity.ERROR,
                    "$.nodes",
                    f"duplicate meaning: {values}",
                    True,
                )
            )

    seen_edges = set()
    related = Counter()
    for index, edge in enumerate(graph.edges):
        path = f"$.edges[{index}]"
        if edge.source_node_id not in idset or edge.target_node_id not in idset:
            out.append(
                issue(
                    "UNKNOWN_NODE_REFERENCE",
                    Severity.ERROR,
                    path,
                    "edge references unknown node",
                    True,
                )
            )
            continue
        if edge.source_node_id == edge.target_node_id:
            out.append(
                issue(
                    "SELF_DEPENDENCY",
                    Severity.ERROR,
                    path,
                    "self edge is forbidden",
                    True,
                )
            )
        signature = (edge.source_node_id, edge.target_node_id, edge.edge_type)
        if signature in seen_edges:
            out.append(
                issue(
                    "DUPLICATE_EDGE",
                    Severity.ERROR,
                    path,
                    "duplicate edge",
                    True,
                )
            )
        seen_edges.add(signature)
        related[edge.source_node_id] += 1
        related[edge.target_node_id] += 1

    active_edges = [
        edge
        for edge in graph.edges
        if edge.source_node_id in active_idset and edge.target_node_id in active_idset
    ]
    cyc = cycle_nodes(active_ids, active_edges)
    if cyc:
        out.append(
            issue(
                "PREREQUISITE_CYCLE",
                Severity.ERROR,
                "$.edges",
                f"prerequisite cycle: {cyc}",
                True,
            )
        )
    if len(active_ids) > 1:
        for node in active_nodes:
            if related[node.node_id] == 0:
                out.append(
                    issue(
                        "ORPHAN_NODE",
                        Severity.ERROR,
                        f"$.nodes[{node.node_id}]",
                        "node has no relation",
                        True,
                    )
                )
    if len(active_ids) >= 4 and not any(
        node.node_type == NodeType.FOUNDATION for node in active_nodes
    ):
        out.append(
            issue(
                "MISSING_FOUNDATION",
                Severity.WARNING,
                "$.nodes",
                "no foundation node",
            )
        )

    valid_refs = {r.ref_id for r in request.source_refs}
    node_plans = defaultdict(list)
    for plan in graph.resource_plans:
        node_plans[plan.node_id].append(plan)

    for node in graph.nodes:
        path = f"$.nodes[{node.node_id}]"
        incomplete = not (
            node.learning_objectives and node.key_concepts and node.mastery_evidence
        )
        if incomplete and node.status in {NodeStatus.COMPLETED, NodeStatus.LEARNING}:
            out.append(
                issue(
                    "EXISTING_NODE_CONTRACT_INCOMPLETE",
                    Severity.REVIEW_REQUIRED,
                    path,
                    "historical completed/learning node remains incomplete and was not fabricated",
                    False,
                )
            )
        elif node.status != NodeStatus.SUPERSEDED:
            if not node.learning_objectives:
                out.append(
                    issue(
                        "MISSING_LEARNING_OBJECTIVE",
                        Severity.ERROR,
                        path,
                        "learning objective missing",
                        True,
                    )
                )
            if not node.key_concepts:
                out.append(
                    issue(
                        "MISSING_KEY_CONCEPT",
                        Severity.ERROR,
                        path,
                        "key concept missing",
                        True,
                    )
                )
            if not node.mastery_evidence:
                out.append(
                    issue(
                        "MISSING_MASTERY_EVIDENCE",
                        Severity.ERROR,
                        path,
                        "mastery evidence missing",
                        True,
                    )
                )

        if node.status != NodeStatus.SUPERSEDED:
            if valid_refs and not node.source_refs:
                out.append(
                    issue(
                        "SOURCE_REFERENCE_MISSING",
                        Severity.ERROR,
                        path,
                        "node has no source reference",
                        False,
                    )
                )
            if any(ref not in valid_refs for ref in node.source_refs):
                out.append(
                    issue(
                        "SOURCE_REFERENCE_MISSING",
                        Severity.ERROR,
                        path,
                        "node references unknown source",
                        True,
                    )
                )
            if len(node_plans[node.node_id]) > request.constraints.max_resources_per_node:
                out.append(
                    issue(
                        "RESOURCE_BUDGET_EXCEEDED",
                        Severity.ERROR,
                        path,
                        "too many resource plans",
                        True,
                    )
                )
        if request.update_mode == UpdateMode.CREATE and node.status in {
            NodeStatus.COMPLETED,
            NodeStatus.LEARNING,
        }:
            out.append(
                issue(
                    "UNAUTHORIZED_COMPLETION_STATE",
                    Severity.ERROR,
                    path,
                    "initial graph cannot be completed or learning",
                    True,
                )
            )

    for index, plan in enumerate(graph.resource_plans):
        path = f"$.resource_plans[{index}]"
        if plan.node_id not in idset:
            out.append(
                issue(
                    "RESOURCE_NODE_MISMATCH",
                    Severity.ERROR,
                    path,
                    "resource references unknown node",
                    True,
                )
            )
        if not enabled.supports(plan.resource_type):
            out.append(
                issue(
                    "UNSUPPORTED_RESOURCE_TYPE",
                    Severity.ERROR,
                    path,
                    f"unsupported resource {plan.resource_type}",
                    True,
                )
            )
        if plan.status not in {
            ResourcePlanStatus.PLANNED,
            ResourcePlanStatus.GENERATED,
            ResourcePlanStatus.GENERATING,
        }:
            out.append(
                issue(
                    "INVALID_RESOURCE_STATUS",
                    Severity.ERROR,
                    path,
                    "invalid lazy generation state",
                    True,
                )
            )
        if request.update_mode == UpdateMode.CREATE and (
            plan.status != ResourcePlanStatus.PLANNED or plan.artifact_id is not None
        ):
            out.append(
                issue(
                    "LAZY_GENERATION_VIOLATION",
                    Severity.ERROR,
                    path,
                    "initial resource must remain planned without artifact",
                    True,
                )
            )
        if plan.status == ResourcePlanStatus.GENERATED and plan.artifact_id is None:
            out.append(
                issue(
                    "GENERATED_RESOURCE_ARTIFACT_MISSING",
                    Severity.REVIEW_REQUIRED,
                    path,
                    "generated resource has no artifact_id",
                )
            )

    if len(active_ids) < request.constraints.min_nodes:
        out.append(
            issue(
                "GRAPH_TOO_SHALLOW",
                Severity.ERROR,
                "$.nodes",
                "below minimum node count",
                True,
            )
        )
    if len(active_ids) > request.constraints.max_nodes:
        out.append(
            issue(
                "GRAPH_TOO_FRAGMENTED",
                Severity.ERROR,
                "$.nodes",
                "above node budget",
                True,
            )
        )
    if len(graph.edges) > request.constraints.max_edges:
        out.append(
            issue(
                "GRAPH_TOO_DENSE",
                Severity.ERROR,
                "$.edges",
                "above edge budget",
                True,
            )
        )
    depth = graph_depth(active_ids, active_edges)
    if depth > request.constraints.max_depth:
        out.append(
            issue(
                "GRAPH_TOO_DEEP",
                Severity.ERROR,
                "$.edges",
                "above depth budget",
                True,
            )
        )
    if len(active_ids) >= 5 and depth <= 1:
        out.append(
            issue(
                "GRAPH_TOO_SHALLOW",
                Severity.WARNING,
                "$.edges",
                "graph has no meaningful prerequisite depth",
                True,
            )
        )

    unit_nodes = []
    for index, unit in enumerate(graph.learning_units):
        for node_id in unit.node_ids:
            if node_id not in idset:
                out.append(
                    issue(
                        "UNKNOWN_NODE_REFERENCE",
                        Severity.ERROR,
                        f"$.learning_units[{index}]",
                        "unit references unknown node",
                        True,
                    )
                )
            unit_nodes.append(node_id)
    for node_id in idset:
        if unit_nodes.count(node_id) != 1:
            out.append(
                issue(
                    "UNIT_NODE_MISMATCH",
                    Severity.ERROR,
                    "$.learning_units",
                    f"node must occur in exactly one unit: {node_id}",
                    True,
                )
            )
    unit_map = {
        node_id: unit.unit_id
        for unit in graph.learning_units
        for node_id in unit.node_ids
    }
    for node in graph.nodes:
        if node.unit_id != unit_map.get(node.node_id):
            out.append(
                issue(
                    "UNIT_NODE_MISMATCH",
                    Severity.ERROR,
                    f"$.nodes[{node.node_id}].unit_id",
                    "node.unit_id disagrees with learning unit membership",
                    True,
                )
            )

    combined = normalized_meaning(
        " ".join(
            [node.title + " " + " ".join(node.learning_objectives) for node in graph.nodes]
        )
    )
    goal = normalized_meaning(request.user_goal)
    if goal and not any(ch in combined for ch in goal[: min(8, len(goal))]):
        out.append(
            issue(
                "GOAL_COVERAGE_WEAK",
                Severity.WARNING,
                "$.nodes",
                "goal terms are weakly represented",
            )
        )

    out.extend(_public_forbidden_key_issues(request, graph))
    roots, terminals = roots_and_terminals(active_ids, active_edges)
    return _report(out, depth=depth, roots=roots, terminals=terminals)
