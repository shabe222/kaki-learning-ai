import type { GeneratedItem } from '../api/modules';

export type ArtifactGroup = 'document' | 'practice' | 'code' | 'mindmap' | 'presentation' | 'other';

export interface ArtifactInsight {
  total: number;
  distribution: Array<{ key: ArtifactGroup; label: string; value: number }>;
  trend: Array<{ date: string; label: string; value: number }>;
  recent: GeneratedItem[];
  states: { runnableSucceeded: number; downloadable: number; editable: number };
}

export function artifactGroup(type?: string): ArtifactGroup {
  const value = String(type ?? '').toLowerCase();
  if (/document|lecture|summary|note/.test(value)) return 'document';
  if (/question|exam|quiz/.test(value)) return 'practice';
  if (/code/.test(value)) return 'code';
  if (/mindmap/.test(value)) return 'mindmap';
  if (/ppt|presentation|interactive|video/.test(value)) return 'presentation';
  return 'other';
}

export function isArtifactRecord(item: GeneratedItem): boolean {
  if (!item || item.id == null) return false;
  const type = String(item.type ?? '').toLowerCase();
  if (type.startsWith('tutor_action:') || type === 'chat_profile' || type === 'tutor' || type === 'search') return false;
  const raw = (item.raw_data ?? {}) as Record<string, unknown>;
  if (raw.error || raw.errors) return false;
  try {
    const parsed = item.content ? JSON.parse(item.content) as Record<string, unknown> : null;
    return !parsed?.error && !parsed?.errors;
  } catch {
    return true;
  }
}

function dateKey(value?: string) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toISOString().slice(0, 10) : '';
}

function rawRecord(item: GeneratedItem) {
  return (item.raw_data ?? {}) as Record<string, unknown>;
}

export function buildArtifactInsights(items: GeneratedItem[], now = new Date()): ArtifactInsight {
  const valid = items.filter(isArtifactRecord);
  const groupLabels: Record<ArtifactGroup, string> = {
    document: '讲义与总结', practice: '题目与练习', code: '代码', mindmap: '思维导图', presentation: '演示与视频', other: '其他'
  };
  const groupOrder: ArtifactGroup[] = ['document', 'practice', 'code', 'mindmap', 'presentation', 'other'];
  const counts = new Map<ArtifactGroup, number>(groupOrder.map((key) => [key, 0]));
  valid.forEach((item) => counts.set(artifactGroup(item.type), (counts.get(artifactGroup(item.type)) ?? 0) + 1));

  const trend = Array.from({ length: 7 }, (_, offset) => {
    const date = new Date(now);
    date.setHours(0, 0, 0, 0);
    date.setDate(date.getDate() - (6 - offset));
    const key = date.toISOString().slice(0, 10);
    return { date: key, label: `${date.getMonth() + 1}/${date.getDate()}`, value: valid.filter((item) => dateKey(item.created_at) === key).length };
  });

  const runnableSucceeded = valid.filter((item) => {
    if (artifactGroup(item.type) !== 'code') return false;
    const raw = rawRecord(item);
    const run = (raw.run ?? raw.last_run ?? {}) as Record<string, unknown>;
    return Number(run.exit_code) === 0 || raw.status === 'success' || raw.run_status === 'success';
  }).length;
  const downloadable = valid.filter((item) => Boolean(item.content?.trim()) || Boolean(rawRecord(item).download_url) || Boolean(rawRecord(item).export_formats)).length;
  const editable = valid.filter((item) => ['document', 'practice', 'code', 'mindmap', 'presentation'].includes(artifactGroup(item.type))).length;

  return {
    total: valid.length,
    distribution: groupOrder.map((key) => ({ key, label: groupLabels[key], value: counts.get(key) ?? 0 })),
    trend,
    recent: [...valid].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 4),
    states: { runnableSucceeded, downloadable, editable }
  };
}
