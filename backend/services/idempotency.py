"""付费调用的幂等闸门。

存在理由：`/api/chat` 与 `/api/chat/quick` 每次调用都会真实计费。客户端的双击、
超时重试、以及代理层的重复投递都会造成同一轮对话被购买两次。`ChatRequest.message_id`
的注释一直写着「服务端发送锁与幂等键」，但代码只把它当 ID 生成器用。

契约：
- 同一 (user_id, message_id) 且未显式 `retry` 时，第一次真正执行；
- 执行中的重复请求被识别为 in_flight，不再次计费；
- 已完成的重复请求直接重放上次结果（含 SSE 全文），字节级一致；
- `retry=True` 表示用户显式要求重做，绕过闸门。

边界：进程内实现，配合单进程部署正确；多 worker / 多副本需要换成共享存储
（见 PRODUCT_PROGRESS 的债务清单）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal, Optional

logger = logging.getLogger(__name__)

_TTL_SECONDS = 15 * 60
_MAX_ENTRIES = 200
_MAX_CACHED_BYTES = 512 * 1024


@dataclass
class Guard:
    state: Literal["new", "in_flight", "cached", "disabled"]
    payload: Optional[str] = None


class IdempotencyGuard:
    def __init__(self, *, ttl_seconds: int = _TTL_SECONDS, max_entries: int = _MAX_ENTRIES) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._inflight: dict[tuple[int, str], float] = {}
        self._done: dict[tuple[int, str], tuple[float, Optional[str]]] = {}
        self._lock = asyncio.Lock()

    # ---- 内部 ----

    def _sweep(self, now: float) -> None:
        for store in (self._inflight, self._done):
            for key, value in list(store.items()):
                stamp = value if isinstance(value, float) else value[0]
                if now - stamp > self._ttl:
                    store.pop(key, None)
        if len(self._done) > self._max_entries:
            victims = sorted(self._done.items(), key=lambda item: item[1][0])[
                : len(self._done) - self._max_entries
            ]
            for key, _ in victims:
                self._done.pop(key, None)

    # ---- 生命周期 ----

    async def begin(self, *, user_id: int, message_id: Optional[str], retry: bool = False) -> Guard:
        """决定这次调用是否应该真正执行。"""
        if retry or not message_id:
            # 没有幂等键就没有可判定的身份，只能放行。
            return Guard("disabled" if retry else "new")

        key = (int(user_id), str(message_id))
        now = time.time()
        async with self._lock:
            self._sweep(now)
            done = self._done.get(key)
            if done is not None:
                return Guard("cached", done[1])
            if key in self._inflight:
                return Guard("in_flight")
            self._inflight[key] = now
            return Guard("new")

    async def complete(self, *, user_id: int, message_id: Optional[str], payload: Optional[str] = None) -> None:
        """标记完成，并（在预算内）缓存可重放的结果。"""
        if not message_id:
            return
        key = (int(user_id), str(message_id))
        async with self._lock:
            self._inflight.pop(key, None)
            if payload is None or len(payload.encode("utf-8")) > _MAX_CACHED_BYTES:
                # 太大就不缓存正文，只保留「已完成」，避免无界内存占用。
                self._done[key] = (time.time(), None)
            else:
                self._done[key] = (time.time(), payload)

    async def release(self, *, user_id: int, message_id: Optional[str]) -> None:
        """执行失败时释放闸门，让用户可以立刻重试——失败的调用不该占住幂等键。"""
        if not message_id:
            return
        key = (int(user_id), str(message_id))
        async with self._lock:
            self._inflight.pop(key, None)

    async def reset(self) -> None:
        """测试用。"""
        async with self._lock:
            self._inflight.clear()
            self._done.clear()


_guard = IdempotencyGuard()


def get_idempotency_guard() -> IdempotencyGuard:
    return _guard


def sse_error(code: str, message: str, retryable: bool = False) -> str:
    """构造一条与现有 SSE 契约一致的错误事件。"""
    import json

    return "data: " + json.dumps(
        {"type": "error", "code": code, "message": message, "retryable": retryable},
        ensure_ascii=False,
    ) + "\n\n"
