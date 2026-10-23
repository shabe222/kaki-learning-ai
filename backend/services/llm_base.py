"""
BaseLLMClient - LLM 客户端抽象基类

抽离 XingHuo / DeepSeek 两个实现的共同逻辑：
- HTTP chat / chat_stream 调用
- 缓存读写
- JSON 结构化输出
- 简单对话封装

子类只需实现：
- _get_auth_token()  返回当前 Provider 的鉴权 token
- LOG_TAG  日志前缀

修复 chat_stream 中 `len(content)` 当 token 数的 bug：
  字符数 ≠ token 数，统一改为不再累加（需要准确 token 时用 chat() 的 usage 字段）。
"""
import httpx
import json
import time
import hashlib
import logging
import certifi
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator, List, Dict, Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _normalize_response_format(response_format):
    """Normalize the one structured-output mode supported by this client.

    ``"json"`` is kept as a compatibility alias. Arbitrary provider payloads
    are rejected so callers cannot smuggle unrelated request options through
    the shared LLM layer.
    """
    if response_format is None:
        return None
    if response_format == "json":
        return {"type": "json_object"}
    if (
        isinstance(response_format, dict)
        and response_format.get("type") == "json_object"
        and set(response_format) == {"type"}
    ):
        return {"type": "json_object"}
    raise ValueError("unsupported response_format; expected json_object")


