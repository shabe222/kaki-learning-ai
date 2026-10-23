"""Evidence-backed tutor context and deterministic teaching decisions.

This module is deliberately thin: it reads existing Graph/Evaluator/Learner State/
Profile/Material/Artifact facts and projects a program-owned next teaching action.
It does not create a new mastery, profile, graph, path, or material authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from typing import Any, Iterable

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AIImpression,
    ChatSession,
    GeneratedItem,
    GraphNode,
    GraphResourcePlan,
    LearningEvaluation,
    LearningEvaluationResultRecord,
    LearningGraph,
    StudentProfile,
    TutorSession,
    WorkspaceAgentTask,
    WorkspaceLearningEvent,
)
from services.normalized_learner_state import get_normalized_learner_state
from services.learning_personalization import build_personalized_learning_strategy
from services.workspace_agent_adapters import AGENT_ADAPTERS
from services.workspace_materials import MaterialAccessError, build_conversation_material_context
from services.workspace_task_contracts import AgentTask, AgentType, TaskState
from services.workspace_task_store import load_task_by_id, save_task


PATH_AUTHORITY_STATUS = "INDEPENDENT_VALIDATION_READY_BUT_ENVIRONMENT_BLOCKED"

_CONFUSION_RE = re.compile(
    r"(?:还是不懂|还不懂|没懂|不理解|没理解|糊涂|卡住|换个讲法|再讲|看不明白|不明白)",
    re.I,
)

_UNDERSTANDING_CLAIM_RE = re.compile(
    r"(?:我(?:已经|现在)?懂了|我(?:已经|现在)?明白了|我会了|我掌握了|我理解了|应该懂了|感觉懂了)",
    re.I,
)

_PEDAGOGY_LABELS = {
    "socratic_questioning": "苏格拉底式追问",
    "feynman_teach_back": "费曼复述法",
    "worked_example": "例题示范",
    "scaffolding": "支架式教学",
    "retrieval_practice": "检索练习",
    "transfer_challenge": "迁移挑战",
}

_PEDAGOGY_REQUEST_PATTERNS = (
    ("socratic_questioning", ("苏格拉底", "追问我", "用问题引导", "多问我为什么")),
    ("feynman_teach_back", ("费曼", "让我复述", "让我讲一遍", "让我解释给你")),
    ("worked_example", ("先给例题", "先看例题", "例题示范", "先给例子", "从例子开始")),
    ("scaffolding", ("一步一步", "拆开讲", "分步骤", "慢慢带我", "支架")),
    ("retrieval_practice", ("抽问我", "检索练习", "先别提示", "让我回忆", "闭卷回忆")),
    ("transfer_challenge", ("迁移挑战", "换个情境", "难一点", "挑战题")),
)

# A supporting strategy is selectable only when _resource_spec() has an explicit
# causal branch for the same (action, strategy) pair. Tests counterfactually
# compare every allowed pair against supporting_strategy=None.
_PEDAGOGY_SUPPORT_COMPATIBILITY: dict[str, frozenset[str]] = {
    "misconception_distinction": frozenset({"worked_example", "scaffolding", "feynman_teach_back"}),
    "reteach_scaffold": frozenset({"worked_example", "scaffolding", "socratic_questioning"}),
    "collect_more_evidence": frozenset({"feynman_teach_back", "socratic_questioning"}),
    "targeted_practice": frozenset({"worked_example", "scaffolding", "retrieval_practice"}),
    "transfer_challenge": frozenset({"feynman_teach_back", "retrieval_practice"}),
}


@dataclass
class TutorContext:
    user_id: int
    thread_id: str
    tutor_session_id: str
    session_goal: str | None = None
    current_object: dict[str, Any] | None = None
    graph: dict[str, Any] | None = None
    node: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None
    learner_state: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
    impressions: list[dict[str, Any]] = field(default_factory=list)
    material_context: dict[str, Any] = field(default_factory=lambda: {"material_ids": [], "text": "", "citations": []})
    evidence_status: dict[str, Any] = field(default_factory=dict)
    agent_capabilities: list[dict[str, Any]] = field(default_factory=list)
    path_authority_status: str = PATH_AUTHORITY_STATUS
    warnings: list[str] = field(default_factory=list)

    def public_payload(self) -> dict[str, Any]:
        """Bounded public/debug projection; raw material text remains server-side."""
        return {
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "tutor_session_id": self.tutor_session_id,
            "session_goal": self.session_goal,
            "current_object": self.current_object,
            "graph": self.graph,
            "node": self.node,
            "evaluation": self.evaluation,
            "learner_state": self.learner_state,
            "profile": self.profile,
            "impressions": self.impressions,
            "material_context": {
                "material_ids": list(self.material_context.get("material_ids") or []),
                "citations": list(self.material_context.get("citations") or []),
                "has_text": bool(self.material_context.get("text")),
            },
            "evidence_status": dict(self.evidence_status or {}),
            "agent_capabilities": self.agent_capabilities,
            "path_authority_status": self.path_authority_status,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class TutorDecision:
    action: str
    target_node_id: str | None
    target_object: dict[str, Any] | None
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    confidence: float
    uncertainty: float
    path_authority_status: str
    agent_task_spec: dict[str, Any] | None = None
    presentation_policy: dict[str, Any] | None = None

    def public_payload(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target_node_id": self.target_node_id,
            "target_object": self.target_object,
            "reason_codes": list(self.reason_codes),
            "evidence_refs": list(self.evidence_refs),
            "confidence": round(float(self.confidence), 3),
            "uncertainty": round(float(self.uncertainty), 3),
            "path_authority_status": self.path_authority_status,
            "agent_task_spec": dict(self.agent_task_spec or {}) or None,
            "presentation_policy": dict(self.presentation_policy or {}),
        }


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _graph_public(graph: LearningGraph) -> dict[str, Any]:
    return {
        "id": graph.id,
        "external_id": graph.external_id,
        "graph_revision": int(graph.graph_revision or 0),
        "title": graph.title,
        "goal": graph.goal,
        "status": graph.status,
        "is_primary": bool(graph.is_primary),
        "source": "formal_learning_graph",
    }


def _node_public(node: GraphNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "external_id": node.external_id or f"node-{node.id}",
        "title": node.title,
        "summary": node.summary or node.description or "",
        "status": node.status,
        "knowledge_type": node.knowledge_type,
        "difficulty": int(node.difficulty or 3),
        "learning_objectives": list(node.learning_objectives or []),
        "mastery_evidence": list(node.mastery_evidence or []),
        "source": "formal_graph_node",
    }


def _goal_match_score(goal: str, graph: LearningGraph) -> int:
    goal_norm = _text(goal).lower()
    title = _text(graph.title).lower()
    graph_goal = _text(graph.goal).lower()
    if not goal_norm:
        return 0
    score = 0
    if title and title in goal_norm:
        score += 5
    if goal_norm and goal_norm in title:
        score += 4
    if graph_goal and (graph_goal in goal_norm or goal_norm in graph_goal):
        score += 3
    if graph.is_primary:
        score += 1
    return score


async def resolve_formal_graph_node(
    db: AsyncSession,
    *,
    user_id: int,
    session: TutorSession,
) -> tuple[LearningGraph | None, GraphNode | None]:
    """Resolve only user-owned formal graph facts; static GraphCatalog is not authority."""
    graph: LearningGraph | None = None
    graph_ref = _text(session.graph_id)
    if graph_ref:
        conditions = [LearningGraph.external_id == graph_ref]
        if graph_ref.isdigit():
            conditions.append(LearningGraph.id == int(graph_ref))
        graph = await db.scalar(select(LearningGraph).where(
            LearningGraph.user_id == user_id,
            or_(*conditions),
        ))

    if graph is None:
        rows = list((await db.execute(
            select(LearningGraph)
            .where(LearningGraph.user_id == user_id, LearningGraph.status.in_(("active", "draft")))
            .order_by(desc(LearningGraph.is_primary), desc(LearningGraph.updated_at), desc(LearningGraph.id))
        )).scalars().all())
        ranked = sorted(rows, key=lambda item: _goal_match_score(session.goal, item), reverse=True)
        if ranked and _goal_match_score(session.goal, ranked[0]) >= 3:
            graph = ranked[0]

    if graph is None:
        return None, None

    node: GraphNode | None = None
    node_ref = _text(session.current_node_id)
    if node_ref:
        conditions = [GraphNode.external_id == node_ref]
        if node_ref.isdigit():
            conditions.append(GraphNode.id == int(node_ref))
        node = await db.scalar(select(GraphNode).where(
            GraphNode.graph_id == graph.id,
            GraphNode.user_id == user_id,
            or_(*conditions),
        ))

    if node is None:
        current_plan = next(
            (item for item in list(session.plan_nodes or []) if str(item.get("id")) == str(session.current_node_id)),
            None,
        )
        plan_title = _text((current_plan or {}).get("title"))
        if plan_title:
            node = await db.scalar(select(GraphNode).where(
                GraphNode.graph_id == graph.id,
                GraphNode.user_id == user_id,
                GraphNode.title == plan_title,
            ).limit(1))

    if node is None:
        node = await db.scalar(select(GraphNode).where(
            GraphNode.graph_id == graph.id,
            GraphNode.user_id == user_id,
            GraphNode.is_hidden.is_(False),
        ).order_by(
            # Prefer a currently active/review node, then stable graph order.
            GraphNode.order_index.asc(), GraphNode.id.asc(),
        ).limit(1))
    return graph, node


async def build_formal_tutor_plan(
    db: AsyncSession,
    *,
    user_id: int,
    goal: str,
    graph_ref: str | None = None,
) -> tuple[LearningGraph, list[dict[str, Any]]] | None:
    """Build the tutor compatibility plan from the existing formal graph when possible."""
    graph: LearningGraph | None = None
    if graph_ref:
        conditions = [LearningGraph.external_id == graph_ref]
        if str(graph_ref).isdigit():
            conditions.append(LearningGraph.id == int(graph_ref))
        graph = await db.scalar(select(LearningGraph).where(
            LearningGraph.user_id == user_id,
            or_(*conditions),
        ))
    if graph is None:
        rows = list((await db.execute(
            select(LearningGraph)
            .where(LearningGraph.user_id == user_id, LearningGraph.status.in_(("active", "draft")))
            .order_by(desc(LearningGraph.is_primary), desc(LearningGraph.updated_at), desc(LearningGraph.id))
        )).scalars().all())
        ranked = sorted(rows, key=lambda item: _goal_match_score(goal, item), reverse=True)
        if ranked and _goal_match_score(goal, ranked[0]) >= 3:
            graph = ranked[0]
    if graph is None:
        return None
    nodes = list((await db.execute(select(GraphNode).where(
        GraphNode.graph_id == graph.id,
        GraphNode.user_id == user_id,
        GraphNode.is_hidden.is_(False),
    ).order_by(GraphNode.order_index.asc(), GraphNode.id.asc()))).scalars().all())
    if not nodes:
        return None
    plan = []
    for node in nodes:
        resource_types = []
        for item in list(node.resources or []):
            if isinstance(item, dict) and item.get("type"):
                resource_types.append(str(item["type"]))
        plan.append({
            "id": str(node.external_id or node.id),
            "title": node.title,
            "estimated_minutes": int(node.estimated_minutes or 30),
            "recommended_resources": list(dict.fromkeys(resource_types)),
            "source": "formal_learning_graph",
            "graph_node_db_id": node.id,
        })
    return graph, plan


async def _owned_artifact(
    db: AsyncSession, *, user_id: int, thread_id: str, artifact_id: int | None,
) -> dict[str, Any] | None:
    if not artifact_id:
        return None
    item = await db.scalar(select(GeneratedItem).where(
        GeneratedItem.id == int(artifact_id),
        GeneratedItem.user_id == user_id,
    ))
    if item is None:
        return None
    metadata = dict(item.raw_data or {})
    owner_thread = _text(metadata.get("thread_id"))
    if owner_thread and owner_thread != thread_id:
        return None
    return {
        "kind": "artifact",
        "artifact_id": item.id,
        "artifact_kind": item.type,
        "title": item.title,
        "topic": item.chapter,
        "thread_id": owner_thread or thread_id,
        "graph_id": metadata.get("graph_id"),
        "graph_node_id": metadata.get("graph_node_external_id") or metadata.get("graph_node_id"),
        "source_material_ids": list(metadata.get("source_material_ids") or []),
        "source": "owned_generated_item",
    }


async def _formal_node_resource_artifact(
    db: AsyncSession,
    *,
    user_id: int,
    graph: LearningGraph | None,
    node: GraphNode | None,
) -> dict[str, Any] | None:
    """Resolve one generated formal resource for the current owned node.

    A Tutor task may be opened from ``我的学习`` without an explicit workspace
    artifact selection.  The graph resource plan is already the authority that
    binds that node to its GeneratedItem, so use that existing relation instead
    of issuing a new resource or inventing an evidence artifact.  This is a
    read-only fallback; explicit active artifacts still take precedence.
    """
    if graph is None or node is None:
        return None
    row = await db.execute(
        select(GraphResourcePlan, GeneratedItem)
        .join(GeneratedItem, GeneratedItem.id == GraphResourcePlan.artifact_id)
        .where(
            GraphResourcePlan.graph_id == graph.id,
            GraphResourcePlan.node_id == node.id,
            GraphResourcePlan.status == "generated",
            GraphResourcePlan.artifact_id.is_not(None),
            GeneratedItem.user_id == user_id,
        )
        .order_by(desc(GraphResourcePlan.priority), desc(GraphResourcePlan.updated_at), desc(GraphResourcePlan.id))
        .limit(1)
    )
    pair = row.first()
    if pair is None:
        return None
    plan, item = pair
    metadata = dict(item.raw_data or {})
    graph_ref = str(graph.external_id or graph.id)
    node_ref = str(node.external_id or node.id)
    # Fail closed if a stale plan somehow points to an artifact from another
    # graph/node.  The plan relation and the item's durable metadata must agree.
    if str(metadata.get("graph_id") or "") != graph_ref:
        return None
    if str(metadata.get("graph_node_external_id") or metadata.get("graph_node_id") or "") not in {node_ref, str(node.id)}:
        return None
    return {
        "kind": "artifact",
        "artifact_id": item.id,
        "artifact_kind": item.type,
        "title": item.title,
        "topic": item.chapter,
        "thread_id": metadata.get("thread_id"),
        "graph_id": graph_ref,
        "graph_node_id": node_ref,
        "source_material_ids": list(metadata.get("source_material_ids") or []),
        "resource_plan_id": plan.external_id,
        "source": "formal_graph_resource_binding",
    }


async def _profile_context(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    profile = await db.scalar(select(StudentProfile).where(StudentProfile.user_id == user_id))
    if profile is None:
        return None
    return {
        "preferred_language": profile.preferred_language,
        "cognitive_profile": dict(profile.cognitive_profile or {}),
        "behavior_pattern": dict(profile.behavior_pattern or {}),
        "motivation_profile": dict(profile.motivation_profile or {}),
        "misconception_archive": dict(profile.misconception_archive or {}),
        "profile_version": int(getattr(profile, "profile_version", 1) or 1),
        "confidence_score": float(getattr(profile, "confidence_score", 0.5) or 0.5),
        "authority": "existing_student_profile",
    }


async def _impression_context(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    rows = list((await db.execute(select(AIImpression).where(
        AIImpression.user_id == user_id,
        AIImpression.status.notin_(("rejected", "uncertain")),
    ).order_by(desc(AIImpression.confirmed_by_user), desc(AIImpression.confidence), desc(AIImpression.updated_at)))).scalars().all())
    output = []
    for item in rows[:8]:
        if not item.confirmed_by_user and float(item.confidence or 0) < 0.75:
            continue
        output.append({
            "impression_key": item.impression_key,
            "dimension": item.dimension,
            "summary": item.summary,
            "confidence": float(item.confidence or 0),
            "evidence_refs": list(item.evidence_refs or []),
            "status": item.status,
            "confirmed_by_user": bool(item.confirmed_by_user),
            "profile_mapping_key": item.profile_mapping_key,
            "authority": "user_confirmed_inference" if item.confirmed_by_user else "evidence_backed_inference",
        })
    return output


async def _formal_evidence_status(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    graph: LearningGraph | None,
    node: GraphNode | None,
    evaluation: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project user-visible evidence freshness without creating a mastery authority.

    WorkspaceLearningEvent is the immutable observation stream. The latest trusted
    Evaluator public record decides which event ids have actually been assessed.
    New product evidence therefore remains *pending* until a later trusted revision
    explicitly consumes it.
    """
    if graph is None or node is None:
        return {
            "state": "no_formal_object",
            "pending_count": 0,
            "pending_event_refs": [],
            "evaluated_event_count": 0,
            "latest_event_type": None,
            "latest_event_at": None,
            "evaluator_revision": int((evaluation or {}).get("evaluation_revision") or 0),
            "evaluator_result_ref": (evaluation or {}).get("result_ref"),
        }

    graph_refs = {str(graph.id), str(graph.external_id or "")} - {""}
    node_refs = {str(node.id), str(node.external_id or "")} - {""}
    rows = list((await db.execute(
        select(WorkspaceLearningEvent)
        .where(
            WorkspaceLearningEvent.user_id == user_id,
            WorkspaceLearningEvent.thread_id == thread_id,
        )
        .order_by(desc(WorkspaceLearningEvent.id))
        .limit(120)
    )).scalars().all())

    matching: list[WorkspaceLearningEvent] = []
    for row in rows:
        payload = dict(row.payload or {})
        event_node = str(payload.get("graph_node_external_id") or payload.get("graph_node_id") or "")
        if not event_node or event_node not in node_refs:
            continue
        event_graph = str(payload.get("graph_id") or "")
        if event_graph and event_graph not in graph_refs:
            continue
        matching.append(row)

    evaluated_ids: set[int] = set()
    for value in list((evaluation or {}).get("evidence_event_ids") or []):
        text = str(value or "").strip()
        if text.isdigit():
            evaluated_ids.add(int(text))
            continue
        for prefix in ("workspace-event-", "workspace-event:"):
            if text.startswith(prefix) and text[len(prefix):].isdigit():
                evaluated_ids.add(int(text[len(prefix):]))
                break
    pending = [row for row in matching if int(row.id) not in evaluated_ids]
    assessed = [row for row in matching if int(row.id) in evaluated_ids]
    latest = matching[0] if matching else None
    if pending:
        state = "evidence_pending"
    elif evaluation is not None:
        state = "trusted_evaluation"
    elif matching:
        state = "evidence_pending"
    else:
        state = "no_formal_evidence"
    return {
        "state": state,
        "pending_count": len(pending),
        "pending_event_refs": [f"workspace-event:{row.id}" for row in pending[:8]],
        "evaluated_event_count": len(assessed),
        "latest_event_type": latest.event_type if latest else None,
        "latest_event_at": latest.created_at.isoformat() if latest and latest.created_at else None,
        "evaluator_revision": int((evaluation or {}).get("evaluation_revision") or 0),
        "evaluator_result_ref": (evaluation or {}).get("result_ref"),
    }


