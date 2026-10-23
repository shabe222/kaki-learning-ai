import { useMemo, useState } from 'react';
import { ArrowLeft, ArrowRight, CheckCircle2, HelpCircle, Lightbulb, MessageCircle, RotateCcw, XCircle } from 'lucide-react';
import type { ArtifactPublicProjection } from '../../api/artifacts';
import styles from './QuestionPracticeRenderer.module.css';

type PracticeQuestion = {
  id: string;
  type: string;
  stem: string;
  options: string[];
  answer: string;
  explanation: string;
  hint: string;
  knowledgePoint: string;
  difficulty: string;
};

type QuestionState = {
  selected: number | null;
  text: string;
  submitted: boolean;
  correct: boolean | null;
  hintShown: boolean;
};

const TYPE_LABEL: Record<string, string> = {
  choice: '选择题',
  single_choice: '选择题',
  multiple_choice: '多选题',
  fill_blank: '填空题',
  fill: '填空题',
  coding: '编程题',
  code: '编程题',
  case_study: '案例分析',
  short_answer: '简答题',
  essay: '论述题',
};

const DIFFICULTY_LABEL: Record<string, string> = {
  '1': '入门', '2': '基础', '3': '进阶', '4': '挑战', '5': '挑战',
  introductory: '入门', basic: '基础', intermediate: '进阶', advanced: '挑战',
};

function parseJson(value?: string): unknown {
  if (!value) return null;
  try { return JSON.parse(value); } catch { return null; }
}

function optionLetterPrefix(value: string) {
  return value.replace(/^[A-H]\s*[.、．]\s*/i, '');
}

function answerLetter(answer: unknown, options: string[]): string {
  const raw = String(answer ?? '').trim();
  if (!raw) return '';
  const leading = raw.match(/^[A-H](?=\s*[.、．]|\s|$)/i);
  if (leading) return leading[0].toUpperCase();
  const numeric = Number(raw);
  if (Number.isInteger(numeric) && numeric >= 0 && numeric < options.length) return String.fromCharCode(65 + numeric);
  const byText = options.findIndex(option => optionLetterPrefix(String(option)).trim() === raw);
  return byText >= 0 ? String.fromCharCode(65 + byText) : '';
}

function normalizeQuestion(raw: Record<string, unknown>, index: number): PracticeQuestion {
  const options = Array.isArray(raw.options) ? raw.options.map(option => String(option)) : [];
  return {
    id: String(raw.id || `q${index + 1}`),
    type: String(raw.type || (options.length ? 'choice' : 'short_answer')),
    stem: String(raw.question || raw.content || raw.title || `第 ${index + 1} 题`),
    options,
    answer: String(raw.answer ?? raw.correct_answer ?? ''),
    explanation: String(raw.explanation || raw.analysis || ''),
    hint: Array.isArray(raw.hints) && raw.hints.length ? String(raw.hints[0]) : String(raw.hint || ''),
    knowledgePoint: String(raw.knowledge_point || raw.knowledgePoint || ''),
    difficulty: DIFFICULTY_LABEL[String(raw.difficulty ?? '').toLowerCase()] || '',
  };
}

