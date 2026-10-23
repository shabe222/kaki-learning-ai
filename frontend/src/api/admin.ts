import { getData, postData, putData } from './client';

export interface AdminInfo {
  admin_id: number;
  username: string;
  display_name?: string | null;
  role: string;
  is_active: boolean;
}

interface AdminLoginPayload {
  token: string;
  admin: AdminInfo;
}

export interface CoreMetrics {
  total_users: number;
  active_users: number;
  disabled_users: number;
  today_new_users: number;
  week_new_users: number;
  profiled_users: number;
  profile_completion_rate: number | null;
  total_sessions: number;
  today_sessions: number;
  ai_interactions: number;
  week_ai_interactions: number;
  active_ai_users: number;
  total_tasks: number;
  today_tasks: number;
  running_tasks: number;
  week_success_tasks: number;
  week_failed_tasks: number;
  week_success_rate: number | null;
  total_items: number;
  week_artifacts: number;
  total_materials: number;
  error_count_24h: number;
}

export interface WeeklyTrendItem {
  date: string;
  new_users: number;
  task_count: number;
  ai_interactions: number;
  agent_tasks: number;
  artifacts: number;
  success_count: number;
  failed_count: number;
}

export interface SystemStatus {
  backend: string;
  database: string;
  llm_provider: string;
  llm_model: string;
  mode: string;
  python_version: string;
  api_routes: number;
  agent_count: number;
  error_count_24h: number;
}

export interface RecentTaskItem {
  id: number;
  user_id: number;
  agent_name: string;
  agent_label?: string | null;
  status: string;
  duration_ms?: number | null;
  created_at?: string | null;
  error_message?: string | null;
  record_kind?: string;
  tracking_status?: string;
  source_module?: string;
  artifact_id?: number | null;
  output_type?: string | null;
}

export interface DashboardData {
  core_metrics: CoreMetrics;
  weekly_trend: WeeklyTrendItem[];
  recent_tasks: RecentTaskItem[];
  system_status: SystemStatus;
}

export interface UserListItem {
  user_id: number;
  username: string;
  is_active: boolean;
  has_profile: boolean;
  profile_completeness: number | null;
  created_at: string | null;
  last_active_at: string | null;
  chat_count: number;
  task_count: number;
  item_count: number;
}