async def _latest_evaluation_context(
    db: AsyncSession,
    *,
    user_id: int,
    graph: LearningGraph | None,
    node: GraphNode | None,
) -> dict[str, Any] | None:
    if graph is None or node is None:
        return None
    evaluation = await db.scalar(select(LearningEvaluation).where(
        LearningEvaluation.user_id == user_id,
        LearningEvaluation.graph_id == graph.id,
        LearningEvaluation.node_id == node.id,
    ))
    if evaluation is None:
        return None
    record = await db.scalar(select(LearningEvaluationResultRecord).where(
        LearningEvaluationResultRecord.evaluation_id == evaluation.id,
        LearningEvaluationResultRecord.user_id == user_id,
        LearningEvaluationResultRecord.graph_id == graph.id,
        LearningEvaluationResultRecord.node_id == node.id,
    ).order_by(desc(LearningEvaluationResultRecord.evaluation_revision)).limit(1))
    if record is None:
        return None
    public = dict(record.public_projection or {})
    node_ref = str(node.external_id or f"node-{node.id}")
    assessments = list(public.get("node_assessments") or [])
    assessment = next((item for item in assessments if str(item.get("node_id")) == node_ref), assessments[0] if assessments else None)
    return {
        "result_ref": record.result_ref,
        "evaluation_revision": int(record.evaluation_revision or 0),
        "graph_revision": int(record.graph_revision or 0),
        "provider_runtime": record.provider_runtime,
        "node_assessment": dict(assessment or {}),
        "global_warnings": list(public.get("global_warnings") or []),
        "next_evaluation_actions": list(public.get("next_evaluation_actions") or []),
        "evidence_event_ids": list(record.evidence_event_ids or []),
        "authority": "learning_evaluator_public_projection",
    }


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value or ""))


