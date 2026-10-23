/**
 * 板子上方那一句「正在讲」。
 *
 * 板面是粉笔的地盘，字幕压在下沿会盖住刚写的那一笔，也让人以为板子自带一个播放条。
 * 所以这一句住在板子上方的说话区，和 DARYA 的正文同一支笔 —— 读起来是"她在边讲边画"，
 * 而不是"黑板在念稿"。
 *
 * 上一句一边淡出一边让位，下一句同时长出来：交接处不空一拍，眼睛始终只跟着一处。
 * 内容仍由 Blackboard 排出来（它才知道每一节什么时候开始），这里只负责画。
 */

import type { BoardNarration } from './Blackboard';
import styles from './Blackboard.module.css';

export function NarrationCaption({ saying, leaving }: BoardNarration) {
  if (!saying && !leaving) return null;
  return (
    // 读屏软件要的是完整的几句话，不是一句一句插播进来的字幕 ——
    // 那份清单在 Blackboard 里（srOnly），这里对辅助技术隐身。
    <div className={styles.captionLayer} data-testid="darya-board-narration" aria-hidden="true">
      {leaving && <p key={leaving.key} className={`${styles.caption} ${styles.captionOut}`}>{leaving.text}</p>}
      {saying && <p key={saying.key} className={styles.caption}>{saying.text}</p>}
    </div>
  );
}
