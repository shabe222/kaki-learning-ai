import { useEffect, useMemo, useRef, useState } from 'react';
import { Braces, Compass, FileText, Lightbulb, ListChecks, Map, Route, Sparkles, Target, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../../stores/authStore';
import { getData } from '../../../api/client';
import { SafeMarkdown } from '../../../components/SafeMarkdown';
import type { DaryaCognitionOrchestration, DaryaLearningLoopSnapshot, DaryaLongTermMemory, DaryaTeachingStageProjection, TideMessage, TidePerspectiveFieldName, TideUserContextClaim, TideUserContextStatus } from '../tideTypes';
import { projectTideMoment } from '../content/tideContentProjection';
import { tideChatApi } from '../chat/tideChatApi';
import { DARYA_LEARNING_ROUTE_UPDATED } from '../learningRouteEvents';
import { useTideStore } from '../state/tideStore';
import { TideArtifactWorkbench } from '../workbench/TideArtifactWorkbenchView';
import { TideLearningFitView } from './TideLearningFitView';
import { TideTeachingStageCard, type TeachingStageAction } from './TideTeachingStageCard';
import { DaryaSupportWorkbench } from './DaryaSupportWorkbench';
import styles from './TideContextRail.module.css';
import './tideContextRail-wave01.css';

const kindIcon = {
  'action-plan': ListChecks,
  'learning-map': Map,
  'exploration-map': Compass,
  explanation: Lightbulb,
  checklist: ListChecks,
  draft: FileText,
  'resource-map': Map,
  workflow: Route,
  code: Braces,
  'working-material': FileText,
  'learning-fit': Target,
};

const kindLabel = {
  'action-plan': '行动结构',
  'learning-map': '学习地图',
  'exploration-map': '探索框架',
  explanation: '解释结构',
  checklist: '检查清单',
  draft: '可编辑草稿',
  'resource-map': '资源组织',
  workflow: '工作路径',
  code: '代码与说明',
  'working-material': '工作材料',
  'learning-fit': '学习适配',
};

const GOAL_KIND_LABEL: Record<string, string> = {
  teaching: '教学',
  life_guidance: '引导',
  task: '任务',
};

const perspectiveLabels: Partial<Record<TidePerspectiveFieldName, string>> = {
  goal: '当前目标',
  stage: '所处阶段',
  current_artifacts: '当前工作对象',
  constraints: '当前约束',
  confirmed_decisions: '已经确定',
  unfinished_items: '尚未完成',
  next_action: '接下来',
};

const perspectiveOrder: TidePerspectiveFieldName[] = [
  'goal',
  'parallel_goals',
  'stage',
  'current_artifacts',
  'constraints',
  'confirmed_decisions',
  'unfinished_items',
  'next_action',
];

const workspaceStatusLabel = {
  exploring: '探索中',
  planning: '规划中',
  creating: '推进中',
  reviewing: '审阅中',
  completed: '已完成',
};

const artifactActionStatusLabel = {
  analyzing: '分析中',
  proposing: '等待确认',
  creating: '创建新版本',
  completed: '已完成',
  conflicted: '提案已失效',
  blocked: '质量门未放行',
  failed: '未完成',
  cancelled: '已暂缓',
};

const userContextStatusLabel: Record<TideUserContextStatus, string> = {
  USER_PROVIDED: '文档提供',
  INFERRED: 'DARYA 推断',
  CONFIRMED: '你已确认',
  REJECTED: '你已排除',
};

const userContextKindLabel: Record<string, string> = {
  education: '教育背景',
  project: '项目经历',
  achievement: '成果与奖项',
  experience: '实践经历',
  skill: '技能',
  goal: '目标',
  constraint: '约束',
  preference: '偏好',
  other: '其他背景',
};

type ObjectEditField = 'title' | 'purpose' | 'focus';

function displayPerspectiveValue(fieldName: TidePerspectiveFieldName, value: unknown) {
  if (fieldName === 'current_artifacts' && Array.isArray(value)) {
    return value
      .map((item) => item && typeof item === 'object' && 'title' in item
        ? String((item as { title?: unknown }).title || '未命名产物')
        : '')
      .filter(Boolean)
      .join('、');
  }
  if (Array.isArray(value)) return value.map(String).join('；');
  return typeof value === 'string' ? value : String(value ?? '');
}

function userContextProvenance(claim: TideUserContextClaim) {
  const reference = claim.source_reference;
  const fileName = typeof reference.document_name === 'string' ? reference.document_name : '已上传文档';
  const pageNumber = typeof reference.page_start === 'number' ? reference.page_start : null;
  const page = pageNumber ? `第 ${pageNumber} 页` : '';
  const section = typeof reference.section === 'string' ? reference.section : '';
  return [fileName, page, section].filter(Boolean).join(' · ');
}

const memoryTypeLabel = {
  FACT: '事实',
  PREFERENCE: '偏好',
  LESSON: '经验',
  FUTURE_POLICY: '以后这样做',
};

function memoryProvenance(memory: DaryaLongTermMemory) {
  const sourceType = memory.provenance.source_type;
  if (sourceType === 'PHASE_2_8_CONTEXT_CLAIM') {
    const fileName = typeof memory.provenance.document_name === 'string'
      ? memory.provenance.document_name
      : '你确认过的材料背景';
    return `${fileName} · 你已确认，不等于外部核验`;
  }
  if (sourceType === 'USER_CORRECTION') return '由你修正 · 不等于外部核验';
  if (sourceType === 'AUTO_OBSERVED_USER_STATEMENT') return '你自然提到 · 尚未确认，也不等于外部核验';
  return '你明确让我记住 · 不等于外部核验';
}

function currentSupportPlan(messages: Array<{ role: string; metadata?: { cognition_orchestration?: DaryaCognitionOrchestration | null } | null; cognition_orchestration?: DaryaCognitionOrchestration | null }>) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const plan = message.role === 'assistant'
      ? (message.metadata?.cognition_orchestration || message.cognition_orchestration)
      : null;
    if (!plan || plan.source !== 'structured_model') continue;
    if (!['TASK', 'LEARNING'].includes(plan.turn_mode)) return null;
    if (plan.goal_lifecycle && plan.goal_lifecycle !== 'ACTIVE') return null;
    if (plan.work_brief?.goal || plan.help_opportunity?.true_goal) return plan;
  }
  return null;
}

