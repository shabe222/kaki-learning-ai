import type { DaryaTeachingStageProjection } from '../tideTypes';
import styles from './TideTeachingStageCard.module.css';

export type TeachingStageAction = 'example' | 'steps' | 'practice' | 'pause' | 'resume' | 'end';

export function TideTeachingStageCard({
  stage, busy, error, onAction,
}: {
  stage: DaryaTeachingStageProjection;
  busy: boolean;
  error: string;
  onAction: (action: TeachingStageAction) => void;
}) {
  const active = stage.status === 'ACTIVE';
  const summary = stage.summary || stage.progress;
  return (
    <section className={styles.card} aria-label="这一段教学" data-testid="darya-teaching-stage" data-stage-status={stage.status}>
      <header className={styles.header}>
        <span>这一段教学</span>
        <strong>{active ? '路线可继续' : stage.status === 'PAUSED' ? '已暂停，可接着学' : '本段已结束'}</strong>
      </header>
      <div className={styles.method}>
        <span>知识点 · {stage.progress.topic}</span>
        <span>当前讲法</span>
        <strong>{stage.method}</strong>
        <p>{stage.method_reason}</p>
      </div>
      {stage.last_transition && active && (
        <p className={styles.transition}>上一格：{stage.last_transition.from} → {stage.last_transition.to}。{stage.last_transition.reason}</p>
      )}
      {!active && (
        <div className={styles.summary} role="status">
          <strong>{summary.topic} · 阶段小结</strong>
          <p><span>已经看到</span>{summary.observed}</p>
          <p><span>仍需检查</span>{summary.remaining_gap}</p>
          <p><span>下次接着</span>{summary.next_step}</p>
          <small>{summary.mastery_conclusion}</small>
        </div>
      )}
      <div className={styles.actions} aria-label="教学操作">
        {active ? (
          <>
            <button type="button" disabled={busy} onClick={() => onAction('example')}>换个例子</button>
            <button type="button" disabled={busy} onClick={() => onAction('steps')}>分步讲</button>
            <button type="button" disabled={busy} onClick={() => onAction('practice')}>练一题</button>
            <button type="button" disabled={busy} onClick={() => onAction('pause')}>暂停</button>
            <button type="button" disabled={busy} onClick={() => onAction('end')}>结束本段</button>
          </>
        ) : (
          <>
            <button type="button" disabled={busy} onClick={() => onAction('resume')}>继续学习</button>
            {stage.status === 'PAUSED' && <button type="button" disabled={busy} onClick={() => onAction('end')}>结束本段</button>}
          </>
        )}
      </div>
      {error && <p className={styles.error} role="alert">{error}</p>}
    </section>
  );
}
