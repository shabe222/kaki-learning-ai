import { getData } from './client';

export interface AgentInvocationItem {
  id: number;
  agent_name: string;
  agent_label: string;
  target?: string | null;
  status: string;
  duration_ms: number;
  input_summary: string;
  output_id?: number | null;
  output_type?: string | null;
  error_message?: string | null;
  created_at: string;
}

export interface AgentInvocationDetail extends AgentInvocationItem {
  output: {
    id: number | null;
    title: string;
    type: string;
    ext: string;
    size_label: string;
    chapter?: string;
    content: string;
    raw_data?: Record<string, unknown> | null;
  } | null;
}

export interface AgentRecordsListResponse {
  list: AgentInvocationItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AgentRecordsStats {
  online: number;
  busy: number;
  idle: number;
  total_agents: number;
  health: string;
  success_rate: number;
  total_invocations: number;
  success_invocations: number;
  failed_invocations: number;
}

export interface AgentTrendPoint {
  date: string;
  success: number;
  failed: number;
  total: number;
}

export interface AgentTrendResponse {
  series: AgentTrendPoint[];
}

export interface RecentOutputItem {
  invocation_id: number;
  output_id: number;
  title: string;
  type: string;
  ext: string;
  size_label: string;
  agent_label: string;
  created_at: string;
}

export interface RecentOutputsResponse {
  list: RecentOutputItem[];
}

export const agentRecordsApi = {
  list: (params: { status?: string; agent_name?: string; keyword?: string; page?: number; page_size?: number } = {}) =>
    getData<AgentRecordsListResponse>('/api/agent-records', {
      status: params.status,
      agent_name: params.agent_name,
      keyword: params.keyword,
      page: params.page ?? 1,
      page_size: params.page_size ?? 20
    }),
  stats: () => getData<AgentRecordsStats>('/api/agent-records/stats'),
  trend: () => getData<AgentTrendResponse>('/api/agent-records/trend'),
  recentOutputs: (limit = 6) => getData<RecentOutputsResponse>('/api/agent-records/recent-outputs', { limit }),
  detail: (id: number) => getData<AgentInvocationDetail>(`/api/agent-records/${id}`)
};
