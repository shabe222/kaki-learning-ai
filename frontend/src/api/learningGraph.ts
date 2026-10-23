import { getData, postData, putData, deleteData } from './client';

const BASE = '/api/learning-graphs';

export class GraphApiError extends Error {
  status?: number;
  code?: string;

  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.name = 'GraphApiError';
    this.status = status;
    this.code = code;
  }
}

export interface GraphSummary {
  id: number; user_id: number; title: string; goal: string; goal_type: string;
  external_id?: string | null; graph_revision?: number; graph_summary?: string | null;
  source_type: string; planning_rationale?: string | null;
  status: string; is_primary: boolean;
  total_nodes: number; completed_nodes: number; progress_pct: number;
  created_at: string | null; updated_at: string | null;
}

export interface FormalLearningUnit {
  unit_id: string; title: string; summary: string; node_ids: string[]; unit_order: number;
}

export interface FormalGraphNode {
  node_id: string; title: string; summary: string; node_type: string;
  learning_objectives: string[]; key_concepts: string[]; mastery_evidence: string[];
  difficulty_band: string; estimated_effort_minutes: number; source_refs: string[];
  resource_plan_ids: string[]; status: string; unit_id: string;
}

export interface FormalGraphEdge {
  edge_id: string; source_node_id: string; target_node_id: string; edge_type: string; reason: string;
}

export interface FormalResourcePlan {
  resource_plan_id: string; node_id: string; resource_type: string; purpose: string;
  trigger: string; status: string; priority: number; artifact_id: number | null;
}

export interface FormalGraphDetail {
  graph_id: string; graph_revision: number; graph_title: string; graph_summary: string;
  learning_units: FormalLearningUnit[]; nodes: FormalGraphNode[]; edges: FormalGraphEdge[];
  resource_plans: FormalResourcePlan[]; status: 'valid' | 'needs_review';
}

export interface GraphNode {
  id: number; graph_id: number; parent_id: number | null;
  title: string; description: string | null;
  knowledge_type: string; difficulty: number; estimated_minutes: number;
  order_index: number; status: string; mastery_score: number;
  resources: Array<{artifact_id: number | null; type: string; title: string; status: string; agent: string; created_at: string; error?: string}>;
  depends_on: string[]; tags: string[];
  auto_generated: boolean; is_hidden?: boolean; is_custom?: boolean;
  learning_goal?: string | null; self_assessment?: string | null;
  created_at: string | null;
}

export interface GraphDetail extends GraphSummary {
  nodes: GraphNode[];
  node_tree: Array<GraphNode & { children?: any[] }>;
}

export interface CourseTreeData {
  graph_id: number; title: string; progress_pct: number;
  total_nodes: number; status_counts: Record<string, number>;
  next_node: GraphNode | null;
  nodes: GraphNode[];
}

export interface NetworkData {
  graph_id: number; title: string;
  nodes: Array<{id: number; title: string; status: string; mastery_score: number; difficulty: number; x: number; y: number; size: number; depends_on: string[]; tags: string[]}>;
  edges: Array<{source: number; target: number; type: string}>;
}

// Wrap API errors into structured messages for UI display
async function callWithUserError<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    const msg = typeof detail === 'string' ? detail : detail?.message || e?.message || '网络异常';
    const code = typeof detail === 'object' ? detail?.code : undefined;
    const status = typeof e?.response?.status === 'number' ? e.response.status : undefined;
    throw new GraphApiError(typeof msg === 'string' ? msg : '请求失败，请稍后重试', status, code);
  }
}

