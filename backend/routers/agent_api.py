"""
8 Agent 互通 API - 双入口

入口 1: POST /api/agent/{agent_name}/invoke
- 单 Agent 调用，自动注入 SharedContext
- 自动从 DB 加载用户最新画像
- 自动持久化该 agent 的产出到 DB

入口 2: POST /api/agent/workflow/run
- 全工作流调用（与现有 /api/chat/stream 能力相同）
- 流式返回每个节点结果

辅助端点:
- GET  /api/agent/registry        - 列出所有可用 agent + 依赖
- GET  /api/agent/{agent_name}/ctx - 读取某用户的完整 SharedContext 快照

设计原则：
1. 8 agent 互通基于 SharedContext（强类型 Pydantic）
2. router 不重复实现业务逻辑，只做 ctx 装配 + DB 持久化
3. 与现有 /api/agents-day4/* /api/chat/* /api/wizard/* 完全并行，不替代
"""
import logging
import asyncio
import time
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.models import User, StudentProfile, AgentInvocation, GeneratedItem
from database.dependencies import get_db
from utils.auth import get_current_user_id, require_self

from agents import (
    AGENT_REGISTRY,
    AGENT_WRITES,
    SharedContext,
    BaseAgent,
)
from graph.runtime import execute_workflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent-shared-context"])


# ============= Schemas =============

class SingleAgentRequest(BaseModel):
    """单 Agent 调用请求"""
    thread_id: Optional[str] = "default"
    input: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # 可选：上游 context 注入（其他 agent 已经写入的字段）
    upstream: Optional[Dict[str, Any]] = Field(default_factory=dict)
    persist: bool = True  # 是否持久化到 DB


class WorkflowRequest(BaseModel):
    """全工作流调用请求"""
    user_query: str
    thread_id: Optional[str] = "default"
    chat_history: Optional[List[Dict[str, Any]]] = None
    # 注入已有画像
    existing_profile: Optional[Dict[str, Any]] = None


# ============= 辅助函数 =============