function currentGeneralTeachingStage(messages: TideMessage[]): DaryaTeachingStageProjection | null {
  const latest = [...messages].reverse().find((message) => message.role === 'assistant' && message.status === 'complete' &&
    (message.metadata?.cognition_orchestration || message.cognition_orchestration)?.source === 'structured_model');
  if (!latest) return null;
  const plan = latest.metadata?.cognition_orchestration || latest.cognition_orchestration;
  if (!plan || !['LEARNING', 'PAUSED', 'STOPPED'].includes(plan.turn_mode)) return null;
  if (latest.metadata?.learning_loop_domain_id) return null;
  const status = plan.turn_mode === 'PAUSED' || plan.goal_lifecycle === 'PAUSED' ? 'PAUSED'
    : plan.turn_mode === 'STOPPED' || plan.goal_lifecycle === 'COMPLETED' ? 'ENDED' : 'ACTIVE';
  const source = status === 'ACTIVE' ? latest : [...messages].reverse().find((message) =>
    message.role === 'assistant' && message.status === 'complete' &&
    !message.metadata?.learning_loop_domain_id &&
    (message.metadata?.cognition_orchestration || message.cognition_orchestration)?.turn_mode === 'LEARNING' &&
    (message.metadata?.cognition_orchestration || message.cognition_orchestration)?.goal_lifecycle === 'ACTIVE') || latest;
  const sourcePlan = source?.metadata?.cognition_orchestration || source?.cognition_orchestration;
  if (!sourcePlan) return null;
  const topic = sourcePlan.work_brief.goal || sourcePlan.help_opportunity.true_goal;
  if (!topic) return null;
  const strategy = sourcePlan.work_brief.strategy?.trim() || '';
  const method = typeof source.metadata?.teaching_method_label === 'string'
    ? source.metadata.teaching_method_label
    : /纠错|判据错误|误区|判断都错|错在/.test(strategy) ? '纠错引导'
    : /练习|作答|独立判断/.test(strategy) ? '练习检验'
    : /例子|示例|示范/.test(strategy) ? '例题示范'
    : /分步|逐步/.test(strategy) ? '分步讲解'
    : '对话引导';
  const summary = {
    topic,
    observed: '本段有对话记录；尚无可据此宣布掌握的独立评估。',
    evidence_count: 0,
    remaining_gap: plan.work_brief.unknowns[0] || sourcePlan.work_brief.unknowns[0] || '仍需在新情境下独立作答。',
    next_step: sourcePlan.help_opportunity.best_immediate_deliverable || sourcePlan.work_brief.success_checks[0] || '从当前问题继续。',
    mastery_conclusion: status === 'PAUSED' ? '进度已保留；暂停不代表掌握。' : '本段已收束；掌握情况仍需独立作答或正式评估确认。',
  };
  return {
    scope: 'general', status, method,
    method_reason: strategy || sourcePlan.work_brief.stage || '按这一轮的学习目标安排下一步。',
    progress: summary, summary: status === 'ACTIVE' ? null : summary,
    revision: messages.length,
  };
}