export const graphApi = {
  list: () => getData<GraphSummary[]>(BASE),
  get: (id: number) => getData<GraphDetail>(`${BASE}/${id}`),
  create: (body: {title: string; goal?: string; goal_type?: string; source_type?: string}) =>
    postData<{id: number; title: string; is_primary: boolean}>(BASE, body),
  update: (id: number, body: Record<string, unknown>) =>
    putData<{id: number; status: string; is_primary: boolean}>(`${BASE}/${id}`, body),
  delete: (id: number) => deleteData(`${BASE}/${id}`),
  setPrimary: (id: number) => postData<{id: number; is_primary: boolean}>(`${BASE}/${id}/set-primary`),
  generateSkeleton: (id: number, chapters?: string[]) =>
    postData<{created: number}>(`${BASE}/${id}/generate-skeleton`, {chapters}),
  generateRecommended: (id: number) =>
    postData<{generated: number}>(`${BASE}/${id}/generate-recommended`),
  courseTree: (id: number) => getData<CourseTreeData>(`${BASE}/${id}/course-tree`),
  knowledgeNetwork: (id: number) => getData<NetworkData>(`${BASE}/${id}/knowledge-network`),
  addNode: (graphId: number, body: Record<string, unknown>) =>
    postData<{id: number; title: string; status: string}>(`${BASE}/${graphId}/nodes`, body),
  updateNode: (graphId: number, nodeId: number, body: Record<string, unknown>) =>
    putData<{id: number; status: string}>(`${BASE}/${graphId}/nodes/${nodeId}`, body),
  completeNode: (graphId: number, nodeId: number, mastery: number) =>
    postData<{id: number; status: string; mastery_score: number}>(`${BASE}/${graphId}/nodes/${nodeId}/complete`, {mastery_score: mastery}),
  needsReview: (graphId: number, nodeId: number) =>
    postData<{id: number; status: string}>(`${BASE}/${graphId}/nodes/${nodeId}/needs-review`),
  generateNodeResources: (graphId: number, nodeId: number) =>
    postData<GraphNode>(`${BASE}/${graphId}/nodes/${nodeId}/generate-resources`),
  hideNode: (graphId: number, nodeId: number) =>
    postData<{id: number; is_hidden: boolean}>(`${BASE}/${graphId}/nodes/${nodeId}/hide`),
  unhideNode: (graphId: number, nodeId: number) =>
    postData<{id: number; is_hidden: boolean}>(`${BASE}/${graphId}/nodes/${nodeId}/unhide`),
  reorderNodes: (graphId: number, orders: Array<{node_id: number; order_index: number}>) =>
    postData<{message: string}>(`${BASE}/${graphId}/reorder-nodes`, {node_orders: orders}),
  getFormal: (graphId: string) => callWithUserError(() => getData<FormalGraphDetail>(`${BASE}/formal/${encodeURIComponent(graphId)}`)),
  orchestrate: (body: {
    request_id: string; idempotency_key: string; thread_id: string; user_goal: string;
    domain: string; source_material_ids?: string[]; update_mode?: string;
    graph_id?: string | null; graph_revision?: number; replaced_source_ref_ids?: string[];
  }) => callWithUserError(() => postData<FormalGraphDetail>(`${BASE}/orchestrate`, body)),
  materialize: (graphId: string, nodeId: string, planId: string, threadId: string, executionKey: string) =>
    callWithUserError(() => postData<{artifact_id: number; resource_plan_id: string; status: string}>(
      `${BASE}/formal/${encodeURIComponent(graphId)}/nodes/${encodeURIComponent(nodeId)}/resource-plans/${encodeURIComponent(planId)}/generate`,
      {thread_id: threadId, execution_key: executionKey},
    )),
  retryMaterialize: (graphId: string, nodeId: string, planId: string, threadId: string, executionKey: string) =>
    callWithUserError(() => postData<{artifact_id: number; resource_plan_id: string; status: string}>(
      `${BASE}/formal/${encodeURIComponent(graphId)}/nodes/${encodeURIComponent(nodeId)}/resource-plans/${encodeURIComponent(planId)}/retry`,
      {thread_id: threadId, execution_key: executionKey},
    )),
  getArtifact: (artifactId: number) => getData<Record<string, any>>(`/api/chat/tasks/artifacts/${artifactId}`),
};