def _learner_state_public(snapshot: Any | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    barrier = getattr(snapshot, "primary_barrier", None)
    return {
        "state_id": snapshot.state_id,
        "topic_key": snapshot.topic_key,
        "topic_name": snapshot.topic_name,
        "topic_mastery": _enum_value(snapshot.topic_mastery),
        "primary_barrier": {
            "category": _enum_value(getattr(barrier, "category", "")),
            "description": str(getattr(barrier, "description", "")),
            "evidence_refs": list(getattr(barrier, "evidence_refs", []) or []),
        },
        "support_level": _enum_value(snapshot.support_level),
        "transfer_readiness": _enum_value(snapshot.transfer_readiness),
        "next_teaching_focus": snapshot.next_teaching_focus,
        "confidence": float(snapshot.confidence),
        "evidence_refs": list(snapshot.evidence_refs or []),
        "revision": int(snapshot.revision),
        "source": snapshot.source,
        "authority": "existing_learner_state",
    }


def _agent_capabilities() -> list[dict[str, Any]]:
    result = []
    for agent_type, adapter in AGENT_ADAPTERS.items():
        result.append({
            "agent_type": agent_type.value,
            "required_slots": list(adapter.required_slots),
            "optional_slots": list(adapter.optional_slots),
        })
    return sorted(result, key=lambda item: item["agent_type"])


async def resolve_tutor_context(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    session: TutorSession,
    active_artifact_id: int | None = None,
    requested_material_ids: Iterable[str] = (),
    use_material_context: bool = True,
) -> TutorContext:
    chat = await db.scalar(select(ChatSession).where(
        ChatSession.user_id == user_id,
        ChatSession.thread_id == thread_id,
    ))
    if chat is None or session.user_id != user_id or session.thread_id != thread_id:
        raise PermissionError("tutor context unavailable")

    graph, node = await resolve_formal_graph_node(db, user_id=user_id, session=session)
    artifact = await _owned_artifact(
        db, user_id=user_id, thread_id=thread_id, artifact_id=active_artifact_id,
    )
    if active_artifact_id and artifact is None:
        raise PermissionError("tutor current artifact unavailable")
    if artifact is None:
        artifact = await _formal_node_resource_artifact(
            db, user_id=user_id, graph=graph, node=node,
        )

    material_ids = [str(value) for value in requested_material_ids if str(value).strip()] if use_material_context else []
    if use_material_context and not material_ids and artifact:
        material_ids = [str(value) for value in artifact.get("source_material_ids") or []]
    if use_material_context and not material_ids and chat.active_material_id:
        material_ids = [str(chat.active_material_id)]

    material_payload = {"material_ids": [], "text": "", "citations": []}
    if material_ids:
        query = " ".join(part for part in (
            (node.title if node else ""),
            (artifact.get("topic") if artifact else ""),
            session.goal,
        ) if part)
        try:
            material = await build_conversation_material_context(
                db,
                user_id=user_id,
                thread_id=thread_id,
                query=query,
                requested_material_ids=material_ids,
            )
            material_payload = {
                "material_ids": list(material.material_ids),
                "text": material.text,
                "citations": list(material.citations),
            }
        except MaterialAccessError as exc:
            # Explicit/stale material IDs fail closed rather than leaking another scope.
            raise PermissionError("tutor material unavailable") from exc

    evaluation = await _latest_evaluation_context(
        db, user_id=user_id, graph=graph, node=node,
    )
    evidence_status = await _formal_evidence_status(
        db, user_id=user_id, thread_id=thread_id, graph=graph, node=node, evaluation=evaluation,
    )
    learner_state = (await get_normalized_learner_state(db, user_id)).model_dump(mode="json")
    profile = await _profile_context(db, user_id)
    impressions = await _impression_context(db, user_id)

    current_object: dict[str, Any] | None = None
    if artifact:
        current_object = artifact
    elif material_payload["material_ids"]:
        current_object = {
            "kind": "material",
            "material_ids": list(material_payload["material_ids"]),
            "title": "当前会话资料",
            "source": "workspace_material_binding",
        }
    elif node:
        current_object = {
            "kind": "graph_node",
            "graph_id": (graph.external_id or str(graph.id)) if graph else None,
            "node_id": node.external_id or str(node.id),
            "title": node.title,
            "source": "formal_graph_node",
        }

    warnings: list[str] = []
    if graph is None or node is None:
        warnings.append("formal_graph_missing")
    if evaluation is None:
        warnings.append("evaluation_missing")
    if learner_state is None:
        warnings.append("learner_state_missing")
    if profile is None:
        warnings.append("profile_missing")

    return TutorContext(
        user_id=user_id,
        thread_id=thread_id,
        tutor_session_id=session.session_id,
        session_goal=_text(session.goal),
        current_object=current_object,
        graph=_graph_public(graph) if graph else None,
        node=_node_public(node) if node else None,
        evaluation=evaluation,
        learner_state=learner_state,
        profile=profile,
        impressions=impressions,
        material_context=material_payload,
        evidence_status=evidence_status,
        agent_capabilities=_agent_capabilities(),
        warnings=warnings,
    )


def _assessment(context: TutorContext) -> dict[str, Any]:
    return dict((context.evaluation or {}).get("node_assessment") or {})


def _unresolved_misconceptions(assessment: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item) for item in list(assessment.get("misconceptions") or [])
        if not bool(item.get("resolved")) and float(item.get("confidence") or 0) >= 0.35
    ]


