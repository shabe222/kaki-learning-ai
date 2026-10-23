"""
视频生成服务 - LLM Provider 抽象层（Day 3 接口契约实现 → Day 6 重构版）

本文件是 Day 3 暴露的"视频生成 LLM 接口契约"的实现入口。
Day 6 重构：集成 LLMService 统一的 LLMProvider 枚举，消除重复的 Provider 定义和验证逻辑。
- 接口契约详见：docs/视频生成-LLM-接口契约.md
- 当前 Provider: mock（占位实现）
- 待接入 Provider: deepseek / spark(讯飞星火) / qwen(通义千问)

⚠️ Day 3 范围：仅暴露接口契约 + mock，真实 LLM 调用留给队友 PR
⚠️ 历史兼容：OpenMAIC 旧实现已迁移到 services.video_service_openmaic
"""

import asyncio
import logging
import time
import uuid
from typing import Dict, Any, Optional

from config import settings
from services.llm_service import LLMProvider

logger = logging.getLogger(__name__)

# 同步/异步分流阈值（≤ 60s 走同步通道，> 60s 走异步通道）
SYNC_DURATION_THRESHOLD = 60


class VideoService:
    """视频生成 LLM 抽象服务（Day 3 暴露层）

    用法：
        from backend.services.video_service import video_service
        result = await video_service.generate("操作系统进程管理")
    """

    def __init__(self, llm_provider: str = "mock"):
        # 统一使用 LLMProvider 枚举验证（复用 LLMService 的白名单定义）
        valid = [p.value for p in LLMProvider]
        if llm_provider not in valid:
            logger.warning(
                f"[VideoService] 未知 provider '{llm_provider}'，回退到 mock"
            )
            llm_provider = "mock"
        self.llm_provider: str = llm_provider
        # 异步任务注册表：job_id -> job_meta
        self.jobs: Dict[str, Dict[str, Any]] = {}

    async def generate(
        self,
        topic: str,
        profile: Optional[Dict[str, Any]] = None,
        duration_sec: int = 60,
        user_id: int = 0,
    ) -> Dict[str, Any]:
        """同步生成视频（小视频，duration_sec ≤ 60）

        返回字段（与契约 GenerateResponse 一致）：
            - video_url: str  视频访问地址（mock 为 placeholder）
            - transcript: str 视频讲稿文字
            - generation_ms: int 生成耗时（毫秒）
            - llm_provider: str 实际使用的 Provider（mock/deepseek/spark/qwen）
        """
        if not topic or not topic.strip():
            raise ValueError("topic 不能为空")
        if duration_sec < 15 or duration_sec > 600:
            raise ValueError("duration_sec 必须在 [15, 600] 区间")

        profile = profile or {}
        start = time.time()

        if self.llm_provider == LLMProvider.MOCK.value:
            # mock 模式：返回占位 URL，模拟 0.5s 网络延迟
            await asyncio.sleep(0.5)
            video_id = uuid.uuid4().hex[:8]
            grade = profile.get("grade") or profile.get("grade_level") or "未知"
            target_role = (
                profile.get("target_role")
                or profile.get("major")
                or "未知"
            )
            return {
                "video_url": f"https://placeholder.local/videos/{video_id}.mp4",
                "transcript": (
                    f"[MOCK] 这是关于「{topic}」的 {duration_sec} 秒讲解视频。"
                    f"适配用户：{grade} / {target_role}。"
                ),
                "generation_ms": int((time.time() - start) * 1000),
                "llm_provider": "mock",
            }

        if not getattr(settings, "OPENMAIC_ENABLED", False):
            raise NotImplementedError("OpenMAIC video generation is disabled")

        from services.video_search_client import generate_lecture_script_md
        from services.video_service_openmaic import generate_video as generate_openmaic_video

        lecture = await generate_lecture_script_md(topic, profile)
        transcript = (lecture.get("script") or "").strip()
        scene_prompt = transcript or topic
        video_data = await generate_openmaic_video(
            prompt=scene_prompt,
            user_id=user_id,
            duration=max(5, min(duration_sec, 600)),
            ratio="16:9",
            resolution="720p",
        )
        return {
            "video_url": video_data.get("url") or "",
            "transcript": transcript,
            "video_result": video_data,
            "generation_ms": int((time.time() - start) * 1000),
            "llm_provider": self.llm_provider,
        }

    async def _fallback_mock(
        self, topic: str, duration_sec: int, profile: dict, start: float
    ) -> dict:
        """real provider 不可用时的降级实现（向后兼容 router 直接调用）"""
        logger.warning(
            f"[VideoService] Real LLM provider '{self.llm_provider}' 尚未实现，"
            f"自动降级 mock（保证演示可走通）"
        )
        await asyncio.sleep(0.3)
        video_id = uuid.uuid4().hex[:8]
        grade = profile.get("grade") or profile.get("grade_level") or "未知"
        target_role = (
            profile.get("target_role")
            or profile.get("major")
            or "未知"
        )
        return {
            "video_url": f"https://placeholder.local/videos/fallback-{video_id}.mp4",
            "transcript": (
                f"[MOCK-FALLBACK] 这是关于「{topic}」的 {duration_sec} 秒讲解视频。"
                f"适配用户：{grade} / {target_role}。"
                f"\n（real LLM 尚未接入，自动降级 mock）"
            ),
            "generation_ms": int((time.time() - start) * 1000),
            "llm_provider": "mock",
        }

    async def submit_async(
        self,
        topic: str,
        profile: Optional[Dict[str, Any]] = None,
        duration_sec: int = 60,
    ) -> Dict[str, Any]:
        """异步提交视频生成任务（duration_sec > 60 的长视频）

        返回字段（与契约 202 响应一致）：
            - job_id: str  任务 ID（用于 status 查询）
            - status: str  任务状态（pending / running / succeeded / failed）
        """
        if not topic or not topic.strip():
            raise ValueError("topic 不能为空")

        profile = profile or {}
        job_id = uuid.uuid4().hex[:12]
        self.jobs[job_id] = {
            "status": "pending",
            "topic": topic,
            "profile": profile,
            "duration_sec": duration_sec,
            "created_at": time.time(),
            "llm_provider": self.llm_provider,
        }
        return {"job_id": job_id, "status": "pending"}

    async def get_status(self, job_id: str) -> Dict[str, Any]:
        """查询异步任务状态（与契约 /api/video/status/{job_id} 一致）

        状态机：
            pending  -> 任务已入队
            running  -> 正在生成
            succeeded -> 生成完成（含 video_url / transcript）
            failed   -> 生成失败（含 error 字段）
            not_found -> 任务不存在（契约 404）
        """
        if job_id not in self.jobs:
            return {"status": "not_found", "job_id": job_id}
        return self.jobs[job_id]


# 模块级单例（默认 mock Provider，配置从 config.LLM_PROVIDER 读取）
def _default_provider() -> str:
    """延迟读取 config.settings.LLM_PROVIDER，复用 LLMProvider 白名单验证"""
    try:
        from config import settings
        provider = getattr(settings, "LLM_PROVIDER", "mock") or "mock"
        # 统一使用 LLMProvider 枚举验证
        valid = [p.value for p in LLMProvider]
        if provider not in valid:
            return "mock"
        return provider
    except Exception:
        return "mock"


video_service = VideoService(llm_provider=_default_provider())
