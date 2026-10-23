import type { BlackboardScript } from '../blackboard/types';

export type TideMessageRole = 'user' | 'assistant' | 'system';

export type TideMessageStatus = 'complete' | 'streaming' | 'failed' | 'stopped';

export interface TideMessageArtifact {
  id?: number | string;
  kind?: string;
  title?: string;
}

export interface TideHistoryMessage {
  id?: string;
  role: TideMessageRole;
  content: string;
  conversation_action?: DaryaConversationAction | null;
  orchestration_trace?: DaryaOrchestrationStep[];
  search_evidence?: DaryaSearchGrounding | null;
  artifact?: TideMessageArtifact | null;
  metadata?: {
    cognition_orchestration?: DaryaCognitionOrchestration | null;
    [key: string]: unknown;
  } | null;
  cognition_orchestration?: DaryaCognitionOrchestration | null;
  learning_loop_domain_id?: string | null;
  teaching_method_label?: string | null;
}

export interface TideMessage extends TideHistoryMessage {
  id: string;
  status: TideMessageStatus;
  requestId?: string;
  conversationAction?: DaryaConversationAction | null;
  searchEvidence?: DaryaSearchGrounding | null;
  searchStatus?: string | null;
  /** 这一轮正在做什么（后端在真实阶段点发出来的） */
  progress?: string | null;
  /** 这一轮的阶段轨迹，按顺序留下来给思考过程显示用 */
  progressTrail?: string[];
}

export interface DaryaCognitionWorkBrief {
  goal: string;
  stage: string;
  confirmed_facts: string[];
  observed_facts: string[];
  unknowns: string[];
  strategy: string;
  deliverables: string[];
  resource_purposes: string[];
  success_checks: string[];
}

export interface DaryaCognitionHelpOpportunity {
  true_goal: string;
  highest_value_unknown: string;
  private_input_needed: string | null;
  best_immediate_deliverable: string;
  success_signal: string;
}

export interface DaryaCognitionResearchTask {
  needed: boolean;
  questions: Array<{ claim: string; why_it_changes_the_plan: string }>;
  stop_condition: string;
}

/** Public, reviewable work brief. It intentionally excludes hidden reasoning. */
export interface DaryaCognitionOrchestration {
  contract_version: 'darya_cognition_orchestration_v2';
  source: 'structured_model' | 'context_only_fallback';
  turn_mode: 'CONVERSATION' | 'LEARNING' | 'TASK' | 'PAUSED' | 'STOPPED';
  goal_lifecycle?: 'ACTIVE' | 'PAUSED' | 'COMPLETED' | 'BACKGROUND';
  goal_completion_reason?: string;
  confidence: number;
  help_opportunity: DaryaCognitionHelpOpportunity;
  research_task: DaryaCognitionResearchTask;
  work_brief: DaryaCognitionWorkBrief;
}

export interface DaryaSearchEvidenceSource {
  source_id: string;
  title: string;
  publisher: string;
  url: string;
  locator: string;
  authority: 'primary' | 'authoritative' | 'secondary' | 'community' | 'unknown';
  trust_reason: string;
  freshness: {
    status: 'LIVE_RETRIEVED' | 'RETRIEVED_THIS_TURN';
    retrieved_at: string;
    source_date?: string | null;
    current_claim: boolean;
  };
  claim_provenance: {
    contract_version: string;
    claim: string;
    support_status: 'SUPPORTED' | 'CORROBORATES' | 'CONFLICTING' | 'LEAD_ONLY' | 'DISCOVERY_ONLY' | 'UNVERIFIED';
    evidence_span: string;
  };
}

export interface DaryaSearchGrounding {
  contract_version: 'darya_session_grounded_knowledge_v1';
  decision: 'SEARCH_NOT_NEEDED' | 'SEARCH_RECOMMENDED' | 'SEARCH_REQUIRED';
  decision_reason: string;
  trigger: string;
  search_invocation_count: number;
  search_executed: boolean;
  provider?: string | null;
  evidence_status: 'SUPPORTED' | 'PARTIALLY_SUPPORTED' | 'CONFLICTING' | 'UNVERIFIED' | 'NOT_SEARCHED';
  review_disposition: 'ANSWER' | 'FURTHER_RETRIEVAL' | 'CALIBRATED_FALLBACK' | 'NO_SEARCH';
  sufficient: boolean;
  sources: DaryaSearchEvidenceSource[];
  conflict: { status: 'NONE' | 'UNRESOLVED' | 'RESOLVED_SCOPE_MISMATCH'; notes: string[]; resolution_policy: string };
  grounding_scope: 'SESSION_THREAD';
  installed_domain_pack_created: false;
  durable_memory_write: false;
  formal_learning_evidence_created: false;
  mastery_or_route_write: false;
}

