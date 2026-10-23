"""
Agent 记录页 API

- GET  /api/agent-records                列表（分页 + 筛选）
- GET  /api/agent-records/stats           顶部状态卡 + 整体健康度 + 触发成功率
- GET  /api/agent-records/trend           近 7 日调用趋势
- GET  /api/agent-records/recent-outputs  最近 N 条产物（join generated_items）
- GET  /api/agent-records/{id}            单条调用详情（含产物全文）

数据源：agent_invocations 表（由 routers/agent_api.py 写入）。
"""
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import AgentInvocation, GeneratedItem
from utils.auth import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-records", tags=["agent-records"])


# ============= 1. 列表 =============

@router.get("", summary="Agent 调用记录列表")
async def list_invocations(
    status: Optional[str] = Query(None, description="success / failed / running"),
    agent_name: Optional[str] = Query(None, description="agent 短名"),
    keyword: Optional[str] = Query(None, description="匹配 input_summary / agent_label"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Agent 记录页左侧列表。"""
    base = select(AgentInvocation).where(AgentInvocation.user_id == current_user_id)
    if status and status != "all":
        base = base.where(AgentInvocation.status == status)
    if agent_name:
        base = base.where(AgentInvocation.agent_name == agent_name)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        base = base.where(AgentInvocation.input_summary.like(kw) | AgentInvocation.agent_label.like(kw))

    total_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(total_stmt)).scalar() or 0

    rows = (await db.execute(
        base.order_by(desc(AgentInvocation.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()

    return {
        "success": True,
        "data": {
            "list": [
                {
                    "id": row.id,
                    "agent_name": row.agent_name,
                    "agent_label": row.agent_label or row.agent_name,
                    "target": row.target,
                    "status": row.status,
                    "duration_ms": row.duration_ms or 0,
                    "input_summary": row.input_summary or "",
                    "output_id": row.output_id,
                    "output_type": row.output_type,
                    "error_message": row.error_message,
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


# ============= 2. 顶部状态卡 + 健康度 + 成功率 =============

@router.get("/stats", summary="Agent 状态卡 / 整体健康度 / 触发成功率")
async def get_stats(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """前端 Agent 记录页顶部 5 个状态卡：在线 / 忙碌 / 空闲 / 整体健康度 / 触发成功率。

    - 在线 / 忙碌 / 空闲：根据 8 个 agent 在最近一次调用时的状态聚合（无调用则为「在线」）
    - 整体健康度：按 7 日调用成功率分档（优秀 ≥95 / 良好 ≥85 / 一般 ≥70 / 待优化 <70）
    - 触发成功率：成功调用 / 总调用（近 30 天）
    """
    # 近 30 天总调用 + 成功调用
    success_count = (await db.execute(
        select(func.count()).select_from(AgentInvocation).where(
            and_(AgentInvocation.user_id == current_user_id, AgentInvocation.status == "success")
        )
    )).scalar() or 0
    total_count = (await db.execute(
        select(func.count()).select_from(AgentInvocation).where(AgentInvocation.user_id == current_user_id)
    )).scalar() or 0
    failed_count = (await db.execute(
        select(func.count()).select_from(AgentInvocation).where(
            and_(AgentInvocation.user_id == current_user_id, AgentInvocation.status == "failed")
        )
    )).scalar() or 0

    success_rate = round((success_count / total_count) * 100, 1) if total_count else 100.0

    if success_rate >= 95:
        health = "优秀"
    elif success_rate >= 85:
        health = "良好"
    elif success_rate >= 70:
        health = "一般"
    elif total_count:
        health = "待优化"
    else:
        health = "良好"  # 零调用默认良好

    # 8 个 agent 的最近一次状态 → 分布
    # 通过窗口函数取每个 agent_name 的最新 status（SQLite / PG 都用 row_number）
    from sqlalchemy import over
    subq = (
        select(
            AgentInvocation.agent_name,
            AgentInvocation.status,
            func.row_number().over(
                partition_by=AgentInvocation.agent_name,
                order_by=desc(AgentInvocation.created_at)
            ).label("rn"),
        ).where(AgentInvocation.user_id == current_user_id)
        .subquery()
    )
    latest_per_agent = (await db.execute(
        select(subq.c.agent_name, subq.c.status).where(subq.c.rn == 1)
    )).all()

    # 与 8 个固定 agent 名取并集（缺失的视为「在线」）
    all_agents = {"profile", "document", "question", "mindmap", "code", "path", "tutor", "video"}
    latest_map = {row.agent_name: row.status for row in latest_per_agent}
    online = busy = idle = 0
    for name in all_agents:
        s = latest_map.get(name, "success")
        if s == "failed":
            idle += 1
        elif s == "running":
            busy += 1
        else:
            online += 1

    return {
        "success": True,
        "data": {
            "online": online,
            "busy": busy,
            "idle": idle,
            "total_agents": len(all_agents),
            "health": health,
            "success_rate": success_rate,
            "total_invocations": total_count,
            "success_invocations": success_count,
            "failed_invocations": failed_count,
        },
    }


# ============= 3. 近 7 日调用趋势 =============

@router.get("/trend", summary="近 7 日调用趋势")
async def get_trend(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """返回近 7 天每天的 成功/失败/总调用 数（按 created_at 日期分组）。"""
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    start = today - timedelta(days=6)  # 含今天共 7 天
    rows = (await db.execute(
        select(
            func.date(AgentInvocation.created_at).label("d"),
            AgentInvocation.status,
            func.count().label("c"),
        ).where(
            and_(
                AgentInvocation.user_id == current_user_id,
                func.date(AgentInvocation.created_at) >= start.isoformat(),
            )
        ).group_by(func.date(AgentInvocation.created_at), AgentInvocation.status)
    )).all()

    by_day: dict = {}
    for d, status, c in rows:
        day_str = str(d)
        by_day.setdefault(day_str, {"date": day_str, "success": 0, "failed": 0, "total": 0})
        by_day[day_str][status if status in ("success", "failed") else "success"] += c
        by_day[day_str]["total"] += c

    # 补齐缺失日期
    series = []
    for i in range(7):
        d = start + timedelta(days=i)
        ds = d.isoformat()
        series.append(by_day.get(ds, {"date": ds, "success": 0, "failed": 0, "total": 0}))

    return {"success": True, "data": {"series": series}}


# ============= 4. 最近产物（join generated_items） =============

@router.get("/recent-outputs", summary="最近 N 条产物（按 invocation.output_id 关联）")
async def get_recent_outputs(
    limit: int = Query(6, ge=1, le=20),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """取最近 6 条成功的 invocation，对应 generated_items 拿产物预览。"""
    invs = (await db.execute(
        select(AgentInvocation)
        .where(and_(AgentInvocation.user_id == current_user_id, AgentInvocation.output_id.is_not(None)))
        .order_by(desc(AgentInvocation.created_at))
        .limit(limit)
    )).scalars().all()
    if not invs:
        return {"success": True, "list": []}

    output_ids = [inv.output_id for inv in invs if inv.output_id]
    items = (await db.execute(
        select(GeneratedItem).where(
            and_(GeneratedItem.user_id == current_user_id, GeneratedItem.id.in_(output_ids))
        )
    )).scalars().all()
    item_map = {it.id: it for it in items}

    result = []
    for inv in invs:
        item = item_map.get(inv.output_id) if inv.output_id else None
        if not item:
            continue
        # 估算文件大小（content 字符数 / 200 ≈ KB）
        size_kb = max(1, len(item.content or "") // 200) if item.content else 0
        ext = "PDF" if "ppt" in (item.type or "") else "DOCX"
        result.append({
            "invocation_id": inv.id,
            "output_id": item.id,
            "title": item.title,
            "type": item.type,
            "ext": ext,
            "size_label": f"{size_kb / 1024:.2f} MB" if size_kb >= 1024 else f"{size_kb} KB",
            "agent_label": inv.agent_label or inv.agent_name,
            "created_at": item.created_at.isoformat() if item.created_at else "",
        })
    return {"success": True, "data": {"list": result}}


# ============= 5. 单条详情 =============

@router.get("/{invocation_id}", summary="单条调用详情")
async def get_invocation_detail(
    invocation_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(
        select(AgentInvocation).where(
            and_(AgentInvocation.id == invocation_id, AgentInvocation.user_id == current_user_id)
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="invocation not found")

    output = None
    if row.output_id:
        item = (await db.execute(
            select(GeneratedItem).where(GeneratedItem.id == row.output_id)
        )).scalar_one_or_none()
        if item:
            size_kb = max(1, len(item.content or "") // 200) if item.content else 0
            ext = "PDF" if "ppt" in (item.type or "") else "DOCX"
            output = {
                "id": item.id,
                "title": item.title,
                "type": item.type,
                "ext": ext,
                "size_label": f"{size_kb / 1024:.2f} MB" if size_kb >= 1024 else f"{size_kb} KB",
                "chapter": item.chapter or "",
                "content": item.content or "",
                "raw_data": item.raw_data,
            }

    # profile 智能体的产出写进 StudentProfile（不写 generated_items），
    # 若未关联 output_id，则从 StudentProfile 读画像数据构造 output，避免详情页空白。
    if output is None and row.agent_name == "profile" and row.status == "success":
        from database.models import StudentProfile
        profile_row = (await db.execute(
            select(StudentProfile)
            .where(StudentProfile.user_id == current_user_id)
            .order_by(StudentProfile.id.desc())
            .limit(1)
        )).scalar_one_or_none()
        if profile_row is not None:
            import json

            def _safe_load(raw):
                if raw is None or raw == "":
                    return None
                if isinstance(raw, (dict, list)):
                    return raw
                try:
                    return json.loads(raw)
                except Exception:
                    return None

            skill = _safe_load(profile_row.skill_matrix) or {}
            cognitive = _safe_load(profile_row.cognitive_profile) or {}
            motivation = _safe_load(profile_row.motivation_profile) or {}
            misconception = _safe_load(profile_row.misconception_archive) or {}
            behavior = _safe_load(profile_row.behavior_pattern) or {}

            lines = []
            title = "学情分析报告"
            header_bits = [
                profile_row.major and f"专业：{profile_row.major}",
                profile_row.grade_level and f"年级：{profile_row.grade_level}",
                profile_row.target_role and f"目标岗位：{profile_row.target_role}",
                profile_row.preferred_language and f"偏好语言：{profile_row.preferred_language}",
            ]
            header = "  ".join(b for b in header_bits if b)
            if header:
                lines.append(header)
                lines.append("")

            if skill:
                lines.append("【能力矩阵】")
                # 支持扁平 {chapter: score} 和嵌套 {domain: {kp: score}} 两种结构
                for chap, score in list(skill.items())[:12]:
                    if isinstance(score, dict):
                        # 嵌套：取该域的平均分
                        nums = [v for v in score.values() if isinstance(v, (int, float))]
                        avg = round(sum(nums) / len(nums), 1) if nums else 0
                        kps = "、".join(list(score.keys())[:6])
                        lines.append(f"- {chap}（{avg}）：{kps}")
                    else:
                        try:
                            lines.append(f"- {chap}：{round(float(score), 1)}")
                        except (TypeError, ValueError):
                            lines.append(f"- {chap}：{score}")
                lines.append("")

            if cognitive:
                lines.append("【认知特征】")
                for k, v in list(cognitive.items())[:8]:
                    lines.append(f"- {k}：{v}")
                lines.append("")

            if motivation:
                mp = motivation.get("primary_motivation") if isinstance(motivation, dict) else None
                if mp:
                    lines.append(f"【主要动机】{mp}")
                    lines.append("")

            if misconception:
                lines.append("【常见误区】")
                for k, v in list(misconception.items())[:6]:
                    lines.append(f"- {k}：{v}")
                lines.append("")

            if behavior:
                lines.append("【学习行为】")
                for k, v in list(behavior.items())[:6]:
                    lines.append(f"- {k}：{v}")
                lines.append("")

            if profile_row.confidence_score is not None:
                lines.append(f"【画像置信度】{round(float(profile_row.confidence_score) * 100, 1)}%")

            content = "\n".join(l for l in lines if l is not None).strip() or "画像已生成，但暂无可展示字段。"
            output = {
                "id": None,
                "title": title,
                "type": "profile",
                "ext": "MD",
                "size_label": f"{max(1, len(content) // 200)} KB",
                "chapter": "",
                "content": content,
                "raw_data": {
                    "major": profile_row.major,
                    "grade_level": profile_row.grade_level,
                    "target_role": profile_row.target_role,
                    "preferred_language": profile_row.preferred_language,
                    "confidence_score": profile_row.confidence_score,
                    "profile_version": profile_row.profile_version,
                    "skill_matrix": skill,
                    "cognitive_profile": cognitive,
                    "motivation_profile": motivation,
                    "misconception_archive": misconception,
                    "behavior_pattern": behavior,
                },
            }

    return {
        "success": True,
        "data": {
            "id": row.id,
            "agent_name": row.agent_name,
            "agent_label": row.agent_label or row.agent_name,
            "target": row.target,
            "status": row.status,
            "duration_ms": row.duration_ms or 0,
            "input_summary": row.input_summary or "",
            "error_message": row.error_message,
            "output_id": row.output_id,
            "output_type": row.output_type or ("profile" if row.agent_name == "profile" else None),
            "output": output,
            "created_at": row.created_at.isoformat() if row.created_at else "",
        },
    }
