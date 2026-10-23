from __future__ import annotations

import logging
import json
import hashlib
import time
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

import database.connection as db_conn
from database.models import LearningMaterial, MasteryHistory, WeakPoint, StudentProfile
from utils.auth import get_current_user_id, require_self
from services.material_service import (
    get_stage, get_stage_config, get_user_chapter_mastery,
    get_all_materials, get_current_material, get_chapter_materials,
    get_mastery_history, get_due_reviews, get_weak_points, record_mastery,
    save_material, calculate_mastery_delta, record_weak_point,
    resolve_weak_point, get_next_review_date, STAGE_CONFIG
)
from services.knowledge_graph import KNOWLEDGE_GRAPH, CHAPTER_ORDER
from graph.runtime import execute_workflow
from services.llm_service_real import _make_cache_payload
from services.skill_library import skill_prompt_prefix
from datetime import datetime
import asyncio
from services.profile_rules import apply as apply_profile_rules
from services.profile_service import save_answer_records, load_existing_profile, save_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/materials", tags=["materials"])

@router.get("/library/{user_id}")
async def get_material_library(user_id: int, current_user_id: int = Depends(get_current_user_id)):
    require_self(user_id, current_user_id)
    if user_id <= 0:
        raise HTTPException(400, "user_id 无效")
    if not db_conn._async_session_factory:
        raise HTTPException(500, "数据库未初始化")
    async with db_conn._async_session_factory() as db:
        materials = await get_all_materials(db, user_id)
        weak_points = await get_weak_points(db, user_id)
        due_reviews = await get_due_reviews(db, user_id)
        chapters = []
        for ch_id in CHAPTER_ORDER:
            ch = KNOWLEDGE_GRAPH.get(ch_id, {})
            mastery = await get_user_chapter_mastery(db, user_id, ch_id)
            stage = get_stage(mastery)
            stage_cfg = get_stage_config(stage)
            ch_materials = [m for m in materials if m.chapter_id == ch_id]
            ch_weak = [w for w in weak_points if w.chapter_id == ch_id]
            current_mat = ch_materials[0] if ch_materials else None
            chapters.append({
                "id": ch_id,
                "name": ch.get("name", ch_id),
                "icon": ch.get("icon", "📌"),
                "difficulty": ch.get("difficulty", 3),
                "mastery": mastery,
                "stage": stage,
                "stage_label": stage_cfg["label"],
                "has_material": current_mat is not None,
                "material_id": current_mat.id if current_mat else None,
                "material_count": len(ch_materials),
                "material_created": current_mat.created_at.isoformat() if current_mat and current_mat.created_at else None,
                "review_count": current_mat.review_count if current_mat else 0,
                "weak_points": [{"id": w.id, "point": w.knowledge_point, "count": w.occurrence_count} for w in ch_weak],
                "is_due_review": any(m.chapter_id == ch_id for m in due_reviews),
            })
        return {
            "success": True,
            "data": {
                "chapters": chapters,
                "due_reviews": [{"chapter_id": m.chapter_id, "next_review": m.next_review_at.isoformat() if m.next_review_at else None} for m in due_reviews],
                "total_materials": len(materials),
                "total_weak_points": len(weak_points),
            }
        }

@router.get("/detail/{user_id}/{chapter_id}")
async def get_material_detail(user_id: int, chapter_id: str, current_user_id: int = Depends(get_current_user_id)):
    require_self(user_id, current_user_id)
    if chapter_id not in KNOWLEDGE_GRAPH:
        raise HTTPException(404, f"未知章节: {chapter_id}")
    if not db_conn._async_session_factory:
        raise HTTPException(500, "数据库未初始化")
    async with db_conn._async_session_factory() as db:
        material = await get_current_material(db, user_id, chapter_id)
        all_materials = await get_chapter_materials(db, user_id, chapter_id)
        mastery = await get_user_chapter_mastery(db, user_id, chapter_id)
        stage = get_stage(mastery)
        history = await get_mastery_history(db, user_id, chapter_id)
        weak = await get_weak_points(db, user_id, chapter_id)
        all_stages = []
        for s, cfg in STAGE_CONFIG.items():
            all_stages.append({
                "stage": s, "label": cfg["label"],
                "min": cfg["min"], "max": cfg["max"],
                "unlocked": mastery >= cfg["min"],
                "is_current": s == stage,
            })
        materials_list = []
        for m in all_materials:
            materials_list.append({
                "id": m.id,
                "stage": m.stage,
                "is_current": m.is_current,
                "content": m.content,
                "weak_points": m.weak_points,
                "mastery_at_creation": m.mastery_at_creation,
                "review_count": m.review_count,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "next_review_at": m.next_review_at.isoformat() if m.next_review_at else None,
            })
        return {
            "success": True,
            "data": {
                "chapter_id": chapter_id,
                "chapter": KNOWLEDGE_GRAPH[chapter_id],
                "mastery": mastery,
                "stage": stage,
                "stage_config": get_stage_config(stage),
                "all_stages": all_stages,
                "material": materials_list[0] if materials_list else None,
                "all_materials": materials_list,
                "mastery_history": [{"mastery": h.mastery, "source": h.source, "detail": h.detail, "created_at": h.created_at.isoformat() if h.created_at else None} for h in history],
                "weak_points": [{"id": w.id, "point": w.knowledge_point, "count": w.occurrence_count, "doc_section": w.doc_section_id} for w in weak],
            }
        }