export function QuestionPracticeRenderer({
  artifact,
  onAskAi,
  assistantName = 'DARYA',
}: {
  artifact: ArtifactPublicProjection;
  onAskAi?: () => void;
  assistantName?: string;
}) {
  const questions = useMemo(() => {
    const data = (artifact.render_payload?.data || {}) as Record<string, unknown>;
    const fromPayload = Array.isArray(data.questions) ? data.questions : null;
    const fromRaw = Array.isArray(artifact.raw_data?.questions) ? artifact.raw_data.questions as unknown[] : null;
    const parsedContent = parseJson(artifact.content);
    const fromContent = Array.isArray(parsedContent)
      ? parsedContent
      : Array.isArray((parsedContent as Record<string, unknown> | null)?.questions)
        ? (parsedContent as Record<string, unknown[]>).questions
        : null;
    const list = fromPayload || fromRaw || fromContent || [];
    return list.map((item, index) => normalizeQuestion(item as Record<string, unknown>, index));
  }, [artifact]);

  const [started, setStarted] = useState(false);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [states, setStates] = useState<Record<string, QuestionState>>({});

  const current = questions[currentIndex] || null;
  const currentState: QuestionState = current
    ? states[current.id] || { selected: null, text: '', submitted: false, correct: null, hintShown: false }
    : { selected: null, text: '', submitted: false, correct: null, hintShown: false };
  const answeredCount = questions.filter(question => states[question.id]?.submitted).length;
  const choiceQuestions = questions.filter(question => question.options.length > 0);
  const correctCount = choiceQuestions.filter(question => states[question.id]?.submitted && states[question.id]?.correct === true).length;
  const selfCheckCount = choiceQuestions.filter(question => states[question.id]?.submitted && states[question.id]?.correct === null).length;
  const judgedChoiceCount = choiceQuestions.length - selfCheckCount;
  const allSubmitted = questions.length > 0 && answeredCount === questions.length;

  function patch(id: string, next: Partial<QuestionState>) {
    setStates(existing => {
      const base: QuestionState = existing[id] || { selected: null, text: '', submitted: false, correct: null, hintShown: false };
      return { ...existing, [id]: { ...base, ...next } };
    });
  }

  function submit() {
    if (!current || currentState.submitted) return;
    if (current.options.length) {
      if (currentState.selected === null) return;
      const expected = answerLetter(current.answer, current.options);
      // expected 为空说明答案键不是单个选项（如多选题 "AB"）。此时不能断言对错，
      // 保持 correct=null 表示「未判定」，由界面引导学员对照参考答案自查。
      patch(current.id, { submitted: true, correct: expected ? String.fromCharCode(65 + currentState.selected) === expected : null });
      return;
    }
    if (!currentState.text.trim()) return;
    patch(current.id, { submitted: true, correct: null });
  }

  function retry() {
    if (!current) return;
    patch(current.id, { selected: null, text: '', submitted: false, correct: null });
  }

  function restart() {
    setStates({});
    setCurrentIndex(0);
    setStarted(true);
  }

  if (!questions.length) {
    return <section className={styles.empty} data-testid="question-practice-empty">
      <HelpCircle />
      <strong>这份练习暂时没有可作答的题目</strong>
      <span>可以返回学习空间，看看这个节点下的其他学习资源。</span>
    </section>;
  }

  if (!started) {
    return <section className={styles.intro} data-testid="question-practice-intro">
      <span className={styles.introKind}>练习自测</span>
      <h2>{artifact.title}</h2>
      <p>共 {questions.length} 道题{choiceQuestions.length ? `，其中 ${choiceQuestions.length} 道选择题可以当场判断对错` : ''}。答完每道题就能看到解析；开放式题目会对照参考答案自查。</p>
      <small>自测结果只用于当场核对，不会写入你的学习档案。</small>
      <div><button type="button" className={styles.startButton} data-testid="practice-start" onClick={() => setStarted(true)}>开始自测</button></div>
    </section>;
  }

  const isChoice = Boolean(current?.options.length);
  return <section className={styles.renderer} data-testid="question-practice-renderer">
    <header className={styles.progressHeader}>
      <div><span>练习自测</span><strong>第 {currentIndex + 1} / {questions.length} 题</strong></div>
      <div className={styles.progressBar}><span style={{ width: `${Math.round(((currentIndex + 1) / questions.length) * 100)}%` }} /></div>
      <small>作答后立刻能看到解析，答错可以换个思路再试一次。</small>
      <div className={styles.semanticStatus} aria-live="polite">
        <span>已完成 {answeredCount} / {questions.length} 题</span>
        {allSubmitted && <span>自测已完成</span>}
      </div>
    </header>

    {current && <article className={styles.questionCard}>
      <p className={styles.stepLabel}>
        {TYPE_LABEL[current.type] || '问答题'}
        {current.knowledgePoint ? ` · ${current.knowledgePoint}` : ''}
        {current.difficulty ? ` · ${current.difficulty}` : ''}
      </p>
      <h2>{current.stem}</h2>

      {isChoice ? <div className={styles.options} role="radiogroup" aria-label="答案选项">
        {current.options.map((option, index) => <button
          key={index}
          type="button"
          role="radio"
          aria-checked={currentState.selected === index}
          data-selected={currentState.selected === index}
          data-result={currentState.submitted && currentState.selected === index && currentState.correct !== null
            ? (currentState.correct ? 'correct' : 'wrong')
            : currentState.submitted && currentState.correct === false && answerLetter(current.answer, current.options) === String.fromCharCode(65 + index)
              ? 'correct'
              : undefined}
          disabled={currentState.submitted && currentState.correct === true}
          data-testid="practice-option"
          onClick={() => patch(current.id, { selected: index })}
        ><span>{String.fromCharCode(65 + index)}</span>{optionLetterPrefix(option)}</button>)}
      </div> : <textarea
        className={styles.answerInput}
        data-testid="practice-answer-input"
        placeholder="写下你的回答；不需要很长，说清楚思路就好。"
        value={currentState.text}
        disabled={currentState.submitted}
        onChange={event => patch(current.id, { text: event.target.value })}
      />}

      {currentState.hintShown && current.hint && <div className={styles.hint}><strong>方向提示</strong><p>{current.hint}</p></div>}

      {currentState.submitted && isChoice && (currentState.correct === null
        ? <div className={styles.solution} data-testid="practice-feedback">
          <CheckCircle2 />
          <div>
            <strong>对照参考答案自查</strong>
            <p>这道题的答案不是单个选项，系统不替你判定对错。参考答案：{current.answer || '见解析'}。</p>
          </div>
        </div>
        : <div className={currentState.correct ? styles.correct : styles.wrong} data-testid="practice-feedback">
          {currentState.correct ? <CheckCircle2 /> : <XCircle />}
          <div>
            <strong>{currentState.correct ? '回答正确' : '这次还不对'}</strong>
            <p>{currentState.correct ? '继续保持，可以进入下一题。' : `参考答案是 ${answerLetter(current.answer, current.options) || '见解析'}；可以再试一次，或直接看解析。`}</p>
          </div>
        </div>)}
      {currentState.submitted && !isChoice && <div className={styles.solution} data-testid="practice-feedback">
        <strong>参考答案</strong>
        <p>{current.answer || '这份题目没有附带参考答案。'}</p>
      </div>}
      {currentState.submitted && current.explanation && <div className={styles.solution}><strong>解析</strong><p>{current.explanation}</p></div>}

      <div className={styles.actions}>
        {!currentState.submitted && current.hint && !currentState.hintShown && <button type="button" onClick={() => patch(current.id, { hintShown: true })}><Lightbulb size={16} />给我一点提示</button>}
        {!currentState.submitted && <button
          className={styles.submit}
          type="button"
          data-testid="practice-submit"
          disabled={isChoice ? currentState.selected === null : !currentState.text.trim()}
          onClick={submit}
        >提交本题</button>}
        {currentState.submitted && currentState.correct !== true && <button type="button" data-testid="practice-retry" onClick={retry}><RotateCcw size={16} />{isChoice && currentState.correct === null ? '重新选择' : '再试一次'}</button>}
        {onAskAi && <button type="button" onClick={onAskAi}><MessageCircle size={16} />问 {assistantName}</button>}
      </div>
    </article>}

    {allSubmitted && <aside className={styles.summary} data-testid="practice-summary">
      <strong>这次自测完成了</strong>
      {judgedChoiceCount > 0 && <p>选择题答对 {correctCount} / {judgedChoiceCount} 道{correctCount < judgedChoiceCount ? '，答错的题目可以回到对应题号再试一次。' : '，全部答对，很扎实。'}</p>}
      {selfCheckCount > 0 && <p>另有 {selfCheckCount} 道选择题答案不是单个选项，系统不替你判定，请对照参考答案自查。</p>}
      {questions.length > choiceQuestions.length && <p>另有 {questions.length - choiceQuestions.length} 道开放式题目，记得对照参考答案自查思路。</p>}
      <small>自测结果只用于当场核对，不会写入你的学习档案{onAskAi ? `；想深入哪道题，随时可以问 ${assistantName}` : ''}。</small>
      <div><button type="button" onClick={restart}><RotateCcw size={15} />重新自测一遍</button></div>
    </aside>}

    <footer className={styles.navigation}>
      <button type="button" disabled={currentIndex <= 0} onClick={() => setCurrentIndex(index => Math.max(0, index - 1))}><ArrowLeft />上一题</button>
      <span>{artifact.title}</span>
      <button type="button" disabled={currentIndex >= questions.length - 1} onClick={() => setCurrentIndex(index => Math.min(questions.length - 1, index + 1))}>下一题<ArrowRight /></button>
    </footer>
  </section>;
}

export default QuestionPracticeRenderer;
