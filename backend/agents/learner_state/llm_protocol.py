from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class LLMResponse:
    content: str
    finish_reason: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class StructuredLLM(Protocol):
    async def chat(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        response_format: dict[str, str] | None = None,
    ) -> Any: ...


class UnavailableLLM:
    def __init__(self, reason: str = "provider unavailable") -> None:
        self.reason = reason

    async def chat(self, **_: Any) -> Any:
        raise RuntimeError(self.reason)


def coerce_llm_response(value: Any) -> LLMResponse:
    if isinstance(value, LLMResponse):
        return value
    if isinstance(value, str):
        return LLMResponse(content=value)
    if isinstance(value, dict):
        content = value.get("content")
        if content is None and isinstance(value.get("message"), dict):
            content = value["message"].get("content")
        usage = value.get("usage") or {}
        return LLMResponse(
            content=str(content or ""),
            finish_reason=value.get("finish_reason"),
            model=value.get("model"),
            prompt_tokens=value.get("prompt_tokens") or usage.get("prompt_tokens"),
            completion_tokens=value.get("completion_tokens") or usage.get("completion_tokens"),
        )
    content = getattr(value, "content", None)
    if content is not None:
        return LLMResponse(
            content=str(content),
            finish_reason=getattr(value, "finish_reason", None),
            model=getattr(value, "model", None),
            prompt_tokens=getattr(value, "prompt_tokens", None),
            completion_tokens=getattr(value, "completion_tokens", None),
        )
    raise TypeError(f"unsupported LLM response type: {type(value).__name__}")
