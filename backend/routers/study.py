"""
学习中心 API - SOLO 简化版
4个核心 Agent: 意图识别 → 画像Agent → 文档Agent → 题库Agent → 总结节点
"""

from __future__ import annotations

import logging
import json
import asyncio
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select

import database.connection as db_conn
from database.models import StudentProfile, LearningResource
from services.knowledge_graph import KNOWLEDGE_GRAPH
from graph.runtime import execute_workflow
from utils.auth import get_current_user_id, require_self

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/study", tags=["study"])

@router.get("/health")
async def health_check():
    return {"status": "ok", "message": "Study API is working"}

CHAPTER_AGENT_PROMPTS = {
    "ml_overview": "机器学习概述章节，涵盖机器学习定义与分类、监督/无监督/强化学习区别、训练集/验证集/测试集划分、偏差-方差权衡。帮助建立ML全局视野。",
    "math_foundation": "数学基础章节，涵盖线性代数、概率统计、微积分、优化方法、信息论基础。重点讲解向量矩阵运算、概率分布、梯度下降、熵与信息增益等核心概念。",
    "programming": "编程与数据处理章节，涵盖Python编程、NumPy、Pandas、数据预处理、scikit-learn。重点用代码实现数学公式，数据清洗与特征标准化，理论与实践结合。",
    "supervised_learning": "监督学习核心章节，涵盖线性回归、逻辑回归、朴素贝叶斯、KNN、决策树、SVM、集成学习。每个算法需从原理推导到代码实现，配合可视化案例。",
    "unsupervised_learning": "无监督学习章节，涵盖K-Means聚类、层次聚类、DBSCAN密度聚类、PCA降维、t-SNE可视化。强调降维可视化、聚类评估、实际应用场景。",
    "neural_networks": "神经网络基础章节，涵盖感知机、反向传播、激活函数与优化器、深度学习框架。从感知机到多层网络，配合PyTorch代码，掌握训练流程。",
    "deep_learning": "深度学习章节，涵盖CNN卷积网络、RNN与LSTM、Transformer与注意力机制、GAN生成对抗网络、迁移学习与预训练。Transformer是核心，务必深入理解自注意力。",
    "model_evaluation": "模型评估与调优章节，涵盖过拟合与欠拟合、交叉验证、评估指标、正则化、特征工程、超参数调优。强调实战调参技巧和模型选择策略。",
    "advanced_interest": "进阶方向章节，涵盖NLP、计算机视觉、大语言模型、强化学习、推荐系统、MLOps。大语言模型是必学方向，建议根据兴趣选择1-2个方向深入。"
}

AGENT_DISPLAY = {
    "analyze_intent": {"name": "意图识别", "icon": "🎯", "stage": 1},
    "build_profile": {"name": "画像Agent", "icon": "👤", "stage": 2},
    "generate_doc": {"name": "文档Agent", "icon": "📝", "stage": 3},
    "generate_questions": {"name": "题库Agent", "icon": "❓", "stage": 4},
    "final_summary": {"name": "总结节点", "icon": "✅", "stage": 5},
}


