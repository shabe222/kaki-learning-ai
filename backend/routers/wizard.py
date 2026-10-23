"""
画像构建向导 API — 6 阶段流程

阶段 0: 注册后自动进入
阶段 1: 上传学习背景文档 (PDF/DOCX/TXT/MD)
阶段 2: 选择学科 (机器学习)
阶段 3: 知识问答 (15题自适应)
阶段 4: 画像生成 (文档+问答 → capability_matrix)
阶段 5: 画像展示与确认 (手动微调)
阶段 6: 学习资料生成 (基于低分知识点)

本文件结构（B7 拆分后）：
- 阶段 0/1/2/3/4/5/6 的业务路由
- 所有 Pydantic 模型已迁出至 schemas/wizard.py
- 知识图谱 4 端点已迁出至 routers/knowledge_graph.py
- 答题进度状态已迁出至 services/quiz_state.py
- 画像提取工具已迁出至 services/profile_extractor.py
"""

import os
import logging
import hashlib
import time as time_module
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
import json

import database.connection as db_conn
from database.models import User, StudentProfile, WeakPoint, LearningMaterial, GeneratedItem, AgentInvocation
from database.dependencies import get_db
from utils.auth import get_current_user_id, require_self
from services.document_parser import document_parser

# 向导资料上传的硬上限。以前这里没有任何限制，且原始 filename 未净化。
MAX_WIZARD_UPLOAD_BYTES = 32 * 1024 * 1024
from services.question_bank import (
    get_question, get_questions_by_chapter, get_all_chapters,
    CHAPTER_ALLOCATION, get_chapter_names, QUESTIONS
)
from services.profile_engine import profile_engine
from services.cache_service import CacheService
from services.profile_rules import apply as apply_profile_rules
from services.profile_service import save_answer_records, load_existing_profile, save_profile
from services.profile_extractor import (
    extract_profile_from_document,
    apply_profile_extraction,
)
from services.quiz_state import (
    quiz_progress_store,
    get_quiz_progress as get_quiz_progress_from_db,
    save_quiz_progress as save_quiz_progress_to_db,
    clear_quiz_progress as clear_quiz_progress_from_db,
    get_chapter_progress_from_memory,
    set_chapter_progress_in_memory,
)

