import { useCallback, useEffect, useRef, useState } from 'react';
import { Clock } from 'lucide-react';
import { MAX_REPORT_SECONDS, fetchStudySummary, reportStudyHeartbeat } from './studyTimeApi';
import styles from './StudyClock.module.css';

/**
 * 顶栏上那个会走的钟。
 *
 * 三件事，缺一件它就不像"活的"：
 *
 * 1. 数字自己会动。服务端只在心跳时才知道你还在，30 秒一跳的话数字就会
 *    一顿一顿地跳。所以本地每秒钟自己走，心跳只负责把它校准回真相 ——
 *    人是看着秒针理解"我在学习"的，不是看着一个每半分钟刷新一次的计数器。
 *
 * 2. 它只在你在的时候走。切到别的标签页、或者人离开键盘几分钟，就停下不再上报。
 *    后端把"上报"定义为"刚刚过去的这段时间"，所以离开时不上报就等于没发生，
 *    不需要额外去减。一个明明没人还在涨的钟，比没有钟更假。
 *
 * 3. 上报的是增量而不是总数。见 studyTimeApi 里的说明。
 */

// 心跳间隔。后端单次上限 180 秒，这里留足余量给卡顿和重试。
const HEARTBEAT_MS = 30_000;
// 多久没有键鼠动作就算人离开了。比心跳间隔长，避免刚读完一段长文就断线。
const IDLE_MS = 3 * 60_000;
// 本地秒表的 tick。人眼对秒的感知就在这里。
const TICK_MS = 1_000;

function formatToday(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分钟`;
  return total > 0 ? `${total} 秒` : '刚刚开始';
}

export function StudyClock({ onOpen }: { onOpen: () => void }) {
  const [seconds, setSeconds] = useState(0);
  // 本地秒表相对服务端已知值的增量。心跳成功时清零并接受服务端返回的真实值。
  const localRef = useRef(0);
  const lastReportRef = useRef(Date.now());
  const lastActivityRef = useRef(Date.now());
  const reportedRef = useRef(0);

  // 进页面时先把今天已有的数拉回来，否则每次刷新都从零开始涨。
  useEffect(() => {
    let cancelled = false;
    void fetchStudySummary(7)
      .then((summary) => {
        if (cancelled) return;
        reportedRef.current = summary.today_seconds;
        localRef.current = 0;
        setSeconds(summary.today_seconds);
      })
      .catch(() => {
        // 拉不到就从零开始记，心跳稍后会把它校正回来。
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const touch = () => { lastActivityRef.current = Date.now(); };
    const events: Array<keyof WindowEventMap> = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'];
    events.forEach((event) => window.addEventListener(event, touch, { passive: true }));
    return () => events.forEach((event) => window.removeEventListener(event, touch));
  }, []);

  const present = useCallback(
    () => document.visibilityState === 'visible' && Date.now() - lastActivityRef.current < IDLE_MS,
    [],
  );

  useEffect(() => {
    const tick = window.setInterval(() => {
      if (!present()) {
        // 离开时把秒表的起点推后，回来时才不会把离开的那段一次性补上。
        lastReportRef.current = Date.now();
        return;
      }
      localRef.current += TICK_MS / 1000;
      setSeconds(reportedRef.current + localRef.current);
    }, TICK_MS);
    return () => window.clearInterval(tick);
  }, [present]);

  useEffect(() => {
    let cancelled = false;
    const beat = () => {
      if (!present()) {
        lastReportRef.current = Date.now();
        return;
      }
      const elapsed = Math.round((Date.now() - lastReportRef.current) / 1000);
      if (elapsed < 5) return;
      const claim = Math.min(elapsed, MAX_REPORT_SECONDS);
      lastReportRef.current = Date.now();
      void reportStudyHeartbeat(claim)
        .then((todayTotal) => {
          if (cancelled || !todayTotal) return;
          // 用服务端返回的当日总数重新校准，本地秒表从上一次上报之后重新起算。
          reportedRef.current = todayTotal;
          localRef.current = Math.max(0, Math.round((Date.now() - lastReportRef.current) / 1000));
          setSeconds(todayTotal + localRef.current);
        })
        .catch(() => {
          // 上报失败不清零、不弹窗：时长是背景事实，不该打断正在看的东西。
          // 下一次心跳会重新算 elapsed，最多丢掉这半分钟。
        });
    };
    const beatTimer = window.setInterval(beat, HEARTBEAT_MS);
    // 切回前台时立刻补一次，免得刚回来那半分钟是空的。
    const onVisible = () => { if (document.visibilityState === 'visible') beat(); };
    document.addEventListener('visibilitychange', onVisible);
    beat();
    return () => {
      cancelled = true;
      window.clearInterval(beatTimer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [present]);

  const counting = seconds > 0;

  return (
    <button
      type="button"
      className={styles.clock}
      data-counting={counting ? 'true' : undefined}
      onClick={onOpen}
      title="今天在这里待了多久 · 点开看这一个月"
      aria-label={`今天学习了 ${formatToday(seconds)}，点击查看学习时长`}
      data-testid="darya-study-clock"
    >
      <span className={styles.dial} aria-hidden="true">
        <Clock size={14} strokeWidth={1.7} />
        {counting && <i className={styles.pulse} />}
      </span>
      <span className={styles.readout}>
        <b>{formatToday(seconds)}</b>
        <em>今天</em>
      </span>
    </button>
  );
}