def _evidence_refs(context: TutorContext) -> tuple[str, ...]:
    refs: list[str] = []
    if context.evaluation:
        refs.append(
            f"evaluation:{context.evaluation.get('result_ref')}@r{context.evaluation.get('evaluation_revision')}"
        )
        refs.extend(f"workspace-event:{value}" for value in context.evaluation.get("evidence_event_ids") or [])
    if context.learner_state:
        refs.append(f"learner-state:{context.learner_state.get('state_id')}@r{context.learner_state.get('revision')}")
        refs.extend(str(value) for value in context.learner_state.get("evidence_refs") or [])
    for citation in list(context.material_context.get("citations") or []):
        refs.append(
            f"material:{citation.get('material_id')}:chunk:{citation.get('chunk_index')}"
        )
    refs.extend(str(value) for value in list((context.evidence_status or {}).get("pending_event_refs") or []))
    return tuple(dict.fromkeys(refs))


_PRESENTATION_DIMENSIONS = (
    "response_preference",
    "learning_style",
    "difficulty_preference",
    "interaction_style",
    "learning_pace",
    "content_preference",
)

_PRESENTATION_DEFAULTS = {
    "response_preference": "balanced",
    "learning_style": "balanced",
    "difficulty_preference": "adaptive",
    "interaction_style": "guided",
    "learning_pace": "balanced",
    "content_preference": "mixed",
}

_PRESENTATION_SOURCE_LABELS = {
    "current_request": "按你这轮刚刚的要求",
    "user_confirmed_impression": "按你确认过的偏好",
    "active_impression": "按近期稳定的学习偏好",
    "student_profile": "按已有学习画像",
    "normalized_learner_state": "按已合并并核对来源的学习偏好",
    "default": "按默认平衡方式",
}

_PRESENTATION_VALUE_LABELS = {
    "response_preference": {
        "concise": "简短直接",
        "detailed": "解释更完整",
        "balanced": "详略适中",
    },
    "learning_style": {
        "practice_first": "先练再讲",
        "example_first": "先例子后抽象",
        "hands_on": "边做边学",
        "theory_first": "先机制后应用",
        "balanced": "讲练结合",
    },
    "difficulty_preference": {
        "basic": "例子从基础开始",
        "advanced": "保留挑战性",
        "adaptive": "难度随可信证据调整",
    },
    "interaction_style": {
        "iterative": "分轮调整",
        "direct": "少确认、直接推进",
        "guided": "必要时引导确认",
    },
    "learning_pace": {
        "stepwise": "一步一步推进",
        "fast": "节奏更紧凑",
        "slow": "节奏更舒缓",
        "balanced": "正常节奏",
    },
    "content_preference": {
        "code": "优先代码例子",
        "video": "优先动态/视频表达",
        "document": "优先文字结构化说明",
        "interactive": "优先互动练习",
        "mixed": "混合呈现",
    },
}


def _preference_value(dimension: str, statement: Any, impression_key: str = "") -> str | None:
    text = _text(statement).lower()
    key = _text(impression_key).lower()
    if dimension == "response_preference":
        if any(token in text for token in ("详细", "细致", "展开", "完整解释", "讲清楚")):
            return "detailed"
        if any(token in text for token in ("简短", "短一点", "直接", "一句", "少说", "别讲太多")):
            return "concise"
        if key.endswith(".detailed"):
            return "detailed"
        if key.endswith(".concise"):
            return "concise"
    elif dimension == "learning_style":
        if any(token in text for token in ("先做题", "先练习", "先出题", "做完再讲", "先练")):
            return "practice_first"
        if any(token in text for token in ("先看例子", "先看实例", "从例子", "实例", "具体例子")):
            return "example_first"
        if any(token in text for token in ("边做边学", "动手", "实际操作", "亲自运行", "实践")):
            return "hands_on"
        if any(token in text for token in ("先讲理论", "先讲原理", "先理解机制", "理论优先")):
            return "theory_first"
        for suffix, value in ((".practice_first", "practice_first"), (".example_first", "example_first"), (".hands_on", "hands_on")):
            if key.endswith(suffix):
                return value
    elif dimension == "difficulty_preference":
        if any(token in text for token in ("简单", "基础", "入门", "降低难度")):
            return "basic"
        if any(token in text for token in ("难一点", "提高难度", "挑战", "进阶")):
            return "advanced"
        if key.endswith(".basic"):
            return "basic"
        if key.endswith(".advanced"):
            return "advanced"
    elif dimension == "interaction_style":
        if any(token in text for token in ("一步步改", "逐步调整", "继续修改", "再改", "分轮")):
            return "iterative"
        if any(token in text for token in ("直接做", "直接推进", "别反复确认", "少确认")):
            return "direct"
        if key.endswith(".iterative"):
            return "iterative"
    elif dimension == "learning_pace":
        if any(token in text for token in ("一步一步", "一条一条", "一次一个", "慢慢来")):
            return "stepwise"
        if any(token in text for token in ("快一点", "加快", "紧凑", "快速")):
            return "fast"
        if any(token in text for token in ("慢一点", "放慢", "别太快")):
            return "slow"
        if key.endswith(".stepwise"):
            return "stepwise"
    elif dimension == "content_preference":
        if any(token in text for token in ("代码", "python", "编程示例")):
            return "code"
        if any(token in text for token in ("视频", "动画", "动态演示")):
            return "video"
        if any(token in text for token in ("文档", "文字", "讲义", "结构化说明")):
            return "document"
        if any(token in text for token in ("互动", "边答", "小测", "练习卡")):
            return "interactive"
        if key.endswith(".code"):
            return "code"
    return None


def _profile_preferences(profile: dict[str, Any] | None) -> dict[str, str]:
    if not profile:
        return {}
    cognitive = dict(profile.get("cognitive_profile") or {})
    behavior = dict(profile.get("behavior_pattern") or {})
    result: dict[str, str] = {}

    response_raw = cognitive.get("response_style") or behavior.get("response_style")
    response_value = _preference_value("response_preference", response_raw)
    if response_value:
        result["response_preference"] = response_value

    learning_style_raw = cognitive.get("learning_style") or cognitive.get("teaching_method")
    learning_style_value = _preference_value("learning_style", learning_style_raw)
    if not learning_style_value:
        if cognitive.get("needs_concrete_examples") is True:
            learning_style_value = "example_first"
        elif cognitive.get("prefers_theory_first") is True:
            learning_style_value = "theory_first"
    if learning_style_value:
        result["learning_style"] = learning_style_value

    difficulty_raw = cognitive.get("difficulty_preference") or behavior.get("difficulty_preference")
    difficulty_value = _preference_value("difficulty_preference", difficulty_raw)
    if not difficulty_value:
        depth = _text(cognitive.get("question_depth")).lower()
        if depth == "surface":
            difficulty_value = "basic"
        elif depth == "deep":
            difficulty_value = "advanced"
    if difficulty_value:
        result["difficulty_preference"] = difficulty_value

    interaction_raw = behavior.get("interaction_style") or cognitive.get("interaction_style")
    interaction_value = _preference_value("interaction_style", interaction_raw)
    if interaction_value:
        result["interaction_style"] = interaction_value

    pace = _text(cognitive.get("learning_pace") or behavior.get("learning_pace")).lower()
    pace_map = {"slow": "slow", "medium": "balanced", "fast": "fast", "stepwise": "stepwise"}
    if pace in pace_map:
        result["learning_pace"] = pace_map[pace]

    formats = behavior.get("content_format_preference")
    format_items = formats if isinstance(formats, list) else [formats] if formats else []
    for raw in format_items:
        value = _preference_value("content_preference", raw)
        if value:
            result["content_preference"] = value
            break
    return result


def _explicit_preferences(user_message: str) -> dict[str, str]:
    text = _text(user_message)
    if not text:
        return {}
    result: dict[str, str] = {}
    for dimension in _PRESENTATION_DIMENSIONS:
        value = _preference_value(dimension, text)
        if value:
            result[dimension] = value
    return result


def _normalized_preferences(learner_state: dict[str, Any] | None) -> dict[str, str]:
    modalities = list((learner_state or {}).get("preferred_modalities") or [])
    result: dict[str, str] = {}
    if "concise" in modalities:
        result["response_preference"] = "concise"
    elif "detailed" in modalities:
        result["response_preference"] = "detailed"
    if "practice" in modalities:
        result["learning_style"] = "practice_first"
    elif "example" in modalities:
        result["learning_style"] = "example_first"
    if "code" in modalities:
        result["content_preference"] = "code"
    elif "video" in modalities:
        result["content_preference"] = "video"
    elif "reading" in modalities or "document" in modalities:
        result["content_preference"] = "document"
    elif "visual" in modalities:
        result["content_preference"] = "interactive"
    if "step_by_step" in modalities:
        result["learning_pace"] = "stepwise"
    return result


