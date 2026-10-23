import type { ModuleActionTransport } from '../contracts/moduleActionContracts';

export type ActionEventStatus = 'success' | 'local-fallback' | 'error';

export interface ActionEvent {
  id: string;
  module: string;
  action: string;
  title: string;
  payload?: Record<string, unknown>;
  status: ActionEventStatus;
  transport: ModuleActionTransport;
  createdAt: string;
}

export interface ProfileSignals {
  total: number;
  practiceCount: number;
  agentCount: number;
  summaryCount: number;
  reviewCount: number;
  lastUpdatedAt: string | null;
  recentLabel: string;
}

interface RadarPoint {
  label: string;
  value: number;
}

const STORAGE_KEY = 'kaki_action_events';
const ACTION_EVENT_NAME = 'kaki-action-event';
const MAX_EVENTS = 80;
const DEFAULT_RADAR: RadarPoint[] = [
  { label: '理解', value: 84 },
  { label: '练习', value: 78 },
  { label: '总结', value: 76 },
  { label: '应用', value: 72 },
  { label: '复习', value: 80 }
];

function canUseBrowserStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function safeParseEvents(value: string | null): ActionEvent[] {
  if (!value) {
    return [];
  }

  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.filter((item): item is ActionEvent => Boolean(item?.id && item?.module && item?.action)) : [];
  } catch {
    return [];
  }
}

export function getActionEvents(limit = MAX_EVENTS) {
  if (!canUseBrowserStorage()) {
    return [];
  }
  return safeParseEvents(window.localStorage.getItem(STORAGE_KEY)).slice(0, limit);
}

export function recordActionEvent(input: Omit<ActionEvent, 'id' | 'createdAt'>) {
  const existing = getActionEvents();
  const event: ActionEvent = {
    ...input,
    id: `${Date.now()}-${existing.length + 1}`,
    createdAt: new Date().toISOString()
  };
  const next = [event, ...existing].slice(0, MAX_EVENTS);

  if (canUseBrowserStorage()) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // The UI still receives the in-memory event below if storage is unavailable.
    }
    window.dispatchEvent(new CustomEvent<ActionEvent>(ACTION_EVENT_NAME, { detail: event }));
  }

  return event;
}

export function subscribeActionEvents(listener: (events: ActionEvent[]) => void) {
  if (!canUseBrowserStorage()) {
    return () => undefined;
  }

  const emit = () => listener(getActionEvents());
  const handleActionEvent = () => emit();
  const handleStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) {
      emit();
    }
  };

  window.addEventListener(ACTION_EVENT_NAME, handleActionEvent);
  window.addEventListener('storage', handleStorage);
  return () => {
    window.removeEventListener(ACTION_EVENT_NAME, handleActionEvent);
    window.removeEventListener('storage', handleStorage);
  };
}

export function getAgentInteractionEvents(limit = 6, events = getActionEvents()) {
  return events
    .filter((event) => event.module === 'agents' || event.action.includes('agent'))
    .slice(0, limit);
}

export function getProfileSignals(events = getActionEvents()): ProfileSignals {
  const count = (predicate: (event: ActionEvent) => boolean) => events.filter(predicate).length;
  const recent = events[0] ?? null;

  return {
    total: events.length,
    practiceCount: count((event) => event.action.includes('practice') || event.action.includes('exam')),
    agentCount: count((event) => event.module === 'agents' || event.action.includes('agent')),
    summaryCount: count((event) => ['report', 'notes', 'resources'].includes(event.module) || event.action.includes('export')),
    reviewCount: count((event) => ['learning', 'video', 'qa'].includes(event.module) || event.action.includes('continue')),
    lastUpdatedAt: recent?.createdAt ?? null,
    recentLabel: recent ? `最近：${recent.title}` : '还没有学习记录，完成一次练习后这里会动起来'
  };
}

export function getProfileRadarData(base?: RadarPoint[], events = getActionEvents()) {
  const source = base?.length ? base : DEFAULT_RADAR;
  const signals = getProfileSignals(events);
  const boosts = [
    Math.min(8, signals.total),
    Math.min(12, signals.practiceCount * 3),
    Math.min(10, signals.summaryCount * 3),
    Math.min(10, signals.practiceCount + signals.agentCount * 2),
    Math.min(8, signals.reviewCount * 2)
  ];

  return source.map((item, index) => ({
    ...item,
    value: Math.min(100, Number(item.value ?? 0) + (boosts[index] ?? 0))
  }));
}

export function formatActionEventTime(value: string | null | undefined) {
  if (!value) {
    return '--';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '--';
  }
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
}
