import { useEffect, useRef, useState } from 'react';
import { ArtifactShell } from '../artifact/ArtifactShell';
import type { ArtifactPublicProjection, ArtifactSelectionReference } from '../../api/artifacts';
import styles from './AiWorkspaceKernel.module.css';

const WORKSPACE_SCRIPTS = [
  'config.js','ai-workspace-api.js','ai-render.js','ai-workspace-state.js','ai-workspace-context.js','ai-workspace-context-integration.js','ai-workspace-agents.js','ai-workspace-memory-editor.js','ai-workspace-impressions.js','ai-workspace-scroll.js','ai-workspace-speech.js','ai-workspace-avatar.js','ai-workspace-search.js','ai-workspace-harness.js'
] as const;
const SCRIPT_GLOBALS = ['ML_CONFIG','API_BASE_URL','AIWorkspaceApiClient','apiClient','AIRender','AIWorkspaceState','AIWorkspaceContext','KakiWorkspaceContextBridge','AIWorkspaceAgents','AIWorkspaceMemoryEditor','AIWorkspaceImpressions','AIWorkspaceScroll','AIWorkspaceSpeech','speechAdapter','KakiAvatarRuntime','digitalTutorAdapter','KakiTeachingBoard','KakiBlackboardHost','KakiVoiceCallRuntime','KakiWorkspaceRuntimeBridge','AIWorkspaceSearch','KakiArtifactShellHost'] as const;

type ShellOpenItem = { artifact_id?: number; id?: number; kind?: string; type?: string; version_id?: number; version_number?: number; version?: number; thread_id?: string };

function artifactKind(item: ShellOpenItem) {
  return String(item.kind || item.type || '').replace(/^workspace_/, '');
}

const SHELL_ITEM_KINDS = new Set(['lecture', 'document', 'code', 'mindmap', 'presentation', 'interactive_lesson', 'video', 'question']);

function shouldOpenInArtifactShell(item: ShellOpenItem) {
  const kind = artifactKind(item);
  return !kind || SHELL_ITEM_KINDS.has(kind);
}
type ContextBridge = {
  bindArtifact?: (artifact: unknown, meta?: unknown) => void;
  setSelectionFromShell?: (selection: unknown, exactText?: string) => void;
  clearSelection?: () => void;
  setViewMode?: (mode: string) => void;
  setReadingPositionFromShell?: (position: unknown) => void;
  getState?: () => { thread_ref?: string } | null;
};

declare global {
  interface Window {
    KakiArtifactShellHost?: { open: (item: ShellOpenItem, options?: Record<string, unknown>) => void; close: () => void };
    KakiWorkspaceOpenArtifact?: (item: ShellOpenItem, options?: Record<string, unknown>) => void;
    KakiWorkspaceContextBridge?: ContextBridge;
  }
}

function loadScript(source: string, mountId: string) {
  return new Promise<HTMLScriptElement>((resolve, reject) => {
    const script = document.createElement('script'); script.src = `/ai-workspace/scripts/${source}?host=${encodeURIComponent(mountId)}`; script.dataset.aiWorkspaceMount = mountId;
    script.onload = () => resolve(script); script.onerror = () => reject(new Error(`无法加载 AI 工作区模块：${source}`)); document.body.appendChild(script);
  });
}
function rewriteWorkspaceAssets(root: HTMLElement) { root.querySelectorAll<HTMLElement>('[src]').forEach(element => { const source = element.getAttribute('src'); if (source?.startsWith('assets/')) element.setAttribute('src', `/ai-workspace/${source}`); }); }

