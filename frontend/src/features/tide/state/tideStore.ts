import { create } from 'zustand';
import { tideChatApi, TideTransportError } from '../chat/tideChatApi';
import { readDaryaTheme } from '../theme/daryaTheme';
import type {
  DaryaLongTermMemorySnapshot,
  DaryaConversationAction,
  DaryaSearchGrounding,
  DaryaMemoryControlRequest,
  DaryaOpportunityOpenResult,
  DaryaProactiveOpportunity,
  TideArtifactAction,
  TideArtifactActionControlRequest,
  TideArtifactWorkbench,
  TideConversation,
  TideHistoryMessage,
  TideInteractionContext,
  TideMessage,
  TidePerspectiveControlRequest,
  TideSharedPerspective,
  TideStreamEvent,
  TideUserContextControlRequest,
  TideResearchJob,
  TideUserContextSnapshot,
  TideWorkspaceObject,
  TideWorkspaceObjectControlRequest,
} from '../tideTypes';
import { buildArtifactWorkbench } from '../workbench/tideArtifactWorkbench';

const TIDE_THREAD_KEY = 'tide_current_thread_id';
const LEGACY_THREAD_KEY = 'ai_workspace_test_thread_id';
const TIDE_VIEW_STATE_KEY = 'tide_view_state_v1';

type LoadState = 'idle' | 'loading' | 'ready' | 'error';
type StreamState = 'idle' | 'connecting' | 'streaming' | 'stopped' | 'error';
type PerspectiveState = 'idle' | 'loading' | 'ready' | 'saving' | 'error';
type WorkspaceObjectState = 'idle' | 'loading' | 'ready' | 'saving' | 'error';
type ArtifactActionState = 'idle' | 'loading' | 'ready' | 'saving' | 'error';
type ArtifactWorkbenchState = 'idle' | 'loading' | 'ready' | 'error';
type UserContextState = 'idle' | 'loading' | 'ready' | 'uploading' | 'saving' | 'error';
type LongTermMemoryState = 'idle' | 'ready' | 'saving' | 'error';
type OpportunityState = 'idle' | 'loading' | 'ready' | 'saving' | 'error';

interface LastAttempt {
  text: string;
  threadId: string;
  messageId: string;
  userMessageId: string;
  assistantMessageId: string;
}

interface TideState {
  // SOURCES: authenticated Tide APIs and validated SSE events for currentThreadId.
  // CONSUMERS: TideContextRail and the chat prompt assembled by the backend.
  // PERSISTENCE: server database only; browser storage keeps just the thread pointer.
  conversations: TideConversation[];
  currentThreadId: string | null;
  messages: TideMessage[];
  draft: string;
  scrollTop: number;
  loadState: LoadState;
  streamState: StreamState;
  error: string | null;
  railOpen: boolean;
  interactionContext: TideInteractionContext | null;
  sharedPerspective: TideSharedPerspective | null;
  perspectiveState: PerspectiveState;
  perspectiveError: string | null;
  workspaceObject: TideWorkspaceObject | null;
  workspaceObjectState: WorkspaceObjectState;
  workspaceObjectError: string | null;
  artifactAction: TideArtifactAction | null;
  artifactActionState: ArtifactActionState;
  artifactActionError: string | null;
  artifactWorkbench: TideArtifactWorkbench | null;
  artifactWorkbenchState: ArtifactWorkbenchState;
  artifactWorkbenchError: string | null;
  userContext: TideUserContextSnapshot | null;
  userContextState: UserContextState;
  userContextError: string | null;
  /** 最近一次研究任务。它比一轮对话活得久，所以要挂在 store 上而不是组件里。 */
  researchJob: TideResearchJob | null;
  researchState: 'idle' | 'starting' | 'watching' | 'error';
  researchError: string | null;
  /** 研究条收起时留一行，别把引用一起丢掉——丢掉就再也回不来了。 */
  researchCollapsed: boolean;
  longTermMemory: DaryaLongTermMemorySnapshot | null;
  longTermMemoryState: LongTermMemoryState;
  longTermMemoryError: string | null;
  opportunity: DaryaProactiveOpportunity | null;
  opportunityState: OpportunityState;
  opportunityError: string | null;
  activeController: AbortController | null;
  // Synchronous send latch: the first message still awaits createThread()
  // before activeController exists, and a double activation during that
  // round-trip would otherwise start a duplicate paid stream.
  sendInFlight: boolean;
  lastAttempt: LastAttempt | null;
  /**
   * 这一会话里真的在浏览器中长出来过的助手消息（发出去的每一轮，含落库后的新 id）。
   *
   * 翻历史翻回来的消息不在里面 —— 一条回复的"边讲边画"是当场发生的事，刷新页面后
   * 把上一轮重新演一遍，那不是回放，那是假直播。
   */
  liveMessageIds: string[];
  hydrate: (force?: boolean) => Promise<void>;
  selectThread: (threadId: string) => Promise<void>;
  createThread: () => Promise<string>;
  setDraft: (draft: string) => void;
  setScrollTop: (scrollTop: number) => void;
  setRailOpen: (open: boolean) => void;
  refreshSharedPerspective: (threadId?: string | null) => Promise<void>;
  controlSharedPerspective: (
    request: Omit<TidePerspectiveControlRequest, 'expected_revision'>,
  ) => Promise<void>;
  refreshWorkspaceObject: (threadId?: string | null) => Promise<void>;
  controlWorkspaceObject: (
    request: Omit<TideWorkspaceObjectControlRequest, 'expected_revision'>,
  ) => Promise<void>;
  refreshArtifactAction: (threadId?: string | null) => Promise<void>;
  refreshArtifactWorkbench: (
    threadId?: string | null,
    workspaceObject?: TideWorkspaceObject | null,
    artifactAction?: TideArtifactAction | null,
  ) => Promise<void>;
  controlArtifactAction: (
    control: TideArtifactActionControlRequest['control'],
  ) => Promise<void>;
  refreshUserContext: (threadId?: string | null) => Promise<void>;
  uploadUserContextDocument: (file: File) => Promise<void>;
  ingestUserContextUrl: (url: string) => Promise<void>;
  startResearch: (question: string) => Promise<void>;
  refreshResearch: (jobId?: number) => Promise<void>;
  retryResearch: (jobId: number) => Promise<void>;
  /** 把当前会话里已有的研究任务接回来（收起过、或刚刷新过）。 */
  restoreResearch: (threadId?: string) => Promise<void>;
  setResearchCollapsed: (collapsed: boolean) => void;
  controlUserContextClaim: (
    claimId: number,
    request: Omit<TideUserContextControlRequest, 'expected_revision'>,
  ) => Promise<void>;
  promoteContextClaimToMemory: (claimId: number) => Promise<void>;
  controlLongTermMemory: (
    memoryId: number,
    request: Omit<DaryaMemoryControlRequest, 'expected_revision'>,
  ) => Promise<void>;
  refreshOpportunity: (threadId?: string | null) => Promise<void>;
  laterOpportunity: () => Promise<void>;
  dismissOpportunity: () => Promise<void>;
  openOpportunity: () => Promise<DaryaOpportunityOpenResult | null>;
  advanceConversationAction: (action: DaryaConversationAction) => Promise<DaryaConversationAction>;
  /**
   * `speech` 表示这一轮会被朗读（语音通话）。省略即打字对话，行为与从前完全一致。
   */
  send: (text?: string, retry?: boolean, speech?: boolean) => Promise<void>;
  retryLast: () => Promise<void>;
  abort: () => void;
}