@router.post("/generate/{user_id}/{chapter_id}")
async def generate_material(user_id: int, chapter_id: str, current_user_id: int = Depends(get_current_user_id)):
    require_self(user_id, current_user_id)
    if user_id <= 0:
        raise HTTPException(400, "user_id 无效")
    if chapter_id not in KNOWLEDGE_GRAPH:
        raise HTTPException(404, f"未知章节: {chapter_id}")
    if not db_conn._async_session_factory:
        raise HTTPException(500, "数据库未初始化")
    chapter = KNOWLEDGE_GRAPH[chapter_id]
    async def event_generator():
        try:
            mastery = 0.0
            stage = "seedling"
            stage_cfg = STAGE_CONFIG["seedling"]
            profile = None
            weak_names: list[str] = []

            if db_conn._async_session_factory:
                async with db_conn._async_session_factory() as db:
                    mastery = await get_user_chapter_mastery(db, user_id, chapter_id)
                    stage = get_stage(mastery)
                    stage_cfg = get_stage_config(stage)
                    result = await db.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
                    p = result.scalar_one_or_none()
                    if p:
                        profile = {
                            "major": p.major, "grade_level": p.grade_level,
                            "target_role": p.target_role, "preferred_language": p.preferred_language,
                            "skill_matrix": p.skill_matrix or {}, "cognitive_profile": p.cognitive_profile or {},
                            "capability_matrix": p.capability_matrix or {},
                        }
                    weak = await get_weak_points(db, user_id, chapter_id)
                    weak_names = [w.knowledge_point for w in weak]

            yield f"data: {json.dumps({'type': 'stage_info', 'mastery': mastery, 'stage': stage, 'stage_label': stage_cfg['label'], 'chapter': chapter['name']}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'agent_start', 'agent': 'parallel', 'name': '文档+题库 并行生成中', 'icon': '🚀', 'stage': 1, 'total': 3}, ensure_ascii=False)}\n\n"
            import asyncio
            from services.llm_service import get_llm_service
            llm = await get_llm_service()
            doc_prompt = _build_doc_prompt(chapter, stage_cfg, profile, mastery, weak_names, user_id)
            quiz_prompt = _build_quiz_prompt(chapter, stage_cfg, profile, "", weak_names, user_id)
            doc_profile_hash = _hash_profile(profile, user_id)
            doc_result_obj = None
            doc_content = ""
            doc_last_error = ""
            questions = []
            quiz_result_obj = None
            quiz_last_error = ""

            event_queue: asyncio.Queue = asyncio.Queue()

            async def _emit(event_str: str):
                await event_queue.put(event_str)

            async def _run_doc():
                nonlocal doc_content, doc_result_obj, doc_last_error
                for attempt in range(1, 2):  # token优化：失败不重试，减少重复LLM调用
                    try:
                        await _emit(json.dumps({'type': 'agent_start', 'agent': 'generate_doc', 'name': '文档Agent', 'icon': '📖', 'stage': 1, 'attempt': attempt, 'total': 1}, ensure_ascii=False))
                        doc_result_obj = await llm.chat(
                            messages=[{"role": "user", "content": doc_prompt}],
                            system_prompt=skill_prompt_prefix("lecture-authoring", max_chars_per_skill=1600) + f"你是机器学习教学专家，当前为{stage_cfg['label']}阶段学生生成内容。",
                            max_tokens=stage_cfg["max_tokens"],
                            cache_agent="doc",
                            cache_chapter=chapter_id,
                            cache_stage=stage,
                            cache_profile_hash=doc_profile_hash,
                            cache_user_id=user_id,
                            use_cache=False,
                            write_cache=False
                        )
                        tmp = doc_result_obj.get("content", "")
                        is_valid, reason = _is_valid_doc_content(tmp)
                        if is_valid:
                            doc_content = tmp
                            return
                        doc_last_error = reason
                        logger.warning(f"[Generate] doc 校验失败 (第{attempt}次): {reason}")
                        if attempt < 2:
                            await _emit(json.dumps({'type': 'agent_content', 'agent': 'generate_doc', 'content_parts': {'retry': f'文档格式校验失败: {reason}，正在重试...'}, 'attempt': attempt}, ensure_ascii=False))
                    except Exception as e:
                        logger.error(f"文档生成异常 (第{attempt}次): {e}")
                        doc_last_error = str(e)

            async def _run_quiz():
                nonlocal questions, quiz_result_obj, quiz_last_error
                for attempt in range(1, 2):  # token优化：失败不重试
                    try:
                        await _emit(json.dumps({'type': 'agent_start', 'agent': 'generate_questions', 'name': '题库Agent', 'icon': '❓', 'stage': 1, 'attempt': attempt, 'total': 1}, ensure_ascii=False))
                        quiz_result_obj = await llm.chat(
                            messages=[{"role": "user", "content": quiz_prompt}],
                            system_prompt=skill_prompt_prefix("question-authoring", max_chars_per_skill=1600) + "你是机器学习出题专家，严格返回JSON格式题目列表。",
                            max_tokens=2000,
                            cache_agent="quiz",
                            cache_chapter=chapter_id,
                            cache_stage=stage,
                            cache_profile_hash=doc_profile_hash,
                            cache_user_id=user_id,
                            use_cache=False,
                            write_cache=False
                        )
                        tmp = _parse_questions(quiz_result_obj.get("content", "[]"))
                        is_valid, reason = _is_valid_quiz_content(tmp)
                        if is_valid:
                            questions = tmp
                            return
                        quiz_last_error = reason
                        logger.warning(f"[Generate] quiz 校验失败 (第{attempt}次): {reason}")
                        if attempt < 2:
                            await _emit(json.dumps({'type': 'agent_content', 'agent': 'generate_questions', 'content_parts': {'retry': f'题库格式校验失败: {reason}，正在重试...'}, 'attempt': attempt}, ensure_ascii=False))
                    except Exception as e:
                        logger.error(f"题目生成异常 (第{attempt}次): {e}")
                        quiz_last_error = str(e)

            import time
            doc_task = asyncio.create_task(_run_doc())
            quiz_task = asyncio.create_task(_run_quiz())
            start_time = time.time()
            overall_timeout = 180
            poll_interval = 0.3
            while not (doc_task.done() and quiz_task.done()):
                if time.time() - start_time > overall_timeout:
                    logger.error(f"并行生成超时 ({overall_timeout}s)")
                    doc_task.cancel()
                    quiz_task.cancel()
                    break
                await asyncio.sleep(poll_interval)
                while not event_queue.empty():
                    try:
                        ev = event_queue.get_nowait()
                        yield f"data: {ev}\n\n"
                    except asyncio.QueueEmpty:
                        break
            while not event_queue.empty():
                try:
                    ev = event_queue.get_nowait()
                    yield f"data: {ev}\n\n"
                except asyncio.QueueEmpty:
                    break
            try:
                doc_task.result()
            except (asyncio.CancelledError, Exception) as e:
                logger.warning(f"doc_task 异常: {e}")
            try:
                quiz_task.result()
            except (asyncio.CancelledError, Exception) as e:
                logger.warning(f"quiz_task 异常: {e}")

            if not doc_content:
                logger.error(f"文档生成2次都失败: {doc_last_error}")
                placeholder = f"# {chapter['name']} - {stage_cfg['label']}\n\n## [SEC-01] 核心概念\n\n文档生成遇到问题，请重新生成。\n\n## [SEC-02] 原理详解\n\n暂无内容\n\n## [SEC-03] 代码示例\n\n暂无内容\n\n## [SEC-04] 应用场景\n\n暂无内容\n\n## [SEC-05] 常见误区与总结\n\n暂无内容"
                doc_content = placeholder
                doc_sections = _parse_sections(doc_content)
                yield f"data: {json.dumps({'type': 'error', 'message': f'文档生成2次都失败: {doc_last_error}，请稍后重试', 'stage': 1}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'error': True}, ensure_ascii=False)}\n\n"
                return
            if not questions:
                logger.error(f"题目生成2次都失败: {quiz_last_error}")
                yield f"data: {json.dumps({'type': 'error', 'message': f'题目生成2次都失败: {quiz_last_error}', 'stage': 1}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'error': True}, ensure_ascii=False)}\n\n"
                return
            doc_sections = _parse_sections(doc_content)
            yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'generate_doc', 'content_parts': {'document': {'title': chapter['name'] + ' - ' + stage_cfg['label'], 'content': doc_content, 'sections': doc_sections}}}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'agent_complete', 'agent': 'generate_doc', 'stage': 1}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'generate_questions', 'content_parts': {'questions': questions}}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'agent_complete', 'agent': 'generate_questions', 'stage': 2}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'agent_complete', 'agent': 'generate_questions', 'stage': 2}, ensure_ascii=False)}\n\n"
            material_content = {
                "document": {"title": chapter['name'] + ' - ' + stage_cfg['label'], "content": doc_content, "sections": doc_sections},
                "questions": questions,
            }
            material_id = None
            db_save_ok = False
            if db_conn._async_session_factory:
                async with db_conn._async_session_factory() as db:
                    material = await save_material(db, user_id, chapter_id, stage, material_content, profile_snapshot=profile, weak_points=weak_names)
                    material_id = material.id
                    stage_boost = min(0.1, 0.02 * len(weak_names) + 0.05) if not weak_names else 0.05
                    new_mastery_for_gen = min(mastery + stage_boost, 5.0)
                    await record_mastery(db, user_id, chapter_id, new_mastery_for_gen, "generation", {"stage": stage, "boost": round(stage_boost, 3)})
                    db_save_ok = True

            yield f"data: {json.dumps({'type': 'agent_start', 'agent': 'save', 'name': '资料存储', 'icon': '💾', 'stage': 3, 'total': 3}, ensure_ascii=False)}\n\n"
            stage_label = stage_cfg["label"]
            yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'save', 'content': f'资料已保存到学习资料库，阶段: {stage_label}'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'agent_complete', 'agent': 'save', 'stage': 3}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'material_id': material_id, 'stage': stage, 'mastery': mastery}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"资料生成流程异常: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': f'生成失败: {str(e)}'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*"})

