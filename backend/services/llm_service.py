"""
LLMService - 统一 LLM Provider 抽象（Day 4 → Day 5 强化版 → Day 6 Provider 重构版）

═══════════════════ Day 6 重构点 ═══════════════════
- 引入 BaseProvider 抽象基类，使用模板方法模式统一 HTTP 调用流程
- DeepSeekProvider / SparkProvider / QwenProvider 仅实现配置差异（各 ~15 行）
- 消除约 230 行重复代码，新增 Provider 成本从 70 行降至 20 行
═══════════════════ Day 5 强化点 ═══════════════════
- 超时配置：settings.LLM_TIMEOUT_SEC（默认 30s），LLMService.generate 强制兜底
- mock 模式：返回与 real 完全同 schema（保证降级时调用方无感）
- _count_tokens / _validate_response 辅助
═══════════════════
"""
import json
import logging
import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional, Dict, Any, List, AsyncGenerator

try:
    import aiohttp  # 真实 LLM HTTP 调用
except ImportError:  # 极端环境兜底
    aiohttp = None  # type: ignore

from config import settings

logger = logging.getLogger(__name__)

# 全局单例 + 降级状态
_llm_service = None
_llm_mode = "unknown"          # mock / production / degraded
_llm_last_error: Optional[str] = None


# ===== Provider 白名单 =====

class LLMProvider(str, Enum):
    """
    LLM Provider 白名单
    - mock: 本地 mock（仅显式配置时可用；生产模式被 validate_production_secrets 拒绝）
    - deepseek: 火山方舟 / DeepSeek 直连
    - spark: 讯飞星火
    - qwen: 阿里云通义千问
    - openai_compatible: 任意 OpenAI 兼容端点（OPENAI_COMPATIBLE_* 配置）
    """
    MOCK = "mock"
    DEEPSEEK = "deepseek"
    SPARK = "spark"
    QWEN = "qwen"
    OPENAI_COMPATIBLE = "openai_compatible"


# ===== 错误码（与 utils/errors.py 对齐，避免硬编码）=====
class _Err:
    TIMEOUT = "LLM_001"
    AUTH = "LLM_002"
    RATE = "LLM_003"
    BAD_RESP = "LLM_004"
    NOT_CONFIGURED = "LLM_005"
    NETWORK = "LLM_006"
    NOT_IMPLEMENTED = "LLM_007"


# ===== Provider 抽象基类（Day 6 模板方法模式）=====

def _has_structured_content(messages) -> bool:
    """消息里是否带 OpenAI 风格的 content 块（text / image_url 分段）。

    这种消息不能拍平成提示词字符串：图片块经 f-string 序列化会变成 Python
    列表的字面文本，模型收到的是那串字符而不是图，等于彻底失明。
    """
    for message in messages or []:
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            return True
    return False