let hydrationPromise: Promise<void> | null = null;
const activeConversationActions = new Set<string>();

interface TideViewState {
  threadId: string | null;
  draft: string;
  scrollTop: number;
}

function readViewState(): TideViewState {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(TIDE_VIEW_STATE_KEY) || 'null') as Partial<TideViewState> | null;
    return {
      threadId: typeof parsed?.threadId === 'string' ? parsed.threadId : null,
      draft: typeof parsed?.draft === 'string' ? parsed.draft : '',
      scrollTop: typeof parsed?.scrollTop === 'number' && Number.isFinite(parsed.scrollTop) ? parsed.scrollTop : 0,
    };
  } catch {
    return { threadId: null, draft: '', scrollTop: 0 };
  }
}

function persistViewState(next: Partial<TideViewState>) {
  try {
    sessionStorage.setItem(TIDE_VIEW_STATE_KEY, JSON.stringify({ ...readViewState(), ...next }));
  } catch {
    // The in-memory Zustand projection remains usable when storage is unavailable.
  }
}

function persistThread(threadId: string | null) {
  if (threadId) localStorage.setItem(TIDE_THREAD_KEY, threadId);
  else localStorage.removeItem(TIDE_THREAD_KEY);
}

function candidateThreadId() {
  const tidePointer = localStorage.getItem(TIDE_THREAD_KEY);
  if (tidePointer) return tidePointer;
  try {
    return sessionStorage.getItem(LEGACY_THREAD_KEY);
  } catch {
    return null;
  }
}

function messageProjection(threadId: string, messages: TideHistoryMessage[]): TideMessage[] {
  return (messages || [])
    .filter((message) => message && typeof message.content === 'string')
    .map((message, index) => ({
      id: message.id || `history-${threadId}-${index}-${message.role}`,
      role: message.role,
      content: message.content,
      status: 'complete' as const,
      conversationAction: message.conversation_action || null,
      orchestration_trace: message.orchestration_trace,
      searchEvidence: message.search_evidence || null,
      searchStatus: null,
      artifact: message.artifact || null,
      metadata: message.metadata || (message.cognition_orchestration || message.learning_loop_domain_id || message.teaching_method_label
        ? {
            cognition_orchestration: message.cognition_orchestration || null,
            learning_loop_domain_id: message.learning_loop_domain_id || null,
            teaching_method_label: message.teaching_method_label || null,
          }
        : null),
    }));
}

