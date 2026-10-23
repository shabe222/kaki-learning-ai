import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BookOpen, ChevronLeft, Columns2, Download, Expand, Focus, LoaderCircle, MessageCircle, MoreHorizontal, Sparkles, Loader2 } from 'lucide-react';
import { artifactApi, type ArtifactPublicProjection, type ArtifactSelectionReference, type ArtifactVersionComparison } from '../../api/artifacts';
import { httpErrorMessage, postData } from '../../api/client';
import { generateAudioLesson, generateFlashcards } from '../../api/study';
import { ArtifactRenderer, downloadUnifiedArtifact } from '../ArtifactRenderer';
import { CodeRenderer } from './CodeRenderer';
import { DocumentRenderer, DocumentTableOfContents } from './DocumentRenderer';
import { InteractiveQuestionRenderer } from './InteractiveQuestionRenderer';
import { QuestionPracticeRenderer } from './QuestionPracticeRenderer';
import { CanonicalBlockRenderer } from './CanonicalBlockRenderer';
import styles from './ArtifactShell.module.css';

export interface ArtifactShellContext {
  threadId?: string | null;
  mode?: 'workspace' | 'page' | 'learning';
  product?: 'legacy' | 'darya';
}

export interface ArtifactShellProps {
  artifactId: number;
  initialVersionId?: number | null;
  initialVersionNumber?: number | null;
  context?: ArtifactShellContext;
  onClose?: () => void;
  onAskAi?: (selection: ArtifactSelectionReference | null, artifact: ArtifactPublicProjection) => void;
  onVersionSelected?: (artifact: ArtifactPublicProjection) => void;
  onReadingPositionChange?: (position: { artifact_id: number; version: number; version_id: number; anchor: string | null; scroll_top: number }) => void;
}

type ShellError = { kind: 'load' | 'render' | 'generate' | 'compare' | 'export' | 'position'; message: string } | null;

function idempotencyKey(artifactId: number, version: number) {
  const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `artifact-revision:${artifactId}:v${version}:${random}`;
}

function readingKey(threadId: string | null | undefined, artifactId: number, versionId: number) {
  return `kaki_artifact_reading_v1:${threadId || 'no-thread'}:${artifactId}:${versionId}`;
}

function kindLabel(kind: string) {
  const normalized = String(kind || '').replace('workspace_', '');
  const labels: Record<string, string> = { lecture: '学习讲义', document: '学习讲义', code: '代码实验', question: '练习题', interactive_lesson: '互动课堂', mindmap: '思维导图', presentation: '教学演示', video: '教学视频' };
  return labels[normalized] || '学习资源';
}

function statusLabel(status: string) {
  const labels: Record<string, string> = { generated: '已生成', completed: '已保存', passed: '已通过检查', in_review: '审阅中', revision_required: '需要修订', blocked: '暂时受阻', ready: '可查看', success: '已完成', queued: '正在准备', planned: '正在准备', pending: '正在准备', running: '处理中', generating: '生成中', saving: '保存中', failed: '生成失败', error: '暂时不可用', draft: '草稿' };
  return labels[String(status || '').toLowerCase()] || '已保存';
}

function VersionComparisonView({ comparison, onChoose }: { comparison: ArtifactVersionComparison; onChoose: (version: number) => void }) {
  return <section className={styles.compare} data-testid="artifact-version-compare">
    <header><div><strong>版本变化</strong><span>v{comparison.from_version.version_number} → v{comparison.to_version.version_number}</span></div><div><button onClick={() => onChoose(comparison.from_version.version_number)}>继续查看 v{comparison.from_version.version_number}</button><button onClick={() => onChoose(comparison.to_version.version_number)}>切换到 v{comparison.to_version.version_number}</button></div></header>
    <div className={styles.compareList}>
      {comparison.changes.filter(change => change.kind !== 'equal').map((change, index) => <article key={index} data-kind={change.kind}>
        <span>{change.kind === 'added' ? '新增' : change.kind === 'deleted' ? '删除' : '修改'}</span>
        <div className={styles.diffColumns}><pre>{change.from_lines.join('\n') || '—'}</pre><pre>{change.to_lines.join('\n') || '—'}</pre></div>
      </article>)}
      {!comparison.changes.some(change => change.kind !== 'equal') && <p>两个版本的公开内容一致。</p>}
    </div>
  </section>;
}

