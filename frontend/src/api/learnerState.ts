import { getData } from './client';

export type PreferenceTaxonomy =
  | 'visual' | 'video' | 'reading' | 'document' | 'practice'
  | 'code' | 'example' | 'step_by_step' | 'concise' | 'detailed';

export interface PreferenceEvidence {
  preference: PreferenceTaxonomy;
  evidence_type: 'explicit' | 'behavioral' | 'profile';
  source: string;
  polarity: 'supporting' | 'opposing';
  confidence: number;
  evidence_count: number;
  evidence_refs: string[];
  observed_at?: string | null;
}

export interface NormalizedLearnerState {
  user_id: number;
  current_goal?: string | null;
  current_topic?: string | null;
  knowledge_level: string;
  topic_mastery: string;
  primary_barrier: Record<string, unknown>;
  support_level: string;
  transfer_readiness: string;
  next_teaching_focus?: string | null;
  confidence: number;
  evidence_refs: string[];
  mastery_by_knowledge_point: Record<string, number>;
  mastery_sources: Record<string, string>;
  weak_points: string[];
  strengths: string[];
  preferred_modalities: PreferenceTaxonomy[];
  preference_evidence: PreferenceEvidence[];
  presentation_preference: string;
  practice_preference: string;
  code_preference: string;
  pace: string;
  motivation: Record<string, unknown>;
  recent_errors: Array<Record<string, unknown>>;
  recent_evidence: Array<Record<string, unknown>>;
  recent_learning_events: Array<Record<string, unknown>>;
  recent_artifacts: Array<Record<string, unknown>>;
  current_tutor_session?: Record<string, unknown> | null;
  last_learning_time?: string | null;
  state_version: string;
  authority_order: string[];
}

export function getNormalizedLearnerState(userId: number) {
  return getData<NormalizedLearnerState>(`/api/profile/${userId}/learner-state`);
}