export function AiWorkspaceKernel({ presentation = 'stage' }: { presentation?: 'stage' | 'rail' }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const previewUiRef = useRef<{ conversationId?: string; scrollTop: number; draft: string } | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');
  const [retrySeed, setRetrySeed] = useState(0);
  const [shell, setShell] = useState<{ artifactId: number; versionId?: number; versionNumber?: number; threadId?: string } | null>(null);

  useEffect(() => {
    const announcePreview = (open: boolean) => window.dispatchEvent(new CustomEvent('kaki:artifact-preview-state', { detail: { open } }));
    const captureConversationUi = () => {
      if (previewUiRef.current) return;
      const messages = document.getElementById('messages');
      const input = document.getElementById('input') as HTMLTextAreaElement | null;
      previewUiRef.current = {
        conversationId: window.KakiWorkspaceContextBridge?.getState?.()?.thread_ref,
        scrollTop: messages?.scrollTop || 0,
        draft: input?.value || '',
      };
      try { sessionStorage.setItem('kaki_workspace_preview_return_v1', JSON.stringify(previewUiRef.current)); } catch { /* in-memory state is sufficient */ }
    };
    const restoreConversationUi = () => window.requestAnimationFrame(() => {
      const snapshot = previewUiRef.current;
      if (!snapshot) return;
      const messages = document.getElementById('messages');
      const input = document.getElementById('input') as HTMLTextAreaElement | null;
      if (messages) messages.scrollTop = snapshot.scrollTop;
      if (input && input.value !== snapshot.draft) {
        input.value = snapshot.draft;
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
      previewUiRef.current = null;
      try { sessionStorage.removeItem('kaki_workspace_preview_return_v1'); } catch { /* no-op */ }
    });
    window.KakiArtifactShellHost = {
      open(item) {
        // 练习试卷存在 exam_papers，不是 generated_items。交给工作区原有作答页，避免 /api/items/{examId} 404 盖住整个界面。
        if (!shouldOpenInArtifactShell(item)) {
          window.KakiWorkspaceOpenArtifact?.(item);
          return;
        }
        const artifactId = Number(item.artifact_id || item.id);
        if (!Number.isFinite(artifactId) || artifactId <= 0) return;
        captureConversationUi();
        const threadId = item.thread_id || window.KakiWorkspaceContextBridge?.getState?.()?.thread_ref || undefined;
        setShell({ artifactId, versionId: Number(item.version_id) || undefined, versionNumber: Number(item.version_number || item.version) || undefined, threadId });
        document.body.classList.add('kaki-artifact-preview-open');
        announcePreview(true);
      },
      close() {
        setShell(null);
        window.KakiWorkspaceContextBridge?.setViewMode?.('conversation');
        document.body.classList.remove('kaki-artifact-preview-open');
        announcePreview(false);
        restoreConversationUi();
      },
    };
    return () => { document.body.classList.remove('kaki-artifact-preview-open'); announcePreview(false); delete window.KakiArtifactShellHost; };
  }, []);

  useEffect(() => {
    const mountId = `workspace-${Date.now()}-${crypto.randomUUID?.() ?? 'mount'}`;
    const controller = new AbortController(); let disposed = false; let styleElement: HTMLStyleElement | null = null; let openCallListener: (() => void) | null = null; const loadedScripts: HTMLScriptElement[] = [];
    async function mount() {
      try {
        setStatus('loading'); setError('');
        const response = await fetch('/ai-workspace/ai-workspace-test.html', { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error(`AI 工作区模板加载失败（${response.status}）`);
        const html = await response.text(); if (disposed || !hostRef.current) return;
        const parsed = new DOMParser().parseFromString(html, 'text/html');
        const application = parsed.querySelector<HTMLElement>('#app'); const memoryDrawer = parsed.querySelector<HTMLElement>('#memoryDrawer'); const artifactDrawer = parsed.querySelector<HTMLElement>('#artifactDrawer'); const sourceStyle = parsed.querySelector('style');
        if (!application || !memoryDrawer || !artifactDrawer || !sourceStyle) throw new Error('AI 工作区模板缺少必要的界面契约');
        application.id = 'workspaceApp'; application.hidden = false; application.querySelector('#logout')?.remove();
        // 拾光漫游已迁至独立页（侧栏"探索"打开 shiguang-demo.html）；此处不再挂载旧冒险运行时。
        const adventureEntry = application.querySelector<HTMLButtonElement>('#adventureEntry');
        if (adventureEntry) { adventureEntry.hidden = true; adventureEntry.setAttribute('aria-hidden', 'true'); adventureEntry.tabIndex = -1; }
        // These legacy nodes are runtime contracts (the harness writes its
        // diagnostics and reads the optional adapter fields). They remain in
        // the DOM but are hidden by the host stylesheet, so no engineering UI
        // reaches the learner while the real conversation runtime keeps its
        // expected anchors.
        rewriteWorkspaceAssets(application); rewriteWorkspaceAssets(memoryDrawer); rewriteWorkspaceAssets(artifactDrawer); artifactDrawer.hidden = true;
        hostRef.current.replaceChildren(application, memoryDrawer, artifactDrawer);
        styleElement = document.createElement('style'); styleElement.dataset.aiWorkspaceMount = mountId;
        styleElement.textContent = (sourceStyle.textContent ?? '').split("url('assets/").join("url('/ai-workspace/assets/").split('url("assets/').join('url("/ai-workspace/assets/');
        styleElement.textContent += '\n#workspaceApp.resource-open .main{padding-right:18px!important;visibility:visible!important;pointer-events:auto!important}';
        document.head.appendChild(styleElement);
        for (const scriptName of WORKSPACE_SCRIPTS) { if (disposed) return; loadedScripts.push(await loadScript(scriptName, mountId)); }
        const composer = application.querySelector<HTMLElement>('.composer-bottom');
        const composerMore = application.querySelector<HTMLButtonElement>('#composerMore');
        if (composerMore) {
          composerMore.dataset.kakiLearningTools = 'true';
          composerMore.setAttribute('data-testid', 'learning-tool-composer-trigger');
        }
        const voiceTool = application.querySelector<HTMLButtonElement>('#voiceTool');
        const composerActions = composer?.querySelector<HTMLElement>('.composer-actions');
        if (voiceTool && composerActions && !voiceTool.classList.contains('composer-voice-direct')) {
          voiceTool.classList.add('composer-voice-direct');
          voiceTool.setAttribute('aria-label', '语音输入');
          voiceTool.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6"/></svg>';
          composerActions.prepend(voiceTool);
        }
        openCallListener = () => (document.getElementById('callTool') as HTMLButtonElement | null)?.click();
        window.addEventListener('kaki:open-ai-call', openCallListener);
        try {
          const pending = JSON.parse(sessionStorage.getItem('kaki_pending_artifact_context_v1') || 'null');
          sessionStorage.removeItem('kaki_pending_artifact_context_v1');
          if (pending?.artifact_id) {
            window.KakiArtifactShellHost?.open(pending);
            window.setTimeout(() => {
              if (pending.selection) window.KakiWorkspaceContextBridge?.setSelectionFromShell?.(pending.selection, pending.selection.selected_text);
              const input = document.getElementById('input') as HTMLTextAreaElement | HTMLInputElement | null;
              if (input) {
                input.value = pending.prompt || (pending.selection ? '这部分为什么这样写？' : '给我解释一下这个。');
                input.dispatchEvent(new Event('input', { bubbles: true }));
                if (pending.auto_submit) window.setTimeout(() => (document.getElementById('send') as HTMLButtonElement | null)?.click(), 80);
              }
            }, 120);
          }
        } catch { sessionStorage.removeItem('kaki_pending_artifact_context_v1'); }
        try {
          const pendingTutorArtifact = JSON.parse(sessionStorage.getItem('kaki_pending_tutor_artifact_context_v1') || 'null');
          sessionStorage.removeItem('kaki_pending_tutor_artifact_context_v1');
          if (pendingTutorArtifact?.artifact?.artifact_id) {
            window.KakiWorkspaceContextBridge?.bindArtifact?.(pendingTutorArtifact.artifact, {
              source: 'learning-formal-artifact',
              reason: '你从当前正式学习产物进入 AI 导师',
              keepView: true,
            });
            if (pendingTutorArtifact.selection) {
              window.KakiWorkspaceContextBridge?.setSelectionFromShell?.(pendingTutorArtifact.selection, pendingTutorArtifact.selection.selected_text);
            }
            window.KakiWorkspaceContextBridge?.setViewMode?.('conversation');
          }
        } catch { sessionStorage.removeItem('kaki_pending_tutor_artifact_context_v1'); }
        if (sessionStorage.getItem('kaki_open_call_v1') === '1') {
          sessionStorage.removeItem('kaki_open_call_v1');
          window.setTimeout(() => (document.getElementById('callTool') as HTMLButtonElement | null)?.click(), 180);
        }
        if (!disposed) setStatus('ready');
      } catch (reason) { if (disposed || controller.signal.aborted) return; setError(reason instanceof Error ? reason.message : 'AI 工作区加载失败'); setStatus('error'); }
    }
    void mount();
    return () => {
      disposed = true; controller.abort();
      if (openCallListener) window.removeEventListener('kaki:open-ai-call', openCallListener);
      const workspaceWindow = window as typeof window & { AIWorkspaceDestroy?: () => void }; workspaceWindow.AIWorkspaceDestroy?.(); delete workspaceWindow.AIWorkspaceDestroy;
      loadedScripts.forEach(script => script.remove()); styleElement?.remove(); if (hostRef.current) hostRef.current.replaceChildren();
      const globalWindow = window as unknown as Record<string, unknown>; SCRIPT_GLOBALS.filter(name => name !== 'KakiArtifactShellHost').forEach(name => { try { delete globalWindow[name]; } catch { globalWindow[name] = undefined; } });
    };
  }, [retrySeed]);

  function bindVersion(artifact: ArtifactPublicProjection) {
    window.KakiWorkspaceContextBridge?.bindArtifact?.({ ...artifact, version: artifact.version_number, version_id: artifact.version_id }, { source: 'artifact_version_selected', reason: '你明确选择了这份资源版本', keepView: true });
  }
  function askAi(selection: ArtifactSelectionReference | null, artifact: ArtifactPublicProjection) {
    bindVersion(artifact);
    if (selection) window.KakiWorkspaceContextBridge?.setSelectionFromShell?.(selection, selection.selected_text);
    else window.KakiWorkspaceContextBridge?.clearSelection?.();
    const input = document.getElementById('input') as HTMLTextAreaElement | HTMLInputElement | null;
    if (input) { input.value = selection ? '这部分为什么这样写？' : '给我解释一下这个。'; input.dispatchEvent(new Event('input', { bubbles: true })); input.focus(); }
  }

  return <div className={`${styles.host} ${shell ? styles.withShell : ''} ${presentation === 'rail' ? styles.rail : ''}`} data-kernel-state={status} data-testid="ai-workspace-kernel">
    <div className={styles.workspace}>
      {status === 'loading' && <div className={styles.hostState} role="status"><span className={styles.stateMark} aria-hidden="true">柿</span><div><strong>正在恢复 AI 学习工作区</strong><small>会话、资料和当前学习对象正在重新连接…</small></div></div>}
      {status === 'error' && <div className={`${styles.hostState} ${styles.hostError}`} role="alert"><span className={styles.stateMark} aria-hidden="true">!</span><div><strong>AI 学习工作区暂时没有恢复</strong><small>{error}</small><button type="button" onClick={() => setRetrySeed(seed => seed + 1)}>重新加载工作区</button></div></div>}
      <div ref={hostRef} data-testid="ai-workspace-kernel" />
    </div>
    {shell && <aside className={styles.shellPanel} data-testid="ai-workspace-artifact-shell"><ArtifactShell artifactId={shell.artifactId} initialVersionId={shell.versionId} initialVersionNumber={shell.versionNumber} context={{ mode: 'workspace', threadId: shell.threadId }} onClose={() => window.KakiArtifactShellHost?.close()} onAskAi={askAi} onVersionSelected={bindVersion} onReadingPositionChange={position => window.KakiWorkspaceContextBridge?.setReadingPositionFromShell?.(position)} /></aside>}
  </div>;
}