class BaseProvider(ABC):
    """LLM Provider 抽象基类 - 统一 HTTP 调用流程，子类仅需实现配置差异"""

    def __init__(self, timeout_sec: int = 30):
        self.timeout_sec = timeout_sec

    @abstractmethod
    def _get_api_key(self) -> str:
        """获取 API Key"""
        pass

    @abstractmethod
    def _get_endpoint(self) -> str:
        """获取 API 端点 URL"""
        pass

    @abstractmethod
    def _get_model(self) -> str:
        """获取模型名称"""
        pass

    def _get_provider_name(self) -> str:
        """获取 Provider 名称，用于错误消息"""
        return self.__class__.__name__.replace("Provider", "").lower()

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        timeout: int = 30,
        response_format: Optional[str] = None,
        **kwargs,
    ) -> str:
        """模板方法：统一的生成流程"""
        provider_name = self._get_provider_name()

        # 1. 验证 API Key
        api_key = self._get_api_key()
        if not api_key or api_key.startswith("your-") or api_key == "":
            raise RuntimeError(
                f"[{_Err.NOT_CONFIGURED}] {provider_name.upper()}_API_KEY 未配置"
            )

        # 2. 构建请求
        url = self._get_endpoint()
        payload = self._build_payload(prompt, temperature, max_tokens, response_format)
        headers = self._build_headers(api_key)

        # 3. 发送 HTTP 请求
        data = await self._call_http(url, payload, headers, timeout)

        # 4. 解析响应
        return self._parse_response(data)

    def _build_payload(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        response_format: Optional[str],
    ) -> Dict[str, Any]:
        """构建请求 payload — 子类可覆盖以支持特殊参数"""
        payload = {
            "model": self._get_model(),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _build_headers(self, api_key: str) -> Dict[str, str]:
        """构建请求头 — 子类可覆盖以支持特殊认证方式"""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    def _get_proxy(self):
        """需要出网代理的 Provider 覆盖此方法；默认直连。"""
        return None

    async def _call_http(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        timeout: int,
    ) -> Dict[str, Any]:
        """发送 HTTP 请求并统一处理错误"""
        if aiohttp is None:
            raise RuntimeError(
                f"[{_Err.NOT_IMPLEMENTED}] aiohttp 不可用，无法发起 HTTP 调用"
            )

        provider_name = self._get_provider_name()
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        proxy = self._get_proxy()

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(url, json=payload, headers=headers, proxy=proxy) as resp:
                    if resp.status >= 500:
                        body = await resp.text()
                        raise RuntimeError(
                            f"[{_Err.NETWORK}] {provider_name.capitalize()} 5xx: "
                            f"status={resp.status}, body={body[:200]}"
                        )
                    if resp.status == 401:
                        raise RuntimeError(
                            f"[{_Err.AUTH}] {provider_name.capitalize()} 鉴权失败"
                        )
                    if resp.status == 429:
                        raise RuntimeError(
                            f"[{_Err.RATE}] {provider_name.capitalize()} 限流"
                        )
                    if resp.status >= 400:
                        body = await resp.text()
                        raise RuntimeError(
                            f"[{_Err.BAD_RESP}] {provider_name.capitalize()} 4xx: "
                            f"status={resp.status}, body={body[:200]}"
                        )
                    return await resp.json(content_type=None)
        except asyncio.TimeoutError:
            raise
        except aiohttp.ClientError as e:
            raise RuntimeError(
                f"[{_Err.NETWORK}] {provider_name.capitalize()} 网络错误: "
                f"{type(e).__name__}: {e}"
            )

    def _parse_response(self, data: Dict[str, Any]) -> str:
        """解析 OpenAI 兼容响应"""
        provider_name = self._get_provider_name()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"[{_Err.BAD_RESP}] {provider_name.capitalize()} 响应解析失败: {e}, "
                f"data={str(data)[:200]}"
            )

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        """流式对话 - 兼容 chat quick 端点"""
        if aiohttp is None:
            raise RuntimeError(f"[{_Err.NOT_IMPLEMENTED}] aiohttp 不可用")

        api_key = self._get_api_key()
        if not api_key or api_key.startswith("your-") or api_key == "":
            raise RuntimeError(f"[{_Err.NOT_CONFIGURED}] API Key 未配置")

        full_messages = (
            [{"role": "system", "content": system_prompt}] + list(messages)
            if system_prompt else list(messages)
        )

        payload = {
            "model": self._get_model(),
            "messages": full_messages,
            "stream": True,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }

        url = self._get_endpoint()
        headers = self._build_headers(api_key)
        provider_name = self._get_provider_name()
        client_timeout = aiohttp.ClientTimeout(total=180, connect=10)

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        raise RuntimeError(
                            f"[{_Err.NETWORK}] {provider_name} stream {resp.status}: {body[:200]}"
                        )
                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                                choices = chunk.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                continue
        except asyncio.TimeoutError:
            raise
        except aiohttp.ClientError as e:
            raise RuntimeError(
                f"[{_Err.NETWORK}] {provider_name} stream error: {type(e).__name__}: {e}"
            )


# ===== 具体 Provider 实现（各 ~15 行，仅配置差异）=====

