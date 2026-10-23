import { ArrowLeft, BookOpen, CheckCircle2, ChevronRight, Clock3, Eye, EyeOff, FileText, GitBranch, Layers, Lightbulb, Network, Pause, Play, Plus, RefreshCw, Sparkles, Target, ZoomIn, ZoomOut, BarChart3, Archive } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { App as AntdApp, Button, Input, Modal, Progress, Select, Tag } from 'antd';
import { graphApi, GraphApiError, type GraphSummary, type GraphNode, type CourseTreeData, type NetworkData, type FormalGraphDetail, type FormalResourcePlan } from '../api/learningGraph';
import { learningEvaluatorApi, LearningEvaluatorApiError, type PublicLearningEvaluation, type PublicNodeAssessment } from '../api/learningEvaluator';
import { getData } from '../api/client';
import { getNormalizedLearnerState, type NormalizedLearnerState, type PreferenceTaxonomy } from '../api/learnerState';
import type { BackendProfile } from '../api/modules';
import { useAuthStore } from '../stores/authStore';
import { StateBlock } from '../components/StateBlock';
import { ArtifactShell } from '../components/artifact/ArtifactShell';
import type { ArtifactPublicProjection, ArtifactSelectionReference } from '../api/artifacts';
import recArt from '../assets/generated/feature-study-assistant.jpg';
import emptyPlanArt from '../assets/generated/empty-persimmon-basket.jpg';
import styles from './MyLearningPage.module.css';

const TYPE_LABEL: Record<string, string> = {
  theory: '理论基础', practice: '实践训练', project: '项目应用', review: '复习巩固',
};

const STATUS_LABEL: Record<string, string> = {
  planned: '已规划', generating: '正在生成', generated: '已就绪', learning: '学习中',
  completed: '已完成', needs_review: '建议复习', failed: '生成失败',
};
const STATUS_COLOR: Record<string, string> = {
  planned: '#bfb8b2', generating: '#f5a623', generated: '#4a90d9', learning: '#ff5a14',
  completed: '#53ad5c', needs_review: '#e34f4f', failed: '#e34f4f',
};
const TYPE_ICONS: Record<string, any> = { document: FileText, question: BarChart3, mindmap: GitBranch, code: BookOpen, ppt_interactive: Play, tutor: Target, video: Play };

// 产品化标签：正式图谱内部的工程枚举不直接暴露给学生
const EDGE_TYPE_LABEL: Record<string, string> = {
  prerequisite: '前置', supports: '支撑', extends: '延伸', contrasts: '对比', applies: '应用',
};
const DIFFICULTY_BAND_LABEL: Record<string, string> = {
  introductory: '入门', basic: '基础', intermediate: '进阶', advanced: '挑战',
};
const GRAPH_STATUS_LABEL: Record<string, string> = {
  active: '进行中', paused: '已暂停', archived: '已归档', completed: '已完成', draft: '草稿',
};
const RESOURCE_TYPE_LABEL: Record<string, string> = {
  HANDOUT: '讲义', QUESTION: '互动题', CODE: '代码练习', VIDEO: '视频', MIND_MAP: '思维导图',
  INTERACTIVE_CLASS: '互动课件', PPT: '课件', ADVENTURE: '闯关挑战',
  document: '讲义', question: '互动题', code: '代码练习', video: '视频',
  mindmap: '思维导图', ppt_interactive: '互动课件', tutor: 'AI 讲解',
};
// 学生视角的资源动作：不说"生成/产物"，直接说能做什么
const RESOURCE_ACTION_LABEL: Record<string, string> = {
  HANDOUT: '查看讲义', QUESTION: '做练习', CODE: '看代码示例', MIND_MAP: '看思维导图',
  VIDEO: '看视频', INTERACTIVE_CLASS: '看互动课件', PPT: '看课件', ADVENTURE: '进入闯关挑战',
  document: '查看讲义', question: '做练习', code: '看代码示例', video: '看视频',
  mindmap: '看思维导图', ppt_interactive: '看互动课件', tutor: '听 AI 讲解',
};
// 画像偏好 → 推荐动作（用于"准备学习资料"时给一句话理由）
const RECOMMEND_ACTION: Record<string, string> = {
  visual: '看图解', video: '看视频', reading: '看讲义', document: '看讲义',
  practice: '做练习', code: '看代码示例', example: '看例题',
  step_by_step: '按步骤学习', concise: '用简洁版讲解', detailed: '用详细版讲解',
};


const CREATE_IDENTITY_PREFIX = 'kaki:learning-graph:create:';
const RESOURCE_IDENTITY_PREFIX = 'kaki:learning-graph:resource:';
const EVALUATION_IDENTITY_PREFIX = 'kaki:learning-evaluator:evaluate:';


const EVALUATION_BAND_LABEL: Record<string, string> = {
  NO_EVIDENCE: '暂无证据', LOW: '较低', MEDIUM: '中等', HIGH: '较高',
};
const EVALUATION_STATUS_LABEL: Record<string, string> = {
  INSUFFICIENT_EVIDENCE: '证据仍不足', EMERGING: '正在形成理解', DEVELOPING: '掌握正在发展',
  PROFICIENT: '已达到熟练水平', SECURE: '掌握较稳定', NEEDS_REVIEW: '建议继续复核', CONTRADICTORY: '证据存在矛盾',
};
const EVALUATION_ACTION_LABEL: Record<string, string> = {
  COLLECT_MORE_EVIDENCE: '再完成一次不同形式的练习',
  TARGETED_QUESTION: '完成一道针对性问题',
  TRANSFER_TASK: '在新情境中再做一次判断',
  TEACH_BACK: '尝试用自己的话讲一遍',
  CODE_CHALLENGE: '完成一次代码挑战',
  MISCONCEPTION_CHECK: '针对可能的误区再验证一次',
  SPACED_RECHECK: '隔一段时间再复查',
  REVIEW_SOURCE: '回看对应学习资料',
  MANUAL_REVIEW: '请老师或助教协助复核',
};
const EVALUATION_GAP_LABEL: Record<string, string> = {
  NO_VALID_EVIDENCE: '还没有可验证的独立作答',
  INSUFFICIENT_EVIDENCE: '当前证据数量还不够',
  INSUFFICIENT_INDEPENDENT_EVIDENCE: '还缺少独立完成的证据',
  INSUFFICIENT_TRANSFER_EVIDENCE: '还缺少新情境中的迁移证据',
  OBJECTIVE_UNASSESSED: '有学习目标尚未被覆盖',
  OBJECTIVE_NOT_ACHIEVED: '部分学习目标尚未达到',
  OBJECTIVE_COUNTER_EVIDENCE: '存在与当前判断相反的证据',
  REVIEW_REQUIRED: '需要继续复核',
  UNCLASSIFIED_PRIVATE_SIGNAL: '这里还缺少可以确认学习变化的证据',
};

function formatEvaluationCode(code: string): string {
  return EVALUATION_GAP_LABEL[code] || EVALUATION_ACTION_LABEL[code] || code.replace(/_/g, ' ').toLowerCase();
}

function evaluationSummary(result: PublicLearningEvaluation | null | undefined, nodeId: string): PublicNodeAssessment | null {
  return result?.node_assessments?.find(item => item.node_id === nodeId) || result?.node_assessments?.[0] || null;
}

