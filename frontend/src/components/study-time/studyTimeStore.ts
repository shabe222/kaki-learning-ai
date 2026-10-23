// 学习会话计时器：按 userId 持久化到 localStorage。
// 当前学习时间 = now - session_start_time（刷新/重开页面自动恢复，无需每秒写盘）；
// 累计学习时间 = 每次会话结束时 (end - start) 累加，永久保存。

const SESSION_KEY = (userId: number) => `kaki_study_session_start_${userId}`;
const TOTAL_KEY = (userId: number) => `kaki_study_total_seconds_${userId}`;

/** 确保用户有一轮进行中的学习会话：没有则从现在开始，已有则继续（覆盖刷新/重开恢复）。 */
export function ensureStudySessionStarted(userId: number) {
  const start = localStorage.getItem(SESSION_KEY(userId));
  if (!start || Number.isNaN(Date.parse(start))) {
    localStorage.setItem(SESSION_KEY(userId), new Date().toISOString());
  }
}

/** 当前学习会话的开始时间（毫秒时间戳）；没有进行中会话时返回当前时间。 */
export function getStudySessionStartMs(userId: number): number {
  const raw = localStorage.getItem(SESSION_KEY(userId));
  const ms = raw ? Date.parse(raw) : Number.NaN;
  return Number.isNaN(ms) ? Date.now() : ms;
}

/** 累计学习时间（秒）：历史所有已结算会话之和。 */
export function getStudyTotalSeconds(userId: number): number {
  const raw = localStorage.getItem(TOTAL_KEY(userId));
  const value = raw ? Number(raw) : 0;
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

/**
 * 结束一轮学习会话：把 (结束时刻 - 开始时刻) 累加进累计学习时间，并清空进行中标记。
 * 仅在退出登录 / 会话失效时调用；刷新与关闭重开都不触发。
 */
export function endStudySession(userId: number) {
  const startMs = getStudySessionStartMs(userId);
  const elapsed = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
  if (elapsed > 0) {
    localStorage.setItem(TOTAL_KEY(userId), String(getStudyTotalSeconds(userId) + elapsed));
  }
  localStorage.removeItem(SESSION_KEY(userId));
}
