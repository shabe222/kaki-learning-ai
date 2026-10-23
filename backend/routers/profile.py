"""画像路由 - get_profile + analyze_profile_pipeline"""
import json
import asyncio
import logging
from datetime import datetime
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import database.connection as db_conn
from database.models import StudentProfile
from database.dependencies import get_db
from graph.runtime import execute_workflow
from services.cache_service import CacheService
from services.profile_service import load_existing_profile, save_profile, get_profile_status
from services.normalized_learner_state import get_normalized_learner_state
from services.resource_service import save_resources
from services.chat_service import save_chat_session
from utils.text import extract_assistant_summary
from utils.auth import get_current_user_id, require_self

logger = logging.getLogger(__name__)
router = APIRouter()


@router.patch("/api/profile/{user_id}")
async def update_profile_basics(
    user_id: int,
    payload: dict,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    画像页面直接编辑 4 个基础信息字段。
    P0 安全：只能改自己的画像。
    P1 数据流：清除 Redis 缓存使下次拉取拿到最新值。
    P2 健壮性：标量字段必须是 str/int，长度 1-200；prior_courses 是 list[str]；否则 400 拒绝。
    """
    from fastapi import HTTPException

    require_self(user_id, current_user_id)

    # 类型校验：标量字段必须是 str，长度 1-200；prior_courses 必须是 list[str]
    scalar_fields = ["major", "grade_level", "target_role", "preferred_language"]
    for field in scalar_fields:
        v = payload.get(field)
        if v is not None and not isinstance(v, str):
            raise HTTPException(400, detail=f"字段 {field} 必须是字符串，收到 {type(v).__name__}")
        if isinstance(v, str) and len(v) > 200:
            raise HTTPException(400, detail=f"字段 {field} 长度 {len(v)} 超过 200 限制")

    pc = payload.get("prior_courses")
    if pc is not None:
        if not isinstance(pc, list):
            raise HTTPException(400, detail="prior_courses 必须是列表")
        if not all(isinstance(x, str) for x in pc):
            raise HTTPException(400, detail="prior_courses 每项必须是字符串")
        if len(pc) > 50:
            raise HTTPException(400, detail=f"prior_courses 数量 {len(pc)} 超过 50 限制")

    result = await db.execute(
        select(StudentProfile).where(StudentProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        profile = StudentProfile(user_id=user_id)
        db.add(profile)

    editable = scalar_fields + ["prior_courses"]
    try:
        for field in editable:
            if field in payload and payload[field] is not None:
                setattr(profile, field, payload[field])
        await db.commit()
        await db.refresh(profile)  # 重读，确认 setattr 真的落库
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, detail=f"数据库写入失败: {str(e)[:200]}")

    # P2 写后验证：每个传入字段的实际值必须与 payload 一致，否则视为丢失
    mismatched = []
    for field in editable:
        if field in payload and payload[field] is not None:
            actual = getattr(profile, field)
            if actual != payload[field]:
                mismatched.append(f"{field} expected={payload[field]!r} got={actual!r}")
    if mismatched:
        logger.error(f"[PATCH /api/profile/{user_id}] 写后验证失败: {mismatched}")
        raise HTTPException(500, detail=f"数据写入后不一致（疑似丢失）: {'; '.join(mismatched)}")

    try:
        await CacheService.invalidate_profile(user_id)
    except Exception:
        pass

    return {
        "success": True,
        "data": {
            "major": profile.major,
            "grade_level": profile.grade_level,
            "target_role": profile.target_role,
            "preferred_language": profile.preferred_language,
            "prior_courses": profile.prior_courses or []
        },
        "message": "基础信息已更新"
    }


@router.get("/api/profile/{user_id}")
async def get_profile(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取完整学生画像（6大类）。
    P0 修复（账户独立）：只能看自己的画像，否则 403。
    """
    require_self(user_id, current_user_id)
    result = await db.execute(
        select(StudentProfile).where(StudentProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()

    if not profile:
        return {"success": True, "data": None, "message": "暂无画像"}

    # 构建API响应数据
    data = {
        "id": profile.id,
        "user_id": profile.user_id,
        "major": profile.major,
        "grade_level": profile.grade_level,
        "target_role": profile.target_role,
        "prior_courses": profile.prior_courses or [],
        "preferred_language": profile.preferred_language,
        "skill_matrix": profile.skill_matrix or {},
        "capability_matrix": profile.capability_matrix or {},
        "cognitive_profile": profile.cognitive_profile or {},
        "misconception_archive": profile.misconception_archive or {},
        "behavior_pattern": profile.behavior_pattern or {},
        "motivation_profile": profile.motivation_profile or {},
        "profile_data": profile.profile_data or {},
        "overall_score": profile.overall_score,
        "confidence_score": profile.confidence_score,
        "profile_version": profile.profile_version or 1,
        "profile_status": get_profile_status(profile),
        "created_at": str(profile.created_at) if profile.created_at else None,
        "updated_at": str(profile.updated_at) if profile.updated_at else None
    }
    # 统一适配：从 normalized_profile 填充旧结构化字段
    from services.profile_onboarding import enrich_profile_from_normalized
    data = enrich_profile_from_normalized(data)
    return {"success": True, "data": data}


@router.get("/api/profile/{user_id}/learner-state")
async def get_learner_state(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return the shared read-only learner projection for the authenticated user."""
    require_self(user_id, current_user_id)
    state = await get_normalized_learner_state(db, user_id)
    return {"success": True, "data": state.model_dump(mode="json")}


@router.post("/api/profile/analyze/{user_id}")
async def analyze_profile_pipeline(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
):
    """基于已有画像数据，一键触发多 Agent 协作链路，SSE 流式返回"""
    require_self(user_id, current_user_id)

    async def event_generator():
        try:
            # 1. 从数据库加载现有画像
            if not db_conn._async_session_factory:
                yield f"data: {json.dumps({'type': 'error', 'message': '数据库未初始化'}, ensure_ascii=False)}\n\n"
                return

            async with db_conn._async_session_factory() as db:
                existing_profile = await load_existing_profile(db, user_id)
                if not existing_profile:
                    yield f"data: {json.dumps({'type': 'error', 'message': '未找到学习画像，请先完成画像构建向导'}, ensure_ascii=False)}\n\n"
                    return

                # 2. 构造分析指令（基于画像的智能分析）
                profile_summary = {
                    "major": existing_profile.get("major", "未知"),
                    "grade_level": existing_profile.get("grade_level", "未知"),
                    "target_role": existing_profile.get("target_role", "未知"),
                    "confidence_score": existing_profile.get("confidence_score", 0),
                    "skill_matrix": existing_profile.get("skill_matrix", {}),
                    "cognitive_profile": existing_profile.get("cognitive_profile", {}),
                }

                # 识别薄弱技能点
                weak_skills = []
                strong_skills = []
                skill_matrix = existing_profile.get("skill_matrix", {})
                for category, skills in skill_matrix.items():
                    if isinstance(skills, dict):
                        for skill_name, score in skills.items():
                            if isinstance(score, (int, float)):
                                if score <= 2:
                                    weak_skills.append(f"{category}/{skill_name} (得分:{score})")
                                elif score >= 4:
                                    strong_skills.append(f"{category}/{skill_name} (得分:{score})")

                synthesis = f"""基于以下学生画像进行深度分析，为该学生生成个性化机器学习学习资源：

【学生基本信息】
- 专业：{profile_summary['major']}
- 年级：{profile_summary['grade_level']}
- 目标：{profile_summary['target_role']}
- 画像置信度：{profile_summary['confidence_score']}

【薄弱环节（需重点提升）】
{'; '.join(weak_skills) if weak_skills else '暂无数据'}

【优势领域（可进一步深化）】
{'; '.join(strong_skills) if strong_skills else '暂无数据'}

请基于以上画像，生成适合该学生的：
1. 个性化教学文档
2. 针对性练习题
3. 知识思维导图
4. 代码实践案例
5. 学习路径规划"""

                logger.info(f"[ProfileAnalyze] user_id={user_id}, weak_count={len(weak_skills)}, strong_count={len(strong_skills)}")

                # 3. 执行多 Agent 工作流
                last_state = {}
                async for event in execute_workflow(
                    user_query=synthesis,
                    chat_history=[],
                    thread_id=f"profile_analyze_{user_id}_{int(datetime.utcnow().timestamp())}",
                    existing_profile=existing_profile
                ):
                    if not event:
                        continue
                    node_name = list(event.keys())[0]
                    node_output = event.get(node_name, {})
                    last_state = node_output

                    yield f"data: {json.dumps({'type': 'agent_start', 'agent': node_name}, ensure_ascii=False)}\n\n"

                    # 流式推送内容摘要
                    if isinstance(node_output, dict) and node_output:
                        if node_output.get("student_profile"):
                            yield f"data: {json.dumps({'type': 'agent_content', 'agent': node_name, 'resource_type': 'profile', 'preview': '画像分析完成'}, ensure_ascii=False)}\n\n"
                        for key in ['document', 'questions', 'mindmap', 'code_examples']:
                            val = node_output.get(key)
                            if val:
                                yield f"data: {json.dumps({'type': 'agent_content', 'agent': node_name, 'resource_type': key, 'preview': str(val)[:200]}, ensure_ascii=False)}\n\n"
                        if node_output.get("video_result"):
                            v = node_output["video_result"]
                            dur = v.get("duration", 0)
                            res = v.get("resolution", "720p")
                            vu = v.get("url", "")
                            yield "data: " + json.dumps({
                                "type": "agent_content",
                                "agent": node_name,
                                "resource_type": "video",
                                "preview": "教学视频已生成 ({}s, {})".format(dur, res),
                                "video_url": vu,
                            }, ensure_ascii=False) + "\n\n"

                    yield f"data: {json.dumps({'type': 'agent_complete', 'agent': node_name, 'status': 'success'}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.1)

                # 4. 持久化结果
                if last_state.get("student_profile"):
                    await save_profile(db, user_id, last_state["student_profile"])
                    await CacheService.cache_profile(user_id, last_state["student_profile"])
                    logger.info(f"[ProfileAnalyze] 画像已更新 user_id={user_id}")

                await save_resources(db, user_id, f"profile_analyze_{user_id}", last_state)
                summary = extract_assistant_summary(last_state)
                await save_chat_session(db, user_id, f"profile_analyze_{user_id}", [], synthesis, summary)

                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"[ProfileAnalyze] 错误: {str(e)}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*"
        }
    )


@router.get("/api/learning-path-deprecated/{user_id}")
async def get_learning_path_legacy(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    [已废弃] 学习路径聚合端点（profile + library 双源融合）
    前端已迁移至 /api/learning-path/{user_id}（来自 routers/learning_path.py 的 9 章版本）
    保留此接口仅为兼容旧调用方，请尽快切换。
    """
    require_self(user_id, current_user_id)

    # 1) 画像（数据真实 1/2）
    result = await db.execute(
        select(StudentProfile).where(StudentProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        return {
            "success": True,
            "data": None,
            "message": "暂无画像，请先完成画像采集向导",
            "has_profile": False,
        }

    # 2) 资料库章节 mastery（数据真实 2/2）
    #    与 /api/materials/library 口径保持一致：同 KNOWLEDGE_GRAPH + 同 material_service
    from services.material_service import get_stage, get_stage_config, get_user_chapter_mastery
    from services.knowledge_graph import KNOWLEDGE_GRAPH, CHAPTER_ORDER

    stages = []
    for ch_id in CHAPTER_ORDER:
        ch = KNOWLEDGE_GRAPH.get(ch_id, {})
        mastery = await get_user_chapter_mastery(db, user_id, ch_id)
        stage = get_stage(mastery)
        stage_cfg = get_stage_config(stage)
        kps = ch.get("knowledge_points", []) or []
        # 根据 mastery 划分优先级：< 0.3 = 高优，< 0.7 = 中优，>= 0.7 = 低优
        if mastery < 0.3:
            priority = 0
        elif mastery < 0.7:
            priority = 1
        else:
            priority = 2
        stages.append({
            "id": ch_id,
            "name": ch.get("name", ch_id),
            "icon": ch.get("icon", "📌"),
            "difficulty": ch.get("difficulty", 3),
            "mastery": round(mastery, 3),
            "priority": priority,
            "stage": stage,
            "stage_label": stage_cfg["label"],
            "knowledge_points": [
                kp if isinstance(kp, dict) else {"name": str(kp)}
                for kp in kps
            ],
        })

    # 按优先级排序：薄弱章节优先（mastery 越低越靠前）
    stages.sort(key=lambda s: (s["priority"], s["mastery"]))

    # 3) 进度聚合
    total_chapters = len(stages)
    completed_chapters = sum(1 for c in stages if c["mastery"] >= 1.0)

    # 知识点完成度 = 去重集合：mastery >= 1.0  OR  cap.score >= 4
    # key 格式 = "<chapter_id>::<kp_name>"，避免同 kp 在两源中重复 +1
    completed_kp_set: set = set()
    for c in stages:
        for kp in c["knowledge_points"]:
            if not isinstance(kp, dict):
                continue
            kp_name = kp.get("name") or kp.get("id") or ""
            if not kp_name:
                continue
            if kp.get("mastery", 0) >= 1.0:
                completed_kp_set.add(f"{c['id']}::{kp_name}")

    # 画像中的 capability_matrix 用于校正
    cap = profile.capability_matrix or {}
    if isinstance(cap, dict) and cap:
        for ch_id, kp_data in cap.items():
            if not isinstance(kp_data, dict):
                continue
            for kp_name, kp_v in kp_data.items():
                if not isinstance(kp_v, dict):
                    continue
                if kp_v.get("score", 0) >= 4:
                    completed_kp_set.add(f"{ch_id}::{kp_name}")

    completed_kp = len(completed_kp_set)
    total_kp = sum(len(c.get("knowledge_points", [])) for c in stages)

    # 当前阶段 = 第一个 mastery < 1.0 的章节序号（1-based）
    current_stage_idx = next(
        (i + 1 for i, c in enumerate(stages) if c["mastery"] < 1.0),
        total_chapters,
    )
    current_stage_name = (
        stages[current_stage_idx - 1]["name"]
        if 1 <= current_stage_idx <= total_chapters
        else "已全部掌握"
    )

    # 预计剩余天数（基于 behavior_pattern）
    behavior = profile.behavior_pattern or {}
    weekly_hours = float(behavior.get("weekly_hours") or 10)
    avg_session = float(behavior.get("avg_session_minutes") or 30)
    if avg_session < 1:
        avg_session = 30
    sessions_per_week = weekly_hours * 60 / avg_session
    remaining_stages = total_chapters - completed_chapters
    est_days = max(1, int(remaining_stages * 7 / max(sessions_per_week, 0.1)))

    return {
        "success": True,
        "data": {
            "user_id": user_id,
            "profile": {
                "major": profile.major,
                "grade_level": profile.grade_level,
                "target_role": profile.target_role,
                "preferred_language": profile.preferred_language,
                "skill_matrix": profile.skill_matrix or {},
                "capability_matrix": profile.capability_matrix or {},
                "cognitive_profile": profile.cognitive_profile or {},
                "misconception_archive": profile.misconception_archive or {},
                "behavior_pattern": profile.behavior_pattern or {},
                "motivation_profile": profile.motivation_profile or {},
                "confidence_score": profile.confidence_score,
                "overall_score": profile.overall_score,
                "updated_at": str(profile.updated_at) if profile.updated_at else None,
            },
            "stages": stages,
            "current_stage": current_stage_idx,
            "current_stage_name": current_stage_name,
            "total_chapters": total_chapters,
            "completed_chapters": completed_chapters,
            "total_kp": total_kp,
            "completed_kp": completed_kp,
            "est_days": est_days,
            "behavior": {
                "weekly_hours": weekly_hours,
                "avg_session_minutes": avg_session,
            },
        },
        "has_profile": True,
    }
