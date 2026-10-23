"""学习图谱服务 — 多图谱 CRUD、节点状态机、资源管理、进度追踪"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GeneratedItem, GraphNode, LearningGraph
from services.knowledge_graph import CHAPTER_ORDER, KNOWLEDGE_GRAPH
from services.normalized_learner_state import get_normalized_learner_state
from services.learning_personalization import resource_blueprint

logger = logging.getLogger(__name__)

# 节点状态机
NODE_STATUSES = [
    "planned",       # 待生成
    "generating",    # 生成中
    "generated",     # 已生成资源
    "learning",      # 学习中
    "completed",     # 已完成
    "needs_review",  # 需要复习
    "failed",        # 生成失败
]

VALID_TRANSITIONS = {
    "planned": ["generating"],
    "generating": ["generated", "failed"],
    "generated": ["learning", "failed"],
    "learning": ["completed", "needs_review"],
    "completed": ["needs_review"],
    "needs_review": ["learning", "completed"],
    "failed": ["planned"],
}


# ---- Graph CRUD ----


async def create_graph(
    db: AsyncSession,
    user_id: int,
    title: str,
    goal: str = "",
    goal_type: str = "custom",
    source_type: str = "manual",
    source_detail: dict | None = None,
) -> LearningGraph:
    """创建新图谱。如果用户没有主图谱，自动设为主图谱。"""
    existing_primary = await db.execute(
        select(LearningGraph).where(
            LearningGraph.user_id == user_id,
            LearningGraph.is_primary == True,
        )
    )
    is_first = existing_primary.scalar_one_or_none() is None

    graph = LearningGraph(
        user_id=user_id,
        title=title,
        goal=goal,
        goal_type=goal_type,
        source_type=source_type,
        source_detail=source_detail or {},
        is_primary=is_first,
    )
    db.add(graph)
    await db.commit()
    await db.refresh(graph)
    logger.info("[learning_graph] created graph_id=%s user_id=%s title=%s", graph.id, user_id, title)
    return graph


async def list_user_graphs(db: AsyncSession, user_id: int) -> list[dict]:
    """列出用户的所有图谱（含统计摘要）"""
    result = await db.execute(
        select(LearningGraph)
        .where(LearningGraph.user_id == user_id)
        .order_by(desc(LearningGraph.is_primary), desc(LearningGraph.updated_at))
    )
    graphs = result.scalars().all()
    return [_graph_summary(g) for g in graphs]


async def get_graph(db: AsyncSession, graph_id: int, user_id: int) -> dict | None:
    """获取单个图谱详情（含节点列表）"""
    graph = await _get_graph_or_none(db, graph_id, user_id)
    if not graph:
        return None
    nodes_result = await db.execute(
        select(GraphNode)
        .where(GraphNode.graph_id == graph_id)
        .order_by(GraphNode.order_index)
    )
    nodes = nodes_result.scalars().all()
    data = _graph_summary(graph)
    data["nodes"] = [_node_detail(n) for n in nodes]
    # 构建树结构
    data["node_tree"] = _build_node_tree(nodes)
    return data


async def update_graph(
    db: AsyncSession, graph_id: int, user_id: int, updates: dict
) -> LearningGraph | None:
    """更新图谱（标题、目标、状态等）"""
    graph = await _get_graph_or_none(db, graph_id, user_id)
    if not graph:
        return None
    allowed = {"title", "goal", "goal_type", "status", "is_primary"}
    for key, value in updates.items():
        if key in allowed and value is not None:
            setattr(graph, key, value)
    if "status" in updates and updates["status"] == "paused":
        graph.status = "paused"
    elif "status" in updates and updates["status"] == "active":
        graph.status = "active"
    elif "status" in updates and updates["status"] == "archived":
        graph.status = "archived"
    elif "status" in updates and updates["status"] == "completed":
        graph.status = "completed"

    # 如果设为主图谱，先取消其他主图谱
    if updates.get("is_primary"):
        await db.execute(
            update(LearningGraph)
            .where(LearningGraph.user_id == user_id, LearningGraph.id != graph_id)
            .values(is_primary=False)
        )
    await db.commit()
    await db.refresh(graph)
    return graph


async def delete_graph(db: AsyncSession, graph_id: int, user_id: int) -> bool:
    """删除图谱及其所有节点"""
    graph = await _get_graph_or_none(db, graph_id, user_id)
    if not graph:
        return False
    await db.delete(graph)  # cascade deletes nodes
    await db.commit()
    return True


async def set_primary(db: AsyncSession, graph_id: int, user_id: int) -> LearningGraph | None:
    """设为用户主图谱"""
    graph = await _get_graph_or_none(db, graph_id, user_id)
    if not graph:
        return None
    await db.execute(
        update(LearningGraph)
        .where(LearningGraph.user_id == user_id)
        .values(is_primary=False)
    )
    graph.is_primary = True
    await db.commit()
    await db.refresh(graph)
    return graph


# ---- Node Management ----


async def create_skeleton_from_knowledge_graph(
    db: AsyncSession, graph_id: int, user_id: int,
    chapters: list[str] | None = None,
) -> list[GraphNode]:
    """保留作为 fallback：仅当 LLM 不可用时使用 KNOWLEDGE_GRAPH（仅限 ML 相关目标）"""
    graph = await _get_graph_or_none(db, graph_id, user_id)
    if not graph:
        raise ValueError(f"Graph {graph_id} not found")

    chapter_list = chapters or CHAPTER_ORDER
    nodes = []
    for idx, ch_id in enumerate(chapter_list):
        ch = KNOWLEDGE_GRAPH.get(ch_id, {})
        if not ch: continue
        node = GraphNode(graph_id=graph_id, user_id=user_id,
            title=ch.get("name", ch_id), description=ch.get("description", ""),
            knowledge_type="theory", difficulty=ch.get("difficulty", 3),
            estimated_minutes=ch.get("estimated_hours", 0) * 60,
            order_index=idx + 1, status="planned",
            depends_on=ch.get("depends_on", []), tags=ch.get("tags", []), auto_generated=True)
        db.add(node); nodes.append(node)
    graph.total_nodes = len(nodes)
    await db.commit()
    for n in nodes: await db.refresh(n)
    logger.info("[learning_graph] fallback skeleton: %d nodes for graph_id=%s", len(nodes), graph_id)
    return nodes


async def create_personalized_skeleton(
    db: AsyncSession, graph_id: int, user_id: int,
    title: str, goal: str = "", profile_summary: str = "",
    level: str = "beginner", hours_per_week: int = 5,
) -> dict:
    """使用 LLM 根据用户目标生成个性化学习图谱骨架"""
    graph = await _get_graph_or_none(db, graph_id, user_id)
    if not graph:
        raise ValueError(f"Graph {graph_id} not found")

    # Build LLM prompt
    prompt = f"""你是一位资深课程设计师。请为用户规划一个个性化学习路径。

