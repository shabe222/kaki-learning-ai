/**
 * 学习时长：心跳上报 + 汇总读取。
 *
 * 为什么是"上报刚刚过去的这段时间"而不是"上报一个总数"：
 * 客户端一旦报总数，一次重试、一个被恢复的标签页、或者两次心跳乱序到达，
 * 都会把同一个 30 秒记成两遍。报增量的话，重复到达最多多记一小段，
 * 而客户端可以用"距上次成功上报已过去多久"来封顶，不会累积成小时级误差。
 */
import { getUserToken } from '../../../shared/auth';

const configuredBase = import.meta.env.VITE_BROWSER_API_BASE_URL || import.meta.env.VITE_API_BASE_URL || '';
const apiBase = String(configuredBase).replace(/\/$/, '');

/** 后端 MAX_REPORT_SECONDS：单次上报的上限。 */
export const MAX_REPORT_SECONDS = 180;

export interface StudyDay {
  day: string;
  seconds: number;
  minutes: number;
  weekday?: number;
}

export interface StudyTimeSummary {
  contract_version: string;
  today_seconds: number;
  today_minutes: number;
  week_seconds: number;
  week_minutes: number;
  last_week_seconds: number;
  month_seconds: number;
  month_minutes: number;
  month_hours: number;
  active_days: number;
  total_days: number;
  window_days: number;
  current_streak: number;
  longest_streak: number;
  best_day: { day: string; seconds: number; minutes: number } | null;
  week: StudyDay[];
  series: StudyDay[];
  sentence: string;
  counts_only_presence: boolean;
}

function authHeaders(): HeadersInit {
  const token = getUserToken();
  return {
    'Content-Type': 'application/json; charset=utf-8',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export async function reportStudyHeartbeat(seconds: number): Promise<number> {
  const bounded = Math.max(1, Math.min(Math.round(seconds), MAX_REPORT_SECONDS));
  const response = await fetch(`${apiBase}/api/darya/study-time/heartbeat`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ seconds: bounded }),
  });
  if (!response.ok) throw new Error(`study-time heartbeat ${response.status}`);
  const body = await response.json() as { today_seconds?: number };
  return Number(body.today_seconds || 0);
}

export async function fetchStudySummary(days = 30): Promise<StudyTimeSummary> {
  const response = await fetch(`${apiBase}/api/darya/study-time/summary?days=${days}`, {
    method: 'GET',
    headers: authHeaders(),
  });
  if (!response.ok) throw new Error(`study-time summary ${response.status}`);
  return await response.json() as StudyTimeSummary;
}
