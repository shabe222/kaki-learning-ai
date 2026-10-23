"""Strict v0.1.3 adapters for the existing formal learning-graph aggregate."""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Iterable

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agents.learning_graph_orchestrator.enums import (
    EdgeType,
    NodeStatus,
    ResourcePlanStatus,
    ResourceType,
)
from agents.learning_graph_orchestrator.models import (
    ExistingEdgeSummary,
    ExistingGraphSummary,
    ExistingNodeSummary,
    ExistingResourcePlanSummary,
    ExistingUnitSummary,
    LearningGraphBlueprint,
    LearningGraphRequest,
    ResourceCapability,
    ValidationIssue,
    ValidationReport,
)
from agents.learning_graph_orchestrator.runner import (
    ExistingGraphContractError,
    LearningGraphOrchestrator,
)
from agents.learning_graph_orchestrator.serializer import build_public_view
from agents.learning_graph_orchestrator.enums import Severity
from database.models import (
    GeneratedItem,
    GraphKnowledgeEdge,
    GraphLearningUnit,
    GraphNode,
    GraphOrchestrationRun,
    GraphResourcePlan,
    LearningGraph,
    WorkspaceConversationMaterial,
    WorkspaceMaterial,
    WorkspaceMaterialChunk,
)
from services.workspace_agent_adapters import AGENT_ADAPTERS
from services.workspace_task_contracts import AgentType


_RESOURCE_AGENTS = {
    ResourceType.HANDOUT: AgentType.LECTURE,
    ResourceType.QUESTION: AgentType.EXAM,
    ResourceType.CODE: AgentType.CODE,
    ResourceType.MIND_MAP: AgentType.MINDMAP,
    ResourceType.INTERACTIVE_CLASS: AgentType.PRESENTATION,
    ResourceType.PPT: AgentType.PRESENTATION,
}


def build_resource_capabilities() -> list[ResourceCapability]:
    """Project only agents that the executable strict task registry enables."""
    return [
        ResourceCapability(
            resource_type=resource_type,
            agent_key=agent_type.value,
            enabled=True,
            constraints={"lazy_generation": True},
        )
        for resource_type, agent_type in _RESOURCE_AGENTS.items()
        if agent_type in AGENT_ADAPTERS
    ]


def build_topic_request_payload(
    *, request_id: str, topic: str, domain: str, locale: str = "zh-CN",
    resource_capabilities: Iterable[ResourceCapability] | None = None,
) -> dict[str, Any]:
    """Build a deterministic source contract for a user-entered topic."""
    identity = hashlib.sha256(
        f"{domain.strip()}\0{topic.strip()}".encode("utf-8")
    ).hexdigest()[:24]
    source_id = f"topic-{identity}"
    outline_id = f"outline-{identity}"
    capabilities = list(resource_capabilities or build_resource_capabilities())
    return {
        "request_id": request_id,
        "graph_id": None,
        "graph_revision": 0,
        "user_goal": topic,
        "domain": domain,
        "locale": locale,
        "source_refs": [{
            "ref_id": source_id,
            "kind": "topic",
            "title": topic,
            "content_hash": None,
            "metadata": {},
        }],
        "source_outline": [{
            "outline_id": outline_id,
            "title": topic,
            "level": 1,
            "order": 0,
            "parent_outline_id": None,
            "source_ref_id": source_id,
            "page_start": None,
            "page_end": None,
        }],
        "source_summaries": [],
        "existing_graph_summary": None,
        "resource_capabilities": [item.model_dump(mode="json") for item in capabilities],
        "constraints": {
            "requested_resource_types": [item.resource_type.value for item in capabilities],
        },
        "update_mode": "CREATE",
        "replaced_source_ref_ids": [],
    }