export function TideContextRail({ onOpenUnderstanding, onOpenArtifact, onCollapse }: { onOpenUnderstanding?: () => void; onOpenArtifact?: (artifactId: number) => void; onCollapse?: () => void } = {}) {
  const navigate = useNavigate();
  const railRef = useRef<HTMLDivElement>(null);
  const messages = useTideStore((state) => state.messages);
  const send = useTideStore((state) => state.send);
  const streamState = useTideStore((state) => state.streamState);
  const sendInFlight = useTideStore((state) => state.sendInFlight);
  const currentThreadId = useTideStore((state) => state.currentThreadId);
  const sharedPerspective = useTideStore((state) => state.sharedPerspective);
  const perspectiveState = useTideStore((state) => state.perspectiveState);
  const perspectiveError = useTideStore((state) => state.perspectiveError);
  const controlPerspective = useTideStore((state) => state.controlSharedPerspective);
  const workspaceObject = useTideStore((state) => state.workspaceObject);
  const workspaceObjectState = useTideStore((state) => state.workspaceObjectState);
  const workspaceObjectError = useTideStore((state) => state.workspaceObjectError);
  const controlWorkspaceObject = useTideStore((state) => state.controlWorkspaceObject);
  const artifactAction = useTideStore((state) => state.artifactAction);
  const artifactActionState = useTideStore((state) => state.artifactActionState);
  const artifactActionError = useTideStore((state) => state.artifactActionError);
  const controlArtifactAction = useTideStore((state) => state.controlArtifactAction);
  const artifactWorkbench = useTideStore((state) => state.artifactWorkbench);
  const artifactWorkbenchState = useTideStore((state) => state.artifactWorkbenchState);
  const artifactWorkbenchError = useTideStore((state) => state.artifactWorkbenchError);
  const userContext = useTideStore((state) => state.userContext);
  const userContextState = useTideStore((state) => state.userContextState);
  const userContextError = useTideStore((state) => state.userContextError);
  const controlUserContextClaim = useTideStore((state) => state.controlUserContextClaim);
  const longTermMemory = useTideStore((state) => state.longTermMemory);
  const longTermMemoryState = useTideStore((state) => state.longTermMemoryState);
  const longTermMemoryError = useTideStore((state) => state.longTermMemoryError);
  const promoteContextClaimToMemory = useTideStore((state) => state.promoteContextClaimToMemory);
  const controlLongTermMemory = useTideStore((state) => state.controlLongTermMemory);
  const opportunity = useTideStore((state) => state.opportunity);
  const opportunityState = useTideStore((state) => state.opportunityState);
  const opportunityError = useTideStore((state) => state.opportunityError);
  const openOpportunity = useTideStore((state) => state.openOpportunity);
  const laterOpportunity = useTideStore((state) => state.laterOpportunity);
  const dismissOpportunity = useTideStore((state) => state.dismissOpportunity);
  const moment = useMemo(() => projectTideMoment(messages), [messages]);
  const [dismissedId, setDismissedId] = useState<string | null>(null);
  const [editingField, setEditingField] = useState<TidePerspectiveFieldName | null>(null);
  const [archivedOpen, setArchivedOpen] = useState(false);
  const userId = useAuthStore((state) => state.user?.userId);
  const [todayStudy, setTodayStudy] = useState<{
    due_review?: { total: number; decks: Array<{ artifact_id: number; title: string; due: number }> };
    mistake_review?: { books: number; variants: number; latest_artifact_id: number | null };
  } | null>(null);

  useEffect(() => {
    if (!userId) return;
    let active = true;
    getData<{ due_review?: { total: number; decks: Array<{ artifact_id: number; title: string; due: number }> };
      mistake_review?: { books: number; variants: number; latest_artifact_id: number | null };
    }>('/api/study/today')
      .then((data) => { if (active) setTodayStudy(data); })
      .catch(() => { /* 底部待办卡是增强，不阻塞工作台 */ });
    return () => { active = false; };
  }, [userId]);
  // 今日待办：FSRS 到期 + 错题变式，由 /api/study/today 聚合
const dueTotal = todayStudy?.due_review?.total || 0;
  const mistakeVariants = todayStudy?.mistake_review?.variants || 0;
  const hasTodayDebt = dueTotal > 0 || mistakeVariants > 0;
  const [correction, setCorrection] = useState('');
  const [editingObjectField, setEditingObjectField] = useState<ObjectEditField | null>(null);
  const [objectCorrection, setObjectCorrection] = useState('');
  const [editingClaimId, setEditingClaimId] = useState<number | null>(null);
  const [claimCorrection, setClaimCorrection] = useState('');
  const [editingMemoryId, setEditingMemoryId] = useState<number | null>(null);
  const [memoryCorrection, setMemoryCorrection] = useState('');
  const [learningLoop, setLearningLoop] = useState<DaryaLearningLoopSnapshot | null>(null);
  const [stageBusy, setStageBusy] = useState(false);
  const [stageError, setStageError] = useState('');
  const sidecar = moment.sidecar;
  const activeRoute = learningLoop?.active_route || null;
  const generalTeachingStage = useMemo(() => currentGeneralTeachingStage(messages), [messages]);
  const boundedTeachingStage = learningLoop?.teaching_stage || null;
  const teachingStage = boundedTeachingStage?.status === 'PAUSED' || boundedTeachingStage?.status === 'ENDED'
    ? boundedTeachingStage : generalTeachingStage || boundedTeachingStage;
  const visibleOpportunity = generalTeachingStage || teachingStage?.status === 'PAUSED' || teachingStage?.status === 'ENDED'
    ? null : opportunity;
  const supportPlan = useMemo(() => currentSupportPlan(messages), [messages]);
  const learningPaused = teachingStage?.status === 'PAUSED';
  const perspectiveFields = perspectiveOrder.filter((name) => {
    const value = sharedPerspective?.fields[name]?.value;
    return value !== null && value !== undefined && value !== '' && (!Array.isArray(value) || value.length > 0);
  });
  const hasPerspective = perspectiveFields.length > 0;
  const goalKind = String(sharedPerspective?.fields.goal_kind?.value || '');
  const parallelPaths = Array.isArray(sharedPerspective?.fields.parallel_goals?.value)
    ? (sharedPerspective.fields.parallel_goals.value as Array<Record<string, unknown>>)
    : [];
  const archivedGoals = Array.isArray(sharedPerspective?.fields.archived_goals?.value)
    ? (sharedPerspective.fields.archived_goals.value as Array<Record<string, unknown>>)
    : [];
  const hasUserContext = Boolean(userContext?.documents.length || userContext?.claims.length);
  const hasLongTermMemory = Boolean(longTermMemory?.memories.length);
  const railIdentity = artifactWorkbench
    ? `artifact-workbench-${artifactWorkbench.artifactId}-${artifactWorkbench.currentVersionId}`
    : supportPlan
    ? `support-workbench-${supportPlan.work_brief.goal || supportPlan.help_opportunity.true_goal}-${teachingStage?.revision || 0}`
    : activeRoute
    ? `active-route-${activeRoute.revision}-${teachingStage?.revision || 0}`
    : sidecar?.id
    || (artifactAction ? `artifact-action-${artifactAction.id}-${artifactAction.revision}` : null)
    || (workspaceObject ? `workspace-object-${workspaceObject.id}-${workspaceObject.revision}` : null)
    || (hasUserContext ? `user-context-${userContext?.claims.map((claim) => `${claim.id}.${claim.revision}`).join('-')}` : null)
    || (hasLongTermMemory ? `long-term-memory-${longTermMemory?.memories.map((memory) => `${memory.id}.${memory.revision}`).join('-')}` : null)
    || (hasPerspective ? `perspective-${sharedPerspective?.revision}` : null)
    || (teachingStage ? `teaching-stage-${teachingStage.progress.topic}-${teachingStage.revision}` : null)
    || (opportunity ? `opportunity-${opportunity.message_id}` : null);

  useEffect(() => {
    let cancelled = false;
    if (!currentThreadId) {
      setLearningLoop(null);
      return () => { cancelled = true; };
    }
    const load = () => tideChatApi.getLearningLoop(currentThreadId)
      .then((snapshot) => { if (!cancelled) setLearningLoop(snapshot); })
      .catch(() => { if (!cancelled) setLearningLoop(null); });
    void load();
    const handleUpdate = (event: Event) => {
      const detail = (event as CustomEvent<{ threadId?: string }>).detail;
      if (!detail?.threadId || detail.threadId === currentThreadId) void load();
    };
    window.addEventListener(DARYA_LEARNING_ROUTE_UPDATED, handleUpdate);
    return () => {
      cancelled = true;
      window.removeEventListener(DARYA_LEARNING_ROUTE_UPDATED, handleUpdate);
    };
  }, [currentThreadId, messages.length]);

  useEffect(() => {
    if (railIdentity && dismissedId && railIdentity !== dismissedId) setDismissedId(null);
  }, [dismissedId, railIdentity]);

  useEffect(() => {
    setEditingField(null);
    setCorrection('');
  }, [sharedPerspective?.revision]);

  useEffect(() => {
    setEditingObjectField(null);
    setObjectCorrection('');
  }, [workspaceObject?.revision]);

  useEffect(() => {
    setEditingClaimId(null);
    setClaimCorrection('');
  }, [userContext?.claims.map((claim) => `${claim.id}.${claim.revision}`).join('-')]);

  useEffect(() => {
    setEditingMemoryId(null);
    setMemoryCorrection('');
  }, [longTermMemory?.memories.map((memory) => `${memory.id}.${memory.revision}`).join('-')]);

  useEffect(() => {
    if (artifactWorkbench?.currentVersionId) railRef.current?.scrollTo({ top: 0 });
  }, [artifactWorkbench?.currentVersionId]);

  if ((!teachingStage && !supportPlan && !activeRoute && !sidecar && !artifactAction && !artifactWorkbench && !workspaceObject && !hasPerspective && !hasUserContext && !hasLongTermMemory && !longTermMemoryError && !opportunity && !hasTodayDebt) || (railIdentity !== null && railIdentity === dismissedId)) return null;
  const Icon = supportPlan ? Target : activeRoute || teachingStage ? Route : sidecar ? kindIcon[sidecar.kind] : opportunity ? Sparkles : Compass;
  const SidecarIcon = sidecar ? kindIcon[sidecar.kind] : Icon;
  const saving = perspectiveState === 'saving';
  const savingObject = workspaceObjectState === 'saving';
  const savingAction = artifactActionState === 'saving';
  const savingContext = userContextState === 'saving';
  const savingMemory = longTermMemoryState === 'saving';
  const qualityGate = artifactAction?.proposal.quality_gate;
  const opportunityOnly = Boolean(opportunity && !artifactWorkbench && !supportPlan && !activeRoute && !sidecar && !workspaceObject && !hasUserContext && !hasLongTermMemory && !hasPerspective);

  const act = (
    field: TidePerspectiveFieldName,
    action: 'confirm' | 'correct' | 'reject',
    value?: unknown,
  ) => {
    void controlPerspective({ field, action, value }).catch(() => undefined);
  };

  const editObject = (field: ObjectEditField, value: string) => {
    setEditingObjectField(field);
    setObjectCorrection(value);
  };

  const saveObjectEdit = () => {
    const value = objectCorrection.trim();
    if (!editingObjectField || !value) return;
    const action = editingObjectField === 'title'
      ? 'rename'
      : editingObjectField === 'purpose'
        ? 'correct_purpose'
        : 'set_focus';
    void controlWorkspaceObject({ action, value }).catch(() => undefined);
  };

  const runTeachingAction = async (action: TeachingStageAction) => {
    if (!currentThreadId || stageBusy || sendInFlight || streamState === 'connecting' || streamState === 'streaming') return;
    const topic = teachingStage?.progress.topic || activeRoute?.current_title || '当前知识点';
    const command: Record<TeachingStageAction, string> = {
      example: `关于「${topic}」，这一步请换成一个具体例子带我理解，最后让我独立判断。`,
      steps: `关于「${topic}」，这一步请拆开讲，一次推进一个关键点，最后让我独立判断。`,
      practice: `关于「${topic}」，这一步请给我一道练习题，先让我独立作答，再根据我的答案调整。`,
      pause: `暂停这段学习：「${topic}」。保留当前进度和下次要做的检查。`,
      resume: `继续刚才的学习：关于「${topic}」，从当前节点接着教，先给我一个可做的小步骤。`,
      end: `结束这一阶段教学：「${topic}」。请总结我已经独立做过的、还需检查的和下次从哪里继续；不要把停止说成已掌握。`,
    };
    setStageBusy(true);
    setStageError('');
    try {
      await send(command[action]);
      const failure = useTideStore.getState().error;
      if (failure) throw new Error(failure);
      const next = await tideChatApi.getLearningLoop(currentThreadId);
      const expectedStatus = action === 'pause' ? 'PAUSED' : action === 'end' ? 'ENDED' : action === 'resume' ? 'ACTIVE' : null;
      const nextStage = teachingStage?.scope === 'general'
        ? currentGeneralTeachingStage(useTideStore.getState().messages)
        : next.teaching_stage;
      if (expectedStatus && nextStage?.status !== expectedStatus) {
        throw new Error('阶段状态未更新，请再试一次。');
      }
      setLearningLoop(next);
    } catch (reason) {
      setStageError(reason instanceof Error ? reason.message : '操作没有完成，请再试一次。');
    } finally {
      setStageBusy(false);
    }
  };

  return (
    <aside
      className={`${styles.rail} ${artifactWorkbench ? styles.railWorkbench : ''}`}
      aria-label={artifactWorkbench ? 'DARYA 产物工作台' : 'DARYA 上下文工作台'}
      data-testid="tide-sidecar"
      data-sidecar-kind={artifactWorkbench ? 'artifact-workbench' : supportPlan ? 'support-workbench' : activeRoute ? 'active-route' : sidecar?.kind || (workspaceObject ? 'workspace-object' : hasUserContext ? 'user-context' : hasLongTermMemory ? 'long-term-memory' : hasPerspective ? 'shared-perspective' : 'proactive-opportunity')}
    >
      <header className={styles.heading}>
        <div className={styles.headingCopy}>
          <span className={styles.eyebrow}>{artifactWorkbench ? '产物工作台' : supportPlan ? 'DARYA 当前工作' : activeRoute ? '当前学习路线' : teachingStage ? '这一段教学' : sidecar?.eyebrow || (workspaceObject ? '当前工作对象' : hasUserContext ? '这份材料里的背景' : hasLongTermMemory ? '这次用到的记忆' : hasPerspective ? '我们正在做什么' : hasTodayDebt ? '今日学习' : '现在可能有帮助')}</span>
          <h2>{artifactWorkbench?.title || (supportPlan ? '我会怎样带你推进' : activeRoute ? '当前路线 · 为什么这样学' : teachingStage ? teachingStage.progress.topic : sidecar?.title) || workspaceObject?.title || (hasUserContext ? '我从这份材料里先抓到这些' : hasLongTermMemory ? '这些是我这次用到的' : hasPerspective ? '我们正在做什么' : hasTodayDebt ? '今天该推进的' : opportunity?.title)}</h2>
        </div>
        <button
          type="button"
          className={styles.close}
          onClick={() => {
            setDismissedId(railIdentity);
            onCollapse?.();
          }}
          aria-label="收起上下文工作台"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </header>

      <div className={styles.railBody} ref={railRef} data-testid="tide-rail-body">
      {teachingStage && !artifactWorkbench && (
        <TideTeachingStageCard
          stage={teachingStage}
          busy={stageBusy || sendInFlight || streamState === 'connecting' || streamState === 'streaming'}
          error={stageError}
          onAction={(action) => void runTeachingAction(action)}
        />
      )}
      {supportPlan && !artifactWorkbench && <DaryaSupportWorkbench plan={supportPlan} />}
      {activeRoute && teachingStage?.status === 'ACTIVE' && !artifactWorkbench && !supportPlan && (
        <TideLearningFitView
          route={activeRoute}
          fit={sidecar?.learningFit}
          resourceContent={sidecar?.learningFit ? sidecar.content : ''}
          paused={learningPaused}
          onOpenUnderstanding={onOpenUnderstanding}
        />
      )}

      {visibleOpportunity && (
        <section
          className={`${styles.opportunity} ${opportunityOnly ? styles.opportunityOnly : ''}`}
          aria-label="现在可能有帮助"
          data-testid="darya-proactive-opportunity"
          data-opportunity-id={visibleOpportunity.message_id}
          data-opportunity-kind={visibleOpportunity.resource_action.opportunity_kind}
        >
          {!opportunityOnly && <h3>{visibleOpportunity.title}</h3>}
          <p>{visibleOpportunity.content}</p>
          <div className={styles.opportunityActions} aria-label="建议操作">
            <button
              type="button"
              className={styles.opportunityOpen}
              disabled={opportunityState === 'saving'}
              onClick={() => void openOpportunity().then((result) => {
                const artifactId = result?.artifact_id;
                if (artifactId == null) return;
                // 优先在 /darya 内原位打开产物；跳到旧外壳会让海面、主题和上下文工作台全部消失
                if (onOpenArtifact) onOpenArtifact(artifactId);
                else navigate(`/artifacts/${artifactId}`);
              }).catch(() => undefined)}
              data-testid="opportunity-open"
            >
              打开
            </button>
            <button
              type="button"
              disabled={opportunityState === 'saving'}
              onClick={() => void laterOpportunity().catch(() => undefined)}
              data-testid="opportunity-later"
            >
              稍后
            </button>
            <button
              type="button"
              disabled={opportunityState === 'saving'}
              onClick={() => void dismissOpportunity().catch(() => undefined)}
              data-testid="opportunity-dismiss"
            >
              不再提示
            </button>
          </div>
          {opportunityError && <p className={styles.perspectiveError} role="alert">{opportunityError}</p>}
        </section>
      )}

      {artifactWorkbench && <TideArtifactWorkbench workbench={artifactWorkbench} action={artifactAction} />}
      {!artifactWorkbench && artifactWorkbenchState === 'loading' && workspaceObject?.related_artifact_references.length ? (
        <p className={styles.workbenchState}>正在读取当前产物…</p>
      ) : null}
      {!artifactWorkbench && artifactWorkbenchState === 'error' && artifactWorkbenchError ? (
        <p className={styles.perspectiveError} role="alert">当前产物暂时无法显示：{artifactWorkbenchError}</p>
      ) : null}

      {!artifactWorkbench && hasUserContext && (
        <section className={styles.userContext} aria-label="当前对话中的用户背景" data-testid="tide-user-context">
          <div className={styles.userContextIntro}>
            <p>先核对，再使用。文档原文和 DARYA 的推断不会被当成外部事实认证。</p>
            <small>{userContext?.documents.map((document) => document.file_name).join('、')}</small>
          </div>
          <div className={styles.userContextClaims}>
            {userContext?.claims.map((claim) => (
              <article
                key={claim.id}
                className={styles.userContextClaim}
                data-claim-id={claim.id}
                data-kind={claim.kind}
                data-status={claim.status}
              >
                <div className={styles.userContextClaimMeta}>
                  <span>{userContextKindLabel[claim.kind] || claim.kind}</span>
                  <small data-status={claim.status}>{userContextStatusLabel[claim.status]}</small>
                </div>
                <p>{claim.value}</p>
                <cite>{userContextProvenance(claim)}</cite>
                {editingClaimId === claim.id ? (
                  <div className={styles.contextCorrection}>
                    <input
                      value={claimCorrection}
                      maxLength={500}
                      aria-label={`修正${userContextKindLabel[claim.kind] || '背景信息'}`}
                      onChange={(event) => setClaimCorrection(event.target.value)}
                    />
                    <button
                      type="button"
                      disabled={savingContext || !claimCorrection.trim()}
                      onClick={() => void controlUserContextClaim(claim.id, {
                        action: 'correct',
                        value: claimCorrection.trim(),
                      }).catch(() => undefined)}
                    >保存修正</button>
                    <button type="button" disabled={savingContext} onClick={() => setEditingClaimId(null)}>取消</button>
                  </div>
                ) : (
                  <div className={styles.contextClaimActions}>
                    {claim.status !== 'CONFIRMED' && claim.status !== 'REJECTED' && (
                      <button
                        type="button"
                        disabled={savingContext}
                        onClick={() => void controlUserContextClaim(claim.id, { action: 'confirm' }).catch(() => undefined)}
                        data-testid={`user-context-confirm-${claim.id}`}
                      >确认</button>
                    )}
                    {claim.status !== 'REJECTED' && (
                      <button
                        type="button"
                        disabled={savingContext}
                        onClick={() => {
                          setEditingClaimId(claim.id);
                          setClaimCorrection(claim.value);
                        }}
                        data-testid={`user-context-correct-${claim.id}`}
                      >修正</button>
                    )}
                    {claim.status !== 'REJECTED' && (
                      <button
                        type="button"
                        disabled={savingContext}
                        onClick={() => void controlUserContextClaim(claim.id, { action: 'reject' }).catch(() => undefined)}
                        data-testid={`user-context-reject-${claim.id}`}
                      >不是我的情况</button>
                    )}
                    {claim.status === 'CONFIRMED' && (
                      <button
                        type="button"
                        disabled={savingMemory}
                        onClick={() => void promoteContextClaimToMemory(claim.id).catch(() => undefined)}
                        data-testid={`user-context-save-memory-${claim.id}`}
                      >以后也记得</button>
                    )}
                  </div>
                )}
              </article>
            ))}
          </div>
          {userContextError && <p className={styles.perspectiveError} role="alert">{userContextError}</p>}
        </section>
      )}

      {!artifactWorkbench && hasLongTermMemory && (
        <section className={styles.longTermMemory} aria-label="与当前问题相关的长期记忆" data-testid="darya-long-term-memory">
          <div className={styles.memoryIntro}>
            <p>下面这些记忆正在影响这次回答。与事实不符的直接忘掉，说错半个字就修正。</p>
          </div>
          <div className={styles.memoryList}>
            {longTermMemory?.memories.map((memory) => (
              <article key={memory.id} className={styles.memoryItem} data-memory-id={memory.id} data-memory-type={memory.type}>
                <div className={styles.memoryMeta}>
                  <span data-type={memory.type}>{memoryTypeLabel[memory.type]}</span>
                  <small data-state={memory.state}>{memory.state === 'STALE' ? '需要再确认' : '正在影响回答'}</small>
                </div>
                <p>{memory.content}</p>
                <cite>{memoryProvenance(memory)}</cite>
                {editingMemoryId === memory.id ? (
                  <div className={styles.memoryCorrection}>
                    <input
                      value={memoryCorrection}
                      maxLength={500}
                      aria-label="修正长期记忆"
                      onChange={(event) => setMemoryCorrection(event.target.value)}
                    />
                    <button
                      type="button"
                      disabled={savingMemory || !memoryCorrection.trim()}
                      onClick={() => void controlLongTermMemory(memory.id, {
                        action: 'correct',
                        content: memoryCorrection.trim(),
                      }).catch(() => undefined)}
                    >保存修正</button>
                    <button type="button" disabled={savingMemory} onClick={() => setEditingMemoryId(null)}>取消</button>
                  </div>
                ) : (
                  <div className={styles.memoryActions}>
                    <button
                      type="button"
                      disabled={savingMemory}
                      onClick={() => {
                        setEditingMemoryId(memory.id);
                        setMemoryCorrection(memory.content);
                      }}
                      data-testid={`memory-correct-${memory.id}`}
                    >修正</button>
                    <button
                      type="button"
                      disabled={savingMemory}
                      onClick={() => void controlLongTermMemory(memory.id, { action: 'forget' }).catch(() => undefined)}
                      data-testid={`memory-forget-${memory.id}`}
                    >忘掉</button>
                  </div>
                )}
              </article>
            ))}
          </div>
          {longTermMemoryError && <p className={styles.perspectiveError} role="alert">{longTermMemoryError}</p>}
        </section>
      )}

      {/* 「以后也记得」失败时往往还没有任何记忆，hasLongTermMemory 为 false，
          错误提示若只挂在记忆区块里就永远看不到 —— 因此这里独立渲染一次。 */}
      {!artifactWorkbench && !hasLongTermMemory && longTermMemoryError && (
        <p className={styles.perspectiveError} role="alert" data-testid="darya-memory-error">{longTermMemoryError}</p>
      )}

      {artifactAction && (
        <section className={`${styles.artifactAction} ${artifactWorkbench ? styles.artifactActionCompact : ''}`} aria-label="当前产物动作" data-testid="tide-artifact-action">
          <div className={styles.actionHeading}>
            <span><Sparkles size={13} aria-hidden="true" /> 当前动作</span>
            <small data-status={artifactAction.status}>{artifactActionStatusLabel[artifactAction.status]}</small>
          </div>
          <div className={styles.actionLine}>
            <span>正在做</span>
            <p>{artifactAction.proposal.scope ? `改进${artifactAction.proposal.scope}` : '改进当前演示文稿'}</p>
          </div>
          <div className={styles.actionLine}>
            <span>为什么</span>
            <p>{artifactAction.reason}</p>
          </div>
          {(artifactAction.proposal.changes?.length || 0) > 0 && (
            <div className={styles.actionLine}>
              <span>准备调整</span>
              <ul>
                {artifactAction.proposal.changes?.map((change) => <li key={change}>{change}</li>)}
              </ul>
            </div>
          )}
          {(qualityGate?.strengthened?.length || 0) > 0 && (
            <div className={styles.qualityNote} data-testid="tide-quality-strengthened">
              <span>为什么这一版更稳</span>
              <ul>
                {qualityGate?.strengthened?.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          )}
          {(qualityGate?.needs_evidence?.length || 0) > 0 && (
            <div className={styles.evidenceNote} data-testid="tide-quality-needs-evidence">
              <span>仍需依据</span>
              <ul>
                {qualityGate?.needs_evidence?.map((item, index) => (
                  <li key={`${item.location || 'artifact'}-${index}`}>
                    {item.location ? `${item.location}：` : ''}{item.description}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {artifactAction.status === 'completed' && (
            <div className={styles.actionResult}>
              已从 v{artifactAction.source_version_number || artifactAction.proposal.source_version_number || '—'} 创建
              v{artifactAction.proposal.target_version_number || '新版本'}；旧版本仍保留。
            </div>
          )}
          {artifactAction.status === 'conflicted' && (
            <div className={styles.actionConflict} role="status">
              当前产物已经前进到 v{artifactAction.proposal.current_version_number || '更新版本'}。这份旧提案不会执行，请基于当前版本重新提出修改。
            </div>
          )}
          {artifactAction.status === 'blocked' && (
            <div className={styles.actionBlocked} role="status" data-testid="tide-quality-blocked">
              {qualityGate?.summary || '这份候选还没有通过质量审阅，原版本保持不变。'}
            </div>
          )}
          {artifactAction.status === 'proposing' && (
            <div className={styles.actionControls}>
              <button
                type="button"
                disabled={savingAction}
                onClick={() => void controlArtifactAction('confirm').catch(() => undefined)}
              >
                确认并创建新版本
              </button>
              <button
                type="button"
                disabled={savingAction}
                onClick={() => void controlArtifactAction('cancel').catch(() => undefined)}
              >
                暂不修改
              </button>
            </div>
          )}
          {artifactActionError && <p className={styles.perspectiveError} role="alert">{artifactActionError}</p>}
        </section>
      )}

      {artifactWorkbench && workspaceObject && (
        <details className={styles.contextDisclosure} data-testid="tide-workspace-object">
          <summary>
            <span>工作上下文</span>
            <strong>{workspaceObject.title}</strong>
            {workspaceObject.related_artifact_references.length > 0 && (
              <small>{workspaceObject.related_artifact_references.map((reference) => reference.title || `产物 #${reference.artifact_id}`).join('、')}</small>
            )}
          </summary>
          <div className={styles.contextBody}>
            <p><span>目的</span>{workspaceObject.purpose}</p>
            <p><span>阶段</span>{workspaceStatusLabel[workspaceObject.status]}</p>
            {workspaceObject.next_action && <p><span>接下来</span>{workspaceObject.next_action}</p>}
            {Boolean(sharedPerspective?.fields.goal?.value) && (
              <p data-testid="tide-shared-perspective"><span>共同目标</span>{displayPerspectiveValue('goal', sharedPerspective?.fields.goal?.value)}</p>
            )}
          </div>
          <div className={styles.objectActions}>
            <button type="button" disabled={savingObject} onClick={() => editObject('title', workspaceObject.title)}>重命名</button>
            <button type="button" disabled={savingObject} onClick={() => editObject('purpose', workspaceObject.purpose)}>修正目的</button>
            <button type="button" disabled={savingObject} onClick={() => editObject('focus', workspaceObject.next_action || '')}>调整焦点</button>
          </div>
          {editingObjectField && (
            <div className={styles.objectCorrection}>
              <input
                value={objectCorrection}
                maxLength={500}
                aria-label="修正当前工作对象"
                onChange={(event) => setObjectCorrection(event.target.value)}
              />
              <button type="button" disabled={savingObject || !objectCorrection.trim()} onClick={saveObjectEdit}>保存</button>
              <button type="button" disabled={savingObject} onClick={() => setEditingObjectField(null)}>取消</button>
            </div>
          )}
          {workspaceObjectError && <p className={styles.perspectiveError} role="alert">{workspaceObjectError}</p>}
        </details>
      )}

      {!artifactWorkbench && workspaceObject && (
        <section className={styles.workspaceObject} aria-label="当前工作对象" data-testid="tide-workspace-object">
          <div className={styles.workspaceHeading}>
            <span><Target size={13} aria-hidden="true" /> 这就是我们正在推进的事</span>
            <small data-confirmed={workspaceObject.is_confirmed}>
              {workspaceObject.is_confirmed ? '已确认' : '待确认'}
            </small>
          </div>
          <div className={styles.workspaceBody}>
            <div className={styles.workspaceLine}>
              <span>目的</span>
              <p>{workspaceObject.purpose}</p>
            </div>
            <div className={styles.workspaceLine}>
              <span>当前阶段</span>
              <p>{workspaceStatusLabel[workspaceObject.status]}</p>
            </div>
            {workspaceObject.next_action && (
              <div className={styles.workspaceLine}>
                <span>眼下先做</span>
                <p>{workspaceObject.next_action}</p>
              </div>
            )}
            {workspaceObject.related_artifact_references.length > 0 && (
              <div className={styles.workspaceLine}>
                <span>相关产物</span>
                <p>{workspaceObject.related_artifact_references.map((reference) => (
                  reference.title || `产物 #${reference.artifact_id}`
                )).join('、')}</p>
              </div>
            )}
            {workspaceObject.related_decisions.length > 0 && (
              <div className={styles.workspaceLine}>
                <span>已经决定</span>
                <p>{workspaceObject.related_decisions.join('；')}</p>
              </div>
            )}
          </div>
          {editingObjectField ? (
            <div className={styles.objectCorrection}>
              <input
                value={objectCorrection}
                maxLength={500}
                aria-label="修正当前工作对象"
                onChange={(event) => setObjectCorrection(event.target.value)}
              />
              <button type="button" disabled={savingObject || !objectCorrection.trim()} onClick={saveObjectEdit}>保存</button>
              <button type="button" disabled={savingObject} onClick={() => setEditingObjectField(null)}>取消</button>
            </div>
          ) : (
            <div className={styles.objectActions}>
              {!workspaceObject.is_confirmed && (
                <button type="button" disabled={savingObject} onClick={() => void controlWorkspaceObject({ action: 'confirm' }).catch(() => undefined)}>确认对象</button>
              )}
              <button type="button" disabled={savingObject} onClick={() => editObject('title', workspaceObject.title)}>重命名</button>
              <button type="button" disabled={savingObject} onClick={() => editObject('purpose', workspaceObject.purpose)}>修正目的</button>
              <button type="button" disabled={savingObject} onClick={() => editObject('focus', workspaceObject.next_action || '')}>调整焦点</button>
            </div>
          )}
          {workspaceObjectError && <p className={styles.perspectiveError} role="alert">{workspaceObjectError}</p>}
        </section>
      )}

      {!artifactWorkbench && hasPerspective && !activeRoute && !supportPlan && sidecar?.kind !== 'learning-fit' && (
        <section className={styles.perspective} aria-label="当前共同视角" data-testid="tide-shared-perspective">
          <div className={styles.perspectiveHeading}>
            <span>目前我理解到的</span>
            <small>只关于这段共同工作</small>
          </div>
          <dl>
            {perspectiveFields.map((name) => {
              const field = sharedPerspective?.fields[name];
              if (!field) return null;
              const editable = name !== 'current_artifacts';
              return (
                <div className={styles.perspectiveRow} key={name} data-field={name}>
                  <dt>
                    {name === 'goal' && goalKind ? `${perspectiveLabels[name]} · ${GOAL_KIND_LABEL[goalKind] || '任务'}` : perspectiveLabels[name]}
                    <span data-status={field.status}>{field.status === 'confirmed' ? '已确认' : '待确认'}</span>
                  </dt>
                  <dd>{displayPerspectiveValue(name, field.value)}</dd>
                  {editingField === name ? (
                    <div className={styles.correction}>
                      <input
                        value={correction}
                        maxLength={500}
                        aria-label={`修正${perspectiveLabels[name]}`}
                        onChange={(event) => setCorrection(event.target.value)}
                      />
                      <button
                        type="button"
                        disabled={saving || !correction.trim()}
                        onClick={() => act(name, 'correct', correction.trim())}
                      >
                        保存修正
                      </button>
                      <button type="button" disabled={saving} onClick={() => setEditingField(null)}>取消</button>
                    </div>
                  ) : (
                    <div className={styles.fieldActions}>
                      {field.status !== 'confirmed' && (
                        <button type="button" disabled={saving} onClick={() => act(name, 'confirm')}>确认</button>
                      )}
                      {editable && (
                        <button
                          type="button"
                          disabled={saving}
                          onClick={() => {
                            setEditingField(name);
                            setCorrection(displayPerspectiveValue(name, field.value));
                          }}
                        >
                          修正
                        </button>
                      )}
                      <button type="button" disabled={saving} onClick={() => act(name, 'reject')}>不是这个</button>
                    </div>
                  )}
                </div>
              );
            })}
          </dl>
          {parallelPaths.length > 0 && (
            <div className={styles.parallelPaths} data-testid="tide-parallel-paths">
              {parallelPaths.map((path, index) => (
                <div className={styles.parallelPath} key={index}>
                  <span className={styles.parallelBadge}>并行</span>
                  <p>{typeof path === 'string' ? path : String(path?.text || '')}</p>
                  <small>{typeof path === 'object' && typeof path?.kind === 'string' ? GOAL_KIND_LABEL[path.kind] || '任务' : ''}</small>
                </div>
              ))}
            </div>
          )}
          {archivedGoals.length > 0 && (
            <div className={styles.archivedGoals} data-testid="tide-archived-goals">
              <button type="button" onClick={() => setArchivedOpen((open) => !open)}>
                归档目标 {archivedGoals.length}
              </button>
              {archivedOpen && (
                <ul>
                  {archivedGoals.map((entry, index) => (
                    <li key={index}>
                      <span>{typeof entry === 'string' ? entry : String(entry?.text || '')}</span>
                      <small>{typeof entry === 'object' && typeof entry?.reason === 'string' ? entry.reason : '已完成'}</small>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {perspectiveError && <p className={styles.perspectiveError} role="alert">{perspectiveError}</p>}
        </section>
      )}

      {sidecar && !artifactWorkbench && !supportPlan && !(activeRoute && sidecar.learningFit) && (
        <>
          <div className={styles.objectType}>
            <SidecarIcon size={14} strokeWidth={1.55} aria-hidden="true" />
            <span>{kindLabel[sidecar.kind]}</span>
          </div>
          {sidecar.learningFit ? (
            <TideLearningFitView fit={sidecar.learningFit} resourceContent={sidecar.content} paused={learningPaused} />
          ) : (
            <SafeMarkdown source={sidecar.content} className={styles.markdown} testId="tide-sidecar-content" />
          )}
        </>
      )}
      <footer>{artifactWorkbench ? 'DARYA 将当前真实产物放在这里工作 · 版本变化只在确认后发生' : supportPlan ? '这是模型公开的当前工作简报：可被下一轮复核，不是固定路线，也不等于掌握结论' : activeRoute ? '路线会随你的真实学习慢慢更新 · 普通聊天不会被打断成教学' : sidecar ? '当前共同视角与本轮真实工作对象' : workspaceObject ? '共同推进中的工作对象 · 不等于文件或最终产物' : hasUserContext ? '当前对话背景只有在你选择“以后也记得”后才进入长期记忆' : hasLongTermMemory ? '只在相关时使用 · 可修正、可忘掉 · 不建立监控式画像' : hasPerspective ? '可确认、修正或排除 · 不写入长期画像' : hasTodayDebt ? '底部是今天欠的学习债 · 处理完它们再休息' : '可忽略，不会抢走当前对话'}</footer>
      </div>

      {hasTodayDebt && (
        <div className={styles.todayDock} data-testid="tide-today-dock">
          {dueTotal > 0 && (
            <button type="button" className={styles.todayRow} onClick={() => {
              const deck = todayStudy?.due_review?.decks?.[0];
              if (!deck) return;
              if (onOpenArtifact) onOpenArtifact(deck.artifact_id);
              else window.open(`/artifacts/${deck.artifact_id}?kind=flashcards`, '_self');
            }} data-testid="today-dock-cards">
              <strong>{dueTotal}</strong>
              <span>张记忆卡到期</span>
              <small>去复习</small>
            </button>
          )}
          {mistakeVariants > 0 && (
            <button type="button" className={styles.todayRow} onClick={() => {
              const id = todayStudy?.mistake_review?.latest_artifact_id;
              if (!id) return;
              if (onOpenArtifact) onOpenArtifact(id);
              else window.open(`/artifacts/${id}?kind=mistake_book`, '_self');
            }} data-testid="today-dock-mistakes">
              <strong>{mistakeVariants}</strong>
              <span>道错题变式待练</span>
              <small>去巩固</small>
            </button>
          )}
        </div>
      )}
    </aside>
  );
}
