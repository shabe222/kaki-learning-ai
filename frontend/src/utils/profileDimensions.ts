import type { BackendProfile } from '../api/modules';

export type EvidenceState = 'verified' | 'limited' | 'insufficient';

export interface ProfileDimensionView {
  key: string;
  label: string;
  value: number | null;
  evidence: EvidenceState;
  confidence: number | null;
  updatedAt: string | null;
  trend: 'up' | 'steady' | 'unknown';
  source: string;
}

const LABELS = [
  ['knowledge', '基础知识掌握度'], ['concept', '概念理解深度'], ['transfer', '知识迁移能力'],
  ['memory', '记忆保持能力'], ['problem', '问题解决能力'], ['efficiency', '学习效率'],
  ['metacognition', '元认知与自我判断'], ['initiative', '学习主动性与执行力'],
  ['rhythm', '学习节奏与坚持度'], ['preference', '兴趣、动机与资源偏好']
] as const;

function score(value: unknown): number | null {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < 0) return null;
  if (numeric <= 1) return Math.round(numeric * 100);
  if (numeric <= 5) return Math.round(numeric * 20);
  return Math.min(100, Math.round(numeric));
}

function numeric(source: Record<string, unknown> | undefined, keys: string[]) {
  if (!source) return null;
  for (const key of keys) {
    const value = score(source[key]);
    if (value !== null) return value;
  }
  return null;
}

export function buildProfileDimensions(
  profile: BackendProfile | null | undefined,
  radar: Array<{ label: string; value: number }> = [],
  evidence: Record<string, boolean> = {}
): ProfileDimensionView[] {
  const radarMap = new Map(radar.map((item) => [item.label, score(item.value)]));
  const cognitive = (profile?.cognitive_profile ?? {}) as Record<string, unknown>;
  const behavior = (profile?.behavior_pattern ?? {}) as Record<string, unknown>;
  const motivation = (profile?.motivation_profile ?? {}) as Record<string, unknown>;
  const normalized = ((profile?.profile_data?.normalized_profile ?? {}) as Record<string, unknown>);
  const preferences = (normalized.preferences ?? normalized.resource_preferences ?? {}) as Record<string, unknown>;
  const interestEvidence = Object.keys(preferences).length > 0 || Object.keys(motivation).length > 0;
  const values: Record<string, { value: number | null; source: string; evidenceOnly?: boolean }> = {
    knowledge: { value: radarMap.get('知识掌握') ?? numeric(cognitive, ['knowledge_mastery', 'foundation_score']), source: '学习路径章节进度' },
    concept: { value: numeric(cognitive, ['concept_depth', 'concept_understanding']) ?? radarMap.get('理解迁移') ?? null, source: '画像认知字段与能力矩阵' },
    transfer: { value: numeric(cognitive, ['knowledge_transfer', 'transfer_ability']), source: '画像认知字段' },
    memory: { value: numeric(cognitive, ['memory_retention', 'retention']) ?? radarMap.get('复习习惯') ?? null, source: '复习记录与画像认知字段' },
    problem: { value: numeric(cognitive, ['problem_solving', 'problem_solving_ability']) ?? radarMap.get('练习表现') ?? null, source: '正式测评与错题记录' },
    efficiency: { value: numeric(behavior, ['learning_efficiency', 'efficiency_score']), source: '学习行为记录' },
    metacognition: { value: numeric(cognitive, ['metacognition', 'self_assessment']), source: '画像认知字段' },
    initiative: { value: numeric(motivation, ['initiative', 'execution', 'learning_initiative']), source: '动机画像与任务行为' },
    rhythm: { value: numeric(behavior, ['rhythm_score', 'persistence']) ?? radarMap.get('学习节奏') ?? null, source: '学习时长与节奏记录' },
    preference: { value: numeric(motivation, ['motivation_score', 'interest_score']), source: '兴趣、动机与资源偏好', evidenceOnly: interestEvidence }
  };
  const confidence = profile?.confidence_score == null ? null : score(profile.confidence_score);
  return LABELS.map(([key, label]) => {
    const entry = values[key];
    const hasEvidence = evidence[key] ?? (entry.value !== null || entry.evidenceOnly);
    return {
      key,
      label,
      value: hasEvidence ? entry.value : null,
      evidence: !hasEvidence ? 'insufficient' : confidence !== null && confidence >= 70 ? 'verified' : 'limited',
      confidence: hasEvidence ? confidence : null,
      updatedAt: hasEvidence ? profile?.updated_at ?? null : null,
      trend: 'unknown',
      source: entry.source
    };
  });
}

export function profilePreferenceTags(profile: BackendProfile | null | undefined): string[] {
  const normalized = ((profile?.profile_data?.normalized_profile ?? {}) as Record<string, unknown>);
  const values = [normalized.preferred_modalities, normalized.resource_preferences, profile?.preferred_language, profile?.target_role];
  const tags: string[] = [];
  values.forEach((value) => {
    if (Array.isArray(value)) value.forEach((item) => tags.push(String(item)));
    else if (value && typeof value === 'object') Object.values(value as Record<string, unknown>).forEach((item) => { if (typeof item === 'string') tags.push(item); });
    else if (typeof value === 'string') tags.push(value);
  });
  return Array.from(new Set(tags.map((item) => item.trim()).filter(Boolean))).slice(0, 8);
}