# 引入拆分出去的 Pydantic 模型
from schemas.wizard import (
    QuizAnswer,
    SubmitQuizRequest,
    BasicInfoRequest,
    LearningGoalsRequest,
    PriorExperienceRequest,
    ProfileConfirmRequest,
    ChapterQuestion,
    GenerateProfileRequest,
    SaveComputedProfileRequest,
    ChatProfileMessage,
    ChatProfileRequest,
    CombineProfileRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wizard", tags=["wizard"])

# 对话画像请求去重（防竞态 / 重复提交）
# 结构: {user_id: {"hash": "...", "reply": "...", "timestamp": float}}
_chat_dedup_cache: Dict[int, Dict[str, Any]] = {}
_CHAT_DEDUP_TTL_SEC = 30  # 30秒内相同消息返回缓存


def _clean_expired_dedup():
    """清理过期的去重缓存"""
    now = time_module.time()
    expired = [uid for uid, v in _chat_dedup_cache.items() if now - v.get("timestamp", 0) > _CHAT_DEDUP_TTL_SEC]
    for uid in expired:
        del _chat_dedup_cache[uid]


# ===== 阶段 0: 检查画像状态 =====

@router.get("/status/{user_id}")
async def check_wizard_status(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
):
    """检查用户是否需要走画像构建向导"""
    require_self(user_id, current_user_id)
    async with db_conn._async_session_factory() as db:
        result = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()

        has_profile = profile is not None
        has_capability = has_profile and profile.capability_matrix is not None
        has_document = has_profile and profile.profile_detail is not None and \
            profile.profile_detail.get("uploaded_document") is not None

        return {
            "success": True,
            "needs_wizard": not has_capability,
            "stages_completed": {
                "stage_0_registered": True,
                "stage_1_document": has_document,
                "stage_2_subject": has_profile,
                "stage_3_quiz": has_capability,
                "stage_4_profile_generated": has_capability,
                "stage_5_confirmed": has_profile and profile.profile_version > 1
            }
        }


_WIZARD_AGENT_LABEL_MAP = {
    "profile": "学情分析智能体",
    "wizard_chat": "画像构建Agent",
    "wizard_combine": "画像合并Agent",
}


async def _record_wizard_invocation(
    db: AsyncSession,
    user_id: int,
    *,
    agent_name: str,
    input_summary: str,
    status: str = "success",
    duration_ms: int = 0,
    error_message: str = None,
) -> None:
    """为 wizard 流程中的画像生成调用写一条 agent_invocations（Agent 记录页数据源）。"""
    record = AgentInvocation(
        user_id=user_id,
        agent_name=agent_name,
        agent_label=_WIZARD_AGENT_LABEL_MAP.get(agent_name, agent_name),
        target=agent_name,
        status=status,
        duration_ms=duration_ms or 0,
        input_summary=input_summary[:120] if input_summary else None,
        error_message=(error_message or "")[:480] or None,
    )
    db.add(record)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise


# ===== 阶段 1: 文档上传（增强版 — 上传后自动调用画像构建Agent）=====

@router.post("/upload-document/{user_id}")
async def upload_document(
    user_id: int,
    file: UploadFile = File(...),
    current_user_id: int = Depends(get_current_user_id),
):
    """上传学习背景文档并解析 + 自动调用画像构建Agent提取结构化画像数据"""
    require_self(user_id, current_user_id)
    # 检查格式
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in document_parser.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            400, f"不支持的格式: {ext}，支持: {document_parser.SUPPORTED_EXTENSIONS}"
        )

    # 保存到临时目录
    upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    # 只取 basename：原始 filename 可能带 ../，会把文件写到 uploads 之外。
    safe_name = os.path.basename(file.filename or "upload.bin") or "upload.bin"
    file_path = os.path.join(upload_dir, f"{user_id}_{safe_name}")
    # 边流边落盘并计数，避免把任意大的文件整个读进内存。
    written = 0
    with open(file_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_WIZARD_UPLOAD_BYTES:
                f.close()
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                raise HTTPException(
                    413,
                    f"文件不能超过 {MAX_WIZARD_UPLOAD_BYTES // (1024 * 1024)}MB",
                )
            f.write(chunk)

    # 解析文档
    try:
        result = await document_parser.parse(file_path)
    except Exception as e:
        logger.error(f"[Wizard] 文档解析失败 user_id={user_id} file={file.filename}: {e}", exc_info=True)
        raise HTTPException(500, "文档解析失败，请检查文件格式或稍后重试")

    # 清理临时文件
    try:
        os.remove(file_path)
    except:
        pass

    # 存储解析结果到画像表
    async with db_conn._async_session_factory() as db:
        result_obj = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result_obj.scalar_one_or_none()

        if not profile:
            profile = StudentProfile(user_id=user_id, profile_version=0)
            db.add(profile)

        # 更新 profile_detail
        existing_detail = profile.profile_detail or {}
        existing_detail["uploaded_document"] = {
            "format": result["format"],
            "char_count": result["char_count"],
            "extracted_info": result["extracted_info"]
        }
        profile.profile_detail = existing_detail
        await db.commit()

    # 调用画像构建Agent从文档中提取结构化画像数据
    profile_extracted = None
    raw_text = result.get("raw_text", "")
    extracted_info = result.get("extracted_info", {})
    doc_text_for_llm = raw_text[:8000] if raw_text else ""

    if doc_text_for_llm and len(doc_text_for_llm) > 50:
        try:
            profile_extracted = await extract_profile_from_document(doc_text_for_llm, extracted_info)
            if profile_extracted:
                async with db_conn._async_session_factory() as db:
                    result_obj = await db.execute(
                        select(StudentProfile).where(StudentProfile.user_id == user_id)
                    )
                    profile = result_obj.scalar_one_or_none()
                    if profile:
                        existing_detail = dict(profile.profile_detail or {})
                        existing_detail["pending_profile_data"] = profile_extracted
                        existing_detail["pending_profile_source"] = "document"
                        existing_detail["pending_profile_at"] = datetime.now().isoformat()

                        existing_detail["basic_info"] = {
                            "major": profile_extracted.get("major") or "",
                            "grade": profile_extracted.get("grade_level") or "",
                            "preferred_language": profile_extracted.get("preferred_language") or ""
                        }
                        existing_detail["learning_goals"] = {
                            "target_role": profile_extracted.get("target_role") or "",
                            "motivation": (profile_extracted.get("motivation_profile") or {}).get("primary_motivation") or ""
                        }

                        if profile_extracted.get("major"):
                            profile.major = profile_extracted["major"]
                        if profile_extracted.get("grade_level"):
                            profile.grade_level = profile_extracted["grade_level"]
                        if profile_extracted.get("target_role"):
                            profile.target_role = profile_extracted["target_role"]
                        if profile_extracted.get("preferred_language"):
                            profile.preferred_language = profile_extracted["preferred_language"]
                        if profile_extracted.get("prior_courses"):
                            profile.prior_courses = profile_extracted["prior_courses"]

                        profile.profile_detail = existing_detail
                        await db.commit()
                        logger.info(f"[Wizard] 画像构建Agent提取的数据已暂存: user_id={user_id}")

                        # 自动刷新：清除缓存，使 full-profile 下次读取最新数据
                        try:
                            await CacheService.invalidate_profile(user_id)
                            logger.info(f"[Wizard] 画像缓存已刷新 user_id={user_id}")
                        except Exception as cache_err:
                            logger.warning(f"[Wizard] 刷新缓存失败: {cache_err}")
        except Exception as e:
            logger.warning(f"[Wizard] 画像构建Agent提取失败（不影响上传）: {e}")

    return {
        "success": True,
        "data": {
            "format": result["format"],
            "char_count": result["char_count"],
            "extracted_info": result["extracted_info"],
            "profile_extracted": profile_extracted,
            "has_profile_data": profile_extracted is not None and len(profile_extracted) > 0
        }
    }


# ===== 阶段 2: 获取学科列表 + 章节信息 =====

@router.get("/subjects")
async def get_subjects():
    """获取可选学科列表"""
    return {
        "success": True,
        "data": {
            "subjects": [
                {"id": "machine_learning", "name": "机器学习", "chapters": 9, "knowledge_points": 45}
            ]
        }
    }


@router.get("/chapters")
async def get_chapters():
    """获取所有章节信息和题数分配"""
    chapters = []
    chapter_names = get_chapter_names()
    for ch_id, alloc in CHAPTER_ALLOCATION.items():
        chapters.append({
            "id": ch_id,
            "name": chapter_names.get(ch_id, ch_id),
            "total_questions": alloc["total"],
            "required_correct": alloc["required"]
        })
    return {"success": True, "data": {"chapters": chapters, "total": 15}}


@router.get("/quiz-bundle")
async def get_quiz_bundle():
    """
    P0 修复: 一次性返回所有章节和题（不含正确答案，避免泄露）。
    前端 wizard 流程用此端点替代前端硬编码的 QUIZ_DATA。
    """
    chapter_names = get_chapter_names()
    chapters_payload = []
    questions_payload = []
    for ch_id, alloc in CHAPTER_ALLOCATION.items():
        chapters_payload.append({
            "id": ch_id,
            "name": chapter_names.get(ch_id, ch_id),
            "total_questions": alloc["total"],
            "required_correct": alloc["required"]
        })
        # 取该章节的 alloc["total"] 道题（不含 answer 字段，避免泄露）
        for q in get_questions_by_chapter(ch_id)[: alloc["total"]]:
            questions_payload.append({
                "id": q["id"],
                "chapter": q["chapter"],
                "chapter_name": chapter_names.get(q["chapter"], q["chapter"]),
                "knowledge_points": q.get("knowledge_points", []),
                "difficulty": q.get("difficulty", 1),
                "question": q["question"],
                "options": q["options"],
                # 故意不返回 "answer" 字段，前端无法提前得知正确答案
                # 答案在 submit-answer 端点提交时由后端比对
                "explanation_preview": (q.get("explanation") or "")[:60]  # 仅显示前 60 字预览
            })
    return {
        "success": True,
        "data": {
            "chapters": chapters_payload,
            "questions": questions_payload,
            "total": len(questions_payload)
        }
    }


# ===== 阶段 3: 自适应答题 =====

@router.get("/question/{chapter}/{index}")
async def get_next_question(
    chapter: str,
    index: int,
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    previous_correct: Optional[bool] = None
):
    """
    获取下一道题 — 支持自适应跳题逻辑，进度持久化到 DB

    规则:
    - 用户答对某章节 required 数量后，跳过后续题
    - 答错则追加同章节下一道（不超过 total）
    - 进度持久化到 StudentProfile.profile_detail.quiz_progress
    """
    # 校验章节存在
    require_self(user_id, current_user_id)

    questions = get_questions_by_chapter(chapter)
    if not questions:
        chapter_names = get_chapter_names()
        valid_chapters = list(CHAPTER_ALLOCATION.keys())
        raise HTTPException(404, f"未知章节: {chapter}，有效章节: {', '.join(valid_chapters)}")

    alloc = CHAPTER_ALLOCATION.get(chapter, {})
    required = alloc.get("required", 1)
    total = alloc.get("total", len(questions))

    # 从 DB 加载进度（自动初始化）
    async with db_conn._async_session_factory() as db:
        full_progress = await get_quiz_progress_from_db(db, user_id)

        if not full_progress:
            # 自动初始化 quiz session
            full_progress = {
                "start_time": datetime.now().isoformat(),
                "current_chapter": chapter,
                "current_index": index,
                "chapters": {},
            }
            await save_quiz_progress_to_db(db, user_id, full_progress)
        elif chapter not in full_progress.get("chapters", {}):
            # 已有 session 但新章节，初始化该章节
            chapters = full_progress.get("chapters", {})
            chapters[chapter] = {"correct": 0, "asked": 0, "last_correct": None}
            full_progress["chapters"] = chapters
            full_progress["current_chapter"] = chapter
            full_progress["current_index"] = index

    # 获取当前章节进度
    progress = get_chapter_progress_from_memory(user_id, chapter)
    if not progress:
        progress = {"correct": 0, "asked": 0, "last_correct": None}
        set_chapter_progress_in_memory(user_id, chapter, progress)

    if previous_correct is not None:
        progress["asked"] += 1
        progress["last_correct"] = previous_correct
        if previous_correct:
            progress["correct"] += 1
        set_chapter_progress_in_memory(user_id, chapter, progress)
        # 持久化到 DB
        full_progress["chapters"] = full_progress.get("chapters", {})
        full_progress["chapters"][chapter] = dict(progress)
        full_progress["current_chapter"] = chapter
        full_progress["current_index"] = index
        async with db_conn._async_session_factory() as db:
            await save_quiz_progress_to_db(db, user_id, full_progress)

    # 计算总题数和当前进度
    total_questions = sum(alloc.get("total", 0) for alloc in CHAPTER_ALLOCATION.values())
    answered_so_far = sum(
        ch.get("asked", 0) for ch in full_progress.get("chapters", {}).values()
    )

    # 自适应跳题: 如果已答对 required 道 → 跳过本章剩余
    if progress["correct"] >= required:
        return {
            "success": True,
            "data": {
                "skip_chapter": True,
                "message": f"本章已达标（{progress['correct']}/{required}），跳过剩余题目",
                "chapter": chapter,
                "progress": {"correct": progress["correct"], "required": required},
                "total_questions": total_questions,
                "current_index": answered_so_far,
            }
        }

    # 如果已答完所有题
    if progress["asked"] >= total:
        return {
            "success": True,
            "data": {
                "chapter_complete": True,
                "message": "本章所有题目已答完",
                "chapter": chapter,
                "progress": {"correct": progress["correct"], "total": total},
                "total_questions": total_questions,
                "current_index": answered_so_far,
            }
        }

    # 获取当前题目
    q_idx = min(index, len(questions) - 1)
    q = questions[q_idx]

    chapter_keys = list(CHAPTER_ALLOCATION.keys())
    chapter_index = chapter_keys.index(chapter) if chapter in chapter_keys else 0

    return {
        "success": True,
        "data": {
            "skip_chapter": False,
            "question": {
                "id": q["id"],
                "chapter": q["chapter"],
                "question": q["question"],
                "options": q["options"]
            },
            "progress": {
                "correct": progress["correct"],
                "required": required,
                "total": total,
                "asked": progress["asked"]
            },
            "total_questions": total_questions,
            "current_index": answered_so_far,
            "chapter_index": chapter_index,
            "total_chapters": len(CHAPTER_ALLOCATION)
        }
    }


@router.post("/submit-answer/{user_id}")
async def submit_answer(
    user_id: int,
    answer: QuizAnswer,
    current_user_id: int = Depends(get_current_user_id),
):
    """提交单题答案，返回正确/错误 + 解析，进度持久化到 DB"""
    require_self(user_id, current_user_id)

    q = get_question(answer.question_id)
    if not q:
        raise HTTPException(404, f"题目不存在: {answer.question_id}")

    is_interest = q["answer"] == -1  # 兴趣探测题
    is_correct = answer.selected_option == q["answer"] if not is_interest else True

    # 记录答题
    chapter = q["chapter"]
    key = f"{user_id}_{chapter}"
    if key not in quiz_progress_store:
        quiz_progress_store[key] = {"correct": 0, "asked": 0, "last_correct": None}

    progress = quiz_progress_store[key]
    progress["asked"] += 1
    progress["last_correct"] = is_correct
    if is_correct and not is_interest:
        progress["correct"] += 1

    # 持久化到 DB
    try:
        async with db_conn._async_session_factory() as db:
            full_progress = await get_quiz_progress_from_db(db, user_id)
            if not full_progress:
                full_progress = {
                    "start_time": datetime.now().isoformat(),
                    "current_chapter": chapter,
                    "current_index": progress["asked"],
                    "chapters": {},
                }
            full_progress["chapters"] = full_progress.get("chapters", {})
            full_progress["chapters"][chapter] = dict(progress)
            full_progress["current_chapter"] = chapter
            full_progress["current_index"] = progress["asked"]
            await save_quiz_progress_to_db(db, user_id, full_progress)
    except Exception as e:
        logger.warning(f"[Wizard] quiz progress 持久化失败（不影响答题）: {e}")

    return {
        "success": True,
        "data": {
            "question_id": answer.question_id,
            "is_correct": is_correct,
            "is_interest": is_interest,
            "correct_answer": q["answer"] if not is_interest else None,
            "explanation": q["explanation"],
            "chapter_progress": {
                "correct": progress["correct"],
                "required": CHAPTER_ALLOCATION[chapter]["required"],
                "asked": progress["asked"],
                "total": CHAPTER_ALLOCATION[chapter]["total"]
            }
        }
    }


@router.get("/quiz-progress/{user_id}")
async def get_quiz_progress(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
):
    """获取用户当前 quiz 进度（供前端轮询/恢复使用）

    返回是否活跃 session、当前章节、当前题目索引、已回答总数
    """
    require_self(user_id, current_user_id)
    async with db_conn._async_session_factory() as db:
        full_progress = await get_quiz_progress_from_db(db, user_id, force_reload=True)

    if not full_progress:
        # 检查内存中是否有残余进度
        meta = quiz_progress_store.get(f"_meta_{user_id}")
        if meta:
            # 从内存重建进度数据
            mem_chapters = {}
            for k, v in quiz_progress_store.items():
                if isinstance(k, str) and k.startswith(f"{user_id}_") and not k.startswith(f"{user_id}_meta_"):
                    chapter = k[len(str(user_id)) + 1:]
                    mem_chapters[chapter] = dict(v)
            if mem_chapters or meta:
                return {
                    "success": True,
                    "data": {
                        "has_active_session": True,
                        "current_chapter": meta.get("current_chapter", ""),
                        "current_index": meta.get("current_index", 0),
                        "total_answered": sum(ch.get("asked", 0) for ch in mem_chapters.values()),
                        "start_time": meta.get("start_time"),
                        "source": "memory",
                    }
                }
        return {
            "success": True,
            "data": {
                "has_active_session": False,
                "current_chapter": None,
                "current_index": None,
                "total_answered": 0,
            }
        }

    chapters = full_progress.get("chapters", {})
    total_answered = sum(ch.get("asked", 0) for ch in chapters.values())
    total_questions = sum(alloc.get("total", 0) for alloc in CHAPTER_ALLOCATION.values())

    return {
        "success": True,
        "data": {
            "has_active_session": True,
            "current_chapter": full_progress.get("current_chapter"),
            "current_index": full_progress.get("current_index", 0),
            "total_answered": total_answered,
            "total_questions": total_questions,
            "start_time": full_progress.get("start_time"),
            "chapters_progress": {
                ch_id: {"correct": ch_data.get("correct", 0), "asked": ch_data.get("asked", 0)}
                for ch_id, ch_data in chapters.items()
            },
            "source": "db",
        }
    }


@router.post("/submit-all-answers/{user_id}")
async def submit_all_answers(
    user_id: int,
    request: SubmitQuizRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """提交全部答案，清除进度缓存，持久化答题记录到 profile_detail"""
    require_self(user_id, current_user_id)

    results = []
    correct_count = 0
    interest_answer = None

    for a in request.answers:
        q = get_question(a.question_id)
        if not q:
            continue
        is_interest = q["answer"] == -1
        is_correct = a.selected_option == q["answer"] if not is_interest else True

        # 读取实际 time_spent_sec（如果 QuizAnswer 中有该字段）
        time_spent = getattr(a, "time_spent_sec", None)
        if time_spent is None:
            time_spent = 0

        results.append({
            "question_id": a.question_id,
            "chapter": q["chapter"],
            "knowledge_points": q["knowledge_points"],
            "selected_option": a.selected_option,
            "is_correct": is_correct,
            "correct_answer": q["answer"] if not is_interest else None,
            "explanation": q["explanation"],
            "time_spent_sec": time_spent,
        })

        if is_interest:
            interest_answer = a.selected_option  # 0=NLP, 1=CV, 2=RL, 3=MLOps
        elif is_correct:
            correct_count += 1

    # 清除该用户的进度缓存（内存 + DB）
    keys_to_del = [k for k in quiz_progress_store if k.startswith(f"{user_id}_")]
    for k in keys_to_del:
        del quiz_progress_store[k]
    quiz_progress_store.pop(f"_meta_{user_id}", None)

    async with db_conn._async_session_factory() as db:
        # 通过 DB 查询清除 quiz_progress（替代 startswith 遍历内存）
        await clear_quiz_progress_from_db(db, user_id)

        # 画像自动更新：记答题记录 → 规则引擎即时改 → 异步 AI 深度评估
        answers = []
        for r in results:
            answers.append({
                "question_id": str(r["question_id"]),
                "topic": r["chapter"],
                "correct": r["is_correct"],
                "time_spent_sec": r.get("time_spent_sec", 0),
            })
        await save_answer_records(db, user_id, answers, "wizard")
        existing = await load_existing_profile(db, user_id) or {}
        updated = apply_profile_rules(existing, answers)
        await save_profile(db, user_id, {"profile_data": updated})

        # 更新 StudentProfile.profile_detail 记录答题结果
        result_obj = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result_obj.scalar_one_or_none()
        if profile:
            pd = dict(profile.profile_detail or {})
            pd["quiz_result"] = {
                "total_questions": len(results),
                "correct_count": correct_count,
                "completion_rate": round(correct_count / max(len(results), 1), 2),
                "interest_answer": interest_answer,
                "completed_at": datetime.now().isoformat(),
            }
            profile.profile_detail = pd
            await db.commit()

    return {
        "success": True,
        "data": {
            "total_questions": len(results),
            "correct_count": correct_count,
            "completion_rate": round(correct_count / max(len(results), 1), 2),
            "interest_answer": interest_answer,
            "answers": results
        }
    }


# ===== 阶段 4: 画像生成 =====

@router.post("/generate-profile/{user_id}")
async def generate_profile(
    user_id: int,
    request: GenerateProfileRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """综合文档解析 + 答题结果 → 生成完整画像"""
    require_self(user_id, current_user_id)
    started_at = time_module.monotonic()
    try:
        async with db_conn._async_session_factory() as db:
            # 获取之前上传的文档解析结果
            result_obj = await db.execute(
                select(StudentProfile).where(StudentProfile.user_id == user_id)
            )
            profile = result_obj.scalar_one_or_none()

            doc_extracted = None
            if profile and profile.profile_detail:
                uploaded = profile.profile_detail.get("uploaded_document", {})
                if uploaded.get("extracted_info"):
                    doc_extracted = uploaded["extracted_info"]

            # 1. 构建画像详情
            basic_info = request.basic_info or {}
            learning_goals = request.learning_goals or {}
            prior_experience = request.prior_experience or {}

            # 从文档补充信息
            if doc_extracted:
                if not prior_experience.get("courses_taken") and doc_extracted.get("courses_taken"):
                    prior_experience["courses_taken"] = doc_extracted["courses_taken"]
                if not prior_experience.get("projects") and doc_extracted.get("projects"):
                    prior_experience["projects"] = doc_extracted["projects"]
                if not prior_experience.get("self_assessment") and doc_extracted.get("self_assessment"):
                    prior_experience["self_assessment"] = "\n".join(doc_extracted["self_assessment"])

            # 2. 调用规则引擎生成 capability_matrix
            engine_result = profile_engine.build_capability_matrix(
                quiz_answers=request.quiz_answers,
                doc_info=doc_extracted
            )

            # 3. 获取兴趣方向
            interest_answer = None
            for a in request.quiz_answers:
                if a.get("question_id") == "adv_1":
                    interest_answer = a.get("selected_option")
                    break

            advanced_interest = {}
            for i, field in enumerate(["nlp", "cv", "reinforcement_learning", "mlops"]):
                advanced_interest[field] = (interest_answer == i) if interest_answer is not None else False

            # 4. 组装完整画像
            profile_detail = {
                "basic_info": basic_info,
                "learning_goals": learning_goals,
                "prior_experience": prior_experience,
                "uploaded_document": profile.profile_detail.get("uploaded_document") if profile else None,
                "quiz_result": {
                    "total_questions": len(request.quiz_answers),
                    "correct_count": sum(1 for a in request.quiz_answers if a.get("is_correct")),
                    "completion_rate": engine_result["quiz_completeness"]
                },
                "advanced_interest": advanced_interest
            }

            # 5. 存入数据库
            if not profile:
                profile = StudentProfile(user_id=user_id)
                db.add(profile)

            profile.capability_matrix = engine_result["capability_matrix"]
            profile.overall_score = engine_result["overall_score"]
            profile.confidence_score = engine_result["confidence_score"]
            profile.profile_version = (profile.profile_version or 0) + 1
            profile.profile_detail = profile_detail
            profile.major = basic_info.get("major") or profile.major
            profile.grade_level = basic_info.get("grade") or profile.grade_level
            profile.target_role = learning_goals.get("target_role") or profile.target_role
            profile.preferred_language = basic_info.get("preferred_language") or profile.preferred_language

            await db.commit()

            # 6. 缓存到 Redis（异步调用）
            try:
                await CacheService.cache_profile(user_id, {
                    "capability_matrix": engine_result["capability_matrix"],
                    "overall_score": engine_result["overall_score"],
                    "confidence_score": engine_result["confidence_score"],
                    "profile_detail": profile_detail
                })
            except Exception as cache_err:
                logger.warning(f"[Wizard] Redis缓存写入失败（不影响功能）: {cache_err}")

            weak_points = profile_engine.get_weak_points(engine_result["capability_matrix"])
            strong_points = profile_engine.get_strong_points(engine_result["capability_matrix"])

            # 7. 落一条 agent_invocations（让 /agent-records 能看到这次画像生成）
            try:
                summary_parts = [
                    str(basic_info.get("major") or "").strip(),
                    str(learning_goals.get("target_role") or "").strip(),
                ]
                summary = " · ".join([p for p in summary_parts if p]) or f"阶段4 画像生成（{len(request.quiz_answers)} 题测评）"
                await _record_wizard_invocation(
                    db,
                    user_id,
                    agent_name="profile",
                    input_summary=summary,
                    status="success",
                    duration_ms=int((time_module.monotonic() - started_at) * 1000),
                )
            except Exception as record_err:
                logger.warning(f"[Wizard] generate-profile 记录 invocation 失败（不影响功能）: {record_err}")

            return {
                "success": True,
                "data": {
                    "capability_matrix": engine_result["capability_matrix"],
                    "overall_score": engine_result["overall_score"],
                    "confidence_score": engine_result["confidence_score"],
                    "profile_detail": profile_detail,
                    "weak_points": weak_points,
                    "strong_points": strong_points,
                    "profile_version": profile.profile_version
                }
            }
    except Exception as e:
        logger.error(f"[Wizard] 生成画像失败: {e}", exc_info=True)
        raise HTTPException(500, f"画像生成失败: {str(e)}")


# ===== 前端向导直接保存画像（使用前端计算的能力矩阵） =====

@router.post("/save-computed-profile/{user_id}")
async def save_computed_profile(
    user_id: int,
    request: SaveComputedProfileRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """接收前端向导已计算的 capability_matrix 并存入数据库"""
    require_self(user_id, current_user_id)
    started_at = time_module.monotonic()
    try:
        async with db_conn._async_session_factory() as db:
            result_obj = await db.execute(
                select(StudentProfile).where(StudentProfile.user_id == user_id)
            )
            profile = result_obj.scalar_one_or_none()

            # 将 0-100 分数映射为 capability_matrix 格式
            frontend_matrix = request.capability_matrix
            scores = list(frontend_matrix.values())
            overall_score = round(sum(scores) / max(len(scores), 1), 1) if scores else 0
            confidence_score = 0.5

            # 转换为后端格式（兼容 full-profile 读取）
            capability_matrix = {}
            for ch_name, score in frontend_matrix.items():
                # 将 0-100 分数映射到 1-5 分
                mapped_score = round(max(1.0, min(5.0, score / 20)), 1)
                capability_matrix[ch_name] = {
                    "章节掌握度": {
                        "score": mapped_score,
                        "confidence": confidence_score,
                        "notes": f"基于15题测评结果，章节得分 {score}/100"
                    }
                }

            # 存入数据库
            if not profile:
                profile = StudentProfile(user_id=user_id)
                db.add(profile)

            profile.capability_matrix = capability_matrix
            profile.overall_score = overall_score
            profile.confidence_score = confidence_score
            profile.profile_version = (profile.profile_version or 0) + 1
            # === P1 修复：basic_info / learning_goals 也落库 ===
            # 合并 basic_info：保留已有值（除非前端传了非空）
            existing_detail = dict(profile.profile_detail or {})
            incoming_basic = request.basic_info or {}
            incoming_goals = request.learning_goals or {}

            merged_basic = dict(existing_detail.get("basic_info") or {})
            for k, v in incoming_basic.items():
                if v not in (None, ""):
                    merged_basic[k] = v

            merged_goals = dict(existing_detail.get("learning_goals") or {})
            for k, v in incoming_goals.items():
                if v not in (None, ""):
                    merged_goals[k] = v

            existing_detail["basic_info"] = merged_basic
            existing_detail["learning_goals"] = merged_goals
            existing_detail["quiz_result"] = {
                "total_questions": len(request.quiz_answers) if request.quiz_answers else 15,
                "completion_rate": 1.0
            }
            profile.profile_detail = existing_detail

            # 标量字段：直接 setattr 到列，画像页面 GET 立刻可读
            if merged_basic.get("major"):
                profile.major = merged_basic["major"]
            if merged_basic.get("grade"):
                profile.grade_level = merged_basic["grade"]
            if merged_basic.get("preferred_language"):
                profile.preferred_language = merged_basic["preferred_language"]
            if merged_goals.get("target_role"):
                profile.target_role = merged_goals["target_role"]

            await db.commit()
            await db.refresh(profile)  # P2 写后重读

            # P2 写后验证：4 个字段必须和传入一致
            expected_map = {
                "major": merged_basic.get("major"),
                "grade_level": merged_basic.get("grade"),
                "preferred_language": merged_basic.get("preferred_language"),
                "target_role": merged_goals.get("target_role")
            }
            mismatched = []
            for f, exp in expected_map.items():
                if exp and getattr(profile, f) != exp:
                    mismatched.append(f"{f} expected={exp!r} got={getattr(profile, f)!r}")
            if mismatched:
                logger.error(f"[Wizard save-computed-profile {user_id}] 写后验证失败: {mismatched}")
                raise HTTPException(500, f"画像写入后不一致: {'; '.join(mismatched)}")

            logger.info(f"[Wizard] 前端计算画像已保存: user_id={user_id}, overall={overall_score}")

            # 落一条 agent_invocations（让 /agent-records 看到 wizard 端点直存）
            try:
                await _record_wizard_invocation(
                    db,
                    user_id,
                    agent_name="profile",
                    input_summary=f"前端计算画像保存（{len(frontend_matrix)} 章节）",
                    status="success",
                    duration_ms=int((time_module.monotonic() - started_at) * 1000),
                )
            except Exception as record_err:
                logger.warning(f"[Wizard] save-computed-profile 记录 invocation 失败（不影响功能）: {record_err}")

            return {
                "success": True,
                "data": {
                    "overall_score": overall_score,
                    "confidence_score": confidence_score,
                    "profile_version": profile.profile_version
                }
            }
    except Exception as e:
        logger.error(f"[Wizard] 保存画像失败: {e}", exc_info=True)
        raise HTTPException(500, f"画像保存失败: {str(e)}")


# ===== 阶段 5: 画像确认与微调 =====

@router.post("/confirm-profile/{user_id}")
async def confirm_profile(
    user_id: int,
    request: ProfileConfirmRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """用户确认画像，支持手动微调"""
    require_self(user_id, current_user_id)
    async with db_conn._async_session_factory() as db:
        result_obj = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result_obj.scalar_one_or_none()

        if not profile:
            raise HTTPException(404, "画像不存在，请先生成")

        # 应用微调
        if request.adjustments:
            matrix = profile.capability_matrix or {}
            for chapter, kps in request.adjustments.items():
                if chapter in matrix and isinstance(kps, dict):
                    for kp_name, adjustment in kps.items():
                        if kp_name in matrix[chapter] and isinstance(adjustment, (int, float)):
                            old = matrix[chapter][kp_name]["score"]
                            matrix[chapter][kp_name]["score"] = max(1.0, min(5.0, old + adjustment))
                            matrix[chapter][kp_name]["notes"] = (
                                matrix[chapter][kp_name].get("notes", "") +
                                f" (用户手动调整 {adjustment:+d})"
                            )
            profile.capability_matrix = matrix

        # 添加备注
        if request.notes:
            existing_detail = profile.profile_detail or {}
            existing_detail["user_notes"] = request.notes
            profile.profile_detail = existing_detail

        profile.profile_version = (profile.profile_version or 0) + 1
        await db.commit()

        return {
            "success": True,
            "message": "画像已确认",
            "data": {
                "capability_matrix": profile.capability_matrix,
                "overall_score": profile.overall_score,
                "profile_version": profile.profile_version
            }
        }


# ===== 阶段 6: 获取弱项知识点（供后续学习资料生成）=====

@router.get("/weak-points/{user_id}")
async def get_weak_points(
    user_id: int,
    threshold: float = 2.5,
    current_user_id: int = Depends(get_current_user_id),
):
    """获取薄弱知识点列表（score ≤ threshold），驱动学习资料生成"""
    require_self(user_id, current_user_id)
    async with db_conn._async_session_factory() as db:
        result_obj = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result_obj.scalar_one_or_none()

        if not profile or not profile.capability_matrix:
            return {"success": True, "data": {"weak_points": [], "message": "画像未生成"}}

        weak_points = profile_engine.get_weak_points(profile.capability_matrix, threshold)
        strong_points = profile_engine.get_strong_points(profile.capability_matrix)

        return {
            "success": True,
            "data": {
                "weak_points": weak_points,
                "strong_points": strong_points,
                "overall_score": profile.overall_score,
                "total_weak": len(weak_points),
                "recommendation": "系统将基于薄弱知识点生成个性化学习资料"
            }
        }


# ===== 获取完整画像 =====

@router.get("/full-profile/{user_id}")
async def get_full_profile(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
):
    """获取完整画像数据（用于前端展示雷达图/热力图）"""
    require_self(user_id, current_user_id)
    async with db_conn._async_session_factory() as db:
        result_obj = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result_obj.scalar_one_or_none()

        if not profile:
            return {"success": True, "has_profile": False, "data": None, "message": "画像不存在", "status": "empty"}

        chapter_names = get_chapter_names()

        # 计算章节级汇总
        chapter_summary = {}
        capability_matrix_array = []

        if profile.capability_matrix:
            for ch_id, kps in profile.capability_matrix.items():
                if isinstance(kps, dict) and kps:
                    # 提取知识点分数
                    kp_list = []
                    for kp_name, kp_data in kps.items():
                        if isinstance(kp_data, dict) and "score" in kp_data:
                            kp_list.append({
                                "name": kp_name,
                                "score": kp_data["score"],
                                "confidence": kp_data.get("confidence", 0.5)
                            })

                    avg = round(sum(k["score"] for k in kp_list) / len(kp_list), 1) if kp_list else 0

                    chapter_summary[ch_id] = {
                        "name": chapter_names.get(ch_id, ch_id),
                        "avg_score": avg,
                        "knowledge_points": kps
                    }

                    capability_matrix_array.append({
                        "chapter_name": chapter_names.get(ch_id, ch_id),
                        "avg_score": avg,
                        "knowledge_points": kp_list
                    })

        # 兜底：从 profile_detail 读取对话提取的 capability_matrix
        if not capability_matrix_array and profile.profile_detail and profile.profile_detail.get("capability_matrix"):
            cm = profile.profile_detail["capability_matrix"]
            if isinstance(cm, dict) and cm:
                vals = [int(v) for v in cm.values() if v]
                if vals:
                    avg = round(sum(vals) / len(vals), 1)
                    kp_list = [{"name": k, "score": int(v), "confidence": 0.5} for k, v in cm.items()]
                    chapter_summary["general"] = {"name": "综合能力", "avg_score": avg, "knowledge_points": {k: {"score": int(v)} for k, v in cm.items()}}
                    capability_matrix_array.append({"chapter_name": "综合能力", "avg_score": avg, "knowledge_points": kp_list})

        # 合并 basic_info：优先 profile_detail 中的，兜底用数据库字段
        basic_info = (profile.profile_detail or {}).get("basic_info", {})
        if isinstance(basic_info, dict):
            basic_info = dict(basic_info)
        else:
            basic_info = {}
        if not basic_info.get("major") and profile.major:
            basic_info["major"] = profile.major
        if not basic_info.get("grade") and profile.grade_level:
            basic_info["grade"] = profile.grade_level
        if not basic_info.get("preferred_language") and profile.preferred_language:
            basic_info["preferred_language"] = profile.preferred_language

        # ===== 实时聚合：薄弱点 / 已学概念 / 章节统计 =====
        # 1) 薄弱点：从 weak_points 表 + misconception_archive 合并
        weak_points_list = []
        try:
            wp_rows = await db.execute(
                select(WeakPoint).where(WeakPoint.user_id == user_id, WeakPoint.status == "active")
            )
            for wp in wp_rows.scalars().all():
                weak_points_list.append({
                    "chapter_id": wp.chapter_id,
                    "knowledge_point": wp.knowledge_point,
                    "occurrence_count": wp.occurrence_count,
                    "first_seen_at": str(wp.first_seen_at) if wp.first_seen_at else None,
                    "source": "weak_points"
                })
        except Exception as e:
            logger.warning(f"[full-profile] weak_points 聚合失败: {e}")

        # 2) 已学章节 / 已学知识点：从 learning_materials 汇总
        chapters_count = 0
        completed_topics = 0
        try:
            mat_rows = await db.execute(
                select(LearningMaterial).where(LearningMaterial.user_id == user_id)
            )
            unique_chapters = set()
            for m in mat_rows.scalars().all():
                unique_chapters.add(m.chapter_id)
                # 知识点数粗估：取 mastery_at_creation > 0.3 的材料
                if (m.mastery_at_creation or 0) >= 0.3:
                    completed_topics += 1
            chapters_count = len(unique_chapters)
        except Exception as e:
            logger.warning(f"[full-profile] materials 聚合失败: {e}")

        # 3) 知识图谱章节顺序（9章）
        total_chapters = 9
        # 知识点总数（45）取自 knowledge_graph 服务
        total_topics = 45
        try:
            from services.knowledge_graph import KNOWLEDGE_GRAPH
            if KNOWLEDGE_GRAPH:
                total_chapters = len(KNOWLEDGE_GRAPH)
                total_topics = sum(len(ch.get("knowledge_points", [])) for ch in KNOWLEDGE_GRAPH.values())
        except Exception:
            pass

        # 调试用：标记已走到这里
        logger.info(f"[DEBUG-FP] user_id={user_id} weak_points_list_len={len(weak_points_list)} chapters_count={chapters_count} completed_topics={completed_topics} total_chapters={total_chapters} total_topics={total_topics}")

        # 计算画像状态
        has_capability = bool(profile.capability_matrix and len(profile.capability_matrix) > 0)
        # 也从 profile_detail 检查兜底的 capability_matrix
        if not has_capability and profile.profile_detail:
            cm = profile.profile_detail.get("capability_matrix")
            if isinstance(cm, dict) and cm and any(v for v in cm.values() if v):
                has_capability = True
        if has_capability:
            status = "ready"
        else:
            status = "building"

        return {
            "success": True,
            "has_profile": True,
            "status": status,
            "data": {
                "user_id": user_id,
                "basic_info": basic_info,
                "learning_goals": (profile.profile_detail or {}).get("learning_goals", {}),
                "prior_experience": (profile.profile_detail or {}).get("prior_experience", {}),
                "capability_matrix": profile.capability_matrix or {},
                "capability_matrix_array": capability_matrix_array,
                "chapter_summary": chapter_summary,
                "overall_score": profile.overall_score,
                "confidence_score": profile.confidence_score,
                "profile_version": profile.profile_version,
                "created_at": str(profile.created_at) if profile.created_at else None,
                "updated_at": str(profile.updated_at) if profile.updated_at else None,
                # 修复：前端 setText 需要的字段
                "weak_points": weak_points_list,
                "weak_points_count": len(weak_points_list),
                "chapters_count": chapters_count,
                "completed_topics": completed_topics,
                "total_chapters": total_chapters,
                "total_topics": total_topics,
                "skill_matrix": profile.skill_matrix or {},
            }
        }


# ===== 对话式画像构建（无文档上传时使用）=====

@router.post("/chat-profile/{user_id}")
async def chat_profile(
    user_id: int,
    request: ChatProfileRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """对话式画像构建：通过自然语言对话提取学生特征（≥6维度）

    当用户没有上传文档时，通过此接口与画像构建Agent对话，
    Agent会引导用户提供专业、年级、知识基础、学习风格、目标等信息
    """
    require_self(user_id, current_user_id)

    # 去重：检查是否短时间内相同消息已处理
    _clean_expired_dedup()
    last_msg = request.messages[-1]["content"] if request.messages else ""
    msg_hash = hashlib.sha256(f"{user_id}:{last_msg}".encode()).hexdigest()[:16]
    cached = _chat_dedup_cache.get(user_id)
    if cached and cached.get("hash") == msg_hash:
        logger.info(f"[Wizard] chat-profile 命中去重缓存 user_id={user_id}")
        return cached["reply"]

    from services.llm_service import get_llm_service

    llm = await get_llm_service()

    conversation = request.messages

    profile_context = ""
    async with db_conn._async_session_factory() as db:
        result_obj = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result_obj.scalar_one_or_none()
        if profile:
            existing = {}
            if profile.major:
                existing["major"] = profile.major
            if profile.grade_level:
                existing["grade_level"] = profile.grade_level
            if profile.target_role:
                existing["target_role"] = profile.target_role
            if profile.prior_courses:
                existing["prior_courses"] = profile.prior_courses
            if profile.skill_matrix:
                existing["skill_matrix"] = profile.skill_matrix
            if profile.cognitive_profile:
                existing["cognitive_profile"] = profile.cognitive_profile
            if profile.motivation_profile:
                existing["motivation_profile"] = profile.motivation_profile
            if existing:
                profile_context = f"\n【已收集的画像数据】：{json.dumps(existing, ensure_ascii=False)}\n"

    system_prompt = f"""你是机器学习学生画像构建助手。你的任务是通过友好的对话方式，逐步了解学生的学习背景。
{profile_context}
你需要收集以下 6 大类信息（≥6 维度）：
1. 基础档案：专业、年级、目标职业
2. 技能水平：数学基础（线性代数、概率、微积分等）、编程能力（Python、NumPy、Pandas等）、机器学习知识（监督学习、深度学习、NLP、CV等）
3. 已修课程和项目经历
4. 学习风格偏好（视觉型/听觉型/阅读型/实践型）
5. 学习目标（找工作/科研/转行/兴趣）
6. 薄弱点和困惑

对话策略：
- 每次只问 1-2 个问题，不要一口气问太多
- 根据对方的回答自然地引导到下一个话题
- 用友好的语气，让学生感到舒适
- 如果学生已经提供了一些信息，就跳过那些话题
- 每轮回复控制在 3-5 句话内
- 如果已经收集了足够信息（专业+年级+至少3个技能维度+目标），在回复末尾加上 [PROFILE_READY]

对话历史：
"""
    # token优化：只保留最近10轮对话
    recent_msgs = conversation[-10:] if len(conversation) > 10 else conversation
    for msg in recent_msgs:
        role = "学生" if msg["role"] == "user" else "助手"
        system_prompt += f"{role}: {msg['content']}\n"

    system_prompt += "\n请继续对话，根据已收集的信息进行下一步引导。"

    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": "（请根据对话历史和已收集的信息，继续引导对话收集画像数据）"}],
            system_prompt=system_prompt,
            max_tokens=1500,
            temperature=0.7
        )
        reply = result.get("content", "")
        profile_ready = "[PROFILE_READY]" in reply
        reply = reply.replace("[PROFILE_READY]", "").strip()

        response_data = {
            "success": True,
            "data": {
                "reply": reply,
                "profile_ready": profile_ready,
                "tokens_used": result.get("tokens_used", 0)
            }
        }

        # 缓存本次回复（用于去重）
        _chat_dedup_cache[user_id] = {
            "hash": msg_hash,
            "reply": response_data,
            "timestamp": time_module.time(),
        }

        # 保存聊天记录到数据库
        try:
            item = GeneratedItem(
                user_id=user_id,
                type="chat_profile",
                title=last_msg[:80] if last_msg else "画像对话",
                chapter="画像构建对话",
                content=json.dumps({
                    "conversation": conversation,
                    "reply": reply,
                    "profile_ready": profile_ready,
                    "timestamp": datetime.now().isoformat(),
                }, ensure_ascii=False),
                raw_data={"messages": conversation, "reply": reply},
            )
            db.add(item)
            await db.commit()
        except Exception:
            logger.warning("chat-profile 对话保存失败，不影响响应")
            await db.rollback()

        return response_data
    except Exception as e:
        logger.error(f"[Wizard] 对话画像失败: {e}")
        raise HTTPException(500, f"对话画像失败: {str(e)}")


@router.post("/generate-chat-profile/{user_id}")
async def generate_chat_profile(
    user_id: int,
    request: ChatProfileRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """基于对话历史生成完整结构化画像"""
    require_self(user_id, current_user_id)
    from services.llm_service import get_llm_service

    llm = await get_llm_service()

    conversation_text = ""
    for msg in request.messages:
        role = "学生" if msg["role"] == "user" else "助手"
        conversation_text += f"{role}: {msg['content']}\n"

    prompt = f"""你是机器学习教育画像分析专家。请从以下对话中提取完整的学生画像。

【对话内容】：
{conversation_text}

返回纯 JSON（不要用 markdown 代码块包裹）："""

    prompt += r"""
{
  "major": "专业名称",
  "grade_level": "大一/大二/大三/大四/研一/研二/研三/已工作",
  "target_role": "目标职业",
  "prior_courses": ["课程1"],
  "preferred_language": "Python/R",

  "skill_matrix": {
    "math": {"linear_algebra": 1-5, "probability": 1-5, "calculus": 1-5, "statistics": 1-5, "optimization": 1-5},
    "programming": {"python": 1-5, "numpy": 1-5, "pandas": 1-5, "data_structure": 1-5, "algorithm": 1-5, "sql": 1-5},
    "machine_learning": {"supervised": 1-5, "unsupervised": 1-5, "neural_networks": 1-5, "deep_learning": 1-5, "nlp": 1-5, "cv": 1-5, "reinforcement": 1-5, "feature_engineering": 1-5, "model_evaluation": 1-5, "mlops": 1-5, "ensemble_methods": 1-5},
    "tools": {"sklearn": 1-5, "pytorch": 1-5, "tensorflow": 1-5, "jupyter": 1-5, "git": 1-5, "docker": 1-5, "matplotlib": 1-5, "mlflow": 1-5}
  },

  "cognitive_profile": {
    "bloom_level": "remember/understand/apply/analyze/evaluate/create",
    "learning_style": "visual/auditory/reading/kinesthetic",
    "abstraction_ability": 1-5,
    "mathematical_maturity": 1-5,
    "learning_pace": "slow/medium/fast",
    "prefers_theory_first": true/false,
    "needs_concrete_examples": true/false
  },

  "motivation_profile": {
    "primary_motivation": "job_hunting/research/career_change/curiosity/exam",
    "urgency_level": 1-5,
    "self_efficacy": 1-5,
    "topic_passion": [{"topic": "主题", "interest": 0.0-1.0}],
    "career_target": "目标岗位"
  },

  "behavior_pattern": {
    "avg_session_minutes": 45,
    "content_format_preference": ["document", "code"],
    "exercise_type_preference": "coding",
    "persistence_score": 3
  },

  "confidence_score": 0.0-1.0
}

要求：
1. 从对话中尽可能提取信息，不确定的维度给保守值（不要给null）
2. skill_matrix 中，说"学过"给3分，"掌握"给4分，"精通"给5分，"了解"给2分
3. confidence_score 表示你对提取结果的信心
"""

    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="你是机器学习教育画像分析专家。从对话中提取学生特征。返回纯JSON。",
            temperature=0.2,
            max_tokens=2000,
        )
        content = result.get("content", "")
        content = content.strip()

        # 健壮的 JSON 提取：处理多种 LLM 响应格式
        # 1) 去掉 ```json ... ``` 包裹
        if content.startswith("```"):
            lines = content.split("\n")
            # 去掉第一行（可能是 ``` 或 ```json）
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            # 去掉最后一行 ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines).strip()
            if content.lower().startswith("json"):
                content = content[4:].strip()

        # 2) 处理 "好的，这是你的画像：\n{...}" 等中文前缀
        first_brace = content.find("{")
        last_brace = content.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            content = content[first_brace:last_brace + 1]

        # 3) 解析 JSON
        try:
            profile_data = json.loads(content)
        except json.JSONDecodeError as json_err:
            snippet = content[:300] if len(content) > 300 else content
            logger.error(f"[Wizard] 对话画像JSON解析失败: {json_err}, raw_snippet={snippet}")
            raise HTTPException(500, "画像数据解析失败，请稍后重试")

        async with db_conn._async_session_factory() as db:
            result_obj = await db.execute(
                select(StudentProfile).where(StudentProfile.user_id == user_id)
            )
            profile = result_obj.scalar_one_or_none()
            if not profile:
                profile = StudentProfile(user_id=user_id)
                db.add(profile)

            existing_detail = dict(profile.profile_detail or {})
            existing_detail["pending_profile_data"] = profile_data
            existing_detail["pending_profile_source"] = "chat"
            existing_detail["pending_profile_at"] = datetime.now().isoformat()

            existing_detail["basic_info"] = {
                "major": profile_data.get("major") or "",
                "grade": profile_data.get("grade_level") or "",
                "preferred_language": profile_data.get("preferred_language") or ""
            }
            existing_detail["learning_goals"] = {
                "target_role": profile_data.get("target_role") or "",
                "motivation": (profile_data.get("motivation_profile") or {}).get("primary_motivation") or ""
            }

            if profile_data.get("major"):
                profile.major = profile_data["major"]
            if profile_data.get("grade_level"):
                profile.grade_level = profile_data["grade_level"]
            if profile_data.get("target_role"):
                profile.target_role = profile_data["target_role"]
            if profile_data.get("preferred_language"):
                profile.preferred_language = profile_data["preferred_language"]
            if profile_data.get("prior_courses"):
                profile.prior_courses = profile_data["prior_courses"]

            # 写入 capability_matrix（即使 LLM 只给了部分维度也给，方便前端判断画像就绪）
            existing_detail["capability_matrix"] = {
                "ml_knowledge": profile_data.get("ml_knowledge") or 1,
                "math_foundation": profile_data.get("math_foundation") or 1,
                "programming_skill": profile_data.get("programming_skill") or 1,
                "project_experience": profile_data.get("project_experience") or 1,
                "data_analysis": profile_data.get("data_analysis") or 1,
                "system_design": profile_data.get("system_design") or 1,
            }
            caps = existing_detail["capability_matrix"]
            vals = [v for v in [caps["ml_knowledge"],caps["math_foundation"],caps["programming_skill"],caps["project_experience"],caps["data_analysis"],caps["system_design"]] if v > 0]
            existing_detail["overall_score"] = round(sum(vals) / len(vals)) if vals else 0
            existing_detail["profile_version"] = (existing_detail.get("profile_version") or 0) + 1
            profile.overall_score = existing_detail["overall_score"]

            profile.profile_detail = existing_detail
            await db.commit()

        return {
            "success": True,
            "data": {
                "profile": profile_data,
                "confidence_score": profile_data.get("confidence_score", 0)
            }
        }
    except Exception as e:
        logger.error(f"[Wizard] 生成对话画像失败: {e}")
        raise HTTPException(500, f"生成画像失败: {str(e)}")


@router.get("/profile-llm-extracted/{user_id}")
async def get_profile_llm_extracted(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
):
    """获取 LLM 画像构建Agent提取的最新画像数据"""
    require_self(user_id, current_user_id)
    async with db_conn._async_session_factory() as db:
        result_obj = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result_obj.scalar_one_or_none()

        if not profile:
            return {"success": True, "has_profile": False, "data": None, "message": "画像不存在"}

        llm_extracted = (profile.profile_detail or {}).get("llm_extracted")

        return {
            "success": True,
            "data": {
                "major": profile.major,
                "grade_level": profile.grade_level,
                "target_role": profile.target_role,
                "prior_courses": profile.prior_courses,
                "preferred_language": profile.preferred_language,
                "skill_matrix": profile.skill_matrix,
                "cognitive_profile": profile.cognitive_profile,
                "motivation_profile": profile.motivation_profile,
                "misconception_archive": profile.misconception_archive,
                "behavior_pattern": profile.behavior_pattern,
                "profile_version": profile.profile_version,
                "llm_extracted": llm_extracted
            }
        }


@router.post("/combine-and-generate-profile/{user_id}")
async def combine_and_generate_profile(
    user_id: int,
    request: CombineProfileRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """Stage 5 画像生成：合并测评结果 + 暂存的文档/对话画像数据，生成完整画像

    在用户完成能力测评后调用此端点，将测评的 capability_matrix
    与之前暂存的 pending_profile_data 合并，通过LLM生成完整结构化画像
    """
    require_self(user_id, current_user_id)
    from services.llm_service import get_llm_service
    started_at = time_module.monotonic()

    async with db_conn._async_session_factory() as db:
        result_obj = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )
        profile = result_obj.scalar_one_or_none()

        if not profile:
            profile = StudentProfile(user_id=user_id)
            db.add(profile)

        pd = dict(profile.profile_detail or {})
        pending = pd.pop("pending_profile_data", None)
        pending_source = pd.pop("pending_profile_source", "none")

        scores_text = json.dumps(request.capability_matrix, ensure_ascii=False)
        pending_text = json.dumps(pending, ensure_ascii=False) if pending else "无暂存数据"

        llm = await get_llm_service()

        prompt = f"""你是机器学习教育画像分析专家。请合并以下两方面的数据，生成一个完整的学生画像。

【能力测评结果（基于 15 道题）】：
{scores_text}

【暂存画像数据（来源: {pending_source}）】：
{pending_text}

请综合两份数据，返回纯 JSON（不要用 markdown 代码块包裹）："""

        prompt += r"""
{
  "major": "专业名称",
  "grade_level": "大一/大二/大三/大四/研一/研二/研三/已工作",
  "target_role": "目标职业",
  "prior_courses": ["课程1"],
  "preferred_language": "Python/R",

  "skill_matrix": {
    "math": {"linear_algebra": 1-5, "probability": 1-5, "calculus": 1-5, "statistics": 1-5, "optimization": 1-5},
    "programming": {"python": 1-5, "numpy": 1-5, "pandas": 1-5, "data_structure": 1-5, "algorithm": 1-5, "sql": 1-5},
    "machine_learning": {"supervised": 1-5, "unsupervised": 1-5, "neural_networks": 1-5, "deep_learning": 1-5, "nlp": 1-5, "cv": 1-5, "reinforcement": 1-5, "feature_engineering": 1-5, "model_evaluation": 1-5, "mlops": 1-5, "ensemble_methods": 1-5},
    "tools": {"sklearn": 1-5, "pytorch": 1-5, "tensorflow": 1-5, "jupyter": 1-5, "git": 1-5, "docker": 1-5, "matplotlib": 1-5, "mlflow": 1-5}
  },

  "cognitive_profile": {
    "bloom_level": "remember/understand/apply/analyze/evaluate/create",
    "learning_style": "visual/auditory/reading/kinesthetic",
    "abstraction_ability": 1-5,
    "mathematical_maturity": 1-5,
    "learning_pace": "slow/medium/fast",
    "prefers_theory_first": true/false,
    "needs_concrete_examples": true/false
  },

  "motivation_profile": {
    "primary_motivation": "job_hunting/research/career_change/curiosity/exam",
    "urgency_level": 1-5,
    "self_efficacy": 1-5,
    "topic_passion": [{"topic": "主题", "interest": 0.0-1.0}],
    "career_target": "目标岗位"
  },

  "overall_score": 0-100,
  "confidence_score": 0.0-1.0
}

要求：
1. 优先使用暂存画像数据中的 personal info（专业、年级、目标）
2. skill_matrix 结合测评结果和暂存数据
3. overall_score 基于测评的 capability_matrix 计算均分
4. 暂存数据中的认知画像、动机画像直接使用（这些测评是测不出来的）
5. 如果暂存数据中 target_role 为空，根据测评得分分布推断：高分章节多的偏向"算法工程师"，均衡的偏向"数据分析师"
6. 如果暂存数据中 motivation_profile.primary_motivation 为空，根据测评表现推断：主动做测评 → "curiosity"，得分高 → "job_hunting"
7. 即使暂存数据中某些字段为空，也要给出合理的推断值，不要返回 null 或空字符串
"""

        try:
            result = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="你是机器学习教育画像分析专家。合并测评结果和暂存数据生成完整画像。返回纯JSON。",
                temperature=0.3,
                max_tokens=2500,
            )
            content = result.get("content", "")
            content = content.strip()

            # 健壮的 JSON 提取（同 generate_chat_profile）
            if content.startswith("```"):
                lines = content.split("\n")
                if lines[0].strip().startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines).strip()
                if content.lower().startswith("json"):
                    content = content[4:].strip()

            first_brace = content.find("{")
            last_brace = content.rfind("}")
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                content = content[first_brace:last_brace + 1]

            try:
                combined = json.loads(content)
            except json.JSONDecodeError as json_err:
                snippet = content[:300] if len(content) > 300 else content
                logger.error(f"[Wizard] 合并画像JSON解析失败: {json_err}, raw_snippet={snippet}")
                raise HTTPException(500, "画像数据解析失败，请稍后重试")

            apply_profile_extraction(profile, combined)

            pd["basic_info"] = {
                "major": combined.get("major") or pd.get("basic_info", {}).get("major", ""),
                "grade": combined.get("grade_level") or pd.get("basic_info", {}).get("grade", ""),
                "preferred_language": combined.get("preferred_language") or pd.get("basic_info", {}).get("preferred_language", "")
            }
            pd["learning_goals"] = {
                "target_role": combined.get("target_role") or pd.get("learning_goals", {}).get("target_role", ""),
                "motivation": (combined.get("motivation_profile") or {}).get("primary_motivation") or pd.get("learning_goals", {}).get("motivation", "")
            }
            if combined.get("prior_courses"):
                pd["prior_experience"] = pd.get("prior_experience", {})
                pd["prior_experience"]["courses_taken"] = combined["prior_courses"]

            profile.profile_detail = pd

            scores_list = list(request.capability_matrix.values())
            combined["overall_score"] = combined.get("overall_score") or (
                round(sum(scores_list) / max(len(scores_list), 1), 1) if scores_list else 0
            )

            capability_matrix = {}
            for ch_name, score in request.capability_matrix.items():
                mapped_score = round(max(1.0, min(5.0, score / 20)), 1)
                capability_matrix[ch_name] = {
                    "章节掌握度": {
                        "score": mapped_score,
                        "confidence": combined.get("confidence_score", 0.5),
                        "notes": f"基于测评得分 {score}/100，结合暂存画像数据"
                    }
                }
            profile.capability_matrix = capability_matrix
            profile.overall_score = combined.get("overall_score", 0)
            profile.confidence_score = combined.get("confidence_score", 0.5)
            profile.profile_version = (profile.profile_version or 0) + 1

            await db.commit()

            logger.info(f"[Wizard] 合并画像生成完成: user_id={user_id}, source={pending_source}")

            # 落一条 agent_invocations（让 /agent-records 看到 wizard 阶段5 画像合并）
            try:
                summary = f"阶段5 画像合并（{pending_source}）"
                await _record_wizard_invocation(
                    db,
                    user_id,
                    agent_name="profile",
                    input_summary=summary,
                    status="success",
                    duration_ms=int((time_module.monotonic() - started_at) * 1000),
                )
            except Exception as record_err:
                logger.warning(f"[Wizard] combine-and-generate 记录 invocation 失败（不影响功能）: {record_err}")

            return {
                "success": True,
                "data": {
                    "profile": combined,
                    "overall_score": profile.overall_score,
                    "confidence_score": profile.confidence_score,
                    "profile_version": profile.profile_version,
                    "pending_source": pending_source
                }
            }
        except Exception as e:
            logger.error(f"[Wizard] 合并画像生成失败: {e}")
            try:
                async with db_conn._async_session_factory() as db:
                    await _record_wizard_invocation(
                        db,
                        user_id,
                        agent_name="profile",
                        input_summary=f"合并画像失败：{str(e)[:80]}",
                        status="failed",
                        duration_ms=int((time_module.monotonic() - started_at) * 1000),
                        error_message=str(e),
                    )
            except Exception:
                pass
            raise HTTPException(500, f"合并画像生成失败: {str(e)}")
