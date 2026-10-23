import { useEffect, useMemo, useState } from 'react';
import { Clock, Timer } from 'lucide-react';
import { getData } from '../../api/client';
import styles from './StudyTime.module.css';
import studyTimeBg from '../../assets/generated/learning-time-bg.png';
import { getStudySessionStartMs, getStudyTotalSeconds } from './studyTimeStore';

function formatSession(seconds: number) {
  const safe = Math.max(0, Math.floor(seconds));
  const h = String(Math.floor(safe / 3600)).padStart(2, '0');
  const m = String(Math.floor((safe % 3600) / 60)).padStart(2, '0');
  const s = String(safe % 60).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

function formatTotal(totalSeconds: number) {
  const minutes = Math.max(0, Math.round(totalSeconds / 60));
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours > 0) return `${hours}小时${mins}分钟`;
  return `${mins}分钟`;
}

// 学习时光只负责展示：当前学习时间 = 现在 - session_start_time（刷新自动继续），
// 累计学习时间 = 持久化的 total_learning_seconds。会话生命周期由 MainLayout 管理。
export function StudyTime({ userId }: { userId?: number }) {
  const [now, setNow] = useState(() => Date.now());
  const [backendSeconds, setBackendSeconds] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  // 累计学习时间 = 本地已结算会话 + 后端真实学习记录（测验用时 time_spent_sec + 答题用时 time_spent_sec 聚合）
  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    Promise.all([
      getData<{ data: Array<{ time_spent_sec?: number | null }> }>(`/api/exam/list/${userId}`).then(r => {
        const exams = r?.data ?? r ?? [];
        return (Array.isArray(exams) ? exams : []).reduce((sum, e) => sum + (Number(e.time_spent_sec) || 0), 0);
      }).catch(() => 0),
      getData<{ data: { by_topic: Record<string, Array<{ time_spent_sec?: number }>> } }>(`/api/exam/wrong-questions/${userId}`).then(r => {
        const payload = r?.data ?? r ?? { by_topic: {} };
        const byTopic = payload.by_topic ?? {};
        return Object.values(byTopic).flat().reduce((sum, rec) => sum + (Number(rec.time_spent_sec) || 0), 0);
      }).catch(() => 0),
    ]).then(([examSec, answerSec]) => {
      if (!cancelled) setBackendSeconds(examSec + answerSec);
    });
    return () => { cancelled = true; };
  }, [userId]);

  const sessionStart = useMemo(() => (userId ? getStudySessionStartMs(userId) : 0), [userId]);
  const sessionSeconds = userId ? (now - sessionStart) / 1000 : 0;
  const totalSeconds = useMemo(() => (userId ? getStudyTotalSeconds(userId) + backendSeconds : 0), [backendSeconds, userId]);
  if (!userId) return null;

  return (
    <section className={styles.outer} data-testid="study-time-card" aria-label="学习时光">
      <header className={styles.titleRow}>
        <Clock size={18} strokeWidth={1.8} className={styles.titleIcon} aria-hidden="true" />
        <h3 className={styles.title}>学习时光</h3>
      </header>
      <div
        className={styles.panel}
        style={{ '--study-time-bg': `url(${studyTimeBg})` } as React.CSSProperties}
      >
        <div className={styles.session}>
          <div className={styles.labelRow}>
            <span className={styles.dot} aria-hidden="true" />
            <span className={styles.label}>当前学习时间</span>
            <span className={styles.badge}>正在学习</span>
          </div>
          <strong className={styles.sessionTime} data-testid="study-time-current">{formatSession(sessionSeconds)}</strong>
          <p className={styles.sessionNote}>柿小吉陪你专注学习中～</p>
        </div>
        <div className={styles.divider} aria-hidden="true" />
        <div className={styles.total}>
          <div className={styles.labelRow}>
            <Timer size={12} strokeWidth={2.2} className={styles.totalIcon} aria-hidden="true" />
            <span className={styles.label}>累计学习时间</span>
          </div>
          <strong className={styles.totalTime} data-testid="study-time-total">{formatTotal(totalSeconds)}</strong>
          <p className={styles.totalNote}>每一次努力，都会被记住 ✨</p>
        </div>
      </div>
    </section>
  );
}