export interface DaryaArtifactBinding {
  artifact_id: number;
  version_id: number;
  version_number: number;
  kind: string;
  artifact_type: string;
  title: string;
  reason: string;
  status: string;
}

export interface DaryaOrchestrationStep {
  label: string;
  detail: string;
  technical_label?: string;
  status: 'completed';
}

export interface DaryaConversationAction {
  action_key: string;
  source_message_id: string;
  assistant_message_id: string;
  kind: 'HANDOUT' | 'PRACTICAL_GUIDE' | 'ASSESSMENT' | 'CODE_LAB' | 'PRESENTATION' | 'MINDMAP' | 'VIDEO' | 'STORYBOOK' | 'DEMONSTRATE';
  status: 'planned' | 'preparing' | 'reviewing' | 'ready' | 'failed';
  assistant_response: string;
  reason: string;
  artifact: DaryaArtifactBinding | null;
  /** 黑板脚本：几何由后端解算好，前端只负责照它落笔 */
  blackboard_script?: BlackboardScript | null;
  trace: DaryaOrchestrationStep[];
  retryable: boolean;
  error_message?: string | null;
  working_result?: {
    kind: 'UNREVIEWED_DRAFT';
    body: string;
    limitation: string;
  } | null;
}

export interface DaryaMaterialSummary {
  artifact_id: number;
  kind: string;
  // 后端 `_product_category` 会返回下面全部取值；旧的联合类型漏了几类，
  // 让真实存在的类别在类型上"不存在"。标签文案见 DaryaOceanSurface 的 categoryCopy。
  category: 'handout' | 'guide' | 'assessment' | 'code' | 'mindmap' | 'presentation' | 'video' | 'audio' | 'picture_book' | 'other';
  type: string;
  title: string;
  topic: string;
  summary: string;
  version: number;
  status: string;
  review_verdict?: string | null;
  source_table?: 'generated_items' | 'exam_papers' | string;
  open_action?: string;
  thread_id?: string | null;
  created_at: string;
}

export interface DaryaUnderstandingClaim {
  id: string;
  source_kind: 'memory' | 'impression';
  source_id: number;
  statement: string;
  why_it_matters: string;
  certainty: 'confirmed' | 'observed' | 'inferred' | 'uncertain';
  certainty_label: string;
  can_confirm: boolean;
  can_correct: boolean;
  can_forget: boolean;
  revision: number | null;
  updated_at: string;
}

export interface DaryaSelfUnderstanding {
  contract_version: 'darya_self_understanding_v1';
  claims: DaryaUnderstandingClaim[];
  groups: Record<'confirmed' | 'observed' | 'inferred' | 'uncertain', DaryaUnderstandingClaim[]>;
  empty: boolean;
  boundaries: {
    formal_learning_state_dominates: true;
    soft_preferences_used_for_mastery: false;
    second_profile_store_created: false;
  };
}

export interface TideConversation {
  id: number;
  thread_id: string;
  messages: TideHistoryMessage[];
  message_count: number;
  response_style?: string | null;
  mode?: string | null;
  created_at: string;
  last_message_at: string;
  updated_at?: string;
  title?: string;
}

