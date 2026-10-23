import { getData, http, postData } from './client';
import { useAuthStore } from '../stores/authStore';

// 真实后端契约：POST /api/chat (backend/routers/chat.py:60-130)
export interface QaChatHistoryMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface QaSendRequest {
  message: string;
  history: QaChatHistoryMessage[];
  thread_id: string;
  user_id?: number;
}

export interface QaSendResponse {
  profile?: unknown;
  document?: unknown;
  questions?: unknown;
  mindmap?: unknown;
  code_examples?: unknown;
  learning_path?: unknown;
  tutor_response?: string;
  summary?: string;
  execution_log?: unknown[];
  errors?: unknown[];
}

// 真实后端契约：GET /api/chat/history/{user_id} (backend/routers/chat.py:27-57)
export interface QaConversation {
  id: number;
  thread_id: string;
  messages: QaChatHistoryMessage[];
  message_count: number;
  created_at: string;
  last_message_at: string;
}

function currentUserId(): number {
  const { user } = useAuthStore.getState();
  if (user?.userId) return user.userId;
  const raw = localStorage.getItem('kaki_user');
  if (!raw) throw new Error('当前未登录');
  try {
    const parsed = JSON.parse(raw) as { userId?: number; user_id?: number; id?: number };
    const id = parsed.userId ?? parsed.user_id ?? parsed.id;
    if (!id) throw new Error('当前未登录');
    return id;
  } catch {
    throw new Error('当前未登录');
  }
}

/**
 * POST /api/chat 的工作流需要运行 LangGraph 全流程（intake + profile + parallel agents + summary），
 * 实际耗时通常 30-60 秒，远超默认 axios 20s 超时。本接口单独使用 90s 超时。
 */
async function postChatLongTimeout<T, B = unknown>(url: string, body: B): Promise<T> {
  const response = await http.post<T>(url, body, { timeout: 90_000 });
  return response.data as T;
}

export const qaApi = {
  /**
   * 发送消息到 /api/chat
   * 后端在 chat.py:60-130 强制使用 token 中的 user_id 覆盖 request.user_id
   * 返回 data.tutor_response 是默认主回答；其他字段（document/questions/...）保留以便未来 Phase 使用
   */
  async send(input: { message: string; thread_id: string; history: QaChatHistoryMessage[] }): Promise<QaSendResponse> {
    const userId = currentUserId();
    const body: QaSendRequest = {
      message: input.message,
      history: input.history,
      thread_id: input.thread_id,
      user_id: userId,
    };
    // 长超时：直接走 http（不走 unwrapData），拿到完整响应体
    type Envelope = { success: boolean; data: QaSendResponse; message?: string };
    const envelope = await postChatLongTimeout<Envelope, QaSendRequest>('/api/chat', body);
    if (!envelope.success) {
      throw new Error(envelope.message || '后端返回失败');
    }
    return envelope.data;
  },

  /**
   * 获取当前用户最近 20 条会话
   * 后端按 last_message_at desc 排序，limit 20（chat.py:39）
   */
  async listConversations(): Promise<QaConversation[]> {
    const userId = currentUserId();
    return getData<QaConversation[]>(`/api/chat/history/${userId}`);
  },
};

/**
 * 生成新会话 thread_id。
 * 客户端生成；后端不限制格式，仅作为会话分组键。
 */
export function newThreadId(): string {
  return `qa-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
