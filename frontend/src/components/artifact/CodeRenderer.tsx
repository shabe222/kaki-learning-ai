import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Clock3, Code2, LoaderCircle, MessageCircle, Play, RefreshCw, RotateCcw, Save, Send, TerminalSquare, XCircle } from 'lucide-react';
import { artifactApi, type ArtifactCodeRun, type ArtifactPublicProjection, type ArtifactSelectionReference, type CodeLabResult, type CodeLabSubmission, type CodeLabSubmitResponse } from '../../api/artifacts';
import { notifyDaryaLearningRouteUpdated } from '../../features/tide/learningRouteEvents';
import styles from './CodeRenderer.module.css';

function nonce(prefix: string) {
  return `${prefix}:${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
}

function statusText(status?: ArtifactCodeRun['status']) {
  const map: Record<string, string> = {
    PENDING: '等待运行', RUNNING: '运行中', SUCCEEDED: '运行成功', FAILED: '运行失败',
    TIMED_OUT: '运行超时', SANDBOX_UNAVAILABLE: '沙箱暂不可用',
  };
  return status ? map[status] || status : '尚未运行';
}

function lineNumbered(source: string) {
  return source.split('\n').map((line, index) => ({ number: index + 1, text: line }));
}

/** 把本机绝对路径从默认界面上隐去；完整原文保留在折叠的“技术详情”里。 */
function maskLocalPaths(text: string) {
  return text
    .replace(/[A-Za-z]:\\[^\s'":]+/g, '<本机路径>')
    .replace(/(?:\/home|\/tmp|\/Users|\/var|\/root)\/[^\s'":]+/g, '<本机路径>');
}

interface RunFailureDescription {
  summary: string;
  locate: string;
  advice: string;
}

const pyFailureCopy: Array<{ test: RegExp; summary: string; advice: string }> = [
  { test: /SyntaxError/i, summary: '这段代码有语法问题，Python 还没有真正运行就停下了。', advice: '检查报错行附近是否少了冒号、括号没有配对，或者缩进不对。' },
  { test: /IndentationError/i, summary: '缩进不一致，Python 靠缩进来理解代码结构。', advice: '检查报错行及其上一行的缩进层级是否统一。' },
  { test: /NameError|UnboundLocalError/i, summary: '用到了一个还没有定义（或还没赋值）的名字。', advice: '检查是变量名拼写不一致，还是在赋值之前就使用了它；函数内先赋值再使用，必要时确认是否忘了声明。' },
  { test: /TypeError/i, summary: '把不匹配的数据类型用在了一起。', advice: '确认参与运算或传参的值类型，需要时先做转换。' },
  { test: /ValueError/i, summary: '传入的值内容不合适，无法处理。', advice: '检查传入的值是否符合函数的要求，比如能否转成数字。' },
  { test: /IndexError/i, summary: '访问的下标超出了序列的范围。', advice: '打印列表长度看看，注意下标从 0 开始。' },
  { test: /KeyError/i, summary: '访问了字典里不存在的键。', advice: '先确认键名拼写，或用 get 访问可能缺失的键。' },
  { test: /AttributeError/i, summary: '在这个对象上找不到对应的方法或属性。', advice: '确认对象类型，检查方法名是否拼写正确。' },
  { test: /ModuleNotFoundError|ImportError/i, summary: '导入模块没有成功。', advice: '检查模块名拼写；运行环境里只预装了标准库。' },
  { test: /ZeroDivisionError/i, summary: '出现了除以 0 的情况。', advice: '在除法前检查分母是否可能为 0。' },
  { test: /AssertionError/i, summary: '有一处断言没有通过。', advice: '对照断言内容检查：程序结果和预期差在哪里。' },
  { test: /RecursionError/i, summary: '递归太深，没有正常停下来。', advice: '检查递归的结束条件是否总能被触发。' },
  { test: /TimeoutError|timed out/i, summary: '程序运行超时被终止。', advice: '检查有没有死循环，或把计算量拆小再试。' },
];

/** 从真实 stderr 提炼一句学习者能读懂的错误描述。只做展示，不影响判定。 */
function describeRunFailure(stderr: string, errorLine?: number | null): RunFailureDescription {
  const clean = stderr.replace(/\r/g, '');
  const lines = clean.split('\n').filter((line) => line.trim().length > 0);
  const tracebackStart = lines.map((line) => line.trim().startsWith('Traceback (most recent call last)')).lastIndexOf(true);
  const exceptionLine = [...lines.slice(tracebackStart >= 0 ? tracebackStart + 1 : 0)]
    .reverse()
    .find((line) => !line.trim().startsWith('File ')) || lines[lines.length - 1] || '';
  const matched = pyFailureCopy.find((entry) => entry.test.test(exceptionLine)) || pyFailureCopy.find((entry) => entry.test.test(clean));
  const detail = maskLocalPaths(exceptionLine.trim()).slice(0, 200);
  const summary = matched
    ? matched.summary
    : '程序在运行中报错停止了，具体信息在下方技术详情里。';
  const locate = errorLine
    ? `第 ${errorLine} 行附近${exceptionLine ? ` · ${detail}` : ''}`
    : detail || '请展开技术详情查看完整输出。';
  return {
    summary,
    locate,
    advice: matched?.advice || '先读一遍报错里最后的异常名和提示，再回到对应代码行检查。',
  };
}

export function CodeRenderer({
  artifact,
  threadId,
  onSelection,
  onAskAi,
  onNewVersion,
  assistantName = 'DARYA',
  product = 'legacy',
}: {
  artifact: ArtifactPublicProjection;
  threadId?: string | null;
  onSelection?: (selection: ArtifactSelectionReference) => void;
  onAskAi?: (selection?: ArtifactSelectionReference) => void;
  onNewVersion?: (artifact: ArtifactPublicProjection) => void;
  assistantName?: string;
  product?: 'legacy' | 'darya';
}) {
  const [runs, setRuns] = useState<ArtifactCodeRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState('');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(artifact.render_payload.content || '');
  const [revisionInstruction, setRevisionInstruction] = useState('');
  const [savingVersion, setSavingVersion] = useState(false);
  const [revisionError, setRevisionError] = useState('');
  const [submissions, setSubmissions] = useState<CodeLabSubmission[]>([]);
  const [submission, setSubmission] = useState<CodeLabSubmitResponse | null>(null);
  const [assessmentResult, setAssessmentResult] = useState<CodeLabResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submissionError, setSubmissionError] = useState('');
  const codeRef = useRef<HTMLPreElement>(null);
  const draftRef = useRef<HTMLTextAreaElement>(null);
  const currentSelectionRef = useRef<ArtifactSelectionReference | undefined>(undefined);
  const runningRef = useRef(false);
  const runIdentityRef = useRef<{ signature: string; requestId: string; idempotencyKey: string } | null>(null);
  const versionIdentityRef = useRef<{ signature: string; idempotencyKey: string } | null>(null);
  const submissionIdentityRef = useRef<{ signature: string; requestId: string; idempotencyKey: string } | null>(null);
  const source = artifact.render_payload.content || '';
  const isCodeLab = artifact.canonical?.purpose.experience === 'PERSONALIZED_CODE_LAB';
  const lines = useMemo(() => lineNumbered(editing ? draft : source), [draft, editing, source]);
  const selectedRun = runs.find(run => run.run_id === selectedRunId) || runs[0] || null;

  async function refreshRuns() {
    setRunError('');
    try {
      const rows = await artifactApi.listCodeRuns(artifact.artifact_id, artifact.version_id);
      setRuns(rows);
      setSelectedRunId(current => current && rows.some(row => row.run_id === current) ? current : rows[0]?.run_id || null);
    } catch (error) {
      setRunError(error instanceof Error ? error.message : '运行历史暂时无法加载');
    }
  }

  useEffect(() => {
    setDraft(source);
    setEditing(isCodeLab);
    setRevisionInstruction('');
    void refreshRuns();
    if (isCodeLab) {
      void artifactApi.listCodeLabSubmissions(artifact.artifact_id, artifact.version_id).then(rows => setSubmissions(rows)).catch(() => setSubmissions([]));
      void artifactApi.getCodeLabResult(artifact.artifact_id, artifact.version_id).then(setAssessmentResult).catch(() => setAssessmentResult(null));
    } else { setSubmissions([]); setAssessmentResult(null); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifact.artifact_id, artifact.version_id, source, isCodeLab]);

  async function runCurrentVersion() {
    // 历史产物属于它创建时的会话；运行优先跟随产物自己的归属线程，避免跨会话误判。
    const effectiveThread = artifact.thread_id || threadId;
    if (!effectiveThread || runningRef.current) {
      if (!effectiveThread) setRunError('当前资源没有可验证的会话身份，暂时无法运行。');
      return;
    }
    runningRef.current = true;
    setRunning(true); setRunError('');
    const runSource = isCodeLab ? draft : source;
    const signature = `${effectiveThread}:${artifact.artifact_id}:${artifact.version_id}:${runSource}`;
    if (!runIdentityRef.current || runIdentityRef.current.signature !== signature) {
      runIdentityRef.current = {
        signature, requestId: nonce('code-run-request'),
        idempotencyKey: nonce(`code-run:${artifact.artifact_id}:${artifact.version_id}`),
      };
    }
    try {
      const response = await artifactApi.runCode(artifact.artifact_id, {
        thread_id: effectiveThread,
        artifact_version_id: artifact.version_id,
        execution_request_id: runIdentityRef.current.requestId,
        idempotency_key: runIdentityRef.current.idempotencyKey,
        parameters: {},
        source: isCodeLab ? draft : undefined,
      });
      setRuns(current => [response.run, ...current.filter(row => row.run_id !== response.run.run_id)]);
      setSelectedRunId(response.run.run_id);
      runIdentityRef.current = null;
    } catch (error) {
      setRunError(error instanceof Error ? error.message : '代码运行失败；当前版本仍然可用。');
    } finally { runningRef.current = false; setRunning(false); }
  }

  function selectionReference(selectedText: string, startLine: number, count: number): ArtifactSelectionReference {
    return {
      artifact_id: artifact.artifact_id, artifact_version_id: artifact.version_id,
      artifact_version_number: artifact.version_number, section_anchor: `code-line-${startLine}`,
      range_label: `代码第 ${startLine}${count > 1 ? `—${startLine + count - 1}` : ''} 行`,
      summary: selectedText.replace(/\s+/g, ' ').slice(0, 160), selected_text: selectedText.slice(0, 1200),
      content_checksum: artifact.content_digest, block_id: 'code-lab-source',
      purpose: '解释选中代码但不代替运行或提交',
    };
  }

  function captureSelection() {
    if (!onSelection || !codeRef.current || !window.getSelection) return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    const common = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? range.commonAncestorContainer as Element
      : range.commonAncestorContainer.parentElement;
    if (!common || !codeRef.current.contains(common)) return;
    const selectedText = selection.toString().split('\u0000').join('').trim();
    if (!selectedText) return;
    try {
      const prefix = document.createRange();
      prefix.selectNodeContents(codeRef.current);
      prefix.setEnd(range.startContainer, range.startOffset);
      const startLine = Math.max(1, prefix.toString().split('\n').length);
      const count = Math.max(1, selectedText.split('\n').length);
      const reference = selectionReference(selectedText, startLine, count);
      currentSelectionRef.current = reference; onSelection(reference);
    } catch { /* selection remains optional */ }
  }

  function captureDraftSelection() {
    const element = draftRef.current;
    if (!element || element.selectionStart === element.selectionEnd) return;
    const selectedText = draft.slice(element.selectionStart, element.selectionEnd).trim();
    if (!selectedText) return;
    const startLine = draft.slice(0, element.selectionStart).split('\n').length;
    const count = Math.max(1, selectedText.split('\n').length);
    const reference = selectionReference(selectedText, startLine, count);
    currentSelectionRef.current = reference; onSelection?.(reference);
  }

  async function submitWorkingCopy() {
    const effectiveThread = threadId || artifact.thread_id;
    if (!effectiveThread || submitting || !draft.trim()) return;
    setSubmitting(true); setSubmissionError('');
    const signature = `${effectiveThread}:${artifact.artifact_id}:${artifact.version_id}:${draft}`;
    if (!submissionIdentityRef.current || submissionIdentityRef.current.signature !== signature) {
      submissionIdentityRef.current = {
        signature, requestId: nonce('code-lab-submit'),
        idempotencyKey: nonce(`code-lab:${artifact.artifact_id}:${artifact.version_id}`),
      };
    }
    try {
      const response = await artifactApi.submitCodeLab(artifact.artifact_id, {
        thread_id: effectiveThread, artifact_version_id: artifact.version_id, source: draft,
        submission_request_id: submissionIdentityRef.current.requestId,
        idempotency_key: submissionIdentityRef.current.idempotencyKey,
      });
      setSubmission(response);
      setSubmissions(current => [response.attempt, ...current.filter(row => row.attempt_id !== response.attempt.attempt_id)]);
      setAssessmentResult(await artifactApi.replanCodeLab(artifact.artifact_id, {
        artifact_version_id: artifact.version_id, attempt_id: response.attempt.attempt_id,
      }));
      notifyDaryaLearningRouteUpdated(effectiveThread, 'code_submission');
      submissionIdentityRef.current = null;
    } catch (error) {
      setSubmissionError(error instanceof Error ? error.message : '提交没有完成；工作代码仍然保留。');
    } finally { setSubmitting(false); }
  }

  async function createVersion() {
    const effectiveThread = threadId || artifact.thread_id;
    if (!effectiveThread || !revisionInstruction.trim() || savingVersion) return;
    setSavingVersion(true); setRevisionError('');
    try {
      const instruction = `${revisionInstruction.trim()}\n\n学习者编辑草稿：\n${draft.slice(0, 12000)}`;
      const signature = `${artifact.artifact_id}:${artifact.version_number}:${instruction}`;
      if (!versionIdentityRef.current || versionIdentityRef.current.signature !== signature) {
        versionIdentityRef.current = { signature, idempotencyKey: nonce(`code-version:${artifact.artifact_id}:v${artifact.version_number}`) };
      }
      const result = await artifactApi.reviseCode(artifact.artifact_id, {
        thread_id: effectiveThread,
        instruction,
        expected_version: artifact.version_number,
        idempotency_key: versionIdentityRef.current.idempotencyKey,
      });
      onNewVersion?.(result);
      setEditing(false);
      setRevisionInstruction('');
      versionIdentityRef.current = null;
    } catch (error) {
      setRevisionError(error instanceof Error ? error.message : '新版本生成失败；当前版本与运行记录未改变。');
    } finally { setSavingVersion(false); }
  }

  return <section className={styles.codeRenderer} data-testid="code-renderer" data-single-file="true" data-product={product}>
    {isCodeLab && <section className={styles.labBrief} data-testid="code-lab-brief">
      <span>迁移任务</span><strong>修复闭包计数器，让状态可以持续更新且互不串联</strong>
      <p>先用「运行代码」试一试当前的想法；只有「提交判定」会计入后面的学习安排。</p>
    </section>}
    <div className={styles.fileBar}>
      <div><Code2 size={17} /><strong>main.py</strong><span>Python · 单文件</span></div>
      <div>
        {!isCodeLab && <button type="button" onClick={() => { setEditing(value => !value); setDraft(source); }}>{editing ? '退出编辑' : '编辑草稿'}</button>}
        {isCodeLab && <button type="button" onClick={() => { setDraft(source); setSubmission(null); setSubmissionError(''); currentSelectionRef.current = undefined; }}><RotateCcw size={15} />重置起始代码</button>}
        <button className={styles.runButton} type="button" disabled={running || (editing && !isCodeLab)} onClick={() => void runCurrentVersion()}>
          {running ? <LoaderCircle className={styles.spin} size={16} /> : <Play size={16} />}{running ? '运行中' : isCodeLab ? '运行代码' : '运行当前版本'}
        </button>
        {isCodeLab && <button className={styles.submitButton} type="button" disabled={submitting || !draft.trim()} onClick={() => void submitWorkingCopy()}>{submitting ? <LoaderCircle className={styles.spin} size={16} /> : <Send size={16} />}{submitting ? '判定中' : '提交判定'}</button>}
      </div>
    </div>

    <div className={styles.mainGrid}>
      <div className={styles.editorPane}>
        {editing ? <textarea ref={draftRef} aria-label={isCodeLab ? 'Code Lab 工作代码' : '代码编辑草稿'} value={draft} onSelect={captureDraftSelection} onChange={event => { setDraft(event.target.value); setSubmission(null); setAssessmentResult(null); setSubmissionError(''); }} /> :
          <pre ref={codeRef} onMouseUp={captureSelection} onKeyUp={captureSelection} tabIndex={0} aria-label="代码正文">
            {lines.map(line => <div key={line.number} id={`code-line-${line.number}`} data-line={line.number}><span>{line.number}</span><code>{line.text || ' '}</code></div>)}
          </pre>}
        {editing && !isCodeLab && <div className={styles.revisionPanel}>
          <label>修改要求<textarea value={revisionInstruction} onChange={event => setRevisionInstruction(event.target.value)} placeholder="例如：把学习率改成可配置参数，并补充震荡观察。" /></label>
          <p>保存会创建不可变新版本；v{artifact.version_number}和它的运行历史不会被覆盖。</p>
          {revisionError && <div className={styles.inlineError}>{revisionError}</div>}
          <button type="button" disabled={!revisionInstruction.trim() || savingVersion} onClick={() => void createVersion()}><Save size={15} />{savingVersion ? '正在生成新版本…' : '生成新版本'}</button>
        </div>}
      </div>

      <aside className={styles.runPane}>
        <header><div><TerminalSquare size={17} /><strong>运行结果</strong></div><button type="button" onClick={() => void refreshRuns()}><RefreshCw size={15} />刷新</button></header>
        {runError && <div className={styles.inlineError}>{runError}</div>}
        {submissionError && <div className={styles.inlineError}>{submissionError}</div>}
        {!selectedRun && !running && <div className={styles.emptyRun}><Clock3 /><strong>代码已生成，尚未运行</strong><span>运行结果会绑定当前版本，不会串到其他版本。</span></div>}
        {selectedRun && <>
          <div className={styles.runSummary} data-status={selectedRun.status}>
            {selectedRun.status === 'SUCCEEDED' ? <CheckCircle2 /> : selectedRun.status === 'RUNNING' ? <LoaderCircle className={styles.spin} /> : <XCircle />}
            <div><strong>{statusText(selectedRun.status)}</strong><span>只是先试一试，不会当作正式作答。</span></div>
          </div>
          <section><h3>运行输出</h3><pre>{selectedRun.stdout || '（没有输出）'}</pre></section>
          {selectedRun.stderr ? (() => {
            const failure = describeRunFailure(selectedRun.stderr, selectedRun.error_line);
            return (
              <section className={styles.failureBlock} data-testid="code-lab-error" data-failed={selectedRun.status === 'FAILED'}>
                <h3>发生了什么</h3>
                <p className={styles.failureSummary}>{failure.summary}</p>
                <div className={styles.failureRow}><span>定位</span><code>{failure.locate}</code></div>
                <div className={styles.failureRow}><span>可以先检查</span>{failure.advice}</div>
                <details className={styles.techDetails}>
                  <summary>技术详情（完整错误输出）</summary>
                  <p className={styles.techMeta}>Run {selectedRun.run_number} · v{selectedRun.artifact_version_number} · {selectedRun.duration_ms ?? 0} ms · exit {selectedRun.exit_code ?? '—'}</p>
                  <pre>{selectedRun.stderr}</pre>
                </details>
              </section>
            );
          })() : <section><h3>错误输出</h3><pre>（没有错误输出）</pre></section>}
          {selectedRun.error_line && <button type="button" className={styles.errorJump} onClick={() => document.getElementById(`code-line-${selectedRun.error_line}`)?.scrollIntoView({ block: 'center' })}><AlertTriangle size={15} />定位到第 {selectedRun.error_line} 行</button>}
        </>}
        {runs.length > 0 && <div className={styles.history}><strong>当前版本运行记录</strong>{runs.map(run => <button key={run.run_id} data-active={run.run_id === selectedRun?.run_id} onClick={() => setSelectedRunId(run.run_id)}>Run {run.run_number} · v{run.artifact_version_number}<span>{statusText(run.status)}</span></button>)}</div>}
        {isCodeLab && submission && <section className={styles.judgment} data-testid="code-lab-judgment" data-correct={submission.attempt.correct}>
          <h3>{submission.attempt.correct ? '提交通过' : '提交未通过'}</h3>
          <strong>{submission.attempt.tests_passed} / {submission.attempt.tests_total} 项检查通过</strong>
          <p>{submission.attempt.correct ? '这次提交已经记入你的学习安排。' : '这次提交已保存；可以继续修改后再提交一次。'}</p>
        </section>}
        {isCodeLab && assessmentResult && <section className={styles.nextPlan} data-testid="code-lab-result">
          <h3>{assessmentResult.title}</h3><p>{assessmentResult.demonstrated}</p>
          <dl><div><dt>仍需处理</dt><dd>{assessmentResult.remaining}</dd></div><div><dt>下一步</dt><dd>{assessmentResult.recommendation}</dd></div><div><dt>为什么改变</dt><dd>{assessmentResult.why_changed}</dd></div></dl>
        </section>}
        {isCodeLab && submissions.length > 0 && <div className={styles.submissionHistory}><strong>正式提交记录</strong>{submissions.map(row => <span key={row.attempt_id}>第 {row.attempt_number} 次 · {row.correct ? '通过' : '未通过'} · {row.tests_passed}/{row.tests_total}</span>)}</div>}
      </aside>
    </div>
    {onAskAi && <div className={styles.footerActions}><button type="button" onClick={() => onAskAi(currentSelectionRef.current)}><MessageCircle size={16} />问 {assistantName}{currentSelectionRef.current ? '选中代码' : ''}</button></div>}
  </section>;
}