export interface UserListData {
  items: UserListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface UserDetail {
  user_id: number;
  username: string;
  email: string | null;
  is_active: boolean;
  created_at: string | null;
  last_active_at: string | null;
  has_profile: boolean;
  profile_completeness: number | null;
  chat_count: number;
  task_count: number;
  success_tasks: number;
  failed_tasks: number;
  item_count: number;
  material_count: number;
  activity_timeline: Array<{ event_type: string; event_label: string; time: string | null }>;
}

export interface AnnouncementItem {
  id: number;
  title: string;
  content: string;
  level: string;
  status: string;
  target_scope: string;
  published_at: string | null;
  expires_at: string | null;
  created_by: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface AnnouncementListData {
  items: AnnouncementItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface OperationRecord {
  id: number;
  user_id: number;
  agent_name: string;
  agent_label: string | null;
  status: string;
  duration_ms: number | null;
  created_at: string | null;
  output_type: string | null;
  error_category: string | null;
  record_kind: string;
  tracking_status: string;
  source_module: string;
  task_type: string;
  model: string;
  artifact_id: number | null;
  task_id: number | null;
}

export interface OperationListData {
  items: OperationRecord[];
  total: number;
  page: number;
  page_size: number;
}

export interface OperationsStats {
  total: number;
  day_total: number;
  day_success: number;
  day_failed: number;
  day_running: number;
  week_success_rate: number | null;
  avg_duration_ms: number | null;
  agent_distribution: Array<{ agent_name: string; agent_label: string | null; count: number }>;
  daily_trend: Array<{ date: string; total: number; success: number; failed: number }>;
  error_categories: Array<{ category: string; count: number }>;
  ai_interactions: number;
  agent_tasks: number;
  artifacts: number;
  active_users: number;
}

export interface OperationDetail {
  id: number;
  user_id: number;
  agent_name: string;
  agent_label: string | null;
  status: string;
  duration_ms: number | null;
  input_summary: string | null;
  output_type: string | null;
  error_message: string | null;
  created_at: string | null;
  source_module: string;
  task_type: string;
  model: string;
  artifact_id: number | null;
  tracking_status: string;
  execution_stages: string[];
  result_summary: string | null;
  privacy_note: string;
}

export interface SystemInfo {
  backend: string;
  database: string;
  llm_provider: string;
  llm_model: string;
  mode: string;
  python_version: string;
  api_routes: number;
  agent_count: number;
  registered_agent_count: number;
  agent_status: string;
  task_queue: { status: string; running: number; backend: string };
  file_storage: { status: string; backend: string; path: string };
  redis: { status: string; enabled: boolean };
  milvus: { status: string; enabled: boolean };
  minio: { status: string; enabled: boolean };
  last_health_check_at: string;
  recent_success_rate: number | null;
  recent_error_categories: Array<{ category: string; count: number }>;
  table_row_counts: Record<string, number>;
  version: string;
  configurable: Record<string, string>;
}

export interface SystemConfigItem {
  runtime: string;
  env_file?: string;
  effect?: string;
}

export interface SystemConfigData {
  LLM_PROVIDER: SystemConfigItem;
  LLM_MODEL: SystemConfigItem;
  MODE: SystemConfigItem;
  API_KEY: SystemConfigItem & { secret: boolean };
  editable: boolean;
  notice: string;
}

export interface AuditLogItem {
  id: number;
  admin_id: number;
  action: string;
  target_type: string;
  target_id: string | null;
  success: boolean;
  summary: string | null;
  created_at: string | null;
}

export interface AuditLogListData {
  items: AuditLogItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface UserAnnouncementData {
  items: Array<{
    id: number;
    title: string;
    content: string;
    level: string;
    published_at: string | null;
    expires_at: string | null;
    is_read: boolean;
  }>;
  unread_count: number;
}

export const adminApi = {
  async login(body: { username: string; password: string }) {
    const payload = await postData<AdminLoginPayload, typeof body>('/api/admin/login', body);
    return { token: payload.token, admin: payload.admin };
  },

  async me(signal?: AbortSignal) {
    return await getData<AdminInfo>('/api/admin/me', undefined, signal);
  },

  async dashboard(signal?: AbortSignal) {
    return await getData<DashboardData>('/api/admin/dashboard', undefined, signal);
  },

  async listUsers(params: Record<string, unknown>, signal?: AbortSignal) {
    return await getData<UserListData>('/api/admin/users', params, signal);
  },

  async getUserDetail(userId: number, signal?: AbortSignal) {
    return await getData<UserDetail>(`/api/admin/users/${userId}`, undefined, signal);
  },

  async setUserStatus(userId: number, isActive: boolean, reason:string) {
    return await putData<{ success: boolean; message: string }>(`/api/admin/users/${userId}/status`, { is_active: isActive, reason });
  },

  async listAnnouncements(params: Record<string, unknown>, signal?: AbortSignal) {
    return await getData<AnnouncementListData>('/api/admin/announcements', params, signal);
  },

  async createAnnouncement(body: { title: string; content: string; level: string }) {
    return await postData<AnnouncementItem>('/api/admin/announcements', body);
  },

  async updateAnnouncement(id: number, body: Record<string, unknown>) {
    return await putData<AnnouncementItem>(`/api/admin/announcements/${id}`, body);
  },

  async publishAnnouncement(id: number) {
    return await postData<AnnouncementItem>(`/api/admin/announcements/${id}/publish`);
  },

  async revokeAnnouncement(id: number) {
    return await postData<AnnouncementItem>(`/api/admin/announcements/${id}/revoke`);
  },

  async listOperations(params: Record<string, unknown>, signal?: AbortSignal) {
    return await getData<OperationListData>('/api/admin/operations', params, signal);
  },

  async getOperationsStats(signal?: AbortSignal) {
    return await getData<OperationsStats>('/api/admin/operations-stats', undefined, signal);
  },

  async getOperationDetail(id: number, signal?: AbortSignal) {
    return await getData<OperationDetail>(`/api/admin/operations/${id}`, undefined, signal);
  },

  async getSystemInfo(signal?: AbortSignal) {
    return await getData<SystemInfo>('/api/admin/system', undefined, signal);
  },

  async getSystemConfig(signal?: AbortSignal) {
    return await getData<SystemConfigData>('/api/admin/system/config', undefined, signal);
  },

  async updateSystemConfig(updates: Record<string, string>) {
    return await putData<{ success: boolean; message: string; needs_restart: boolean }>(
      '/api/admin/system/config', { updates }
    );
  },

  async getStorybookGlmKey(signal?: AbortSignal) {
    return await getData<{ configured: boolean; masked: string }>(
      '/api/admin/storybook/glm-key', undefined, signal
    );
  },

  async setStorybookGlmKey(glmKey: string) {
    return await putData<{ message: string }>(
      '/api/admin/storybook/glm-key', { glmKey }
    );
  },

  async clearCache() {
    return await postData<{ success: boolean; message: string }>('/api/admin/system/cache/clear');
  },

  async listAuditLogs(params: Record<string, unknown>, signal?: AbortSignal) {
    return await getData<AuditLogListData>('/api/admin/audit-logs', params, signal);
  },
};

export const userAnnouncementApi = {
  async getActive(signal?: AbortSignal) {
    return await getData<UserAnnouncementData>('/api/announcements/active', undefined, signal);
  },
  async markRead(announcementId: number) {
    return await postData<{ success: boolean }>(`/api/announcements/${announcementId}/read`);
  },
};
