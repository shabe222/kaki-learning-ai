import { getUserToken } from '../../../shared/auth';
import { useAuthStore } from '../../../stores/authStore';
import type {
  DaryaLongTermMemorySnapshot,
  DaryaConversationAction,
  DaryaMaterialSummary,
  DaryaSelfUnderstanding,
  DaryaMemoryControlRequest,
  DaryaOpportunityOpenResult,
  DaryaProactiveOpportunity,
  DaryaLearningLoopSnapshot,
  TideConversation,
  TideArtifactAction,
  TideArtifactActionControlRequest,
  TideArtifactActionControlResult,
  TideArtifactVersionProjection,
  TideCreatedConversation,
  TidePerspectiveControlRequest,
  TideSharedPerspective,
  TideUserContextControlRequest,
  TideResearchJob,
  TideUserContextSnapshot,
  TideWorkspaceObject,
  TideWorkspaceObjectControlRequest,
  TideStreamEvent,
  TideStreamRequest,
  TideStreamResult,
} from '../tideTypes';

const configuredBase = import.meta.env.VITE_BROWSER_API_BASE_URL || import.meta.env.VITE_API_BASE_URL || '';
const apiBase = String(configuredBase).replace(/\/$/, '');

interface SuccessEnvelope<T> {
  success: boolean;
  message?: string;
  data: T;
}

export interface DaryaTranscription {
  text: string;
  engine: string;
  language: string;
  duration_seconds: number;
}

export class TideTransportError extends Error {
  readonly code: 'AUTH' | 'HTTP' | 'PROTOCOL' | 'EARLY_EOF' | 'NETWORK';
  readonly status?: number;

  constructor(
    message: string,
    code: TideTransportError['code'],
    status?: number,
  ) {
    super(message);
    this.name = 'TideTransportError';
    this.code = code;
    this.status = status;
  }
}

function apiUrl(path: string) {
  return `${apiBase}${path}`;
}

function currentUserId(): number {
  const storeUser = useAuthStore.getState().user;
  if (storeUser?.userId) return storeUser.userId;

  try {
    const raw = localStorage.getItem('kaki_user');
    const parsed = raw ? JSON.parse(raw) as { userId?: number; user_id?: number; id?: number } : null;
    const value = parsed?.userId ?? parsed?.user_id ?? parsed?.id;
    if (value) return Number(value);
  } catch {
    // The shared auth bootstrap owns malformed-session recovery.
  }
  throw new TideTransportError('当前登录状态不可用，请重新登录。', 'AUTH', 401);
}

function authHeaders(contentType = false): HeadersInit {
  const token = getUserToken();
  if (!token) throw new TideTransportError('登录已失效，请重新登录。', 'AUTH', 401);
  return {
    Authorization: `Bearer ${token}`,
    ...(contentType ? { 'Content-Type': 'application/json; charset=utf-8' } : {}),
  };
}

async function responseMessage(response: Response, fallback: string) {
  try {
    const body = await response.json() as { detail?: unknown; message?: unknown; msg?: unknown };
    const nestedDetail = body.detail && typeof body.detail === 'object'
      ? body.detail as { message?: unknown }
      : null;
    const detail = typeof body.detail === 'string' ? body.detail
      : typeof nestedDetail?.message === 'string' ? nestedDetail.message
        : undefined;
    const message = typeof body.message === 'string' ? body.message : undefined;
    const msg = typeof body.msg === 'string' ? body.msg : undefined;
    return detail || message || msg || fallback;
  } catch {
    return fallback;
  }
}

async function checkedFetch(path: string, init: RequestInit) {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), init);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new TideTransportError('暂时无法连接学习服务，请检查网络后重试。', 'NETWORK');
  }

  if (response.status === 401) {
    useAuthStore.getState().clearSession();
    throw new TideTransportError('登录已失效，请重新登录。', 'AUTH', 401);
  }
  if (!response.ok) {
    const message = await responseMessage(response, `学习服务返回 ${response.status}`);
    throw new TideTransportError(message, 'HTTP', response.status);
  }
  return response;
}

async function requestData<T>(path: string, init: RequestInit): Promise<T> {
  const response = await checkedFetch(path, init);
  const body = await response.json() as SuccessEnvelope<T>;
  if (!body.success) throw new TideTransportError(body.message || '请求未完成。', 'PROTOCOL');
  return body.data;
}

function parseFrame(frame: string): TideStreamEvent | null {
  const payload = frame
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n')
    .trim();

  if (!payload || payload === '[DONE]') return payload === '[DONE]' ? { type: 'done' } : null;
  try {
    return JSON.parse(payload) as TideStreamEvent;
  } catch {
    throw new TideTransportError('收到了无法识别的流式响应。', 'PROTOCOL');
  }
}