function messageFor(error: unknown) {
  if (error instanceof TideTransportError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return '这次连接没有完成，请稍后重试。';
}

function titleFrom(messages: TideHistoryMessage[]) {
  const first = messages.find((message) => message.role === 'user' && message.content.trim());
  return first?.content.trim().slice(0, 32) || '新对话';
}

function responseStyleFor(message: string): 'concise' | 'detailed' {
  return /详细|深入|展开|推导|步骤|完整|系统|分析|计划|清单|公式|证明|代码|实现|debug|code|explain/i.test(message)
    ? 'detailed'
    : 'concise';
}

function isTideInteractionContext(event: TideStreamEvent): event is TideStreamEvent & TideInteractionContext {
  if (event.type !== 'tide_context') return false;
  const candidate = event as Record<string, unknown>;
  const snapshot = candidate.snapshot as Record<string, unknown> | null;
  if (!snapshot) return false;
  return ['EXECUTE', 'ORIENT', 'EXPLAIN', 'CLARIFY'].includes(String(candidate.mode))
    && typeof candidate.reason === 'string'
    && [0, 1, 2].includes(Number(candidate.proactive_level))
    && typeof candidate.sidecar === 'boolean'
    && typeof snapshot.goal === 'string'
    && typeof snapshot.current_stage === 'string'
    && ['high', 'normal'].includes(String(snapshot.urgency))
    && typeof snapshot.risk === 'string'
    && typeof snapshot.recommended_next_action === 'string';
}

function isSharedPerspectiveEvent(
  event: TideStreamEvent,
): event is TideStreamEvent & { type: 'shared_perspective'; snapshot: TideSharedPerspective } {
  const candidate = event as { type?: unknown; snapshot?: unknown };
  const snapshot = candidate.snapshot as Partial<TideSharedPerspective> | null;
  return candidate.type === 'shared_perspective'
    && typeof snapshot === 'object'
    && snapshot !== null
    && typeof snapshot.thread_id === 'string'
    && typeof snapshot.revision === 'number'
    && typeof snapshot.fields === 'object';
}

function isWorkspaceObjectEvent(
  event: TideStreamEvent,
): event is TideStreamEvent & { type: 'workspace_object'; object: TideWorkspaceObject } {
  const candidate = event as { type?: unknown; object?: unknown };
  const object = candidate.object as Partial<TideWorkspaceObject> | null;
  return candidate.type === 'workspace_object'
    && typeof object === 'object'
    && object !== null
    && typeof object.id === 'number'
    && typeof object.thread_id === 'string'
    && typeof object.title === 'string'
    && typeof object.revision === 'number';
}

function isArtifactActionEvent(
  event: TideStreamEvent,
): event is TideStreamEvent & { type: 'artifact_action'; action: TideArtifactAction } {
  const candidate = event as { type?: unknown; action?: unknown };
  const action = candidate.action as Partial<TideArtifactAction> | null;
  return candidate.type === 'artifact_action'
    && typeof action === 'object'
    && action !== null
    && typeof action.id === 'number'
    && typeof action.thread_id === 'string'
    && typeof action.reason === 'string'
    && typeof action.status === 'string'
    && typeof action.revision === 'number';
}

function isConversationActionEvent(
  event: TideStreamEvent,
): event is TideStreamEvent & { type: 'conversation_action'; action: DaryaConversationAction } {
  const candidate = event as { type?: unknown; action?: unknown };
  const action = candidate.action as Partial<DaryaConversationAction> | null;
  return candidate.type === 'conversation_action'
    && typeof action === 'object'
    && action !== null
    && typeof action.action_key === 'string'
    && typeof action.assistant_message_id === 'string'
    && typeof action.status === 'string'
    && Array.isArray(action.trace);
}

function isSearchGroundingEvent(
  event: TideStreamEvent,
): event is TideStreamEvent & { type: 'search_grounding'; assistant_message_id?: string; grounding: DaryaSearchGrounding } {
  const candidate = event as { type?: unknown; assistant_message_id?: unknown; grounding?: unknown };
  const grounding = candidate.grounding as Partial<DaryaSearchGrounding> | null;
  return candidate.type === 'search_grounding'
    && typeof grounding === 'object'
    && grounding !== null
    && grounding.contract_version === 'darya_session_grounded_knowledge_v1'
    && ['SEARCH_NOT_NEEDED', 'SEARCH_RECOMMENDED', 'SEARCH_REQUIRED'].includes(String(grounding.decision))
    && Array.isArray(grounding.sources);
}

function isLongTermMemoryEvent(
  event: TideStreamEvent,
): event is TideStreamEvent & {
  type: 'long_term_memory_used' | 'long_term_memory_saved';
  snapshot: DaryaLongTermMemorySnapshot;
} {
  const candidate = event as { type?: unknown; snapshot?: unknown };
  const snapshot = candidate.snapshot as Partial<DaryaLongTermMemorySnapshot> | null;
  return (candidate.type === 'long_term_memory_used' || candidate.type === 'long_term_memory_saved')
    && typeof snapshot === 'object'
    && snapshot !== null
    && snapshot.scope === 'LONG_TERM_PERSONAL'
    && Array.isArray(snapshot.memories);
}

function conversationFromCreated(created: Awaited<ReturnType<typeof tideChatApi.createConversation>>): TideConversation {
  return {
    id: Date.now(),
    thread_id: created.thread_id,
    messages: [],
    message_count: 0,
    mode: created.mode,
    title: created.title,
    created_at: created.created_at,
    last_message_at: created.updated_at,
    updated_at: created.updated_at,
  };
}

export const useTideStore = create<TideState>((set, get) => ({
  conversations: [],
  currentThreadId: null,
  messages: [],
  draft: '',
  scrollTop: 0,
  loadState: 'idle',
  streamState: 'idle',
  error: null,
  railOpen: false,
  interactionContext: null,
  sharedPerspective: null,
  perspectiveState: 'idle',
  perspectiveError: null,
  workspaceObject: null,
  workspaceObjectState: 'idle',
  workspaceObjectError: null,
  artifactAction: null,
  artifactActionState: 'idle',
  artifactActionError: null,
  artifactWorkbench: null,
  artifactWorkbenchState: 'idle',
  artifactWorkbenchError: null,
  userContext: null,
  researchJob: null,
  researchState: 'idle',
  researchError: null,
  researchCollapsed: false,
  userContextState: 'idle',
  userContextError: null,
  longTermMemory: null,
  longTermMemoryState: 'idle',
  longTermMemoryError: null,
  opportunity: null,
  opportunityState: 'idle',
  opportunityError: null,
  activeController: null,
  sendInFlight: false,
  lastAttempt: null,
  liveMessageIds: [],

  hydrate: async (force = false) => {
    if (!force && get().loadState === 'ready') return;
    if (!force && hydrationPromise) return hydrationPromise;

    hydrationPromise = (async () => {
      set({ loadState: 'loading', error: null });
      try {
        let conversations = await tideChatApi.listConversations();
        const candidate = candidateThreadId();
        let selected = candidate
          ? conversations.find((conversation) => conversation.thread_id === candidate) ?? null
          : null;

        if (candidate && !selected) {
          const exact = await tideChatApi.listConversations(candidate);
          selected = exact[0] ?? null;
          if (selected && !conversations.some((item) => item.thread_id === selected?.thread_id)) {
            conversations = [selected, ...conversations];
          }
        }
        selected ??= conversations[0] ?? null;
        persistThread(selected?.thread_id ?? null);
        const savedView = readViewState();
        const restoresSelectedThread = Boolean(selected && savedView.threadId === selected.thread_id);
        set({
          conversations,
          currentThreadId: selected?.thread_id ?? null,
          messages: selected ? messageProjection(selected.thread_id, selected.messages) : [],
          loadState: 'ready',
          streamState: 'idle',
          error: null,
          draft: restoresSelectedThread ? savedView.draft : '',
          scrollTop: restoresSelectedThread ? savedView.scrollTop : 0,
          interactionContext: null,
          sharedPerspective: null,
          perspectiveState: selected ? 'loading' : 'idle',
          perspectiveError: null,
          workspaceObject: null,
          workspaceObjectState: selected ? 'loading' : 'idle',
          workspaceObjectError: null,
          artifactAction: null,
          artifactActionState: selected ? 'loading' : 'idle',
          artifactActionError: null,
          artifactWorkbench: null,
          artifactWorkbenchState: selected ? 'loading' : 'idle',
          artifactWorkbenchError: null,
          userContext: null,
          userContextState: selected ? 'loading' : 'idle',
          userContextError: null,
          longTermMemory: null,
          longTermMemoryState: 'idle',
          longTermMemoryError: null,
          opportunity: null,
          opportunityState: selected ? 'loading' : 'idle',
          opportunityError: null,
        });
        if (selected) {
          await Promise.all([
            get().refreshSharedPerspective(selected.thread_id),
            get().refreshWorkspaceObject(selected.thread_id),
            get().refreshArtifactAction(selected.thread_id),
            get().refreshUserContext(selected.thread_id),
            get().refreshOpportunity(selected.thread_id),
            // 研究会话活过一轮对话，也活过一次刷新：回到会话时把它接回来，
            // 否则「跑着的任务」和「刚出来的报告」在刷新后都无处可寻。
            get().restoreResearch(selected.thread_id),
          ]);
          await get().refreshArtifactWorkbench(selected.thread_id);
          const resumableActions = selected.messages
            .filter((message) => (
              message.role === 'assistant'
              && message.conversation_action
              && !['ready', 'failed'].includes(message.conversation_action.status)
            ))
            .map((message) => message.conversation_action as DaryaConversationAction);
          resumableActions.forEach((action) => { void get().advanceConversationAction(action); });
        }
      } catch (error) {
        set({ loadState: 'error', error: messageFor(error) });
      } finally {
        hydrationPromise = null;
      }
    })();
    return hydrationPromise;
  },

  selectThread: async (threadId) => {
    if (threadId === get().currentThreadId && get().loadState === 'ready') return;
    get().abort();
    set({ loadState: 'loading', error: null });
    try {
      let conversation = get().conversations.find((item) => item.thread_id === threadId) ?? null;
      if (!conversation) conversation = (await tideChatApi.listConversations(threadId))[0] ?? null;
      if (!conversation) throw new TideTransportError('这段对话已不在可用历史中。', 'HTTP', 404);
      persistThread(threadId);
      persistViewState({ threadId, draft: '', scrollTop: 0 });
      set((state) => ({
        conversations: state.conversations.some((item) => item.thread_id === threadId)
          ? state.conversations
          : [conversation as TideConversation, ...state.conversations],
        currentThreadId: threadId,
        messages: messageProjection(threadId, conversation?.messages ?? []),
        loadState: 'ready',
        streamState: 'idle',
        error: null,
        scrollTop: 0,
        lastAttempt: null,
        interactionContext: null,
        sharedPerspective: null,
        perspectiveState: 'loading',
        perspectiveError: null,
        workspaceObject: null,
        workspaceObjectState: 'loading',
        workspaceObjectError: null,
        artifactAction: null,
        artifactActionState: 'loading',
        artifactActionError: null,
        artifactWorkbench: null,
        artifactWorkbenchState: 'loading',
        artifactWorkbenchError: null,
        userContext: null,
        userContextState: 'loading',
        userContextError: null,
        longTermMemory: null,
        longTermMemoryState: 'idle',
        longTermMemoryError: null,
        opportunity: null,
        opportunityState: 'loading',
        opportunityError: null,
      }));
      await Promise.all([
        get().refreshSharedPerspective(threadId),
        get().refreshWorkspaceObject(threadId),
        get().refreshArtifactAction(threadId),
        get().refreshUserContext(threadId),
        get().refreshOpportunity(threadId),
      ]);
      await get().refreshArtifactWorkbench(threadId);
      const resumableActions = (conversation.messages ?? [])
        .filter((message) => (
          message.role === 'assistant'
          && message.conversation_action
          && !['ready', 'failed'].includes(message.conversation_action.status)
        ))
        .map((message) => message.conversation_action as DaryaConversationAction);
      resumableActions.forEach((action) => { void get().advanceConversationAction(action); });
    } catch (error) {
      set({ loadState: 'error', error: messageFor(error) });
    }
  },

  createThread: async () => {
    get().abort();
    set({ loadState: 'loading', error: null });
    try {
      const created = await tideChatApi.createConversation();
      const conversation = conversationFromCreated(created);
      persistThread(created.thread_id);
      persistViewState({ threadId: created.thread_id, draft: '', scrollTop: 0 });
      set((state) => ({
        conversations: [conversation, ...state.conversations],
        currentThreadId: created.thread_id,
        messages: [],
        loadState: 'ready',
        streamState: 'idle',
        error: null,
        scrollTop: 0,
        lastAttempt: null,
        interactionContext: null,
        sharedPerspective: {
          thread_id: created.thread_id,
          revision: 0,
          fields: {},
          updated_at: null,
        },
        perspectiveState: 'ready',
        perspectiveError: null,
        workspaceObject: null,
        workspaceObjectState: 'ready',
        workspaceObjectError: null,
        artifactAction: null,
        artifactActionState: 'ready',
        artifactActionError: null,
        artifactWorkbench: null,
        artifactWorkbenchState: 'ready',
        artifactWorkbenchError: null,
        userContext: {
          thread_id: created.thread_id,
          scope: 'thread',
          memory_boundary: 'NOT_LONG_TERM_MEMORY',
          documents: [],
          claims: [],
          summary: [],
          confirmed_count: 0,
          rejected_count: 0,
        },
        userContextState: 'ready',
        userContextError: null,
        longTermMemory: {
          scope: 'LONG_TERM_PERSONAL',
          reason: 'contextual_empty',
          memories: [],
        },
        longTermMemoryState: 'ready',
        longTermMemoryError: null,
        opportunity: null,
        opportunityState: 'ready',
        opportunityError: null,
      }));
      return created.thread_id;
    } catch (error) {
      set({ loadState: 'error', error: messageFor(error) });
      throw error;
    }
  },

  setDraft: (draft) => {
    persistViewState({ threadId: get().currentThreadId, draft });
    set({ draft });
  },
  setScrollTop: (scrollTop) => {
    persistViewState({ threadId: get().currentThreadId, scrollTop });
    set({ scrollTop });
  },
  setRailOpen: (railOpen) => set({ railOpen }),

  refreshSharedPerspective: async (requestedThreadId) => {
    const threadId = requestedThreadId ?? get().currentThreadId;
    if (!threadId) {
      set({ sharedPerspective: null, perspectiveState: 'idle', perspectiveError: null });
      return;
    }
    set({ perspectiveState: 'loading', perspectiveError: null });
    try {
      const snapshot = await tideChatApi.getSharedPerspective(threadId);
      if (get().currentThreadId === threadId) {
        set({ sharedPerspective: snapshot, perspectiveState: 'ready', perspectiveError: null });
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ perspectiveState: 'error', perspectiveError: messageFor(error) });
      }
    }
  },

  controlSharedPerspective: async (request) => {
    const threadId = get().currentThreadId;
    const snapshot = get().sharedPerspective;
    if (!threadId || !snapshot || get().perspectiveState === 'saving') return;
    set({ perspectiveState: 'saving', perspectiveError: null });
    try {
      const updated = await tideChatApi.controlSharedPerspective(threadId, {
        ...request,
        expected_revision: snapshot.revision,
      });
      if (get().currentThreadId === threadId) {
        set({ sharedPerspective: updated, perspectiveState: 'ready', perspectiveError: null });
        await get().refreshWorkspaceObject(threadId);
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ perspectiveState: 'error', perspectiveError: messageFor(error) });
        await get().refreshSharedPerspective(threadId);
      }
      throw error;
    }
  },

  refreshWorkspaceObject: async (requestedThreadId) => {
    const threadId = requestedThreadId ?? get().currentThreadId;
    if (!threadId) {
      set({ workspaceObject: null, workspaceObjectState: 'idle', workspaceObjectError: null });
      return;
    }
    set({ workspaceObjectState: 'loading', workspaceObjectError: null });
    try {
      const object = await tideChatApi.getWorkspaceObject(threadId);
      if (get().currentThreadId === threadId) {
        set({ workspaceObject: object, workspaceObjectState: 'ready', workspaceObjectError: null });
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ workspaceObjectState: 'error', workspaceObjectError: messageFor(error) });
      }
    }
  },

  controlWorkspaceObject: async (request) => {
    const threadId = get().currentThreadId;
    const object = get().workspaceObject;
    if (!threadId || !object || get().workspaceObjectState === 'saving') return;
    set({ workspaceObjectState: 'saving', workspaceObjectError: null });
    try {
      const updated = await tideChatApi.controlWorkspaceObject(threadId, {
        ...request,
        expected_revision: object.revision,
      });
      if (get().currentThreadId === threadId) {
        set({ workspaceObject: updated, workspaceObjectState: 'ready', workspaceObjectError: null });
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ workspaceObjectState: 'error', workspaceObjectError: messageFor(error) });
        await get().refreshWorkspaceObject(threadId);
      }
      throw error;
    }
  },

  refreshArtifactAction: async (requestedThreadId) => {
    const threadId = requestedThreadId ?? get().currentThreadId;
    if (!threadId) {
      set({ artifactAction: null, artifactActionState: 'idle', artifactActionError: null });
      return;
    }
    set({ artifactActionState: 'loading', artifactActionError: null });
    try {
      const action = await tideChatApi.getArtifactAction(threadId);
      if (get().currentThreadId === threadId) {
        set({ artifactAction: action, artifactActionState: 'ready', artifactActionError: null });
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ artifactActionState: 'error', artifactActionError: messageFor(error) });
      }
    }
  },

  refreshArtifactWorkbench: async (requestedThreadId, objectOverride, actionOverride) => {
    const threadId = requestedThreadId ?? get().currentThreadId;
    const workspaceObject = objectOverride ?? get().workspaceObject;
    const artifactAction = actionOverride ?? get().artifactAction;
    const reference = workspaceObject?.related_artifact_references.find((item) => (
      Number(item.artifact_id) > 0
      && Number(item.version_id) > 0
      && (!item.kind || item.kind === 'presentation' || item.kind === 'workspace_presentation')
    ));
    if (!threadId || !workspaceObject || !reference?.version_id) {
      set({ artifactWorkbench: null, artifactWorkbenchState: threadId ? 'ready' : 'idle', artifactWorkbenchError: null });
      return;
    }
    set({ artifactWorkbenchState: 'loading', artifactWorkbenchError: null });
    try {
      const current = await tideChatApi.getArtifactVersion(reference.artifact_id, reference.version_id);
      if (current.type !== 'workspace_presentation') {
        if (get().currentThreadId === threadId) {
          set({ artifactWorkbench: null, artifactWorkbenchState: 'ready', artifactWorkbenchError: null });
        }
        return;
      }
      let source = null;
      let sourceError: string | null = null;
      if (artifactAction?.artifact_id === current.artifact_id) {
        if (artifactAction.source_version_id === current.version_id) source = current;
        else {
          try {
            source = await tideChatApi.getArtifactVersion(current.artifact_id, artifactAction.source_version_id);
          } catch (error) {
            sourceError = messageFor(error);
          }
        }
      }
      if (get().currentThreadId === threadId) {
        set({
          artifactWorkbench: buildArtifactWorkbench(current, source, artifactAction),
          artifactWorkbenchState: 'ready',
          artifactWorkbenchError: sourceError,
        });
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ artifactWorkbench: null, artifactWorkbenchState: 'error', artifactWorkbenchError: messageFor(error) });
      }
    }
  },

  controlArtifactAction: async (control) => {
    const threadId = get().currentThreadId;
    const action = get().artifactAction;
    if (!threadId || !action || get().artifactActionState === 'saving') return;
    set({ artifactActionState: 'saving', artifactActionError: null });
    try {
      const result = await tideChatApi.controlArtifactAction(threadId, action.id, {
        control,
        expected_revision: action.revision,
      });
      if (get().currentThreadId === threadId) {
        set({
          artifactAction: result.action,
          artifactActionState: 'ready',
          artifactActionError: null,
          workspaceObject: result.workspace_object,
          workspaceObjectState: 'ready',
          workspaceObjectError: null,
        });
        await get().refreshArtifactWorkbench(threadId, result.workspace_object, result.action);
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ artifactActionState: 'error', artifactActionError: messageFor(error) });
        await get().refreshArtifactAction(threadId);
        await get().refreshWorkspaceObject(threadId);
        await get().refreshArtifactWorkbench(threadId);
      }
      throw error;
    }
  },

  refreshUserContext: async (requestedThreadId) => {
    const threadId = requestedThreadId ?? get().currentThreadId;
    if (!threadId) {
      set({ userContext: null, userContextState: 'idle', userContextError: null });
      return;
    }
    set({ userContextState: 'loading', userContextError: null });
    try {
      const snapshot = await tideChatApi.getUserContext(threadId);
      if (get().currentThreadId === threadId) {
        set({ userContext: snapshot, userContextState: 'ready', userContextError: null });
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ userContextState: 'error', userContextError: messageFor(error) });
      }
    }
  },

  uploadUserContextDocument: async (file) => {
    if (get().userContextState === 'uploading') return;
    let threadId = get().currentThreadId;
    if (!threadId) threadId = await get().createThread();
    set({ userContextState: 'uploading', userContextError: null });
    try {
      const snapshot = await tideChatApi.uploadUserContextDocument(threadId, file);
      if (get().currentThreadId === threadId) {
        set({
          userContext: snapshot,
          userContextState: 'ready',
          userContextError: null,
          railOpen: false,
        });
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ userContextState: 'error', userContextError: messageFor(error) });
      }
      throw error;
    }
  },

  ingestUserContextUrl: async (url) => {
    const text = String(url || '').trim();
    if (!text) return;
    if (get().userContextState === 'uploading') return;
    let threadId = get().currentThreadId;
    if (!threadId) threadId = await get().createThread();
    set({ userContextState: 'uploading', userContextError: null });
    try {
      const snapshot = await tideChatApi.ingestUserContextUrl(threadId, text);
      if (get().currentThreadId === threadId) {
        set({
          userContext: snapshot,
          userContextState: 'ready',
          userContextError: null,
          railOpen: false,
        });
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ userContextState: 'error', userContextError: messageFor(error) });
      }
      throw error;
    }
  },

  startResearch: async (question) => {
    const text = String(question || '').trim();
    if (!text || get().researchState === 'starting') return;
    let threadId = get().currentThreadId;
    if (!threadId) threadId = await get().createThread();
    set({ researchState: 'starting', researchError: null });
    try {
      const job = await tideChatApi.startResearch(threadId, text);
      set({ researchJob: job, researchState: 'watching', researchError: null });
    } catch (error) {
      set({ researchState: 'error', researchError: messageFor(error) });
      throw error;
    }
  },

  /**
   * 拉一次研究任务的进度。
   *
   * 任务在服务端跑，前端只负责如实显示它到了哪一步。终态之后不再拉 ——
   * 一个已经结束的任务不需要继续轮询，那只是白白烧请求。
   */
  refreshResearch: async (jobId) => {
    const target = jobId ?? get().researchJob?.artifact_id;
    if (!target) return;
    try {
      const job = await tideChatApi.getResearch(target);
      set({ researchJob: job, researchState: 'watching', researchError: null });
    } catch (error) {
      set({ researchState: 'error', researchError: messageFor(error) });
    }
  },

  retryResearch: async (jobId) => {
    try {
      const job = await tideChatApi.retryResearch(jobId);
      set({ researchJob: job, researchState: 'watching', researchError: null, researchCollapsed: false });
    } catch (error) {
      set({ researchState: 'error', researchError: messageFor(error) });
      throw error;
    }
  },

  /**
   * 把会话里已有的研究任务接回来。
   *
   * 收起研究条只清掉了浏览器里的引用，刷新页面同样会丢（researchJob 只在内存里），
   * 于是「关掉就回不去」有两条路径。这里在会话确定之后按会话列一次，取最新的那条：
   * **还在跑的展开**（learner 正等着看它到哪一步了），**已经结束的收起成一行**
   * （完成的任务不该每次回到会话都铺满一次报告，但必须留一个入口）。
   */
  restoreResearch: async (threadId) => {
    const target = threadId ?? get().currentThreadId;
    if (!target) return;
    try {
      const jobs = await tideChatApi.listResearch(target);
      if (get().currentThreadId !== target) return;
      const latest = jobs[0];
      if (!latest) {
        set({ researchJob: null, researchCollapsed: false });
        return;
      }
      const terminal = latest.status === 'READY' || latest.status === 'PARTIAL' || latest.status === 'FAILED';
      set({
        researchJob: latest,
        researchState: terminal ? 'idle' : 'watching',
        researchError: null,
        researchCollapsed: terminal,
      });
    } catch {
      // 接不回来不是 learner 的问题，也不该让整页变成错误态：静默留空即可。
    }
  },

  setResearchCollapsed: (collapsed) => {
    set({ researchCollapsed: collapsed });
  },

  controlUserContextClaim: async (claimId, request) => {
    const threadId = get().currentThreadId;
    const claim = get().userContext?.claims.find((item) => item.id === claimId);
    if (!threadId || !claim || get().userContextState === 'saving') return;
    set({ userContextState: 'saving', userContextError: null });
    try {
      const snapshot = await tideChatApi.controlUserContextClaim(threadId, claimId, {
        ...request,
        expected_revision: claim.revision,
      });
      if (get().currentThreadId === threadId) {
        set({ userContext: snapshot, userContextState: 'ready', userContextError: null });
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ userContextState: 'error', userContextError: messageFor(error) });
        await get().refreshUserContext(threadId);
      }
      throw error;
    }
  },

  promoteContextClaimToMemory: async (claimId) => {
    const threadId = get().currentThreadId;
    const claim = get().userContext?.claims.find((item) => item.id === claimId);
    if (!threadId || !claim || claim.status !== 'CONFIRMED' || get().longTermMemoryState === 'saving') return;
    set({ longTermMemoryState: 'saving', longTermMemoryError: null });
    try {
      const snapshot = await tideChatApi.promoteContextClaim(threadId, claimId);
      if (get().currentThreadId === threadId) {
        set({ longTermMemory: snapshot, longTermMemoryState: 'ready', longTermMemoryError: null });
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ longTermMemoryState: 'error', longTermMemoryError: messageFor(error) });
      }
      throw error;
    }
  },

  controlLongTermMemory: async (memoryId, request) => {
    const memory = get().longTermMemory?.memories.find((item) => item.id === memoryId);
    if (!memory || get().longTermMemoryState === 'saving') return;
    set({ longTermMemoryState: 'saving', longTermMemoryError: null });
    try {
      const snapshot = await tideChatApi.controlLongTermMemory(memoryId, {
        ...request,
        expected_revision: memory.revision,
      });
      set({ longTermMemory: snapshot, longTermMemoryState: 'ready', longTermMemoryError: null });
    } catch (error) {
      set({ longTermMemoryState: 'error', longTermMemoryError: messageFor(error) });
      throw error;
    }
  },

  refreshOpportunity: async (requestedThreadId) => {
    const threadId = requestedThreadId ?? get().currentThreadId;
    if (!threadId) {
      set({ opportunity: null, opportunityState: 'idle', opportunityError: null });
      return;
    }
    set({ opportunityState: 'loading', opportunityError: null });
    try {
      const opportunity = await tideChatApi.getCurrentOpportunity(threadId);
      if (get().currentThreadId === threadId) {
        set({ opportunity, opportunityState: 'ready', opportunityError: null });
      }
    } catch (error) {
      if (get().currentThreadId === threadId) {
        set({ opportunityState: 'error', opportunityError: messageFor(error) });
      }
    }
  },

  laterOpportunity: async () => {
    const opportunity = get().opportunity;
    if (!opportunity || get().opportunityState === 'saving') return;
    set({ opportunityState: 'saving', opportunityError: null });
    try {
      await tideChatApi.laterOpportunity(opportunity.message_id);
      set({ opportunity: null, opportunityState: 'ready', opportunityError: null });
    } catch (error) {
      set({ opportunityState: 'error', opportunityError: messageFor(error) });
      throw error;
    }
  },

  dismissOpportunity: async () => {
    const opportunity = get().opportunity;
    if (!opportunity || get().opportunityState === 'saving') return;
    set({ opportunityState: 'saving', opportunityError: null });
    try {
      await tideChatApi.dismissOpportunity(opportunity.message_id);
      set({ opportunity: null, opportunityState: 'ready', opportunityError: null });
    } catch (error) {
      set({ opportunityState: 'error', opportunityError: messageFor(error) });
      throw error;
    }
  },

  openOpportunity: async () => {
    const opportunity = get().opportunity;
    if (!opportunity || get().opportunityState === 'saving') return null;
    set({ opportunityState: 'saving', opportunityError: null });
    try {
      const result = await tideChatApi.openOpportunity(opportunity.message_id);
      set({ opportunity: null, opportunityState: 'ready', opportunityError: null });
      if (result.conversation_id) {
        const refreshed = await tideChatApi.listConversations(result.conversation_id);
        const canonical = refreshed[0];
        if (canonical && get().currentThreadId === result.conversation_id) {
          set((state) => ({
            messages: messageProjection(result.conversation_id as string, canonical.messages),
            conversations: [
              canonical,
              ...state.conversations.filter((item) => item.thread_id !== result.conversation_id),
            ],
          }));
        }
      }
      return result;
    } catch (error) {
      set({ opportunityState: 'error', opportunityError: messageFor(error) });
      throw error;
    }
  },

  advanceConversationAction: async (initialAction) => {
    const project = (next: DaryaConversationAction) => {
      set((state) => ({
        messages: state.messages.map((message) => (
          message.id === next.assistant_message_id
          || message.conversationAction?.action_key === next.action_key
            ? { ...message, conversationAction: next, orchestration_trace: next.trace }
            : message
        )),
      }));
    };
    project(initialAction);
    if (activeConversationActions.has(initialAction.action_key)) return initialAction;
    activeConversationActions.add(initialAction.action_key);
    void (async () => {
      let action = initialAction;
      let retryFailedAction = action.status === 'failed';
      let unsuccessfulReads = 0;
      const isVisible = () => get().messages.some((message) => (
        message.id === initialAction.assistant_message_id
        || message.conversationAction?.action_key === initialAction.action_key
      ));
      const observeMutation = async (request: Promise<DaryaConversationAction>) => {
        let settled = false;
        let readFailures = 0;
        const outcome = request.then(
          (value) => { settled = true; return { value, error: null }; },
          (error: unknown) => { settled = true; return { value: null, error }; },
        );
        while (!settled && isVisible()) {
          await Promise.race([outcome, new Promise((resolve) => setTimeout(resolve, Math.min(3000 + readFailures * 3000, 30000)))]);
          if (settled) break;
          try {
            action = await tideChatApi.getConversationCapabilityAction(initialAction.action_key);
            project(action);
            readFailures = 0;
          } catch {
            readFailures += 1;
            // A transient read error must not replace the last known server state.
          }
        }
        const result = await outcome;
        if (result.error) throw result.error;
        action = result.value as DaryaConversationAction;
        project(action);
      };
      try {
        while (isVisible() && (retryFailedAction || (action.status !== 'ready' && action.status !== 'failed'))) {
          try {
            if (action.status === 'planned' || retryFailedAction) {
              // The server owns the status even while this request is running.
              retryFailedAction = false;
              await observeMutation(tideChatApi.prepareConversationCapabilityAction(action.action_key, readDaryaTheme()));
            } else if (action.status === 'reviewing') {
              await observeMutation(tideChatApi.reviewConversationCapabilityAction(action.action_key));
            } else {
              await new Promise((resolve) => setTimeout(resolve, Math.min(5000 + unsuccessfulReads * 5000, 30000)));
              if (!isVisible()) break;
              action = await tideChatApi.getConversationCapabilityAction(action.action_key);
            }
            unsuccessfulReads = 0;
            project(action);
          } catch {
            // A timed-out request can still finish on the server. Read its
            // persisted state before deciding whether to prepare or review again.
            await new Promise((resolve) => setTimeout(resolve, Math.min(5000 + unsuccessfulReads * 5000, 30000)));
            unsuccessfulReads += 1;
            if (!isVisible()) break;
            try {
              action = await tideChatApi.getConversationCapabilityAction(action.action_key);
              project(action);
            } catch {
              // Keep the last server state visible and try again.
            }
          }
        }
      } finally {
        activeConversationActions.delete(initialAction.action_key);
      }
    })();
    return initialAction;
  },

  send: async (rawText, retry = false, speech = false) => {
    if (get().activeController || get().sendInFlight) return;
    const text = (rawText ?? get().draft).trim();
    if (!text) return;

    let threadId = get().currentThreadId;
    if (!threadId) {
      set({ sendInFlight: true });
      try {
        threadId = await get().createThread();
      } catch {
        set({ sendInFlight: false });
        return;
      }
      if (!threadId) {
        set({ sendInFlight: false });
        return;
      }
    }

    const previous = retry ? get().lastAttempt : null;
    const messageId = previous?.messageId ?? crypto.randomUUID();
    const userMessageId = previous?.userMessageId ?? `user-${messageId}`;
    const assistantMessageId = previous?.assistantMessageId ?? `assistant-${messageId}`;
    const attempt: LastAttempt = { text, threadId, messageId, userMessageId, assistantMessageId };
    const history = get().messages
      .filter((message) => message.status === 'complete')
      .map(({ role, content }) => ({ role, content }));

    const controller = new AbortController();
    if (!retry) persistViewState({ threadId, draft: '' });
    set((state) => {
      const withoutAttempt = retry
        ? state.messages.filter((message) => message.id !== assistantMessageId)
        : state.messages;
      const withUser = retry || withoutAttempt.some((message) => message.id === userMessageId)
        ? withoutAttempt
        : [...withoutAttempt, { id: userMessageId, role: 'user' as const, content: text, status: 'complete' as const, requestId: messageId }];
      return {
        messages: [...withUser, { id: assistantMessageId, role: 'assistant', content: '', status: 'streaming', requestId: messageId, progress: null, progressTrail: [] }],
        draft: retry ? state.draft : '',
        streamState: 'connecting',
        error: null,
        activeController: controller,
        sendInFlight: false,
        lastAttempt: attempt,
        liveMessageIds: state.liveMessageIds.includes(assistantMessageId)
          ? state.liveMessageIds
          : [...state.liveMessageIds, assistantMessageId],
        interactionContext: retry ? state.interactionContext : null,
        longTermMemory: retry ? state.longTermMemory : null,
        longTermMemoryState: retry ? state.longTermMemoryState : 'idle',
        longTermMemoryError: null,
      };
    });

    const onEvent = (event: TideStreamEvent) => {
      if (event.type === 'agent_start' || event.type === 'context') set({ streamState: 'streaming' });
      if (isTideInteractionContext(event)) {
        set({ interactionContext: event, streamState: 'streaming' });
        return;
      }
      if (isSharedPerspectiveEvent(event)) {
        if (event.snapshot.thread_id === get().currentThreadId) {
          set({
            sharedPerspective: event.snapshot,
            perspectiveState: 'ready',
            perspectiveError: null,
          });
        }
        return;
      }
      if (isWorkspaceObjectEvent(event)) {
        if (event.object.thread_id === get().currentThreadId) {
          set({
            workspaceObject: event.object,
            workspaceObjectState: 'ready',
            workspaceObjectError: null,
          });
          void get().refreshArtifactWorkbench(event.object.thread_id, event.object, get().artifactAction);
        }
        return;
      }
      if (isArtifactActionEvent(event)) {
        if (event.action.thread_id === get().currentThreadId) {
          set({
            artifactAction: event.action,
            artifactActionState: 'ready',
            artifactActionError: null,
          });
          void get().refreshArtifactWorkbench(event.action.thread_id, get().workspaceObject, event.action);
        }
        return;
      }
      if (isConversationActionEvent(event)) {
        set((state) => ({
          messages: state.messages.map((message) => (
            message.id === event.action.assistant_message_id
              ? { ...message, conversationAction: event.action, orchestration_trace: event.action.trace }
              : message
          )),
        }));
        return;
      }
      if (event.type === 'progress' && typeof event.label === 'string') {
        // 后端在每个真实阶段点发一句，界面照它变，不要自己编动画。
        const label = event.label as string;
        set((state) => ({
          streamState: 'streaming',
          messages: state.messages.map((message) => message.id === assistantMessageId
            ? {
                ...message,
                progress: label,
                progressTrail: [...(message.progressTrail ?? []), label]
                  // 同一步重复到达不必记两遍，否则思考过程会被刷屏
                  .filter((item, index, all) => item !== all[index - 1]),
              }
            : message),
        }));
        return;
      }
      if (event.type === 'search_status' && typeof event.message === 'string') {
        // Failed provider details stay in telemetry and the evidence contract;
        // they should not displace the learner's answer with infrastructure copy.
        const searchStatus = event.status === 'failed' ? null : event.message;
        set((state) => ({
          streamState: 'streaming',
          messages: state.messages.map((message) => message.id === assistantMessageId
            ? { ...message, searchStatus }
            : message),
        }));
        return;
      }
      if (isSearchGroundingEvent(event)) {
        const targetId = event.assistant_message_id || assistantMessageId;
        set((state) => ({
          messages: state.messages.map((message) => message.id === targetId
            ? { ...message, searchEvidence: event.grounding, searchStatus: null }
            : message),
        }));
        return;
      }
      if (isLongTermMemoryEvent(event)) {
        set({
          longTermMemory: event.snapshot,
          longTermMemoryState: 'ready',
          longTermMemoryError: null,
        });
        return;
      }
      if (event.type !== 'agent_content' || typeof event.preview !== 'string') return;
      set((state) => ({
        streamState: 'streaming',
        messages: state.messages.map((message) => message.id === assistantMessageId
          ? {
              ...message,
              content: event.resource_type === 'tide_replacement'
                ? event.preview as string
                : `${message.content}${event.preview}`,
              searchStatus: null,
              status: 'streaming',
            }
          : message),
      }));
    };

    try {
      await tideChatApi.stream({
        message: text,
        history,
        thread_id: threadId,
        user_id: tideChatApi.currentUserId(),
        style: responseStyleFor(text),
        message_id: messageId,
        retry,
        experience: 'tide_v1',
        speech,
      }, onEvent, controller.signal);

      set((state) => ({
        messages: state.messages.map((message) => message.id === assistantMessageId
          ? { ...message, status: 'complete' }
          : message),
        streamState: 'idle',
        activeController: null,
        error: null,
      }));

      const refreshed = await tideChatApi.listConversations(threadId);
      const canonical = refreshed[0];
      if (canonical && get().currentThreadId === threadId) {
        const persisted = messageProjection(threadId as string, canonical.messages);
        // 落库后这一轮换了 id：刚才那一轮得跟着换名，不然黑板会把刚说完的话
        // 当成"翻回来的历史"，讲到一半就跳到终态。
        const persistedAssistantId = [...persisted].reverse()
          .find((message) => message.role === 'assistant')?.id;
        set((state) => ({
          messages: persisted,
          conversations: [canonical, ...state.conversations.filter((item) => item.thread_id !== threadId)],
          lastAttempt: null,
          liveMessageIds: persistedAssistantId && !state.liveMessageIds.includes(persistedAssistantId)
            ? [...state.liveMessageIds, persistedAssistantId]
            : state.liveMessageIds,
        }));
        const action = [...canonical.messages]
          .reverse()
          .find((message) => message.role === 'assistant' && message.conversation_action)
          ?.conversation_action;
        if (action) {
          try {
            await get().advanceConversationAction(action);
          } catch {
            // The inline card owns retry and retains the failed action projection.
          }
        }
      }
      await get().refreshOpportunity(threadId);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        set((state) => ({
          messages: state.messages.map((message) => message.id === assistantMessageId
            ? { ...message, content: message.content || '已停止这次回答。', status: 'stopped' }
            : message),
          streamState: 'stopped',
          activeController: null,
          error: null,
        }));
        return;
      }
      const errorText = messageFor(error);
      set((state) => ({
        messages: state.messages.map((message) => message.id === assistantMessageId
          ? { ...message, content: message.content || '这次没有收到完整回答。', status: 'failed' }
          : message),
        streamState: 'error',
        activeController: null,
        error: errorText,
      }));
    }
  },

  retryLast: async () => {
    const attempt = get().lastAttempt;
    if (!attempt || get().activeController || get().sendInFlight) return;
    await get().send(attempt.text, true);
  },

  abort: () => {
    get().activeController?.abort();
  },
}));

export function tideConversationTitle(conversation: TideConversation) {
  return conversation.title?.trim() || titleFrom(conversation.messages || []);
}

export const tideThreadStorageContract = {
  tidePointer: TIDE_THREAD_KEY,
  legacyReadOnlySeed: LEGACY_THREAD_KEY,
} as const;
