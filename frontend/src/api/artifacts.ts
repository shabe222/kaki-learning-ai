import { getData, postData } from './client';

export interface ArtifactVersionSummary {
  version_id: number;
  version_number: number;
  title: string;
  status: string;
  created_at: string;
  is_current: boolean;
  is_viewing: boolean;
}

export interface ArtifactVersionRef {
  version_id: number;
  version_number: number;
}

export interface ArtifactPublicProjection {
  artifact_id: number;
  version_id: number;
  version_number: number;
  version: number;
  kind: string;
  type: string;
  title: string;
  topic: string;
  graph_id?: string | null;
  graph_revision?: number | null;
  graph_node_id?: string | null;
  graph_node_title?: string | null;
  resource_plan_id?: string | null;
  resource_plan_continuity_key?: string | null;
  thread_id?: string | null;
  status: string;
  content_digest: string;
  created_at: string;
  updated_at: string;
  version_created_at: string;
  available_versions: ArtifactVersionSummary[];
  export_formats: string[];
  previous_version_ref?: ArtifactVersionRef | null;
  next_version_ref?: ArtifactVersionRef | null;
  current_version_ref: ArtifactVersionRef;
  render_payload: {
    content: string;
    data: Record<string, unknown>;
  };
  content: string;
  raw_data: Record<string, unknown>;
  canonical?: CanonicalArtifactV1 | null;
  canonical_error?: string | null;
}

export type CanonicalArtifactStatus = 'PLANNED' | 'GENERATING' | 'GENERATED' | 'IN_REVIEW' | 'REVISION_REQUIRED' | 'PASSED' | 'FAILED' | 'BLOCKED';

export interface CanonicalArtifactBlockV1 {
  contract: 'darya.artifact-block.v1';
  block_id: string;
  type: 'HEADING' | 'TEXT' | 'CALLOUT' | 'CONCEPT' | 'EXAMPLE' | 'QUESTION' | 'TASK' | 'CHECKLIST' | 'CODE' | 'RUNNABLE_CODE' | 'DIAGRAM' | 'IMAGE' | 'VIDEO' | 'AUDIO' | 'SOURCE' | 'REFLECTION' | 'CHECKPOINT';
  data: Record<string, unknown>;
  knowledge_refs: string[];
  source_refs: string[];
  accessibility: Record<string, unknown>;
}

export interface CanonicalArtifactV1 {
  contract: 'darya.artifact.v1';
  artifact_id: number;
  artifact_type: string;
  version: {
    version_id: number;
    number: number;
    parent_version_id?: number | null;
    status: CanonicalArtifactStatus;
    content_digest: string;
    created_at: string;
  };
  purpose: Record<string, unknown>;
  personalization: Record<string, unknown>;
  blocks: CanonicalArtifactBlockV1[];
  source_refs: unknown[];
  lineage: Array<Record<string, unknown>>;
  review: Record<string, unknown>;
  status: {
    generation_status: CanonicalArtifactStatus;
    review_verdict?: 'PASS' | 'REVISE' | 'REJECT' | 'BLOCKED' | null;
    learning_evaluation?: Record<string, unknown> | null;
    canonical_status: CanonicalArtifactStatus;
  };
  capabilities: Record<string, string[]>;
}

export interface ArtifactVersionDiffBlock {
  kind: 'equal' | 'added' | 'deleted' | 'modified';
  from_start: number;
  from_end: number;
  to_start: number;
  to_end: number;
  from_lines: string[];
  to_lines: string[];
}

export interface ArtifactVersionComparison {
  artifact_id: number;
  from_version: ArtifactVersionRef & { title: string };
  to_version: ArtifactVersionRef & { title: string };
  summary: Record<'equal' | 'added' | 'deleted' | 'modified', number>;
  changes: ArtifactVersionDiffBlock[];
}

export interface ArtifactSelectionReference {
  artifact_id: number;
  artifact_version_id: number;
  artifact_version_number: number;
  section_anchor?: string | null;
  range_label: string;
  summary: string;
  selected_text: string;
  content_checksum: string;
  purpose: string;
  version_digest?: string;
  block_id?: string | null;
  text_range?: { start: number; end: number } | null;
  timestamp?: { start_sec: number; end_sec?: number } | null;
  knowledge_refs?: string[];
  source_refs?: string[];
  artifact_purpose?: Record<string, unknown>;
}

