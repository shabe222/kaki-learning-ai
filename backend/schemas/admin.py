"""管理端请求/响应 Schema"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ===== 认证 =====

class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminInfoResponse(BaseModel):
    admin_id: int
    username: str
    display_name: Optional[str] = None
    role: str
    is_active: bool


class AdminLoginResponse(BaseModel):
    token: str
    admin: AdminInfoResponse


# ===== Dashboard =====

class CoreMetricsResponse(BaseModel):
    total_users: int
    active_users: int
    disabled_users: int
    today_new_users: int
    week_new_users: int
    profiled_users: int
    profile_completion_rate: Optional[float] = None
    total_sessions: int
    today_sessions: int
    total_tasks: int
    today_tasks: int
    running_tasks: int
    week_success_tasks: int
    week_failed_tasks: int
    week_success_rate: Optional[float] = None
    total_items: int
    total_materials: int
    error_count_24h: int


class WeeklyTrendItem(BaseModel):
    date: str
    new_users: int
    task_count: int
    success_count: int
    failed_count: int


class SystemStatusResponse(BaseModel):
    backend: str
    database: str
    llm_provider: str
    llm_model: str
    mode: str
    python_version: str
    api_routes: int
    agent_count: int
    error_count_24h: int


class RecentTaskItem(BaseModel):
    id: int
    user_id: int
    agent_name: str
    agent_label: Optional[str] = None
    status: str
    duration_ms: Optional[int] = None
    created_at: Optional[str] = None
    error_message: Optional[str] = None


class DashboardResponse(BaseModel):
    core_metrics: CoreMetricsResponse
    weekly_trend: List[WeeklyTrendItem]
    recent_tasks: List[RecentTaskItem]
    system_status: SystemStatusResponse


# ===== 用户管理 =====

class AdminUserListItem(BaseModel):
    user_id: int
    username: str
    is_active: bool
    has_profile: bool
    profile_completeness: Optional[float] = None
    created_at: Optional[str] = None
    last_active_at: Optional[str] = None
    chat_count: int
    task_count: int
    item_count: int


class AdminUserListResponse(BaseModel):
    items: List[AdminUserListItem]
    total: int
    page: int
    page_size: int


class AdminUserDetailResponse(BaseModel):
    user_id: int
    username: str
    email: Optional[str] = None
    is_active: bool
    created_at: Optional[str] = None
    last_active_at: Optional[str] = None
    has_profile: bool
    profile_completeness: Optional[float] = None
    chat_count: int
    task_count: int
    success_tasks: int
    failed_tasks: int
    item_count: int
    material_count: int
    activity_timeline: List[dict]


class AdminUserStatusRequest(BaseModel):
    is_active: bool
    reason: str = Field(min_length=3, max_length=300)


# ===== 公告 =====

class AnnouncementCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    level: str = Field(default="info")
    expires_at: Optional[datetime] = None


class AnnouncementUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, min_length=1)
    level: Optional[str] = None
    expires_at: Optional[datetime] = None


class AnnouncementResponse(BaseModel):
    id: int
    title: str
    content: str
    level: str
    status: str
    target_scope: str
    published_at: Optional[str] = None
    expires_at: Optional[str] = None
    created_by: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AnnouncementListResponse(BaseModel):
    items: List[AnnouncementResponse]
    total: int
    page: int
    page_size: int


class UserAnnouncementItem(BaseModel):
    id: int
    title: str
    content: str
    level: str
    published_at: Optional[str] = None
    expires_at: Optional[str] = None
    is_read: bool


# ===== 运营记录 =====

class OperationRecordItem(BaseModel):
    id: int
    user_id: int
    agent_name: str
    agent_label: Optional[str] = None
    status: str
    duration_ms: Optional[int] = None
    created_at: Optional[str] = None
    output_type: Optional[str] = None
    error_category: Optional[str] = None


class OperationListResponse(BaseModel):
    items: List[OperationRecordItem]
    total: int
    page: int
    page_size: int


class OperationDetailResponse(BaseModel):
    id: int
    user_id: int
    agent_name: str
    agent_label: Optional[str] = None
    status: str
    duration_ms: Optional[int] = None
    input_summary: Optional[str] = None
    output_type: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None


# ===== 系统状态 =====

class SystemInfoResponse(BaseModel):
    backend: str
    database: str
    llm_provider: str
    llm_model: str
    mode: str
    python_version: str
    api_routes: int
    agent_count: int
    recent_success_rate: Optional[float] = None
    recent_error_categories: List[dict]
    table_row_counts: dict
    version: str


# ===== 审计日志 =====

class AuditLogItem(BaseModel):
    id: int
    admin_id: int
    action: str
    target_type: str
    target_id: Optional[str] = None
    success: bool
    summary: Optional[str] = None
    created_at: Optional[str] = None


class AuditLogListResponse(BaseModel):
    items: List[AuditLogItem]
    total: int
    page: int
    page_size: int
