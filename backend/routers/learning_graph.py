"""学习图谱路由 — 多图谱 CRUD + 节点管理 + 双视图"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from services.learning_graph_service import (
    add_custom_node,
    complete_node,
    create_graph,
    create_personalized_skeleton,
    delete_graph,
    generate_node_resources,
    generate_recommended_nodes,
    get_course_tree,
    get_graph,
    get_knowledge_network,
    hide_node,
    list_user_graphs,
    mark_node_needs_review,
    reorder_nodes,
    set_primary,
    unhide_node,
    update_graph,
    update_node,
)
from utils.auth import get_current_user_id
from agents.learning_graph_orchestrator.compiler import CandidateContractError
from agents.learning_graph_orchestrator.runner import (
    BlueprintContractError,
    ExistingGraphContractError,
    SourceContractError,
    UnrecoverableFallbackError,
)
from services.learning_graph_orchestrator_integration import (
    build_formal_request,
    load_public_graph,
    orchestrate_and_save,
)
from services.learning_graph_resource_generation import (
    materialize_resource_plan,
    resolve_resource_record_ids,
    retry_resource_generation,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/learning-graphs", tags=["learning-graphs"])


# ---- Pydantic schemas ----


class CreateGraphRequest(BaseModel):
    title: str
    goal: str = ""
    goal_type: str = "custom"
    source_type: str = "manual"
    source_detail: Optional[dict] = None


class UpdateGraphRequest(BaseModel):
    title: Optional[str] = None
    goal: Optional[str] = None
    goal_type: Optional[str] = None
    status: Optional[str] = None
    is_primary: Optional[bool] = None


class CreateNodeRequest(BaseModel):
    title: str = "新节点"
    description: str = ""
    knowledge_type: str = "theory"
    difficulty: int = 3
    estimated_minutes: int = 30
    depends_on: list[str] = []
    tags: list[str] = []
    parent_id: Optional[int] = None


class UpdateNodeRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    knowledge_type: Optional[str] = None
    difficulty: Optional[int] = None
    estimated_minutes: Optional[int] = None
    order_index: Optional[int] = None
    status: Optional[str] = None
    tags: Optional[list[str]] = None
    parent_id: Optional[int] = None


class CompleteNodeRequest(BaseModel):
    mastery_score: float = 80.0


class GenerateSkeletonRequest(BaseModel):
    chapters: Optional[list[str]] = None
    level: str = "beginner"
    hours_per_week: int = 5
    profile_summary: str = ""


class OrchestrateGraphRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=4, max_length=100)
    idempotency_key: str = Field(min_length=4, max_length=140)
    thread_id: str = Field(min_length=1, max_length=100)
    user_goal: str = Field(min_length=2, max_length=1000)
    domain: str = Field(min_length=2, max_length=100)
    locale: str = Field(default="zh-CN", max_length=20)
    source_material_ids: list[str] = Field(default_factory=list, max_length=8)
    update_mode: str = Field(default="CREATE", pattern=r"^(CREATE|EXTEND|REVISE|REPLACE_SOURCE|MERGE)$")
    graph_id: Optional[str] = Field(default=None, max_length=100)
    graph_revision: int = Field(default=0, ge=0)
    replaced_source_ref_ids: list[str] = Field(default_factory=list, max_length=40)


class ResourceMaterializeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=100)
    execution_key: str = Field(min_length=4, max_length=140)


def _contract_error(exc: Exception) -> HTTPException:
    report = getattr(exc, "report", None)
    codes = [item.code for item in getattr(report, "issues", [])]
    if isinstance(exc, UnrecoverableFallbackError):
        reports = [
            exc.source_contract_report,
            exc.existing_graph_report,
            exc.candidate_report,
            exc.blueprint_report,
            exc.fallback_report,
        ]
        codes = sorted({
            item.code for report in reports if report
            for item in report.issues
        })
    return HTTPException(422, {
        "code": type(exc).__name__,
        "issues": codes,
        "message": "learning graph contract rejected",
    })


# ---- Graph endpoints ----


@router.get("")
async def list_graphs(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户的所有学习图谱"""
    graphs = await list_user_graphs(db, current_user_id)
    return {"success": True, "data": graphs}


