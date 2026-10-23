"""
知识图谱 API — 4 端点

- GET  /knowledge-graph                           : 完整知识图谱
- GET  /knowledge-graph/recommended-path/{uid}    : 推荐学习路径
- GET  /knowledge-graph/personalized-graph/{uid}  : 个性化节点数据
- GET  /knowledge-graph/root-cause/{uid}/{cid}    : 病根溯源

所有端点都严格按 user_id 隔离，绝不串号。
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

import database.connection as db_conn
from database.models import StudentProfile
from services.knowledge_graph import (
    get_knowledge_graph,
    get_recommended_path,
    trace_root_cause,
    build_reverse_dependencies,
    CHAPTER_ORDER,
)
from utils.auth import get_current_user_id, require_self

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wizard", tags=["knowledge-graph"])


@router.get("/knowledge-graph")
async def get_knowledge_graph_endpoint():
    """获取完整知识图谱（7章 + 依赖关系）"""
    graph = get_knowledge_graph()
    reverse_deps = build_reverse_dependencies()
    return {
        "success": True,
        "data": {
            "graph": graph,
            "reverse_dependencies": reverse_deps,
            "chapter_order": list(graph.keys())
        }
    }


@router.get("/knowledge-graph/recommended-path/{user_id}")
async def get_recommended_path_endpoint(user_id: int, current_user_id: int = Depends(get_current_user_id)):
    """根据用户画像生成推荐学习路径（严格按 user_id 隔离）"""
    require_self(user_id, current_user_id)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id 无效")
    async with db_conn._async_session_factory() as db:
        result_obj = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result_obj.scalar_one_or_none()

        chapter_scores = {}
        if profile and profile.capability_matrix:
            for ch_id, ch_data in profile.capability_matrix.items():
                if isinstance(ch_data, dict):
                    score = ch_data.get("章节掌握度", {}).get("score", 0)
                    chapter_scores[ch_id] = round(score * 20)
                else:
                    chapter_scores[ch_id] = ch_data

        path = get_recommended_path(chapter_scores)
        return {
            "success": True,
            "data": {
                "user_id": user_id,
                "path": path,
                "chapter_scores": chapter_scores,
                "has_profile": profile is not None,
            }
        }


@router.get("/knowledge-graph/personalized-graph/{user_id}")
async def get_personalized_graph(user_id: int, current_user_id: int = Depends(get_current_user_id)):
    """获取个性化神经网络结构图数据（按用户画像定制节点大小、推荐路径、重点标记）"""
    require_self(user_id, current_user_id)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id 无效")

    graph = get_knowledge_graph()

    async with db_conn._async_session_factory() as db:
        result_obj = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result_obj.scalar_one_or_none()

        chapter_scores = {}
        if profile and profile.capability_matrix:
            for ch_id, ch_data in profile.capability_matrix.items():
                if isinstance(ch_data, dict):
                    score = ch_data.get("章节掌握度", {}).get("score", 0)
                    chapter_scores[ch_id] = round(score * 20)
                else:
                    chapter_scores[ch_id] = ch_data

    # 计算节点个性化数据
    node_personalization = {}
    for ch_id in CHAPTER_ORDER:
        ch = graph.get(ch_id, {})
        score = chapter_scores.get(ch_id, 0)
        mastery = score / 100 * 5

        # 确定节点状态
        locked = False
        for dep in ch.get("depends_on", []):
            if chapter_scores.get(dep, 0) / 100 * 5 < 1.0:
                locked = True
                break

        if locked:
            state = "locked"
        elif mastery >= 4.0:
            state = "active"
        elif mastery >= 2.5:
            state = "partial"
        elif mastery > 0:
            state = "weak"
        else:
            state = "inactive"

        # 节点大小：薄弱 → 大（需要关注），已掌握 → 小
        if state == "locked":
            node_size = 0.8
            emphasis = 0
        elif state == "active":
            node_size = 0.75
            emphasis = 0
        elif state == "weak":
            node_size = 1.25
            emphasis = 2
        elif state == "partial":
            node_size = 1.0
            emphasis = 1
        else:
            node_size = 1.1
            emphasis = 1

        # 确定推荐优先级
        if locked:
            priority = 4
        elif state == "weak":
            priority = 0
        elif state == "partial":
            priority = 1
        elif state == "inactive":
            priority = 2
        else:
            priority = 3

        node_personalization[ch_id] = {
            "score": score,
            "mastery": round(mastery, 1),
            "state": state,
            "node_size": node_size,
            "emphasis": emphasis,
            "priority": priority,
            "is_locked": locked,
            "locked_by": [d for d in ch.get("depends_on", []) if chapter_scores.get(d, 0) / 100 * 5 < 1.0]
        }

    # 计算推荐学习路径上的连接
    recommended_connections = []
    sorted_chapters = sorted(
        [(cid, node_personalization[cid]["priority"], node_personalization[cid]["mastery"])
         for cid in CHAPTER_ORDER],
        key=lambda x: (x[1], x[2])
    )

    next_focus = None
    for cid, pri, _ in sorted_chapters:
        if pri <= 2 and not node_personalization[cid]["is_locked"]:
            next_focus = cid
            break

    # 标记推荐路径上的连接
    if next_focus:
        visited = set()

        def mark_upstream_path(ch_id):
            if ch_id in visited:
                return
            visited.add(ch_id)
            ch = graph.get(ch_id, {})
            for dep in ch.get("depends_on", []):
                conn_key = f"{dep}->{ch_id}"
                if conn_key not in recommended_connections:
                    recommended_connections.append(conn_key)
                mark_upstream_path(dep)

        def mark_downstream_path(ch_id):
            if ch_id in visited:
                return
            visited.add(ch_id)
            rev_deps = build_reverse_dependencies()
            for downstream in rev_deps.get(ch_id, []):
                conn_key = f"{ch_id}->{downstream}"
                if conn_key not in recommended_connections:
                    recommended_connections.append(conn_key)
                mark_downstream_path(downstream)

        mark_upstream_path(next_focus)
        visited = set()
        mark_downstream_path(next_focus)

    return {
        "success": True,
        "data": {
            "user_id": user_id,
            "has_profile": profile is not None,
            "chapter_scores": chapter_scores,
            "node_personalization": node_personalization,
            "next_focus": next_focus,
            "recommended_connections": recommended_connections,
            "graph": graph,
            "chapter_order": CHAPTER_ORDER
        }
    }


@router.get("/knowledge-graph/root-cause/{user_id}/{chapter_id}")
async def get_root_cause_endpoint(user_id: int, chapter_id: str, current_user_id: int = Depends(get_current_user_id)):
    """病根溯源：分析某章节薄弱的前置依赖原因（严格按 user_id 隔离）"""
    require_self(user_id, current_user_id)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id 无效")
    async with db_conn._async_session_factory() as db:
        result_obj = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result_obj.scalar_one_or_none()

        chapter_scores = {}
        if profile and profile.capability_matrix:
            for ch_id, ch_data in profile.capability_matrix.items():
                if isinstance(ch_data, dict):
                    score = ch_data.get("章节掌握度", {}).get("score", 0)
                    chapter_scores[ch_id] = round(score * 20)
                else:
                    chapter_scores[ch_id] = ch_data

        result = trace_root_cause(chapter_id, chapter_scores)
        return {
            "success": True,
            "data": {
                "user_id": user_id,
                "chapter_id": chapter_id,
                **result,
            }
        }
