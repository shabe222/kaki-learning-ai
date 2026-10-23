import { Button, Empty, Progress, Skeleton, Tag } from 'antd';
import {
  ArrowRight,
  Bell,
  BookOpen,
  Bot,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Clock3,
  FileText,
  LibraryBig,
  NotebookPen,
  PackageOpen,
  Target,
  TrendingUp
} from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { moduleApi } from '../api/modules';
import type { BackendProfile, DashboardActivity, DashboardSuggestion, DashboardTodo } from '../api/modules';
import { listNotes, type NoteItem } from '../api/notes';
import { userAnnouncementApi } from '../api/admin';
import { TrendChart } from '../components/DataPanel';
import { RelaxMoment } from '../components/relax/RelaxMoment';
import { useRequest } from '../hooks';
import { useAuthStore } from '../stores/authStore';
import styles from './DashboardPage.module.css';

function shortTime(value?: string | null) {
  if (!value) return '';
  return value.replace('T', ' ').slice(0, 16);
}

function timeGreeting() {
  const hour = new Date().getHours();
  if (hour < 6) return '夜深了';
  if (hour < 11) return '早上好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

function artifactTypeLabel(type?: string) {
  const value = String(type ?? '').toLowerCase();
  if (value.includes('ppt')) return 'PPT';
  if (value.includes('mindmap')) return '导图';
  if (value.includes('question')) return '题目';
  if (value.includes('document')) return '讲义';
  if (value.includes('code')) return '代码';
  if (value.includes('video')) return '视频';
  return '产物';
}

function cleanArtifactTitle(title?: string) {
  return (title || '未命名产物').replace(/^(Python)(?:\s+Python)+/i, '$1');
}

function clampPercent(value: unknown) {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.min(100, Math.round(numeric)));
}

interface RecommendationView {
  title: string;
  reason: string;
  method: string;
  duration: string;
  route: string;
  evidence: string;
}

interface VisibleLearningBrief {
  remembered: string;
  decision: string;
  cadence: string | null;
}

function confirmedProfileValue(profile: BackendProfile | null | undefined, key: string) {
  const onboarding = profile?.profile_data?.profile_onboarding;
  if (!onboarding || typeof onboarding !== 'object') return '';
  const entries = (onboarding as Record<string, unknown>).entries;
  if (!entries || typeof entries !== 'object') return '';
  const entry = (entries as Record<string, unknown>)[key];
  if (!entry || typeof entry !== 'object') return '';
  const value = (entry as Record<string, unknown>).value;
  return typeof value === 'string' ? value.trim() : '';
}

function buildVisibleLearningBrief(profile: BackendProfile | null | undefined): VisibleLearningBrief | null {
  const baseline = confirmedProfileValue(profile, 'knowledge_baseline');
  const preference = confirmedProfileValue(profile, 'learning_preference');
  const rhythm = confirmedProfileValue(profile, 'study_rhythm');
  if (!baseline && !preference && !rhythm) return null;

  const remembered = baseline
    ? `你告诉我：${baseline}`
    : preference
      ? `你更适合：${preference}`
      : `你的学习节奏是：${rhythm}`;
  const decision = preference
    ? `所以这一轮我会优先按「${preference}」来安排，不让你从不适合的讲法开始。`
    : baseline
      ? '所以这一轮会先从你已经会的部分接上，再把不稳的地方拆开练。'
      : '所以我会把今天的任务收在你能稳定完成的范围内。';
  return { remembered, decision, cadence: rhythm || null };
}

