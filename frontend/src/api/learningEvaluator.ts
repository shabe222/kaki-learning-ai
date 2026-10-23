import { getData, postData } from './client';

const BASE = '/api/learning-evaluations';
export class LearningEvaluatorApiError extends Error {
  status?: number;
  code?: string;

  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.name = 'LearningEvaluatorApiError';
    this.status = status;
    this.code = code;
  }
}

async function callWithEvaluatorError<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (error: any) {
    const detail = error?.response?.data?.detail;
    const code = typeof detail === 'object' ? detail?.code : undefined;
    const message = typeof detail === 'string'
      ? detail
      : detail?.message || error?.message || '学习评估暂时不可用';
    throw new LearningEvaluatorApiError(message, error?.response?.status, code);
  }
}


export interface PublicEvidenceSummary {
  evidence_type: string;
  occurred_at: string;
  state: string;
  trust_level: string;
  recency_band: string;
}

export interface PublicDimensionAssessment {
  dimension: string;
  score_band: string;
  confidence: number;
  missing_evidence: boolean;
}

export interface PublicObjectiveAssessment {
  objective_id: string;
  public_label: string;
  achievement_status: string;
  achieved: boolean;
  achievement_score_band: string;
  coverage_confidence_band: string;
  evidence_count: number;
  counter_evidence_count: number;
  safe_gap_codes: string[];
}

export interface PublicMasteryRequirementAssessment {
  requirement_id: string;
  requirement_type: string;
  satisfied: boolean;
  reason_code: string;
}

export interface PublicMisconception {
  code: string;
  summary: string;
  confidence: number;
  severity: string;
  resolved: boolean;
}

export interface PublicRecommendedEvaluationAction {
  action_type: string;
  target_node_id: string;
  reason_codes: string[];
  required_evidence_dimension?: string | null;
  priority: number;
}

export interface PublicNodeAssessment {
  node_id: string;
  assessment_status: string;
  mastery_signal: number;
  confidence: number;
  evidence_sufficiency: number;
  dimensions: PublicDimensionAssessment[];
  objective_coverage: PublicObjectiveAssessment[];
  mastery_requirement_coverage: PublicMasteryRequirementAssessment[];
  misconceptions: PublicMisconception[];
  evidence_gaps: string[];
  recommended_evaluation_actions: PublicRecommendedEvaluationAction[];
  completion_eligibility_signal: string;
  needs_review: boolean;
}

export interface PublicLearningEvaluation {
  evaluation_id: string;
  evaluation_revision: number;
  graph_id: string;
  graph_revision: number;
  evaluated_at: string;
  node_assessments: PublicNodeAssessment[];
  evidence_summaries: PublicEvidenceSummary[];
  global_warnings: string[];
  next_evaluation_actions: PublicRecommendedEvaluationAction[];
}

export interface EvaluateNodeInput {
  request_id: string;
  idempotency_key: string;
  expected_evaluation_revision: number;
}

function nodeBase(graphId: string, nodeId: string): string {
  return `${BASE}/graphs/${encodeURIComponent(graphId)}/nodes/${encodeURIComponent(nodeId)}`;
}

export const learningEvaluatorApi = {
  evaluate: (graphId: string, nodeId: string, body: EvaluateNodeInput) =>
    callWithEvaluatorError(() => postData<PublicLearningEvaluation, EvaluateNodeInput>(`${nodeBase(graphId, nodeId)}/evaluate`, body)),
  latest: (graphId: string, nodeId: string) =>
    callWithEvaluatorError(() => getData<PublicLearningEvaluation | null>(`${nodeBase(graphId, nodeId)}/latest`)),
  history: (graphId: string, nodeId: string) =>
    callWithEvaluatorError(() => getData<PublicLearningEvaluation[]>(`${nodeBase(graphId, nodeId)}/history`)),
};