async def build_material_request_payload(
    db: AsyncSession, *, user_id: int, thread_id: str, request_id: str,
    user_goal: str, domain: str, source_material_ids: list[str],
    locale: str = "zh-CN", resource_capabilities: Iterable[ResourceCapability] | None = None,
) -> dict[str, Any]:
    """Project owned material structure and bounded local chunks without paths."""
    requested = list(dict.fromkeys(source_material_ids))
    rows = list((await db.scalars(
        select(WorkspaceMaterial)
        .join(
            WorkspaceConversationMaterial,
            WorkspaceConversationMaterial.material_id == WorkspaceMaterial.material_id,
        )
        .where(
            WorkspaceMaterial.user_id == user_id,
            WorkspaceMaterial.material_id.in_(requested),
            WorkspaceMaterial.parse_status == "ready",
            WorkspaceConversationMaterial.user_id == user_id,
            WorkspaceConversationMaterial.thread_id == thread_id,
            WorkspaceConversationMaterial.is_active.is_(True),
        )
    )).all())
    by_id = {row.material_id: row for row in rows}
    missing = [item for item in requested if item not in by_id]
    if missing:
        raise PermissionError("material is not owned and bound to the current conversation")

    chunks = list((await db.scalars(select(WorkspaceMaterialChunk).where(
        WorkspaceMaterialChunk.user_id == user_id,
        WorkspaceMaterialChunk.material_id.in_(requested),
    ).order_by(
        WorkspaceMaterialChunk.material_id,
        WorkspaceMaterialChunk.chunk_index,
    ))).all())
    chunks_by_material: dict[str, list[WorkspaceMaterialChunk]] = {item: [] for item in requested}
    for chunk in chunks:
        if len(chunks_by_material[chunk.material_id]) < 4:
            chunks_by_material[chunk.material_id].append(chunk)

    source_refs: list[dict[str, Any]] = []
    outline: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    order = 0
    for material_id in requested:
        material = by_id[material_id]
        source_refs.append({
            "ref_id": material.material_id,
            "kind": material.file_type,
            "title": material.file_name,
            "content_hash": None,
            "metadata": {
                "page_count": material.page_count,
                "section_count": material.section_count,
            },
        })
        structure = list(material.structure or []) or [{
            "heading": material.file_name, "level": 1,
            "page_start": None, "page_end": None,
        }]
        parent_by_level: dict[int, str] = {}
        outline_ids: list[str] = []
        for index, section in enumerate(structure[:120]):
            level = max(1, min(8, int(section.get("level") or 1)))
            outline_id = f"{material_id}.o{index}"
            parent_id = parent_by_level.get(level - 1)
            outline.append({
                "outline_id": outline_id,
                "title": str(section.get("heading") or f"Section {index + 1}")[:200],
                "level": level,
                "order": order,
                "parent_outline_id": parent_id,
                "source_ref_id": material_id,
                "page_start": section.get("page_start"),
                "page_end": section.get("page_end"),
            })
            order += 1
            outline_ids.append(outline_id)
            parent_by_level[level] = outline_id
            for stale_level in [item for item in parent_by_level if item > level]:
                parent_by_level.pop(stale_level, None)
        root_outline_id = outline_ids[0]
        for chunk in chunks_by_material[material_id]:
            text = str(chunk.content or "").strip()[:4000]
            if len(text) < 4:
                continue
            summaries.append({
                "summary_id": f"{material_id}.s{chunk.chunk_index}",
                "source_ref_id": material_id,
                "outline_id": root_outline_id,
                "text": text,
                "key_terms": [],
            })

    capabilities = list(resource_capabilities or build_resource_capabilities())
    return {
        "request_id": request_id,
        "graph_id": None,
        "graph_revision": 0,
        "user_goal": user_goal,
        "domain": domain,
        "locale": locale,
        "source_refs": source_refs,
        "source_outline": outline,
        "source_summaries": summaries,
        "existing_graph_summary": None,
        "resource_capabilities": [item.model_dump(mode="json") for item in capabilities],
        "constraints": {
            "requested_resource_types": [item.resource_type.value for item in capabilities],
        },
        "update_mode": "CREATE",
        "replaced_source_ref_ids": [],
    }


