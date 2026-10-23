"""
8-Agent 顶层 API 路由 - v5.3 真实 LLM 调用
所有 Agent 产物均已接通数据库持久化。
"""

import json
import logging
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from agents.profile_agent import ProfileAgent
from agents.document_agent import DocumentAgent
from agents.question_agent import QuestionAgent
from agents.mindmap_agent import MindmapAgent
from agents.code_agent import CodeAgent
from agents.path_agent import PathAgent
from agents.tutor_agent import TutorAgent
from agents.video_agent import VideoAgent
from graph.state import AgentState
from database.dependencies import get_db
from database.models import GeneratedItem, LearningResource, StudentProfile
from sqlalchemy import select
from utils.auth import get_current_user_id, require_self

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["agents-day4"])


# ============= Schemas =============

class InitRequest(BaseModel):
    user_id: str
    grade: Optional[str] = "高三"
    target_role: Optional[str] = "软件工程师"
    extra: Optional[Dict[str, Any]] = None


class TopicRequest(BaseModel):
    user_id: Optional[str] = "anonymous"
    topic: str
    count: Optional[int] = 5
    language: Optional[str] = "python"
    duration_sec: Optional[int] = 60
    target_role: Optional[str] = None
    style: Optional[str] = None  # 前端设置面板的风格参数
    mode: Optional[str] = None  # 视频模式：classroom / script / etc.


class AskRequest(BaseModel):
    user_id: Optional[str] = "anonymous"
    question: str
    context: Optional[Dict[str, Any]] = None


# ============= 辅助函数 =============

async def _load_profile(db: AsyncSession, user_id: int) -> dict:
    """从数据库加载学生画像，转为 AgentState 可用的 dict"""
    try:
        result = await db.execute(
            select(StudentProfile)
            .where(StudentProfile.user_id == user_id)
            .order_by(StudentProfile.id.desc())
            .limit(1)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            return {}
        import json as _json
        p = {}
        for field in ("major", "grade_level", "target_role", "preferred_language"):
            v = getattr(profile, field, None)
            if v is not None:
                p[field] = str(v)
        for json_field in ("skill_matrix", "cognitive_profile", "misconception_archive",
                           "behavior_pattern", "motivation_profile", "prior_courses"):
            v = getattr(profile, json_field, None)
            if v is not None:
                try:
                    p[json_field] = _json.loads(v) if isinstance(v, str) else v
                except Exception:
                    p[json_field] = v if isinstance(v, dict) else {}
        p["confidence_score"] = float(getattr(profile, "confidence_score", 0) or 0)
        logger.info(f"[day4] 加载画像成功: user={user_id} grade={p.get('grade_level','?')} major={p.get('major','?')}")
        return p
    except Exception as e:
        logger.warning(f"[day4] 加载画像失败: user={user_id} {e}")
        return {}

def _make_state(user_id: str, profile: dict = None, **overrides) -> AgentState:
    """构建 AgentState 字典（可注入真实画像）"""
    return {
        "user_id": user_id,
        "messages": [],
        "student_profile": profile or {},
        "user_query": "",
        **overrides,
    }


def _parse_user_id(user_id_raw) -> int:
    """安全解析 user_id 为整数，失败返回 0"""
    try:
        return int(user_id_raw)
    except (TypeError, ValueError):
        return 0


def _resolve_authenticated_uid(user_id_raw, current_user_id: int) -> int:
    if user_id_raw in (None, "", "anonymous"):
        return current_user_id
    uid = _parse_user_id(user_id_raw)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="user_id is invalid")
    require_self(uid, current_user_id)
    return uid


