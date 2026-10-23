"""
视频生成服务 - OpenMAIC Provider（Day 1-2 实现，Day 3 起被新 LLM 抽象取代）

Provider: OpenMAIC（基于大模型的交互式教学动画生成）
调用模式: 异步任务 —— 提交 → 轮询 → 获取HTML → 转存 OSS

⚠️ Day 3 之后：
- 路由层（routers/video.py）已切换到 services.video_service.VideoService（LLM 抽象）
- 本文件保留为 OpenMAIC Provider 旧实现，供 backend/agents/video_agent.py 调用
- 真实 LLM Provider（DeepSeek / 讯飞星火 / 通义千问）尚未实现，由队友 PR 接入
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional
import httpx

from config import settings
from services.oss_service import upload_text, get_presigned_url

logger = logging.getLogger(__name__)

DEFAULT_OPENMAIC_URL = "http://localhost:3001"
POLL_INTERVAL = 5
MAX_POLL_RETRIES = 60


async def test_connectivity() -> tuple[bool, str]:
    """测试 OpenMAIC 连通性"""
    base_url = settings.OPENMAIC_BASE_URL or DEFAULT_OPENMAIC_URL
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(f"{base_url}/api/generate-classroom/health-check")
            if resp.status_code == 404:
                resp2 = await client.get(f"{base_url}")
                if resp2.status_code == 200:
                    return True, "OpenMAIC 连接成功"
            if resp.status_code == 200:
                return True, "OpenMAIC 连接成功"
            return False, f"OpenMAIC 不可达 ({resp.status_code})"
    except Exception as e:
        return False, f"OpenMAIC 连接异常: {str(e)}"


async def submit_openmaic_job(topic: str) -> str:
    """提交 OpenMAIC 课堂生成任务，返回 jobId"""
    base_url = settings.OPENMAIC_BASE_URL or DEFAULT_OPENMAIC_URL

    body = {
        "requirement": topic,
        "enableVideoGeneration": False,
        "enableImageGeneration": True,
        "enableTTS": False,
    }

    logger.info(f"[OpenMAIC] 提交任务: topic={topic[:60]}...")

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(
            f"{base_url}/api/generate-classroom",
            json=body,
        )

        if resp.status_code not in (200, 202):
            text = resp.text[:500]
            raise RuntimeError(f"OpenMAIC 任务提交失败 ({resp.status_code}): {text}")

        data = resp.json()
        job_id = data.get("data", {}).get("jobId", "")
        if not job_id:
            raise RuntimeError("OpenMAIC 返回了空 jobId")

        logger.info(f"[OpenMAIC] 任务已提交: jobId={job_id}")
        return job_id


async def poll_openmaic_job(job_id: str) -> Optional[dict]:
    """轮询 OpenMAIC 任务，完成时返回结果"""
    base_url = settings.OPENMAIC_BASE_URL or DEFAULT_OPENMAIC_URL

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{base_url}/api/generate-classroom/{job_id}",
        )

        if resp.status_code != 200:
            text = resp.text[:500]
            raise RuntimeError(f"OpenMAIC 轮询失败 ({resp.status_code}): {text}")

        data = resp.json()
        job_data = data.get("data", {})
        status = job_data.get("status", "")

        if status == "succeeded":
            result = job_data.get("result", {})
            logger.info(f"[OpenMAIC] 课堂生成成功: jobId={job_id}")
            return {
                "classroom": result,
                "scenes": job_data.get("scenesGenerated", 0),
            }

        if status == "failed":
            error_msg = job_data.get("error", "未知错误")
            raise RuntimeError(f"OpenMAIC 生成失败: {error_msg}")

        return None


async def generate_video(prompt: str, user_id: int = 0,
                         duration: int = 5, ratio: str = "16:9",
                         resolution: str = "720p") -> dict:
    """完整视频生成流程: 提交 OpenMAIC → 轮询 → 提取HTML → 存入 OSS → 返回访问地址"""
    job_id = await submit_openmaic_job(prompt)

    for attempt in range(1, MAX_POLL_RETRIES + 1):
        await asyncio.sleep(POLL_INTERVAL)
        result = await poll_openmaic_job(job_id)
        if result:
            break
        if attempt % 6 == 0:
            logger.info(f"[OpenMAIC] 轮询中... ({attempt * POLL_INTERVAL}s, jobId={job_id})")
    else:
        raise RuntimeError(
            f"OpenMAIC 生成超时 ({MAX_POLL_RETRIES * POLL_INTERVAL}s)"
        )

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_prompt = prompt[:30].replace(" ", "_").replace("/", "_")
    object_name = f"user_{user_id}/{timestamp}_{safe_prompt}.html"

    classroom_data = result.get("classroom", {})
    html_content = _classroom_to_html(classroom_data, prompt)

    oss_path = await upload_text("videos", object_name, html_content,
                                 content_type="text/html")
    if not oss_path:
        logger.warning("[OpenMAIC] OSS 转存失败")
        return {
            "url": "",
            "duration": 0,
            "ratio": ratio,
            "resolution": resolution,
            "stored_in_oss": False,
            "html_content": html_content,
        }

    presigned_url = await get_presigned_url("videos", object_name,
                                            expires_hours=72)

    logger.info(f"[OpenMAIC] HTML已存入 OSS: {object_name}")

    return {
        "url": presigned_url,
        "object_name": object_name,
        "duration": 0,
        "ratio": ratio,
        "resolution": resolution,
        "stored_in_oss": True,
    }


def _classroom_to_html(classroom_data: dict, title: str) -> str:
    """将 OpenMAIC 课堂数据转为独立 HTML 页面"""
    scenes = classroom_data.get("scenes", [])
    title_text = classroom_data.get("title", title)

    slides_html = ""
    for scene in scenes:
        scene_type = scene.get("type", "slide")
        content = scene.get("content", "")

        if scene_type == "slide":
            slides_html += f'<section class="slide"><div class="slide-content">{content}</div></section>\n'
        elif scene_type == "quiz":
            slides_html += f'<section class="quiz"><div class="quiz-content">{content}</div></section>\n'
        elif scene_type == "interactive":
            slides_html += f'<section class="interactive"><div class="interactive-content">{content}</div></section>\n'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title_text}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
    <script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
    <style>
        body {{ font-family: 'Noto Sans SC', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 2rem; }}
        .slide {{ background: white; border-radius: 16px; padding: 2rem; margin: 1rem 0; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
        .slide-content {{ font-size: 1.1rem; line-height: 1.8; }}
        .quiz {{ background: #f0f9ff; border-radius: 16px; padding: 2rem; margin: 1rem 0; border-left: 4px solid #6366f1; }}
        .interactive {{ background: #f0fdf4; border-radius: 16px; padding: 2rem; margin: 1rem 0; border-left: 4px solid #10b981; }}
        h1 {{ color: white; text-align: center; font-size: 2rem; margin-bottom: 2rem; }}
    </style>
</head>
<body>
    <div class="max-w-4xl mx-auto">
        <h1>{title_text}</h1>
        {slides_html}
    </div>
    <script>
        renderMathInElement(document.body, {{ delimiters: [{{left: '$$', right: '$$', display: true}}, {{left: '$', right: '$', display: false}}], throwOnError: false }});
    </script>
</body>
</html>"""