async def build_formal_request(
    db: AsyncSession, *, user_id: int, thread_id: str, request_id: str,
    user_goal: str, domain: str, source_material_ids: list[str] | None = None,
    locale: str = "zh-CN", update_mode: str = "CREATE", graph_id: str | None = None,
    graph_revision: int = 0, replaced_source_ref_ids: list[str] | None = None,
) -> LearningGraphRequest:
    capabilities = build_resource_capabilities()
    if source_material_ids:
        payload = await build_material_request_payload(
            db,
            user_id=user_id,
            thread_id=thread_id,
            request_id=request_id,
            user_goal=user_goal,
            domain=domain,
            source_material_ids=source_material_ids,
            locale=locale,
            resource_capabilities=capabilities,
        )
    else:
        payload = build_topic_request_payload(
            request_id=request_id,
            topic=user_goal,
            domain=domain,
            locale=locale,
            resource_capabilities=capabilities,
        )
    payload["update_mode"] = update_mode
    payload["graph_id"] = graph_id
    payload["graph_revision"] = graph_revision
    payload["replaced_source_ref_ids"] = list(replaced_source_ref_ids or [])
    if update_mode != "CREATE":
        if not graph_id:
            raise ValueError("graph_id is required for update mode")
        existing = await load_existing_graph_summary(
            db, user_id=user_id, graph_id=graph_id,
        )
        if graph_revision != existing.graph_revision:
            raise ValueError("revision conflict before Provider invocation")
        # The blueprint validator checks every preserved node source against
        # the complete request source contract. Keep prior opaque identities
        # available without inventing titles, content or private metadata.
        known_source_ids = {
            str(item.get("ref_id") or "") for item in payload["source_refs"]
        }
        existing_source_ids = sorted({
            str(source_ref)
            for node in existing.nodes
            for source_ref in node.source_refs
            if str(source_ref).strip()
        })
        missing_source_ids = [
            source_ref
            for source_ref in existing_source_ids
            if source_ref not in known_source_ids
        ]
        if len(payload["source_refs"]) + len(missing_source_ids) > 40:
            raise ValueError("existing graph source identity set exceeds request limit")
        payload["source_refs"].extend({
            "ref_id": source_ref,
            "kind": "existing_graph",
            "title": None,
            "content_hash": None,
            "metadata": {},
        } for source_ref in missing_source_ids)
        payload["existing_graph_summary"] = existing.model_dump(mode="json")
    return LearningGraphRequest.model_validate(payload)


def _existing_error(code: str, path: str, message: str) -> ExistingGraphContractError:
    return ExistingGraphContractError(ValidationReport(
        valid=False,
        issues=[ValidationIssue(
            code=code,
            severity=Severity.ERROR,
            path=path,
            message=message,
            repairable=False,
        )],
    ))


async def _owned_graph(db: AsyncSession, user_id: int, graph_id: str) -> LearningGraph:
    graph = await db.scalar(select(LearningGraph).where(
        LearningGraph.user_id == user_id,
        LearningGraph.external_id == graph_id,
    ))
    if graph is None:
        exists_for_another_user = await db.scalar(select(LearningGraph.id).where(
            LearningGraph.external_id == graph_id,
        ))
        if exists_for_another_user is not None:
            raise PermissionError("learning graph belongs to another user")
        raise LookupError("learning graph not found")
    return graph


