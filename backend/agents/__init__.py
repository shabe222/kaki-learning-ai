"""
8 个专业 Agent 共享基类 - 统一 __call__ 模板 + 统一 LLM 初始化/降级

约定：所有 Agent 必须实现 3 个方法：
- _extract_input(state, ctx)    从 state/ctx 抽取该 Agent 需要的输入
- _mock_generate(input_data)     走 MockLLMService 的业务逻辑
- _build_result(output_data, ctx)  把 _process 的输出组装成 LangGraph state 增量

【Day 6 重构 - 消除重复】
基类统一处理：
1. LLM 服务初始化（self.llm / self.mock_llm）— 子类不再需要 __init__
2. mock/real 切换 + 失败自动降级 — 基类提供 _process_with_fallback 模板方法
3. 降级统计与告警 — get_fallback_stats() 暴露给监控

子类用法：
1. 设置 AGENT_NAME
2. 实现 _extract_input / _mock_generate / _real_generate（如有 real 模式）
3. 可选：覆盖 _process 实现自定义降级策略（特殊情况如 video_agent）

共享上下文池：
- 子类可直接访问 self.ctx（SharedContext 实例）
- self.ctx.get('document')  读上游产出
- self.ctx.set('questions', [...])  写自己的产出
- self.set_ctx(ctx)        由 router 显式注入

向后兼容：
- _extract_input / _mock_generate / _build_result 仍接受原有签名
- 旧版子类若自定义 __init__ 初始化 llm/mock_llm 仍能工作（基类会跳过）
- 旧版子类若自定义 _process 实现自定义降级逻辑仍能工作
"""
import asyncio
import time
import logging
from typing import Dict, Any, Optional

from graph.state import AgentState
from agents.shared_context import SharedContext

logger = logging.getLogger(__name__)


# ===== 同步 state 字段到 ctx 的字段白名单 =====
_CTX_SYNC_FIELDS = (
    "student_profile", "profile_confidence",
    "document", "mindmap", "questions", "code_examples",
    "learning_path", "video_result", "tutor_response",
    "user_query", "user_intent",
    "errors", "execution_log", "processing_time", "current_agent",
)