export const tideChatApi = {
  currentUserId,

  listConversations(threadId?: string) {
    const userId = currentUserId();
    const query = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : '';
    return requestData<TideConversation[]>(`/api/chat/history/${userId}${query}`, {
      method: 'GET',
      headers: authHeaders(),
    });
  },

  createConversation() {
    return requestData<TideCreatedConversation>('/api/chat/conversations', {
      method: 'POST',
      headers: authHeaders(true),
      body: '{}',
    });
  },

  generatePresentation(body: {
    thread_id: string; topic: string; delivery_mode?: string; theme?: string;
    level?: string; learning_goal?: string; confirmed: boolean;
  }) {
    return requestData<{
      artifact_id: number; artifact_version_id?: number; kind?: string;
      title?: string; slide_count?: number; presentation_theme?: string;
      summary?: string;
    }>(`/api/chat/tasks/presentation`, {
      method: 'POST',
      headers: authHeaders(true),
      body: JSON.stringify({
        delivery_mode: 'presentation_only',
        theme: 'kaki',
        ...body,
      }),
    });
  },

  /**
   * `voice` / `rate` / `pitch` 以前写死在这里，而后端一直收这几个参数——
   * 所以「换一个声音、调快慢」以前产品里没有任何地方能做到。
   * 不传时行为与从前一致（默认音色、正常语速与音调）。
   */
  async synthesizeSpeech(
    text: string,
    options: { voice?: string; rate?: number; pitch?: number } = {},
    signal?: AbortSignal,
  ): Promise<Blob> {
    const response = await checkedFetch('/api/video/speech', {
      method: 'POST',
      headers: authHeaders(true),
      body: JSON.stringify({
        text: text.slice(0, 1200),
        voice: options.voice || 'zh-CN-XiaoxiaoNeural',
        rate: options.rate ?? 0,
        pitch: options.pitch ?? 0,
      }),
      signal,
    });
    return response.blob();
  },

  transcribeAudio(audio: Blob, signal?: AbortSignal) {
    const mime = audio.type.toLowerCase();
    const extension = mime.includes('ogg') ? 'ogg'
      : mime.includes('mp4') ? 'mp4'
        : mime.includes('mpeg') ? 'mp3'
          : mime.includes('wav') ? 'wav'
            : 'webm';
    const form = new FormData();
    form.append('file', audio, `darya-recording.${extension}`);
    return requestData<DaryaTranscription>('/api/chat/input/transcribe', {
      method: 'POST',
      headers: authHeaders(),
      body: form,
      signal,
    });
  },

  getSharedPerspective(threadId: string) {
    return requestData<TideSharedPerspective>(
      `/api/tide/shared-perspective/${encodeURIComponent(threadId)}`,
      { method: 'GET', headers: authHeaders() },
    );
  },

  getLearningLoop(threadId: string) {
    return requestData<DaryaLearningLoopSnapshot>(
      `/api/tide/learning-loop/${encodeURIComponent(threadId)}`,
      { method: 'GET', headers: authHeaders() },
    );
  },

  controlSharedPerspective(threadId: string, request: TidePerspectiveControlRequest) {
    return requestData<TideSharedPerspective>(
      `/api/tide/shared-perspective/${encodeURIComponent(threadId)}`,
      {
        method: 'PATCH',
        headers: authHeaders(true),
        body: JSON.stringify(request),
      },
    );
  },

  getUserContext(threadId: string) {
    return requestData<TideUserContextSnapshot>(
      `/api/tide/user-context/${encodeURIComponent(threadId)}`,
      { method: 'GET', headers: authHeaders() },
    );
  },

  uploadUserContextDocument(threadId: string, file: File) {
    const form = new FormData();
    form.append('file', file, file.name);
    return requestData<TideUserContextSnapshot>(
      `/api/tide/user-context/${encodeURIComponent(threadId)}/documents`,
      { method: 'POST', headers: authHeaders(), body: form },
    );
  },

  /**
   * 把一个网页导入成资料来源。
   *
   * 服务端会拒内网与本机地址，并把正文抓成 Markdown 走已有的资料入库链路 ——
   * 所以它出现在「我的资料」里的形态和上传一份文件完全一样。
   */
  ingestUserContextUrl(threadId: string, url: string) {
    return requestData<TideUserContextSnapshot>(
      `/api/tide/user-context/${encodeURIComponent(threadId)}/url`,
      { method: 'POST', headers: authHeaders(true), body: JSON.stringify({ url }) },
    );
  },

  startResearch(threadId: string, question: string) {
    return requestData<TideResearchJob>(`/api/chat/tasks/research`, {
      method: 'POST',
      headers: authHeaders(true),
      body: JSON.stringify({ thread_id: threadId, question, confirmed: true }),
    });
  },

  getResearch(jobId: number) {
    return requestData<TideResearchJob>(`/api/chat/tasks/research/${jobId}`, {
      method: 'GET',
      headers: authHeaders(),
    });
  },

  retryResearch(jobId: number) {
    return requestData<TideResearchJob>(`/api/chat/tasks/research/${jobId}/retry`, {
      method: 'POST',
      headers: authHeaders(true),
    });
  },

  /**
   * 这个会话里已经有的研究任务，新的在前。
   *
   * 收起研究条、或者刷新页面之后，learner 需要一条回来的路；任务一直在服务端，
   * 缺的就是把它列出来。
   */
  listResearch(threadId: string) {
    return requestData<TideResearchJob[]>(
      `/api/chat/tasks/research?thread_id=${encodeURIComponent(threadId)}`,
      { method: 'GET', headers: authHeaders() },
    );
  },

  controlUserContextClaim(
    threadId: string,
    claimId: number,
    request: TideUserContextControlRequest,
  ) {
    return requestData<TideUserContextSnapshot>(
      `/api/tide/user-context/${encodeURIComponent(threadId)}/claims/${claimId}`,
      {
        method: 'PATCH',
        headers: authHeaders(true),
        body: JSON.stringify(request),
      },
    );
  },

  getLongTermMemories(query?: string, all = false) {
    const params = new URLSearchParams();
    if (query) params.set('query', query);
    if (all) params.set('all', 'true');
    const suffix = params.size ? `?${params.toString()}` : '';
    return requestData<DaryaLongTermMemorySnapshot>(
      `/api/tide/long-term-memory${suffix}`,
      { method: 'GET', headers: authHeaders() },
    );
  },

  promoteContextClaim(threadId: string, claimId: number) {
    return requestData<DaryaLongTermMemorySnapshot>(
      '/api/tide/long-term-memory/from-context-claim',
      {
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({ thread_id: threadId, claim_id: claimId }),
      },
    );
  },

  controlLongTermMemory(memoryId: number, request: DaryaMemoryControlRequest) {
    return requestData<DaryaLongTermMemorySnapshot>(
      `/api/tide/long-term-memory/${memoryId}`,
      {
        method: 'PATCH',
        headers: authHeaders(true),
        body: JSON.stringify(request),
      },
    );
  },

  getCurrentOpportunity(threadId: string) {
    return requestData<DaryaProactiveOpportunity | null>(
      `/api/chat/tutor/proactive/opportunities/current?conversation_id=${encodeURIComponent(threadId)}`,
      { method: 'GET', headers: authHeaders() },
    );
  },

  laterOpportunity(messageId: string, hours = 12) {
    return requestData<DaryaProactiveOpportunity>(
      `/api/chat/tutor/proactive/opportunities/${encodeURIComponent(messageId)}/later`,
      {
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({ hours }),
      },
    );
  },

  dismissOpportunity(messageId: string) {
    return requestData<DaryaProactiveOpportunity>(
      `/api/chat/tutor/proactive/messages/${encodeURIComponent(messageId)}/dismiss`,
      { method: 'POST', headers: authHeaders(true), body: '{}' },
    );
  },

  openOpportunity(messageId: string) {
    return requestData<DaryaOpportunityOpenResult>(
      `/api/chat/tutor/proactive/messages/${encodeURIComponent(messageId)}/action`,
      { method: 'POST', headers: authHeaders(true), body: '{}' },
    );
  },

  getWorkspaceObject(threadId: string) {
    return requestData<TideWorkspaceObject | null>(
      `/api/tide/workspace-object/${encodeURIComponent(threadId)}`,
      { method: 'GET', headers: authHeaders() },
    );
  },

  controlWorkspaceObject(threadId: string, request: TideWorkspaceObjectControlRequest) {
    return requestData<TideWorkspaceObject>(
      `/api/tide/workspace-object/${encodeURIComponent(threadId)}`,
      {
        method: 'PATCH',
        headers: authHeaders(true),
        body: JSON.stringify(request),
      },
    );
  },

  getArtifactAction(threadId: string) {
    return requestData<TideArtifactAction | null>(
      `/api/tide/artifact-action/${encodeURIComponent(threadId)}`,
      { method: 'GET', headers: authHeaders() },
    );
  },

  analyzeArtifact(threadId: string, instruction: string, requestKey: string) {
    return requestData<TideArtifactAction | null>(
      `/api/tide/artifact-action/${encodeURIComponent(threadId)}/analyze`,
      {
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({ instruction, request_key: requestKey }),
      },
    );
  },

  controlArtifactAction(
    threadId: string,
    actionId: number,
    request: TideArtifactActionControlRequest,
  ) {
    return requestData<TideArtifactActionControlResult>(
      `/api/tide/artifact-action/${encodeURIComponent(threadId)}/${actionId}`,
      {
        method: 'PATCH',
        headers: authHeaders(true),
        body: JSON.stringify(request),
      },
    );
  },

  getArtifactVersion(artifactId: number, versionId: number) {
    return requestData<TideArtifactVersionProjection>(
      `/api/items/${artifactId}?version_id=${versionId}`,
      { method: 'GET', headers: authHeaders() },
    );
  },

  getConversationCapabilityAction(actionKey: string) {
    return requestData<DaryaConversationAction>(
      `/api/chat/capability-actions/${encodeURIComponent(actionKey)}`,
      { method: 'GET', headers: authHeaders() },
    );
  },

  prepareConversationCapabilityAction(actionKey: string, theme?: string) {
    return requestData<DaryaConversationAction>(
      `/api/chat/capability-actions/${encodeURIComponent(actionKey)}/prepare`,
      { method: 'POST', headers: authHeaders(true), body: JSON.stringify({ theme: theme ?? '' }) },
    );
  },

  reviewConversationCapabilityAction(actionKey: string) {
    return requestData<DaryaConversationAction>(
      `/api/chat/capability-actions/${encodeURIComponent(actionKey)}/review`,
      { method: 'POST', headers: authHeaders(true), body: '{}' },
    );
  },

  listMaterials(category = 'all') {
    const query = new URLSearchParams({ scope: 'all', artifact_type: category });
    return requestData<DaryaMaterialSummary[]>(
      `/api/chat/artifacts?${query.toString()}`,
      { method: 'GET', headers: authHeaders() },
    );
  },

  getSelfUnderstanding() {
    return requestData<DaryaSelfUnderstanding>(
      '/api/darya/self-understanding',
      { method: 'GET', headers: authHeaders() },
    );
  },

  updateUnderstandingMemory(
    memoryId: number,
    request: { action: 'correct' | 'forget'; expected_revision: number; correction?: string | null },
  ) {
    return requestData<DaryaSelfUnderstanding>(
      `/api/darya/self-understanding/memories/${memoryId}`,
      { method: 'PATCH', headers: authHeaders(true), body: JSON.stringify(request) },
    );
  },

  updateImpression(impressionId: number, action: 'accurate' | 'modify' | 'inaccurate', summary?: string) {
    return requestData<unknown>(
      `/api/chat/impressions/${impressionId}`,
      {
        method: 'PATCH',
        headers: authHeaders(true),
        body: JSON.stringify({ action, ...(summary ? { summary } : {}) }),
      },
    );
  },

  async stream(
    request: TideStreamRequest,
    onEvent: (event: TideStreamEvent) => void,
    signal: AbortSignal,
  ): Promise<TideStreamResult> {
    const response = await checkedFetch('/api/chat/stream', {
      method: 'POST',
      headers: authHeaders(true),
      body: JSON.stringify(request),
      signal,
    });
    if (!response.body) throw new TideTransportError('当前浏览器无法读取流式响应。', 'PROTOCOL');

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let sawDone = false;
    let terminal: TideStreamResult = {};

    const consume = (frame: string) => {
      const event = parseFrame(frame);
      if (!event) return;
      if (event.type === 'error') {
        const message = typeof event.message === 'string' ? event.message
          : typeof event.detail === 'string' ? event.detail
            : typeof event.error === 'string' ? event.error
              : '回答生成失败。';
        throw new TideTransportError(message, 'HTTP');
      }
      if (event.type === 'done') {
        sawDone = true;
        terminal = {
          requestId: typeof event.request_id === 'string' ? event.request_id : undefined,
          assistantMessageId: typeof event.assistant_message_id === 'string' ? event.assistant_message_id : undefined,
        };
      }
      onEvent(event);
    };

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n');
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        consume(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf('\n\n');
      }
      if (done) break;
    }

    if (buffer.trim()) consume(buffer);
    if (!sawDone) {
      throw new TideTransportError('连接在回答完成前中断，可以保留问题后重试。', 'EARLY_EOF');
    }
    return terminal;
  },
};