function onboardingValue(profile: BackendProfile | null, key: string) {
  const raw = profile?.profile_data?.profile_onboarding;
  const entries = raw && typeof raw === 'object' ? (raw as Record<string, unknown>).entries : null;
  const entry = entries && typeof entries === 'object' ? (entries as Record<string, unknown>)[key] : null;
  const value = entry && typeof entry === 'object' ? (entry as Record<string, unknown>).value : null;
  return typeof value === 'string' ? value.trim() : '';
}

function learningPathEvidence(profile: BackendProfile | null) {
  const baseline = onboardingValue(profile, 'knowledge_baseline');
  const preference = onboardingValue(profile, 'learning_preference');
  const rhythm = onboardingValue(profile, 'study_rhythm');
  if (!baseline && !preference && !rhythm) return null;
  return {
    headline: preference ? `按你的偏好，从「${preference}」开始` : '按你已确认的学习情况安排起点',
    detail: baseline
      ? `你提到：${baseline}`
      : rhythm
        ? `你可用的学习节奏：${rhythm}`
        : '这条路线会随着你后续的真实学习证据继续调整。',
    cadence: rhythm,
  };
}

const LEARNER_PREFERENCE_LABEL: Record<PreferenceTaxonomy, string> = {
  visual: '图示优先', video: '视频讲解', reading: '阅读理解', document: '讲义资料',
  practice: '练习驱动', code: '代码实践', example: '例题引导', step_by_step: '逐步讲解',
  concise: '简洁表达', detailed: '详细说明',
};
const UNDERSTANDING_STAGE_LABEL: Record<string, string> = {
  unknown: '等待可信学习证据', emerging: '正在建立理解', developing: '掌握正在发展',
  partial: '已形成部分掌握', secure: '掌握较稳定',
};

function normalizedPathEvidence(state: NormalizedLearnerState | null) {
  if (!state) return null;
  const preferences = state.preferred_modalities.map(item => LEARNER_PREFERENCE_LABEL[item]).filter(Boolean);
  const focus = state.next_teaching_focus || state.current_topic || state.weak_points[0];
  return {
    headline: preferences.length ? `本轮按「${preferences.slice(0, 2).join('、')}」呈现` : '按已确认的学习证据安排本轮路径',
    detail: focus ? `建议优先学习：${focus}` : state.current_goal ? `当前目标：${state.current_goal}` : '学习路线会随新的有效证据继续调整。',
    cadence: state.pace && state.pace !== 'balanced' ? state.pace : '',
  };
}

function fallbackFingerprint(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

async function operationFingerprint(value: string): Promise<string> {
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  }
  return fallbackFingerprint(value);
}