@router.post("/quiz-submit/{user_id}/{chapter_id}")
async def submit_quiz(user_id: int, chapter_id: str, answers: dict, current_user_id: int = Depends(get_current_user_id)):
    require_self(user_id, current_user_id)
    if not db_conn._async_session_factory:
        raise HTTPException(500, "数据库未初始化")
    async with db_conn._async_session_factory() as db:
        mastery = await get_user_chapter_mastery(db, user_id, chapter_id)
        results = []
        total_delta = 0.0
        for ans in answers.get("answers", []):
            qid = ans.get("question_id", "")
            correct = ans.get("correct", False)
            difficulty = ans.get("difficulty", 2)
            kp = ans.get("knowledge_point", "")
            delta = calculate_mastery_delta(correct, difficulty, mastery)
            total_delta += delta
            results.append({"question_id": qid, "correct": correct, "delta": delta})
            if not correct and kp:
                await record_weak_point(db, user_id, chapter_id, kp, ans.get("doc_section_id"))
        new_mastery = max(0.0, min(5.0, mastery + total_delta))
        await record_mastery(db, user_id, chapter_id, new_mastery, "quiz", {"answers": results, "total_delta": round(total_delta, 2)})
        old_stage = get_stage(mastery)
        new_stage = get_stage(new_mastery)
        stage_upgraded = new_stage != old_stage and new_mastery > mastery
        # 画像自动更新：记答题记录 → 规则引擎即时改
        answers_payload = []
        for ans in answers.get("answers", []):
            answers_payload.append({
                "question_id": str(ans.get("question_id", "")),
                "topic": ans.get("knowledge_point", chapter_id),
                "correct": ans.get("correct", False),
                "time_spent_sec": float(ans.get("time_spent", 0)),
            })
        await save_answer_records(db, user_id, answers_payload, "quiz")

        existing = await load_existing_profile(db, user_id) or {}
        updated = apply_profile_rules(existing, answers_payload)
        await save_profile(db, user_id, {"profile_data": updated})

        return {
            "success": True,
            "data": {
                "old_mastery": mastery, "new_mastery": new_mastery,
                "old_stage": old_stage, "new_stage": new_stage,
                "stage_upgraded": stage_upgraded,
                "new_stage_label": get_stage_config(new_stage)["label"],
                "results": results,
            }
        }