async def _load_profile(db, user_id: int) -> dict | None:
    result = await db.execute(
        select(StudentProfile).where(StudentProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        return None
    return {
        "major": profile.major,
        "grade_level": profile.grade_level,
        "target_role": profile.target_role,
        "prior_courses": profile.prior_courses or [],
        "preferred_language": profile.preferred_language,
        "skill_matrix": profile.skill_matrix or {},
        "cognitive_profile": profile.cognitive_profile or {},
        "misconception_archive": profile.misconception_archive or {},
        "behavior_pattern": profile.behavior_pattern or {},
        "motivation_profile": profile.motivation_profile or {},
        "confidence_score": profile.confidence_score or 0.5,
        "profile_version": profile.profile_version or 1,
        "overall_score": profile.overall_score or 0,
        "capability_matrix": profile.capability_matrix or {},
    }


def _build_profile_context(profile: dict) -> str:
    if not profile:
        return "暂无学生画像数据，请按通用水平生成内容。"

    parts = []
    if profile.get("major"):
        parts.append(f"- 专业：{profile['major']}")
    if profile.get("grade_level"):
        parts.append(f"- 年级：{profile['grade_level']}")
    if profile.get("target_role"):
        parts.append(f"- 目标职业：{profile['target_role']}")
    if profile.get("preferred_language"):
        parts.append(f"- 常用语言：{profile['preferred_language']}")

    skill_matrix = profile.get("skill_matrix", {})
    if skill_matrix:
        weak_skills = []
        strong_skills = []
        for category, skills in skill_matrix.items():
            if isinstance(skills, dict):
                for skill_name, score in skills.items():
                    if isinstance(score, (int, float)):
                        if score <= 2:
                            weak_skills.append(f"{category}/{skill_name}({score}/5)")
                        elif score >= 4:
                            strong_skills.append(f"{category}/{skill_name}({score}/5)")
        if weak_skills:
            parts.append(f"- 薄弱环节：{', '.join(weak_skills[:5])}")
        if strong_skills:
            parts.append(f"- 优势领域：{', '.join(strong_skills[:5])}")

    cognitive = profile.get("cognitive_profile", {})
    if cognitive:
        if cognitive.get("learning_style"):
            parts.append(f"- 学习风格：{cognitive['learning_style']}")
        if cognitive.get("learning_pace"):
            parts.append(f"- 学习节奏：{cognitive['learning_pace']}")

    if not parts:
        return "暂无学生画像数据，请按通用水平生成内容。"
    return "学生画像：\n" + "\n".join(parts)


@router.get("/generate/{user_id}/{chapter_id}")
async def generate_study_materials(
    user_id: int,
    chapter_id: str,
    current_user_id: int = Depends(get_current_user_id),
):
    require_self(user_id, current_user_id)
    if chapter_id not in KNOWLEDGE_GRAPH:
        raise HTTPException(404, f"未知章节: {chapter_id}")

    chapter = KNOWLEDGE_GRAPH[chapter_id]
    chapter_context = CHAPTER_AGENT_PROMPTS.get(chapter_id, chapter.get("description", ""))

    async def event_generator():
        db = None
        try:
            if not db_conn._async_session_factory:
                yield f"data: {json.dumps({'type': 'error', 'message': '数据库未初始化'}, ensure_ascii=False)}\n\n"
                return

            async with db_conn._async_session_factory() as db:
                profile = await _load_profile(db, user_id)
                profile_context = _build_profile_context(profile)

                synthesis = f"""请为以下学生生成「{chapter['name']}」章节的个性化学习资料。

【章节信息】
- 名称：{chapter['name']}
- 图标：{chapter.get('icon', '📌')}
- 难度：{'⭐' * chapter.get('difficulty', 3)}
- 描述：{chapter.get('description', '')}
- 知识点：{', '.join(kp['name'] for kp in chapter.get('knowledge_points', []))}
- 学习建议：{chapter.get('learning_tip', '')}
- 章节上下文：{chapter_context}

【{profile_context}】

请按照以下要求生成：
1. **个性化教学文档**：Markdown格式，包含概念讲解、公式推导
2. **针对性练习题**：3-5道题，包含选择题和简答题
"""

                logger.info(f"[Study] 开始生成 user_id={user_id} chapter={chapter_id}")

                # 阶段1: 意图识别
                yield f"data: {json.dumps({'type': 'agent_start', 'agent': 'intent_router', 'name': '意图识别', 'icon': '🎯', 'stage': 1, 'total': 5}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.2)
                intent_content = f"已识别学习意图：学习「{chapter['name']}」章节"
                yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'intent_router', 'content': intent_content}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'agent_complete', 'agent': 'intent_router', 'status': 'success', 'stage': 1}, ensure_ascii=False)}\n\n"

                # 阶段2: 画像Agent
                yield f"data: {json.dumps({'type': 'agent_start', 'agent': 'build_profile', 'name': '画像Agent', 'icon': '👤', 'stage': 2, 'total': 5}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.2)
                profile_summary = {}
                if profile:
                    profile_summary = {
                        "major": profile.get("major", "未知"),
                        "grade": profile.get("grade_level", "未知"),
                        "target": profile.get("target_role", "未知"),
                        "overall_score": profile.get("overall_score", 0),
                    }
                yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'build_profile', 'content': '画像数据已加载，个性化适配中...', 'profile_summary': profile_summary}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'agent_complete', 'agent': 'build_profile', 'status': 'success', 'stage': 2}, ensure_ascii=False)}\n\n"

                # 阶段3-4: 执行LangGraph工作流
                last_state = {}
                thread_id = f"study_{user_id}_{chapter_id}_{int(datetime.utcnow().timestamp())}"

                yield f"data: {json.dumps({'type': 'status', 'message': '正在启动AI生成...'}, ensure_ascii=False)}\n\n"

                async for event in execute_workflow(
                    user_query=synthesis,
                    chat_history=[],
                    thread_id=thread_id,
                    existing_profile=profile,
                    user_id=user_id
                ):
                    if not event:
                        continue
                    node_name = list(event.keys())[0]
                    node_output = event.get(node_name, {})
                    last_state = node_output

                    agent_info = AGENT_DISPLAY.get(node_name)
                    if not agent_info:
                        continue

                    stage = agent_info.get("stage", 0)
                    yield f"data: {json.dumps({'type': 'agent_start', 'agent': node_name, 'name': agent_info['name'], 'icon': agent_info['icon'], 'stage': stage, 'total': 5}, ensure_ascii=False)}\n\n"

                    await asyncio.sleep(0.1)

                    if isinstance(node_output, dict) and node_output:
                        content_parts = {}
                        if node_output.get("document"):
                            doc = node_output["document"]
                            content_parts["document"] = {
                                "title": doc.get("title", "") if isinstance(doc, dict) else "",
                                "content": doc.get("content", str(doc)[:3000]) if isinstance(doc, dict) else str(doc)[:3000],
                            }
                        if node_output.get("questions"):
                            qs = node_output["questions"]
                            content_parts["questions"] = qs if isinstance(qs, list) else [qs]

                        if content_parts:
                            yield f"data: {json.dumps({'type': 'agent_content', 'agent': node_name, 'content_parts': content_parts}, ensure_ascii=False)}\n\n"

                    yield f"data: {json.dumps({'type': 'agent_complete', 'agent': node_name, 'status': 'success', 'stage': stage}, ensure_ascii=False)}\n\n"

                # 最终总结
                summary_parts = []
                if last_state.get("document"):
                    summary_parts.append("📄 已生成个性化教学文档")
                if last_state.get("questions"):
                    qlen = len(last_state['questions']) if isinstance(last_state['questions'], list) else 1
                    summary_parts.append(f"✏️ 已生成{qlen}道练习题")

                summary_text = " | ".join(summary_parts) if summary_parts else "✅ 已完成"
                yield f"data: {json.dumps({'type': 'agent_content', 'agent': 'final_summary', 'content': summary_text, 'summary_parts': summary_parts}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'agent_complete', 'agent': 'final_summary', 'status': 'success', 'stage': 5}, ensure_ascii=False)}\n\n"

                await _save_resources(db, user_id, chapter_id, last_state)
                logger.info(f"[Study] 生成完成 user_id={user_id} chapter={chapter_id}")

                yield f"data: {json.dumps({'type': 'done', 'total_stages': 5, 'completed_stages': 5}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"[Study] 错误: {str(e)}", exc_info=True)
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


async def _save_resources(db, user_id: int, chapter_id: str, output: dict):
    resource_map = {
        "document": ("document", "title"),
        "questions": ("questions", "topic"),
    }

    saved_count = 0
    for key, (resource_type, title_field) in resource_map.items():
        data = output.get(key)
        if not data:
            continue
        title = data.get(title_field, "") if isinstance(data, dict) else key
        resource = LearningResource(
            user_id=user_id,
            session_id=f"study_{chapter_id}",
            resource_type=resource_type,
            title=str(title)[:300],
            content=data if isinstance(data, dict) else {"data": str(data)},
            topic=chapter_id
        )
        db.add(resource)
        saved_count += 1

    await db.commit()
    logger.info(f"[Study] 资源已保存 user_id={user_id} count={saved_count}")


@router.get("/chapter-info/{chapter_id}")
async def get_chapter_info(chapter_id: str):
    ch = KNOWLEDGE_GRAPH.get(chapter_id)
    if not ch:
        raise HTTPException(404, f"未知章节: {chapter_id}")
    return {"success": True, "data": ch}