export function ArtifactShell({ artifactId, initialVersionId, initialVersionNumber, context, onClose, onAskAi, onVersionSelected, onReadingPositionChange }: ArtifactShellProps) {
  const [artifact, setArtifact] = useState<ArtifactPublicProjection | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ShellError>(null);
  const [selection, setSelection] = useState<ArtifactSelectionReference | null>(null);
  const [focusMode, setFocusMode] = useState(false);
  const [tocOpen, setTocOpen] = useState(true);
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [instruction, setInstruction] = useState('');
  const [generating, setGenerating] = useState(false);
  const [derivedAction, setDerivedAction] = useState<string | null>(null);
  const [studyAction, setStudyAction] = useState<string | null>(null);
  const [derivedResult, setDerivedResult] = useState<{ artifact_id: number; kind: string; title: string; summary: string } | null>(null);
  const [derivedError, setDerivedError] = useState('');
  const [newVersion, setNewVersion] = useState<ArtifactPublicProjection | null>(null);
  const [compareOpen, setCompareOpen] = useState(false);
  const [comparison, setComparison] = useState<ArtifactVersionComparison | null>(null);
  const daryaHost = context?.product === 'darya';
  const assistantName = 'DARYA';
  const bodyRef = useRef<HTMLDivElement>(null);
  const positionTimer = useRef<number | null>(null);

  const load = useCallback(async (options: { versionId?: number | null; version?: number | null; silent?: boolean } = {}) => {
    if (!options.silent) { setLoading(true); setError(null); }
    try {
      const result = await artifactApi.get(artifactId, { versionId: options.versionId || undefined, version: options.version || undefined });
      setArtifact(result); setSelection(null); setComparison(null); setCompareOpen(false);
      onVersionSelected?.(result);
    } catch (reason) {
      if (!options.silent) setError({ kind: 'load', message: httpErrorMessage(reason, '学习资源暂时无法加载') });
    } finally { if (!options.silent) setLoading(false); }
  }, [artifactId, onVersionSelected]);

  useEffect(() => { void load({ versionId: initialVersionId, version: initialVersionNumber }); }, [load, initialVersionId, initialVersionNumber]);

  useEffect(() => {
    if (!daryaHost || !artifact) return;
    const verdict = String(artifact.canonical?.status.review_verdict || '').toUpperCase();
    const status = String(artifact.canonical?.status.canonical_status || artifact.status || '').toUpperCase();
    if (verdict === 'PASS' || !['PLANNED', 'GENERATING', 'GENERATED', 'IN_REVIEW'].includes(status)) return;
    // Background artifacts progress independently of the chat response. Poll
    // the current version so an initially queued view becomes playable without
    // making the learner close and reopen it.
    const timer = window.setTimeout(() => { void load({ silent: true }); }, 1200);
    return () => window.clearTimeout(timer);
  }, [artifact, daryaHost, load]);

  useEffect(() => {
    if (!artifact || !bodyRef.current) return;
    const key = readingKey(context?.threadId, artifact.artifact_id, artifact.version_id);
    try {
      const restored = JSON.parse(localStorage.getItem(key) || '{}');
      window.requestAnimationFrame(() => {
        if (restored.anchor) document.getElementById(restored.anchor)?.scrollIntoView({ block: 'start' });
        else bodyRef.current?.scrollTo({ top: Number(restored.scrollTop || 0) });
      });
    } catch { setError({ kind: 'position', message: '阅读位置未能自动恢复，正文仍可正常阅读。' }); }
  }, [artifact, context?.threadId]);

  function savePosition() {
    if (!artifact || !bodyRef.current) return;
    if (positionTimer.current) window.clearTimeout(positionTimer.current);
    positionTimer.current = window.setTimeout(() => {
      const visible = [...bodyRef.current!.querySelectorAll<HTMLElement>('[data-section-anchor]')].find(node => node.getBoundingClientRect().top >= 80);
      const position = { artifact_id: artifact.artifact_id, version: artifact.version_number, version_id: artifact.version_id, scroll_top: bodyRef.current!.scrollTop, anchor: visible?.dataset.sectionAnchor || null };
      try { localStorage.setItem(readingKey(context?.threadId, artifact.artifact_id, artifact.version_id), JSON.stringify({ scrollTop: position.scroll_top, anchor: position.anchor, updatedAt: new Date().toISOString() })); onReadingPositionChange?.(position); }
      catch { setError({ kind: 'position', message: '阅读位置同步失败；当前正文和版本不会受影响。' }); }
    }, 180);
  }

  const isDocument = useMemo(() => ['lecture', 'document'].includes(String(artifact?.kind || '').replace('workspace_', '')), [artifact]);
  const hasCanonicalDocument = Boolean(isDocument && artifact?.canonical?.contract === 'darya.artifact.v1' && artifact.canonical.blocks.length);
  const isCode = useMemo(() => String(artifact?.kind || '').replace('workspace_', '') === 'code', [artifact]);
  const isInteractive = useMemo(() => String(artifact?.kind || '').replace('workspace_', '') === 'interactive_lesson', [artifact]);
  const isTieredAssessment = artifact?.canonical?.purpose?.experience === 'TIERED_ASSESSMENT';
  const isQuestion = useMemo(() => String(artifact?.kind || '').replace('workspace_', '') === 'question', [artifact]);
  const canGenerateVersion = useMemo(() => isDocument && Boolean(artifact?.thread_id), [artifact, isDocument]);

  async function generateVersion() {
    if (!artifact || !artifact.thread_id || !isDocument || !instruction.trim() || generating) return;
    setGenerating(true); setError(null);
    try {
      const result = await artifactApi.reviseLecture(artifact.artifact_id, { thread_id: artifact.thread_id, instruction: instruction.trim(), expected_version: artifact.version_number, idempotency_key: idempotencyKey(artifact.artifact_id, artifact.version_number) });
      setNewVersion(result); setRevisionOpen(false); setInstruction('');
    } catch (reason) {
      setError({ kind: 'generate', message: httpErrorMessage(reason, '新版本生成失败；当前版本已保留。') });
    } finally { setGenerating(false); }
  }

  async function openCompare() {
    if (!artifact || artifact.available_versions.length < 2) return;
    setCompareOpen(true); setError(null);
    const other = artifact.previous_version_ref?.version_number || artifact.next_version_ref?.version_number || artifact.available_versions[0].version_number;
    try { setComparison(await artifactApi.compare(artifact.artifact_id, Math.min(other, artifact.version_number), Math.max(other, artifact.version_number))); }
    catch (reason) { setError({ kind: 'compare', message: reason instanceof Error ? reason.message : '版本比较暂时不可用。' }); }
  }

  function askAi(explicitSelection?: ArtifactSelectionReference | null) {
    if (!artifact) return;
    const activeSelection = explicitSelection === undefined ? selection : explicitSelection;
    if (onAskAi) { onAskAi(activeSelection, artifact); return; }
    try {
      sessionStorage.setItem('kaki_pending_artifact_context_v1', JSON.stringify({
        artifact_id: artifact.artifact_id, version_id: artifact.version_id, version_number: artifact.version_number,
        thread_id: artifact.thread_id, selection: activeSelection ? { ...activeSelection, selected_text: activeSelection.selected_text.slice(0, 1200) } : null,
        prompt: activeSelection
          ? activeSelection.block_id === 'code-lab-source'
            ? `请解释这个精确代码锚点，但不要替我运行或提交：v${activeSelection.artifact_version_number} · ${activeSelection.range_label} · ${activeSelection.block_id} · ${activeSelection.content_checksum}：${activeSelection.summary.slice(0, 240)}`
            : `请解释这段已选内容，但不要替我提交：${activeSelection.summary.slice(0, 240)}`
          : undefined,
      }));
    } catch { /* navigation remains available without storage */ }
    window.location.assign(context?.product === 'darya' ? '/darya' : '/ai-learning');
  }

  async function runDerivedAction(action: 'EXERCISE' | 'KNOWLEDGE_CARD' | 'MINDMAP' | 'QUIZ') {
    if (!artifact) return;
    if (derivedAction || generating) return;
    const threadId = artifact.thread_id || context?.threadId;
    if (!threadId) {
      setDerivedError('当前学习资源还没有关联会话，无法生成衍生产物。');
      return;
    }
    setDerivedAction(action); setDerivedError(''); setDerivedResult(null);
    try {
      const result = await postData<{ artifact_id?: number; id?: number; kind?: string; title?: string; summary?: string; question_count?: number }>(
        `/api/chat/tasks/artifacts/${artifact.artifact_id}/actions/derive`,
        { thread_id: threadId, action },
      );
      setDerivedResult({
        artifact_id: Number(result.artifact_id ?? result.id ?? 0),
        kind: String(result.kind || (action === 'MINDMAP' ? 'mindmap' : 'knowledge_card')),
        title: String(result.title || ''),
        summary: String(result.summary || ''),
      });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '生成失败，请稍后重试。';
      if (message.includes('正在生成')) {
        setDerivedError(message);
      } else {
        setDerivedError(message);
      }
    } finally {
      setDerivedAction(null);
    }
  }

  function openDerived() {
    if (!derivedResult || !derivedResult.artifact_id) return;
    window.open(`/artifacts/${derivedResult.artifact_id}?kind=${encodeURIComponent(derivedResult.kind)}`, '_blank');
  }

  // Study-resource generation (flashcards / audio lesson) runs on the
  // /api/study endpoints; results open through the same artifact viewer.
  async function runStudyAction(action: 'flashcards' | 'audio') {
    if (!artifact) return;
    if (studyAction || generating) return;
    setStudyAction(action); setDerivedError(''); setDerivedResult(null);
    try {
      const result = action === 'flashcards'
        ? await generateFlashcards({ source_artifact_id: artifact.artifact_id, thread_id: artifact.thread_id || context?.threadId || '' })
        : await generateAudioLesson({ source_artifact_id: artifact.artifact_id, thread_id: artifact.thread_id || context?.threadId || '' });
      setDerivedResult({
        artifact_id: result.artifact_id,
        kind: String(result.kind || (action === 'flashcards' ? 'flashcards' : 'audio')),
        title: String(result.title || ''),
        summary: action === 'flashcards' ? '已按检索练习原则生成记忆卡组，可翻卡复习。' : '音频课已生成，可以收听并查看文稿。',
      });
    } catch (reason) {
      setDerivedError(reason instanceof Error ? reason.message : '生成失败，请稍后重试。');
    } finally {
      setStudyAction(null);
    }
  }

  async function exportArtifact() {
    if (!artifact) return;
    try { await downloadUnifiedArtifact({ artifact_id: artifact.artifact_id, kind: artifact.kind, type: artifact.type, title: artifact.title, topic: artifact.topic, content: artifact.content, raw_data: artifact.raw_data, status: artifact.status, version: artifact.version_number, version_id: artifact.version_id, export_formats: artifact.export_formats }); }
    catch (reason) { setError({ kind: 'export', message: reason instanceof Error ? reason.message : '导出失败；正文仍可查看。' }); }
  }

  if (loading) return <section className={styles.state} role="status"><LoaderCircle className={styles.spin} />正在加载学习资源与版本…</section>;
  if (!artifact) return <section className={styles.state} role="alert"><p>{error?.message || '学习资源不存在或无权访问'}</p><button onClick={() => void load()}>重新加载</button></section>;
  const daryaReviewPassed = artifact.canonical?.contract === 'darya.artifact.v1'
    && String(artifact.canonical?.status.review_verdict || '').toUpperCase() === 'PASS';
  // 学习工具类产物（记忆卡/错题本/音频课/教学绘本）不走 canonical 教学契约：它们在生成时
  // 已通过各自的机械校验（卡组 schema / 判分记录绑定 / TTS 媒体探针 / 绘本页面与体积契约），
  // 由专用渲染器展示。
  const studyToolTypes = ['study_flashcard_deck', 'personalized_mistake_book', 'study_audio_lesson', 'study_picture_book'];
  const isStudyTool = studyToolTypes.includes(String(artifact.type || ''));
  if (daryaHost && !daryaReviewPassed && !isStudyTool) {
    return <section className={styles.state} role="alert"><p>这份资料还没有通过正式检查，暂时不会在 DARYA 中打开。</p></section>;
  }

  const content = artifact.render_payload.content || '';
  const whyThis = typeof artifact.canonical?.personalization?.why_this === 'string' ? artifact.canonical.personalization.why_this : '';
  return <section className={`${styles.shell} ${focusMode ? styles.focus : ''}`} data-testid="artifact-shell" data-mode={context?.mode || 'page'} data-product={daryaHost ? 'darya' : 'legacy'}>
    <header className={styles.header}>
      <div className={styles.identity}><span className={styles.kind}>{isTieredAssessment ? '分层测评' : kindLabel(artifact.kind)}</span><div><h1>{artifact.title}</h1><p>{artifact.topic || '当前学习主题'}{artifact.graph_node_title ? ` · ${artifact.graph_node_title}` : ''}</p></div></div>
      <div className={styles.headerActions}>
        {daryaHost
          ? <span className={styles.status}>v{artifact.version_number}</span>
          : <label className={styles.versionSelect}>版本<select value={artifact.version_number} onChange={event => void load({ version: Number(event.target.value) })}>{artifact.available_versions.map(item => <option key={item.version_id} value={item.version_number}>v{item.version_number}{item.is_current ? ' · 最新' : ''}</option>)}</select></label>}
        <span className={styles.status}>{statusLabel(artifact.canonical?.status.canonical_status || artifact.status)}</span>
        {!daryaHost && <button className={styles.primaryAction} onClick={() => askAi()}><MessageCircle size={17} />问 {assistantName}</button>}
        {!daryaHost && canGenerateVersion && <button className={styles.primaryAction} onClick={() => setRevisionOpen(value => !value)}><Sparkles size={17} />生成新版本</button>}
        {!daryaHost && <details className={styles.more}><summary aria-label="更多操作"><MoreHorizontal size={19} /></summary><div><button onClick={() => void openCompare()}><Columns2 size={15} />比较版本</button><button onClick={() => void exportArtifact()}><Download size={15} />导出</button><button onClick={() => window.open(`/artifacts/${artifact.artifact_id}?version=${artifact.version_number}`, '_blank')}><Expand size={15} />全屏</button><button onClick={() => setFocusMode(value => !value)}><Focus size={15} />{focusMode ? '退出专注' : '专注模式'}</button></div></details>}
        {onClose && <button className={styles.close} onClick={onClose} aria-label="返回聊天"><ChevronLeft /><span>返回聊天</span></button>}
      </div>
    </header>

    {error && <div className={styles.error} role="alert"><div><strong>{error.kind === 'generate' ? '新版本没有生成' : error.kind === 'render' ? '部分内容渲染失败' : '操作未完成'}</strong><span>{error.message}</span></div><button onClick={() => setError(null)}>知道了</button></div>}
    {whyThis && <details className={styles.whyThis} data-testid="artifact-why-this"><summary>{daryaHost ? '为什么现在给你这个' : '为什么是这个'}</summary><p>{whyThis}</p></details>}
    {newVersion && <div className={styles.newVersion}><div><strong>新版本 v{newVersion.version_number} 已生成</strong><span>你仍在阅读 v{artifact.version_number}；旧版没有被覆盖。</span></div><button onClick={() => { const version = newVersion; setNewVersion(null); void load({ versionId: version.version_id }); }}>切换到 v{newVersion.version_number}</button></div>}
    {revisionOpen && canGenerateVersion && <div className={styles.revision}><label>你希望怎样修改这份讲义？<textarea value={instruction} onChange={event => setInstruction(event.target.value)} placeholder="例如：增加一个生活化例子，并把第三节解释得更直观。" /></label><div><button onClick={() => setRevisionOpen(false)}>取消</button><button disabled={!instruction.trim() || generating} onClick={() => void generateVersion()}>{generating ? '正在生成…' : '生成不可变新版本'}</button></div></div>}
    {isDocument && !daryaHost && <section className={styles.learningChain} aria-label="根据这份讲义继续学习">
      <div><strong>继续学习</strong><span>{assistantName} 会保留这份讲义作为来源，带你进入下一步。</span></div>
      <div>
        <button onClick={() => void runDerivedAction('EXERCISE')} disabled={derivedAction !== null}>
          {derivedAction === 'EXERCISE' ? <><Loader2 size={14} className={styles.spin} /> 生成中…</> : '生成练习'}
        </button>
        <button onClick={() => void runDerivedAction('KNOWLEDGE_CARD')} disabled={derivedAction !== null}>
          {derivedAction === 'KNOWLEDGE_CARD' ? <><Loader2 size={14} className={styles.spin} /> 生成中…</> : '生成知识卡'}
        </button>
        <button onClick={() => void runDerivedAction('MINDMAP')} disabled={derivedAction !== null}>
          {derivedAction === 'MINDMAP' ? <><Loader2 size={14} className={styles.spin} /> 生成中…</> : '生成导图'}
        </button>
        <button onClick={() => void runDerivedAction('QUIZ')} disabled={derivedAction !== null}>
          {derivedAction === 'QUIZ' ? <><Loader2 size={14} className={styles.spin} /> 生成中…</> : '开始测验'}
        </button>
        <button onClick={() => void runStudyAction('flashcards')} disabled={studyAction !== null}>
          {studyAction === 'flashcards' ? <><Loader2 size={14} className={styles.spin} /> 生成中…</> : '生成记忆卡'}
        </button>
        <button onClick={() => void runStudyAction('audio')} disabled={studyAction !== null}>
          {studyAction === 'audio' ? <><Loader2 size={14} className={styles.spin} /> 生成中…</> : '生成音频课'}
        </button>
      </div>
    </section>}
    {derivedResult && derivedResult.artifact_id > 0 && (
      <div className={styles.newVersion} data-testid="artifact-derived-result">
        <div><strong>{derivedResult.title}</strong><span>{derivedResult.summary}</span></div>
        <button onClick={openDerived}>{derivedResult.kind === 'mindmap' ? '查看导图' : derivedResult.kind === 'knowledge_card' ? '查看知识卡' : '查看'} ›</button>
      </div>
    )}
    {derivedError && !derivedAction && (
      <div className={styles.error} role="alert">
        <div><strong>生成没有完成</strong><span>{derivedError}</span></div>
        <button onClick={() => setDerivedError('')}>知道了</button>
      </div>
    )}
    {selection && !daryaHost && <div className={styles.selection}><div><strong>{selection.range_label}</strong><span>{selection.summary}</span><small>本次询问会绑定 v{selection.artifact_version_number}；完整选中文字只随当前请求发送。</small></div><button onClick={() => askAi()}>问问 {assistantName}</button><button onClick={() => setSelection(null)}>清除</button></div>}

    {compareOpen && comparison ? <VersionComparisonView comparison={comparison} onChoose={version => void load({ version })} /> : <div className={`${styles.readerLayout} ${(!isDocument || hasCanonicalDocument || focusMode) ? styles.readerFull : ''}`}>
      {isDocument && !hasCanonicalDocument && !focusMode && <aside className={`${styles.tocPane} ${tocOpen ? '' : styles.tocCollapsed}`}><button className={styles.tocToggle} onClick={() => setTocOpen(value => !value)}>{tocOpen ? <ChevronLeft /> : <BookOpen />}</button>{tocOpen && <DocumentTableOfContents content={content} onNavigate={anchor => document.getElementById(anchor)?.scrollIntoView({ behavior: 'smooth', block: 'start' })} />}</aside>}
      <div ref={bodyRef} className={`${styles.body} ${isCode ? styles.codeBody : ''}`} onScroll={savePosition}>
        {isDocument ? (hasCanonicalDocument ? <CanonicalBlockRenderer artifact={artifact} onSelection={daryaHost ? undefined : setSelection} /> : <DocumentRenderer artifact={artifact} onSelection={daryaHost ? undefined : setSelection} />)
          : isCode ? <CodeRenderer
            artifact={artifact}
            threadId={context?.threadId}
            onSelection={setSelection}
            onAskAi={daryaHost ? undefined : reference => askAi(reference)}
            onNewVersion={setNewVersion}
            assistantName={assistantName}
            product={daryaHost ? 'darya' : 'legacy'}
          />
              : isInteractive ? <InteractiveQuestionRenderer artifact={artifact} threadId={context?.threadId} onAskAi={daryaHost ? undefined : reference => askAi(reference)} assistantName={assistantName} product={daryaHost ? 'darya' : 'legacy'} />
                : isQuestion ? <QuestionPracticeRenderer artifact={artifact} onAskAi={daryaHost ? undefined : () => askAi()} assistantName={assistantName} />
              : <ArtifactRenderer artifact={{ artifact_id: artifact.artifact_id, kind: artifact.kind, type: artifact.type, title: artifact.title, topic: artifact.topic, content: artifact.content, raw_data: artifact.raw_data, status: artifact.status, version: artifact.version_number, version_id: artifact.version_id, export_formats: artifact.export_formats }} />}
      </div>
    </div>}
  </section>;
}

export default ArtifactShell;