用户信息：
- 计划名称：{title}
- 学习目标：{goal or title}
- 学习水平：{level}
- 每周可投入：{hours_per_week} 小时
- 画像摘要：{profile_summary or '暂无画像'}

请输出 JSON（不要 Markdown 代码块）：
{{
  "graph_title": "精简后的图谱标题",
  "normalized_goal": "标准化的学习目标",
  "planning_rationale": "个性化规划理由，2-3句话，解释为什么要这样组织",
  "nodes": [
    {{
      "title": "节点标题",
      "learning_goal": "本节点学习目标",
      "description": "简要描述",
      "difficulty": 3,
      "estimated_minutes": 45,
      "node_type": "theory",
      "prerequisites": [],
      "recommended_resources": ["document", "question", "mindmap"]
    }}
  ]
}}

要求：
- 生成 6-10 个学习节点
- 节点标题简洁（5-15字）
- 节点按学习顺序排列
- 前置依赖合理（数组填前面节点的标题，不要自依赖）
- 难度 1-5（1=入门，5=高级）
- node_type: theory/practice/project/review
- 内容必须与用户目标相关，不要生成无关主题"""

    try:
        from services.llm_service_real import get_llm_service
        llm = await get_llm_service()
        result = await llm.chat(messages=[{"role": "user", "content": prompt}], temperature=0.5, max_tokens=3000)
        raw = result.get("content", "") if isinstance(result, dict) else str(result)
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        import json as _json
        try:
            plan = _json.loads(raw)
        except _json.JSONDecodeError:
            # Try to extract JSON from text
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                plan = _json.loads(raw[start:end])
            else:
                raise ValueError(f"Cannot parse LLM response as JSON")

        # Validate
        nodes_data = plan.get("nodes", [])
        if not isinstance(nodes_data, list) or len(nodes_data) < 3:
            raise ValueError("Too few nodes in plan")

        # Update graph metadata
        graph.title = plan.get("graph_title", title)[:200]
        graph.goal = plan.get("normalized_goal", goal)[:500]
        graph.planning_rationale = plan.get("planning_rationale", "")[:1000]
        graph.source_type = "ai_planner"
        # Auto-set as primary so user sees their new graph immediately
        await db.execute(update(LearningGraph).where(LearningGraph.user_id == user_id).values(is_primary=False))
        graph.is_primary = True

        # Create nodes
        title_to_node_id = {}
        nodes = []
        for idx, nd in enumerate(nodes_data[:15]):
            if not isinstance(nd, dict) or not nd.get("title"):
                continue
            node_title = str(nd.get("title", f"Node {idx+1}"))[:200]
            prereqs = nd.get("prerequisites", []) or []
            # Resolve prerequisite node IDs
            depends_on = []
            for p_title in prereqs:
                if p_title in title_to_node_id:
                    depends_on.append(title_to_node_id[p_title])

            node = GraphNode(
                graph_id=graph_id, user_id=user_id,
                title=node_title,
                description=nd.get("description", "")[:500] if nd.get("description") else "",
                learning_goal=nd.get("learning_goal", "")[:500] if nd.get("learning_goal") else "",
                knowledge_type=_map_node_type(nd.get("node_type", "theory")),
                difficulty=max(1, min(5, int(nd.get("difficulty", 3)))),
                estimated_minutes=max(15, min(180, int(nd.get("estimated_minutes", 45)))),
                order_index=idx + 1, status="planned",
                depends_on=depends_on,
                tags=nd.get("recommended_resources", []),
                auto_generated=True,
            )
            db.add(node)
            await db.flush()
            nodes.append(node)
            title_to_node_id[node_title] = node.id

        graph.total_nodes = len(nodes)
        graph.status = "active"
        await db.commit()
        for n in nodes: await db.refresh(n)
        logger.info("[learning_graph] AI planner: %d nodes for graph_id=%s (%s)", len(nodes), graph_id, graph.title)
        return {"graph_id": graph_id, "title": graph.title, "nodes_created": len(nodes),
                "planning_rationale": graph.planning_rationale}

    except Exception as e:
        logger.error(f"[learning_graph] AI planner failed for graph_id={graph_id}: {e}")
        graph.status = "draft"
        graph.planning_rationale = f"AI 规划暂时不可用，请稍后重试。错误：{str(e)[:200]}"
        await db.commit()
        if _is_ml_related(title, goal):
            return await create_skeleton_from_knowledge_graph(db, graph_id, user_id)
        raise ValueError(f"图谱规划失败：{str(e)[:200]}")


def _map_node_type(nt: str) -> str:
    mapping = {"theory": "theory", "practice": "practice", "project": "project", "review": "review"}
    return mapping.get(str(nt).lower(), "theory")


def _is_ml_related(title: str, goal: str) -> bool:
    """仅当目标明确与机器学习相关时才允许 fallback 到 KNOWLEDGE_GRAPH"""
    ml_keywords = ["机器学习", "machine learning", "深度学习", "deep learning", "神经网络",
                   "neural network", "监督学习", "supervised", "ml", "data science", "数据科学"]
    combined = f"{title} {goal}".lower()
    return any(kw in combined for kw in ml_keywords)


async def generate_recommended_nodes(
    db: AsyncSession, graph_id: int, user_id: int, count: int = 3
) -> list[dict]:
    """为图谱前 count 个 planned 节点创建真实资源生成计划，触发 agent 生成"""
    if await _get_graph_or_none(db, graph_id, user_id) is None:
        raise PermissionError("graph does not belong to authenticated user")
    result = await db.execute(
        select(GraphNode)
        .where(GraphNode.graph_id == graph_id, GraphNode.status == "planned")
        .order_by(GraphNode.order_index)
        .limit(count)
    )
    nodes = result.scalars().all()
    learner_state = await get_normalized_learner_state(db, user_id)

    results = []
    for node in nodes:
        node.status = "learning"  # 标记为学习中
        node.resources = _real_resources_for_node(node, learner_state)
        await db.commit()
        await db.refresh(node)
        # 异步触发真实 agent 生成资源
        await _trigger_resource_generation(db, node, user_id)
        results.append(_node_detail(node))

    return results


async def generate_node_resources(
    db: AsyncSession, node_id: int, user_id: int
) -> dict | None:
    """按需懒加载生成单个节点资源（Lazy Generation）"""
    result = await db.execute(
        select(GraphNode).where(GraphNode.id == node_id, GraphNode.user_id == user_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        return None
    if node.status == "generated":
        return _node_detail(node)

    # 设置资源计划
    if not node.resources:
        learner_state = await get_normalized_learner_state(db, user_id)
        node.resources = _real_resources_for_node(node, learner_state)
    node.status = "generating"
    await db.commit()
    await db.refresh(node)

    # 触发真实资源生成
    await _trigger_resource_generation(db, node, user_id)

    # 重新读取节点状态（_trigger_resource_generation 已更新）
    await db.refresh(node)
    return _node_detail(node)


async def hide_node(db: AsyncSession, node_id: int, user_id: int) -> GraphNode | None:
    """隐藏节点"""
    result = await db.execute(
        select(GraphNode).where(GraphNode.id == node_id, GraphNode.user_id == user_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        return None
    node.is_hidden = True
    await db.commit()
    await db.refresh(node)
    return node


async def unhide_node(db: AsyncSession, node_id: int, user_id: int) -> GraphNode | None:
    """恢复隐藏的节点"""
    result = await db.execute(
        select(GraphNode).where(GraphNode.id == node_id, GraphNode.user_id == user_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        return None
    node.is_hidden = False
    await db.commit()
    await db.refresh(node)
    return node


async def reorder_nodes(
    db: AsyncSession, graph_id: int, user_id: int, node_orders: list[dict]
) -> bool:
    """重新排序节点 [{node_id, order_index}]，含依赖校验"""
    graph = await _get_graph_or_none(db, graph_id, user_id)
    if not graph:
        return False
    # 构建 node_id -> new_order 的映射
    order_map = {item["node_id"]: item["order_index"] for item in node_orders}
    nodes_result = await db.execute(
        select(GraphNode).where(GraphNode.graph_id == graph_id)
    )
    nodes = nodes_result.scalars().all()

    # 依赖校验: 前置节点的 order_index 必须 < 依赖该节点的 order_index
    node_map = {n.id: n for n in nodes}
    for n in nodes:
        new_order = order_map.get(n.id, n.order_index)
        for dep_title_or_id in (n.depends_on or []):
            # 找到依赖节点
            dep_node = None
            for other in nodes:
                if other.title == dep_title_or_id or str(other.id) == str(dep_title_or_id):
                    dep_node = other
                    break
            if dep_node:
                dep_order = order_map.get(dep_node.id, dep_node.order_index)
                if dep_order >= new_order:
                    logger.warning(f"[reorder] 依赖冲突: node {n.id} order={new_order}, dep {dep_node.id} order={dep_order}")
                    return False  # 依赖冲突

    for n in nodes:
        if n.id in order_map:
            n.order_index = order_map[n.id]
    await db.commit()
    return True


async def add_custom_node(
    db: AsyncSession, graph_id: int, user_id: int, node_data: dict
) -> GraphNode:
    """用户手动添加自定义节点"""
    graph = await _get_graph_or_none(db, graph_id, user_id)
    if not graph:
        raise ValueError(f"Graph {graph_id} not found")

    # 确定排序位置
    count_result = await db.execute(
        select(func.count()).where(GraphNode.graph_id == graph_id)
    )
    count = count_result.scalar() or 0

    node = GraphNode(
        graph_id=graph_id,
        user_id=user_id,
        title=node_data.get("title", "新节点"),
        description=node_data.get("description", ""),
        knowledge_type=node_data.get("knowledge_type", "theory"),
        difficulty=node_data.get("difficulty", 3),
        estimated_minutes=node_data.get("estimated_minutes", 30),
        order_index=count + 1,
        status="planned",
        depends_on=node_data.get("depends_on", []),
        tags=node_data.get("tags", []),
        auto_generated=False,
    )
    if node_data.get("parent_id"):
        node.parent_id = node_data["parent_id"]

    db.add(node)
    graph.total_nodes = (graph.total_nodes or 0) + 1
    await db.commit()
    await db.refresh(node)
    return node


async def update_node(
    db: AsyncSession, node_id: int, user_id: int, updates: dict
) -> GraphNode | None:
    """更新节点（标题、描述、状态等）"""
    result = await db.execute(
        select(GraphNode).where(GraphNode.id == node_id, GraphNode.user_id == user_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        return None

    allowed = {"title", "description", "knowledge_type", "difficulty",
               "estimated_minutes", "order_index", "tags", "parent_id"}
    for key, value in updates.items():
        if key in allowed and value is not None:
            setattr(node, key, value)

    # 状态转换校验
    new_status = updates.get("status")
    if new_status and new_status in NODE_STATUSES:
        valid_next = VALID_TRANSITIONS.get(node.status, [])
        if new_status in valid_next or new_status == node.status:
            node.status = new_status

    await db.commit()
    await db.refresh(node)
    return node


async def complete_node(
    db: AsyncSession, node_id: int, user_id: int, mastery_score: float = 80.0
) -> GraphNode | None:
    """标记节点为完成"""
    result = await db.execute(
        select(GraphNode).where(GraphNode.id == node_id, GraphNode.user_id == user_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        return None

    node.status = "completed"
    node.mastery_score = mastery_score

    # 更新图谱进度
    graph = await _get_graph_or_none(db, node.graph_id, user_id)
    if graph:
        completed = await db.execute(
            select(func.count()).where(
                GraphNode.graph_id == node.graph_id,
                GraphNode.status == "completed",
            )
        )
        graph.completed_nodes = completed.scalar() or 0
        if graph.total_nodes and graph.total_nodes > 0:
            graph.progress_pct = round(graph.completed_nodes / graph.total_nodes * 100, 1)

    await db.commit()
    await db.refresh(node)
    return node


async def mark_node_needs_review(
    db: AsyncSession, node_id: int, user_id: int
) -> GraphNode | None:
    """标记节点需要复习"""
    result = await db.execute(
        select(GraphNode).where(GraphNode.id == node_id, GraphNode.user_id == user_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        return None
    node.status = "needs_review"
    await db.commit()
    await db.refresh(node)
    return node


# ---- Course Tree & Network Views ----


async def get_course_tree(db: AsyncSession, graph_id: int, user_id: int) -> dict:
    """获取课程树视图数据（纵向）"""
    graph = await _get_graph_or_none(db, graph_id, user_id)
    if not graph:
        return {"error": "graph not found"}
    nodes_result = await db.execute(
        select(GraphNode)
        .where(GraphNode.graph_id == graph_id)
        .order_by(GraphNode.order_index)
    )
    nodes = nodes_result.scalars().all()

    # 构建进度统计
    status_counts = {}
    for s in NODE_STATUSES:
        status_counts[s] = 0
    for n in nodes:
        status_counts[n.status] = status_counts.get(n.status, 0) + 1

    # 找到下一个推荐节点
    next_node = None
    for n in nodes:
        if n.status in ("planned", "learning"):
            next_node = _node_detail(n)
            break

    return {
        "graph_id": graph_id,
        "title": graph.title,
        "progress_pct": graph.progress_pct,
        "total_nodes": len(nodes),
        "status_counts": status_counts,
        "next_node": next_node,
        "nodes": [_node_detail(n) for n in nodes],
    }


async def get_knowledge_network(db: AsyncSession, graph_id: int, user_id: int) -> dict:
    """获取知识网络视图数据（全局、可缩放、可拖拽）"""
    graph = await _get_graph_or_none(db, graph_id, user_id)
    if not graph:
        return {"error": "graph not found"}
    nodes_result = await db.execute(
        select(GraphNode)
        .where(GraphNode.graph_id == graph_id)
        .order_by(GraphNode.order_index)
    )
    nodes = nodes_result.scalars().all()

    # 构建网络节点 — 使用有向层级布局（左到右：基础→进阶）
    network_nodes = []
    n = len(nodes)
    # Calculate column based on dependency depth
    node_depth = {}
    node_map = {n.id: n for n in nodes}
    title_to_id = {n.title: n.id for n in nodes}

    def calc_depth(node_id, visited=None):
        if visited is None: visited = set()
        if node_id in visited: return 0
        if node_id in node_depth: return node_depth[node_id]
        visited.add(node_id)
        node = node_map.get(node_id)
        max_dep = 0
        if node and node.depends_on:
            for dep in node.depends_on:
                dep_id = title_to_id.get(dep) or (int(dep) if str(dep).isdigit() else None)
                if dep_id and dep_id in node_map:
                    max_dep = max(max_dep, calc_depth(dep_id, visited.copy()) + 1)
        node_depth[node_id] = max_dep
        return max_dep

    for node in nodes:
        calc_depth(node.id)

    # Group by depth
    depth_groups = {}
    for node in nodes:
        d = node_depth.get(node.id, 0)
        depth_groups.setdefault(d, []).append(node)

    max_depth = max(depth_groups.keys()) if depth_groups else 0
    spacing_x = 250
    spacing_y = 80

    for depth, group in depth_groups.items():
        for j, node in enumerate(group):
            x = depth * spacing_x - (max_depth * spacing_x) / 2
            y = (j - len(group) / 2) * spacing_y
            size = 60 + (node.mastery_score or 0) * 0.3

            network_nodes.append({
                "id": node.id, "title": node.title, "status": node.status,
                "mastery_score": node.mastery_score, "difficulty": node.difficulty,
                "x": round(x, 1), "y": round(y, 1), "size": round(size, 1),
                "depends_on": node.depends_on or [], "tags": node.tags or [],
            })

    # 构建边 — depends_on 可能是章节 ID (如 "ml_overview") 或节点标题
    edges = []
    node_ids = {n.id for n in nodes}
    # 先建一章ID到节点标题的映射
    chapter_id_to_node = {}
    for node in nodes:
        for ch_id, ch_data in KNOWLEDGE_GRAPH.items():
            if ch_data.get("name") == node.title:
                chapter_id_to_node[ch_id] = node.id
                break

    for node in nodes:
        for dep_ref in (node.depends_on or []):
            target_id = None
            # 尝试章节 ID 匹配
            if dep_ref in chapter_id_to_node:
                target_id = chapter_id_to_node[dep_ref]
            else:
                # 尝试标题匹配
                for other in nodes:
                    if other.title == dep_ref or str(other.id) == str(dep_ref):
                        target_id = other.id
                        break
            if target_id and target_id != node.id:
                edges.append({
                    "source": target_id,
                    "target": node.id,
                    "type": "prerequisite",
                })

    return {
        "graph_id": graph_id,
        "title": graph.title,
        "nodes": network_nodes,
        "edges": edges,
    }


# ---- Helpers ----


async def _get_graph_or_none(db: AsyncSession, graph_id: int, user_id: int) -> LearningGraph | None:
    result = await db.execute(
        select(LearningGraph).where(
            LearningGraph.id == graph_id,
            LearningGraph.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


def _node_detail(n: GraphNode) -> dict:
    return {
        "id": n.id,
        "graph_id": n.graph_id,
        "parent_id": n.parent_id,
        "title": n.title,
        "description": n.description,
        "knowledge_type": n.knowledge_type,
        "difficulty": n.difficulty,
        "estimated_minutes": n.estimated_minutes,
        "order_index": n.order_index,
        "status": n.status,
        "mastery_score": n.mastery_score,
        "resources": n.resources or [],
        "depends_on": n.depends_on or [],
        "tags": n.tags or [],
        "auto_generated": n.auto_generated,
        "is_hidden": n.is_hidden if hasattr(n, 'is_hidden') else False,
        "is_custom": n.is_custom if hasattr(n, 'is_custom') else False,
        "learning_goal": getattr(n, 'learning_goal', None),
        "self_assessment": getattr(n, 'self_assessment', None),
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def _graph_summary(g: LearningGraph) -> dict:
    return {
        "id": g.id,
        "external_id": getattr(g, "external_id", None),
        "graph_revision": getattr(g, "graph_revision", 0) or 0,
        "graph_summary": getattr(g, "graph_summary", None),
        "user_id": g.user_id,
        "title": g.title,
        "goal": g.goal,
        "goal_type": g.goal_type,
        "source_type": g.source_type,
        "planning_rationale": getattr(g, 'planning_rationale', None),
        "status": g.status,
        "is_primary": g.is_primary,
        "total_nodes": g.total_nodes,
        "completed_nodes": g.completed_nodes,
        "progress_pct": g.progress_pct,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "updated_at": g.updated_at.isoformat() if g.updated_at else None,
    }


def _real_resources_for_node(node: GraphNode, learner_state: Any | None = None) -> list[dict]:
    """Select a real resource order from the shared learner projection."""
    labels = {
        "document": ("讲义", "document"), "question": ("练习", "question"),
        "mindmap": ("思维导图", "mindmap"), "code": ("代码示例", "code"),
        "ppt_interactive": ("互动课堂", "ppt"), "tutor": ("辅导解析", "tutor"),
    }
    types = resource_blueprint(learner_state or {}, knowledge_type=str(node.knowledge_type or "theory"))
    resources = []
    for resource_type in types:
        suffix, agent = labels[resource_type]
        resources.append({
            "artifact_id": None, "type": resource_type, "title": f"{node.title} {suffix}",
            "status": "generating", "agent": agent, "created_at": datetime.now().isoformat(),
        })
    return resources


async def _trigger_resource_generation(
    db: AsyncSession, node: GraphNode, user_id: int
) -> None:
    """触发真实 agent 生成资源并保存 artifact ID"""
    try:
        from services.llm_service_real import get_llm_service
    except ImportError as e:
        logger.warning(f"[learning_graph] LLM service import failed: {e}")
        node.status = "generated"  # fallback: keep existing resource plans
        await db.commit()
        return

    try:
        llm = await get_llm_service()
    except Exception as e:
        logger.error(f"[learning_graph] LLM service init failed: {e}")
        node.status = "failed"
        await db.commit()
        return

    from database.models import GeneratedItem
    all_generated = True
    any_success = False

    for resource in (node.resources or []):
        if resource.get("artifact_id"):
            any_success = True
            continue

        resource_type = resource.get("type", "document")
        topic = resource.get("title", node.title)

        try:
            prompt = _build_generation_prompt(node, resource_type)
            result = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=2000,
            )
            content = result.get("content", "") if isinstance(result, dict) else str(result)

            if not content or len(content.strip()) < 10:
                raise ValueError(f"Empty or too-short response ({len(content)} chars)")

            artifact = GeneratedItem(
                user_id=user_id,
                type=resource_type,
                title=topic,
                content=content[:8000],
                raw_data={"source": "learning_graph", "node_id": node.id, "resource_type": resource_type},
            )
            db.add(artifact)
            await db.commit()
            await db.refresh(artifact)

            resource["artifact_id"] = artifact.id
            resource["status"] = "generated"
            any_success = True
            logger.info(f"[learning_graph] Generated artifact #{artifact.id} ({resource_type}) for node #{node.id}, content={len(content)} chars")

        except Exception as e:
            resource["status"] = "failed"
            resource["error"] = str(e)[:200]
            all_generated = False
            logger.error(f"[learning_graph] Failed {resource_type} for node #{node.id}: {e}")

    # Flag JSON column as modified (in-place mutations not auto-detected by SQLAlchemy)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(node, "resources")
    node.status = "generated" if any_success else "failed"
    await db.commit()
    logger.info(f"[learning_graph] Node #{node.id} generation complete: status={node.status}, any_success={any_success}")


def _build_generation_prompt(node: GraphNode, resource_type: str) -> str:
    """构建不同类型资源的 LLM 提示词"""
    base = f"学习主题：{node.title}\n描述：{node.description or '无'}\n知识类型：{node.knowledge_type}\n难度：{node.difficulty}/5\n"

    prompts = {
        "document": base + "请生成一份结构清晰的学习讲义，包含：概念定义、核心原理、关键公式或代码、实例说明。使用Markdown格式。",
        "question": base + "请生成5道练习题，包含选择题和简答题。每道题标注正确答案和简要解析。使用Markdown格式。",
        "mindmap": base + "请生成一份思维导图大纲，使用缩进结构表示层级关系。中心主题为学习主题，展开3-4层分支。",
        "code": base + "请提供可运行的代码示例，包含注释解释关键逻辑。使用Python语言。",
        "tutor": base + "请以辅导老师身份，用通俗易懂的方式讲解这个主题的核心概念。用对话式语气。",
        "ppt_interactive": base + "请生成互动课堂的幻灯片大纲，包含5-7页幻灯片标题和每页要点。",
        "video": base + "请生成一段5-8分钟教学视频的脚本大纲，包含开场、核心讲解、示例演示和总结。",
    }
    return prompts.get(resource_type, prompts["document"])


def _build_node_tree(nodes: list[GraphNode]) -> list[dict]:
    """构建节点树（父子层级）"""
    by_parent: dict[int | None, list[dict]] = {}
    node_map = {n.id: _node_detail(n) for n in nodes}
    for n in nodes:
        pid = n.parent_id
        if pid not in by_parent:
            by_parent[pid] = []
        by_parent[pid].append(node_map[n.id])

    def build_tree(parent_id: int | None) -> list[dict]:
        children = by_parent.get(parent_id, [])
        for child in children:
            child["children"] = build_tree(child["id"])
        return children

    return build_tree(None)


def _mock_resources_for_node(node: GraphNode) -> list[dict]:
    """为节点生成模拟资源（后续由 T07-T09 的 agent 流程真实生成）"""
    resources = [
        {
            "artifact_id": None,
            "type": "lecture",
            "title": f"{node.title} 讲义",
            "status": "pending",
            "agent": "document_generate",
            "created_at": datetime.now().isoformat(),
        },
        {
            "artifact_id": None,
            "type": "question",
            "title": f"{node.title} 练习",
            "status": "pending",
            "agent": "question_generate",
            "created_at": datetime.now().isoformat(),
        },
    ]
    # 根据知识类型动态添加
    if node.knowledge_type in ("code", "practice"):
        resources.append({
            "artifact_id": None,
            "type": "code",
            "title": f"{node.title} 代码示例",
            "status": "pending",
            "agent": "code_generate",
            "created_at": datetime.now().isoformat(),
        })
    elif node.knowledge_type in ("visual",):
        resources.append({
            "artifact_id": None,
            "type": "mindmap",
            "title": f"{node.title} 思维导图",
            "status": "pending",
            "agent": "mindmap_generate",
            "created_at": datetime.now().isoformat(),
        })
    return resources