@router.post("/orchestrate")
async def orchestrate_graph(
    req: OrchestrateGraphRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Run strict v0.1.3 and persist only a valid public result."""
    try:
        request = await build_formal_request(
            db,
            user_id=current_user_id,
            thread_id=req.thread_id,
            request_id=req.request_id,
            user_goal=req.user_goal,
            domain=req.domain,
            source_material_ids=req.source_material_ids,
            locale=req.locale,
            update_mode=req.update_mode,
            graph_id=req.graph_id,
            graph_revision=req.graph_revision,
            replaced_source_ref_ids=req.replaced_source_ref_ids,
        )
        result = await orchestrate_and_save(
            db,
            user_id=current_user_id,
            request=request,
            idempotency_key=req.idempotency_key,
        )
        return {"success": True, "data": result, "validation": {"valid": True}}
    except PermissionError as exc:
        raise HTTPException(403, "learning graph access denied") from exc
    except (SourceContractError, ExistingGraphContractError, CandidateContractError,
            BlueprintContractError, UnrecoverableFallbackError) as exc:
        raise _contract_error(exc) from exc
    except ValueError as exc:
        internal_message = str(exc).lower()
        is_revision = "revision" in internal_message
        is_idempotency = "idempotency" in internal_message
        code = (
            "LEARNING_GRAPH_REVISION_CONFLICT" if is_revision
            else "LEARNING_GRAPH_IDEMPOTENCY_CONFLICT" if is_idempotency
            else "LEARNING_GRAPH_INVALID"
        )
        raise HTTPException(409 if is_revision or is_idempotency else 422, {
            "code": code,
            "message": (
                "learning graph request conflicts with persisted state"
                if is_revision or is_idempotency
                else "learning graph request is invalid"
            ),
        }) from exc


@router.get("/formal/{graph_id}")
async def formal_graph(
    graph_id: str,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {"success": True, "data": await load_public_graph(
            db, user_id=current_user_id, graph_id=graph_id,
        )}
    except PermissionError as exc:
        raise HTTPException(403, "learning graph access denied") from exc
    except LookupError as exc:
        raise HTTPException(404, "learning graph not found") from exc
    except ExistingGraphContractError as exc:
        raise _contract_error(exc) from exc


@router.post("/formal/{graph_id}/nodes/{node_id}/resource-plans/{plan_id}/generate")
async def materialize_formal_resource(
    graph_id: str,
    node_id: str,
    plan_id: str,
    req: ResourceMaterializeRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        graph_db_id, node_db_id, plan_db_id = await resolve_resource_record_ids(
            db,
            user_id=current_user_id,
            graph_id=graph_id,
            node_id=node_id,
            resource_plan_id=plan_id,
        )
        artifact = await materialize_resource_plan(
            db,
            user_id=current_user_id,
            thread_id=req.thread_id,
            graph_id=graph_db_id,
            node_id=node_db_id,
            resource_plan_id=plan_db_id,
            execution_key=req.execution_key,
        )
        return {"success": True, "data": {
            "artifact_id": artifact.id,
            "resource_plan_id": plan_id,
            "status": "generated",
        }}
    except PermissionError as exc:
        raise HTTPException(403, "resource access denied") from exc
    except ValueError as exc:
        raise HTTPException(409, {
            "code": "RESOURCE_GENERATION_CONFLICT",
            "message": "resource generation conflicts with persisted state",
        }) from exc
    except Exception as exc:
        logger.warning(
            "formal graph resource generation failed error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(503, "资源生成失败，未创建产物。") from exc


@router.post("/formal/{graph_id}/nodes/{node_id}/resource-plans/{plan_id}/retry")
async def retry_formal_resource(
    graph_id: str,
    node_id: str,
    plan_id: str,
    req: ResourceMaterializeRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        graph_db_id, node_db_id, plan_db_id = await resolve_resource_record_ids(
            db,
            user_id=current_user_id,
            graph_id=graph_id,
            node_id=node_id,
            resource_plan_id=plan_id,
        )
        artifact = await retry_resource_generation(
            db,
            user_id=current_user_id,
            thread_id=req.thread_id,
            graph_id=graph_db_id,
            node_id=node_db_id,
            resource_plan_id=plan_db_id,
            execution_key=req.execution_key,
        )
        return {"success": True, "data": {
            "artifact_id": artifact.id,
            "resource_plan_id": plan_id,
            "status": "generated",
        }}
    except PermissionError as exc:
        raise HTTPException(403, "resource access denied") from exc
    except ValueError as exc:
        raise HTTPException(409, {
            "code": "RESOURCE_RETRY_CONFLICT",
            "message": "resource retry conflicts with persisted state",
        }) from exc
    except Exception as exc:
        logger.warning(
            "formal graph resource retry failed error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(503, "资源重试失败，未创建产物。") from exc


@router.post("")
async def create_new_graph(
    req: CreateGraphRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """创建新学习图谱"""
    graph = await create_graph(
        db,
        user_id=current_user_id,
        title=req.title,
        goal=req.goal,
        goal_type=req.goal_type,
        source_type=req.source_type,
        source_detail=req.source_detail,
    )
    return {"success": True, "data": {"id": graph.id, "title": graph.title, "is_primary": graph.is_primary}}


@router.get("/{graph_id}")
async def get_graph_detail(
    graph_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """获取图谱详情（含节点列表和树结构）"""
    data = await get_graph(db, graph_id, current_user_id)
    if not data:
        raise HTTPException(404, "Graph not found")
    return {"success": True, "data": data}


@router.put("/{graph_id}")
async def update_graph_info(
    graph_id: int,
    req: UpdateGraphRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """更新图谱（标题、目标、状态、是否主图谱）"""
    updates = {k: v for k, v in req.dict().items() if v is not None}
    graph = await update_graph(db, graph_id, current_user_id, updates)
    if not graph:
        raise HTTPException(404, "Graph not found")
    return {"success": True, "data": {"id": graph.id, "status": graph.status, "is_primary": graph.is_primary}}


@router.delete("/{graph_id}")
async def delete_graph_endpoint(
    graph_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """删除图谱及其所有节点"""
    ok = await delete_graph(db, graph_id, current_user_id)
    if not ok:
        raise HTTPException(404, "Graph not found")
    return {"success": True, "message": "Graph deleted"}


@router.post("/{graph_id}/set-primary")
async def set_primary_graph(
    graph_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """设为用户主图谱"""
    graph = await set_primary(db, graph_id, current_user_id)
    if not graph:
        raise HTTPException(404, "Graph not found")
    return {"success": True, "data": {"id": graph.id, "is_primary": True}}


# ---- Skeleton & Generation ----


@router.post("/{graph_id}/generate-skeleton")
async def generate_skeleton(
    graph_id: int,
    req: GenerateSkeletonRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """使用 AI 语义规划器生成个性化学习图谱骨架"""
    try:
        # Get graph for title/goal
        graph = await _get_graph_or_none(db, graph_id, current_user_id)
        if not graph:
            raise HTTPException(404, "Graph not found")
        result = await create_personalized_skeleton(
            db, graph_id, current_user_id,
            title=graph.title, goal=graph.goal or "",
            profile_summary=req.profile_summary,
            level=req.level, hours_per_week=req.hours_per_week,
        )
        return {"success": True, "data": result}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


async def _get_graph_or_none(db, graph_id, user_id):
    from sqlalchemy import select
    from database.models import LearningGraph
    r = await db.execute(select(LearningGraph).where(LearningGraph.id == graph_id, LearningGraph.user_id == user_id))
    return r.scalar_one_or_none()


async def _require_owned_node(db: AsyncSession, graph_id: int, node_id: int, user_id: int) -> None:
    """Reject path-identity mixing before calling legacy node services."""
    from sqlalchemy import select
    from database.models import GraphNode, LearningGraph

    owned = await db.scalar(
        select(GraphNode.id)
        .join(LearningGraph, LearningGraph.id == GraphNode.graph_id)
        .where(
            GraphNode.id == node_id,
            GraphNode.graph_id == graph_id,
            GraphNode.user_id == user_id,
            LearningGraph.user_id == user_id,
        )
    )
    if owned is None:
        raise HTTPException(404, "Node not found in graph")


@router.post("/{graph_id}/generate-recommended")
async def generate_recommended(
    graph_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """为前 3 个 planned 节点预生成资源推荐"""
    if await _get_graph_or_none(db, graph_id, current_user_id) is None:
        raise HTTPException(404, "Graph not found")
    nodes = await generate_recommended_nodes(db, graph_id, current_user_id, count=3)
    return {"success": True, "data": {"generated": len(nodes)}}


# ---- Node endpoints ----


@router.post("/{graph_id}/nodes")
async def create_node(
    graph_id: int,
    req: CreateNodeRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """在指定图谱中添加自定义节点"""
    try:
        node = await add_custom_node(db, graph_id, current_user_id, req.dict())
        return {"success": True, "data": {"id": node.id, "title": node.title, "status": node.status}}
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.put("/{graph_id}/nodes/{node_id}")
async def update_node_info(
    graph_id: int,
    node_id: int,
    req: UpdateNodeRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """更新节点信息"""
    await _require_owned_node(db, graph_id, node_id, current_user_id)
    updates = {k: v for k, v in req.dict().items() if v is not None}
    node = await update_node(db, node_id, current_user_id, updates)
    if not node:
        raise HTTPException(404, "Node not found")
    return {"success": True, "data": {"id": node.id, "status": node.status}}


@router.post("/{graph_id}/nodes/{node_id}/complete")
async def complete_node_endpoint(
    graph_id: int,
    node_id: int,
    req: CompleteNodeRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """标记节点为已完成"""
    await _require_owned_node(db, graph_id, node_id, current_user_id)
    node = await complete_node(db, node_id, current_user_id, req.mastery_score)
    if not node:
        raise HTTPException(404, "Node not found")
    return {"success": True, "data": {"id": node.id, "status": node.status, "mastery_score": node.mastery_score}}


@router.post("/{graph_id}/nodes/{node_id}/needs-review")
async def needs_review_endpoint(
    graph_id: int,
    node_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """标记节点需要复习"""
    await _require_owned_node(db, graph_id, node_id, current_user_id)
    node = await mark_node_needs_review(db, node_id, current_user_id)
    if not node:
        raise HTTPException(404, "Node not found")
    return {"success": True, "data": {"id": node.id, "status": "needs_review"}}


# ---- Lazy Generation & Node Actions ----

class ReorderRequest(BaseModel):
    node_orders: list[dict]  # [{node_id, order_index}]


@router.post("/{graph_id}/nodes/{node_id}/generate-resources")
async def lazy_generate_node_resources(
    graph_id: int,
    node_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """按需懒加载生成单个节点资源（Lazy Generation）"""
    await _require_owned_node(db, graph_id, node_id, current_user_id)
    node = await generate_node_resources(db, node_id, current_user_id)
    if not node:
        raise HTTPException(404, "Node not found")
    return {"success": True, "data": node}


@router.post("/{graph_id}/nodes/{node_id}/hide")
async def hide_node_endpoint(
    graph_id: int,
    node_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """隐藏节点"""
    await _require_owned_node(db, graph_id, node_id, current_user_id)
    node = await hide_node(db, node_id, current_user_id)
    if not node:
        raise HTTPException(404, "Node not found")
    return {"success": True, "data": {"id": node.id, "is_hidden": True}}


@router.post("/{graph_id}/nodes/{node_id}/unhide")
async def unhide_node_endpoint(
    graph_id: int,
    node_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """恢复隐藏节点"""
    await _require_owned_node(db, graph_id, node_id, current_user_id)
    node = await unhide_node(db, node_id, current_user_id)
    if not node:
        raise HTTPException(404, "Node not found")
    return {"success": True, "data": {"id": node.id, "is_hidden": False}}


@router.post("/{graph_id}/reorder-nodes")
async def reorder_nodes_endpoint(
    graph_id: int,
    req: ReorderRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """重新排序节点（含依赖校验）"""
    ok = await reorder_nodes(db, graph_id, current_user_id, req.node_orders)
    if not ok:
        raise HTTPException(400, "依赖冲突: 前置节点不能排在依赖节点之后")
    return {"success": True, "message": "Nodes reordered"}


# ---- View endpoints ----


@router.get("/{graph_id}/course-tree")
async def course_tree_view(
    graph_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """获取课程树视图（纵向课程大纲）"""
    data = await get_course_tree(db, graph_id, current_user_id)
    if "error" in data:
        raise HTTPException(404, data["error"])
    return {"success": True, "data": data}


@router.get("/{graph_id}/knowledge-network")
async def knowledge_network_view(
    graph_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """获取知识网络视图（全局可拖拽可缩放）"""
    data = await get_knowledge_network(db, graph_id, current_user_id)
    if "error" in data:
        raise HTTPException(404, data["error"])
    return {"success": True, "data": data}
