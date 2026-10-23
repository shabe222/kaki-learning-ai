"""
慢查询日志中间件（Day 4 — 2c）

目标：
1. 监控每个 HTTP 请求的耗时（毫秒）
2. 超过 SLOW_THRESHOLD_MS 阈值时，输出到 rjianb.slow_query logger（WARNING 级别）
3. 包含：method / path / duration_ms / status_code — 便于快速定位慢端点

设计：
- 作为最外层中间件（最后 add_middleware）注册，捕获完整请求耗时
- 与 StructuredLoggingMiddleware 互补：后者输出每条请求的结构化 JSON 日志，
  本中间件只在超阈值时打 WARNING，避免日志噪音
"""
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


SLOW_THRESHOLD_MS = 500

slow_logger = logging.getLogger("rjianb.slow_query")
slow_logger.setLevel(logging.WARNING)
slow_logger.propagate = True  # 让根 logger 的 handler 也能收到


class SlowQueryMiddleware(BaseHTTPMiddleware):
    """
    慢查询日志中间件（基于 BaseHTTPMiddleware）
    记录耗时 > SLOW_THRESHOLD_MS 的请求。
    """

    def __init__(self, app: ASGIApp, threshold_ms: int = SLOW_THRESHOLD_MS) -> None:
        super().__init__(app)
        self.threshold_ms = threshold_ms

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.time()
        response = await call_next(request)
        duration_ms = int((time.time() - start) * 1000)
        if duration_ms > self.threshold_ms:
            slow_logger.warning(
                f"SLOW: {request.method} {request.url.path} · {duration_ms}ms · status={response.status_code}"
            )
        return response