def build_presentation_policy(context: TutorContext, user_message: str = "") -> dict[str, Any]:
    """Resolve how to present without altering evidence-authoritative learning facts."""
    chosen: dict[str, dict[str, Any]] = {
        dimension: {
            "value": default,
            "source": "default",
            "source_label": _PRESENTATION_SOURCE_LABELS["default"],
            "label": _PRESENTATION_VALUE_LABELS[dimension][default],
        }
        for dimension, default in _PRESENTATION_DEFAULTS.items()
    }

    def apply(values: dict[str, str], source: str) -> None:
        for dimension, value in values.items():
            if dimension not in chosen or value not in _PRESENTATION_VALUE_LABELS.get(dimension, {}):
                continue
            chosen[dimension] = {
                "value": value,
                "source": source,
                "source_label": _PRESENTATION_SOURCE_LABELS[source],
                "label": _PRESENTATION_VALUE_LABELS[dimension][value],
            }

    if (context.learner_state or {}).get("state_version"):
        apply(_normalized_preferences(context.learner_state), "normalized_learner_state")
    else:
        apply(_profile_preferences(context.profile), "student_profile")
        active: dict[str, str] = {}
        confirmed: dict[str, str] = {}
        for item in context.impressions:
            dimension = _text(item.get("dimension"))
            if dimension not in _PRESENTATION_DIMENSIONS:
                continue
            if _text(item.get("status")).lower() in {"rejected", "uncertain"}:
                continue
            value = _preference_value(dimension, item.get("summary"), item.get("impression_key"))
            if not value:
                continue
            if bool(item.get("confirmed_by_user")):
                confirmed.setdefault(dimension, value)
            elif float(item.get("confidence") or 0) >= 0.75:
                active.setdefault(dimension, value)
        apply(active, "active_impression")
        apply(confirmed, "user_confirmed_impression")

    explicit = _explicit_preferences(user_message)
    apply(explicit, "current_request")

    source_rank = {
        "current_request": 4,
        "user_confirmed_impression": 3,
        "active_impression": 2,
        "student_profile": 1,
        "normalized_learner_state": 3,
        "default": 0,
    }
    applied = sorted([
        {
            "dimension": dimension,
            "value": item["value"],
            "label": item["label"],
            "source": item["source"],
            "source_label": item["source_label"],
        }
        for dimension, item in chosen.items()
        if item["source"] != "default"
    ], key=lambda item: (-source_rank.get(str(item.get("source")), 0), _PRESENTATION_DIMENSIONS.index(str(item.get("dimension")))))
    return {
        "dimensions": chosen,
        "applied_preferences": applied,
        "transient_override_dimensions": sorted(explicit),
        "learning_fact_authority": "unchanged",
        "does_not_affect": ["mastery", "evaluator", "graph", "path"],
    }


def _explicit_pedagogy_request(user_message: str) -> str | None:
    text = _text(user_message)
    if not text:
        return None
    lowered = text.lower()
    for strategy, tokens in _PEDAGOGY_REQUEST_PATTERNS:
        if any(token.lower() in lowered for token in tokens):
            return strategy
    return None


def _high_support_needed(context: TutorContext) -> bool:
    learner = dict(context.learner_state or {})
    barrier = dict(learner.get("primary_barrier") or {})
    haystack = " ".join((
        _text(barrier.get("category")),
        _text(barrier.get("description")),
        _text(learner.get("support_level")),
        _text(learner.get("next_teaching_focus")),
    )).lower()
    return any(token in haystack for token in (
        "cognitive", "overload", "high_load", "intensive", "负荷", "连续失败", "吃力", "注意力", "卡住",
    ))


def _preference_supporting_strategy(policy: dict[str, Any]) -> str | None:
    learning_style = _presentation_value(policy, "learning_style", "balanced")
    pace = _presentation_value(policy, "learning_pace", "balanced")
    difficulty = _presentation_value(policy, "difficulty_preference", "adaptive")
    content = _presentation_value(policy, "content_preference", "mixed")
    if learning_style == "example_first":
        return "worked_example"
    if learning_style == "practice_first" or content == "interactive":
        return "retrieval_practice"
    if pace in {"stepwise", "slow"} or difficulty == "basic":
        return "scaffolding"
    if difficulty == "advanced":
        return "transfer_challenge"
    return None


def build_pedagogical_strategy(
    context: TutorContext,
    *,
    action: str,
    user_message: str,
    presentation_policy: dict[str, Any],
) -> dict[str, Any]:
    """Select a deterministic teaching method without changing learning-fact authority."""
    assessment = _assessment(context)
    status = _text(assessment.get("assessment_status")).upper()
    mastery = float(assessment.get("mastery_signal") or 0)
    sufficiency = float(assessment.get("evidence_sufficiency") or 0)
    misconceptions = _unresolved_misconceptions(assessment)
    self_claim = bool(_UNDERSTANDING_CLAIM_RE.search(_text(user_message)))
    high_support = _high_support_needed(context)

    if action == "misconception_distinction" or misconceptions:
        primary = "socratic_questioning"
        why = "刚才的学习表现里还有一个容易混淆的点，我会先用连续追问和对比，帮你自己发现差别。"
        status_text = "正在为你采用：苏格拉底式追问"
        basis = "misconception"
    elif action == "collect_more_evidence" and self_claim:
        primary = "feynman_teach_back"
        why = "你已经表达了理解，但现在还需要一份可验证的学习表现；请先用自己的话讲一遍，我再根据你的解释继续。"
        status_text = "根据你刚才的表达，正在切换为：费曼复述法"
        basis = "self_report_needs_evidence"
    elif action == "transfer_challenge":
        primary = "transfer_challenge"
        why = "当前可信学习表现比较稳定，接下来会换一个新情境，检查你能不能把同一机制迁移过去。"
        status_text = "正在为你采用：迁移挑战"
        basis = "stable_for_transfer"
    elif high_support:
        primary = "scaffolding"
        why = "当前更适合把任务拆成更小的步骤，我会一次只推进一个关键点，并在每一步确认理解。"
        status_text = "这一部分我会采用：支架式教学"
        basis = "high_support_needed"
    elif action == "reteach_scaffold" and (status in {"EMERGING", "NEEDS_REVIEW"} or mastery < 0.55):
        primary = "worked_example"
        why = "当前基础还不够稳，我会先用一个完整例题示范思路，再把例子里的关键机制抽出来。"
        status_text = "这一部分我会先用例题带你理解"
        basis = "new_or_weak_foundation"
    elif action == "targeted_practice" or (0.45 <= sufficiency < 0.8 and status not in {"SECURE"}):
        primary = "retrieval_practice"
        why = "这个知识点已经学过，但还需要确认能不能从记忆中稳定提取；我会先让你回忆和作答，再给提示。"
        status_text = "正在为你采用：检索练习"
        basis = "learned_but_unstable"
    elif action == "collect_more_evidence":
        primary = "retrieval_practice"
        why = "现在最重要的是获得一份可信的学习表现，我会先让你独立回忆或作答，再决定下一步。"
        status_text = "正在为你采用：检索练习"
        basis = "evidence_collection"
    else:
        primary = "scaffolding"
        why = "我会把当前任务拆成清晰的小步骤，根据你每一步的表现再决定下一步。"
        status_text = "这一部分我会采用：支架式教学"
        basis = "safe_default"

    requested = _explicit_pedagogy_request(user_message)
    preferred = requested or _preference_supporting_strategy(presentation_policy)
    supporting = (
        preferred
        if preferred
        and preferred != primary
        and preferred in _PEDAGOGY_SUPPORT_COMPATIBILITY.get(action, frozenset())
        else None
    )
    if supporting:
        why += f" 同时会照顾你这次更偏好的“{_PEDAGOGY_LABELS[supporting]}”方式。"

    behavior_map = {
        "socratic_questioning": ["先追问理由", "用对比问题暴露混淆", "不给出替代性自报掌握结论"],
        "feynman_teach_back": ["让学习者用自己的话复述", "从解释缺口继续追问", "把复述作为新证据而非掌握结论"],
        "worked_example": ["先展示完整例题", "再标出关键步骤", "最后换一个近似例子检查理解"],
        "scaffolding": ["拆成小步骤", "一次推进一个关键点", "逐步减少提示"],
        "retrieval_practice": ["先独立回忆或作答", "延后提示", "用结果决定是否继续练习"],
        "transfer_challenge": ["使用新情境", "要求解释迁移理由", "避免只复述原题"],
    }
    execution = list(behavior_map[primary])
    if supporting:
        execution.append(f"辅助采用：{_PEDAGOGY_LABELS[supporting]}")
    return {
        "strategy": primary,
        "label": _PEDAGOGY_LABELS[primary],
        "supporting_strategy": supporting,
        "supporting_label": _PEDAGOGY_LABELS.get(supporting) if supporting else None,
        "status_text": status_text,
        "why_text": why,
        "execution": execution,
        "selection_basis": basis,
        "learning_fact_authority": "unchanged",
        "does_not_affect": ["mastery", "evaluator", "graph", "learner_state", "path"],
    }