async def load_existing_graph_summary(
    db: AsyncSession, *, user_id: int, graph_id: str,
) -> ExistingGraphSummary:
    """Load a full private summary without inventing legacy contract fields."""
    graph = await _owned_graph(db, user_id, graph_id)
    if not graph.graph_revision or graph.graph_revision < 1:
        raise _existing_error(
            "EXISTING_GRAPH_REVISION_MISSING", "$.existing_graph_summary.graph_revision",
            "legacy graph has no persisted formal revision",
        )

    units = list((await db.scalars(select(GraphLearningUnit).where(
        GraphLearningUnit.graph_id == graph.id,
        GraphLearningUnit.status != "superseded",
    ).order_by(GraphLearningUnit.unit_order, GraphLearningUnit.id))).all())
    nodes = list((await db.scalars(select(GraphNode).where(
        GraphNode.graph_id == graph.id,
        GraphNode.user_id == user_id,
    ).order_by(GraphNode.order_index, GraphNode.id))).all())
    edges = list((await db.scalars(select(GraphKnowledgeEdge).where(
        GraphKnowledgeEdge.graph_id == graph.id,
    ).order_by(GraphKnowledgeEdge.id))).all())
    plans = list((await db.scalars(select(GraphResourcePlan).where(
        GraphResourcePlan.graph_id == graph.id,
    ).order_by(GraphResourcePlan.id))).all())

    historical_incomplete = {
        NodeStatus.COMPLETED.value, NodeStatus.LEARNING.value, NodeStatus.SUPERSEDED.value,
    }
    for index, node in enumerate(nodes):
        incomplete = not (
            list(node.learning_objectives or [])
            and list(node.key_concepts or [])
            and list(node.mastery_evidence or [])
        )
        if incomplete and node.status not in historical_incomplete:
            raise _existing_error(
                "EXISTING_NODE_CONTRACT_INCOMPLETE",
                f"$.existing_graph_summary.nodes[{index}]",
                f"active existing node contract is incomplete: {node.external_id or node.id}",
            )

    node_external = {node.id: node.external_id for node in nodes}
    for plan in plans:
        if plan.status == ResourcePlanStatus.GENERATED.value:
            artifact = await db.scalar(select(GeneratedItem).where(
                GeneratedItem.id == plan.artifact_id,
                GeneratedItem.user_id == user_id,
            ))
            if artifact is None:
                raise _existing_error(
                    "RESOURCE_ARTIFACT_STATUS_MISMATCH", "$.existing_graph_summary.resource_plans",
                    f"generated plan lacks an owned GeneratedItem: {plan.external_id}",
                )

    summary = ExistingGraphSummary(
        graph_id=graph.external_id,
        graph_revision=graph.graph_revision,
        graph_title=graph.title,
        graph_summary=graph.graph_summary or "",
        learning_units=[ExistingUnitSummary(
            unit_id=item.external_id,
            continuity_key=item.continuity_key,
            title=item.title,
            summary=item.summary or "",
            node_ids=list(item.node_ids or []),
            unit_order=item.unit_order,
        ) for item in units],
        nodes=[ExistingNodeSummary(
            node_id=item.external_id or str(item.id),
            continuity_key=item.continuity_key,
            title=item.title,
            summary=item.summary if item.summary is not None else (item.description or ""),
            node_type=item.node_type or item.knowledge_type or "concept",
            learning_objectives=list(item.learning_objectives or []),
            key_concepts=list(item.key_concepts or []),
            mastery_evidence=list(item.mastery_evidence or []),
            difficulty_band=_difficulty_band(item),
            estimated_effort_minutes=item.estimated_minutes or 30,
            source_refs=list(item.source_refs or []),
            resource_plan_ids=list(item.resource_plan_ids or []),
            status=item.status,
            unit_id=item.unit_id,
        ) for item in nodes],
        edges=[ExistingEdgeSummary(
            edge_id=item.external_id,
            source_node_id=node_external[item.source_node_id],
            target_node_id=node_external[item.target_node_id],
            edge_type=item.edge_type,
            reason=item.reason or "",
        ) for item in edges],
        resource_plans=[ExistingResourcePlanSummary(
            resource_plan_id=item.external_id,
            continuity_key=item.continuity_key,
            node_id=node_external[item.node_id],
            resource_type=item.resource_type,
            purpose=item.purpose,
            generation_spec=dict(item.generation_spec or {}),
            required_inputs=list(item.required_inputs or []),
            trigger=item.trigger,
            status=item.status,
            priority=item.priority,
            artifact_id=item.artifact_id,
            private_annotations={
                **dict(item.private_annotations or {}),
                **({"execution_task_id": item.execution_task_id} if item.execution_task_id else {}),
                **({"failure_metadata": item.failure_metadata} if item.failure_metadata else {}),
            },
        ) for item in plans],
    )
    return summary