@router.get("/llm-health")
async def llm_health_check(current_user_id: int = Depends(get_current_user_id)):
    """检查当前 LLM 服务是否可用，验证 API Key 和模型配置"""
    try:
        from services.llm_service import get_llm_service
        llm = await get_llm_service()
        start = time.time()
        result = await llm.chat(
            messages=[{"role": "user", "content": "ping"}],
            system_prompt="只回复 ok",
            max_tokens=10,
            use_cache=False,
            write_cache=False
        )
        elapsed = time.time() - start
        return {
            "success": True,
            "data": {
                "model": result.get("model", "unknown"),
                "latency": round(elapsed, 3),
                "content_preview": result.get("content", "")[:50],
                "tokens": result.get("tokens_used", 0)
            }
        }
    except Exception as e:
        logger.error(f"[LLM健康检查] 失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "hint": "检查 .env 中 LLM_PROVIDER、DEEPSEEK_API_KEY、DEEPSEEK_BASE_URL、DEEPSEEK_MODEL 配置"
        }

@router.post("/review/{material_id}")
async def review_material(material_id: int, current_user_id: int = Depends(get_current_user_id)):
    if not db_conn._async_session_factory:
        raise HTTPException(500, "数据库未初始化")
    async with db_conn._async_session_factory() as db:
        result = await db.execute(select(LearningMaterial).where(LearningMaterial.id == material_id))
        mat = result.scalar_one_or_none()
        if not mat or mat.user_id != current_user_id:
            raise HTTPException(404, "资料不存在")
        mat.review_count += 1
        mat.last_reviewed_at = datetime.utcnow()
        mat.next_review_at = get_next_review_date(mat.review_count, mat.last_reviewed_at)
        await db.commit()
        return {"success": True, "data": {"review_count": mat.review_count, "next_review_at": mat.next_review_at.isoformat() if mat.next_review_at else None}}