class DeepSeekProvider(BaseProvider):
    """DeepSeek / 火山方舟 Provider"""

    def _get_api_key(self) -> str:
        return getattr(settings, "DEEPSEEK_API_KEY", "") or ""

    def _get_endpoint(self) -> str:
        base_url = (getattr(settings, "DEEPSEEK_BASE_URL", "")
                    or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
        return f"{base_url}/chat/completions"

    def _get_model(self) -> str:
        return getattr(settings, "DEEPSEEK_MODEL", "") or "deepseek-v4.1-flash"


class SparkProvider(BaseProvider):
    """讯飞星火 Provider"""

    def _get_api_key(self) -> str:
        return getattr(settings, "XUNFEI_API_PASSWORD", "") or ""

    def _get_endpoint(self) -> str:
        return "https://spark-api-open.xf-yun.com/v2/chat/completions"

    def _get_model(self) -> str:
        return getattr(settings, "LLM_MODEL", "spark-x") or "spark-x"


class QwenProvider(BaseProvider):
    """通义千问 Provider"""

    def _get_api_key(self) -> str:
        return getattr(settings, "QWEN_API_KEY", "") or ""

    def _get_endpoint(self) -> str:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    def _get_model(self) -> str:
        return getattr(settings, "QWEN_MODEL", "") or "qwen-plus"


class OpenAICompatibleProvider(BaseProvider):
    """任意 OpenAI 兼容端点。

    存在的理由：`config.validate_production_secrets` 一直把 openai_compatible 当作
    合法的生产 Provider，但 LLMService 之前没有对应实现，会静默回退到 mock ——
    于是「配置合法」与「真的调用模型」之间出现了缺口。这里把它补成真实通道。
    """

    def _get_api_key(self) -> str:
        return getattr(settings, "OPENAI_COMPATIBLE_API_KEY", "") or ""

    def _get_endpoint(self) -> str:
        base = (getattr(settings, "OPENAI_COMPATIBLE_BASE_URL", "") or "").rstrip("/")
        if not base:
            return ""
        return base if base.endswith("/chat/completions") else f"{base}/chat/completions"

    def _get_model(self) -> str:
        return getattr(settings, "OPENAI_COMPATIBLE_MODEL", "") or getattr(settings, "LLM_MODEL", "") or ""

    def _get_proxy(self):
        # 境外端点常需本机代理；未配置时返回 None，不影响直连场景。
        return (getattr(settings, "OPENAI_COMPATIBLE_PROXY", "") or "").strip() or None


# ===== LLMService（重构版 — 委托给 Provider 实例）=====

class LLMService:
    """
    Day 6 重构版 LLM 抽象服务。

    用法：
        svc = LLMService(provider=settings.LLM_PROVIDER)
        text = await svc.generate(prompt="...", temperature=0.3, max_tokens=2000)

    行为契约：
        - provider == MOCK                              → _mock_generate 返回预定义响应
        - provider in {DEEPSEEK, SPARK, QWEN}            → 委派给对应 Provider 实例
            * 无 API Key / 平台 5xx / 超时                → 抛具体异常，由 agent 外层 try/except 降级
    """

    # Provider 类映射（替代原 _PROVIDER_DISPATCH 方法名字符串跳转）
    _PROVIDER_CLASSES = {
        LLMProvider.DEEPSEEK: DeepSeekProvider,
        LLMProvider.SPARK: SparkProvider,
        LLMProvider.QWEN: QwenProvider,
        LLMProvider.OPENAI_COMPATIBLE: OpenAICompatibleProvider,
    }

    def __init__(self, provider: str = "mock"):
        valid = [p.value for p in LLMProvider]
        if provider not in valid:
            # 绝不静默回退 mock：写错 provider 时必须立刻炸掉，
            # 否则生产环境会端出一份模板内容而所有人以为它是真实回答。
            raise ValueError(
                f"[LLMService] 未知 provider '{provider}'；"
                f"可选值：{', '.join(valid)}"
            )
        self.provider = LLMProvider(provider)
        self.timeout_sec: int = getattr(settings, "LLM_TIMEOUT_SEC", None) or \
                                getattr(settings, "LLM_TIMEOUT", 30)

        # 非 mock 模式：初始化 Provider 实例
        self._provider_instance = None
        if self.provider != LLMProvider.MOCK:
            provider_class = self._PROVIDER_CLASSES.get(self.provider)
            if provider_class is None:
                raise ValueError(
                    f"[LLMService] provider '{self.provider.value}' 没有可用实现"
                )
            self._provider_instance = provider_class(timeout_sec=self.timeout_sec)

        logger.debug(
            f"[LLMService] init: provider={self.provider.value}, "
            f"timeout={self.timeout_sec}s"
        )

    # ============= 统一生成入口 =============

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        timeout: Optional[int] = None,
        response_format: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        统一生成入口（Day 6 重构版 — 委派给 Provider 实例）。

        Args:
            prompt: 用户提示词
            temperature: 0-1，越低越确定
            max_tokens: 最大 token 数
            timeout: 超时秒数（None 时取 self.timeout_sec）
            response_format: "json" 强制 JSON 输出

        Returns:
            LLM 原始文本响应（mock 模式为预定义 JSON 字符串）
        """
        effective_timeout = int(timeout) if timeout else self.timeout_sec

        # Mock 模式
        if self.provider == LLMProvider.MOCK:
            return await asyncio.wait_for(
                self._mock_generate(
                    prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=effective_timeout,
                    response_format=response_format,
                    **kwargs,
                ),
                timeout=effective_timeout + 5,
            )

        # Real 模式：委派给 Provider 实例
        if self._provider_instance is None:
            raise NotImplementedError(
                f"Provider '{self.provider.value}' 暂未实现"
            )

        result = await asyncio.wait_for(
            self._provider_instance.generate(
                prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=effective_timeout,
                response_format=response_format,
                **kwargs,
            ),
            timeout=effective_timeout + 5,
        )
        return self._validate_response(result)

    # ============= 辅助方法 =============

    def _count_tokens(self, text: str) -> int:
        """估算 token 数（字符/4 粗略估算）"""
        if not text:
            return 0
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars / 3.5)

    def _validate_response(self, response: Optional[str]) -> str:
        """校验 LLM 响应非空"""
        if response is None:
            raise ValueError("LLM response is None")
        text = response.strip()
        if not text:
            raise ValueError("LLM response is empty")
        return text

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """
        解析 LLM 输出 JSON。
        优先 json.loads → 失败去 markdown 包裹 → 再失败正则提取。
        """
        import re
        if not text or not text.strip():
            raise ValueError("LLM 返回为空")

        # 尝试 1：直接 json.loads
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试 2：去除 markdown 代码块包裹
        cleaned = text.strip()
        if cleaned.startswith("```"):
            first_newline = cleaned.find("\n")
            if first_newline != -1:
                cleaned = cleaned[first_newline + 1:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 尝试 3：正则提取首个 JSON 对象
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError(f"LLM 返回非 JSON: {text[:200]}")

    # ============= 模式 A：mock =============

    async def _mock_generate(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        timeout: int = 30,
        response_format: Optional[str] = None,
        **kwargs,
    ) -> str:
        """mock 模式：返回预定义画像 JSON"""
        await asyncio.sleep(0)
        return json.dumps({
            "major": "计算机科学与技术",
            "grade_level": "高三",
            "target_role": "软件工程师",
            "prior_courses": ["数据结构", "概率论"],
            "preferred_language": "Python",
            "learning_style": "visual",
            "pace": "medium",
            "weaknesses": ["数学推导", "特征工程"],
            "strengths": ["Python 基础", "数据处理"],
            "_meta": {
                "tokens_used": self._count_tokens(prompt) + 256,
                "model": "mock-v1",
                "provider": "mock",
            }
        }, ensure_ascii=False)

    # ============= 兼容方法（与 MockLLMService / BaseLLMClient 对齐） =============

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """对外暴露的 chat 兼容方法。

        带 content 块（图片）的调用走多模态通道，原样送达消息数组；纯文本调用
        保持原来的拼装路径，不改动既有生成器的行为。
        """
        if _has_structured_content(messages) and self.provider != LLMProvider.MOCK:
            return await self._chat_with_content_blocks(
                messages,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        full_prompt = ""
        if system_prompt:
            full_prompt += f"[System]\n{system_prompt}\n\n"
        for m in messages or []:
            role = m.get("role", "user")
            content = m.get("content", "")
            full_prompt += f"[{role}]\n{content}\n\n"

        response_text = await self.generate(
            prompt=full_prompt.strip(),
            temperature=temperature or 0.3,
            max_tokens=max_tokens or 2000,
        )
        return {
            "content": response_text,
            "tokens_used": self._count_tokens(response_text),
            "processing_time": 0.0,
            "model": self.provider.value,
        }

    async def _chat_with_content_blocks(
        self,
        messages: List[Dict[str, Any]],
        *,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """多模态路径：把 content 块原样发给模型。

        复用 Provider 的请求管道，保证鉴权、错误映射与响应解析仍然只有一份实现。
        """
        provider = self._provider_instance
        if provider is None:
            raise NotImplementedError(f"Provider '{self.provider.value}' 暂未实现")

        api_key = provider._get_api_key()
        if not api_key or api_key.startswith("your-") or api_key == "":
            raise RuntimeError(
                f"[{_Err.NOT_CONFIGURED}] {self.provider.value.upper()}_API_KEY 未配置"
            )

        full_messages: List[Dict[str, Any]] = list(messages or [])
        if system_prompt:
            full_messages = [{"role": "system", "content": system_prompt}] + full_messages

        payload: Dict[str, Any] = {
            "model": provider._get_model(),
            "messages": full_messages,
            "temperature": float(temperature if temperature is not None else 0.3),
            "max_tokens": int(max_tokens or 2000),
            "stream": False,
        }
        data = await provider._call_http(
            provider._get_endpoint(), payload, provider._build_headers(api_key), self.timeout_sec,
        )
        response_text = provider._parse_response(data)
        return {
            "content": response_text,
            "tokens_used": self._count_tokens(response_text),
            "processing_time": 0.0,
            "model": self.provider.value,
        }

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """流式对话 - 委派给 Provider 实例"""
        if self.provider == LLMProvider.MOCK:
            from services.mock_llm_service import get_llm_service as get_mock
            mock_svc = await get_mock()
            async for chunk in mock_svc.chat_stream(messages, system_prompt=system_prompt):
                yield chunk
            return

        if self._provider_instance is None:
            raise RuntimeError(f"Provider {self.provider.value} 未初始化")

        async for chunk in self._provider_instance.chat_stream(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature or 0.7,
            max_tokens=max_tokens or 2048,
        ):
            yield chunk

    async def structured_output(
        self,
        messages: List[Dict[str, str]],
        output_schema: Dict[str, Any],
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """对外暴露的 structured_output 兼容方法"""
        schema_instruction = (
            f"\n请严格按照以下JSON格式返回：\n"
            f"{json.dumps(output_schema, ensure_ascii=False, indent=2)}\n"
            f"直接返回JSON，不要其他文字。"
        )
        full_system = (
            f"{system_prompt}\n\n{schema_instruction}" if system_prompt
            else schema_instruction
        )
        chat_result = await self.chat(
            messages=messages,
            system_prompt=full_system,
            temperature=0.3,
            max_tokens=2500,
        )
        content = chat_result["content"]
        try:
            parsed = self._extract_json(content)
            parsed["_meta"] = {
                "tokens_used": chat_result["tokens_used"],
                "model": self.provider.value,
            }
            return parsed
        except ValueError as e:
            logger.warning(
                f"[LLMService.structured_output] 解析失败: {e}, "
                f"返回空 schema 占位"
            )
            return {**output_schema, "_meta": {"tokens_used": 0}}

    async def simple_chat(self, message: str) -> str:
        """与 BaseLLMClient.simple_chat 对齐"""
        result = await self.chat([{"role": "user", "content": message}])
        return result["content"]

    async def close(self):
        pass


# ============= 单例 + 老 get_llm_service() 兼容 =============

async def get_llm_service():
    """
    兼容旧调用：
    - MODE=mock 或 LLM_PROVIDER=mock → 返回 MockLLMService
    - MODE=production + LLM_PROVIDER≠mock → 返回新 LLMService
      初始化失败时直接抛错，避免生产环境静默写入 mock 内容。
    """
    global _llm_service, _llm_mode, _llm_last_error

    if _llm_service is not None:
        return _llm_service

    if settings.LLM_PROVIDER == LLMProvider.MOCK.value or settings.MODE == "mock":
        from services.mock_llm_service import get_llm_service as get_mock
        _llm_service = await get_mock()
        _llm_mode = "mock"
        _llm_last_error = None
        logger.info("[LLM] 🎭 使用 Mock 模式")
        return _llm_service

    try:
        _llm_service = LLMService(provider=settings.LLM_PROVIDER)
        _llm_mode = "production"
        _llm_last_error = None
        logger.info(
            f"[LLM] 🤖 使用 Production 模式 (provider={_llm_service.provider.value})"
        )
    except Exception as e:
        _llm_last_error = f"{type(e).__name__}: {e}"
        if settings.MODE == "production":
            _llm_mode = "failed"
            logger.error(
                f"[LLM] ❌ Production 初始化失败，拒绝降级 Mock: {_llm_last_error}"
            )
            raise
        logger.warning(
            f"[LLM] ⚠️ LLM 初始化失败，开发模式降级到 Mock: {_llm_last_error}"
        )
        try:
            from services.mock_llm_service import get_llm_service as get_mock
            _llm_service = await get_mock()
            _llm_mode = "degraded"
        except Exception as mock_err:
            logger.error(f"[LLM] ❌ Mock 兜底也失败: {mock_err}")
            _llm_mode = "failed"
            raise

    return _llm_service


def get_llm_status() -> dict:
    """供 /api/health 端点使用。"""
    return {
        "mode": _llm_mode,
        "initialized": _llm_service is not None,
        "last_error": _llm_last_error,
    }


def reset_llm_service():
    """重置 LLM 单例（测试用）。"""
    global _llm_service, _llm_mode, _llm_last_error
    _llm_service = None
    _llm_mode = "unknown"
    _llm_last_error = None


# 向后兼容
__all__ = [
    'get_llm_service', 'get_llm_status', 'reset_llm_service',
    'LLMProvider', 'LLMService',
    'BaseProvider', 'DeepSeekProvider', 'SparkProvider', 'QwenProvider',
]