function createNonce(): string {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function getStoredOperationIdentity(storageKey: string, prefix: string): string {
  try {
    const existing = globalThis.sessionStorage?.getItem(storageKey);
    if (existing) return existing;
    const created = `${prefix}-${createNonce()}`;
    globalThis.sessionStorage?.setItem(storageKey, created);
    return created;
  } catch {
    return `${prefix}-${createNonce()}`;
  }
}

function clearStoredOperationIdentity(storageKey: string): void {
  try { globalThis.sessionStorage?.removeItem(storageKey); } catch { /* storage is optional */ }
}

export function MyLearningPage() {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [graphs, setGraphs] = useState<GraphSummary[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [treeData, setTreeData] = useState<CourseTreeData | null>(null);
  const [networkData, setNetworkData] = useState<NetworkData | null>(null);
  const [viewMode, setViewMode] = useState<'tree' | 'network'>('tree');
  const [expandedNodes, setExpandedNodes] = useState<Set<number>>(new Set());
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createTitle, setCreateTitle] = useState('');
  const [createGoal, setCreateGoal] = useState('');
  const [networkScale, setNetworkScale] = useState(1);
  const [networkPan, setNetworkPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [formalGraph, setFormalGraph] = useState<FormalGraphDetail | null>(null);
  const [formalError, setFormalError] = useState('');
  const [materializingPlan, setMaterializingPlan] = useState('');
  const [openedArtifactId, setOpenedArtifactId] = useState<number | null>(null);
  const [formalNotice, setFormalNotice] = useState('');
  const [formalNoticeTone, setFormalNoticeTone] = useState<'info' | 'success' | 'error'>('info');
  const [detailOpen, setDetailOpen] = useState<Record<string, boolean>>({});
  const [judgmentOpen, setJudgmentOpen] = useState<Record<string, boolean>>({});
  const [creatingGraph, setCreatingGraph] = useState(false);
  // In-flight guard for the paid resource-generation buttons: a double click
  // must not fire a second LLM 讲义/练习 generation.
  const [generatingResources, setGeneratingResources] = useState(false);
  const [evaluations, setEvaluations] = useState<Record<string, PublicLearningEvaluation | null>>({});
  const [evaluationLoading, setEvaluationLoading] = useState<Record<string, boolean>>({});
  const [evaluationErrors, setEvaluationErrors] = useState<Record<string, string>>({});
  const [profile, setProfile] = useState<BackendProfile | null>(null);
  const [learnerState, setLearnerState] = useState<NormalizedLearnerState | null>(null);

  const loadGraphs = useCallback(async () => {
    setLoading(true); setError('');
    try { setGraphs(await graphApi.list()); }
    catch (e) { setError(e instanceof Error ? e.message : 'Load failed'); }
    finally { setLoading(false); }
  }, []);

  const loadTree = useCallback(async (id: number) => {
    try {
      const [tree, net] = await Promise.all([graphApi.courseTree(id), graphApi.knowledgeNetwork(id)]);
      setTreeData(tree); setNetworkData(net);
      if (tree.next_node) setExpandedNodes(prev => new Set([...prev, tree.next_node!.id]));
    } catch {}
  }, []);

  const loadFormal = useCallback(async (externalId: string) => {
    setFormalError('');
    try { setFormalGraph(await graphApi.getFormal(externalId)); }
    catch (e) { setFormalGraph(null); setFormalError(e instanceof Error ? e.message : '学习路线加载失败'); }
  }, []);

  const loadNodeEvaluation = useCallback(async (graphId: string, nodeId: string) => {
    setEvaluationLoading(prev => ({ ...prev, [nodeId]: true }));
    setEvaluationErrors(prev => ({ ...prev, [nodeId]: '' }));
    try {
      const result = await learningEvaluatorApi.latest(graphId, nodeId);
      setEvaluations(prev => ({ ...prev, [nodeId]: result }));
    } catch (e) {
      setEvaluationErrors(prev => ({ ...prev, [nodeId]: e instanceof Error ? e.message : '评估加载失败' }));
    } finally {
      setEvaluationLoading(prev => ({ ...prev, [nodeId]: false }));
    }
  }, []);

  useEffect(() => { loadGraphs(); }, [loadGraphs]);
  useEffect(() => {
    if (!user?.userId) return;
    getData<BackendProfile | null>(`/api/profile/${user.userId}`).then(setProfile).catch(() => setProfile(null));
    getNormalizedLearnerState(user.userId).then(setLearnerState).catch(() => setLearnerState(null));
  }, [user?.userId]);
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get('graph');
    if (!requested || !graphs.length) return;
    const matched = graphs.find(g => g.external_id === requested);
    if (matched && activeId !== matched.id) setActiveId(matched.id);
  }, [graphs, activeId]);
  useEffect(() => {
    if (activeId) {
      loadTree(activeId);
      const selected = graphs.find(g => g.id === activeId);
      if (selected?.external_id) loadFormal(selected.external_id); else setFormalGraph(null);
      return;
    }
    // On overview, load primary graph's tree for recommended section
    const primary = graphs.find(g => g.is_primary);
    if (primary) loadTree(primary.id);
  }, [activeId, graphs, loadTree, loadFormal]);
  useEffect(() => {
    if (!formalGraph) {
      setEvaluations({});
      setEvaluationErrors({});
      return;
    }
    formalGraph.nodes.forEach(node => { void loadNodeEvaluation(formalGraph.graph_id, node.node_id); });
  }, [formalGraph?.graph_id, formalGraph?.graph_revision, loadNodeEvaluation]);

  const enterGraph = (graph: GraphSummary) => {
    setActiveId(graph.id);
    if (graph.external_id) window.history.replaceState({}, '', `/learning?graph=${encodeURIComponent(graph.external_id)}`);
  };

  const doCreate = async () => {
    if (creatingGraph || !createTitle.trim()) return;
    const domain = createTitle.trim();
    const goal = createGoal.trim();
    if (!goal || goal.length < 4 || /^(学会|了解|入门|学习|掌握)$/.test(goal)) {
      message.warning('请填写更具体的学习目标，说明希望学习的范围和程度。'); return;
    }
    setCreatingGraph(true);
    let storageKey = '';
    try {
      const fingerprint = await operationFingerprint(JSON.stringify({ domain, goal, source_material_ids: [], update_mode: 'CREATE' }));
      storageKey = `${CREATE_IDENTITY_PREFIX}${fingerprint}`;
      const identity = getStoredOperationIdentity(storageKey, 'learning-create');
      message.loading('正在为你规划学习路线…', 1);
      const formal = await graphApi.orchestrate({
        request_id: identity,
        idempotency_key: identity,
        thread_id: identity,
        user_goal: goal,
        domain,
        source_material_ids: [],
        update_mode: 'CREATE',
      });
      clearStoredOperationIdentity(storageKey);
      message.success('学习计划已创建');
      setCreateOpen(false); setCreateTitle(''); setCreateGoal('');
      await loadGraphs();
      window.history.replaceState({}, '', `/learning?graph=${encodeURIComponent(formal.graph_id)}`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '创建失败';
      if (msg.includes('planning') || msg.includes('规划')) {
        message.error('暂时没能规划出学习路线，试试把学习目标写得更具体一些');
      } else {
        message.error(msg);
      }
      // A response means the server made a definitive decision; only uncertain network failures keep the replay identity.
      if (storageKey && e instanceof GraphApiError && e.status && e.status !== 409) {
        clearStoredOperationIdentity(storageKey);
      }
    } finally {
      setCreatingGraph(false);
    }
  };
  const doLazyGen = async (nid: number) => {
    if (!activeId || generatingResources) return;
    setGeneratingResources(true);
    try {
      await graphApi.generateNodeResources(activeId, nid);
      message.success('资源已生成');
      await loadTree(activeId);
    } catch (e) {
      message.error(String(e));
    } finally {
      setGeneratingResources(false);
    }
  };
  const doHide = async (nid: number) => { if (!activeId) return; try { await graphApi.hideNode(activeId, nid); await loadTree(activeId); } catch (e) { message.error(String(e)); } };
  const doShow = async (nid: number) => { if (!activeId) return; try { await graphApi.unhideNode(activeId, nid); await loadTree(activeId); } catch (e) { message.error(String(e)); } };
  const doDone = async (nid: number) => { if (!activeId) return; try { await graphApi.completeNode(activeId, nid, 85); message.success('已完成！'); await loadTree(activeId); } catch (e) { message.error(String(e)); } };
  const doGraphStatus = async (id: number, s: string) => { try { await graphApi.update(id, { status: s }); loadGraphs(); } catch (e) { message.error(String(e)); } };
  const doSetPrimary = async (id: number) => { try { await graphApi.setPrimary(id); loadGraphs(); } catch (e) { message.error(String(e)); } };
  const doGenRecs = async () => {
    if (!activeId || generatingResources) return;
    setGeneratingResources(true);
    try {
      await graphApi.generateRecommended(activeId);
      message.success('已生成推荐资源');
      await loadTree(activeId);
    } catch (e) {
      message.error(String(e));
    } finally {
      setGeneratingResources(false);
    }
  };
  const doFormalPlan = async (plan: FormalResourcePlan) => {
    if (!formalGraph || materializingPlan) return;
    const actionName = plan.status === 'failed' ? 'retry' : 'generate';
    const storageKey = `${RESOURCE_IDENTITY_PREFIX}${formalGraph.graph_id}:${plan.resource_plan_id}:${actionName}`;
    const executionKey = getStoredOperationIdentity(storageKey, `graph-${actionName}`);
    const threadId = `learning-${formalGraph.graph_id}`;
    setMaterializingPlan(plan.resource_plan_id);
    setFormalNotice('');
    setFormalNoticeTone('info');
    try {
      const action = plan.status === 'failed' ? graphApi.retryMaterialize : graphApi.materialize;
      await action(formalGraph.graph_id, plan.node_id, plan.resource_plan_id, threadId, executionKey);
      clearStoredOperationIdentity(storageKey);
      setFormalNotice('学习产物已生成');
      setFormalNoticeTone('success');
      await loadFormal(formalGraph.graph_id);
    } catch (e) {
      const messageText = e instanceof Error ? e.message : '产物生成失败';
      try {
        const refreshed = await graphApi.getFormal(formalGraph.graph_id);
        setFormalGraph(refreshed);
        const durablePlan = refreshed.resource_plans.find(item => item.resource_plan_id === plan.resource_plan_id);
        if (durablePlan?.status === 'generated' && durablePlan.artifact_id) {
          clearStoredOperationIdentity(storageKey);
          setFormalNotice('学习产物已恢复，可以打开了');
          setFormalNoticeTone('success');
          return;
        }
        if (durablePlan?.status === 'failed') clearStoredOperationIdentity(storageKey);
      } catch { /* retain the key when durable status cannot be confirmed */ }
      setFormalNotice(messageText);
      setFormalNoticeTone('error');
    } finally { setMaterializingPlan(''); }
  };
  const openFormalArtifact = async (artifactId: number) => {
    setOpenedArtifactId(artifactId);
  };
  const doEvaluateNode = async (nodeId: string) => {
    if (!formalGraph || evaluationLoading[nodeId]) return;
    const latest = evaluations[nodeId];
    const baseRevision = latest?.evaluation_revision || 0;
    const evidenceCount = latest?.evidence_summaries?.length || 0;
    const operationShape = JSON.stringify({
      graph_id: formalGraph.graph_id, node_id: nodeId,
      graph_revision: formalGraph.graph_revision, base_revision: baseRevision, evidence_count: evidenceCount,
    });
    const fingerprint = await operationFingerprint(operationShape);
    const storageKey = `${EVALUATION_IDENTITY_PREFIX}${fingerprint}`;
    const identity = getStoredOperationIdentity(storageKey, 'node-evaluation');
    setEvaluationLoading(prev => ({ ...prev, [nodeId]: true }));
    setEvaluationErrors(prev => ({ ...prev, [nodeId]: '' }));
    try {
      const result = await learningEvaluatorApi.evaluate(formalGraph.graph_id, nodeId, {
        request_id: identity, idempotency_key: identity, expected_evaluation_revision: baseRevision,
      });
      clearStoredOperationIdentity(storageKey);
      setEvaluations(prev => ({ ...prev, [nodeId]: result }));
      message.success(result.evidence_summaries.length ? '学习证据评估已更新' : '评估完成，但还没有足够的可信作答证据');
    } catch (e: any) {
      const code = e instanceof LearningEvaluatorApiError ? e.code : '';
      const text = code === 'EVALUATION_REVISION_CONFLICT'
        ? '评估状态已经更新，已重新加载最新结果'
        : code === 'IDEMPOTENCY_KEY_REUSE_CONFLICT'
          ? '这次评估请求与原请求不一致，请重新发起'
          : e instanceof Error ? e.message : '评估暂时不可用';
      setEvaluationErrors(prev => ({ ...prev, [nodeId]: text }));
      await loadNodeEvaluation(formalGraph.graph_id, nodeId);
    } finally {
      setEvaluationLoading(prev => ({ ...prev, [nodeId]: false }));
    }
  };

  const primaryGraph = graphs.find(g => g.is_primary);
  const visiblePathEvidence = normalizedPathEvidence(learnerState) || learningPathEvidence(profile);
  const activeGraph = graphs.find(g => g.id === activeId);
  const activeGraphs = graphs.filter(g => g.status !== 'archived');
  const archivedGraphs = graphs.filter(g => g.status === 'archived');

  if (loading) return <StateBlock state="loading" title="正在加载你的学习旅程..." />;
  if (error) return <StateBlock state="error" title="加载失败" description={error} onRetry={loadGraphs} />;

  // ===== LEVEL 1: OVERVIEW =====
  if (!activeId) {
    return (
      <div className={styles.page} data-testid="mylearning-overview">
        {/* Hero */}
        <section className={styles.overviewHero}>
          <div className={styles.heroContent}>
            <span>柿小吉为你整理</span>
            <h1>我的学习计划</h1>
            <p>{primaryGraph ? `目标：${primaryGraph.goal || primaryGraph.title}` : '告诉柿小吉一个目标，它会安排清晰的下一步。'}</p>
          </div>
          <Button type="primary" icon={<Plus size={16} />} onClick={() => setCreateOpen(true)}>添加学习目标</Button>
        </section>
        <section className={styles.learningNow} data-testid="learning-now-summary">
          <div><small>当前目标</small><strong>{learnerState?.current_goal || primaryGraph?.goal || '先确定一个具体学习目标'}</strong></div>
          <div><small>当前阶段</small><strong>{UNDERSTANDING_STAGE_LABEL[learnerState?.topic_mastery || learnerState?.knowledge_level || 'unknown'] || '理解基础'}</strong></div>
          <div><small>下一步</small><strong>{treeData?.next_node?.title || learnerState?.next_teaching_focus || '先和柿小吉确定学习重点'}</strong></div>
          <div><small>为什么</small><strong>{learnerState?.next_teaching_focus ? `根据你最近需要加强的「${learnerState.next_teaching_focus}」安排` : treeData?.next_node ? '沿着当前学习路线，为你安排最合适的一小步' : '开始一次学习后，柿小吉会根据结果持续调整'}</strong></div>
        </section>

        {/* Recommended: primary graph's next node */}
        {primaryGraph && treeData && treeData.next_node && <section className={styles.routeJourney} data-testid="learning-route-journey"><div className={styles.routeHeading}><span>当前学习路线</span><small>{primaryGraph.title}</small></div><div className={styles.routeNodes}>{treeData.nodes.slice(0, 6).map((node, index) => <button type="button" key={node.id} className={styles.routeNode} data-status={node.status} data-next={node.id === treeData.next_node?.id || undefined} onClick={() => enterGraph(primaryGraph)}><b>{node.status === 'completed' ? '✓' : index + 1}</b><span>{node.title}</span></button>)}</div><div className={styles.routeNext}><div><span>下一步</span><strong>{treeData.next_node.title}</strong><small>{primaryGraph.goal ? `为「${primaryGraph.goal}」继续这一小步` : '沿着你的学习路线继续前进'}</small></div><Button type="primary" onClick={() => enterGraph(primaryGraph)}>进入学习</Button></div></section>}
        {primaryGraph && treeData && treeData.next_node && (
          <section className={styles.overviewRec}>
            <div className={`${styles.recCard} ${styles.recCardWithArt}`}>
              <img className={styles.recArt} src={recArt} alt="" aria-hidden="true" />
              <div className={styles.recBadge}><Sparkles size={14} /> 推荐继续学习</div>
              <h3>{treeData.next_node.title}</h3>
              <p className={styles.recReason}>{primaryGraph.goal ? `目标：${primaryGraph.goal}` : '继续你的学习进度'}</p>
              {visiblePathEvidence && <div className={styles.pathEvidence} data-testid="learning-path-evidence">
                <strong>{visiblePathEvidence.headline}</strong>
                <span>{visiblePathEvidence.detail}</span>
                {visiblePathEvidence.cadence && <small>本轮节奏：{visiblePathEvidence.cadence}</small>}
              </div>}
              <div className={styles.recMeta}>
                <span><Clock3 size={13} /> ~{treeData.next_node.estimated_minutes}分钟</span>
                <span>难度 {treeData.next_node.difficulty}/5</span>
                <Tag color={STATUS_COLOR[treeData.next_node.status]}>{STATUS_LABEL[treeData.next_node.status]}</Tag>
              </div>
              <div className={styles.recActions}>
                <Button type="primary" onClick={() => enterGraph(primaryGraph)}>进入学习</Button>
                <Button onClick={() => enterGraph(primaryGraph)}>查看全部节点</Button>
              </div>
            </div>
          </section>
        )}

        {/* All Learning Plans */}
        <details className={styles.planManager}>
          <summary>管理其他学习计划</summary>
        <section className={styles.overviewPlans}>
          <h2>全部学习计划</h2>
          {activeGraphs.length === 0 ? (
            <div className={styles.emptyState}>
              <img className={styles.emptyArt} src={emptyPlanArt} alt="" aria-hidden="true" />
              <h3>你还没有正在进行的学习计划</h3>
              <p>告诉柿小吉你想学什么，它会为你规划一条只属于你的学习路线；也可以从一份资料开始。</p>
              <div className={styles.emptyStateActions}>
                <Button type="primary" onClick={() => navigate('/ai-learning')}>告诉柿小吉你想学什么</Button>
                <Button onClick={() => navigate('/resources')}>从资料开始学习</Button>
                <Button type="text" icon={<Plus size={14} />} onClick={() => setCreateOpen(true)}>手动新建计划</Button>
              </div>
            </div>
          ) : (
            <div className={styles.planGrid}>
              {activeGraphs.map(g => (
                <div key={g.id} className={`${styles.planCard} ${g.is_primary ? styles.primaryPlan : ''}`} onClick={() => enterGraph(g)}>
                  <div className={styles.planHeader}>
                    <strong>{g.title}</strong>
                    {g.is_primary && <Tag color="orange">主计划</Tag>}
                    <Tag color={g.status === 'active' ? 'green' : g.status === 'paused' ? 'orange' : 'default'}>{GRAPH_STATUS_LABEL[g.status] || '进行中'}</Tag>
                  </div>
                  {g.goal && <p className={styles.planGoal}>{g.goal}</p>}
                  <div className={styles.planProgress}>
                    <Progress percent={Math.round(g.progress_pct || 0)} strokeColor="#ff5a14" size="small" />
                    <span>{g.completed_nodes || 0}/{g.total_nodes || 0} 节点完成</span>
                  </div>
                  <div className={styles.planActions} onClick={e => e.stopPropagation()}>
                    {!g.is_primary && <Button size="small" onClick={() => doSetPrimary(g.id)}>设为主计划</Button>}
                    {g.status === 'active' ? <Button size="small" onClick={() => doGraphStatus(g.id, 'paused')}><Pause size={12} /> 暂停</Button>
                     : <Button size="small" onClick={() => doGraphStatus(g.id, 'active')}><Play size={12} /> 恢复</Button>}
                    <Button size="small" onClick={() => doGraphStatus(g.id, 'archived')}><Archive size={12} /> 归档</Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
        </details>

        {/* Archived */}
        {archivedGraphs.length > 0 && (
          <section className={styles.overviewArchived}>
            <h2>已归档</h2>
            <div className={styles.planGrid}>
              {archivedGraphs.map(g => (
                <div key={g.id} className={styles.planCard}>
                  <div className={styles.planHeader}><strong>{g.title}</strong><Tag>已归档</Tag></div>
                  <Button size="small" onClick={() => doGraphStatus(g.id, 'active')}>恢复</Button>
                </div>
              ))}
            </div>
          </section>
        )}

        <Modal title="新建学习计划" open={createOpen} onCancel={() => setCreateOpen(false)} width={520}
          footer={[<Button key="cancel" disabled={creatingGraph} onClick={() => setCreateOpen(false)}>取消</Button>, <Button key="create" type="primary" loading={creatingGraph} disabled={creatingGraph} onClick={doCreate}>创建并生成图谱</Button>]}>
          <div style={{display:'grid',gap:12}}>
            <div><label style={{fontWeight:600,fontSize:13,marginBottom:4,display:'block'}}>计划名称 *</label>
              <Input placeholder="如：玉石鉴赏、Python数据分析" value={createTitle} disabled={creatingGraph} onChange={e => setCreateTitle(e.target.value)} /></div>
            <div><label style={{fontWeight:600,fontSize:13,marginBottom:4,display:'block'}}>学习目标</label>
              <Input.TextArea placeholder="描述你想学什么、达到什么程度" value={createGoal} disabled={creatingGraph} onChange={e => setCreateGoal(e.target.value)} rows={2} /></div>
            <div style={{fontSize:12,color:'var(--muted)',padding:'8px 12px',background:'#faf9f7',borderRadius:8}}>
              提交后，柿小吉会根据你的目标规划 6-10 个学习节点，并为每个节点准备学习内容。
            </div>
          </div>
        </Modal>
      </div>
    );
  }

  // ===== LEVEL 2: GRAPH DETAIL =====
  const visibleNodes = treeData?.nodes?.filter((n: GraphNode) => !n.is_hidden) || [];
  const currentFormalNode = formalGraph?.nodes.find(node => node.title === treeData?.next_node?.title)
    || formalGraph?.nodes.find(node => node.status === 'learning')
    || formalGraph?.nodes.find(node => node.status === 'generated')
    || formalGraph?.nodes.find(node => node.status === 'planned')
    || formalGraph?.nodes[0]
    || null;
  const currentFormalPlans = currentFormalNode
    ? formalGraph?.resource_plans.filter(plan => plan.node_id === currentFormalNode.node_id) || []
    : [];
  const openTutorForCurrentTask = () => {
    if (!formalGraph || !currentFormalNode) {
      navigate('/ai-learning');
      return;
    }
    const context = {
      graph: {
        graph_id: formalGraph.graph_id,
        graph_title: formalGraph.graph_title,
        graph_revision: formalGraph.graph_revision,
      },
      node: {
        node_id: currentFormalNode.node_id,
        node_title: currentFormalNode.title,
      },
      source: 'learning-current-task',
    };
    try {
      sessionStorage.setItem('kaki_pending_learning_context_v1', JSON.stringify(context));
      sessionStorage.setItem('kaki_pending_tutor_entry_v1', JSON.stringify(context));
    } catch { /* navigation still works without optional session storage */ }
    navigate('/ai-learning?mode=tutor&from=learning');
  };
  const openTutorForFormalArtifact = (selection: ArtifactSelectionReference | null, artifact: ArtifactPublicProjection) => {
    const node = formalGraph?.nodes.find(item => item.node_id === artifact.graph_node_id)
      || currentFormalNode;
    const graphId = artifact.graph_id || formalGraph?.graph_id;
    if (!node || !graphId) {
      openTutorForCurrentTask();
      return;
    }
    const tutorEntry = {
      graph: {
        graph_id: graphId,
        graph_title: formalGraph?.graph_title || activeGraph?.title || '当前学习计划',
        graph_revision: artifact.graph_revision || formalGraph?.graph_revision,
      },
      node: { node_id: node.node_id, node_title: node.title },
      source: 'learning-formal-artifact',
    };
    try {
      sessionStorage.setItem('kaki_pending_tutor_entry_v1', JSON.stringify(tutorEntry));
      sessionStorage.setItem('kaki_pending_learning_context_v1', JSON.stringify(tutorEntry));
      sessionStorage.setItem('kaki_pending_tutor_artifact_context_v1', JSON.stringify({
        artifact: {
          ...artifact,
          version: artifact.version_number,
          node_id: artifact.graph_node_id,
          node_title: artifact.graph_node_title,
        },
        selection: selection ? { ...selection, selected_text: selection.selected_text.slice(0, 1200) } : null,
      }));
    } catch { /* the route remains available if session storage is unavailable */ }
    navigate('/ai-learning?mode=tutor&from=learning-artifact');
  };

  return (
    <div className={styles.page} data-testid="mylearning-detail">
      {/* Back + Title */}
      <section className={styles.detailTop}>
        <Button icon={<ArrowLeft size={16} />} onClick={() => { setActiveId(null); setFormalGraph(null); setViewMode('tree'); window.history.replaceState({}, '', '/learning'); }}>返回总览</Button>
        <div className={styles.detailInfo}>
          <h1><BookOpen size={20} />{activeGraph?.title}</h1>
          {activeGraph?.goal && <p><Target size={13} /> {activeGraph.goal}</p>}
          {activeGraph?.planning_rationale && <p className={styles.rationale}><Lightbulb size={13} /> {activeGraph.planning_rationale}</p>}
        </div>
        <div className={styles.detailActions}>
          {activeGraph?.status === 'active' ? <Button size="small" icon={<Pause size={14} />} onClick={() => doGraphStatus(activeGraph.id, 'paused')}>暂停</Button>
           : <Button size="small" icon={<Play size={14} />} onClick={() => doGraphStatus(activeGraph!.id, 'active')}>恢复</Button>}
        </div>
      </section>

      {/* 当前学到哪儿了：单元 + 总进度 */}
      {treeData && (() => {
        const currentUnit = formalGraph?.learning_units
          .slice().sort((a, b) => a.unit_order - b.unit_order)
          .find(unit => unit.node_ids.some(id => id === currentFormalNode?.node_id))
          || formalGraph?.learning_units[0];
        return (
          <section className={styles.whereBanner} data-testid="learning-where-banner">
            <div className={styles.whereBannerUnit}>
              <small>第 {currentUnit ? currentUnit.unit_order + 1 : 1} 单元 · {currentUnit?.title || activeGraph?.title || '当前课程'}</small>
              <strong>{activeGraph?.goal || currentFormalNode?.title || '当前学习路线'}</strong>
            </div>
            <div className={styles.whereBannerProgress}>
              <Progress percent={Math.round(treeData.progress_pct || 0)} strokeColor="#ff5a14" size="small" />
              <span>当前进度 {((treeData.status_counts as any)?.completed || 0)} / {treeData.total_nodes}</span>
            </div>
          </section>
        );
      })()}

      {/* 节点时间线：每个节点一行，状态徽章 + 说明 + 推荐资源 + 一个主按钮 */}
      {formalGraph && (
        <section className={styles.nodeTimeline} data-testid="learning-node-timeline">
          {formalGraph.learning_units.slice().sort((a, b) => a.unit_order - b.unit_order).map(unit => (
            <div key={unit.unit_id} className={styles.nodeTimelineUnit}>
              <header className={styles.nodeTimelineUnitHead}>
                <span>第 {unit.unit_order + 1} 单元</span>
                <strong>{unit.title}</strong>
              </header>
              {unit.node_ids.map((nodeId, index) => {
                const node = formalGraph.nodes.find(item => item.node_id === nodeId);
                if (!node) return null;
                const plans = formalGraph.resource_plans.filter(plan => plan.node_id === node.node_id);
                const incomingEdges = formalGraph.edges.filter(edge => edge.target_node_id === node.node_id);
                const isNext = currentFormalNode?.node_id === node.node_id;
                const isCompleted = node.status === 'completed';
                const readyPlan = plans.find(plan => plan.status === 'generated' && plan.artifact_id);
                const pendingPlan = plans.find(plan => plan.status === 'planned' || plan.status === 'failed');
                const preferred = learnerState?.preferred_modalities?.[0];
                const recAction = RECOMMEND_ACTION[preferred || ''];
                const recText = readyPlan
                  ? (RESOURCE_ACTION_LABEL[readyPlan.resource_type] || '查看学习内容')
                  : recAction
                    ? `先${recAction}`
                    : '学习内容';
                const primaryLabel = isCompleted
                  ? (readyPlan ? '查看学习资源' : '回顾内容')
                  : isNext
                    ? (readyPlan ? '开始学习' : '开始学习')
                    : (readyPlan ? '查看学习资源' : '准备学习资料');
                const openPrimary = () => {
                  if (readyPlan?.artifact_id) { void openFormalArtifact(readyPlan.artifact_id); return; }
                  if (isCompleted) { openTutorForCurrentTask(); return; }
                  if (pendingPlan) { void doFormalPlan(pendingPlan); return; }
                  openTutorForCurrentTask();
                };
                const badgeLabel = isCompleted ? '已完成' : isNext ? '下一步' : (STATUS_LABEL[node.status] || '未开始');
                const badgeTone = isCompleted ? 'done' : isNext ? 'next' : 'pending';
                return (
                  <div key={node.node_id} className={styles.nodeTimelineItem} data-node-id={node.node_id} data-status={badgeTone}>
                    <div className={styles.nodeTimelineConnector}>
                      <span className={styles.nodeTimelineDot} data-status={badgeTone}>{isCompleted ? '✓' : index + 1}</span>
                      <span className={styles.nodeTimelineLine} />
                    </div>
                    <div className={styles.nodeCardV2}>
                      <div className={styles.nodeCardV2Head}>
                        <strong>{node.title}</strong>
                        <span className={styles.nodeBadgeV2} data-status={badgeTone}>{badgeLabel}</span>
                      </div>
                      <p className={styles.nodeCardV2Desc}>{node.summary} · 约 {node.estimated_effort_minutes} 分钟</p>
                      <div className={styles.nodeCardV2Divider} />
                      <div className={styles.nodeCardV2Foot}>
                        <span className={styles.nodeCardV2Rec}>推荐：<strong>{recText}</strong></span>
                        <Button type="primary" className={styles.nodeCardV2Action}
                          loading={materializingPlan === pendingPlan?.resource_plan_id}
                          disabled={Boolean(materializingPlan && materializingPlan !== pendingPlan?.resource_plan_id)}
                          onClick={openPrimary}>{primaryLabel}</Button>
                      </div>
                      <div className={styles.detailToggle}>
                        <button type="button" aria-expanded={Boolean(detailOpen[node.node_id])} onClick={() => setDetailOpen(prev => ({ ...prev, [node.node_id]: !prev[node.node_id] }))}>
                          <ChevronRight size={13} className={detailOpen[node.node_id] ? styles.toggleOpen : ''} /> 查看详情
                        </button>
                        <button type="button" aria-expanded={Boolean(judgmentOpen[node.node_id])} onClick={() => setJudgmentOpen(prev => ({ ...prev, [node.node_id]: !prev[node.node_id] }))}>
                          <ChevronRight size={13} className={judgmentOpen[node.node_id] ? styles.toggleOpen : ''} /> 为什么这样判断
                        </button>
                      </div>
                      {detailOpen[node.node_id] && (
                        <div className={styles.detailPanel} data-testid={`node-detail-${node.node_id}`}>
                          {node.learning_objectives.length > 0 && <div className={styles.detailPanelSection}><b>学习目标</b>{node.learning_objectives.map(value => <span key={value}>{value}</span>)}</div>}
                          {node.mastery_evidence.length > 0 && <div className={styles.detailPanelSection}><b>完成条件</b>{node.mastery_evidence.map(value => <span key={value}>{value}</span>)}</div>}
                          {incomingEdges.length > 0 && <div className={styles.detailPanelSection}><b>前置要求</b>{incomingEdges.map(edge => {
                            const source = formalGraph.nodes.find(item => item.node_id === edge.source_node_id);
                            return <span key={edge.edge_id}>{edge.edge_type !== 'prerequisite' && EDGE_TYPE_LABEL[edge.edge_type] ? `${EDGE_TYPE_LABEL[edge.edge_type]}：` : ''}{source?.title || '另一个节点'}{edge.reason ? ` · ${edge.reason}` : ''}</span>;
                          })}</div>}
                          {plans.length > 0 && <div className={styles.detailPanelSection}><b>学习资源</b>{plans.map(plan => (
                            <span key={plan.resource_plan_id}>
                              {RESOURCE_ACTION_LABEL[plan.resource_type] || RESOURCE_TYPE_LABEL[plan.resource_type] || '学习内容'}
                              {plan.status === 'generated' && plan.artifact_id ? ' · 已就绪' : plan.status === 'generating' ? ' · 正在准备' : plan.status === 'failed' ? ' · 准备失败，可重试' : ' · 尚未准备'}
                            </span>
                          ))}</div>}
                        </div>
                      )}
                      {judgmentOpen[node.node_id] && (() => {
                        const result = evaluations[node.node_id];
                        const assessment = evaluationSummary(result, node.node_id);
                        const isLoading = Boolean(evaluationLoading[node.node_id]);
                        const evalError = evaluationErrors[node.node_id];
                        return (
                          <div className={styles.detailPanel} data-testid={`node-judgment-${node.node_id}`}>
                            <div className={styles.judgmentHeader}>
                              <span>学习效果评估 · 依据真实作答记录，不会直接把节点标记为完成</span>
                              <Button size="small" type={result ? 'default' : 'primary'} loading={isLoading} disabled={isLoading}
                                onClick={() => doEvaluateNode(node.node_id)}>
                                {result ? '根据最新证据重新评估' : '评估当前学习证据'}
                              </Button>
                            </div>
                            {isLoading && !result && <div className={styles.evaluationState}>评估中：正在核对作答证据并生成结果…</div>}
                            {evalError && <div className={styles.evaluationError}>{evalError}</div>}
                            {!isLoading && !result && !evalError && <div className={styles.evaluationState}>尚无评估。完成互动题后，可在这里查看基于证据的掌握情况。</div>}
                            {result && assessment && (
                              <div className={styles.evaluationBody}>
                                <div className={styles.evaluationOverview}>
                                  <div><small>评估状态</small><strong>{EVALUATION_STATUS_LABEL[assessment.assessment_status] || '待评估'}</strong></div>
                                  <div><small>证据数量</small><strong>{result.evidence_summaries.length} 条</strong></div>
                                  <div><small>证据充分度</small><strong>{Math.round(assessment.evidence_sufficiency * 100)}%</strong></div>
                                  <div><small>置信程度</small><strong>{EVALUATION_BAND_LABEL[assessment.confidence >= .75 ? 'HIGH' : assessment.confidence >= .4 ? 'MEDIUM' : assessment.confidence > 0 ? 'LOW' : 'NO_EVIDENCE']}</strong></div>
                                </div>
                                <div className={styles.evaluationNarrative}>
                                  {assessment.objective_coverage.map((objective, index) => (
                                    <div key={objective.objective_id}>
                                      <span className={objective.achieved ? styles.evaluationGood : styles.evaluationPending}>{objective.achieved ? '已形成证据' : '仍需验证'}</span>
                                      <p>{node.learning_objectives[index] || objective.public_label}</p>
                                      <small>{objective.evidence_count} 条支持证据 · 掌握程度 {EVALUATION_BAND_LABEL[objective.achievement_score_band] || '待评估'}</small>
                                    </div>
                                  ))}
                                </div>
                                {assessment.misconceptions.filter(item => !item.resolved).length > 0 && <div className={styles.evaluationCallout} data-tone="warning"><b>发现可能的误区</b>{assessment.misconceptions.filter(item => !item.resolved).map(item => <span key={item.code}>{item.summary}</span>)}</div>}
                                {assessment.evidence_gaps.length > 0 && <div className={styles.evaluationCallout}><b>还缺少的证据</b>{Array.from(new Set(assessment.evidence_gaps)).map(code => <span key={code}>{formatEvaluationCode(code)}</span>)}</div>}
                                {assessment.recommended_evaluation_actions.length > 0 && <div className={styles.evaluationCallout}><b>下一步建议</b>{assessment.recommended_evaluation_actions.map((action, index) => <span key={`${action.action_type}-${index}`}>{EVALUATION_ACTION_LABEL[action.action_type] || formatEvaluationCode(action.action_type)}</span>)}</div>}
                                <div className={styles.evaluationFooter}>
                                  <span>{assessment.completion_eligibility_signal === 'ELIGIBLE' ? '证据已达到完成资格建议' : '暂不建议判定为完成'}</span>
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </section>
      )}

      {/* 为什么这样安排：一句自然语言讲清动态路径 */}
      {formalGraph && (() => {
        const completedTitles = formalGraph.nodes.filter(n => n.status === 'completed').map(n => n.title);
        const pathText = currentFormalNode
          ? (completedTitles.length
              ? `你已经完成了${completedTitles.slice(0, 2).join('、')}，接下来通过「${currentFormalNode.title}」确认是否真正掌握；如果测评仍有薄弱点，系统会自动回到对应知识点强化。`
              : `接下来学习「${currentFormalNode.title}」，完成它会帮你确认这一阶段的掌握情况；如果测评仍有薄弱点，系统会自动回到对应知识点强化。`)
          : (activeGraph?.planning_rationale || '学习路线会根据你的真实学习证据持续调整。');
        return (
          <section className={styles.whyFooter} data-testid="learning-why-footer">
            <strong><Lightbulb size={13} /> 为什么这样安排</strong>
            <span>{pathText}</span>
          </section>
        );
      })()}

      {formalError && <StateBlock state="error" title="学习路线加载失败" description={formalError} onRetry={() => activeGraph?.external_id && loadFormal(activeGraph.external_id)} />}

      {/* Recommended node */}
      {!formalGraph && treeData?.next_node && (
        <section className={styles.nextNode}>
          <div className={styles.nextNodeCard}>
            <div className={styles.nextNodeBadge}><Sparkles size={14} /> 推荐现在学习</div>
            <h3>{treeData.next_node.title}</h3>
            <p>{treeData.next_node.description || '开始学习这个主题。'}</p>
            <div className={styles.nextNodeMeta}>
              <span><Clock3 size={13} /> ~{treeData.next_node.estimated_minutes}分钟</span>
              <span>难度 {treeData.next_node.difficulty}/5</span>
              <Tag color={STATUS_COLOR[treeData.next_node.status]}>{STATUS_LABEL[treeData.next_node.status]}</Tag>
            </div>
            <div className={styles.nextNodeActions}>
              {treeData.next_node.status === 'planned' && <Button type="primary" loading={generatingResources} icon={<Sparkles size={14} />} onClick={() => doLazyGen(treeData.next_node!.id)}>生成讲义与练习</Button>}
              {(treeData.next_node.status === 'generated' || treeData.next_node.status === 'learning') && <Button type="primary" icon={<Play size={14} />}>开始学习</Button>}
              {(treeData.next_node.resources || []).filter((r: any) => r.artifact_id).length > 0 && <span className={styles.resourceHint}>{(treeData.next_node.resources || []).filter((r: any) => r.artifact_id).length} 个资源已就绪</span>}
            </div>
          </div>
        </section>
      )}

      {/* View toggle */}
      {!formalGraph && <section className={styles.viewToggle}>
        <Button type={viewMode === 'tree' ? 'primary' : 'default'} size="small" icon={<Layers size={14} />} onClick={() => setViewMode('tree')}>课程树</Button>
        <Button type={viewMode === 'network' ? 'primary' : 'default'} size="small" icon={<Network size={14} />} onClick={() => setViewMode('network')}>知识网络</Button>
        <Button size="small" icon={<RefreshCw size={14} />} onClick={() => activeId && loadTree(activeId)}>刷新</Button>
        {!treeData?.next_node && treeData && treeData.nodes.some((n: GraphNode) => n.status === 'planned') && <Button size="small" type="primary" loading={generatingResources} icon={<Sparkles size={14} />} onClick={doGenRecs}>生成推荐</Button>}
      </section>}

      {/* Course Tree */}
      {!formalGraph && viewMode === 'tree' && (
        <section className={styles.treeView}>
          {visibleNodes.length === 0 ? <StateBlock state="empty" title="柿小吉正在准备学习节点" description="节点生成后会出现在这里，稍等片刻刷新看看。" onRetry={() => activeId && loadTree(activeId)} />
           : (
            <div className={styles.courseTree}>
              {visibleNodes.map((node: GraphNode, idx: number) => {
                const isExp = expandedNodes.has(node.id);
                const isSel = selectedNode?.id === node.id;
                const isNext = treeData?.next_node?.id === node.id;
                return (
                  <div key={node.id} className={`${styles.treeNode} ${isNext ? styles.nextHighlight : ''}`} data-status={node.status}>
                    {idx < visibleNodes.length - 1 && <div className={styles.connector} />}
                    <div className={styles.nodeDot} style={{background: STATUS_COLOR[node.status] || '#bfb8b2'}} onClick={() => setSelectedNode(isSel ? null : node)}>
                      {node.status === 'completed' ? <CheckCircle2 size={12} color="#fff" /> : node.status === 'learning' ? <Play size={10} color="#fff" /> : <span>{idx + 1}</span>}
                    </div>
                    <div className={`${styles.nodeCard} ${isSel ? styles.selected : ''}`}
                      onClick={() => { setSelectedNode(isSel ? null : node); setExpandedNodes(prev => { const n = new Set(prev); isExp ? n.delete(node.id) : n.add(node.id); return n; }); }}>
                      <div className={styles.nodeHeader}>
                        <strong>{node.title}</strong>
                        <div className={styles.nodeBadges}><Tag color={STATUS_COLOR[node.status]}>{STATUS_LABEL[node.status]}</Tag>{node.mastery_score > 0 && <Tag>{Math.round(node.mastery_score)}%</Tag>}</div>
                        <ChevronRight size={14} className={`${styles.expandIcon} ${isExp ? styles.rotated : ''}`} />
                      </div>
                      {isExp && (
                        <div className={styles.nodeExpand}>
                          <div className={styles.nodeMeta}><span><Clock3 size={11} /> {node.estimated_minutes}分钟</span><span>难度{node.difficulty}</span><span>{TYPE_LABEL[node.knowledge_type] || '学习内容'}</span></div>
                          {(node.resources || []).length > 0 && (
                            <div className={styles.resourceList}>
                              {(node.resources || []).map((r: any, ri: number) => { const Icon = TYPE_ICONS[r.type] || FileText;
                                return <div key={ri} className={styles.resourceItem}><Icon size={13} /><span>{r.title}</span>{r.status === 'generated' && r.artifact_id ? <Tag color="green">已就绪</Tag> : r.status === 'failed' ? <Tag color="red">失败</Tag> : <Tag>{STATUS_LABEL[r.status] || '待生成'}</Tag>}</div>;
                              })}
                            </div>
                          )}
                          <div className={styles.nodeActions}>
                            {node.status === 'planned' && <Button size="small" type="primary" loading={generatingResources} icon={<Sparkles size={11} />} onClick={e => { e.stopPropagation(); doLazyGen(node.id); }}>生成讲义与练习</Button>}
                            {node.status !== 'completed' && <Button size="small" icon={<CheckCircle2 size={11} />} onClick={e => { e.stopPropagation(); doDone(node.id); }}>完成</Button>}
                            {node.is_hidden ? <Button size="small" icon={<Eye size={11} />} onClick={e => { e.stopPropagation(); doShow(node.id); }}>显示</Button> : <Button size="small" icon={<EyeOff size={11} />} onClick={e => { e.stopPropagation(); doHide(node.id); }}>隐藏</Button>}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* Network */}
      {!formalGraph && viewMode === 'network' && networkData && (
        <section className={styles.networkView}>
          <div className={styles.networkControls}>
            <Button size="small" icon={<ZoomIn size={14} />} onClick={() => setNetworkScale(s => Math.min(2, s + 0.2))} />
            <Button size="small" icon={<ZoomOut size={14} />} onClick={() => setNetworkScale(s => Math.max(0.3, s - 0.2))} />
            <Button size="small" onClick={() => { setNetworkScale(1); setNetworkPan({ x: 0, y: 0 }); }}>重置</Button>
            <span>{Math.round(networkScale * 100)}%</span>
          </div>
          <svg className={styles.networkSvg} viewBox="-300 -300 600 600"
            onMouseDown={e => { setDragging(true); setDragStart({ x: e.clientX - networkPan.x, y: e.clientY - networkPan.y }); }}
            onMouseMove={e => { if (dragging) setNetworkPan({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y }); }}
            onMouseUp={() => setDragging(false)} onMouseLeave={() => setDragging(false)}
            style={{ cursor: dragging ? 'grabbing' : 'grab' }}>
            <g transform={`translate(${networkPan.x},${networkPan.y}) scale(${networkScale})`}>
              {(networkData.edges || []).map((edge: any, i: number) => { const src = networkData.nodes.find((n: any) => n.id === edge.source); const tgt = networkData.nodes.find((n: any) => n.id === edge.target); if (!src || !tgt) return null; return <line key={i} x1={src.x} y1={src.y} x2={tgt.x} y2={tgt.y} stroke="#d4cfc9" strokeWidth={1.5} />; })}
              {(networkData.nodes || []).map((node: any) => (
                <g key={node.id} transform={`translate(${node.x},${node.y})`} style={{cursor:'pointer'}} onClick={() => { const f = treeData?.nodes?.find((n: any) => n.id === node.id); if (f) setSelectedNode(f); }}>
                  <circle r={node.size / 2} fill={STATUS_COLOR[node.status] || '#bfb8b2'} stroke="#fff" strokeWidth={2} opacity={0.9} />
                  <text textAnchor="middle" dy="0.35em" fontSize={Math.max(8, node.size / 4)} fill="#fff" fontWeight={600}>{node.title.slice(0, 6)}</text>
                  <title>{node.title}</title>
                </g>
              ))}
            </g>
          </svg>
        </section>
      )}

      <Modal title="新建学习计划" open={createOpen} onCancel={() => setCreateOpen(false)}
        footer={[<Button key="cancel" disabled={creatingGraph} onClick={() => setCreateOpen(false)}>取消</Button>, <Button key="create" type="primary" loading={creatingGraph} disabled={creatingGraph} onClick={doCreate}>创建</Button>]}>
        <Input placeholder="计划名称" value={createTitle} disabled={creatingGraph} onChange={e => setCreateTitle(e.target.value)} style={{marginBottom:12}} />
        <Input.TextArea placeholder="学习目标（可选）" value={createGoal} disabled={creatingGraph} onChange={e => setCreateGoal(e.target.value)} rows={3} />
      </Modal>
      <Modal title="学习资源" open={openedArtifactId !== null} onCancel={() => setOpenedArtifactId(null)} footer={null} width="min(1180px, 96vw)" destroyOnHidden styles={{ body: { height: 'min(760px, 82vh)', padding: 0 } }}>
        {openedArtifactId !== null && <ArtifactShell artifactId={openedArtifactId} context={{ mode: 'learning' }} onClose={() => setOpenedArtifactId(null)} onAskAi={openTutorForFormalArtifact} />}
      </Modal>
    </div>
  );
}
