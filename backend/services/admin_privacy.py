"""Admin-only privacy projection helpers.

These helpers deliberately expose only coarse technical metadata. They never
return learner prompts, artifact/material titles, profile/impression content,
conversation identifiers, or raw exception text.
"""
from __future__ import annotations

from typing import Optional

_SAFE_AGENT_LABELS = {
    "profile": "画像状态任务",
    "document": "讲义资源任务",
    "lecture": "讲义资源任务",
    "question": "练习 / 考试任务",
    "exam": "练习 / 考试任务",
    "mindmap": "思维导图资源任务",
    "code": "代码学习任务",
    "path": "学习路径任务",
    "tutor": "AI 导师任务",
    "video": "视频资源任务",
    "search": "资料搜索任务",
    "material": "资料处理任务",
}

_SAFE_ERROR_SUMMARIES = {
    "timeout": "任务执行超时；请检查依赖服务状态或稍后重试。",
    "connection": "任务执行时依赖服务连接异常。",
    "auth": "任务执行时服务认证失败。",
    "rate_limit": "任务执行受到服务限流或配额限制。",
    "validation": "任务执行时发生输入结构或结果校验错误。",
    "other": "任务执行失败；原始错误正文仅保留在服务端日志中。",
}


def safe_agent_name(agent_name: Optional[str], output_type: Optional[str] = None) -> str:
    value = (agent_name or output_type or "unknown").strip().lower()
    if value.startswith("workspace_"):
        value = value[len("workspace_"):]
    if value.startswith("tutor_action:"):
        value = "tutor"
    if value in _SAFE_AGENT_LABELS:
        return value
    # Keep the admin surface closed to arbitrary strings stored in historical rows.
    return "unknown"


def safe_agent_label(agent_name: Optional[str], output_type: Optional[str] = None) -> str:
    name = safe_agent_name(agent_name, output_type)
    return _SAFE_AGENT_LABELS.get(name, "AI 任务")


def safe_resource_type(output_type: Optional[str]) -> str:
    if not output_type:
        return "未记录资源类型"
    value = output_type.strip().lower()
    # Resource type is technical metadata, but reject free-form historical strings.
    known = {
        "document", "lecture", "question", "exam", "mindmap", "code", "video",
        "search", "chat_profile", "progress", "report", "quiz_result",
        "workspace_document", "workspace_exam", "workspace_mindmap",
        "workspace_code", "workspace_video",
    }
    if value.startswith("tutor_action:"):
        return "tutor_action"
    return value if value in known else "other"


def error_category(message: Optional[str]) -> Optional[str]:
    if not message:
        return None
    value = str(message).lower()
    if "timeout" in value or "timed out" in value:
        return "timeout"
    if "connection" in value or "refused" in value or "network" in value:
        return "connection"
    if "auth" in value or "unauthorized" in value or "forbidden" in value:
        return "auth"
    if "rate" in value or "quota" in value or "429" in value:
        return "rate_limit"
    if "validation" in value or "schema" in value or "invalid" in value:
        return "validation"
    return "other"


def safe_error_summary(message: Optional[str]) -> Optional[str]:
    category = error_category(message)
    if category is None:
        return None
    return _SAFE_ERROR_SUMMARIES[category]


def safe_task_summary(agent_name: Optional[str], output_type: Optional[str] = None) -> str:
    return f"{safe_agent_label(agent_name, output_type)}；学习内容已隐藏。"


def safe_result_summary(output_type: Optional[str]) -> str:
    return f"处理结果类型：{safe_resource_type(output_type)}；标题与正文已隐藏。"


def mask_email(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()
    if "@" not in raw:
        return "••••••"
    local, domain = raw.split("@", 1)
    local_masked = (local[:1] + "***") if local else "***"
    parts = domain.split(".")
    host = parts[0]
    host_masked = (host[:1] + "***") if host else "***"
    suffix = "." + ".".join(parts[1:]) if len(parts) > 1 else ""
    return f"{local_masked}@{host_masked}{suffix}"


def status_error_category(status: Optional[str]) -> Optional[str]:
    """Coarse admin-safe error category derived from technical task status only."""
    value = (status or "").strip().lower()
    return "task_failed" if value in {"failed", "error"} else None


def safe_status_error_summary(status: Optional[str]) -> Optional[str]:
    """Admin-safe failure summary without reading or materializing raw error text."""
    return "任务执行失败；原始错误正文仅保留在受控服务端日志中。" if status_error_category(status) else None