def _presentation_value(policy: dict[str, Any], dimension: str, fallback: str) -> str:
    return _text(((policy.get("dimensions") or {}).get(dimension) or {}).get("value")) or fallback


def _resource_spec(context: TutorContext, action: str, assessment: dict[str, Any], presentation_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    # The user's own goal is the authoritative topic; graph node / artifact titles
    # are only structural refinements and must never replace what the learner asked to learn.
    node_title = _text(
        (context.node or {}).get("title")
        or (context.current_object or {}).get("title")
        or "当前知识点"
    )
    goal_topic = _text(context.session_goal)
    topic = goal_topic or node_title
    material_ids = list(context.material_context.get("material_ids") or [])
    misconception = (_unresolved_misconceptions(assessment) or [{}])[0]
    if action == "reteach_scaffold":
        policy = presentation_policy or {}
        pedagogy = dict(policy.get("pedagogical_strategy") or {})
        primary_strategy = _text(pedagogy.get("strategy"))
        supporting_strategy = _text(pedagogy.get("supporting_strategy"))
        response_pref = _presentation_value(policy, "response_preference", "balanced")
        learning_style = _presentation_value(policy, "learning_style", "balanced")
        content_pref = _presentation_value(policy, "content_preference", "mixed")
        barrier = _text((context.learner_state or {}).get("primary_barrier", {}).get("description"))
        purpose = "重新讲解当前知识点，并保持学习判断仍以可信证据为准。"
        sections = ["直观例子", "关键机制", "一步一步检查理解"]
        if learning_style == "practice_first":
            sections = ["先做一个最小尝试", "针对尝试解释关键机制", "再次检查理解"]
            purpose += " 呈现上先给一个最小尝试，再针对结果解释。"
        elif learning_style == "example_first":
            sections = ["具体例子", "从例子抽出关键机制", "换一个例子检查理解"]
            purpose += " 呈现上先用具体例子，再抽象概括。"
        elif learning_style == "hands_on":
            sections = ["动手观察", "解释观察到的机制", "自己复现关键步骤"]
            purpose += " 呈现上尽量边做边解释。"
        elif learning_style == "theory_first":
            sections = ["关键机制", "推理关系", "应用例子"]
            purpose += " 呈现上先解释机制，再进入例子。"
        if primary_strategy == "worked_example":
            sections = ["完整例题示范", "标出关键步骤", "从例题抽出机制", "近似例子检查理解"]
            purpose += " 教学法采用例题示范：先完整示范，再逐步抽离提示。"
        elif primary_strategy == "scaffolding":
            sections = ["最小可理解步骤", "一步一检验", "连接关键机制", "减少提示后再做一次"]
            purpose += " 教学法采用支架式教学：把认知负荷拆小并逐步减少帮助。"
        elif primary_strategy == "socratic_questioning":
            sections = ["先问一个关键为什么", "追问依据", "用反例对照", "自己总结规则"]
            purpose += " 教学法采用苏格拉底式追问，不直接替你完成关键推理。"
        if supporting_strategy == "worked_example" and "辅助例题" not in sections:
            sections.insert(1, "辅助例题")
        elif supporting_strategy == "scaffolding":
            sections.insert(1, "一步一检验")
            purpose += " 同时按你的节奏偏好把讲解拆成更小步骤。"
        elif supporting_strategy == "socratic_questioning":
            sections.insert(1, "关键追问")
            purpose += " 同时用关键追问让你主动完成推理。"
        if content_pref == "code" and "代码示例" not in sections:
            sections.insert(1, "代码示例")
        if barrier:
            purpose += f" 当前需要重点处理：{barrier}。"
        return {
            "agent_type": AgentType.LECTURE.value,
            "slots": {
                "topic": topic,
                "purpose": purpose,
                "detail_level": "concise" if response_pref == "concise" else ("detailed" if response_pref == "detailed" else "normal"),
                "sections": sections,
                "export_formats": [],
                "source_material_ids": material_ids,
            },
        }
    if action == "misconception_distinction":
        policy = presentation_policy or {}
        pedagogy = dict(policy.get("pedagogical_strategy") or {})
        primary_strategy = _text(pedagogy.get("strategy"))
        supporting_strategy = _text(pedagogy.get("supporting_strategy"))
        summary = _text(misconception.get("summary")) or "当前存在需要辨析的概念混淆"
        requirements = [
            f"围绕这个已被证据支持的误区设计辨析题：{summary}",
            "至少一题使用反例或近似概念对比；不要直接推进下一知识点",
        ]
        if primary_strategy == "socratic_questioning":
            requirements.insert(0, "先追问学习者为什么这样判断，再根据回答继续追问；不要一开始直接给结论")
        if supporting_strategy == "worked_example":
            requirements.append("追问后可以给一个最小对比例子，但仍要求学习者自己说出差别")
        elif supporting_strategy == "scaffolding":
            requirements.append("若学习者仍混淆，把辨析拆成一次一个判断标准并逐级减少提示，最后要求独立总结差别")
        elif supporting_strategy == "feynman_teach_back":
            requirements.append("辨析后要求学习者用自己的话向初学者解释两个概念的差别，并针对解释缺口继续追问")
        return {
            "agent_type": AgentType.EXAM.value,
            "slots": {
                "topic": topic,
                "question_count": 2,
                "question_types": ["single_choice", "true_false"],
                "difficulty": "adaptive",
                "source_material_ids": material_ids,
                "additional_requirements": requirements,
            },
        }
    if action == "transfer_challenge":
        pedagogy = dict((presentation_policy or {}).get("pedagogical_strategy") or {})
        transfer_requirements = [
            "使用与已有练习不同的新情境检验迁移，不能只复述原题",
            "要求解释理由或应用机制，而不是只给结论",
        ]
        supporting_strategy = _text(pedagogy.get("supporting_strategy"))
        if supporting_strategy == "feynman_teach_back":
            transfer_requirements.append("作答后要求学习者用自己的话解释为什么原机制能迁移到这个新情境")
        elif supporting_strategy == "retrieval_practice":
            transfer_requirements.append("先不给提示，让学习者从记忆中独立提取原机制，再说明它如何应用到新情境")
        return {
            "agent_type": AgentType.EXAM.value,
            "slots": {
                "topic": topic,
                "question_count": 2,
                "question_types": ["short_answer"],
                "difficulty": "advanced",
                "source_material_ids": material_ids,
                "additional_requirements": transfer_requirements,
            },
        }
    # collect_more_evidence / targeted_practice
    pedagogy = dict((presentation_policy or {}).get("pedagogical_strategy") or {})
    primary_strategy = _text(pedagogy.get("strategy"))
    supporting_strategy = _text(pedagogy.get("supporting_strategy"))
    requirements = [
        "只检验当前节点；题目结果用于补充可信学习证据",
        "证据不足时不得仅凭本题宣称已经掌握",
    ]
    question_types = ["single_choice"] if action == "collect_more_evidence" else ["single_choice", "short_answer"]
    if primary_strategy == "feynman_teach_back":
        question_types = ["short_answer"]
        requirements.insert(0, "请学习者不用照抄材料，用自己的话向一个第一次接触该概念的人解释；从解释缺口继续追问")
    elif primary_strategy == "retrieval_practice":
        requirements.insert(0, "先不给提示，让学习者从记忆中检索并作答；作答后再给反馈")
    elif primary_strategy == "scaffolding":
        requirements.insert(0, "把作答拆成一个个最小步骤；只在当前步骤受阻时给提示，并逐步撤掉帮助")
    if supporting_strategy == "worked_example":
        requirements.append("反馈阶段可补一个最小例题，但不能替代学习者先独立作答")
    elif supporting_strategy == "scaffolding":
        requirements.append("如果第一次作答受阻，再逐级给小提示，不要一开始展示完整答案")
    elif supporting_strategy == "feynman_teach_back":
        question_types = ["short_answer"]
        requirements.append("独立作答后再要求用自己的话解释给初学者听，并根据解释缺口继续追问")
    elif supporting_strategy == "socratic_questioning":
        question_types = ["short_answer"]
        requirements.append("独立作答后用一个关键“为什么”追问依据；不要先给结论，再根据回答继续追问")
    elif supporting_strategy == "retrieval_practice":
        requirements.append("第一轮保持无提示检索；只有完成独立回忆后才进入反馈或讲解")
    return {
        "agent_type": AgentType.EXAM.value,
        "slots": {
            "topic": topic,
            "question_count": 1 if action == "collect_more_evidence" else 2,
            "question_types": question_types,
            "difficulty": "basic" if action == "collect_more_evidence" else "adaptive",
            "source_material_ids": material_ids,
            "additional_requirements": requirements,
        },
    }


def decide_tutor_action(context: TutorContext, user_message: str = "") -> TutorDecision:
    assessment = _assessment(context)
    status = _text(assessment.get("assessment_status")).upper()
    mastery = float(assessment.get("mastery_signal") or 0)
    eval_confidence = float(assessment.get("confidence") or 0)
    sufficiency = float(assessment.get("evidence_sufficiency") or 0)
    eligibility = _text(assessment.get("completion_eligibility_signal")).upper()
    misconceptions = _unresolved_misconceptions(assessment)
    explicit_confusion = bool(_CONFUSION_RE.search(_text(user_message)))
    learner = context.learner_state or {}
    learner_mastery = _text(learner.get("topic_mastery")).lower()
    transfer = _text(learner.get("transfer_readiness")).lower()
    learner_confidence = float(learner.get("confidence") or 0)

    reason_codes: list[str] = []
    action: str
    evidence_pending = (context.evidence_status or {}).get("state") == "evidence_pending"

    if explicit_confusion and context.current_object:
        if misconceptions:
            action = "misconception_distinction"
            reason_codes.extend(("explicit_confusion", "evaluator_misconception_present", "keep_current_object"))
        else:
            action = "reteach_scaffold"
            reason_codes.extend(("explicit_confusion", "keep_current_object", "mastery_not_mutated_by_self_report"))
    elif evidence_pending:
        action = "collect_more_evidence"
        reason_codes.extend(("new_formal_evidence_pending_evaluator", "mastery_unchanged_until_trusted_evaluation"))
    elif context.evaluation is None:
        action = "collect_more_evidence"
        reason_codes.extend(("evaluation_missing", "evidence_required_before_mastery_claim"))
    elif status in {"INSUFFICIENT_EVIDENCE", "CONTRADICTORY"} or sufficiency < 0.45:
        action = "collect_more_evidence"
        reason_codes.extend(("evaluator_evidence_insufficient", f"assessment_status_{status.lower() or 'unknown'}"))
    elif misconceptions:
        action = "misconception_distinction"
        reason_codes.extend(("evaluator_misconception_present", "do_not_advance_node"))
    elif (
        (status == "SECURE" and mastery >= 0.8 and eligibility == "ELIGIBLE")
        or (learner_mastery in {"secure", "transferable"} and transfer in {"ready", "demonstrated"} and learner_confidence >= 0.65)
    ):
        action = "transfer_challenge"
        reason_codes.extend(("secure_evidence", "transfer_ready", "path_authority_blocked_no_revision"))
    elif status in {"EMERGING", "NEEDS_REVIEW"} or mastery < 0.55 or learner_mastery in {"unknown", "emerging"}:
        action = "reteach_scaffold"
        reason_codes.extend(("low_or_emerging_mastery", "scaffold_before_more_difficulty"))
    else:
        action = "targeted_practice"
        reason_codes.extend(("developing_mastery", "targeted_practice_before_transfer"))

    confidence = max(eval_confidence, learner_confidence, 0.35 if context.current_object else 0.25)
    if context.evaluation is None:
        confidence = min(confidence, 0.45)
    evidence_refs = _evidence_refs(context)
    presentation_policy = build_presentation_policy(context, user_message)
    tutor_state = dict((context.learner_state or {}).get("current_tutor_session") or {})
    presentation_policy["learning_strategy"] = build_personalized_learning_strategy(
        context.learner_state or {},
        available_minutes=int(tutor_state.get("daily_minutes") or 20),
    )
    presentation_policy["pedagogical_strategy"] = build_pedagogical_strategy(
        context, action=action, user_message=user_message, presentation_policy=presentation_policy,
    )
    spec = _resource_spec(context, action, assessment, presentation_policy)
    target_node = str((context.node or {}).get("external_id") or "") or None
    return TutorDecision(
        action=action,
        target_node_id=target_node,
        target_object=context.current_object,
        reason_codes=tuple(reason_codes),
        evidence_refs=evidence_refs,
        confidence=max(0.0, min(1.0, confidence)),
        uncertainty=max(0.0, min(1.0, 1.0 - confidence)),
        path_authority_status=context.path_authority_status,
        agent_task_spec=spec,
        presentation_policy=presentation_policy,
    )


def explicit_practice_decision(
    context: TutorContext, *, base: TutorDecision | None = None, user_message: str = "给我做题",
) -> TutorDecision:
    """Build a real Exam task decision without changing any learning fact.

    This is used by explicit learner/proactive actions.  It reuses the same
    presentation and resource-spec pipeline as the normal planner; only the
    requested teaching action changes.
    """
    base = base or decide_tutor_action(context, user_message)
    policy = build_presentation_policy(context, user_message)
    tutor_state = dict((context.learner_state or {}).get("current_tutor_session") or {})
    policy["learning_strategy"] = build_personalized_learning_strategy(
        context.learner_state or {}, available_minutes=int(tutor_state.get("daily_minutes") or 20),
    )
    policy["pedagogical_strategy"] = build_pedagogical_strategy(
        context, action="targeted_practice", user_message=user_message, presentation_policy=policy,
    )
    spec = _resource_spec(context, "targeted_practice", _assessment(context), policy)
    return TutorDecision(
        action="targeted_practice", target_node_id=base.target_node_id,
        target_object=base.target_object,
        reason_codes=base.reason_codes + ("explicit_practice_request",),
        evidence_refs=base.evidence_refs, confidence=base.confidence,
        uncertainty=base.uncertainty, path_authority_status=base.path_authority_status,
        agent_task_spec=spec, presentation_policy=policy,
    )


async def latest_tutor_task_public(
    db: AsyncSession, *, user_id: int, thread_id: str, tutor_session_id: str,
) -> dict[str, Any] | None:
    """Return the latest persisted WorkspaceAgentTask owned by this Tutor session."""
    rows = list((await db.execute(
        select(WorkspaceAgentTask)
        .where(
            WorkspaceAgentTask.user_id == user_id,
            WorkspaceAgentTask.thread_id == thread_id,
        )
        .order_by(desc(WorkspaceAgentTask.updated_at), desc(WorkspaceAgentTask.id))
        .limit(40)
    )).scalars().all())
    for row in rows:
        tutor_meta = dict((row.slots or {}).get("_tutor") or {})
        if str(tutor_meta.get("tutor_session_id") or "") != str(tutor_session_id):
            continue
        return {
            "task_id": row.task_id,
            "version": int(row.version or 1),
            "state": row.state,
            "agent_type": row.agent_type,
            "artifact_id": row.artifact_id,
            "error_code": row.error_code,
            "tutor_action": tutor_meta.get("action"),
        }
    return None


def tutor_user_projection(
    *, context: TutorContext, decision: TutorDecision, task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action_labels = {
        "collect_more_evidence": "先补一份可信学习证据",
        "reteach_scaffold": "换一种方式重新讲当前知识点",
        "misconception_distinction": "做一次针对误区的辨析",
        "targeted_practice": "完成针对性练习",
        "transfer_challenge": "用新情境做迁移挑战",
    }
    evidence = dict(context.evidence_status or {})
    state = str(evidence.get("state") or "no_formal_evidence")
    if state == "evidence_pending":
        reason = "刚刚的学习行为已经记录，正式评估还没有更新；我会保持谨慎，不会因为一次作答就宣布你已经掌握。"
        evidence_text = f"已记录 {int(evidence.get('pending_count') or 0)} 条新学习证据，等待正式评估。"
    elif decision.action == "misconception_distinction":
        reason = "可信评估仍显示当前知识点存在需要辨析的误区，所以先把容易混淆的地方分清。"
        evidence_text = "已读取最新可信评估结果。"
    elif decision.action == "transfer_challenge":
        reason = "最新可信评估已经支持当前知识点达到较稳状态，下一步用新情境检验能不能真正迁移。"
        evidence_text = "最新可信评估已更新并被导师采用。"
    elif decision.action == "reteach_scaffold":
        reason = "当前可信证据还不足以直接加难度，我会先换一种方式把这个知识点讲清。"
        evidence_text = "导师仍以现有可信证据为准。"
    else:
        reason = "当前需要先补充可验证的学习表现，再决定是否调整难度或推进。"
        evidence_text = "暂时不会根据自报分数直接改变掌握度。"
    current = dict(context.current_object or {})
    task_payload = dict(task or {}) or None
    task_state_labels = {
        "collecting_requirements": "待补充",
        "awaiting_confirmation": "待确认",
        "running": "正在执行",
        "completed": "已完成",
        "failed": "执行受阻",
        "cancelled": "已取消",
    }
    if task_payload:
        task_payload["state_label"] = task_state_labels.get(str(task_payload.get("state") or ""), str(task_payload.get("state") or ""))
        task_payload["provider_blocked"] = task_payload.get("error_code") == "PROVIDER_BLOCKED"
    policy = dict(decision.presentation_policy or {})
    applied = list(policy.get("applied_preferences") or [])
    if applied:
        first = applied[0]
        extra = f"，另外还会照顾 {len(applied) - 1} 个已知偏好" if len(applied) > 1 else ""
        personalization_text = f"{first.get('source_label')}，这次会采用“{first.get('label')}”的呈现方式{extra}。学习判断仍只看可信学习证据。"
    else:
        personalization_text = "暂时没有需要强行套用的偏好，我会用平衡方式呈现；学习判断仍只看可信学习证据。"
    safe_personalization = {
        "text": personalization_text,
        "applied": [
            {
                "dimension": item.get("dimension"),
                "value": item.get("value"),
                "label": item.get("label"),
                "source_label": item.get("source_label"),
            }
            for item in applied
        ],
        "transient_override_dimensions": list(policy.get("transient_override_dimensions") or []),
        "learning_strategy": {
            "family": dict(policy.get("learning_strategy") or {}).get("family"),
            "plan": list(dict(policy.get("learning_strategy") or {}).get("plan") or []),
            "resource_order": list(dict(policy.get("learning_strategy") or {}).get("resource_order") or []),
            "presentation_policy": dict(dict(policy.get("learning_strategy") or {}).get("presentation_policy") or {}),
            "why_this_action": dict(dict(policy.get("learning_strategy") or {}).get("why_this_action") or {}),
        },
        "scope_note": "个性化只调整讲法和呈现，不改变掌握度、正式评估、学习图谱或路径权威。",
    }
    pedagogy = dict(policy.get("pedagogical_strategy") or {})
    safe_pedagogy = {
        "strategy": pedagogy.get("strategy"),
        "label": pedagogy.get("label"),
        "supporting_strategy": pedagogy.get("supporting_strategy"),
        "supporting_label": pedagogy.get("supporting_label"),
        "status_text": pedagogy.get("status_text"),
        "why_text": pedagogy.get("why_text"),
        "execution": list(pedagogy.get("execution") or []),
        "scope_note": "教学方法只决定怎么教，不改变掌握度、正式评估、学习图谱、学习者状态或路径权威。",
    }
    evidence_pending = state == "evidence_pending"
    orchestration = {
        "can_continue": not evidence_pending and decision.action != "plan_completed",
        "continue_label": "按导师安排继续",
        "delegation_required": True,
        "auto_execute": False,
        "stop_reason": (
            "新学习证据正在等待可信评估，先停在这里；评估更新后我会重新安排下一步。"
            if evidence_pending else None
        ),
    }
    return {
        "current_object": {
            "kind": current.get("kind"),
            "title": current.get("title") or (context.node or {}).get("title") or "当前知识点",
            "artifact_id": current.get("artifact_id"),
            "node_id": (context.node or {}).get("external_id") or current.get("node_id"),
        },
        "action": decision.action,
        "action_label": action_labels.get(decision.action, "继续当前学习"),
        "reason": reason,
        "evidence": {
            "state": state,
            "text": evidence_text,
            "pending_count": int(evidence.get("pending_count") or 0),
            "evaluator_revision": int(evidence.get("evaluator_revision") or 0),
        },
        "task": task_payload,
        "personalization": safe_personalization,
        "pedagogical_strategy": safe_pedagogy,
        "orchestration": orchestration,
        "path_authority_status": context.path_authority_status,
    }


def _provider_status(agent_type: AgentType) -> tuple[str, str | None]:
    """Distinguish a real provider configuration from deterministic/mock execution."""
    if agent_type not in {AgentType.LECTURE, AgentType.PRESENTATION, AgentType.CODE, AgentType.MINDMAP, AgentType.VIDEO}:
        return "NOT_REQUIRED_FOR_TASK_PREPARATION", None
    try:
        from config import settings
        provider = str(getattr(settings, "LLM_PROVIDER", "mock") or "mock").lower()
        if provider == "mock":
            return "REAL_PROVIDER_BLOCKED", "MOCK_PROVIDER_NOT_ACCEPTED_AS_LIVE"
        key_field = {
            "deepseek": "DEEPSEEK_API_KEY",
            "qwen": "QWEN_API_KEY",
            "spark": "XUNFEI_API_KEY",
        }.get(provider)
        if key_field and not str(getattr(settings, key_field, "") or "").strip():
            return "REAL_PROVIDER_BLOCKED", f"{key_field}_MISSING"
        return "REAL_PROVIDER_CONFIGURED_NOT_PROBED", None
    except Exception as exc:  # configuration inspection itself is not task success.
        return "REAL_PROVIDER_BLOCKED", f"PROVIDER_CONFIG_ERROR:{type(exc).__name__}"


def _decision_task_identity(context: TutorContext, decision: TutorDecision) -> str:
    payload = {
        "user_id": context.user_id,
        "thread_id": context.thread_id,
        "tutor_session_id": context.tutor_session_id,
        "graph": (context.graph or {}).get("external_id") or (context.graph or {}).get("id"),
        "node": decision.target_node_id,
        "evaluation_revision": (context.evaluation or {}).get("evaluation_revision"),
        "action": decision.action,
        "evidence_refs": list(decision.evidence_refs),
        "agent_task_spec": decision.agent_task_spec,
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def prepare_tutor_decision_task(
    db: AsyncSession,
    *,
    session: TutorSession,
    context: TutorContext,
    decision: TutorDecision,
) -> tuple[AgentTask, dict[str, Any]]:
    spec = dict(decision.agent_task_spec or {})
    if not spec:
        raise ValueError("tutor decision has no executable agent task")
    agent_type = AgentType(str(spec.get("agent_type")))
    adapter = AGENT_ADAPTERS.get(agent_type)
    if adapter is None:
        raise ValueError("tutor decision references unavailable agent capability")

    digest = _decision_task_identity(context, decision)
    task_id = f"tutor-{digest[:36]}"
    existing = await load_task_by_id(db, context.user_id, context.thread_id, task_id)
    provider_status, provider_reason = _provider_status(agent_type)
    if existing is not None:
        return existing, {
            "idempotent_replay": True,
            "decision_key": digest,
            "provider_status": provider_status,
            "provider_reason": provider_reason,
        }

    slots = adapter.initial_slots()
    slots.update(adapter.normalise_updates(dict(spec.get("slots") or {})))
    slots["_tutor"] = {
        "tutor_session_id": session.session_id,
        "decision_key": digest,
        "decision_action": decision.action,
        "target_node_id": decision.target_node_id,
        "graph_id": (context.graph or {}).get("external_id") or (context.graph or {}).get("id"),
        "graph_node_external_id": (context.node or {}).get("external_id") or decision.target_node_id,
        "evidence_artifact_id": (context.current_object or {}).get("artifact_id"),
        "evaluation_revision": (context.evaluation or {}).get("evaluation_revision"),
        "evidence_refs": list(decision.evidence_refs),
        "path_authority_status": decision.path_authority_status,
    }
    task = AgentTask(
        task_id=task_id,
        user_id=context.user_id,
        thread_id=context.thread_id,
        agent_type=agent_type,
        slots=slots,
        state=TaskState.RUNNING,
        version=1,
        confirmed_version=1,
        execution_key=f"tutor:{digest}",
    )
    # A missing real resource provider is a visible terminal task state, not a fake artifact.
    if provider_status == "REAL_PROVIDER_BLOCKED":
        task.state = TaskState.FAILED
        task.error_code = "PROVIDER_BLOCKED"
    task = await save_task(db, task)
    return task, {
        "idempotent_replay": False,
        "decision_key": digest,
        "provider_status": provider_status,
        "provider_reason": provider_reason,
    }


def task_public_payload(task: AgentTask, runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    slots = dict(task.slots or {})
    tutor_meta = dict(slots.pop("_tutor", {}) or {})
    payload = {
        "task_id": task.task_id,
        "task_version": task.version,
        "version": task.version,
        "kind": task.agent_type.value,
        "agent_type": task.agent_type.value,
        "state": task.state.value,
        "status": task.state.value,
        "confirmed_version": task.confirmed_version,
        "execution_key": task.execution_key,
        "artifact_id": task.artifact_id,
        "error_code": task.error_code,
        **slots,
        "tutor": tutor_meta,
    }
    if task.agent_type == AgentType.EXAM:
        payload["count"] = slots.get("question_count")
        payload["instruction"] = "；".join(slots.get("additional_requirements") or [])
    elif task.agent_type == AgentType.LECTURE:
        payload["instruction"] = slots.get("purpose") or ""
    if runtime:
        payload.update(runtime)
    return payload