def _make_cache_payload(messages, system_prompt, temperature, max_tokens, response_format=None):
    """根据 LLM 调用参数生成缓存 key 的输入指纹"""
    payload = {
        "messages": messages,
        "system": system_prompt or "",
        "temp": temperature,
        "max": max_tokens,
        "response_format": _normalize_response_format(response_format),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


@dataclass
class LLMConfig:
    api_password: str = ""
    api_key: str = ""
    app_id: str = ""
    api_secret: str = ""
    model: str = "spark-x"
    base_url: str = "https://spark-api-open.xf-yun.com/v2/chat/completions"
    max_tokens: int = 2048  # token优化：默认从4096降到2048，各agent按需显式传更大的值
    temperature: float = 0.7
    timeout: float = 180.0
    proxy: str = ""  # 可选HTTP代理（如 http://127.0.0.1:7897），仅对需要代理的第三方平台生效


class BaseLLMClient(ABC):
    """LLM 客户端基类 - 统一 HTTP 调用、缓存、JSON 输出"""

    LOG_TAG: str = "LLM"
    SUPPORTS_JSON_OBJECT: bool = False

    def __init__(self, config: LLMConfig):
        self.config = config
        client_kwargs: dict = {}
        if config.proxy:
            client_kwargs["proxies"] = {"http://": config.proxy, "https://": config.proxy}
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout, connect=10.0),
            # Use the Python environment's explicit CA bundle.  This keeps TLS
            # verification enabled and avoids inheriting invisible proxy state.
            verify=certifi.where(),
            trust_env=False,
            **client_kwargs,
        )
        # 修复：原 chat_stream 用 len(content) 累加当作 token 数，语义错误
        # 改为按"流式已发送字符数"追踪；真实 token 数走 chat() 的 usage 字段
        self.total_chars_streamed = 0

    @abstractmethod
    def _get_auth_token(self) -> str:
        """返回当前 Provider 使用的鉴权 token（XingHuo=api_password, DeepSeek=api_key）"""

    def _build_headers(self, stream: bool = False) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._get_auth_token()}",
        }
        if stream:
            headers["Accept"] = "text/event-stream"
        return headers

    async def _write_cache(self, cache_agent, cache_chapter, cache_stage, cache_profile_hash, content_hash, content, cache_user_id=0):
        from services.cache_service import CacheService
        try:
            combined = hashlib.md5(f"{cache_profile_hash}:{content_hash}".encode()).hexdigest()[:12]
            await CacheService.set_llm_cache(cache_agent, cache_chapter, cache_stage, content, combined, user_id=cache_user_id)
        except Exception as e:
            logger.warning(f"[{self.LOG_TAG}] 缓存写入失败(非阻塞): {e}")

    async def chat(
        self,
        messages,
        system_prompt=None,
        temperature=None,
        max_tokens=None,
        cache_agent=None,
        cache_chapter=None,
        cache_stage=None,
        cache_profile_hash="",
        cache_user_id=0,
        use_cache=True,
        write_cache=True,
        response_format=None,
    ):
        from services.cache_service import CacheService

        effective_max = max_tokens or self.config.max_tokens
        normalized_response_format = _normalize_response_format(response_format)
        content_hash = _make_cache_payload(
            messages,
            system_prompt,
            temperature,
            effective_max,
            normalized_response_format,
        )
        combined_hash = hashlib.md5(f"{cache_profile_hash}:{content_hash}".encode()).hexdigest()[:12]
        if use_cache and cache_agent and cache_chapter and cache_stage:
            cached = await CacheService.get_llm_cache(cache_agent, cache_chapter, cache_stage, combined_hash, user_id=cache_user_id)
            if cached:
                logger.info(f"[{self.LOG_TAG}] cache hit: u{cache_user_id}/{cache_agent}/{cache_chapter}/{cache_stage}")
                return {
                    "content": cached["content"],
                    "tokens_used": 0,
                    "processing_time": 0.001,
                    "model": self.config.model,
                    "finish_reason": None,
                    "_from_cache": True,
                }

        full_messages = (
            [{"role": "system", "content": system_prompt}] + messages
            if system_prompt else messages
        )

        payload = {
            "model": self.config.model,
            "messages": full_messages,
            "stream": False,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": effective_max,
        }
        if normalized_response_format is not None and self.SUPPORTS_JSON_OBJECT:
            payload["response_format"] = normalized_response_format

        try:
            start_time = time.time()
            logger.info(f"[{self.LOG_TAG}] request: model={self.config.model}, user_id={cache_user_id}, messages={len(full_messages)}, max_tokens={effective_max}")
            response = await self.client.post(self.config.base_url, headers=self._build_headers(stream=False), json=payload)
            response.raise_for_status()
            result = response.json()

            if "choices" in result and len(result["choices"]) > 0:
                choice = result["choices"][0]
                msg = choice["message"]
                content = msg.get("content", "") or msg.get("reasoning_content", "")
                finish_reason = choice.get("finish_reason")
                usage = result.get("usage", {})
                tokens_used = usage.get("total_tokens", 0)
            else:
                content = str(result)
                finish_reason = None
                tokens_used = 0

            elapsed = time.time() - start_time
            logger.info(f"[{self.LOG_TAG}] response: {elapsed:.2f}s, tokens={tokens_used}, content_len={len(content)}")

            if use_cache and write_cache and cache_agent and cache_chapter and cache_stage:
                await self._write_cache(cache_agent, cache_chapter, cache_stage, cache_profile_hash, content_hash, content, cache_user_id=cache_user_id)

            return {
                "content": content,
                "tokens_used": tokens_used,
                "processing_time": elapsed,
                "model": self.config.model,
                "finish_reason": finish_reason,
            }

        except httpx.TimeoutException:
            logger.error(f"[{self.LOG_TAG}] chat timeout after {self.config.timeout}s")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"[{self.LOG_TAG}] HTTP {e.response.status_code}: {e.response.text[:300]}")
            raise
        except Exception as e:
            logger.error(f"[{self.LOG_TAG}] chat failed: {str(e)}")
            raise

    async def chat_stream(
        self,
        messages,
        system_prompt=None,
        *,
        response_format=None,
        temperature=None,
        max_tokens=None,
    ) -> AsyncGenerator[str, None]:
        """Stream tokens.

        `response_format` is accepted here because the planner calls need JSON
        mode *and* want to stream: without it, choosing to stream meant giving up
        the guarantee that the answer parses, which is not a trade worth making
        on the turn-planning path. The caller keeps JSON mode; it just also gets
        to say something while the model is still writing.
        """
        full_messages = (
            [{"role": "system", "content": system_prompt}] + messages
            if system_prompt else messages
        )

        payload = {
            "model": self.config.model,
            "messages": full_messages,
            "stream": True,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": self.config.max_tokens if max_tokens is None else max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        try:
            async with self.client.stream("POST", self.config.base_url, headers=self._build_headers(stream=True), json=payload) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk_data = json.loads(data_str)
                            choices = chunk_data.get("choices", [])
                            if len(choices) > 0:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                                    # 修复：原代码用 len(content) 当 token 数累加到 total_tokens_used
                                    # 改为按字符数追踪到 total_chars_streamed（语义正确）
                                    self.total_chars_streamed += len(content)
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error(f"[{self.LOG_TAG}] stream failed: {str(e)}")
            raise

    async def structured_output(self, messages, output_schema, system_prompt=None, cache_user_id=0):
        schema_instruction = f"\n请严格按照以下JSON格式返回：\n{json.dumps(output_schema, ensure_ascii=False, indent=2)}\n直接返回JSON，不要其他文字。"

        full_system = f"{system_prompt}\n\n{schema_instruction}" if system_prompt else schema_instruction

        result = await self.chat(
            messages, system_prompt=full_system, temperature=0.3, max_tokens=2048,
            cache_user_id=cache_user_id, use_cache=False, write_cache=False,
            response_format="json",
        )

        try:
            content = result["content"].strip()
            for prefix in ["```json", "```JSON", "```"]:
                if content.startswith(prefix):
                    content = content[len(prefix):]
                    break
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            if len(content) >= 2 and ((content.startswith('"') and content.endswith('"')) or (content.startswith("'") and content.endswith("'"))):
                content = content[1:-1]
            parsed = json.loads(content)
            return {**parsed, "_meta": {"tokens_used": result["tokens_used"]}}
        except json.JSONDecodeError as e:
            raise Exception(f"JSON parse failed: {e}\nRaw: {result['content'][:500]}")

    async def simple_chat(self, message: str) -> str:
        result = await self.chat([{"role": "user", "content": message}])
        return result["content"]

    async def close(self):
        await self.client.aclose()


class XingHuoLLMService(BaseLLMClient):
    """讯飞星火 LLM 客户端"""

    LOG_TAG = "LLM"

    def _get_auth_token(self) -> str:
        return self.config.api_password


class DeepSeekLLMService(BaseLLMClient):
    """DeepSeek LLM 客户端"""

    LOG_TAG = "DeepSeek"
    SUPPORTS_JSON_OBJECT = True

    def _get_auth_token(self) -> str:
        return self.config.api_key

    async def chat(
        self,
        messages,
        system_prompt=None,
        temperature=None,
        max_tokens=None,
        cache_agent=None,
        cache_chapter=None,
        cache_stage=None,
        cache_profile_hash="",
        cache_user_id=0,
        use_cache=True,
        write_cache=True,
        response_format=None,
    ):
        # Provider diagnostics must never expose any part of a credential.
        endpoint_domain = urlparse(self.config.base_url).netloc or "redacted"
        logger.info(f"[{self.LOG_TAG}] 请求 endpoint_domain: {endpoint_domain}")
        logger.info(f"[{self.LOG_TAG}] 请求 model: {self.config.model}, user_id={cache_user_id}")
        logger.info(f"[{self.LOG_TAG}] API credential configured: {bool(self._get_auth_token())}")
        return await super().chat(
            messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            cache_agent=cache_agent,
            cache_chapter=cache_chapter,
            cache_stage=cache_stage,
            cache_profile_hash=cache_profile_hash,
            cache_user_id=cache_user_id,
            use_cache=use_cache,
            write_cache=write_cache,
            response_format=response_format,
        )