@router.post("/weak-point/resolve/{wp_id}")
async def resolve_wp(wp_id: int, current_user_id: int = Depends(get_current_user_id)):
    if not db_conn._async_session_factory:
        raise HTTPException(500, "数据库未初始化")
    async with db_conn._async_session_factory() as db:
        owner_check = await db.execute(select(WeakPoint).where(WeakPoint.id == wp_id))
        wp_owner = owner_check.scalar_one_or_none()
        if not wp_owner or wp_owner.user_id != current_user_id:
            raise HTTPException(404, "薄弱点不存在")
        wp = await resolve_weak_point(db, wp_id)
        if not wp:
            raise HTTPException(404, "薄弱点不存在")
        return {"success": True}

@router.get("/mastery-history/{user_id}/{chapter_id}")
async def mastery_history(user_id: int, chapter_id: str, limit: int = 30, current_user_id: int = Depends(get_current_user_id)):
    require_self(user_id, current_user_id)
    if not db_conn._async_session_factory:
        raise HTTPException(500, "数据库未初始化")
    async with db_conn._async_session_factory() as db:
        history = await get_mastery_history(db, user_id, chapter_id, limit)
        return {"success": True, "data": [{"mastery": h.mastery, "source": h.source, "detail": h.detail, "created_at": h.created_at.isoformat() if h.created_at else None} for h in history]}


def _is_valid_doc_content(content: str) -> tuple:
    """
    校验 doc_content 是否为合法的 Markdown 教学文档

    Returns:
        (is_valid, reason): 布尔值+失败原因
    """
    if not content or not content.strip():
        return False, "内容为空"

    stripped = content.strip()

    # 1. 严格禁止以 JSON 数组/对象开头
    if stripped.startswith('[') and ('"id"' in stripped[:200] or '"question"' in stripped[:200]):
        return False, "内容是题目 JSON 数组"
    if stripped.startswith('{') and '"id"' in stripped[:200]:
        return False, "内容是题目 JSON 对象"

    # 2. 包含题目相关特征字段（说明 LLM 把题目塞进文档了）
    suspicious_fields = ['"question":', '"explanation":', '"knowledge_point":', '"key_points":', '"doc_section_id":', '"options":']
    for f in suspicious_fields:
        if f in stripped[:5000]:
            return False, f"包含题库字段 {f}"

    # 3. 必须以 Markdown 标题开头
    first_line = stripped.split('\n', 1)[0]
    if not first_line.startswith('# '):
        return False, f"首行不是 Markdown 标题: {first_line[:50]}"

    # 4. 必须包含至少 3 个 SEC 段落
    sec_count = stripped.count('## [SEC-')
    if sec_count < 3:
        return False, f"SEC 段落不足 (只有 {sec_count} 个，需要至少 3 个)"

    # 5. 长度检查（不能太短也不能完全是空）
    if len(stripped) < 300:
        return False, f"内容过短 ({len(stripped)} 字符)"

    # 6. 包含 ```json 标记也算错误
    if '```json' in stripped.lower():
        return False, "包含 ```json 代码块"

    return True, "ok"