async def generate_video_script(topic: str) -> dict:
    """降级模式：LLM 生成视频分镜脚本（不调用 Seedance）"""
    from services.llm_service import get_llm_service

    llm = await get_llm_service()
    prompt = (
        f"为机器学习知识点「{topic}」生成一段教学视频的分镜脚本。\n"
        f"格式要求：\n"
        f"1. 标题：一个吸引人的视频标题\n"
        f"2. 分镜：3-4个镜头，每个镜头描述画面内容和旁白文字\n"
        f"3. 时长估算：每个镜头约多少秒\n"
        f"4. 总结：一句话概括视频核心知识点\n"
        f"用 Markdown 格式输出，适合直接展示给大学生。"
    )

    result = await llm.chat([{
        "role": "user",
        "content": prompt,
    }])

    script = result.get("content", "")

    return {
        "url": "",
        "duration": 0,
        "ratio": "16:9",
        "resolution": "text",
        "stored_in_oss": False,
        "script": script,
        "is_fallback": True,
    }


async def generate_lecture_script(topic: str, profile: dict = None) -> dict:
    """完整课件模式：LLM 生成多章节教学课件脚本（适合替代 PPT 讲解）"""
    from services.llm_service import get_llm_service

    llm = await get_llm_service()

    student_info = ""
    if profile:
        major = profile.get("major", "")
        grade = profile.get("grade_level", "")
        student_info = f"面向{grade}{major}专业学生。根据学生水平调整难度。"

    prompt = (
        f"为机器学习知识点「{topic}」生成一份完整教学课件脚本。{student_info}\n\n"
        f"结构要求：\n"
        f"# 课件标题（吸引人的标题）\n"
        f"## 一、导入（2-3分钟）\n"
        f"- 情景引入：用生活案例引出知识点\n"
        f"- 问题提出：为什么要学这个\n"
        f"## 二、核心讲解（8-10分钟）\n"
        f"- 概念定义：清晰定义核心概念\n"
        f"- 原理推导：分步骤讲解推导过程\n"
        f"- 关键公式：列出核心公式并解释每个符号\n"
        f"- 算法流程：用步骤图描述算法\n"
        f"## 三、案例演示（3-5分钟）\n"
        f"- 给出一个完整的手工计算案例\n"
        f"- 展示Python代码实现\n"
        f"## 四、知识拓展（2-3分钟）\n"
        f"- 常见的变体/改进方法\n"
        f"- 实际应用场景\n"
        f"## 五、课堂小结（1-2分钟）\n"
        f"- 核心要点回顾（3-5条）\n"
        f"- 课后思考题（1-2道）\n\n"
        f"用 Markdown 格式输出，配丰富的表格和代码块。适合直接在大屏幕上展示。总时长约15-25分钟。"
    )

    result = await llm.chat([{
        "role": "user",
        "content": prompt,
    }])

    script = result.get("content", "")

    return {
        "url": "",
        "duration": 0,
        "ratio": "16:9",
        "resolution": "text",
        "stored_in_oss": False,
        "script": script,
        "is_fallback": True,
        "is_lecture": True,
    }