export function DashboardPage() {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const { data, loading, error, retry } = useRequest(() => moduleApi.dashboard(), []);
  const { data: todos } = useRequest(() => moduleApi.todos(), []);
  const { data: activities } = useRequest(() => moduleApi.activities(), []);
  const { data: independentSuggestions } = useRequest(() => moduleApi.suggestions(), []);
  const [todoStatus, setTodoStatus] = useState<Record<number, string>>({});
  const [recentNotes, setRecentNotes] = useState<NoteItem[]>([]);
  const [announcements, setAnnouncements] = useState<Array<{ id: number; title: string; level: string; published_at: string | null }>>([]);
  const [artifacts, setArtifacts] = useState<Array<{ id: number; title: string; type: string; created_at: string | null }>>([]);

  const chapters = data?.learningPath?.chapters ?? [];
  const activeChapter = chapters.find((chapter) => chapter.task_status === 'in_progress' || chapter.stage === 'active') ?? chapters.find((chapter) => clampPercent(chapter.progress_pct) < 100) ?? chapters[0];
  const completedChapters = chapters.filter((chapter) => clampPercent(chapter.progress_pct) >= 100).length;
  const totalChapters = chapters.length;
  const weakTopics = Object.keys(data?.wrongQuestions?.by_topic ?? {});
  const visibleLearningBrief = useMemo(() => buildVisibleLearningBrief(data?.profile), [data?.profile]);
  const activeProgress = clampPercent(activeChapter?.progress_pct);
  const statsProgress = data?.stats?.find((item) => item.key === 'progress')?.value;
  const overallProgress = statsProgress !== undefined
    ? clampPercent(statsProgress)
    : totalChapters
      ? Math.round(chapters.reduce((sum, chapter) => sum + clampPercent(chapter.progress_pct), 0) / totalChapters)
      : 0;

  const loadSide = useCallback(async () => {
    try {
      setRecentNotes((await listNotes({})).slice(0, 3));
    } catch {
      setRecentNotes([]);
    }
    try {
      const announcementData = await userAnnouncementApi.getActive();
      setAnnouncements((announcementData.items || []).slice(0, 2));
    } catch {
      setAnnouncements([]);
    }
    try {
      const items = await moduleApi.generatedItems({});
      setArtifacts((Array.isArray(items) ? items.slice(0, 3) : []).map((item: any) => ({
        id: item.id,
        title: item.title,
        type: item.type,
        created_at: item.created_at
      })));
    } catch {
      setArtifacts([]);
    }
  }, []);

  useEffect(() => {
    void loadSide();
  }, [loadSide]);

  const visibleTodos = todos?.length
    ? todos
    : data?.timeline?.slice(0, 4).map((item, index) => ({
      id: index + 1,
      source_id: item.id,
      title: item.title,
      description: item.description ?? '',
      due_label: item.time ?? '',
      status: item.status === 'done' ? 'done' : item.status === 'active' ? 'active' : 'pending',
      target_route: '/learning'
    } satisfies DashboardTodo));

  const visibleActivities = activities?.length
    ? activities
    : data?.timeline?.slice(0, 4).map((item, index) => ({
      id: index + 1,
      source_id: item.id,
      title: item.title,
      description: item.description ?? '',
      time_label: item.time ?? '',
      target_route: '/learning',
      opened_count: 0
    } satisfies DashboardActivity));

  const primarySuggestion = independentSuggestions?.[0];
  const recommendation: RecommendationView = useMemo(() => {
    if (primarySuggestion) {
      return {
        title: primarySuggestion.title,
        reason: primarySuggestion.description,
        method: '按建议入口继续学习并完成对应任务。',
        duration: '建议安排 10—15 分钟',
        route: primarySuggestion.target_route || '/learning',
        evidence: '后端学习建议'
      };
    }
    const weakTopic = weakTopics[0];
    if (weakTopic) {
      return {
        title: `巩固「${weakTopic}」`,
        reason: `正式错题记录中已出现「${weakTopic}」。`,
        method: '先回顾相关概念，再完成一组针对性练习。',
        duration: '建议安排 12 分钟',
        route: '/exam',
        evidence: '错题诊断'
      };
    }
    if (activeChapter && activeProgress < 100) {
      return {
        title: activeChapter.name,
        reason: `当前学习路径显示该章节进度为 ${activeProgress}%。`,
        method: '继续当前节点，完成后再进入下一章节。',
        duration: '建议安排 15 分钟',
        route: '/learning',
        evidence: '当前学习路径'
      };
    }
    if (data?.learningPlan) {
      return {
        title: `看看「${data.learningPlan.title}」`,
        reason: '学习计划正在准备中，节点生成后就能从这里开始第一步。',
        method: '到「我的学习」查看计划进度，节点就绪后进入学习。',
        duration: '约 1 分钟',
        route: '/learning',
        evidence: '你的学习计划'
      };
    }
    return {
      title: '先告诉柿小吉你想学什么',
      reason: '目前缺少足够的路径、错题或画像证据，系统不会编造推荐理由。',
      method: '用一两句话说说你的目标，柿小吉会为你生成第一条学习路线。',
      duration: '约 1 分钟',
      route: '/ai-learning',
      evidence: '透明降级：证据不足'
    };
  }, [activeChapter, activeProgress, data?.learningPlan, data?.profile?.target_role, primarySuggestion, weakTopics]);

  const completeTodo = async (item: DashboardTodo) => {
    setTodoStatus((current) => ({ ...current, [item.id]: 'done' }));
    try {
      const updated = await moduleApi.updateTodo(item.id, 'done');
      setTodoStatus((current) => ({ ...current, [item.id]: updated.status }));
    } catch {
      setTodoStatus((current) => ({ ...current, [item.id]: item.status }));
    }
  };

  const openActivity = async (item: DashboardActivity) => {
    try {
      await moduleApi.openActivity(item.id);
    } catch {
      // Opening the destination remains available even if the activity counter cannot sync.
    }
    navigate(item.target_route || '/learning');
  };

  const applyAdvice = async (item: DashboardSuggestion) => {
    await moduleApi.applySuggestion(item.id);
    navigate(item.target_route || '/learning');
  };

  const journeyChapters = chapters.slice(0, 5);

  return (
    <div className={styles.page} data-testid="dashboard-page">
      <section className={styles.hero} data-testid="dashboard-hero-metrics">
        <div className={styles.heroCopy}>
          <span className={styles.eyebrow}>{timeGreeting()}，欢迎回来</span>
          <h1>今天从这里继续。</h1>
          <p className={styles.heroLead}>
            {activeChapter
              ? `你上次停在「${activeChapter.name}」。柿小吉已经把当前进度和下一步放在一起，不用重新寻找。`
              : data?.learningPlan
                ? `柿小吉正在为你准备「${data.learningPlan.title}」，第一个学习节点马上就好。`
                : '还没有形成稳定的学习路径。先告诉柿小吉你的目标，它会陪你建立第一条真实可执行的学习路线。'}
          </p>

          <div className={styles.focusStrip}>
            <div>
              <span>当前学习</span>
              <strong>{activeChapter?.name ?? (data?.learningPlan ? data.learningPlan.title : '建立第一条学习路径')}</strong>
              <small>{activeChapter ? `节点进度 ${activeProgress}%` : data?.learningPlan ? '学习节点准备中' : '画像完成后生成真实路径'}</small>
            </div>
            <div className={styles.focusProgress} data-testid="dashboard-primary-progress">
              <Progress percent={activeChapter ? activeProgress : 0} showInfo={false} strokeColor="#c85d2d" trailColor="#eadfd6" />
              <span>{activeChapter ? `${activeProgress}%` : '待开始'}</span>
            </div>
          </div>

          {visibleLearningBrief && (
            <section className={styles.evidenceBrief} data-testid="dashboard-evidence-brief" aria-label="柿小吉对你的学习判断">
              <span>柿小吉记住了</span>
              <p>{visibleLearningBrief.remembered}</p>
              <strong>{visibleLearningBrief.decision}</strong>
              {visibleLearningBrief.cadence && <small>今天按你的节奏：{visibleLearningBrief.cadence}</small>}
            </section>
          )}

          <div className={styles.heroActions}>
            {activeChapter ? (
              <>
                <Link className={styles.primaryAction} to="/learning">
                  <BookOpen size={18} />
                  <span>继续学习</span>
                  <ArrowRight size={16} />
                </Link>
                <Link className={styles.secondaryAction} to="/ai-learning">
                  <Bot size={18} />
                  <span>问问柿小吉</span>
                </Link>
              </>
            ) : (
              <>
                <Link className={styles.primaryAction} to="/ai-learning">
                  <Bot size={18} />
                  <span>告诉柿小吉你想学什么</span>
                  <ArrowRight size={16} />
                </Link>
                <Link className={styles.secondaryAction} to="/resources">
                  <LibraryBig size={18} />
                  <span>从资料开始学习</span>
                </Link>
              </>
            )}
          </div>
        </div>

      </section>

      {announcements[0] && (
        <button className={styles.noticeBar} type="button" onClick={() => navigate(`/announcements?open=${announcements[0].id}`)}>
          <Bell size={15} />
          <Tag color={announcements[0].level === 'important' ? 'red' : announcements[0].level === 'warning' ? 'orange' : 'default'}>
            {announcements[0].level === 'important' ? '重要' : announcements[0].level === 'warning' ? '提醒' : '公告'}
          </Tag>
          <strong>{announcements[0].title}</strong>
          <span>{shortTime(announcements[0].published_at)}</span>
          <ChevronRight size={15} />
        </button>
      )}

      <section className={styles.quickActions} data-testid="dashboard-quick-actions" aria-label="学习快捷入口">
        <Link to="/resources" className={styles.quickAction}>
          <LibraryBig size={20} />
          <div><strong>资料库</strong><span>上传并管理学习资料</span></div>
        </Link>
        <Link to="/exam" className={styles.quickAction}>
          <ClipboardCheck size={20} />
          <div><strong>练习与测评</strong><span>验证掌握度与薄弱点</span></div>
        </Link>
        <Link to="/artifacts" className={styles.quickAction}>
          <PackageOpen size={20} />
          <div><strong>学习产物</strong><span>继续查看与编辑成果</span></div>
        </Link>
        <RelaxMoment className={styles.quickActionRelax} compact />
      </section>

      <section className={styles.contentGrid}>
        <article className={styles.journeyPanel} data-testid="dashboard-overview-card">
          <header className={styles.sectionHeader}>
            <div>
              <span>{data?.learningPlan ? data.learningPlan.title : '正在进行'}</span>
              <h2>你的学习旅程</h2>
            </div>
            <Button type="link" data-testid="dashboard-overview-detail" onClick={() => navigate('/learning')}>查看完整图谱 <ArrowRight size={14} /></Button>
          </header>

          {error ? (
            <div className={styles.stateBox}>
              <strong>暂时没有获取到学习概览</strong>
              <span>不会用假数据补齐，重新连接后再展示真实进度。</span>
              <Button type="primary" onClick={retry}>重新获取</Button>
            </div>
          ) : loading ? (
            <Skeleton active paragraph={{ rows: 5 }} />
          ) : totalChapters === 0 ? (
            data?.learningPlan ? (
              <div className={styles.stateBox}>
                <strong>柿小吉正在为你准备学习计划</strong>
                <span>「{data.learningPlan.title}」的学习节点马上就好，稍后再来看看。</span>
                <Button type="primary" onClick={() => navigate('/learning')}>查看学习计划</Button>
              </div>
            ) : (
              <div className={styles.stateBox}>
                <strong>你还没有正在进行的学习计划</strong>
                <span>告诉柿小吉你想学什么，它会为你生成一条只属于你的学习路线。</span>
                <div className={styles.stateBoxActions}>
                  <Button type="primary" onClick={() => navigate('/ai-learning')}>告诉柿小吉你想学什么</Button>
                  <Button onClick={() => navigate('/resources')}>从资料开始学习</Button>
                </div>
              </div>
            )
          ) : (
            <>
              <div className={styles.journeySummary}>
                <div className={styles.overallProgress}>
                  <Progress type="circle" percent={overallProgress} size={92} strokeColor="#c85d2d" trailColor="#eadfd6" />
                  <div>
                    <span>总体进度</span>
                    <strong>{completedChapters} / {totalChapters || 0} 个章节已完成</strong>
                    <small>{statsProgress !== undefined ? '根据真实学习进度统计' : totalChapters ? '根据路径节点真实进度计算' : '等待学习路径数据'}</small>
                  </div>
                </div>
                <div className={styles.trendWrap}>
                  <span>各节点当前进度</span>
                  <TrendChart data={data?.trend} unit="%" />
                </div>
              </div>

              <div className={styles.chapterList}>
                {journeyChapters.map((chapter, index) => {
                  const progress = clampPercent(chapter.progress_pct);
                  const isActive = chapter.id === activeChapter?.id;
                  const isComplete = progress >= 100;
                  return (
                    <button key={chapter.id} type="button" className={isActive ? styles.chapterActive : styles.chapterRow} onClick={() => navigate('/learning')}>
                      <span className={styles.chapterIndex}>{isComplete ? <CheckCircle2 size={16} /> : String(index + 1).padStart(2, '0')}</span>
                      <div>
                        <strong>{chapter.name}</strong>
                        <small>{isActive ? '当前节点' : isComplete ? '已完成' : chapter.stage || '待学习'}</small>
                      </div>
                      <Progress percent={progress} showInfo={false} strokeColor={isComplete ? '#70854f' : '#c85d2d'} trailColor="#eee4dc" />
                      <em>{progress}%</em>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </article>

        <aside className={styles.recommendationPanel} data-testid="dashboard-recommendation">
          <div className={styles.sectionHeaderCompact}>
            <span>下一步</span>
            <strong>{recommendation.evidence}</strong>
          </div>
          <div className={styles.recommendationIcon}><Target size={23} /></div>
          <h2>{recommendation.title}</h2>
          <p>{recommendation.reason}</p>
          <dl>
            <div><dt>怎么做</dt><dd>{recommendation.method}</dd></div>
            <div><dt>预计用时</dt><dd>{recommendation.duration}</dd></div>
          </dl>
          <Button
            data-testid="dashboard-advice-0"
            type="primary"
            onClick={() => primarySuggestion ? void applyAdvice(primarySuggestion) : navigate(recommendation.route)}
          >
            开始这一步 <ArrowRight size={14} />
          </Button>
        </aside>

        <article className={styles.recentPanel}>
          <header className={styles.sectionHeader}>
            <div><span>最近成果</span><h2>你的理解正在留下痕迹</h2></div>
            <Button type="link" onClick={() => navigate('/artifacts')}>查看全部</Button>
          </header>
          <div className={styles.recentColumns}>
            <section>
              <div className={styles.subheading}><NotebookPen size={16} /><strong>最近笔记</strong><button type="button" onClick={() => navigate('/notes')}>全部笔记</button></div>
              {recentNotes.length ? recentNotes.map((note) => (
                <button key={note.id} type="button" className={styles.recentItem} onClick={() => navigate('/notes')}>
                  <span className={styles.itemIcon}><FileText size={15} /></span>
                  <div><strong>{note.title || '无标题'}</strong><small>{note.category || '学习笔记'} · {shortTime(note.updated_at)}</small></div>
                  <ChevronRight size={15} />
                </button>
              )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有笔记" />}
            </section>
            <section>
              <div className={styles.subheading}><PackageOpen size={16} /><strong>最近产物</strong><button type="button" onClick={() => navigate('/artifacts')}>全部产物</button></div>
              {artifacts.length ? artifacts.map((item) => (
                <button key={item.id} type="button" className={styles.recentItem} onClick={() => navigate('/artifacts')}>
                  <span className={styles.itemIcon}><PackageOpen size={15} /></span>
                  <div><strong>{cleanArtifactTitle(item.title)}</strong><small>{artifactTypeLabel(item.type)} · {shortTime(item.created_at)}</small></div>
                  <ChevronRight size={15} />
                </button>
              )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无学习产物" />}
            </section>
          </div>
        </article>

        <aside className={styles.footprintPanel}>
          <header className={styles.sectionHeader}>
            <div><span>今天</span><h2>任务与学习足迹</h2></div>
          </header>

          <section className={styles.footprintSection}>
            <div className={styles.subheading}><Clock3 size={16} /><strong>待完成</strong></div>
            {visibleTodos?.length ? visibleTodos.slice(0, 3).map((item) => {
              const done = todoStatus[item.id] === 'done' || item.status === 'done';
              return (
                <div key={item.id} className={styles.todoRow}>
                  <span className={done ? styles.todoDone : styles.todoMark}>{done ? <CheckCircle2 size={15} /> : null}</span>
                  <div><strong>{item.title}</strong><small>{item.due_label || item.description}</small></div>
                  <button type="button" disabled={done} onClick={() => void completeTodo(item)}>{done ? '已完成' : '完成'}</button>
                </div>
              );
            }) : <p className={styles.emptyText}>今天暂时没有待办。</p>}
          </section>

          <section className={styles.footprintSection}>
            <div className={styles.subheading}><TrendingUp size={16} /><strong>最近活动</strong></div>
            {visibleActivities?.length ? visibleActivities.slice(0, 3).map((item) => (
              <button
                data-testid={`dashboard-activity-${item.source_id || item.id}`}
                key={item.id}
                type="button"
                className={styles.activityRow}
                onClick={() => void openActivity(item)}
              >
                <span className={styles.activityDot} />
                <div><strong>{item.title}</strong><small>{item.time_label || item.description}</small></div>
                <ChevronRight size={15} />
              </button>
            )) : <p className={styles.emptyText}>完成一次学习后，这里会留下真实记录。</p>}
          </section>
        </aside>
      </section>
    </div>
  );
}