def _is_valid_quiz_content(questions: list) -> tuple:
    """
    校验题目列表是否合法

    Returns:
        (is_valid, reason)
    """
    if not questions or not isinstance(questions, list):
        return False, "题目列表为空"
    if len(questions) < 1:
        return False, "题目数量为 0"

    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            return False, f"题目 {i+1} 不是 dict"
        if not q.get("id"):
            return False, f"题目 {i+1} 缺少 id"
        if not q.get("type"):
            return False, f"题目 {i+1} 缺少 type"
        if not q.get("question"):
            return False, f"题目 {i+1} 缺少 question"
        if not q.get("answer"):
            return False, f"题目 {i+1} 缺少 answer"

    return True, "ok"


def _build_doc_prompt(chapter: dict, stage_cfg: dict, profile: dict, mastery: float, weak_points: list, user_id: int = 0) -> str:
    profile_str = ""
    preferred_language = "Python"
    if profile:
        parts = []
        if profile.get("major"): parts.append(f"专业: {profile['major']}")
        if profile.get("grade_level"): parts.append(f"年级: {profile['grade_level']}")
        if profile.get("preferred_language"):
            preferred_language = profile["preferred_language"]
            parts.append(f"编程语言: {preferred_language}")
        if profile.get("target_role"): parts.append(f"目标职业: {profile['target_role']}")
        if profile.get("cognitive_profile"):
            cp = profile["cognitive_profile"]
            if isinstance(cp, dict):
                if cp.get("learning_style"): parts.append(f"学习风格: {cp['learning_style']}")
                if cp.get("reading_speed"): parts.append(f"阅读速度: {cp['reading_speed']}")
        if profile.get("motivation_profile"):
            mp = profile["motivation_profile"]
            if isinstance(mp, dict):
                if mp.get("intrinsic_motivation"): parts.append(f"内在动机: {mp['intrinsic_motivation']}")
        if profile.get("confidence_score"):
            parts.append(f"自信度: {float(profile['confidence_score']):.1f}")
        cap = profile.get("capability_matrix", {})
        if cap:
            strong, weak_ch = [], []
            for ch_id, ch_data in cap.items():
                if isinstance(ch_data, dict):
                    for kp, v in ch_data.items():
                        if isinstance(v, dict) and "score" in v:
                            s = v["score"]
                            if s >= 4: strong.append(f"{kp}({s})")
                            elif s <= 2: weak_ch.append(f"{kp}({s})")
            if strong: parts.append(f"优势: {', '.join(strong[:5])}")
            if weak_ch: parts.append(f"薄弱: {', '.join(weak_ch[:5])}")
        if user_id:
            parts.append(f"[uid={user_id}]")
        profile_str = "\n".join(parts)
    else:
        profile_str = "暂无画像"
        if user_id:
            profile_str += f"\n[uid={user_id}]"

    weak_str = ", ".join(weak_points) if weak_points else "无"
    kps = ", ".join(kp["name"] for kp in chapter.get("knowledge_points", []))

    return f"""为以下学生生成「{chapter['name']}」的{stage_cfg['label']}阶段教学文档。

【学生画像】掌握度 {mastery}/5.0
{profile_str}
当前薄弱知识点: {weak_str}

【章节信息】
- 知识点: {kps}
- 难度: {'⭐' * chapter.get('difficulty', 3)}

【生成要求 - {stage_cfg['label']}阶段】
文档风格: {stage_cfg['doc_style']}

请严格按以下Markdown结构生成:
# {chapter['name']} - {stage_cfg['label']}

## [SEC-01] 核心概念
(核心概念解释)

## [SEC-02] 原理详解
(原理推导/详细说明)

## [SEC-03] 代码示例
(用{preferred_language}写完整可运行代码)

## [SEC-04] 应用场景
(实际应用案例)

## [SEC-05] 常见误区与总结
(易错点+总结)

每个 ## 标题必须包含 [SEC-XX] 编号，用于后续题目关联。
重要：必须完整生成全部5个SEC段落，不要中途截断！每个SEC段落控制在200-400字，总字数1500-2500字。
如果篇幅不够，优先保证5个SEC段落都存在，可以适当缩减每个段落的长度。

【严格输出格式 - 违反任意一条即为错误】
1. ⛔ 严格禁止输出 JSON 格式（包括但不限于 [{{...}}]、{{"key": "value"}}、题目数组等结构化数据）
2. ⛔ 严格禁止输出 Python 代码块以外的代码（仅可在 [SEC-03] 代码示例 段落中使用代码块）
3. ⛔ 严格禁止输出类似"题目"、"选择题"、"答案"等出题相关内容
4. ⛔ 严格禁止使用 ```json ``` 标记
5. ✅ 只能输出 Markdown 文本，且必须以 "# {chapter['name']} - {stage_cfg['label']}" 开头
6. ✅ 必须包含完整的 5 个 [SEC-01] 到 [SEC-05] 段落
7. ✅ 在 [SEC-05] 段落结束后立即停止，不要追加任何内容（不要总结、不要问好、不要附加题库）
8. ✅ 不要使用列表/JSON 形式罗列"参考"或"练习"，文档本身就是全部输出

【反例 - 绝不允许】
❌ 开篇直接是 [{{"id":"q1",...}}]
❌ 输出 ```json {{"questions":[...]}} ```
❌ 文档末尾出现 "答案：C" 或 "正确答案是" 等题目相关内容
❌ 包含 "explanation" "knowledge_point" "key_points" 等字段

【正确示例】
# 机器学习基础 - 🌱 启蒙
## [SEC-01] 核心概念
**机器学习** 是...
（这里写正文，不要用 JSON）
## [SEC-02] 原理详解
...

请直接开始输出文档内容，不要在文档前加任何前言/说明/标题以外的文字。"""