async def load_public_graph(
    db: AsyncSession, *, user_id: int, graph_id: str,
) -> dict[str, Any]:
    """Positive public projection of one persisted formal graph."""
    summary = await load_existing_graph_summary(
        db, user_id=user_id, graph_id=graph_id,
    )
    graph = await _owned_graph(db, user_id, graph_id)
    orchestration_status = str(
        dict(graph.source_detail or {}).get("orchestrator_status") or "valid"
    )
    return {
        "graph_id": summary.graph_id,
        "graph_revision": summary.graph_revision,
        "graph_title": summary.graph_title,
        "graph_summary": summary.graph_summary,
        "learning_units": [{
            "unit_id": item.unit_id,
            "title": item.title,
            "summary": item.summary,
            "node_ids": list(item.node_ids),
            "unit_order": item.unit_order,
        } for item in summary.learning_units],
        "nodes": [{
            "node_id": item.node_id,
            "title": item.title,
            "summary": item.summary,
            "node_type": item.node_type.value,
            "learning_objectives": list(item.learning_objectives),
            "key_concepts": list(item.key_concepts),
            "mastery_evidence": list(item.mastery_evidence),
            "difficulty_band": item.difficulty_band,
            "estimated_effort_minutes": item.estimated_effort_minutes,
            "source_refs": list(item.source_refs),
            "resource_plan_ids": list(item.resource_plan_ids),
            "status": item.status.value,
            "unit_id": item.unit_id,
        } for item in summary.nodes],
        "edges": [{
            "edge_id": item.edge_id,
            "source_node_id": item.source_node_id,
            "target_node_id": item.target_node_id,
            "edge_type": item.edge_type.value,
            "reason": item.reason,
        } for item in summary.edges],
        "resource_plans": [{
            "resource_plan_id": item.resource_plan_id,
            "node_id": item.node_id,
            "resource_type": item.resource_type.value,
            "purpose": item.purpose,
            "trigger": item.trigger.value,
            "status": item.status.value,
            "priority": item.priority,
            "artifact_id": item.artifact_id if item.status == ResourcePlanStatus.GENERATED else None,
        } for item in summary.resource_plans],
        "status": orchestration_status,
    }


def _difficulty_band(node: GraphNode) -> str:
    value = getattr(node, "difficulty_band", None)
    if value in {"introductory", "basic", "intermediate", "advanced"}:
        return value
    difficulty = int(node.difficulty or 2)
    return "introductory" if difficulty <= 1 else "basic" if difficulty <= 2 else "intermediate" if difficulty <= 4 else "advanced"


def _difficulty_number(value: str) -> int:
    return {"introductory": 1, "basic": 2, "intermediate": 3, "advanced": 5}[value]


async def _validate_artifact_binding(
    db: AsyncSession, *, user_id: int, graph_db_id: int,
    plan_external_id: str, plan_continuity: str, status: str, artifact_id: int | None,
) -> None:
    if status == ResourcePlanStatus.GENERATED.value:
        if artifact_id is None:
            raise ValueError("generated resource plan requires a real artifact")
        artifact = await db.scalar(select(GeneratedItem).where(
            GeneratedItem.id == artifact_id,
            GeneratedItem.user_id == user_id,
        ))
        if artifact is None:
            raise ValueError("generated resource plan artifact ownership mismatch")
        bound = await db.scalar(select(GraphResourcePlan).where(
            GraphResourcePlan.graph_id == graph_db_id,
            GraphResourcePlan.artifact_id == artifact_id,
        ))
        if bound and (
            bound.external_id != plan_external_id
            or bound.continuity_key != plan_continuity
        ):
            raise ValueError("artifact identity transfer is forbidden")
    elif artifact_id is not None:
        raise ValueError("non-generated resource plan cannot carry an artifact")