export interface TideCreatedConversation {
  thread_id: string;
  title: string;
  mode?: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface TideStreamRequest {
  message: string;
  history: TideHistoryMessage[];
  thread_id: string;
  user_id: number;
  style: 'concise' | 'normal' | 'detailed';
  message_id: string;
  retry: boolean;
  experience: 'tide_v1';
  /**
   * 这一轮的回答会被朗读出来（语音通话）。省略即 false，打字对话完全不受影响。
   *
   * 加它的原因：通话以前走的是和打字一模一样的请求，模型不知道自己说的话是要被
   * 听的，于是按「正常长度」写一整套，前端再用 speakableText() 截到 600 字去念。
   * 截断发生在生成之后——模型讲的东西有一半从来没被人听到过。
   */
  speech?: boolean;
}

export type TideDecisionMode = 'EXECUTE' | 'ORIENT' | 'EXPLAIN' | 'CLARIFY';

export interface TideSituationSnapshot {
  goal: string;
  current_stage: string;
  urgency: 'high' | 'normal';
  risk: string;
  recommended_next_action: string;
}

export interface TideInteractionContext {
  mode: TideDecisionMode;
  reason: string;
  proactive_level: 0 | 1 | 2;
  sidecar: boolean;
  sidecar_type?: string | null;
  snapshot: TideSituationSnapshot;
}

export type TidePerspectiveStatus = 'observed' | 'inferred' | 'confirmed';
export type TidePerspectiveFieldName =
  | 'goal'
  | 'goal_kind'
  | 'parallel_goals'
  | 'archived_goals'
  | 'stage'
  | 'constraints'
  | 'current_artifacts'
  | 'confirmed_decisions'
  | 'unfinished_items'
  | 'next_action';

export interface TidePerspectiveField {
  value: unknown;
  status: TidePerspectiveStatus;
  source_reference: Record<string, unknown>;
  updated_at: string;
}

export interface TideSharedPerspective {
  thread_id: string;
  revision: number;
  workspace_object_id?: number | null;
  fields: Partial<Record<TidePerspectiveFieldName, TidePerspectiveField>>;
  updated_at: string | null;
}

export interface TidePerspectiveControlRequest {
  field: TidePerspectiveFieldName;
  action: 'confirm' | 'correct' | 'reject';
  expected_revision: number;
  value?: unknown;
}

export type TideUserContextStatus = 'USER_PROVIDED' | 'INFERRED' | 'CONFIRMED' | 'REJECTED';

export interface TideUserContextDocument {
  material_id: number;
  file_name: string;
  title: string;
  file_type: string;
  size_bytes: number;
  parse_status: string;
  page_count?: number | null;
  section_count?: number | null;
  ingested_at?: string | null;
}

export interface TideUserContextClaim {
  id: number;
  kind: string;
  value: string;
  status: TideUserContextStatus;
  origin_status: TideUserContextStatus;
  confidence: number;
  source_reference: Record<string, unknown>;
  scope: 'thread';
  revision: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TideUserContextSnapshot {
  thread_id: string;
  scope: 'thread';
  memory_boundary: 'NOT_LONG_TERM_MEMORY';
  documents: TideUserContextDocument[];
  claims: TideUserContextClaim[];
  summary: string[];
  confirmed_count: number;
  rejected_count: number;
}

/** 一个研究任务的状态投影：阶段、来源、引用与报告。 */
export interface TideResearchJob {
  artifact_id: number;
  status: 'QUEUED' | 'PLANNED' | 'SEARCHING' | 'READING' | 'SYNTHESIZING' | 'REVIEWING' | 'READY' | 'PARTIAL' | 'FAILED';
  stage_copy: string;
  title: string;
  question: string;
  sub_questions: string[];
  source_refs: Array<{ title?: string; url?: string }>;
  citation_report: { cited?: number[]; cited_count?: number; invented?: number[]; cites_nothing?: boolean };
  content_review: { verdict?: string; measured?: boolean; scores?: Record<string, number> };
  report: string;
  working_result: string;
  error: string | null;
  summary: string;
}

export interface TideUserContextControlRequest {
  action: 'confirm' | 'correct' | 'reject';
  expected_revision: number;
  value?: string;
}

export type DaryaMemoryType = 'FACT' | 'PREFERENCE' | 'LESSON' | 'FUTURE_POLICY';
export type DaryaMemoryState = 'ACTIVE' | 'STALE';

export interface DaryaLongTermMemory {
  id: number;
  type: DaryaMemoryType;
  topic: string;
  content: string;
  provenance: Record<string, unknown>;
  epistemic_status: 'USER_PROVIDED' | 'USER_CONFIRMED';
  confidence: number;
  source_context_id?: string | null;
  source_claim_id?: number | null;
  source_thread_id: string;
  source_message_id?: string | null;
  freshness: 'STABLE' | 'TIME_BOUND';
  revalidate_after_at?: string | null;
  state: DaryaMemoryState;
  revision: number;
  created_at?: string | null;
  updated_at?: string | null;
  last_confirmed_at?: string | null;
  last_used_at?: string | null;
}

export interface DaryaLongTermMemorySnapshot {
  scope: 'LONG_TERM_PERSONAL';
  reason: 'contextual_empty' | 'relevant' | 'inspect' | 'saved' | 'correct' | 'forget';
  memories: DaryaLongTermMemory[];
}

export interface DaryaMemoryControlRequest {
  action: 'correct' | 'forget';
  expected_revision: number;
  content?: string;
}

export interface DaryaProactiveOpportunity {
  message_id: string;
  conversation_id: string;
  trigger_event: string;
  trigger_event_id: string;
  evidence_ids: string[];
  learner_state_version?: string | null;
  resource_artifact_ids: number[];
  title: string;
  content: string;
  priority: 'normal' | 'high';
  recommended_action: {
    type?: 'continue_tide_learning' | 'open_resource';
    label?: string;
    prompt?: string;
    target_title?: string;
    artifact_id?: number | null;
  };
  resource_action: {
    surface?: 'tide_sidecar';
    opportunity_kind?: 'unresolved_difficulty' | 'repeated_difficulty' | 'useful_continuation';
    why_now?: string;
    interaction_state?: 'available' | 'later' | 'opened';
    later_until?: string | null;
  };
  created_at?: string | null;
  read_at?: string | null;
  dismissed_at?: string | null;
  actioned_at?: string | null;
}

export interface DaryaOpportunityOpenResult {
  idempotent_replay: boolean;
  action: DaryaProactiveOpportunity['recommended_action'];
  conversation_id?: string | null;
  assistant_response?: string | null;
  artifact_id?: number | null;
}

export type TideWorkspaceObjectStatus = 'exploring' | 'planning' | 'creating' | 'reviewing' | 'completed';

export interface TideWorkspaceArtifactReference {
  artifact_id: number;
  version_id?: number | null;
  version_number?: number | null;
  kind?: string | null;
  title?: string | null;
}

export interface TideWorkspaceObject {
  id: number;
  thread_id: string;
  title: string;
  purpose: string;
  status: TideWorkspaceObjectStatus;
  related_artifact_references: TideWorkspaceArtifactReference[];
  related_decisions: string[];
  next_action: string | null;
  is_confirmed: boolean;
  revision: number;
  source_perspective_revision: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface TideWorkspaceObjectControlRequest {
  action: 'confirm' | 'rename' | 'correct_purpose' | 'set_focus';
  expected_revision: number;
  value?: unknown;
}

export type TideArtifactActionStatus =
  | 'analyzing'
  | 'proposing'
  | 'creating'
  | 'completed'
  | 'conflicted'
  | 'blocked'
  | 'failed'
  | 'cancelled';

export interface TideArtifactActionProposal {
  scope?: string;
  page_number?: number | null;
  instruction?: string;
  changes?: string[];
  source_version_number?: number;
  target_version_number?: number;
  current_version_number?: number;
  requires_confirmation?: boolean;
  conflict_detected?: boolean;
  quality_gate?: {
    status?: 'passed' | 'blocked';
    final_verdict?: 'pass' | 'revise' | 'block';
    review_rounds?: number;
    max_review_rounds?: number;
    revision_rounds?: number;
    summary?: string;
    issue_count?: number;
    needs_evidence?: Array<{
      location?: string;
      description?: string;
      evidence_status?: 'SUPPORTED' | 'USER_PROVIDED' | 'INFERRED' | 'UNVERIFIED';
    }>;
    materially_changed?: boolean;
    strengthened?: string[];
  };
}

export interface TideArtifactAction {
  id: number;
  thread_id: string;
  workspace_object_id: number;
  artifact_id: number;
  source_version_id: number;
  source_version_number?: number | null;
  target_version_id?: number | null;
  action_type: 'improve_presentation';
  reason: string;
  status: TideArtifactActionStatus;
  proposal: TideArtifactActionProposal;
  revision: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface TideArtifactActionControlRequest {
  control: 'confirm' | 'cancel';
  expected_revision: number;
}

export interface TideArtifactActionControlResult {
  action: TideArtifactAction;
  workspace_object: TideWorkspaceObject;
}

export interface TideArtifactVersionProjection {
  artifact_id: number;
  version_id: number;
  version_number: number;
  version: number;
  kind: string;
  type: string;
  title: string;
  topic?: string | null;
  status: string;
  previous_version_ref?: { version_id: number; version_number: number } | null;
  current_version_ref: { version_id: number; version_number: number };
  render_payload: {
    content: string;
    data: Record<string, unknown>;
  };
  raw_data: Record<string, unknown>;
}

export interface TidePresentationSlide {
  index: number;
  type: string;
  title: string;
  subtitle: string;
  body: string;
  items: string[];
  narration: string;
  code: string;
  language: string;
  layout: string;
  visualReferences: string[];
}

export interface TideArtifactComparisonChange {
  field: 'title' | 'subtitle' | 'body' | 'items' | 'narration' | 'code' | 'layout' | 'visualReferences';
  label: string;
  before: string;
  after: string;
}

export interface TideArtifactWorkbench {
  artifactId: number;
  title: string;
  currentVersionId: number;
  currentVersionNumber: number;
  sourceVersionId: number | null;
  sourceVersionNumber: number | null;
  focusedPageNumber: number;
  slides: TidePresentationSlide[];
  sourceSlides: TidePresentationSlide[];
  comparison: TideArtifactComparisonChange[];
}

export type TideStreamEvent =
  | { type: 'context'; memory_loaded?: boolean; effective_response_style?: string; user_context_count?: number }
  | { type: 'user_context_used'; claims: TideUserContextClaim[] }
  | { type: 'long_term_memory_used' | 'long_term_memory_saved'; snapshot: DaryaLongTermMemorySnapshot }
  | ({ type: 'tide_context' } & TideInteractionContext)
  | { type: 'shared_perspective'; snapshot: TideSharedPerspective }
  | { type: 'workspace_object'; object: TideWorkspaceObject }
  | { type: 'artifact_action'; action: TideArtifactAction }
  | { type: 'conversation_action'; action: DaryaConversationAction }
  | { type: 'search_status'; status: 'checking'; message: string }
  | { type: 'progress'; stage: string; label: string }
  | { type: 'search_grounding'; assistant_message_id?: string; grounding: DaryaSearchGrounding }
  | { type: 'agent_start'; agent?: string }
  | { type: 'agent_complete'; agent?: string; status?: string }
  | { type: 'agent_content'; agent?: string; resource_type?: string; preview?: string }
  | { type: 'memory_candidate'; [key: string]: unknown }
  | { type: 'memory_saved'; [key: string]: unknown }
  | { type: 'error'; message?: string; detail?: string; error?: string }
  | { type: 'done'; request_id?: string; assistant_message_id?: string }
  | { type: string; [key: string]: unknown };

export interface TideStreamResult {
  requestId?: string;
  assistantMessageId?: string;
}

export interface DaryaLearningFitProjection {
  contract_version: 'darya_visual_learner_fit_v1';
  goal?: string | null;
  target?: string | null;
  gap?: string | null;
  resource_type?: 'EXPLANATION_CARD' | 'PRACTICE_TASK' | 'WORKED_EXAMPLE' | 'QUICK_CHECK' | null;
  why_now?: string | null;
  not_chosen_reason?: string | null;
  preference_reason?: string | null;
  evidence?: string | null;
  evidence_status?: 'OBSERVED' | 'OBSERVED_INSUFFICIENT';
  change?: { before?: string; now?: string; changed?: boolean };
  route?: { movement?: 'start' | 'stay' | 'backward' | 'forward'; before?: string | null; now?: string | null; reason?: string | null };
  previous_resource?: string | null;
  previous_effectiveness?: string | null;
  confidence_label?: string | null;
  uncertainty?: string | null;
  profile_statuses?: Array<'OBSERVED' | 'INFERRED' | 'CONFIRMED'>;
  correction_applied?: boolean;
  updated_at?: string;
}

export interface DaryaActiveRouteProjection {
  contract_version: 'darya_active_route_v1';
  thread_id: string;
  revision: string;
  authority: {
    route: 'DaryaLearningRouteState';
    evidence: 'WorkspaceLearningEvent';
    next_resource: 'GraphResourcePlan';
  };
  goal: string;
  domain_title?: string | null;
  target_title: string;
  current_title: string;
  passed_milestones: Array<{ title: string; status: string }>;
  current_gap: string;
  latest_change: {
    source: 'conversation' | 'assessment' | 'code_submission';
    summary: string;
    explanation: string;
    changed: boolean;
    event_ref?: string | null;
    observed_at?: string | null;
  };
  next: { title: string; reason: string; resource_type?: string | null };
  after_title?: string | null;
  why_this_learning: string;
  updated_at?: string | null;
}

export interface DaryaLearningLoopSnapshot {
  thread_id: string;
  route: Record<string, unknown> | null;
  state: Record<string, unknown>;
  active_route: DaryaActiveRouteProjection | null;
  teaching_stage?: DaryaTeachingStageProjection | null;
  updated_at?: string | null;
}

export interface DaryaTeachingStageProjection {
  scope?: 'bounded' | 'general';
  status: 'ACTIVE' | 'PAUSED' | 'ENDED';
  method: string;
  method_reason: string;
  resource_type?: string | null;
  summary?: DaryaTeachingStageSummary | null;
  progress: DaryaTeachingStageSummary;
  last_transition?: { from?: string | null; to?: string | null; reason?: string | null; evidence_quality?: string | null } | null;
  revision: number;
}

export interface DaryaTeachingStageSummary {
  topic: string;
  observed: string;
  evidence_count: number;
  remaining_gap: string;
  next_step: string;
  mastery_conclusion: string;
}