def _build_quiz_prompt(chapter: dict, stage_cfg: dict, profile: dict, doc_content: str, weak_points: list, user_id: int = 0) -> str:
    doc_preview = doc_content[:2000] if doc_content else ""
    weak_str = ", ".join(weak_points) if weak_points else "无"
    stage_name = stage_cfg['label']
    quiz_ratio = stage_cfg['quiz_ratio']
    stage_key = [k for k, v in STAGE_CONFIG.items() if v['label'] == stage_name]
    stage_key = stage_key[0] if stage_key else "seedling"

    type_rules = {
        "seedling": "生成5道题：3道概念辨析选择题（问'以下哪个是XX？''XX属于哪类？'）+2道判断题（对错判断）。禁止出简答题、推导题、计算题。题目必须非常基础，答案能从文档中直接找到原话。",
        "sprout": "生成5道题：2道直接应用选择题+2道概念辨析判断题+1道简单填空题。可以有1道简述题但必须给出答题框架。题目侧重理解和直接应用。",
        "practice": "生成5道题：1道基础选择题+2道辨析应用题（对比两个概念的区别）+1道综合选择题+1道简答题(text)。题目侧重辨析和变体应用。",
        "advanced": "生成5道题：1道基础题+1道综合分析题+1道开放设计题(text)+1道代码实现题(coding)+1道综合选择题。题目侧重深度理解和创新应用。",
        "master": "生成5道题：1道综合题+2道设计方案题(text)+1道论文复现/前沿分析题(case_study)+1道综合选择题。题目侧重研究能力和教学他人能力。",
    }
    type_rule = type_rules.get(stage_key, type_rules["seedling"])

    uid_tag = f"\n[题目专属用户ID: {user_id}]" if user_id else ""

    return f"""基于以下文档内容，为「{chapter['name']}」章节生成5道练习题。{uid_tag}

【文档内容（节选）】
{doc_preview}

【题目要求 - {stage_name}阶段】
难度分布: {quiz_ratio}
薄弱知识点重点出题: {weak_str}

【题型规则 - 必须严格遵守】
{type_rule}

请严格返回JSON数组:
[
  {{
    "id": "q1",
    "type": "choice",
    "question": "题目内容",
    "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
    "answer": "B",
    "explanation": "详细解析，引用文档中的内容",
    "difficulty": 2,
    "knowledge_point": "对应知识点名称",
    "doc_section_id": "SEC-01",
    "hints": ["提示1"],
    "key_points": ["关键词1", "关键词2", "关键词3"]
  }}
]

字段说明:
- choice/judge题: type="choice"/"judge", options必填, answer为选项字母
- fill题: type="fill", answer为标准答案文本, key_points为答案核心词
- 简答题: type="text", answer为参考答案, key_points必须填写(2-5个核心术语,用于关键词评分)
- 编程题: type="coding", answer为参考代码, key_points必须填写(2-5个技术关键词,如算法名、API等)
- 案例分析题: type="case_study", answer为分析框架, key_points必须填写(2-5个分析维度)
- 所有题都必须有doc_section_id, 关联到对应的SEC-XX
- 判分规则: text/coding/case_study题要求用户答对40%以上key_points算正确
- 编程题和案例题如果没有options, 用户通过文本框作答
- 案例分析题如果有options, 视为多选题处理
- doc_section_id必须对应文档中的SEC-XX编号"""