async def _persist_day4(db: AsyncSession, uid: int, agent_type: str, result: dict):
    """统一持久化 day4 agent 产出到数据库"""
    try:
        if agent_type == "profile" and isinstance(result, dict):
            existing = await db.execute(
                select(StudentProfile).where(StudentProfile.user_id == uid).order_by(StudentProfile.id.desc()).limit(1)
            )
            profile = existing.scalar_one_or_none()
            if not profile:
                profile = StudentProfile(user_id=uid)
                db.add(profile)
            if result.get("grade_level"):
                profile.grade_level = str(result["grade_level"])
            if result.get("major"):
                profile.major = str(result["major"])
            if result.get("target_role"):
                profile.target_role = str(result["target_role"])
            await db.commit()
            logger.info(f"[day4-persist] profile saved for user {uid}")
            return

        if agent_type == "video" and isinstance(result, dict):
            res = LearningResource(
                user_id=uid,
                resource_type="video",
                title=result.get("title", "教学视频"),
                topic=result.get("topic", ""),
                difficulty="中级",
                content={
                    "url": result.get("url", ""),
                    "duration": result.get("duration", 60),
                    "ratio": result.get("ratio", "16:9"),
                    "script": result.get("script", ""),
                    "transcript": result.get("transcript", ""),
                },
            )
            db.add(res)
            await db.commit()
            logger.info(f"[day4-persist] video saved for user {uid}")
            return

        # document / question / mindmap / code / path / tutor
        content_str = ""
        try:
            content_str = json.dumps(result, ensure_ascii=False)
        except Exception:
            content_str = str(result)
        item = GeneratedItem(
            user_id=uid,
            type=agent_type,
            title=result.get("title") or result.get("topic") or result.get("question") or f"{agent_type} 生成",
            chapter=result.get("chapter") or result.get("summary") or "",
            content=content_str,
            raw_data=result,
        )
        db.add(item)
        await db.commit()
        logger.info(f"[day4-persist] {agent_type} saved to generated_items for user {uid}")
    except Exception as e:
        logger.exception(f"[day4-persist] 失败: user={uid} agent={agent_type}: {e}")
        await db.rollback()


# ============= 1. profile init =============

