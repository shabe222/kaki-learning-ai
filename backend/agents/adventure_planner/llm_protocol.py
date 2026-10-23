from __future__ import annotations

from typing import Any, Protocol


class PlannerProviderUnconfiguredError(RuntimeError):
    """Raised when the configured project provider has no usable credential."""


class ChatLLM(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str | dict[str, Any]: ...


class UnavailableLLM:
    def __init__(self, reason: str = "provider is not configured"):
        self.reason = reason

    async def chat(self, *_args: Any, **_kwargs: Any) -> str:
        raise PlannerProviderUnconfiguredError(self.reason)


def extract_text(result: str | dict[str, Any]) -> tuple[str, str | None, int, str | None]:
    """Extract normalized provider text and non-sensitive completion metadata.

    Legacy string providers remain supported. OpenAI-compatible clients may also
    return ``finish_reason`` so the planner can distinguish a clean stop from an
    output-length truncation without exposing raw provider responses.
    """
    if isinstance(result, str):
        return result, None, 0, None
    content = result.get("content")
    if not isinstance(content, str):
        raise TypeError("LLM response does not contain string content")
    model = result.get("model") if isinstance(result.get("model"), str) else None
    tokens = result.get("tokens_used")
    finish_reason = (
        result.get("finish_reason")
        if isinstance(result.get("finish_reason"), str)
        else None
    )
    return (
        content,
        model,
        int(tokens) if isinstance(tokens, (int, float)) and tokens >= 0 else 0,
        finish_reason,
    )