def _hash_profile(profile: dict | None, user_id: int = 0) -> str:
    if not profile and not user_id:
        return "default"
    keys_of_interest = [
        "major", "grade_level", "preferred_language", "target_role",
        "skill_matrix", "cognitive_profile", "misconception_archive",
        "behavior_pattern", "motivation_profile", "confidence_score",
    ]
    subset = {k: profile.get(k) for k in keys_of_interest} if profile else {}
    cap = (profile.get("capability_matrix") if profile else None) or {}
    if isinstance(cap, dict):
        cap_subset = {}
        for ch_id, ch_data in cap.items():
            if isinstance(ch_data, dict):
                cap_subset[ch_id] = {kp: v.get("score") for kp, v in ch_data.items() if isinstance(v, dict)}
        subset["capability_matrix"] = cap_subset
    raw = json.dumps({"user_id": int(user_id or 0), "profile": subset}, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _parse_sections(doc_content: str) -> list:
    sections = []
    lines = doc_content.split("\n")
    current = None
    buf = []
    for line in lines:
        if line.startswith("## [SEC-"):
            if current:
                current["content"] = "\n".join(buf).strip()
                sections.append(current)
            sec_id = line.split("[SEC-")[1].split("]")[0]
            sec_id = "SEC-" + sec_id
            title = line.split("]")[1].strip() if "]" in line else line
            current = {"id": sec_id, "title": title, "content": ""}
            buf = []
        elif current:
            buf.append(line)
    if current:
        current["content"] = "\n".join(buf).strip()
        sections.append(current)
    return sections


def _parse_questions(raw: str) -> list:
    if not raw or not raw.strip():
        logger.warning("[_parse_questions] 输入为空")
        return []
    try:
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        qs = json.loads(raw)
        if not isinstance(qs, list):
            qs = [qs]
        type_map = {"truefalse": "judge", "true_false": "judge", "boolean": "judge",
                    "short_answer": "text", "essay": "text", "open": "text",
                    "multiple_choice": "choice", "single_choice": "choice",
                    "code": "coding", "programming": "coding",
                    "case": "case_study", "scenario": "case_study", "analysis": "case_study"}
        for q in qs:
            if isinstance(q, dict):
                q["type"] = type_map.get(q.get("type", ""), q.get("type", "choice"))
                if q.get("type") == "judge" and not q.get("options"):
                    q["options"] = ["正确", "错误"]
                if not q.get("key_points") and q.get("type") in ("text", "coding", "case_study"):
                    kp = _extract_key_points(q.get("answer", ""), q.get("explanation", ""))
                    if kp:
                        q["key_points"] = kp
        return qs
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.error(f"[_parse_questions] JSON解析失败: {e}, raw前300字: {raw[:300]!r}")
        return []


def _extract_key_points(answer: str, explanation: str, max_n: int = 5) -> list:
    import re
    stop = set("的了是在有和与或不也都而为中上下到从被把让给对等着过的地得之其这那它他我你们会能可要将就已所以因为但又很更最比还当于及个种些样类间内外前后时里只即则若虽然且并".replace(" ", ""))
    text = (answer or "") + "。" + (explanation or "")
    chunks = re.split(r"[，。、；：！？（）()\[\]【】\{\}《》\s,.;:!?()\[\]\{\}\<\>]", text)
    candidates = []
    for c in chunks:
        c = c.strip()
        if 2 <= len(c) <= 10 and c not in stop and not re.fullmatch(r"\d+", c):
            if c not in candidates:
                candidates.append(c)
        if len(candidates) >= max_n * 3:
            break
    return candidates[:max_n]
