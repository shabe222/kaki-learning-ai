import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, LoaderCircle } from 'lucide-react';
import { fetchStudySummary, type StudyDay, type StudyTimeSummary } from './studyTimeApi';
// 二级界面的共用外壳（绝对定位 + 玻璃面板）。不套它就会落在主区下面，
// 而主区退场时只是变淡、并没有消失，于是整页看起来是半透明的。
import surfaceStyles from '../surfaces/DaryaOceanSurface.module.css';
import styles from './StudyTimeSurface.module.css';

/**
 * 学习时长页。
 *
 * 它不是仪表盘。仪表盘回答"各项指标是多少"，而人打开这一页想问的是
 * "我最近到底怎么样"。所以这一页只有一个主体：一句话 + 一张图，
 * 剩下的数字都退到这句话底下去当注脚。
 *
 * 具体做法是把这一页当成一张杂志跨页来排：
 * - 主标题是后端算好的那句人话，不是"本周学习时长"这种字段名；
 * - 主体图像是 30 天的潮汐线，一条平滑的水位线，不是柱状图；
 * - 三个数字用衬线大字 + 小字注脚，中间用发丝线分隔，不套卡片、不描边框；
 * - 最后一排小格子是"来过 / 没来"，用密集的点阵表达持续性，这是柱状图做不到的。
 *
 * 空数据也要好看 —— 新用户第一次点进来就是空的，那一屏同样是第一印象。
 */

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];
const CHART_H = 220;
const CHART_TOP = 34;
const CHART_BOTTOM = 32;

function shortDate(iso: string): string {
  const [, month, day] = iso.split('-');
  return `${Number(month)}月${Number(day)}日`;
}

/** 基数样条：把折线变成一条水位线。控制点夹在上下界内，免得曲线冲出画布。 */
function smooth(points: Array<[number, number]>, low: number, high: number): string {
  if (points.length < 2) return '';
  const clamp = (y: number) => Math.max(low, Math.min(high, y));
  let path = `M ${points[0][0].toFixed(2)} ${points[0][1].toFixed(2)}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const before = points[index - 1] ?? points[index];
    const start = points[index];
    const end = points[index + 1];
    const after = points[index + 2] ?? end;
    const c1x = start[0] + (end[0] - before[0]) / 6;
    const c1y = clamp(start[1] + (end[1] - before[1]) / 6);
    const c2x = end[0] - (after[0] - start[0]) / 6;
    const c2y = clamp(end[1] - (after[1] - start[1]) / 6);
    path += ` C ${c1x.toFixed(2)} ${c1y.toFixed(2)}, ${c2x.toFixed(2)} ${c2y.toFixed(2)}, ${end[0].toFixed(2)} ${end[1].toFixed(2)}`;
  }
  return path;
}

function TideChart({ series }: { series: StudyDay[] }) {
  const geometry = useMemo(() => {
    const days = series.slice(-30);
    const width = 1000;
    const peak = Math.max(1, ...days.map((day) => day.seconds));
    const inner = CHART_H - CHART_TOP - CHART_BOTTOM;
    const step = days.length > 1 ? width / (days.length - 1) : width;
    const points = days.map((day, index) => {
      const ratio = peak > 0 ? day.seconds / peak : 0;
      // 一分钟以下的"来过"也让它离开基线一点点：有痕迹，但不假装学了很久。
      const lift = day.seconds > 0 ? Math.max(ratio, 0.06) : 0;
      return [index * step, CHART_TOP + inner * (1 - lift)] as [number, number];
    });
    const line = smooth(points, CHART_TOP, CHART_H - CHART_BOTTOM);
    const area = line
      ? `${line} L ${width} ${CHART_H - CHART_BOTTOM} L 0 ${CHART_H - CHART_BOTTOM} Z`
      : '';
    return { days, width, line, area, step, peak };
  }, [series]);

  if (geometry.days.length === 0) return null;
  const today = geometry.days[geometry.days.length - 1];
  const baseline = CHART_H - CHART_BOTTOM;
  const lastIndex = geometry.days.length - 1;
  const inner = CHART_H - CHART_TOP - CHART_BOTTOM;
  const lastRatio = geometry.peak > 0 ? today.seconds / geometry.peak : 0;
  const lastY = CHART_TOP + inner * (1 - (today.seconds > 0 ? Math.max(lastRatio, 0.06) : 0));

  return (
    <svg
      className={styles.chart}
      viewBox={`0 0 ${geometry.width} ${CHART_H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`最近 ${geometry.days.length} 天的学习时长起伏`}
    >
      <defs>
        <linearGradient id="tide-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--tide-sea)" stopOpacity="0.34" />
          <stop offset="100%" stopColor="var(--tide-sea)" stopOpacity="0.03" />
        </linearGradient>
      </defs>

      {/* 只有三条发丝线：最高那一天、水线、以及底。刻度再多就变成图表了。 */}
      <line x1="0" y1={CHART_TOP} x2={geometry.width} y2={CHART_TOP} className={styles.hairline} strokeDasharray="2 7" />
      <line x1="0" y1={baseline} x2={geometry.width} y2={baseline} className={styles.hairline} />

      <path d={geometry.area} fill="url(#tide-fill)" />
      <path d={geometry.line} className={styles.tideLine} />

      {/* 每个"来过"的日子在线上留一个点。空白的日子自然没有点。 */}
      {geometry.days.map((day, index) => {
        if (day.seconds <= 0) return null;
        const ratio = geometry.peak > 0 ? day.seconds / geometry.peak : 0;
        const lift = Math.max(ratio, 0.06);
        const y = CHART_TOP + (CHART_H - CHART_TOP - CHART_BOTTOM) * (1 - lift);
        return <circle key={day.day} cx={index * geometry.step} cy={y} r={index === lastIndex ? 0 : 2.4} className={styles.dot} />;
      })}

      <circle cx={lastIndex * geometry.step} cy={lastY} r={9} className={styles.todayHalo} />
      <circle cx={lastIndex * geometry.step} cy={lastY} r={3.4} className={styles.todayDot} />
    </svg>
  );
}

