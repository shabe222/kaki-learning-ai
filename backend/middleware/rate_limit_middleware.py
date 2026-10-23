"""付费与高成本端点的限流。

存在理由：后端此前**没有任何限流**（middleware/ 下只有 logging 与 slow_query）。
一旦公网可达，任何人都能无上限消耗产品的 LLM 额度。

设计：
- ASGI 层拦截，尽量在鉴权与 LLM 调用之前短路，省掉真实成本；
- 身份优先取 Bearer JWT 里的 user_id，未登录时退化为客户端 IP；
- 按路径分类配额（对话类 / 生成类 / 上传类），互不挤占；
- 超限返回 429 + Retry-After，并复用既有错误码风格。
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# 不需要限流的路径前缀：健康检查、静态资源、文档
_EXEMPT_PREFIXES = (
    "/api/health",
    "/static",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/assets",
    "/favicon",
    "/kaki-logo",
)

# 路径 → (类别, 每分钟上限, 每小时上限)
_RULES: tuple[tuple[str, str, int, int], ...] = (
    ("/api/chat", "chat", 30, 400),
    ("/api/exam", "generate", 12, 150),
    ("/api/workspace", "generate", 20, 300),
    ("/api/materials", "generate", 20, 300),
    ("/api/study", "generate", 20, 300),
    ("/api/agent", "generate", 20, 200),
    ("/api/formula", "generate", 20, 300),
    ("/api/upload", "upload", 20, 120),
    ("/api/auth", "auth", 30, 200),
)

_WINDOW_MINUTE = 60
_WINDOW_HOUR = 3600


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # identity -> category -> deque[timestamp]
        self._hits: dict[str, dict[str, deque[float]]] = defaultdict(lambda: defaultdict(deque))

    # ---- 身份 ----

    @staticmethod
    def _identity(scope: Scope) -> str:
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization") or b""
        token = raw.decode("latin-1").strip()
        if token.lower().startswith("bearer "):
            try:
                from utils.auth import decode_token

                payload = decode_token(token[7:].strip())
                if payload and payload.get("sub") is not None:
                    return f"user:{payload['sub']}"
            except Exception:
                pass
        client = scope.get("client") or ("unknown", 0)
        return f"ip:{client[0]}"

    @staticmethod
    def _rule(path: str):
        for prefix, category, per_minute, per_hour in _RULES:
            if path.startswith(prefix):
                return category, per_minute, per_hour
        return None

    def _check(self, identity: str, category: str, per_minute: int, per_hour: int) -> int:
        """返回 0 表示放行，否则返回建议的 Retry-After 秒数。"""
        now = time.time()
        buckets = self._hits[identity][category]
        while buckets and now - buckets[0] > _WINDOW_HOUR:
            buckets.popleft()

        minute_count = 0
        for stamp in buckets:
            if now - stamp <= _WINDOW_MINUTE:
                minute_count += 1

        if minute_count >= per_minute:
            oldest_in_minute = next((s for s in buckets if now - s <= _WINDOW_MINUTE), now)
            return max(1, int(_WINDOW_MINUTE - (now - oldest_in_minute)) + 1)
        if len(buckets) >= per_hour:
            return max(1, int(_WINDOW_HOUR - (now - buckets[0])) + 1)

        buckets.append(now)
        return 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        rule = self._rule(path)
        if rule is None:
            await self.app(scope, receive, send)
            return

        category, per_minute, per_hour = rule
        identity = self._identity(scope)
        retry_after = self._check(identity, category, per_minute, per_hour)
        if not retry_after:
            await self.app(scope, receive, send)
            return

        logger.warning("[RateLimit] 拦截 %s identity=%s category=%s", path, identity, category)
        body = json.dumps(
            {
                "detail": "请求过于频繁，请稍后再试",
                "code": "RATE_LIMITED",
                "retry_after": retry_after,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"retry-after", str(retry_after).encode("ascii")),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def reset(self) -> None:
        """测试用。"""
        self._hits.clear()


_middleware: RateLimitMiddleware | None = None


def get_rate_limit_middleware() -> RateLimitMiddleware | None:
    return _middleware


def install_rate_limit_middleware(app) -> None:
    """在 app 上安装限流，并登记实例以便测试重置。"""
    global _middleware

    from config import settings
    if not getattr(settings, "RATE_LIMIT_ENABLED", True):
        logger.warning("[RateLimit] 已通过配置关闭")
        return
    _middleware = RateLimitMiddleware(app)
    app.add_middleware(RateLimitMiddleware)