async def _load_latest_profile(db: AsyncSession, user_id: int) -> Optional[Dict[str, Any]]:
    """从 DB 加载用户最新画像"""
    result = await db.execute(
        select(StudentProfile)
        .where(StudentProfile.user_id == user_id)
        .order_by(StudentProfile.id.desc())
        .limit(1)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        return None
    # 转 dict
    return _profile_to_dict(profile)


def _profile_to_dict(profile: StudentProfile) -> Dict[str, Any]:
    """StudentProfile ORM → dict"""
    def _try_get(attr):
        try:
            v = getattr(profile, attr, None)
            if v is None:
                return None
            if isinstance(v, (dict, list, str, int, float, bool)):
                return v
            # 尝试 json 解析
            import json
            return json.loads(v) if isinstance(v, str) else v
        except Exception:
            return None

    return {
        "major": _try_get("major"),
        "grade_level": _try_get("grade_level"),
        "target_role": _try_get("target_role"),
        "prior_courses": _try_get("prior_courses") or [],
        "preferred_language": _try_get("preferred_language"),
        "skill_matrix": _try_get("skill_matrix") or {},
        "cognitive_profile": _try_get("cognitive_profile") or {},
        "misconception_archive": _try_get("misconception_archive") or {},
        "behavior_pattern": _try_get("behavior_pattern") or {},
        "motivation_profile": _try_get("motivation_profile") or {},
        "confidence_score": _try_get("confidence_score") or 0.5,
        "profile_version": _try_get("profile_version") or 1,
    }


async def _persist_agent_output(
    db: AsyncSession, user_id: int, agent_short_name: str, output: Any
) -> bool:
    """持久化 agent 产出到 DB
    - profile  → StudentProfile
    - video    → LearningResource
    - 其他（document/question/mindmap/code/path）→ 暂存到 user_data 或日志
    """
    if output is None:
        return False

    try:
        if agent_short_name == "profile" and isinstance(output, dict):
            # 写/更新 StudentProfile
            existing = await db.execute(
                select(StudentProfile)
                .where(StudentProfile.user_id == user_id)
                .order_by(StudentProfile.id.desc())
                .limit(1)
            )
            profile = existing.scalar_one_or_none()
            if profile is None:
                profile = StudentProfile(user_id=user_id)
                db.add(profile)

            # 字段映射
            import json
            for k in ("major", "grade_level", "target_role", "preferred_language"):
                v = output.get(k)
                if v is not None:
                    setattr(profile, k, str(v))
            for k in ("skill_matrix", "cognitive_profile", "misconception_archive",
                      "behavior_pattern", "motivation_profile", "prior_courses"):
                v = output.get(k)
                if v is not None:
                    setattr(profile, k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v)
            v = output.get("confidence_score")
            if v is not None:
                try:
                    profile.confidence_score = float(v)
                except (TypeError, ValueError):
                    pass
            v = output.get("profile_version")
            if v is not None:
                try:
                    profile.profile_version = int(v)
                except (TypeError, ValueError):
                    pass
            await db.commit()
            logger.info(f"[persist] profile saved for user {user_id}")

            # 同时写入 generated_items，让前端智能体页面产物列表可见
            profile_title = "学情分析报告"
            if output.get("major") or output.get("target_role"):
                bits = [b for b in [output.get("major"), output.get("grade_level"), output.get("target_role")] if b]
                profile_title = f"学情分析：{' · '.join(bits[:2])}"
            profile_item = GeneratedItem(
                user_id=user_id,
                type="profile",
                title=profile_title,
                chapter="",
                content=json.dumps(output, ensure_ascii=False),
                raw_data=output,
            )
            db.add(profile_item)
            await db.commit()
            logger.info(f"[persist] profile also saved to generated_items for user {user_id}")
            return True

        if agent_short_name == "video" and isinstance(output, dict):
            # 写 LearningResource（resource_type='video'）
            from database.models import LearningResource
            res = LearningResource(
                user_id=user_id,
                resource_type="video",
                title=output.get("title", "Generated Video"),
                topic=output.get("topic", ""),
                difficulty=output.get("difficulty", "中级"),
                content={
                    "url": output.get("url", ""),
                    "duration": output.get("duration", 60),
                    "provider": output.get("provider", ""),
                    "ratio": output.get("ratio", "16:9"),
                    "resolution": output.get("resolution", "720p"),
                    "object_name": output.get("object_name", ""),
                    "stored_in_oss": output.get("stored_in_oss", False),
                    # 兜底：把 script / transcript 一并存到 content，
                    # 否则 fallback 路径下用户看不到讲义
                    "script": output.get("script", ""),
                    "transcript": output.get("transcript", ""),
                },
            )
            db.add(res)
            await db.commit()
            logger.info(f"[persist] video saved for user {user_id}")
            return True

        # document / question / mindmap / code / path / tutor → generated_items 表
        persistable = {"document", "question", "mindmap", "code", "path", "tutor"}
        if agent_short_name in persistable and isinstance(output, dict):
            content_str = ""
            try:
                import json
                content_str = json.dumps(output, ensure_ascii=False)
            except Exception:
                content_str = str(output)

            item = GeneratedItem(
                user_id=user_id,
                type=agent_short_name,
                title=output.get("title") or output.get("topic") or output.get("question") or str(output.get("name", "")) or f"{agent_short_name} 生成",
                chapter=output.get("chapter") or output.get("summary") or output.get("subtitle") or "",
                content=content_str,
                raw_data=output,
            )
            db.add(item)
            await db.commit()
            logger.info(f"[persist] {agent_short_name} saved to generated_items for user {user_id}")
            return True

        # 未知 agent 类型
        logger.warning(f"[persist] unknown agent type: {agent_short_name}, output type: {type(output).__name__}")
        return False

    except Exception as e:
        logger.exception(f"[persist] 失败: user={user_id} agent={agent_short_name}: {e}")
        await db.rollback()
        return False


# ============= 0a. 智能体调用记录（Agent 记录页数据源） =============

_AGENT_LABEL_MAP = {
    "profile": "学情分析智能体",
    "document": "资料整理智能体",
    "question": "试题生成智能体",
    "mindmap": "思维导图智能体",
    "code": "代码示例智能体",
    "path": "路径规划智能体",
    "tutor": "答疑协作智能体",
    "video": "课堂视频智能体",
}


async def _latest_generated_item_id(db: AsyncSession, user_id: int, agent_short_name: str) -> Optional[int]:
    """取某用户某 agent 类型的最新 generated_item.id（用于关联 invocation.output_id）"""
    result = await db.execute(
        select(GeneratedItem.id)
        .where(GeneratedItem.user_id == user_id, GeneratedItem.type == agent_short_name)
        .order_by(GeneratedItem.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _record_invocation(
    db: AsyncSession,
    user_id: int,
    agent_name: str,
    input_data: Optional[Dict[str, Any]],
    output_id: Optional[int],
    output_type: Optional[str],
    status: str,
    duration_ms: int,
    error_message: Optional[str] = None,
) -> None:
    """把一次 agent 调用落库到 agent_invocations（Agent 记录页数据源）。

    - 成功 / 失败 / running 都会落
    - input_summary 从 input_data 提取 topic/question/title/goal 中第一个非空值
    - 错误日志：失败时也落库，让记录页能看到失败原因
    """
    summary = ""
    if input_data:
        for key in ("topic", "question", "title", "goal", "name"):
            value = input_data.get(key)
            if value:
                summary = str(value)[:120]
                break
    if not summary and error_message:
        summary = f"调用失败：{str(error_message)[:80]}"

    record = AgentInvocation(
        user_id=user_id,
        agent_name=agent_name,
        agent_label=_AGENT_LABEL_MAP.get(agent_name, agent_name),
        target=agent_name,
        status=status,
        duration_ms=duration_ms,
        input_summary=summary or None,
        output_id=output_id,
        output_type=output_type,
        error_message=(error_message or "")[:480] or None,
    )
    db.add(record)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise


# ============= 0. 列出所有 agent =============

@router.get("/registry", summary="列出所有可用 agent + 字段依赖")
async def list_agents():
    """前端可调用此端点动态渲染 8 大智能体卡片"""
    return {
        "success": True,
        "data": [
            {
                "short_name": name,
                "class": cls.__name__,
                "produces": SharedContext.produces(name),
                "depends_on": SharedContext.dependencies(name),
            }
            for name, cls in AGENT_REGISTRY.items()
        ],
        "total": len(AGENT_REGISTRY),
    }


# ============= 1. 单 Agent 调用 =============

@router.post("/{agent_name}/invoke", summary="单 Agent 调用 - 自动注入 SharedContext")
async def invoke_single_agent(
    agent_name: str,
    req: SingleAgentRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """POST /api/agent/{name}/invoke
    - 加载用户最新画像到 ctx
    - 注入请求中的 upstream（其他 agent 已写入的字段）
    - 调目标 agent._process(input)
    - 持久化该 agent 产出（按 AGENT_WRITES）
    - 返回 ctx 快照
    """
    if agent_name not in AGENT_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知 agent: {agent_name}，可选: {list(AGENT_REGISTRY)}")

    agent_cls = AGENT_REGISTRY[agent_name]
    started_at = time.monotonic()

    # 1. 构造 ctx
    profile = await _load_latest_profile(db, user_id)
    ctx = SharedContext(user_id=user_id, thread_id=req.thread_id)
    if profile:
        ctx.set("student_profile", profile)
        ctx.set("profile_confidence", profile.get("confidence_score", 0.5))

    # 2. 注入 upstream（如有）
    if req.upstream:
        for k, v in req.upstream.items():
            if k in ctx._WRITABLE_FIELDS and v is not None:
                ctx.set(k, v)

    # 3. 实例化 agent + 注入 ctx
    agent: BaseAgent = agent_cls().set_ctx(ctx)

    # 4. 构造 _process 的入参
    input_data = dict(req.input or {})
    input_data.setdefault("user_id", user_id)
    input_data.setdefault("user_query", req.input.get("topic", "") if req.input else "")
    if profile:
        input_data.setdefault("profile", profile)
        input_data.setdefault("existing_profile", profile)
        if not input_data.get("grade"):
            input_data["grade"] = profile.get("grade_level", "")
        if not input_data.get("target_role"):
            input_data["target_role"] = profile.get("target_role", "")

    # 5. 调 agent
    error_message: Optional[str] = None
    output_data: Dict[str, Any] = {}
    try:
        output_data = await agent._process(input_data)
    except Exception as e:
        logger.exception(f"[invoke] agent={agent_name} 失败: {e}")
        error_message = str(e)[:480]
        # 失败也要落一条 invocation，让记录页能看到失败原因
        try:
            await _record_invocation(
                db,
                user_id=user_id,
                agent_name=agent_name,
                input_data=req.input or {},
                output_id=None,
                output_type=None,
                status="failed",
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_message=error_message,
            )
        except Exception:
            logger.exception("[invoke] failed 记录 invocation 失败")
        raise HTTPException(status_code=500, detail=f"agent {agent_name} 处理失败: {error_message}")

    # 6. 组装 result + 写 ctx
    result = agent._build_result(output_data)
    field = SharedContext.produces(agent_name)
    if field and field in result:
        ctx.set(field, result[field])

    # 7. 持久化
    persisted = False
    output_id: Optional[int] = None
    if req.persist and field in result:
        persisted = await _persist_agent_output(db, user_id, agent_name, result[field])
        if persisted:
            # 找出最新写入的 generated_item 拿 id
            output_id = await _latest_generated_item_id(db, user_id, agent_name)

    # 8. 落 invocation 记录（Agent 记录页数据源）
    try:
        await _record_invocation(
            db,
            user_id=user_id,
            agent_name=agent_name,
            input_data=req.input or {},
            output_id=output_id,
            output_type=field or None,
            status="success",
            duration_ms=int((time.monotonic() - started_at) * 1000),
            error_message=None,
        )
    except Exception:
        logger.exception("[invoke] 记录 invocation 失败")

    return {
        "success": True,
        "data": {
            "agent": agent_name,
            "field": field,
            "persisted": persisted,
            "output_id": output_id,
            "ctx_snapshot": ctx.snapshot(),
            "result": result,
        },
    }


# ============= 2. 全工作流调用 =============

@router.post("/workflow/run", summary="全工作流调用 - 流式返回节点结果")
async def run_workflow(
    req: WorkflowRequest,
    user_id: int = Depends(get_current_user_id),
):
    """POST /api/agent/workflow/run
    - 走 graph/workflow.py 的 execute_workflow
    - 流式 SSE 返回每个节点完成事件
    - 适合"完整学习闭环"（learn/practice）
    """
    if not req.user_query.strip():
        raise HTTPException(status_code=400, detail="user_query 不能为空")

    async def event_stream():
        try:
            async for event in execute_workflow(
                user_query=req.user_query,
                chat_history=req.chat_history,
                thread_id=req.thread_id,
                existing_profile=req.existing_profile,
            ):
                # event = {node_name: node_output}
                import json
                node_name = list(event.keys())[0]
                node_output = event[node_name]
                yield f"data: {json.dumps({'node': node_name, 'output': node_output}, ensure_ascii=False, default=str)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.exception(f"[workflow] 失败: {e}")
            import json
            yield f"data: {json.dumps({'error': str(e)[:500]})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ============= 3. 读某用户 ctx 快照 =============

@router.get("/{user_id}/ctx", summary="读取某用户 SharedContext 快照")
async def get_user_ctx(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    """GET /api/agent/{user_id}/ctx
    - 从 DB 加载画像
    - 汇总最近产物（暂只读 profile + video 表）
    """
    require_self(user_id, current_user_id)
    ctx = SharedContext(user_id=user_id)

    # 画像
    profile = await _load_latest_profile(db, user_id)
    if profile:
        ctx.set("student_profile", profile)
        ctx.set("profile_confidence", profile.get("confidence_score", 0.5))

    # 视频（最近一条）
    from database.models import LearningResource
    video_result = await db.execute(
        select(LearningResource)
        .where(LearningResource.user_id == user_id, LearningResource.resource_type == "video")
        .order_by(LearningResource.id.desc())
        .limit(1)
    )
    last_video = video_result.scalar_one_or_none()
    if last_video:
        # content 可能是 dict / JSON 字符串 / None - 三种都安全处理
        raw_content = last_video.content
        if isinstance(raw_content, dict):
            content = raw_content
        elif isinstance(raw_content, str):
            try:
                import json
                content = json.loads(raw_content) if raw_content.strip() else {}
            except (ValueError, TypeError):
                content = {}
        else:
            content = {}
        ctx.set("video_result", {
            "url": content.get("url", ""),
            "title": last_video.title or "",
            "topic": last_video.topic or "",
            "duration": content.get("duration", 60),
            # 修复 Q7：把 provider/ratio/resolution/script/transcript 也透出到 ctx
            "provider": content.get("provider", ""),
            "ratio": content.get("ratio", "16:9"),
            "resolution": content.get("resolution", "720p"),
            "script": content.get("script", ""),
            "transcript": content.get("transcript", ""),
        })

    return {
        "success": True,
        "user_id": user_id,
        "ctx": ctx.snapshot(),
        "available_fields": sorted(ctx._WRITABLE_FIELDS),
    }
