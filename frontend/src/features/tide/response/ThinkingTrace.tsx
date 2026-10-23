/**
 * 等待时的那几行字。
 *
 * 不做假动画：这些步骤全部来自后端在真实阶段发出来的 progress 事件（读了你的话 /
 * 想清楚这轮做什么 / 正在写板书）。所以它显示的不是"我在转圈"，而是"我做到哪一步
 * 了"。这也是成熟 agent 的做法 —— 让人看见进度，而不是让人猜。
 */

import styles from './ThinkingTrace.module.css';

/** 保留几步。太多会变成日志，太少看不出进展。 */
const MAX_STEPS = 4;

export function ThinkingTrace({
  stages,
  /** 正文已经开始出现了，步骤就不再是"当前"状态 */
  settled,
}: {
  stages: string[];
  settled?: boolean;
}) {
  const steps = (stages ?? []).filter(Boolean).slice(-MAX_STEPS);
  if (steps.length === 0) return null;

  return (
    <div className={styles.trace} data-testid="darya-thinking" aria-live="polite">
      {steps.map((label, index) => {
        const isLast = index === steps.length - 1;
        const active = isLast && !settled;
        return (
          <div
            key={`${index}-${label}`}
            className={styles.step}
            data-state={active ? 'active' : 'done'}
          >
            {active ? (
              <span className={styles.pulse} aria-hidden="true" />
            ) : (
              <span className={styles.tick} aria-hidden="true">✓</span>
            )}
            <span className={active ? styles.shimmer : undefined}>{label}</span>
          </div>
        );
      })}
    </div>
  );
}

/** 回答正在长出来时跟在末尾的那一小道呼吸。 */
export function RevealingCaret() {
  return <span className={styles.caret} aria-hidden="true" />;
}