async def _save_blueprint_mutation(
    db: AsyncSession, *, user_id: int, blueprint: LearningGraphBlueprint,
    idempotency_key: str, request_hash: str,
) -> dict[str, Any]:
    """Persist a validated Blueprint in one commit with revision/idempotency gates."""
    if not idempotency_key or len(idempotency_key) > 140:
        raise ValueError("invalid idempotency key")
    if len(request_hash) != 64:
        raise ValueError("invalid request hash")

    existing_run = await db.scalar(select(GraphOrchestrationRun).where(
        GraphOrchestrationRun.user_id == user_id,
        GraphOrchestrationRun.idempotency_key == idempotency_key,
    ))
    if existing_run:
        if existing_run.request_hash != request_hash:
            raise ValueError("idempotency key was already used for a different request")
        if existing_run.status == "completed" and existing_run.result_public:
            return dict(existing_run.result_public)
        if existing_run.status != "provider_claimed":
            raise ValueError("idempotency request is already in progress")

    graph = await db.scalar(select(LearningGraph).where(
        LearningGraph.user_id == user_id,
        LearningGraph.external_id == blueprint.graph_id,
    ))
    if graph is None:
        if blueprint.graph_revision != 1:
            raise ValueError("revision conflict: a new graph must start at revision 1")
        primary_count = await db.scalar(select(func.count()).select_from(LearningGraph).where(
            LearningGraph.user_id == user_id,
            LearningGraph.is_primary.is_(True),
        ))
        graph = LearningGraph(
            user_id=user_id,
            external_id=blueprint.graph_id,
            graph_revision=0,
            title=blueprint.graph_title,
            goal=blueprint.graph_summary,
            goal_type="custom",
            source_type="orchestrator",
            source_detail={},
            status="active",
            is_primary=not bool(primary_count),
        )
        db.add(graph)
        await db.flush()
    else:
        expected_revision = blueprint.graph_revision - 1
        if blueprint.graph_revision != graph.graph_revision + 1:
            raise ValueError(
                f"revision conflict: expected {graph.graph_revision + 1}, got {blueprint.graph_revision}"
            )
        revision_claim = await db.execute(update(LearningGraph).where(
            LearningGraph.id == graph.id,
            LearningGraph.user_id == user_id,
            LearningGraph.graph_revision == expected_revision,
        ).values(graph_revision=blueprint.graph_revision))
        if revision_claim.rowcount != 1:
            raise ValueError("revision conflict: graph changed concurrently")
        graph.graph_revision = blueprint.graph_revision

    if existing_run is None:
        run = GraphOrchestrationRun(
            user_id=user_id,
            idempotency_key=idempotency_key,
            graph_id=graph.id,
            request_hash=request_hash,
            graph_revision=blueprint.graph_revision,
            status="claimed",
        )
        db.add(run)
    else:
        run = existing_run
        run.graph_id = graph.id
        run.graph_revision = blueprint.graph_revision
        run.status = "claimed"

    graph.title = blueprint.graph_title
    graph.graph_summary = blueprint.graph_summary
    graph.graph_revision = blueprint.graph_revision
    graph.total_nodes = len([node for node in blueprint.nodes if node.status != NodeStatus.SUPERSEDED])
    graph.completed_nodes = len([node for node in blueprint.nodes if node.status == NodeStatus.COMPLETED])
    graph.progress_pct = round(graph.completed_nodes * 100 / graph.total_nodes, 1) if graph.total_nodes else 0.0
    graph.source_detail = {
        **dict(graph.source_detail or {}),
        "orchestrator_version": "0.1.3",
        "orchestrator_status": blueprint.status,
    }

    unit_rows = {
        row.external_id: row for row in (await db.scalars(select(GraphLearningUnit).where(
            GraphLearningUnit.graph_id == graph.id,
        ))).all()
    }
    current_unit_ids = set()
    for item in blueprint.learning_units:
        current_unit_ids.add(item.unit_id)
        row = unit_rows.get(item.unit_id)
        if row is None:
            row = GraphLearningUnit(graph_id=graph.id, external_id=item.unit_id)
            db.add(row)
        row.continuity_key = item.continuity_key
        row.title = item.title
        row.summary = item.summary
        row.node_ids = list(item.node_ids)
        row.unit_order = item.unit_order
        row.status = "active"
    for external_id in blueprint.changes.superseded_units:
        if external_id in unit_rows:
            unit_rows[external_id].status = "superseded"

    node_rows = {
        row.external_id: row for row in (await db.scalars(select(GraphNode).where(
            GraphNode.graph_id == graph.id,
            GraphNode.user_id == user_id,
        ))).all() if row.external_id
    }
    for index, item in enumerate(blueprint.nodes):
        row = node_rows.get(item.node_id)
        if row is None:
            row = GraphNode(
                graph_id=graph.id,
                user_id=user_id,
                external_id=item.node_id,
                title=item.title,
            )
            db.add(row)
            node_rows[item.node_id] = row
        row.continuity_key = item.continuity_key
        row.unit_id = item.unit_id
        row.title = item.title
        row.summary = item.summary
        row.description = item.summary
        row.node_type = item.node_type.value
        row.knowledge_type = item.node_type.value
        row.learning_objectives = list(item.learning_objectives)
        row.key_concepts = list(item.key_concepts)
        row.mastery_evidence = list(item.mastery_evidence)
        row.difficulty = _difficulty_number(item.difficulty_band)
        row.estimated_minutes = item.estimated_effort_minutes
        row.source_refs = list(item.source_refs)
        row.resource_plan_ids = list(item.resource_plan_ids)
        row.status = item.status.value
        row.order_index = index
    await db.flush()

    await db.execute(delete(GraphKnowledgeEdge).where(GraphKnowledgeEdge.graph_id == graph.id))
    prerequisite_sources: dict[str, list[str]] = {item.node_id: [] for item in blueprint.nodes}
    for item in blueprint.edges:
        source = node_rows[item.source_node_id]
        target = node_rows[item.target_node_id]
        db.add(GraphKnowledgeEdge(
            graph_id=graph.id,
            external_id=item.edge_id,
            source_node_id=source.id,
            target_node_id=target.id,
            edge_type=item.edge_type.value,
            reason=item.reason,
        ))
        if item.edge_type == EdgeType.PREREQUISITE:
            prerequisite_sources[item.target_node_id].append(str(source.id))
    for node_external_id, dependencies in prerequisite_sources.items():
        node_rows[node_external_id].depends_on = dependencies

    plan_rows = {
        row.external_id: row for row in (await db.scalars(select(GraphResourcePlan).where(
            GraphResourcePlan.graph_id == graph.id,
        ))).all()
    }
    public_resources: dict[str, list[dict[str, Any]]] = {item.node_id: [] for item in blueprint.nodes}
    for item in blueprint.resource_plans:
        await _validate_artifact_binding(
            db,
            user_id=user_id,
            graph_db_id=graph.id,
            plan_external_id=item.resource_plan_id,
            plan_continuity=item.continuity_key,
            status=item.status.value,
            artifact_id=item.artifact_id,
        )
        row = plan_rows.get(item.resource_plan_id)
        if row is None:
            row = GraphResourcePlan(graph_id=graph.id, external_id=item.resource_plan_id)
            db.add(row)
            plan_rows[item.resource_plan_id] = row
        annotations = dict(item.private_annotations or {})
        row.continuity_key = item.continuity_key
        row.node_id = node_rows[item.node_id].id
        row.resource_type = item.resource_type.value
        row.purpose = item.purpose
        row.generation_spec = dict(item.generation_spec)
        row.required_inputs = list(item.required_inputs)
        row.trigger = item.trigger.value
        row.status = item.status.value
        row.priority = item.priority
        row.artifact_id = item.artifact_id
        row.execution_task_id = annotations.get("execution_task_id")
        row.failure_metadata = dict(annotations.get("failure_metadata") or {})
        row.thread_id = annotations.get("thread_id")
        row.private_annotations = annotations
        public_resources[item.node_id].append({
            "resource_plan_id": item.resource_plan_id,
            "type": item.resource_type.value,
            "title": item.purpose,
            "purpose": item.purpose,
            "trigger": item.trigger.value,
            "status": item.status.value,
            "priority": item.priority,
            "artifact_id": item.artifact_id if item.status == ResourcePlanStatus.GENERATED else None,
            "agent": _RESOURCE_AGENTS.get(item.resource_type, AgentType.LECTURE).value,
        })
    for node_external_id, resources in public_resources.items():
        node_rows[node_external_id].resources = resources

    public = build_public_view(blueprint).model_dump(mode="json")
    run.status = "completed"
    run.result_public = public
    await db.flush()
    return public


