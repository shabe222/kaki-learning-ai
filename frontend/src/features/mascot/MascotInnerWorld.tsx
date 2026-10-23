import { X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { getData } from '../../api/client';
import { getNormalizedLearnerState, type NormalizedLearnerState } from '../../api/learnerState';
import type { BackendProfile, GeneratedItem, LearningWorkspacePayload } from '../../api/modules';
import styles from './MascotInnerWorld.module.css';

function profileValue(profile: BackendProfile | null, key: string) {
  const entries = (profile?.profile_data?.profile_onboarding as { entries?: Record<string, { value?: unknown }> } | undefined)?.entries;
  const raw = entries?.[key]?.value;
  return typeof raw === 'string' ? raw.trim() : '';
}

type GroundedSnapshot = {
  profile: BackendProfile | null;
  learnerState: NormalizedLearnerState | null;
  workspace: LearningWorkspacePayload | null;
  artifacts: GeneratedItem[];
  unavailable: string[];
};

const EMPTY_SNAPSHOT: GroundedSnapshot = { profile: null, learnerState: null, workspace: null, artifacts: [], unavailable: [] };

const STATUS_LABEL: Record<string, string> = {
  unknown: '刚刚开始建立理解', emerging: '正在建立理解', developing: '理解正在深入',
  partial: '已经形成部分掌握', secure: '掌握比较稳定',
  INSUFFICIENT_EVIDENCE: '证据还不太够', EMERGING: '正在建立理解', DEVELOPING: '理解正在深入',
  PROFICIENT: '已经比较熟练', SECURE: '掌握比较稳定', NEEDS_REVIEW: '还需要再巩固',
};

/** 学习进展的阶段：0 开始学习 → 1 理解概念 → 2 练习巩固 → 3 持续提升 */
type StageIndex = 0 | 1 | 2 | 3;

const STAGE_META: Record<StageIndex, { name: string; pathLabel: string }> = {
  0: { name: '开始学习', pathLabel: '开始学习' },
  1: { name: '理解概念', pathLabel: '概念理解' },
  2: { name: '练习巩固', pathLabel: '概念理解 → 练习巩固' },
  3: { name: '持续提升', pathLabel: '练习巩固 → 持续提升' },
};

function deriveStage(events: Array<Record<string, unknown>>): StageIndex {
  let hasConcept = false;
  let hasPractice = false;
  for (const event of events) {
    const type = String(event?.event_type || '');
    if (/exam|answer|assessment|stage_completed|attempt|hint|solution|grading/i.test(type)) hasPractice = true;
    else if (/resource|lesson|material|open|selected|generate|node|read|completed/i.test(type)) hasConcept = true;
  }
  if (hasPractice) return 2;
  if (hasConcept || events.length > 0) return 1;
  return 0;
}

/** 从偏好标签组合出用户可读的学习方式描述 */
function describeStudyApproach(modalities: string[]): string {
  const set = new Set(modalities);
  const structural = ['visual', 'document', 'reading', 'step_by_step', 'concise'].some((m) => set.has(m));
  const practice = ['practice', 'example', 'code'].some((m) => set.has(m));
  if (structural && practice) return '你更喜欢先把整体结构弄明白，再通过练习把知识用起来。';
  if (structural) return '你更喜欢先把整体结构弄明白，再慢慢深入细节。';
  if (practice) return '你更喜欢一边动手练习，一边把知识学扎实。';
  return '';
}

function countEventKinds(events: Array<Record<string, unknown>>) {
  let concept = 0;
  let practice = 0;
  for (const event of events) {
    const type = String(event?.event_type || '');
    if (/exam|answer|assessment|stage_completed|attempt|grading/i.test(type)) practice += 1;
    else if (/resource|lesson|material|open|selected|generate|node|read/i.test(type)) concept += 1;
  }
  return { concept, practice };
}

export function MascotInnerWorld({ userId, onClose }: { userId: number; onClose: () => void }) {
  const [snapshot, setSnapshot] = useState<GroundedSnapshot>(EMPTY_SNAPSHOT);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', closeOnEscape);
    void Promise.allSettled([
      getData<BackendProfile | null>(`/api/profile/${userId}`),
      getNormalizedLearnerState(userId),
      getData<LearningWorkspacePayload>('/api/learning/workspace'),
      getData<GeneratedItem[]>('/api/items/list')
    ]).then(([profileResult, learnerStateResult, workspaceResult, artifactsResult]) => {
      if (!alive) return;
      const unavailable: string[] = [];
      if (profileResult.status === 'rejected') unavailable.push('学习偏好');
      if (learnerStateResult.status === 'rejected') unavailable.push('学习状态聚合');
      if (workspaceResult.status === 'rejected') unavailable.push('近期学习');
      if (artifactsResult.status === 'rejected') unavailable.push('学习成果');
      setSnapshot({
        profile: profileResult.status === 'fulfilled' ? profileResult.value : null,
        learnerState: learnerStateResult.status === 'fulfilled' ? learnerStateResult.value : null,
        workspace: workspaceResult.status === 'fulfilled' ? workspaceResult.value : null,
        artifacts: artifactsResult.status === 'fulfilled' ? artifactsResult.value : [],
        unavailable
      });
      setLoading(false);
    });
    return () => {
      alive = false;
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [onClose, userId]);

  const view = useMemo(() => {
    const learner = snapshot.learnerState;
    const events = Array.isArray(learner?.recent_learning_events)
      ? learner!.recent_learning_events as Array<Record<string, unknown>>
      : [];
    const stage = deriveStage(events);
    const stageMeta = STAGE_META[stage];

    const topic = learner?.current_topic?.trim()
      || (snapshot.workspace?.chapters?.find((c) => c.task_status === 'in_progress')?.name ?? '')
      || snapshot.artifacts?.[0]?.chapter
      || profileValue(snapshot.profile, 'learning_goal')
      || '';

    const modalities = Array.isArray(learner?.preferred_modalities) ? learner!.preferred_modalities : [];
    const hasPreferenceEvidence = Array.isArray(learner?.preference_evidence) && learner!.preference_evidence.length > 0;
    const approach = describeStudyApproach(modalities);

    const latestEvidence = learner?.recent_evidence?.[0] as Record<string, unknown> | undefined;
    const previousStatus = String(latestEvidence?.previous_assessment_status || '');
    const currentStatus = String(latestEvidence?.assessment_status || '');
    const hasComparableChange = Boolean(previousStatus && currentStatus && previousStatus !== currentStatus);
    const changeFrom = STATUS_LABEL[previousStatus] || previousStatus;
    const changeTo = STATUS_LABEL[currentStatus] || currentStatus;
    const { concept, practice } = countEventKinds(events);
    const changeReason = [
      concept > 0 ? `${concept} 次概念梳理` : '',
      practice > 0 ? `${practice} 次练习` : ''
    ].filter(Boolean).join('、');

    const focus = learner?.next_teaching_focus?.trim()
      || learner?.weak_points?.[0]
      || topic
      || '';
    const weakPoint = learner?.weak_points?.[0] || '';

    return {
      stage,
      stageMeta,
      topic,
      modalities,
      hasPreferenceEvidence,
      approach,
      hasComparableChange,
      changeFrom,
      changeTo,
      changeReason,
      focus,
      weakPoint,
      hasAnyActivity: events.length > 0,
    };
  }, [snapshot]);

  const { stageMeta, topic, approach, hasPreferenceEvidence, hasComparableChange, changeFrom, changeTo, changeReason, focus, weakPoint, hasAnyActivity } = view;
  const stageNum = view.stage + 1;
  const completedSegs = view.stage; // 已完整走完的阶段数
  const halfSeg = view.stage < 3 ? view.stage : 2; // 当前阶段对应的进度段（半亮）

  const dialog = (
    <div
      className={styles.backdrop}
      role="dialog"
      aria-modal="true"
      aria-label="柿小吉眼中的你"
      data-testid="mascot-inner-world"
      data-generation-status="GENERATIVE_EXPRESSION_BLOCKED"
      onPointerDown={(event) => event.stopPropagation()}
      onPointerUp={(event) => event.stopPropagation()}
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <section className={styles.panel}>
        <div className={styles.visualBg} aria-hidden="true" />
        <header>
          <div className={styles.headMain}>
            <span className={styles.avatar}>柿</span>
            <div className={styles.headText}>
              <strong>柿小吉眼中的你</strong>
              <span className={styles.subLabel}>根据你的学习记录，我在慢慢了解你</span>
            </div>
          </div>
          <button type="button" aria-label="关闭内心世界" data-testid="mascot-inner-world-close" onClick={onClose}><X size={18} /></button>
        </header>

        <main>
          {loading ? (
            <p className={styles.empty}>正在读取真实学习记录…</p>
          ) : (
            <>
              {/* Hero：学习进展 + 当前学习（复合） */}
              <section className={styles.hero}>
                <div className={styles.growth}>
                  <div className={styles.sectionHeader}><span className={styles.bar} />学习进展</div>
                  <div className={styles.stageFlow}>
                    {([0, 1, 2, 3] as StageIndex[]).map((index) => (
                      <div
                        key={index}
                        className={[
                          styles.stageItem,
                          index < view.stage ? styles.done : '',
                          index === view.stage ? styles.current : ''
                        ].filter(Boolean).join(' ')}
                      >
                        <div className={styles.stageBody}>
                          <div className={styles.stageName}>
                            {STAGE_META[index].name}
                            {index === view.stage && <span className={styles.currentTag}>现在</span>}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className={styles.heroRight}>
                  <div className={styles.topicBlock}>
                    <div className={styles.topicKicker}>最近正在学习</div>
                    <div className={styles.topicRow}>
                      <div className={styles.topicName}>{topic || '还没有开始新的学习主题'}</div>
                      <span className={styles.topicMeta}>{stageMeta.pathLabel}</span>
                    </div>
                    <div className={styles.progressComposite}>
                      <span className={styles.posLabel}>{stageMeta.name}</span>
                      <span className={styles.segTrack} aria-hidden="true">
                        {[0, 1, 2, 3].map((index) => (
                          <span
                            key={index}
                            className={[
                              styles.seg,
                              index < completedSegs ? styles.filled : '',
                              index === halfSeg && view.stage < 3 ? styles.half : '',
                              index === halfSeg && view.stage < 3 ? styles.currentSeg : ''
                            ].filter(Boolean).join(' ')}
                          />
                        ))}
                      </span>
                      <span className={styles.stageText}>第 {stageNum} 阶段 / 共 4 阶段</span>
                    </div>
                  </div>

                  {approach && (
                    <div className={styles.insight}>
                      <span className={styles.insightKicker}>我发现</span>
                      <span className={styles.insightText}>{approach}</span>
                    </div>
                  )}
                </div>
              </section>

              {/* 三个模块 */}
              <section className={styles.modules}>
                <article className={styles.module}>
                  <div className={styles.badge}>你的学习方式</div>
                  <div className={styles.divider} />
                  <div className={styles.mainText}>
                    {approach ? approach.replace(/^你更喜欢/, '习惯') : '还在通过你的学习记录慢慢了解'}
                  </div>
                  {hasPreferenceEvidence && (
                    <div className={styles.subText}>从你最近看资料、做练习的方式里，我慢慢看出来的。</div>
                  )}
                </article>

                {hasComparableChange && (
                  <article className={styles.module}>
                    <div className={styles.badge}>最近的变化</div>
                    <div className={styles.divider} />
                    <div className={styles.changeFlow}>
                      <div className={styles.changeRow}>
                        <span className={styles.fromTo}>以前：{changeFrom}</span>
                      </div>
                      <div className={styles.changeRow}>
                        <span className={styles.arrow}>↓</span>
                        <span className={`${styles.fromTo} ${styles.to}`}>现在：{changeTo}</span>
                      </div>
                      {changeReason && <div className={styles.changeNote}>来自近期 {changeReason} 的记录。</div>}
                    </div>
                  </article>
                )}

                <article className={`${styles.module} ${styles.wide}`}>
                  <div className={styles.badge}>接下来我会这样陪你</div>
                  <div className={styles.divider} />
                  <div className={styles.mainText}>先把知识结构理清楚，再陪你把练习一步步做完。</div>
                  <div className={styles.futureList}>
                    <div className={styles.futureItem}>
                      <span className={styles.tag}>先理结构</span>
                      {focus ? `「${focus}」的知识结构，我讲给你听` : '从你正在学的内容里，先梳理出知识框架'}
                    </div>
                    <div className={styles.futureItem}>
                      <span className={styles.tag}>再练一练</span>
                      {weakPoint
                        ? `针对「${weakPoint}」安排几道循序渐进的小练习，我们一起过`
                        : '安排几道循序渐进的小练习，我们一起做一遍'}
                    </div>
                  </div>
                </article>
              </section>

              {!hasAnyActivity && (
                <p className={styles.notice}>暂时还没有足够的学习记录，等我们多学几次，我就能更懂你了。</p>
              )}
              {snapshot.unavailable.length > 0 && (
                <p className={styles.notice}>暂时无法读取：{snapshot.unavailable.join('、')}。</p>
              )}
            </>
          )}
        </main>
      </section>
    </div>
  );
  return createPortal(dialog, document.body);
}