export interface CreateArtifactVersionRequest {
  expected_version: number;
  idempotency_key: string;
  title?: string;
  chapter?: string;
  content?: string;
  public_data?: Record<string, unknown>;
  status?: string;
}

export type CodeRunStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'TIMED_OUT' | 'SANDBOX_UNAVAILABLE';
export type CodeObservationStatus = 'NOT_ANALYZED' | 'EXPECTED' | 'UNEXPECTED' | 'NEEDS_EXPLANATION';

export interface ArtifactCodeRun {
  run_id: number;
  artifact_id: number;
  artifact_version_id: number;
  artifact_version_number: number;
  run_number: number;
  status: CodeRunStatus;
  language: string;
  entrypoint: string;
  parameters: Record<string, unknown>;
  stdout: string;
  stderr: string;
  exit_code: number | null;
  timed_out: boolean;
  duration_ms: number | null;
  error_category?: string | null;
  error_line?: number | null;
  output_refs: Array<Record<string, unknown> | string>;
  observation_status: CodeObservationStatus;
  created_at: string;
  started_at: string;
  finished_at: string;
  terminal: boolean;
}

export interface InteractiveAttempt {
  attempt_id: number;
  artifact_id: number;
  artifact_version_id: number;
  item_identity: string;
  attempt_number: number;
  selected_answer: number | string;
  correct: boolean;
  hint_used: boolean;
  solution_viewed: boolean;
  status: string;
  submitted_at: string;
  learning_event_ref?: string | null;
}

export interface InteractiveAttemptResponse {
  attempt: InteractiveAttempt;
  result: {
    correct: boolean;
    explanation_available: boolean;
    retry_allowed: boolean;
    explanation?: string | null;
  };
  progress: {
    current_step: number;
    total_steps: number;
    answered_interactive_items: number;
    total_interactive_items: number;
    all_interactive_items_attempted: boolean;
    lesson_viewing_completed: boolean;
  };
  learning_event_ref?: string | null;
  evaluation_status: string;
  completion_eligibility: string | null;
  artifact_version_id: number;
  artifact_version_number: number;
}

export interface InteractiveAttemptState {
  artifact_id: number;
  artifact_version_id: number;
  artifact_version_number: number;
  current_item: string;
  attempts: InteractiveAttempt[];
  support_by_item: Record<string, { hint_requested: boolean; solution_viewed: boolean; last_event_ref?: string }>;
  progress: {
    current_step: number;
    total_steps: number;
    answered_interactive_items: number;
    total_interactive_items: number;
    all_interactive_items_attempted: boolean;
    lesson_viewing_completed: boolean;
  };
  retry_allowed: boolean;
  evaluation_status: string;
  completion_eligibility: string | null;
}

export interface TieredAssessmentResult {
  outcome: 'REMEDIATION' | 'ADVANCE' | 'COMPLETE' | 'CONTINUE';
  demonstrated: string;
  remaining: string;
  recommendation: string;
  why_changed: string;
  resource_type: string;
  evaluation_revision: number;
}

export interface CodeLabSubmission {
  attempt_id: number; artifact_id: number; artifact_version_id: number;
  item_identity: string; attempt_number: number; code_digest: string;
  correct: boolean; tests_total: number; tests_passed: number; tests_failed: number;
  exit_status: number; runtime_error_category?: string | null; status: string;
  submitted_at: string; learning_event_ref?: string | null;
}

export interface CodeLabSubmitResponse {
  attempt: CodeLabSubmission;
  judgment: { correct: boolean; tests_total: number; tests_passed: number; tests_failed: number; exit_status: number; timed_out: boolean; runtime_error_category?: string | null; stdout: string; stderr: string; duration_ms: number };
  learning_event_ref: string;
  evaluation_status: string;
  idempotent_replay: boolean;
}

export interface CodeLabResult {
  outcome: 'REMEDIATION' | 'ADVANCE'; title: string; demonstrated: string;
  remaining: string; recommendation: string; why_changed: string;
  resource_type: string; evaluation_revision: number;
}