async def save_blueprint(
    db: AsyncSession, *, user_id: int, blueprint: LearningGraphBlueprint,
    idempotency_key: str, request_hash: str,
) -> dict[str, Any]:
    """Persist a Blueprint atomically without leaking failed partial writes."""
    async with db.begin_nested():
        public = await _save_blueprint_mutation(
            db,
            user_id=user_id,
            blueprint=blueprint,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return public


class FormalGraphProvider:
    """One-attempt adapter over the current formal LLM client."""

    name = "formal-llm"

    def __init__(self, llm, *, timeout_seconds: float = 180.0):
        self.llm = llm
        self.timeout_seconds = timeout_seconds
        self.model_id = str(getattr(getattr(llm, "config", None), "model", "configured"))

    async def generate(self, prompt: str) -> str:
        result = await asyncio.wait_for(
            self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=7000,
            ),
            timeout=self.timeout_seconds,
        )
        from services.llm_service_real import extract_llm_text
        return extract_llm_text(result)


async def _claim_provider_invocation(
    db: AsyncSession, *, user_id: int, idempotency_key: str,
    request_hash: str, graph_revision: int,
) -> tuple[GraphOrchestrationRun | None, dict[str, Any] | None]:
    """Durably claim one model invocation before any Provider work begins."""
    existing = await db.scalar(select(GraphOrchestrationRun).where(
        GraphOrchestrationRun.user_id == user_id,
        GraphOrchestrationRun.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ValueError("idempotency key was already used for a different request")
        if existing.status == "completed" and existing.result_public:
            return None, dict(existing.result_public)
        raise ValueError("idempotency request is already in progress or failed")

    run = GraphOrchestrationRun(
        user_id=user_id,
        idempotency_key=idempotency_key,
        graph_id=None,
        request_hash=request_hash,
        graph_revision=max(0, int(graph_revision)),
        status="provider_claimed",
    )
    db.add(run)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(select(GraphOrchestrationRun).where(
            GraphOrchestrationRun.user_id == user_id,
            GraphOrchestrationRun.idempotency_key == idempotency_key,
        ))
        if existing is not None and existing.request_hash == request_hash:
            if existing.status == "completed" and existing.result_public:
                return None, dict(existing.result_public)
            raise ValueError("idempotency request is already in progress or failed")
        raise ValueError("idempotency key conflict")
    return run, None


async def _mark_orchestration_failed(
    db: AsyncSession, *, user_id: int, idempotency_key: str, error_code: str,
) -> None:
    run = await db.scalar(select(GraphOrchestrationRun).where(
        GraphOrchestrationRun.user_id == user_id,
        GraphOrchestrationRun.idempotency_key == idempotency_key,
    ))
    if run is not None and run.status != "completed":
        run.status = "failed"
        run.error_code = error_code[:100]
        await db.commit()


async def orchestrate_and_save(
    db: AsyncSession, *, user_id: int, request: LearningGraphRequest,
    idempotency_key: str, provider=None,
) -> dict[str, Any]:
    request_json = request.model_dump(mode="json")
    request_hash = hashlib.sha256(
        json.dumps(request_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _, replay = await _claim_provider_invocation(
        db,
        user_id=user_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        graph_revision=int(getattr(request, "graph_revision", 0) or 0),
    )
    if replay is not None:
        return replay
    if provider is None:
        from config import settings
        from services.llm_service_real import get_llm_service
        provider = FormalGraphProvider(
            await get_llm_service(),
            timeout_seconds=float(getattr(settings, "LLM_TIMEOUT_SEC", 180)),
        )
    try:
        result = await LearningGraphOrchestrator(provider).run(request)
        if not result.validation.valid:
            raise ValueError("orchestrator returned invalid validation")
        return await save_blueprint(
            db,
            user_id=user_id,
            blueprint=result.blueprint,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
    except Exception as exc:
        await _mark_orchestration_failed(
            db,
            user_id=user_id,
            idempotency_key=idempotency_key,
            error_code=f"ORCHESTRATION_{type(exc).__name__.upper()}",
        )
        raise
