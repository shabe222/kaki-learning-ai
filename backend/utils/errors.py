"""
统一错误码 + 业务异常定义（Day 3 — A3 → Day 5 强化）

设计目标：
1. 全局错误码集中维护，避免每个 router/agent 各写各的
2. 每个错误码 = (code, message, http_status) 三元组，方便 FastAPI 异常处理器
3. BusinessError 携带 code + detail + extra，可被全局 handler 转为标准 JSON 响应
4. Day 5 新增 request_id 注入（与 StructuredLoggingMiddleware 对齐）

约定：
- LLM_xxx:  LLM 服务相关
- PROFILE_xxx: 学生画像
- DOC_xxx / QST_xxx / MM_xxx / CODE_xxx / PATH_xxx / VID_xxx: 资源生成
- SYS_xxx: 通用（参数/鉴权/资源/内部错误）
"""
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


class ErrorCode:
    """统一错误码定义 (code, message, http_status)"""

    # ===== LLM 相关 =====
    LLM_TIMEOUT = ("LLM_001", "LLM 服务调用超时", 504)
    LLM_AUTH_FAILED = ("LLM_002", "LLM 服务鉴权失败", 401)
    LLM_RATE_LIMITED = ("LLM_003", "LLM 服务限流", 429)
    LLM_INVALID_RESPONSE = ("LLM_004", "LLM 返回格式错误", 502)
    LLM_NOT_CONFIGURED = ("LLM_005", "LLM 服务未配置", 503)
    LLM_NETWORK_ERROR = ("LLM_006", "LLM 网络错误", 502)
    LLM_NOT_IMPLEMENTED = ("LLM_007", "LLM Provider 暂未实现", 501)

    # ===== 学生画像相关 =====
    PROFILE_INCOMPLETE = ("PROFILE_001", "学生画像信息不完整", 422)
    PROFILE_NOT_FOUND = ("PROFILE_002", "未找到学生画像", 404)

    # ===== 资源生成相关 =====
    DOCUMENT_GEN_FAILED = ("DOC_001", "文档生成失败", 500)
    QUESTION_GEN_FAILED = ("QST_001", "题目生成失败", 500)
    MINDMAP_GEN_FAILED = ("MM_001", "思维导图生成失败", 500)
    CODE_GEN_FAILED = ("CODE_001", "代码案例生成失败", 500)
    PATH_GEN_FAILED = ("PATH_001", "学习路径生成失败", 500)
    VIDEO_GEN_FAILED = ("VID_001", "视频生成失败", 500)

    # ===== 通用 =====
    INTERNAL_ERROR = ("SYS_001", "服务器内部错误", 500)
    INVALID_PARAMS = ("SYS_002", "请求参数不合法", 400)
    UNAUTHORIZED = ("SYS_003", "未登录或登录已过期", 401)
    FORBIDDEN = ("SYS_004", "没有访问权限", 403)
    NOT_FOUND = ("SYS_005", "资源不存在", 404)

    @classmethod
    def info(cls, code_tuple: tuple) -> dict:
        """将三元组错误码转为 dict，便于日志/响应序列化"""
        return {
            "code": code_tuple[0],
            "message": code_tuple[1],
            "http_status": code_tuple[2],
        }


class BusinessError(Exception):
    """
    业务异常基类（可被 FastAPI 全局异常处理器捕获并转 JSON）。

    Day 5 新增：可选 request_id（默认 uuid4），便于前后端联合定位。
    """

    def __init__(
        self,
        code: tuple,
        detail: Optional[str] = None,
        extra: Optional[dict] = None,
        request_id: Optional[str] = None,
    ):
        self.code_tuple = code
        self.code = code[0]
        self.message = code[1]
        self.http_status = code[2]
        self.detail = detail or self.message
        self.extra = extra or {}
        self.request_id = request_id or uuid.uuid4().hex[:16]
        super().__init__(self.detail)

    def to_dict(self) -> dict:
        result = {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "request_id": self.request_id,
        }
        if self.extra:
            result["extra"] = self.extra
        return result

    def __repr__(self) -> str:
        return (
            f"BusinessError({self.code}: {self.detail}, "
            f"request_id={self.request_id})"
        )


# ============= 快捷构造 =============

def llm_timeout_error(detail: str = None) -> BusinessError:
    return BusinessError(ErrorCode.LLM_TIMEOUT, detail=detail)


def llm_not_configured_error(provider: str) -> BusinessError:
    return BusinessError(
        ErrorCode.LLM_NOT_CONFIGURED,
        detail=f"LLM provider '{provider}' 未配置 API Key",
        extra={"provider": provider},
    )


def llm_not_implemented_error(provider: str) -> BusinessError:
    return BusinessError(
        ErrorCode.LLM_NOT_IMPLEMENTED,
        detail=f"LLM provider '{provider}' 暂未实现",
        extra={"provider": provider},
    )


def internal_error(detail: str = None) -> BusinessError:
    return BusinessError(ErrorCode.INTERNAL_ERROR, detail=detail)