export const artifactApi = {
  get: (artifactId: number, options: { version?: number; versionId?: number } = {}) =>
    getData<ArtifactPublicProjection>(`/api/items/${artifactId}`, {
      version: options.version,
      version_id: options.versionId,
    }),
  listVersions: (artifactId: number) =>
    getData<ArtifactVersionSummary[]>(`/api/items/${artifactId}/versions`),
  createVersion: (artifactId: number, body: CreateArtifactVersionRequest) =>
    postData<ArtifactPublicProjection, CreateArtifactVersionRequest>(`/api/items/${artifactId}/versions`, body),
  compare: (artifactId: number, fromVersion: number, toVersion: number) =>
    getData<ArtifactVersionComparison>(`/api/items/${artifactId}/compare`, {
      from_version: fromVersion,
      to_version: toVersion,
    }),
  reviseLecture: (
    artifactId: number,
    body: { thread_id: string; instruction: string; expected_version: number; idempotency_key: string },
  ) => postData<ArtifactPublicProjection, typeof body>(`/api/chat/tasks/artifacts/${artifactId}/revise`, body),
  reviseCode: (
    artifactId: number,
    body: { thread_id: string; instruction: string; expected_version: number; idempotency_key: string },
  ) => postData<ArtifactPublicProjection, typeof body>(`/api/chat/tasks/code/${artifactId}/revise`, body),
  runCode: (
    artifactId: number,
    body: {
      thread_id: string;
      artifact_version_id: number;
      execution_request_id: string;
      idempotency_key: string;
      parameters?: Record<string, unknown>;
      source?: string;
    },
  ) => postData<{ run: ArtifactCodeRun; idempotent_replay: boolean }, typeof body>(`/api/chat/tasks/code/${artifactId}/rerun`, body),
  listCodeRuns: (artifactId: number, artifactVersionId?: number) =>
    getData<ArtifactCodeRun[]>(`/api/chat/tasks/code/${artifactId}/runs`, { artifact_version_id: artifactVersionId }),
  getCodeRun: (artifactId: number, runId: number) =>
    getData<ArtifactCodeRun>(`/api/chat/tasks/code/${artifactId}/runs/${runId}`),
  submitCodeLab: (
    artifactId: number,
    body: { thread_id: string; artifact_version_id: number; source: string; submission_request_id: string; idempotency_key: string },
  ) => postData<CodeLabSubmitResponse, typeof body>(`/api/code-labs/${artifactId}/submit`, body),
  listCodeLabSubmissions: (artifactId: number, artifactVersionId: number) =>
    getData<CodeLabSubmission[]>(`/api/code-labs/${artifactId}/submissions`, { artifact_version_id: artifactVersionId }),
  replanCodeLab: (artifactId: number, body: { artifact_version_id: number; attempt_id: number }) =>
    postData<CodeLabResult, typeof body>(`/api/code-labs/${artifactId}/replan`, body),
  getCodeLabResult: (artifactId: number, artifactVersionId: number) =>
    getData<CodeLabResult | null>(`/api/code-labs/${artifactId}/result`, { artifact_version_id: artifactVersionId }),
  submitInteractiveAnswer: (
    artifactId: number,
    body: {
      thread_id: string;
      artifact_version_id: number;
      item_identity: string;
      selected_answer: number | string;
      submission_request_id: string;
      idempotency_key: string;
    },
  ) => postData<InteractiveAttemptResponse, typeof body>(`/api/chat/tasks/presentations/${artifactId}/attempts`, body),
  getInteractiveState: (artifactId: number, artifactVersionId: number, threadId?: string | null) =>
    getData<InteractiveAttemptState>(`/api/chat/tasks/presentations/${artifactId}/attempts/state`, {
      artifact_version_id: artifactVersionId,
      thread_id: threadId || undefined,
    }),
  requestInteractiveHint: (
    artifactId: number,
    body: { thread_id: string; artifact_version_id: number; item_identity: string },
  ) => postData<{ action: string; hint: string; event_ref: string }, typeof body>(`/api/chat/tasks/presentations/${artifactId}/attempts/hint`, body),
  viewInteractiveSolution: (
    artifactId: number,
    body: { thread_id: string; artifact_version_id: number; item_identity: string },
  ) => postData<{ action: string; explanation: string; event_ref: string }, typeof body>(`/api/chat/tasks/presentations/${artifactId}/attempts/solution`, body),
  replanTieredAssessment: (
    artifactId: number,
    body: { artifact_version_id: number; item_identity: string },
  ) => postData<TieredAssessmentResult, typeof body>(`/api/tiered-assessments/${artifactId}/replan`, body),
  getTieredAssessmentResult: (
    artifactId: number, artifactVersionId: number, itemIdentity: string,
  ) => getData<TieredAssessmentResult | null>(`/api/tiered-assessments/${artifactId}/result`, {
    artifact_version_id: artifactVersionId, item_identity: itemIdentity,
  }),
};
