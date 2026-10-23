import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, CheckCircle2, HelpCircle, LoaderCircle, MessageCircle, RotateCcw, XCircle } from 'lucide-react';
import { artifactApi, type ArtifactPublicProjection, type ArtifactSelectionReference, type InteractiveAttempt, type InteractiveAttemptResponse, type InteractiveAttemptState, type TieredAssessmentResult } from '../../api/artifacts';
import { learningEvaluatorApi, type PublicLearningEvaluation } from '../../api/learningEvaluator';
import { notifyDaryaLearningRouteUpdated } from '../../features/tide/learningRouteEvents';
import styles from './InteractiveQuestionRenderer.module.css';

function nonce(prefix: string) {
  return `${prefix}:${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
}

type Slide = { title?: string; content?: string; stage?: string; interaction?: { question?: string; prompt?: string; options?: string[]; explanation?: string; hint?: string } };

const STAGE_LABEL: Record<string, string> = {
  FOUNDATION: '基础辨认', UNDERSTANDING: '理解解释', APPLICATION: '应用判断', TRANSFER: '迁移挑战',
};

function readableEvidenceGaps(values: string[]) {
  const visible = values.filter(value => !/^[A-Z0-9_:-]+$/.test(value));
  return visible.length ? visible.join('、') : '换几个不同的情境再练一练';
}

export function InteractiveQuestionRenderer({
  artifact,
  threadId,
  onAskAi,
  assistantName = 'DARYA',
  product = 'legacy',
}: {
  artifact: ArtifactPublicProjection;
  threadId?: string | null;
  onAskAi?: (selection?: ArtifactSelectionReference) => void;
  assistantName?: string;
  product?: 'legacy' | 'darya';
}) {
  const slides = useMemo(() => (artifact.render_payload.data.slides as Slide[] | undefined) || [], [artifact.render_payload.data.slides]);
  const interactiveIndexes = useMemo(() => slides.map((slide, index) => slide.interaction ? index : -1).filter(index => index >= 0), [slides]);
  const [state, setState] = useState<InteractiveAttemptState | null>(null);
  const [currentIndex, setCurrentIndex] = useState(interactiveIndexes[0] || 0);
  const [selected, setSelected] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [response, setResponse] = useState<InteractiveAttemptResponse | null>(null);
  const [hint, setHint] = useState('');
  const [solution, setSolution] = useState('');
  const [error, setError] = useState('');
  const [evaluation, setEvaluation] = useState<PublicLearningEvaluation | null>(null);
  const [evaluationError, setEvaluationError] = useState('');
  const [assessmentResult, setAssessmentResult] = useState<TieredAssessmentResult | null>(null);
  const [replanError, setReplanError] = useState('');
  const submittingRef = useRef(false);
  const submissionIdentityRef = useRef<{ signature: string; requestId: string; idempotencyKey: string } | null>(null);
  const effectiveThread = threadId || artifact.thread_id || '';
  const currentSlide = slides[currentIndex] || null;
  const interaction = currentSlide?.interaction || null;
  const itemIdentity = `slide-${currentIndex}`;
  const attempts = (state?.attempts || []).filter(item => item.item_identity === itemIdentity);
  const latestAttempt: InteractiveAttempt | null = attempts[attempts.length - 1] || null;
  const restoredSupport = state?.support_by_item?.[itemIdentity];
  const isTieredAssessment = artifact.canonical?.purpose?.experience === 'TIERED_ASSESSMENT';
  const entryTier = String((artifact.canonical?.purpose?.assessment_plan_public as Record<string, unknown> | undefined)?.entry_tier || '');

  async function restore() {
    if (!effectiveThread) return;
    setError('');
    try {
      const restored = await artifactApi.getInteractiveState(artifact.artifact_id, artifact.version_id, effectiveThread);
      setState(restored);
      const recoveredIndex = Number(String(restored.current_item || '').replace('slide-', ''));
      if (Number.isFinite(recoveredIndex) && slides[recoveredIndex]) setCurrentIndex(recoveredIndex);
      if (artifact.graph_id && artifact.graph_node_id) {
        try { setEvaluation(await learningEvaluatorApi.latest(String(artifact.graph_id), String(artifact.graph_node_id))); }
        catch { setEvaluation(null); }
      }
      if (isTieredAssessment && restored.attempts.length) {
        const last = restored.attempts[restored.attempts.length - 1];
        try { setAssessmentResult(await artifactApi.getTieredAssessmentResult(artifact.artifact_id, artifact.version_id, last.item_identity)); }
        catch { setAssessmentResult(null); }
      }
    } catch {
      // 没有可恢复的作答进度是常态（第一次打开、或历史资源换会话查看），按空状态继续，不打扰学习。
      setState(null);
    }
  }

  useEffect(() => {
    setResponse(null); setSelected(null); setHint(''); setSolution(''); setEvaluation(null); setEvaluationError(''); setAssessmentResult(null); setReplanError('');
    void restore();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifact.artifact_id, artifact.version_id, effectiveThread]);

  useEffect(() => {
    setResponse(null); setSelected(null); setHint(''); setSolution('');
    submissionIdentityRef.current = null;
  }, [currentIndex]);

  async function refreshEvaluation(attemptId: number, expectedEvidenceCount: number) {
    if (!artifact.graph_id || !artifact.graph_node_id) return;
    const graphId = String(artifact.graph_id);
    const nodeId = String(artifact.graph_node_id);
    try {
      const latest = await learningEvaluatorApi.latest(graphId, nodeId);
      if (latest && latest.evidence_summaries.length >= expectedEvidenceCount) {
        setEvaluation(latest);
        setEvaluationError('');
        return;
      }
      const evaluated = await learningEvaluatorApi.evaluate(graphId, nodeId, {
        request_id: `c1-attempt-${attemptId}`,
        idempotency_key: `c1-attempt-evaluation-${attemptId}`,
        expected_evaluation_revision: latest?.evaluation_revision || 0,
      });
      setEvaluation(evaluated);
      setEvaluationError('');
    } catch {
      setEvaluationError('本次作答已保存；掌握分析暂时没有更新，可稍后重试。');
    }
  }

  async function submit() {
    if (selected === null || submittingRef.current || !effectiveThread) {
      if (selected === null) setError('请先选择一个答案，再提交。');
      return;
    }
    submittingRef.current = true;
    setSubmitting(true); setError('');
    const signature = `${effectiveThread}:${artifact.artifact_id}:${artifact.version_id}:${itemIdentity}:${selected}`;
    if (!submissionIdentityRef.current || submissionIdentityRef.current.signature !== signature) {
      submissionIdentityRef.current = {
        signature, requestId: nonce('interactive-submit'),
        idempotencyKey: nonce(`interactive:${artifact.artifact_id}:${artifact.version_id}:${itemIdentity}`),
      };
    }
    try {
      const result = await artifactApi.submitInteractiveAnswer(artifact.artifact_id, {
        thread_id: effectiveThread,
        artifact_version_id: artifact.version_id,
        item_identity: itemIdentity,
        selected_answer: selected,
        submission_request_id: submissionIdentityRef.current.requestId,
        idempotency_key: submissionIdentityRef.current.idempotencyKey,
      });
      setResponse(result);
      submissionIdentityRef.current = null;
      await restore();
      if (artifact.graph_id && artifact.graph_node_id) {
        await refreshEvaluation(result.attempt.attempt_id, (state?.attempts.length || 0) + 1);
      }
      if (isTieredAssessment) {
        try {
          setAssessmentResult(await artifactApi.replanTieredAssessment(artifact.artifact_id, {
            artifact_version_id: artifact.version_id, item_identity: itemIdentity,
          }));
          notifyDaryaLearningRouteUpdated(effectiveThread, 'assessment');
          setReplanError('');
        } catch {
          setReplanError('作答与证据已经保存；下一资源暂未更新，可点击重试。');
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '答案没有保存，请重试。');
    } finally { submittingRef.current = false; setSubmitting(false); }
  }

  async function requestHint() {
    if (!effectiveThread) return;
    try {
      const result = await artifactApi.requestInteractiveHint(artifact.artifact_id, {
        thread_id: effectiveThread, artifact_version_id: artifact.version_id, item_identity: itemIdentity,
      });
      setHint(result.hint); setError('');
    } catch (reason) { setError(reason instanceof Error ? reason.message : '提示暂时不可用'); }
  }

  async function revealSolution() {
    if (!effectiveThread || !(response || latestAttempt)) return;
    try {
      const result = await artifactApi.viewInteractiveSolution(artifact.artifact_id, {
        thread_id: effectiveThread, artifact_version_id: artifact.version_id, item_identity: itemIdentity,
      });
      setSolution(result.explanation); setError('');
    } catch (reason) { setError(reason instanceof Error ? reason.message : '解析暂时不可用'); }
  }

  function askCurrentQuestion() {
    if (!onAskAi || !isTieredAssessment) { onAskAi?.(); return; }
    const block = artifact.canonical?.blocks.find(candidate =>
      candidate.type === 'QUESTION' && candidate.data.item_identity === itemIdentity,
    );
    const question = String(interaction?.question || interaction?.prompt || currentSlide?.content || '').trim();
    if (!block || !question) { onAskAi(); return; }
    onAskAi({
      artifact_id: artifact.artifact_id,
      artifact_version_id: artifact.version_id,
      artifact_version_number: artifact.version_number,
      section_anchor: block.block_id,
      range_label: STAGE_LABEL[currentSlide?.stage || ''] || '当前测评题',
      summary: question.slice(0, 160), selected_text: question.slice(0, 1200),
      content_checksum: artifact.content_digest, version_digest: artifact.content_digest,
      block_id: block.block_id, text_range: { start: 0, end: question.replace(/\s+/g, '').length },
      knowledge_refs: block.knowledge_refs, source_refs: block.source_refs,
      artifact_purpose: artifact.canonical?.purpose, purpose: '解释当前测评题但不代替正式提交',
    });
  }

  if (!interactiveIndexes.length) return <section className={styles.empty} data-testid="interactive-question-empty"><HelpCircle /><strong>这份资源暂时没有可作答题目</strong><span>你仍可以查看内容或返回相关讲义。</span></section>;

  const resultCorrect = response?.result.correct ?? latestAttempt?.correct;
  const totalPosition = Math.max(1, interactiveIndexes.indexOf(currentIndex) + 1);
  const semanticProgress = response?.progress || state?.progress;
  const savedThisQuestion = Boolean(response || latestAttempt);
  const allInteractiveAttempted = Boolean(semanticProgress?.all_interactive_items_attempted);
  const lessonViewingCompleted = Boolean(semanticProgress?.lesson_viewing_completed);
  return <section className={styles.renderer} data-testid="interactive-question-renderer" data-product={product}>
    <header className={styles.progressHeader}>
      <div><span>{isTieredAssessment ? '分层测评' : '互动课堂'}{isTieredAssessment && entryTier ? ` · 从${STAGE_LABEL[entryTier] || entryTier}进入` : ''}</span><strong>第 {totalPosition} / {interactiveIndexes.length} 步</strong></div>
      <div className={styles.progressBar}><span style={{ width: `${Math.round((totalPosition / interactiveIndexes.length) * 100)}%` }} /></div>
      <small>{isTieredAssessment ? '只有你真正提交的作答会影响后面的学习安排；查看提示不会算作完成。' : `这次作答会用于帮助${assistantName}判断你是否真正理解了这一点。`}</small>
      <div className={styles.semanticStatus} aria-live="polite">
        {response ? <span>作答保存成功</span> : savedThisQuestion ? <span>本题已作答</span> : <span>本题尚未作答</span>}
        {allInteractiveAttempted && <span>本组互动题已作答</span>}
        {lessonViewingCompleted && <span>互动课堂已完成</span>}
      </div>
    </header>

    <article className={styles.questionCard}>
      <p className={styles.stepLabel}>{currentSlide?.stage ? `${STAGE_LABEL[currentSlide.stage] || currentSlide.stage} · ` : ''}{currentSlide?.title || artifact.topic || '当前学习步骤'}</p>
      <h2>{interaction?.question || interaction?.prompt || currentSlide?.content || '请选择最合适的答案'}</h2>
      <div className={styles.options} role="radiogroup" aria-label="答案选项">
        {(interaction?.options || []).map((option, index) => <button
          key={index}
          type="button"
          role="radio"
          aria-checked={selected === index}
          data-selected={selected === index}
          data-result={resultCorrect !== undefined && selected === index ? (resultCorrect ? 'correct' : 'wrong') : undefined}
          disabled={submitting || Boolean(response)}
          onClick={() => { setSelected(index); setError(''); submissionIdentityRef.current = null; }}
        ><span>{String.fromCharCode(65 + index)}</span>{option}</button>)}
      </div>

      {hint && <div className={styles.hint}><strong>方向提示</strong><p>{hint}</p></div>}
      {!hint && restoredSupport?.hint_requested && <div className={styles.restoreNote}>这一步曾使用过提示；可再次点击查看。</div>}
      {!solution && restoredSupport?.solution_viewed && <div className={styles.restoreNote}>完整解析已查看；该状态已从服务端恢复。</div>}
      {error && <div className={styles.error} role="alert">{error}</div>}
      {(response || latestAttempt) && <div className={resultCorrect ? styles.correct : styles.wrong}>
        {resultCorrect ? <CheckCircle2 /> : <XCircle />}
        <div><strong>{resultCorrect ? '这次回答正确' : '这次还没有答对'}</strong><p>{resultCorrect ? '你的选择与服务端保存的正确性事实一致。' : '第一次错误会被保留，你可以先看提示，再形成新的重试记录。'}</p></div>
      </div>}
      {solution && <div className={styles.solution}><strong>完整解析</strong><p>{solution}</p></div>}

      <div className={styles.actions}>
        {!response && <button type="button" onClick={() => void requestHint()}><HelpCircle size={16} />给我一点提示</button>}
        {!response && <button className={styles.submit} type="button" disabled={submitting} onClick={() => void submit()}>{submitting ? <LoaderCircle className={styles.spin} /> : null}{submitting ? '提交中…' : '提交答案'}</button>}
        {(response || latestAttempt) && <button type="button" onClick={() => { setResponse(null); setSelected(null); setSolution(''); }}><RotateCcw size={16} />再试一次</button>}
        {(response || latestAttempt) && <button type="button" onClick={() => void revealSolution()}>查看完整解析</button>}
        {onAskAi && <button type="button" onClick={askCurrentQuestion}><MessageCircle size={16} />问 {assistantName}</button>}
      </div>
    </article>

    {evaluationError && <div className={styles.evaluationPending}><span>{evaluationError}</span>{latestAttempt && <button type="button" onClick={() => void refreshEvaluation(latestAttempt.attempt_id, state?.attempts.length || 1)}>重新分析</button>}</div>}
    {replanError && <div className={styles.evaluationPending}><span>{replanError}</span>{latestAttempt && <button type="button" onClick={async () => {
      try { setAssessmentResult(await artifactApi.replanTieredAssessment(artifact.artifact_id, { artifact_version_id: artifact.version_id, item_identity: latestAttempt.item_identity })); notifyDaryaLearningRouteUpdated(effectiveThread, 'assessment'); setReplanError(''); }
      catch { setReplanError('下一资源仍未更新，请稍后重试。'); }
    }}>重新安排下一步</button>}</div>}
    {evaluation && <aside className={styles.evaluation}>
      <strong>这次作答告诉我什么</strong>
      {evaluation.node_assessments.slice(0, 1).map(node => <div key={node.node_id}>
        <p>已经达标的学习目标：{node.objective_coverage.filter(item => item.achieved).length} 个</p>
        <p>还可以补一补：{node.evidence_gaps.length ? readableEvidenceGaps(node.evidence_gaps) : '暂时没有明显的缺口'}</p>
        <p>{node.completion_eligibility_signal === 'ELIGIBLE'
          ? '这一步你已经练得比较扎实了。'
          : '这一步还可以再巩固一下，多练几种不同的情境。'}</p>
      </div>)}
    </aside>}
    {assessmentResult && <aside className={styles.assessmentResult} data-testid="tiered-assessment-result" data-outcome={assessmentResult.outcome}>
      <div className={styles.resultEyebrow}>{({ ADVANCE: '继续提升', REMEDIATION: '先做定向补救', COMPLETE: '本次检验完成', CONTINUE: '继续本次检验' })[assessmentResult.outcome]}</div>
      <h3>这次作答之后</h3>
      <dl>
        <div><dt>已经看见</dt><dd>{assessmentResult.demonstrated}</dd></div>
        {assessmentResult.remaining && <div><dt>仍需处理</dt><dd>{assessmentResult.remaining}</dd></div>}
        <div><dt>下一步</dt><dd>{assessmentResult.recommendation}</dd></div>
        <div><dt>为什么改变</dt><dd>{assessmentResult.why_changed}</dd></div>
      </dl>
    </aside>}

    <footer className={styles.navigation}>
      <button type="button" disabled={totalPosition <= 1} onClick={() => setCurrentIndex(interactiveIndexes[Math.max(0, totalPosition - 2)])}><ArrowLeft />上一题</button>
      <span>当前版本 v{artifact.version_number} · 已恢复 {state?.attempts.length || 0} 次作答</span>
      <button type="button" disabled={totalPosition >= interactiveIndexes.length} onClick={() => setCurrentIndex(interactiveIndexes[Math.min(interactiveIndexes.length - 1, totalPosition)])}>下一题<ArrowRight /></button>
    </footer>
  </section>;
}
