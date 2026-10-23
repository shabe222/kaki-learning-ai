"""
VideoAgent - Day 6 重构版（保留自定义多层降级逻辑）

【特殊说明】video_agent 的降级链比一般 Agent 复杂：
- mock LLM + OpenMAIC 未启用 → _process_fallback
- real LLM/OpenMAIC 失败 → _process_fallback
- _process_fallback 失败 → _final_fallback（零 LLM / 零网络）

因此本 Agent 覆盖基类的 _process，实现自定义三层降级链。
基类已统一处理：
- LLM 服务初始化（self.llm / self.mock_llm）
- 降级统计与告警（通过 _record_fallback 复用）

═══════════ 接口契约（与 routers/agents_day4.py 对齐）══════════
- 输出字段：video_result (Dict), video_skipped (bool)
  - video_result: {url, duration, ratio, resolution, object_name, stored_in_oss, provider}
══════════════════════════════════════════════════════════
"""
import asyncio
import logging
from typing import Dict, Any

from graph.state import AgentState
from config import settings
from services.llm_service import LLMProvider
from agents import BaseAgent

logger = logging.getLogger("rjianb.video")


def _safe_int(value, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class VideoAgent(BaseAgent):
    """视频生成 Agent（Day 6: 自定义三层降级链，基类已统一初始化）"""

    AGENT_NAME = "generate_video"

    # ============= 模板方法 =============

    def _extract_input(self, state: AgentState) -> Dict[str, Any]:
        profile = state.get("student_profile") or {}
        return {
            "topic": state.get("user_query", ""),
            "profile": profile,
            "document": state.get("document"),
            "mindmap": state.get("mindmap"),
            "user_id": _safe_int(state.get("user_id"), 0),
            "mode": state.get("mode", "both"),
        }

    # 【Day 6 重构】覆盖 _process 实现自定义三层降级链
    async def _process(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """OpenMAIC 不可用 → 直接走 DeepSeek 课堂脚本生成"""
        try:
            return await self._process_fallback(input_data)
        except (asyncio.TimeoutError, Exception) as e:
            if settings.MODE == "production":
                raise
            self._record_fallback(reason=f"课堂脚本也失败 {type(e).__name__}: {e}")
            return self._final_fallback(input_data["topic"])

    def _build_result(self, output_data: Dict[str, Any]) -> Dict[str, Any]:
        if output_data.get("skipped"):
            return {
                "video_result": None,
                "video_skipped": True,
            }

        video_data = output_data.get("video_result", {})
        return {
            "video_result": {
                "url": video_data.get("url", ""),
                "duration": video_data.get("duration", 0),
                "ratio": video_data.get("ratio", "16:9"),
                "resolution": video_data.get("resolution", "720p"),
                "object_name": video_data.get("object_name", ""),
                "stored_in_oss": video_data.get("stored_in_oss", False),
                "provider": output_data.get("provider", ""),
                # 修复 Q5：fallback 路径下，video_data 含 script / transcript
                # 之前被 _build_result 漏掉，导致用户看不到讲义
                "script": video_data.get("script", ""),
                "transcript": video_data.get("transcript", ""),
                "slides": video_data.get("slides", None),
            },
            "video_skipped": False,
        }

    # ============= 主路径 =============

    async def _process_main(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        profile = input_data["profile"] or {}
        topic = input_data["topic"]

        # 生成 scene_prompt（mock/real 都会被外层 catch）
        scene_prompt = await self._build_scene_prompt(topic, profile)

        from services.video_service_openmaic import generate_video

        user_id = input_data.get("user_id") or \
                  (input_data.get("profile") or {}).get("user_id", 0)

        logger.info(
            f"[{self.AGENT_NAME}] 调用 OpenMAIC 生成教学动画: "
            f"topic={topic[:40]}, prompt={scene_prompt[:60]}..."
        )

        video_data = await generate_video(
            prompt=scene_prompt,
            user_id=user_id,
            duration=5,
            ratio="16:9",
            resolution="720p",
        )

        return {
            "video_result": video_data,
            "skipped": False,
            "provider": "openmaic",
        }

    # ============= 降级路径 =============

    async def _process_fallback(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        topic = input_data["topic"]
        profile = input_data["profile"]
        mode = input_data.get("mode", "both")

        from services.video_search_client import generate_classroom_script, generate_lecture_script_md
        try:
            if mode == "classroom":
                script_data = await generate_classroom_script(topic, profile)
            else:
                script_data = await generate_lecture_script_md(topic, profile)
        except Exception as e:
            if settings.MODE == "production":
                raise
            self._record_fallback(reason=f"讲稿生成失败 {type(e).__name__}: {e}")
            return self._final_fallback(topic)

        if isinstance(script_data, dict) and not script_data.get("transcript"):
            script_data["transcript"] = script_data.get("script", "")

        return {
            "video_result": script_data,
            "skipped": False,
            "provider": script_data.get("provider", "script_fallback") if isinstance(script_data, dict) else "script_fallback",
        }

    def _final_fallback(self, topic: str) -> Dict[str, Any]:
        """所有路径都失败时的最末兜底（零 LLM / 零网络）"""
        return {
            "video_result": {
                "url": "",
                "duration": 0,
                "ratio": "16:9",
                "resolution": "720p",
                "object_name": "",
                "stored_in_oss": False,
                "transcript": (
                    f"# {topic} 教学讲义（占位）\n\n"
                    f"由于视频生成服务暂不可用，这里提供讲义占位。\n"
                    f"完整内容请参见关联的 document / mindmap 资源。\n"
                ),
                "provider": "final_fallback",
            },
            "skipped": False,
            "provider": "final_fallback",
        }

    # ============= scene_prompt 生成 =============

    async def _build_scene_prompt(self, topic: str, profile: dict) -> str:
        """
        生成 scene_prompt。Day 6 强化：
        - LLM_PROVIDER=mock → 走 mock LLM
        - LLM_PROVIDER=real → 走 self.llm.generate（失败由外层 _process 捕获）
        """
        grade = profile.get("grade_level", "大学生")
        major = profile.get("major", "计算机")

        if settings.LLM_PROVIDER == LLMProvider.MOCK.value:
            llm = self.mock_llm
            prompt = f"""为机器学习知识点「{topic}」生成一段详细的课堂教学描述，用于AI生成教学动画。

学生信息: {major}专业 {grade}

要求:
1. 描述这个知识点的核心概念和要点
2. 包含具体的数据、公式、或代码示例
3. 适合大学生的理解水平
4. 200-400字

直接输出教学内容，不要标记。"""
            result = await llm.chat([{"role": "user", "content": prompt}])
            scene_prompt = result.get("content", topic).strip()
        else:
            prompt = f"""为「{topic}」生成 200-400 字的课堂教学描述。
学生：{major}专业 {grade}。直接输出正文。"""
            try:
                response_text = await self.llm.generate(
                    prompt=prompt,
                    temperature=0.4,
                    max_tokens=1500,
                    timeout=self.llm.timeout_sec,
                )
                scene_prompt = self.llm._validate_response(response_text)
            except Exception as e:
                if settings.MODE == "production":
                    raise
                # 记录降级原因（不抛异常，让外层 _process 继续走 fallback 链）
                self._record_fallback(reason=f"real LLM scene_prompt 失败 {type(e).__name__}: {e}")
                scene_prompt = ""

        if len(scene_prompt) < 20:
            scene_prompt = (
                f"详细讲解机器学习知识点「{topic}」，包括核心概念、"
                f"数学原理、实际应用，面向{grade}{major}专业学生。"
            )
        return scene_prompt