class BaseAgent:
    """Agent 抽象基类 - 统一 __call__ 行为 + 共享上下文池 + 统一 LLM 降级"""

    AGENT_NAME: str = ""
    # 类级默认 ctx（子类 __init__ 可覆盖为实例属性；router 通过 set_ctx 注入）
    ctx: Optional[SharedContext] = None

    # ===== LLM 服务（由基类统一初始化；子类可直接使用 self.llm / self.mock_llm）=====
    llm: Any = None
    mock_llm: Any = None

    # ===== 降级统计（供监控/告警使用）=====
    _fallback_count: int = 0
    _last_fallback_time: Optional[float] = None
    # 降级频率告警阈值（子类可覆盖）
    FALLBACK_ALERT_THRESHOLD: int = 10

    def __init__(self):
        """基类统一初始化 LLM 服务 — 子类无需重写 __init__"""
        # 仅当子类未自定义时，才由基类初始化（向后兼容）
        if self.llm is None:
            from services.llm_service import LLMService
            from config import settings
            self.llm = LLMService(provider=settings.LLM_PROVIDER)
        if self.mock_llm is None:
            from services.mock_llm_service import MockLLMService
            self.mock_llm = MockLLMService()
        logger.info(
            f"[{self.AGENT_NAME}] 初始化完成，LLM provider={self.llm.provider.value}"
        )

    # ===== 降级统计 API（供监控/测试使用）=====
    def get_fallback_stats(self) -> Dict[str, Any]:
        """获取降级统计信息"""
        return {
            "agent_name": self.AGENT_NAME,
            "fallback_count": self._fallback_count,
            "last_fallback_time": self._last_fallback_time,
            "alert_threshold": self.FALLBACK_ALERT_THRESHOLD,
            "alert_triggered": self._fallback_count >= self.FALLBACK_ALERT_THRESHOLD,
        }

    def reset_fallback_stats(self) -> None:
        """重置降级统计（测试/运维用）"""
        self._fallback_count = 0
        self._last_fallback_time = None

    # ===== 统一降级处理模板（子类默认使用 _mock_generate + _real_generate）=====
    async def _process_with_fallback(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        统一的 mock/real 切换 + 失败降级模板方法。

        子类 _process 默认实现：直接 return await self._process_with_fallback(input_data)
        子类只需实现 _mock_generate（必须）和 _real_generate（可选，无 real 模式时跳过）
        """
        from config import settings
        from services.llm_service import LLMProvider

        # mock 模式：直接走 mock
        if settings.LLM_PROVIDER == LLMProvider.MOCK.value:
            return await self._mock_generate(input_data)

        # real 模式：子类需实现 _real_generate，否则降级到 mock
        real_fn = getattr(self, "_real_generate", None)
        real_not_implemented = type(self)._real_generate is BaseAgent._real_generate
        if real_fn is None or real_not_implemented:
            if settings.MODE == "production":
                raise NotImplementedError(
                    f"[{self.AGENT_NAME}] production requires a real LLM implementation"
                )
            logger.warning(
                f"[{self.AGENT_NAME}] LLM real 模式未实现 _real_generate，"
                f"降级 mock"
            )
            self._record_fallback(reason="no_real_generate")
            return await self._mock_generate(input_data)

        # 尝试 real 模式，失败降级 mock
        try:
            return await real_fn(input_data)
        except (asyncio.TimeoutError, Exception) as e:
            if settings.MODE == "production":
                raise
            self._record_fallback(reason=f"{type(e).__name__}: {e}")
            return await self._mock_generate(input_data)

    def _record_fallback(self, reason: str = "") -> None:
        """记录一次降级（含告警判断）"""
        self._fallback_count += 1
        self._last_fallback_time = time.time()
        logger.warning(
            f"[{self.AGENT_NAME}] LLM real 模式失败，降级 mock: "
            f"{reason} (累计降级: {self._fallback_count}次)"
        )
        # 降级频率过高时告警
        if self._fallback_count == self.FALLBACK_ALERT_THRESHOLD:
            logger.error(
                f"[{self.AGENT_NAME}] 降级次数达到阈值 {self.FALLBACK_ALERT_THRESHOLD}，"
                f"请检查 LLM 服务状态！"
            )
        elif self._fallback_count > self.FALLBACK_ALERT_THRESHOLD and \
                self._fallback_count % self.FALLBACK_ALERT_THRESHOLD == 0:
            logger.error(
                f"[{self.AGENT_NAME}] 持续高频降级，累计 {self._fallback_count}次，"
                f"请立即排查 LLM 服务"
            )

    # ===== ctx 注入 API =====
    def set_ctx(self, ctx: SharedContext) -> "BaseAgent":
        """注入共享上下文 - 返回 self 支持链式调用"""
        self.ctx = ctx
        return self

    def get(self, key: str, default: Any = None) -> Any:
        """读 ctx - self.get('document')"""
        if self.ctx is None:
            return default
        return self.ctx.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """写 ctx - self.set('questions', [...])"""
        if self.ctx is None:
            raise RuntimeError(
                f"[{self.AGENT_NAME}] ctx 未初始化，"
                f"请先调用 set_ctx() 或确保 __call__ 中自动构造"
            )
        self.ctx.set(key, value)

    # ===== ctx 自动同步 =====
    def _ensure_ctx(self, state: AgentState) -> SharedContext:
        """确保 ctx 存在 + 从 state 同步最新值"""
        user_id = state.get("user_id", 0)
        try:
            user_id_int = int(user_id) if user_id else 0
        except (TypeError, ValueError):
            user_id_int = 0

        if self.ctx is None:
            # 首次调用：从 state 构造
            self.ctx = SharedContext.from_agent_state(state, user_id_int)
            logger.debug(f"[{self.AGENT_NAME}] ctx 自动构造 user_id={user_id_int}")
        else:
            # 已有 ctx：从 state 同步最新值（LangGraph 合并后）
            for k in _CTX_SYNC_FIELDS:
                if k in state and state[k] is not None:
                    if not self.ctx.has(k):
                        self.ctx.set(k, state[k])
                    else:
                        # 已有值时，以 state 为准（更权威）
                        self.ctx.set(k, state[k])

        # 同步基础字段
        if "user_query" in state and state["user_query"]:
            self.ctx.user_query = state["user_query"]
        if "user_intent" in state and state["user_intent"]:
            self.ctx.user_intent = state["user_intent"]

        return self.ctx

    # ===== 主入口 =====
    async def __call__(self, state: AgentState) -> Dict[str, Any]:
        start_time = time.time()
        ctx = self._ensure_ctx(state)
        try:
            logger.info(f"[{self.AGENT_NAME}] 开始处理...")

            input_data = self._extract_input(state)
            # 统一处理 style 参数：注入到 topic 中作为显式格式指令
            style = input_data.get("style") or state.get("style", "")
            if style and isinstance(style, str) and len(style) > 2:
                orig_topic = input_data.get("topic", "") or input_data.get("user_query", "")
                if orig_topic:
                    input_data["topic"] = orig_topic + "\n\n【输出要求】" + style
            output_data = await self._process(input_data)
            result = self._build_result(output_data)

            execution_time = time.time() - start_time
            log_entry = {
                "agent_name": self.AGENT_NAME,
                "start_time": start_time,
                "end_time": time.time(),
                "status": "success",
                "input_summary": str(input_data)[:100],
                "output_preview": str(result)[:200],
                "processing_time": execution_time
            }

            logger.info(f"[{self.AGENT_NAME}] 处理完成，耗时 {execution_time:.2f}s")

            return {
                **result,
                "current_agent": self.AGENT_NAME,
                "execution_log": state.get("execution_log", []) + [log_entry],
                "processing_time": {
                    **(state.get("processing_time", {})),
                    self.AGENT_NAME: execution_time
                }
            }
        except Exception as e:
            error_msg = str(e) or "未知错误（可能是API响应为空或JSON解析失败）"
            logger.error(f"[{self.AGENT_NAME}] 处理失败: {error_msg}")
            return {
                "errors": state.get("errors", []) + [f"[{self.AGENT_NAME}]: {str(e)}"],
                "current_agent": self.AGENT_NAME,
                "execution_log": state.get("execution_log", []) + [{
                    "agent_name": self.AGENT_NAME,
                    "status": "error",
                    "error_message": str(e)
                }]
            }

    # ===== 默认 _process 模板（子类可覆盖以自定义降级策略）=====
    async def _process(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        默认 _process 实现 — 委托给 _process_with_fallback。

        子类若无特殊需求，无需重写 _process，只需实现：
        - _mock_generate（必须）
        - _real_generate（可选，无 real 模式时跳过）
        """
        return await self._process_with_fallback(input_data)

    # ===== 子类必须实现的方法 =====
    def _extract_input(self, state: AgentState) -> Dict[str, Any]:
        raise NotImplementedError

    def _build_result(self, output_data: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def _mock_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """子类必须实现 — mock 模式下的业务逻辑"""
        raise NotImplementedError

    async def _real_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """子类可选实现 — real 模式下的业务逻辑（不实现则降级 mock）"""
        raise NotImplementedError


# ============= Agent 类 re-export，保持向后兼容 =============

from agents.code_agent import CodeAgent  # noqa: E402
from agents.document_agent import DocumentAgent  # noqa: E402
from agents.mindmap_agent import MindmapAgent  # noqa: E402
from agents.path_agent import PathAgent  # noqa: E402
from agents.profile_agent import ProfileAgent  # noqa: E402
from agents.question_agent import QuestionAgent  # noqa: E402
from agents.tutor_agent import TutorAgent  # noqa: E402
from agents.video_agent import VideoAgent  # noqa: E402
from agents.shared_context import (  # noqa: E402
    SharedContext,
    AGENT_REGISTRY,
    AGENT_WRITES,
    register_agent,
)

__all__ = [
    "BaseAgent",
    "SharedContext",
    "AGENT_REGISTRY",
    "AGENT_WRITES",
    "register_agent",
    "CodeAgent",
    "DocumentAgent",
    "MindmapAgent",
    "PathAgent",
    "ProfileAgent",
    "QuestionAgent",
    "TutorAgent",
    "VideoAgent",
]


# ============= 注册 8 Agent 到 AGENT_REGISTRY =============
# router 用 AGENT_REGISTRY[short_name] 动态实例化
register_agent("profile",  ProfileAgent)
register_agent("document", DocumentAgent)
register_agent("question", QuestionAgent)
register_agent("mindmap",  MindmapAgent)
register_agent("code",     CodeAgent)
register_agent("path",     PathAgent)
register_agent("tutor",    TutorAgent)
register_agent("video",    VideoAgent)