@router.post("/profile/init", summary="初始化学生画像")
async def profile_init(
    req: InitRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    agent = ProfileAgent()
    uid = _resolve_authenticated_uid(req.user_id, current_user_id)
    profile = await _load_profile(db, uid) if uid > 0 else {}
    state = _make_state(
        str(uid), profile=profile,
        user_query=f"初始化画像：年级={req.grade}，目标={req.target_role}",
    )
    result = await agent(state)
    if uid > 0:
        await _persist_day4(db, uid, "profile", result.get("student_profile", result))
    return {"success": True, "user_id": str(uid), **result}


# ============= 2. document =============

@router.post("/document/generate", summary="生成学习文档/讲义")
async def document_generate(
    req: TopicRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    agent = DocumentAgent()
    uid = _resolve_authenticated_uid(req.user_id, current_user_id)
    profile = await _load_profile(db, uid) if uid > 0 else {}
    state = _make_state(
        str(uid), profile=profile,
        chapter_id=req.topic,
        user_query='请为「' + req.topic + '」生成详细的机器学习讲义文档',
        style=req.style,
    )
    result = await agent(state)
    if uid > 0:
        doc = result.get("document", result)
        artifact_type = "document"
        artifact_title = req.topic
        if req.mode == "interactive-ppt":
            artifact_type = "ppt_interactive"
            artifact_title = f"{req.topic} · 课堂互动PPT"
        elif req.mode == "ppt-handout":
            artifact_type = "ppt_handout"
            artifact_title = f"{req.topic} · PPT讲义"
        payload = doc if isinstance(doc, dict) else result
        if isinstance(payload, dict):
            payload = {**payload, "title": artifact_title, "topic": req.topic, "mode": req.mode}
        await _persist_day4(db, uid, artifact_type, payload if isinstance(payload, dict) else result)
    return {"success": True, "user_id": str(uid), "topic": req.topic, **result}


# ============= 3. question =============

@router.post("/question/generate", summary="生成练习题")
async def question_generate(
    req: TopicRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    agent = QuestionAgent()
    uid = _resolve_authenticated_uid(req.user_id, current_user_id)
    profile = await _load_profile(db, uid) if uid > 0 else {}
    state = _make_state(
        str(uid), profile=profile,
        chapter_id=req.topic,
        user_query='请为「' + req.topic + '」生成 ' + str(req.count or 5) + ' 道练习题',
        difficulty_preference="medium",
        style=req.style,
    )
    result = await agent(state)
    if uid > 0:
        qs = result.get("questions", result)
        await _persist_day4(db, uid, "question", {"title": req.topic, "questions": qs} if isinstance(qs, list) else (qs if isinstance(qs, dict) else result))
    return {"success": True, "user_id": str(uid), "topic": req.topic, **result}


# ============= 4. mindmap =============

@router.post("/mindmap/generate", summary="生成思维导图")
async def mindmap_generate(
    req: TopicRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    agent = MindmapAgent()
    uid = _resolve_authenticated_uid(req.user_id, current_user_id)
    profile = await _load_profile(db, uid) if uid > 0 else {}
    state = _make_state(
        str(uid), profile=profile,
        chapter_id=req.topic,
        user_query='请为「' + req.topic + '」生成思维导图结构',
        style=req.style,
    )
    result = await agent(state)
    if uid > 0:
        mm = result.get("mindmap", result)
        await _persist_day4(db, uid, "mindmap", {"title": req.topic, "mindmap": mm} if isinstance(mm, str) else result)
    return {"success": True, "user_id": str(uid), "topic": req.topic, **result}


# ============= 5. code =============

@router.post("/code/generate", summary="生成代码示例")
async def code_generate(
    req: TopicRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    agent = CodeAgent()
    uid = _resolve_authenticated_uid(req.user_id, current_user_id)
    profile = await _load_profile(db, uid) if uid > 0 else {}
    state = _make_state(
        str(uid), profile=profile,
        chapter_id=req.topic,
        user_query='请用 ' + (req.language or 'python') + ' 为「' + req.topic + '」写代码示例',
        preferred_language=req.language,
        style=req.style,
    )
    result = await agent(state)
    if uid > 0:
        code = result.get("code_examples", result)
        await _persist_day4(db, uid, "code", {"title": req.topic, "code_examples": code} if isinstance(code, list) else result)
    return {"success": True, "user_id": str(uid), "topic": req.topic, "language": req.language, **result}


# ============= 6. path =============

@router.post("/path/generate", summary="生成学习路径")
async def path_generate(
    req: TopicRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    agent = PathAgent()
    uid = _resolve_authenticated_uid(req.user_id, current_user_id)
    profile = await _load_profile(db, uid) if uid > 0 else {}
    state = _make_state(
        str(uid), profile=profile,
        chapter_id=req.topic,
        user_query=f"为「{req.user_id or '学生'}」生成学习路径，目标角色={req.target_role or '软件工程师'}",
        target_role=req.target_role or "软件工程师",
    )
    result = await agent(state)
    if uid > 0:
        lp = result.get("learning_path", result)
        await _persist_day4(db, uid, "path", {"title": f"{req.target_role} 学习路径", "learning_path": lp} if isinstance(lp, list) else result)
    return {"success": True, "user_id": str(uid), "target_role": req.target_role, **result}


# ============= 7. tutor =============

@router.post("/tutor/ask", summary="向 AI 导师提问")
async def tutor_ask(
    req: AskRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    agent = TutorAgent()
    uid = _resolve_authenticated_uid(req.user_id, current_user_id)
    profile = await _load_profile(db, uid) if uid > 0 else {}
    state = _make_state(
        str(uid), profile=profile,
        user_query=req.question,
        messages=[{"role": "user", "content": req.question}],
    )
    result = await agent(state)
    if uid > 0:
        tr = result.get("tutor_response", "")
        await _persist_day4(db, uid, "tutor", {"title": req.question[:60], "question": req.question, "tutor_response": tr})
    return {"success": True, "user_id": str(uid), "question": req.question, **result}


# ============= 8. video =============

@router.post("/video/generate", summary="生成教学视频")
async def video_generate(
    req: TopicRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    agent = VideoAgent()
    uid = _resolve_authenticated_uid(req.user_id, current_user_id)
    profile = await _load_profile(db, uid) if uid > 0 else {}
    state = _make_state(
        str(uid), profile=profile,
        user_query=req.topic,
        mode=req.mode,
    )
    result = await agent(state)
    if uid > 0:
        vr = result.get("video_result", result)
        await _persist_day4(db, uid, "video", vr if isinstance(vr, dict) else result)
    return {"success": True, "user_id": str(uid), "topic": req.topic, **result}
