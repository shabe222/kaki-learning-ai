"""
结构化 JSON 日志中间件（Day 3 — A4）

目标：
1. 每个 HTTP 请求输出单行 JSON 日志（method / path / status / duration_ms / request_id）
2. 响应头注入 X-Request-Id，便于前端报错时回溯
3. 异常路径同样记录（status=500, error/error_type），不吞堆栈

依赖：starlette.middleware.base.BaseHTTPMiddleware（FastAPI 自带）
"""
import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("rjianb.http")


class JsonFormatter(logging.Formatter):
    """
    单行 JSON formatter（Day 4 — 1b）
    避免 root logger 的 `2026-... [INFO] rjianb.http:` 文本前缀混入日志行
    """

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "ts": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            },
            ensure_ascii=False,
        )


# 在 module 末尾或 import 时
http_logger = logging.getLogger("rjianb.http")
http_logger.handlers.clear()
_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
http_logger.addHandler(_handler)
http_logger.setLevel(logging.INFO)
http_logger.propagate = False


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """结构化 JSON 日志中间件"""

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. request_id：优先从 header 拿（链路追踪），否则生成新 uuid
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex

        # 2. 计时
        start = time.perf_counter()

        # 3. 处理请求（捕获异常保证 5xx 也被记录后重新抛出，让上层处理）
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            self._emit_log(
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status=500,
                duration_ms=duration_ms,
                client=request.client.host if request.client else None,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        # 4. 响应头注入 X-Request-Id
        response.headers["X-Request-Id"] = request_id

        # 5. 写结构化日志
        self._emit_log(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            client=request.client.host if request.client else None,
        )

        return response

    @staticmethod
    def _emit_log(**fields) -> None:
        """输出单行 JSON 日志（失败时降级为字符串拼接）"""
        try:
            line = json.dumps(fields, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            line = json.dumps(
                {k: str(v) for k, v in fields.items()},
                ensure_ascii=False,
            )
        logger.info(line)