export function StudyTimeSurface({ onBack }: { onBack: () => void }) {
  const [summary, setSummary] = useState<StudyTimeSummary | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchStudySummary(30)
      .then((value) => { if (!cancelled) setSummary(value); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, []);

  if (failed) {
    return (
      <section className={`${surfaceStyles.surface} ${styles.poster}`} aria-label="学习时长">
        <header className={styles.bar}>
          <button type="button" onClick={onBack}><ArrowLeft size={16} />返回</button>
        </header>
        <div className={styles.loading} role="status">
          <strong>这段时间没能读出来</strong>
          <p>记录还在服务端，刷新一次就能看到。</p>
        </div>
      </section>
    );
  }

  if (!summary) {
    return (
      <section className={`${surfaceStyles.surface} ${styles.poster}`} aria-label="学习时长">
        <header className={styles.bar}>
          <button type="button" onClick={onBack}><ArrowLeft size={16} />返回</button>
        </header>
        <div className={styles.loading} role="status">
          <LoaderCircle className={styles.spin} size={20} aria-hidden="true" />
          <strong>正在数你在这里待过的时间</strong>
        </div>
      </section>
    );
  }

  const days = summary.series.slice(-30);
  const first = days[0];
  const last = days[days.length - 1];
  const peak = summary.best_day;
  const weekDelta = summary.week_seconds - summary.last_week_seconds;
  const range = first && last ? `${shortDate(first.day)} — ${shortDate(last.day)}` : '';
  const marks = days.slice(-28);

  return (
    <section className={`${surfaceStyles.surface} ${styles.poster}`} aria-label="学习时长" data-testid="darya-study-time-surface">
      <header className={styles.bar}>
        <button type="button" onClick={onBack} data-testid="darya-study-time-back">
          <ArrowLeft size={16} />返回
        </button>
        <span className={styles.barTitle}>学习时长</span>
      </header>

      <div className={styles.page}>
        <div className={styles.hero}>
          <span className={styles.eyebrow}>{range} · 最近 {summary.window_days} 天</span>
          {/* 这一句是后端算好的大白话，也是整页的主角。数字都退到它后面。 */}
          <p className={styles.statement}>{summary.sentence}</p>
        </div>

        {summary.total_days === 0 ? (
          <div className={styles.empty}>
            <p className={styles.emptyLead}>这一页现在是空的。</p>
            <p className={styles.emptyBody}>
              它只记一件事：你在 DARYA 这里待了多久。第一次心跳之后，这里就会开始长出线来。
            </p>
          </div>
        ) : (
          <>
            <figure className={styles.figure}>
              <TideChart series={summary.series} />
              <figcaption className={styles.caption}>
                <span>最近 {days.length} 天的水位</span>
                {peak && <span className={styles.captionPeak}>最高的一天是 {shortDate(peak.day)}，{peak.minutes} 分钟</span>}
              </figcaption>
            </figure>

            {/* 三个数字，不成卡片、不描边框：靠排版和发丝线分层。 */}
            <div className={styles.truths}>
              <div className={styles.truth}>
                <b>{summary.week_minutes}</b>
                <span className={styles.unit}>分钟</span>
                <em>这一周</em>
                <i className={weekDelta > 0 ? styles.up : weekDelta < 0 ? styles.down : styles.flat}>
                  {weekDelta === 0
                    ? '与上周持平'
                    : weekDelta > 0
                      ? `比上周多 ${Math.round(weekDelta / 60)} 分钟`
                      : `比上周少 ${Math.round(Math.abs(weekDelta) / 60)} 分钟`}
                </i>
              </div>
              <div className={styles.truth}>
                <b>{summary.current_streak}</b>
                <span className={styles.unit}>天</span>
                <em>连续不断</em>
                <i className={styles.flat}>
                  {summary.current_streak > 0 && summary.current_streak >= summary.longest_streak
                    ? '已经是你的最长纪录'
                    : `最长的一次是 ${summary.longest_streak} 天`}
                </i>
              </div>
              <div className={styles.truth}>
                <b>{summary.month_hours}</b>
                <span className={styles.unit}>小时</span>
                <em>这一个月</em>
                <i className={styles.flat}>{summary.active_days} 天里有你来过的痕迹</i>
              </div>
            </div>

            <div className={styles.presence}>
              <div className={styles.marks} role="img" aria-label={`最近 ${marks.length} 天中 ${summary.total_days} 天有学习记录`}>
                {marks.map((day) => (
                  <span
                    key={day.day}
                    className={day.seconds > 0 ? styles.markOn : styles.markOff}
                    title={`${shortDate(day.day)} · ${day.seconds > 0 ? `${day.minutes} 分钟` : '没有记录'}`}
                  />
                ))}
              </div>
              <p className={styles.presenceNote}>
                每一格是一天。有颜色的那几天，你来过。
              </p>
            </div>

            <div className={styles.weekRow}>
              {summary.week.map((day) => (
                <div key={day.day} className={styles.weekDay}>
                  <span className={styles.weekBar}>
                    <i style={{ height: `${weekHeight(day, summary.week)}%` }} />
                  </span>
                  <em>{WEEKDAYS[day.weekday ?? 0]}</em>
                </div>
              ))}
            </div>
          </>
        )}

        <p className={styles.footnote}>
          这里数的是你待在页面里的时间，不是消息条数，也不含你关掉页面之后的时间。
        </p>
      </div>
    </section>
  );
}

function weekHeight(day: StudyDay, week: StudyDay[]): number {
  const peak = Math.max(1, ...week.map((item) => item.seconds));
  if (day.seconds <= 0) return 4;
  return Math.max(12, Math.round((day.seconds / peak) * 100));
}
