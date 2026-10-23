import { deleteData, getData, http, postData } from './client';
import type { PageQuery, PageResult, ResourceItem, StatCard, TimelineItem } from '../types/api';
import { useAuthStore } from '../stores/authStore';
import { artifactGroup, isArtifactRecord } from '../utils/artifactInsights';
import { loadPrimaryPlan, planToLearningPath, summarizePlan } from './primaryPlan';
import type { PrimaryPlanSummary } from './primaryPlan';

export interface ModuleOverview {
  stats: StatCard[];
  primary?: Record<string, unknown>;
  trend?: Array<{ label: string; value: number }>;
  radar?: Array<{ label: string; value: number }>;
}

export interface ModulePayload extends ModuleOverview {
  timeline?: TimelineItem[];
  resources?: ResourceItem[];
  learningPath?: LearningPathPayload;
  learningPlan?: PrimaryPlanSummary | null;
  materialLibrary?: MaterialLibraryPayload;
  materialDetail?: MaterialDetailPayload;
  videos?: VideoItem[];
  exams?: ExamItem[];
  wrongQuestions?: WrongQuestionsPayload;
  chatSessions?: ChatSessionItem[];
  generatedItems?: GeneratedItem[];
  profile?: BackendProfile | null;
  weakPoints?: Array<{ label: string; value: number | null; meta?: string }>;
  habits?: Array<{ label: string; value: string; desc: string }>;
  suggestions?: Array<{ title: string; desc: string }>;
  paths?: Array<{ title: string; meta: string }>;
  reportMetrics?: Array<{ label: string; value: number; compare?: string }>;
  reportRecords?: Array<{ title: string; time?: string; type?: string }>;
  aiSummary?: string[];
  profileDimensionEvidence?: Record<string, boolean>;
  agentRegistry?: AgentCardItem[];
  agentContext?: AgentContextPayload;
  learningWorkspace?: LearningWorkspacePayload;
}

export interface SearchResultItem {
  id?: string | number;
  title?: string;
  name?: string;
  type?: string;
  resource_type?: string;
  content?: string;
  snippet?: string;
  score?: number;
}

export interface DashboardNotification {
  id: number;
  title: string;
  content: string;
  target_route: string;
  is_read: boolean;
}

export interface DashboardTodo {
  id: number;
  source_id?: string | null;
  title: string;
  description: string;
  due_label: string;
  status: string;
  target_route: string;
}

export interface DashboardActivity {
  id: number;
  source_id?: string | null;
  title: string;
  description: string;
  time_label: string;
  target_route: string;
  opened_count: number;
}

export interface DashboardSuggestion {
  id: number;
  title: string;
  description: string;
  target_route: string;
  status: string;
}

export type LessonStatus = 'locked' | 'not_started' | 'in_progress' | 'completed';
export type MaterialSource = 'official' | 'user' | 'agent';

export interface LearningChapter {
  id: string;
  order: number;
  name: string;
  description?: string;
  difficulty?: number;
  mastery?: number;
  stage?: string;
  progress_pct?: number;
  task_id?: number | null;
  task_status?: string | null;
  has_material?: boolean;
  has_exam?: boolean;
  material_count?: number;
  resource_count?: number;
}

export interface LearningPathPayload {
  user_id: number;
  total_chapters: number;
  chapters: LearningChapter[];
}

export interface LearningLesson {
  id: string;
  chapter_id: string;
  knowledge_point_id: string;
  title: string;
  order: number;
  estimated_minutes: number;
  status: LessonStatus;
  progress_pct: number;
  time_spent_minutes: number;
}

export interface LearningTask {
  id: number;
  chapter_id: string;
  title: string;
  description: string;
  status: string;
  duration_min: number;
}

export interface LearningAchievement {
  key: string;
  label: string;
  value: number;
  unit?: string;
}

export interface LearningWorkspacePayload {
  user_id: number;
  current_chapter_id: string;
  chapters: LearningChapter[];
  lessons: LearningLesson[];
  today_tasks: LearningTask[];
  weekly_activity: Array<{ label: string; value: number }>;
  achievements: LearningAchievement[];
  summary: {
    total_progress: number;
    completed_chapters: number;
    total_chapters: number;
    recent_status: string;
  };
}

export interface MaterialChapter {
  id: string;
  name: string;
  mastery?: number;
  stage?: string;
  stage_label?: string;
  has_material?: boolean;
  material_id?: number | null;
  material_count?: number;
  material_created?: string | null;
  review_count?: number;
  weak_points?: Array<{ id: number; point: string; count: number }>;
  is_due_review?: boolean;
}

export interface MaterialLibraryPayload {
  chapters: MaterialChapter[];
  due_reviews?: Array<{ chapter_id: string; next_review?: string | null }>;
  total_materials: number;
  total_weak_points: number;
}

export interface MaterialDetailPayload {
  chapter_id: string;
  chapter?: { name?: string; knowledge_points?: Array<{ name: string }> };
  mastery?: number;
  stage?: string;
  material?: {
    id: number;
    content?: {
      document?: { title?: string; content?: string; sections?: Array<{ id: string; title: string; content: string }> };
      questions?: unknown[];
    };
    created_at?: string | null;
  } | null;
  all_materials?: unknown[];
  mastery_history?: unknown[];
  weak_points?: Array<{ id: number; point: string; count: number }>;
}

export interface VideoItem {
  id: number;
  title?: string | null;
  topic?: string | null;
  transcript?: string | null;
  video_url?: string | null;
  video_result?: { script?: string; transcript?: string; slides?: Array<Record<string, unknown>> } | null;
  slides?: Array<Record<string, unknown>> | null;
  llm_provider?: string | null;
  mode?: string | null;
  created_at?: string | null;
}

export interface VideoListPayload {
  success: boolean;
  data: VideoItem[];
  count: number;
  source: string;
}

export interface VideoSearchResultItem {
  title?: string;
  platform?: string;
  url?: string;
  cover?: string | null;
  snippet?: string | null;
  image_status?: string;
  image_note?: string;
}

export interface VideoSearchResponse {
  success: boolean;
  results: VideoSearchResultItem[];
  count: number;
  source: string;
  diagnostics?: string[];
  elapsed_ms?: number;
  query: string;
  platforms: string[];
}

export interface ExamItem {
  id: number;
  exam_type: string;
  chapter_ids: string[];
  score?: number | null;
  total_score: number;
  status: string;
  time_limit_min: number;
  time_spent_sec?: number;
  question_count?: number;
  created_at: string;
  completed_at?: string | null;
}

export interface WrongQuestionsPayload {
  total: number;
  by_topic: Record<string, Array<{ question_id: string; time_spent_sec: number; source: string; created_at: string }>>;
}

export interface ChatSessionItem {
  id: number;
  thread_id: string;
  messages?: Array<{ role: string; content: string }>;
  message_count: number;
  created_at: string;
  last_message_at: string;
}

export interface GeneratedItem {
  id: number;
  type: string;
  title: string;
  chapter?: string;
  content?: string;
  raw_data?: Record<string, unknown> | null;
  created_at: string;
}

export interface GeneratedArtifactRequest {
  topic: string;
  count?: number;
  style?: string;
  mode?: string;
}

export interface TutorAskRequest {
  question: string;
  context?: Record<string, unknown>;
}

export interface BackendProfile {
  user_id: number;
  major?: string | null;
  grade_level?: string | null;
  target_role?: string | null;
  preferred_language?: string | null;
  capability_matrix?: Record<string, Record<string, { score?: number; confidence?: number; notes?: string }>>;
  behavior_pattern?: Record<string, unknown>;
  motivation_profile?: Record<string, unknown>;
  cognitive_profile?: Record<string, unknown>;
  skill_matrix?: Record<string, unknown>;
  confidence_score?: number | null;
  overall_score?: number | null;
  profile_version?: number;
  profile_data?: Record<string, unknown>;
  profile_status?: string;
  updated_at?: string | null;
}

export interface AgentRegistryItem {
  short_name: string;
  class: string;
  produces?: string | null;
  depends_on?: string[];
}

export interface AgentContextPayload {
  user_id: number;
  ctx: Record<string, unknown>;
  available_fields?: string[];
}

export interface AgentCardItem {
  name: string;
  desc: string;
  status: string;
  tone: 'online' | 'busy' | 'idle';
  target: string;
  produces?: string | null;
  dependsOn?: string[];
}

function currentUserId() {
  const { user } = useAuthStore.getState();
  if (user?.userId) return user.userId;
  const stored = localStorage.getItem('kaki_user');
  if (stored) {
    const parsed = JSON.parse(stored) as { userId?: number; user_id?: number; id?: number };
    const id = parsed.userId ?? parsed.user_id ?? parsed.id;
    if (id) return id;
  }
  throw new Error('Missing current user id');
}

function statusFromChapter(chapter: LearningChapter): LessonStatus {
  if (chapter.stage === 'completed' || chapter.task_status === 'completed' || Number(chapter.progress_pct ?? 0) >= 100) return 'completed';
  if (chapter.task_status === 'in_progress' || chapter.stage === 'active' || chapter.stage === 'partial') return 'in_progress';
  if (chapter.stage === 'inactive' || chapter.stage === 'locked') return 'locked';
  return 'not_started';
}

function formatDate(value?: string | null) {
  if (!value) return undefined;
  return value.replace('T', ' ').slice(0, 16);
}

function materialSource(chapter: MaterialChapter): MaterialSource {
  if (!chapter.has_material) return 'official';
  if ((chapter.material_count ?? 0) > 1 || chapter.stage === 'agent') return 'agent';
  return 'official';
}

function mapLearningOverview(path: LearningPathPayload): ModulePayload {
  const chapters = path.chapters ?? [];
  const completed = chapters.filter((chapter) => statusFromChapter(chapter) === 'completed').length;
  const active = chapters.find((chapter) => statusFromChapter(chapter) === 'in_progress') ?? chapters[0];
  const progress = chapters.length
    ? Math.round(chapters.reduce((sum, chapter) => sum + Number(chapter.progress_pct ?? 0), 0) / chapters.length)
    : 0;

  return {
    learningPath: path,
    stats: [
      { key: 'course', label: '当前课程', value: active?.name ?? '暂无课程' },
      { key: 'progress', label: '总体进度', value: progress, unit: '%' },
      { key: 'chapter', label: '已完成章节', value: `${completed} / ${chapters.length}` },
      { key: 'time', label: '最近学习', value: active?.task_status ?? '待开始' }
    ],
    timeline: chapters.map((chapter) => ({
      id: chapter.id,
      title: chapter.name,
      description: chapter.description,
      time: `${Math.round(Number(chapter.progress_pct ?? 0))}%`,
      status: statusFromChapter(chapter) === 'completed'
        ? 'done'
        : statusFromChapter(chapter) === 'in_progress'
          ? 'active'
          : statusFromChapter(chapter) === 'locked'
            ? 'locked'
            : 'pending'
    })),
    trend: chapters.slice(0, 6).map((chapter) => ({ label: String(chapter.order), value: Math.round(Number(chapter.progress_pct ?? 0)) }))
  };
}

function mapLearningWorkspace(workspace: LearningWorkspacePayload): ModulePayload {
  const chapters = workspace.chapters ?? [];
  const current = chapters.find((chapter) => chapter.id === workspace.current_chapter_id) ?? chapters[0];
  const summary = workspace.summary ?? {
    total_progress: 0,
    completed_chapters: 0,
    total_chapters: chapters.length,
    recent_status: 'not_started'
  };

  return {
    learningWorkspace: workspace,
    learningPath: {
      user_id: workspace.user_id,
      total_chapters: chapters.length,
      chapters
    },
    stats: [
      { key: 'course', label: '当前课程', value: current?.name ?? '暂无课程' },
      { key: 'progress', label: '总体进度', value: summary.total_progress, unit: '%' },
      { key: 'chapter', label: '已完成章节', value: `${summary.completed_chapters} / ${summary.total_chapters}` },
      { key: 'time', label: '最近学习', value: summary.recent_status === 'in_progress' ? '进行中' : summary.recent_status === 'completed' ? '已完成' : '待开始' }
    ],
    timeline: chapters.map((chapter) => ({
      id: chapter.id,
      title: chapter.name,
      description: chapter.description,
      time: `${Math.round(Number(chapter.progress_pct ?? 0))}%`,
      status: statusFromChapter(chapter) === 'completed'
        ? 'done'
        : statusFromChapter(chapter) === 'in_progress'
          ? 'active'
          : statusFromChapter(chapter) === 'locked'
            ? 'locked'
            : 'pending'
    })),
    trend: workspace.weekly_activity ?? []
  };
}

function mapMaterialLibrary(library: MaterialLibraryPayload): ModulePayload {
  const chapters = library.chapters ?? [];
  return {
    materialLibrary: library,
    stats: [
      { key: 'total', label: '全部资料', value: library.total_materials },
      { key: 'chapter', label: '章节数量', value: chapters.length },
      { key: 'agent', label: '待复习章节', value: library.due_reviews?.length ?? 0 },
      { key: 'weak', label: '薄弱点', value: library.total_weak_points }
    ],
    resources: chapters.map((chapter) => ({
      id: chapter.id,
      chapterId: chapter.id,
      materialId: chapter.material_id,
      name: chapter.has_material ? `${chapter.name} 资料` : `${chapter.name}（待生成）`,
      chapter: chapter.name,
      type: chapter.stage_label ?? chapter.stage ?? '资料',
      source: materialSource(chapter),
      updatedAt: formatDate(chapter.material_created),
      status: chapter.has_material ? 'ready' : 'empty',
      hasMaterial: chapter.has_material,
      progress: Math.round(Number(chapter.mastery ?? 0) * 20)
    }))
  };
}

async function learningOverview() {
  try {
    return mapLearningWorkspace(await getData<LearningWorkspacePayload>('/api/learning/workspace'));
  } catch {
    return mapLearningOverview(planToLearningPath(await loadPrimaryPlan(), currentUserId()));
  }
}

async function materialOverview() {
  const userId = currentUserId();
  const [library, realMaterials, items, profile, learningPath] = await Promise.all([
    getData<MaterialLibraryPayload>(`/api/materials/library/${currentUserId()}`),
    getData<Array<{ parse_status: string }>>('/api/chat/materials/library'),
    getData<GeneratedItem[]>('/api/items/list'),
    getData<BackendProfile | null>(`/api/profile/${userId}`).catch(() => null),
    loadPrimaryPlan().then((plan) => (plan ? planToLearningPath(plan, userId) : undefined))
  ]);
  const mapped = mapMaterialLibrary(library);
  const ready = realMaterials.filter((item) => item.parse_status === 'ready').length;
  const parsing = realMaterials.filter((item) => item.parse_status === 'parsing').length;
  const failed = realMaterials.filter((item) => item.parse_status === 'failed').length;
  return {
    ...mapped,
    stats: [
      { key: 'total', label: '已上传资料', value: realMaterials.length, unit: '份', tone: 'orange' },
      { key: 'ready', label: '可使用', value: ready, unit: '份', tone: 'green', compare: ready ? '文件已解析，可直接使用' : undefined },
      { key: 'parsing', label: '解析中', value: parsing, unit: '份', tone: 'amber', compare: parsing ? '解析完成后即可引用' : undefined },
      { key: 'failed', label: '解析失败', value: failed, unit: '份', tone: 'red', compare: failed ? '可重新上传' : undefined },
    ] satisfies StatCard[],
    materialLibrary: { ...library, total_materials: realMaterials.length, total_weak_points: 0 },
    profile,
    learningPath,
    generatedItems: items.filter((item) => item.type.includes('document') || item.type.includes('mindmap') || item.type.includes('ppt') || item.type.includes('question'))
  };
}

async function materialList(query: PageQuery) {
  const payload = mapMaterialLibrary(await getData<MaterialLibraryPayload>(`/api/materials/library/${currentUserId()}`));
  const keyword = String(query.keyword ?? '').trim().toLowerCase();
  const all = (payload.resources ?? []).filter((item) => (
    !keyword || item.name.toLowerCase().includes(keyword) || item.chapter?.toLowerCase().includes(keyword)
  ));
  const page = Number(query.page ?? 1);
  const pageSize = Number(query.pageSize ?? 10);
  return {
    list: all.slice((page - 1) * pageSize, page * pageSize),
    total: all.length
  };
}

function mapVideoOverview(videos: VideoItem[], source = 'backend', generatedItems: GeneratedItem[] = []): ModulePayload {
  const first = videos[0];
  const transcript = first?.transcript ?? first?.video_result?.script ?? first?.video_result?.transcript ?? '';
  const sourceLabel = source === 'backend' ? '后端学习路径' : source === 'frontend' ? '前端缓存' : source;
  return {
    videos,
    generatedItems,
    stats: [
      { key: 'videos', label: '历史视频', value: videos.length },
      { key: 'source', label: '数据来源', value: sourceLabel },
      { key: 'current', label: '当前视频', value: first?.topic ?? first?.title ?? '暂无视频' },
      { key: 'script', label: '脚本字数', value: transcript.length }
    ],
    timeline: videos.map((video, index) => ({
      id: String(video.id),
      title: video.topic ?? video.title ?? `视频 ${index + 1}`,
      description: video.title ?? undefined,
      time: formatDate(video.created_at),
      status: index === 0 ? 'active' : 'done'
    }))
  };
}

function mapExamOverview(exams: ExamItem[], wrongQuestions?: WrongQuestionsPayload): ModulePayload {
  const completed = exams.filter((exam) => exam.status === 'completed');
  const average = completed.length
    ? Math.round(completed.reduce((sum, exam) => sum + Number(exam.score ?? 0), 0) / completed.length)
    : 0;
  return {
    exams,
    wrongQuestions,
    stats: [
      { key: 'done', label: '已完成考试', value: completed.length },
      { key: 'score', label: '平均得分', value: average, unit: '分' },
      { key: 'wait', label: '未完成考试', value: exams.length - completed.length },
      { key: 'wrong', label: '错题记录', value: wrongQuestions?.total ?? 0 }
    ],
    trend: exams.slice(0, 7).reverse().map((exam) => ({
      label: formatDate(exam.created_at)?.slice(5, 10) ?? String(exam.id),
      value: Math.round(Number(exam.score ?? 0))
    }))
  };
}

async function videoOverview() {
  const [videos, items] = await Promise.all([
    getData<VideoItem[]>(`/api/video/list/${currentUserId()}`),
    getData<GeneratedItem[]>('/api/items/list')
  ]);
  return mapVideoOverview(videos, 'backend', items.filter((item) => item.type.includes('ppt') || item.type.includes('document') || item.type.includes('video')));
}

async function examOverview() {
  const userId = currentUserId();
  const [exams, wrongQuestions, items, plan] = await Promise.all([
    getData<ExamItem[]>(`/api/exam/list/${userId}`),
    getData<WrongQuestionsPayload>(`/api/exam/wrong-questions/${userId}`),
    getData<GeneratedItem[]>('/api/items/list'),
    loadPrimaryPlan()
  ]);
  const learningPath = plan ? planToLearningPath(plan, userId) : undefined;
  return { ...mapExamOverview(exams, wrongQuestions), learningPath, learningPlan: summarizePlan(plan), generatedItems: items.filter((item) => item.type.includes('question') || item.type.includes('mindmap')) };
}

function mapQaOverview(chatSessions: ChatSessionItem[], generatedItems: GeneratedItem[] = []): ModulePayload {
  const latest = chatSessions[0];
  const latestSummary = latest
    ? (latest.messages?.find((message) => message.role === 'user')?.content
        ?? latest.messages?.[0]?.content
        ?? `会话 ${chatSessions.length}`)
    : '暂无';
  return {
    chatSessions,
    generatedItems,
    stats: [
      { key: 'sessions', label: '答疑会话', value: chatSessions.length },
      { key: 'messages', label: '消息总数', value: chatSessions.reduce((sum, item) => sum + Number(item.message_count ?? 0), 0) },
      { key: 'latest', label: '最近答疑', value: latestSummary },
      { key: 'saved', label: '可收藏笔记', value: chatSessions.length }
    ]
  };
}

function mapNotesOverview(items: GeneratedItem[]): ModulePayload {
  return {
    generatedItems: items,
    stats: [
      { key: 'all', label: '全部笔记', value: items.length },
      { key: 'tutor', label: '答疑收藏', value: items.filter((item) => item.type.includes('tutor') || item.type.includes('chat')).length },
      { key: 'agent', label: 'Agent资料', value: items.filter((item) => item.type.includes('agent') || item.type.includes('document')).length },
      { key: 'wrong', label: '错题整理', value: items.filter((item) => item.type.includes('wrong') || item.type.includes('quiz')).length }
    ]
  };
}

async function qaOverview() {
  const [chatSessions, items] = await Promise.all([
    getData<ChatSessionItem[]>(`/api/chat/history/${currentUserId()}`),
    getData<GeneratedItem[]>('/api/items/list')
  ]);
  return mapQaOverview(chatSessions, items.filter((item) => item.type.includes('tutor') || item.type.includes('chat')));
}

async function notesOverview() {
  return mapNotesOverview(await getData<GeneratedItem[]>('/api/items/list'));
}

function profileRadar(profile?: BackendProfile | null) {
  const matrix = profile?.capability_matrix ?? {};
  const entries = Object.entries(matrix).map(([label, groups]) => {
    const scores = Object.values(groups ?? {})
      .map((item) => Number(item?.score ?? 0))
      .filter((score) => Number.isFinite(score) && score > 0);
    const score = scores.length ? scores.reduce((sum, item) => sum + item, 0) / scores.length : 0;
    return { label: localizeCapabilityLabel(label), value: Math.round(Math.max(0, Math.min(100, score * 20))) };
  });
  return entries;
}

const CAPABILITY_LABEL_ZH: Record<string, string> = {
  ml_overview: '机器学习概览',
  math_foundation: '数学基础',
  programming: '编程能力',
  supervised_learning: '监督学习',
  unsupervised_learning: '无监督学习',
  neural_networks: '神经网络',
  deep_learning: '深度学习',
  model_evaluation: '模型评估',
  advanced_interest: '前沿兴趣',
  nlp: '自然语言处理',
  cv: '计算机视觉',
  reinforcement: '强化学习',
  mlops: '机器学习运维',
  ensemble_methods: '集成方法',
  feature_engineering: '特征工程',
};

const TOPIC_LABEL_ZH: Record<string, string> = {
  '数组与字符串': '数组与字符串',
  '链表操作': '链表操作',
  '栈的应用': '栈的应用',
  '队列与循环队列': '队列与循环队列',
  'KMP算法': 'KMP 算法',
  '二叉树遍历': '二叉树遍历',
  '图的基本概念': '图的基本概念',
  '排序算法': '排序算法',
  '算法复杂度分析': '算法复杂度分析',
  '递归理解': '递归理解',
  '线性表基本操作': '线性表基本操作',
  '字符串处理': '字符串处理',
  '线性表': '线性表',
  '顺序存储': '顺序存储',
  '树的存储': '树的存储',
  '迷宫求解': '迷宫求解',
  '算法': '算法',
};

// 通用类型 / 状态 / 资源枚举的中文映射。
// 任何前端组件如果直接把数据库返回的英文字段渲染到 UI 都会先走这里。
const RESOURCE_TYPE_ZH: Record<string, string> = {
  document: '章节讲义',
  mindmap: '思维导图',
  question: '课后练习',
  ppt_interactive: '课堂互动 PPT',
  ppt_handout: 'PPT 讲义',
  video: '视频脚本',
  tutor: '答疑记录',
  note: '学习笔记',
  report: '学习报告',
  agent: '智能体生成',
  official: '官方资料',
  user: '用户上传',
  resource: '学习资源',
  material: '课程资料',
  chat: '答疑会话',
  item: '生成产物',
  quiz: '章节练习',
  wrong: '错题整理',
  exam: '考试记录',
  exam_paper: '考试试卷',
  exam_type: '考试类型',
  video_script: '视频脚本',
  learning_path: '学习路径'
};

const EXAM_TYPE_ZH: Record<string, string> = {
  real_exam: '真题测评',
  mock: '模拟考试',
  quiz: '章节练习',
  custom: '自定义组卷'
};

const STATUS_ZH: Record<string, string> = {
  ready: '已就绪',
  empty: '待生成',
  done: '已完成',
  active: '进行中',
  locked: '未解锁',
  pending: '待开始',
  not_started: '未开始',
  in_progress: '进行中',
  completed: '已完成',
  skipped: '已跳过',
  failed: '失败',
  running: '进行中',
  success: '成功',
  available: '可参加',
  unavailable: '不可用',
  published: '已发布',
  draft: '草稿',
  archived: '已归档',
  normal: '正常',
  abnormal: '异常',
  healthy: '健康'
};

const RESOURCE_ROLE_ZH: Record<string, string> = {
  agent: '智能体生成',
  official: '官方资料',
  user: '用户上传'
};

function localizeCapabilityLabel(label: string): string {
  if (!label) return '';
  if (CAPABILITY_LABEL_ZH[label]) return CAPABILITY_LABEL_ZH[label];
  if (TOPIC_LABEL_ZH[label]) return TOPIC_LABEL_ZH[label];
  return label;
}

export function localizeResourceType(value?: string | null): string {
  if (!value) return '';
  const key = String(value).toLowerCase();
  if (key === 'tutor_action:search') return '学习资料查找';
  if (key === 'tutor_action:video_search') return '学习视频查找';
  if (key.startsWith('tutor_action:')) return 'AI 导师学习动作';
  if (RESOURCE_TYPE_ZH[key]) return RESOURCE_TYPE_ZH[key];
  return value;
}

export function localizeGeneratedItemTitle(item: Pick<GeneratedItem, 'type' | 'title'>): string {
  const type = String(item.type ?? '').toLowerCase();
  const title = String(item.title ?? '').trim();
  if (type === 'tutor_action:search') {
    const topic = title.replace(/^Search resources\s*-\s*/i, '').trim();
    return topic ? `为你查找了「${topic}」相关学习资料` : '为你查找了相关学习资料';
  }
  if (type === 'tutor_action:video_search') {
    const topic = title.replace(/^Search videos\s*-\s*/i, '').trim();
    return topic ? `为你查找了「${topic}」相关学习视频` : '为你查找了相关学习视频';
  }
  return title || '学习记录';
}

export function localizeExamType(value?: string | null): string {
  if (!value) return '';
  if (EXAM_TYPE_ZH[value]) return EXAM_TYPE_ZH[value];
  return value;
}

export function localizeStatus(value?: string | null): string {
  if (!value) return '';
  if (STATUS_ZH[value]) return STATUS_ZH[value];
  return value;
}

export function localizeSource(value?: string | null): string {
  if (!value) return '';
  if (RESOURCE_ROLE_ZH[value]) return RESOURCE_ROLE_ZH[value];
  return value;
}

// 把可能为枚举/JSON/英文/中文混合的"通用文本"先过一遍中文化表,
// 再 fallback 到原值,避免在表格/标签中露出原始英文。
export function localizeGenericText(value?: string | null): string {
  if (!value) return '';
  const text = String(value).trim();
  if (!text) return '';
  if (RESOURCE_TYPE_ZH[text]) return RESOURCE_TYPE_ZH[text];
  if (EXAM_TYPE_ZH[text]) return EXAM_TYPE_ZH[text];
  if (STATUS_ZH[text]) return STATUS_ZH[text];
  if (RESOURCE_ROLE_ZH[text]) return RESOURCE_ROLE_ZH[text];
  if (CAPABILITY_LABEL_ZH[text]) return CAPABILITY_LABEL_ZH[text];
  if (TOPIC_LABEL_ZH[text]) return TOPIC_LABEL_ZH[text];
  return text;
}

function extractAiSummary(item: GeneratedItem): string {
  const raw = (item.raw_data ?? {}) as Record<string, unknown>;
  const contentText = String(item.content ?? '').trim();

  // 1) 显式 summary 字段
  if (typeof raw.summary === 'string' && raw.summary.trim()) return raw.summary.trim();

  // 2) 解析 content JSON
  let parsed: any = null;
  if (contentText.startsWith('{') || contentText.startsWith('[')) {
    try { parsed = JSON.parse(contentText); } catch { parsed = null; }
  }

  if (parsed && typeof parsed === 'object') {
    if (typeof parsed.tutor_response === 'string' && parsed.tutor_response.trim()) {
      return String(parsed.title ?? item.title ?? '答疑摘要') + '：' + parsed.tutor_response.trim().slice(0, 120) + (parsed.tutor_response.length > 120 ? '…' : '');
    }
    if (typeof parsed.summary === 'string' && parsed.summary.trim()) {
      return parsed.summary.trim().slice(0, 180);
    }
    if (Array.isArray(parsed.questions) && parsed.questions.length) {
      return `${item.title || '课后练习'} 共 ${parsed.questions.length} 道题，覆盖章节重点。`;
    }
    if (typeof parsed.central === 'string') {
      const branches = Array.isArray(parsed.branches) ? parsed.branches.length : 0;
      return `思维导图《${parsed.central}》共 ${branches} 个分支，串起本章核心脉络。`;
    }
    if (typeof parsed.title === 'string' && typeof parsed.document === 'string') {
      return parsed.title + '：' + String(parsed.document).slice(0, 140);
    }
  }

  // 3) 兜底：取前 140 个非 JSON 字符
  if (contentText && !contentText.startsWith('{') && !contentText.startsWith('[')) {
    return contentText.slice(0, 140) + (contentText.length > 140 ? '…' : '');
  }

  // 4) 最后兜底用 title
  return String(item.title ?? '').trim();
}

function profileWeakPoints(profile?: BackendProfile | null, wrongQuestions?: WrongQuestionsPayload) {
  const fromProfile = profileRadar(profile)
    .filter((item) => item.value < 70)
    .map((item) => ({ label: item.label, value: item.value, meta: `掌握率 ${item.value}%` }));
  const fromWrong = Object.keys(wrongQuestions?.by_topic ?? {}).map((label) => ({
    label: localizeCapabilityLabel(label),
    value: null,
    meta: `错题 ${wrongQuestions?.by_topic[label]?.length ?? 0} 次 · 尚无掌握率证据`,
  }));
  return fromProfile.length ? fromProfile : fromWrong;
}

function mapProfileOverview(profile: BackendProfile | null, wrongQuestions?: WrongQuestionsPayload): ModulePayload {
  const radar = profileRadar(profile);
  const weakPoints = profileWeakPoints(profile, wrongQuestions);

  // 从 normalized_profile 读取规范化数据（新），回退到旧字段
  const pd = (profile?.profile_data ?? {}) as Record<string, unknown>;
  const np = (pd.normalized_profile ?? {}) as Record<string, unknown>;
  const rhythm = (np.study_rhythm ?? {}) as Record<string, unknown>;
  const motivation = (np.motivation ?? {}) as Record<string, unknown>;
  const goal = (np.learning_goal ?? {}) as Record<string, unknown>;
  const modalities = (np.preferred_modalities ?? []) as string[];

  // 画像完整度
  const completeness = (pd.completeness ?? 0) as number;

  // habits: 优先从 normalized_profile 读取，回退到旧 behavior_pattern
  const dailyMinutes = (rhythm.daily_minutes as number) ?? (profile?.behavior_pattern as Record<string, unknown>)?.avg_session_minutes as number | undefined;
  const preferredPeriod = (rhythm.preferred_period as string) ?? '待记录';
  const periodLabel: Record<string, string> = { morning: '上午', afternoon: '下午', evening: '晚上', weekend: '周末', flexible: '灵活' };
  const motivationPrimary = motivation.primary as string | undefined;
  const motivationLabel: Record<string, string> = { job_hunting: '求职就业', research: '学术研究', career_change: '转行提升', curiosity: '兴趣驱动', exam: '考试备考' };
  const modalityLabel: Record<string, string> = { example_first: '先例后理', practice: '边做边学', theory_first: '理论优先', visual: '可视化' };

  return {
    profile,
    weakPoints,
    radar,
    stats: [
      { key: 'major', label: '学习目标', value: (goal.primary as string) ?? profile?.target_role ?? profile?.major ?? '暂无画像' },
      { key: 'score', label: '综合能力', value: profile?.overall_score == null ? '数据积累中' : Math.round(Number(profile.overall_score)), unit: profile?.overall_score == null ? '' : '分' },
      { key: 'confidence', label: '了解程度', value: pd.completeness == null ? '数据积累中' : Math.round(completeness * 100), unit: pd.completeness == null ? '' : '%' },
      { key: 'version', label: '画像更新', value: profile?.updated_at ? String(profile.updated_at).replace('T', ' ').slice(5, 16) : '等待第一次记录' }
    ],
    habits: [
      { label: '学习时段', value: periodLabel[preferredPeriod] ?? preferredPeriod, desc: '偏好时段' },
      { label: '每日学习', value: dailyMinutes ? String(dailyMinutes) : '待记录', desc: '分钟' },
      { label: '偏好方式', value: modalities.length ? modalities.slice(0, 2).map((m: string) => modalityLabel[m] ?? m).join('+') : '待识别', desc: '学习形式' },
      { label: '学习动机', value: motivationPrimary ? (motivationLabel[motivationPrimary] ?? motivationPrimary) : '待识别', desc: (motivation.description as string) ?? profile?.target_role ?? '' }
    ],
    suggestions: weakPoints.slice(0, 3).map((item) => ({ title: `强化${item.label}`, desc: item.meta ?? '来自你的练习记录。' })),
    paths: weakPoints.slice(0, 3).map((item, index) => ({ title: `${item.label} 提升路径`, meta: `优先级 ${index + 1} · 根据你的薄弱点推荐` })),
    trend: radar.map((item) => ({ label: item.label.slice(0, 4), value: item.value }))
  };
}

async function profileOverview() {
  const userId = currentUserId();
  const [profile, wrongQuestions, plan, exams, chats, items] = await Promise.all([
    getData<BackendProfile | null>(`/api/profile/${userId}`),
    getData<WrongQuestionsPayload>(`/api/exam/wrong-questions/${userId}`),
    loadPrimaryPlan(),
    getData<ExamItem[]>(`/api/exam/list/${userId}`),
    getData<ChatSessionItem[]>(`/api/chat/history/${userId}`),
    getData<GeneratedItem[]>('/api/items/list')
  ]);
  const learning = planToLearningPath(plan, userId);
  const base = mapProfileOverview(profile, wrongQuestions);
  const chapters = learning.chapters ?? [];
  const chapterProgress = chapters.length
    ? Math.round(chapters.reduce((sum, chapter) => sum + Number(chapter.progress_pct ?? 0), 0) / chapters.length)
    : 0;
  const completedExams = exams.filter((exam) => exam.status === 'completed' && exam.score != null);
  const examAverage = completedExams.length
    ? Math.round(completedExams.reduce((sum, exam) => sum + Number(exam.score ?? 0), 0) / completedExams.length)
    : 0;
  const capabilityValues = (base.radar ?? []).map((item) => Number(item.value || 0)).filter((value) => value > 0);
  const capabilityAverage = capabilityValues.length
    ? Math.round(capabilityValues.reduce((sum, value) => sum + value, 0) / capabilityValues.length)
    : 0;
  // behavior 优先从 normalized_profile 读取，回退到旧字段（已通过后端适配器自动填充）
  const pdOverview = (profile?.profile_data ?? {}) as Record<string, unknown>;
  const npOverview = (pdOverview.normalized_profile ?? {}) as Record<string, unknown>;
  const npRhythm = (npOverview.study_rhythm ?? {}) as Record<string, unknown>;
  const behavior = profile?.behavior_pattern ?? {};
  const weeklyHours = Number(behavior.weekly_hours ?? (npRhythm.daily_minutes ? Math.round(Number(npRhythm.daily_minutes) * 5 / 60) : 0));
  const reviewEvidence = Number(behavior.review_count ?? behavior.review_frequency_count ?? 0);
  const onboarding = profile?.profile_data?.profile_onboarding as { entries?: Record<string, unknown> } | undefined;
  const confirmedEntries = Object.keys(onboarding?.entries ?? {}).length;
  const radar = [
    { label: '知识掌握', value: chapterProgress },
    { label: '理解迁移', value: capabilityAverage },
    { label: '练习表现', value: examAverage },
    { label: '学习节奏', value: weeklyHours > 0 ? Math.min(100, Math.round(weeklyHours * 10)) : 0 },
    { label: '复习习惯', value: reviewEvidence > 0 ? Math.min(100, reviewEvidence * 10) : 0 },
    { label: '目标动力', value: confirmedEntries ? Math.round((confirmedEntries / 6) * 100) : 0 }
  ];
  const cognitive = profile?.cognitive_profile ?? {};
  const motivation = profile?.motivation_profile ?? {};
  const hasAny = (source: Record<string, unknown>, keys: string[]) => keys.some((key) => source[key] != null);
  const profileDimensionEvidence = {
    knowledge: chapters.length > 0,
    concept: capabilityValues.length > 0 || hasAny(cognitive, ['concept_depth', 'concept_understanding']),
    transfer: hasAny(cognitive, ['knowledge_transfer', 'transfer_ability']),
    memory: reviewEvidence > 0 || hasAny(cognitive, ['memory_retention', 'retention']),
    problem: completedExams.length > 0 || hasAny(cognitive, ['problem_solving', 'problem_solving_ability']),
    efficiency: hasAny(behavior, ['learning_efficiency', 'efficiency_score']),
    metacognition: hasAny(cognitive, ['metacognition', 'self_assessment']),
    initiative: hasAny(motivation, ['initiative', 'execution', 'learning_initiative']),
    rhythm: weeklyHours > 0 || hasAny(behavior, ['rhythm_score', 'persistence']),
    preference: Boolean(profile?.preferred_language || profile?.target_role || Object.keys(motivation).length)
  };
  const aiSummary = items.map((item) => extractAiSummary(item)).filter(Boolean).slice(0, 3);
  const planSummary = summarizePlan(plan);
  return {
    ...base,
    paths: planSummary
      ? [{ title: planSummary.title, meta: `${planSummary.totalNodes} 个学习节点 · 进度 ${planSummary.progressPct}%` }, ...(base.paths ?? [])]
      : base.paths,
    radar,
    profileDimensionEvidence,
    generatedItems: items,
    reportMetrics: [
      { label: '当前路径进度', value: chapterProgress },
      { label: '正式测评均分', value: examAverage },
      { label: '目标明确度', value: radar[5].value }
    ],
    reportRecords: [
      ...items.slice(0, 3).map((item) => ({ title: localizeGeneratedItemTitle(item), time: formatDate(item.created_at), type: localizeResourceType(item.type) })),
      ...chats.slice(0, 2).map((chat) => ({ title: chat.messages?.[0]?.content ?? chat.thread_id, time: formatDate(chat.last_message_at), type: 'AI 学习' }))
    ],
    aiSummary
  } satisfies ModulePayload;
}

async function agentOverview() {
  const userId = currentUserId();
  const [registry, context, items] = await Promise.all([
    getData<AgentRegistryItem[]>('/api/agent/registry'),
    http.get<{ success: boolean; data?: AgentContextPayload } & AgentContextPayload>(`/api/agent/${userId}/ctx`).then((response) => response.data.data ?? response.data),
    getData<GeneratedItem[]>('/api/items/list')
  ]);
  const cards: AgentCardItem[] = registry.map((agent) => ({
    name: agent.short_name,
    desc: `产出 ${agent.produces ?? 'context'} · 依赖 ${(agent.depends_on ?? []).join(', ') || '无'}`,
    status: '在线',
    tone: 'online',
    target: agent.short_name,
    produces: agent.produces,
    dependsOn: agent.depends_on ?? []
  }));
  return {
    agentRegistry: cards,
    agentContext: context,
    generatedItems: items,
    stats: [
      { key: 'registry', label: '可用智能体', value: cards.length },
      { key: 'outputs', label: '生成产物', value: items.length },
      { key: 'fields', label: '上下文字段', value: context.available_fields?.length ?? 0 },
      { key: 'profile', label: '画像注入', value: context.ctx?.student_profile ? '已注入' : '未注入' }
    ]
  } satisfies ModulePayload;
}

async function reportOverview() {
  const userId = currentUserId();
  const [profile, plan, exams, chats, items] = await Promise.all([
    getData<BackendProfile | null>(`/api/profile/${userId}`),
    loadPrimaryPlan(),
    getData<ExamItem[]>(`/api/exam/list/${userId}`),
    getData<ChatSessionItem[]>(`/api/chat/history/${userId}`),
    getData<GeneratedItem[]>('/api/items/list')
  ]);
  const learning = planToLearningPath(plan, userId);
  const chapters = learning.chapters ?? [];
  const completed = chapters.filter((chapter) => Number(chapter.progress_pct ?? 0) >= 100).length;
  const progress = chapters.length ? Math.round(chapters.reduce((sum, chapter) => sum + Number(chapter.progress_pct ?? 0), 0) / chapters.length) : 0;
  const completedExams = exams.filter((exam) => exam.status === 'completed');
  const examAverage = completedExams.length ? Math.round(completedExams.reduce((sum, exam) => sum + Number(exam.score ?? 0), 0) / completedExams.length) : 0;
  const aiSummary = items
    .map((item) => extractAiSummary(item))
    .filter(Boolean)
    .slice(0, 3);
  return {
    profile,
    generatedItems: items,
    reportMetrics: [
      { label: '课程进度', value: progress },
      { label: '章节完成率', value: chapters.length ? Math.round((completed / chapters.length) * 100) : 0 },
      { label: '考试均分', value: examAverage }
    ],
    reportRecords: [
      ...items.slice(0, 4).map((item) => ({ title: localizeGeneratedItemTitle(item), time: formatDate(item.created_at), type: localizeResourceType(item.type) })),
      ...chats.slice(0, 2).map((chat) => ({ title: chat.messages?.[0]?.content ?? chat.thread_id, time: formatDate(chat.last_message_at), type: '答疑' }))
    ],
    aiSummary: aiSummary.length ? aiSummary : [`画像总分 ${Math.round(Number(profile?.overall_score ?? 0))}，目标 ${profile?.target_role ?? '待补充'}。`],
    radar: profileRadar(profile),
    trend: chapters.slice(0, 7).map((chapter) => ({ label: String(chapter.order), value: Math.round(Number(chapter.progress_pct ?? 0)) })),
    stats: [
      { key: 'progress', label: '课程进度', value: progress, unit: '%' },
      { key: 'exams', label: '考试记录', value: exams.length },
      { key: 'qa', label: '答疑会话', value: chats.length },
      { key: 'items', label: '生成产物', value: items.length }
    ]
  };
}

async function dashboardOverview() {
  const userId = currentUserId();
  const [plan, materials, exams, wrongQuestions, chats, items, profile] = await Promise.all([
    loadPrimaryPlan(),
    getData<MaterialLibraryPayload>(`/api/materials/library/${userId}`),
    getData<ExamItem[]>(`/api/exam/list/${userId}`),
    getData<WrongQuestionsPayload>(`/api/exam/wrong-questions/${userId}`),
    getData<ChatSessionItem[]>(`/api/chat/history/${userId}`),
    getData<GeneratedItem[]>('/api/items/list'),
    getData<BackendProfile | null>(`/api/profile/${userId}`).catch(() => null)
  ]);
  const learning = planToLearningPath(plan, userId);
  const planSummary = summarizePlan(plan);
  const chapters = learning.chapters;
  const completed = chapters.filter((chapter) => statusFromChapter(chapter) === 'completed').length;
  const progress = planSummary?.progressPct
    ?? (chapters.length ? Math.round(chapters.reduce((sum, chapter) => sum + Number(chapter.progress_pct ?? 0), 0) / chapters.length) : 0);
  const weakLabels = Object.keys(wrongQuestions.by_topic ?? {});
  return {
    learningPath: learning,
    learningPlan: planSummary,
    materialLibrary: materials,
    exams,
    wrongQuestions,
    chatSessions: chats,
    generatedItems: items,
    profile,
    stats: [
      { key: 'progress', label: '总体进度', value: progress, unit: '%' },
      { key: 'completed', label: '已完成节点', value: `${completed} / ${chapters.length}` },
      { key: 'materials', label: '资料数量', value: materials.total_materials },
      { key: 'week', label: '本周考试', value: exams.length }
    ],
    timeline: chapters.slice(0, 5).map((chapter) => ({
      id: chapter.id,
      title: chapter.name,
      description: chapter.description,
      time: `${Math.round(Number(chapter.progress_pct ?? 0))}%`,
      status: statusFromChapter(chapter) === 'completed' ? 'done' : statusFromChapter(chapter) === 'in_progress' ? 'active' : 'pending'
    })),
    trend: chapters.slice(0, 7).map((chapter) => ({ label: String(chapter.order), value: Math.round(Number(chapter.progress_pct ?? 0)) })),
    suggestions: planSummary
      ? [
          { title: planSummary.nextNodeTitle ?? planSummary.title, desc: planSummary.nextNodeTitle ? `继续推进「${planSummary.nextNodeTitle}」。` : `继续学习计划「${planSummary.title}」。` },
          ...weakLabels.slice(0, 2).map((label) => ({ title: label, desc: '来自你的错题诊断，建议优先复习。' }))
        ]
      : weakLabels.slice(0, 3).map((label) => ({ title: label, desc: '来自你的错题诊断，建议优先复习。' }))
  } satisfies ModulePayload;
}

export const moduleApi = {
  dashboard: () => dashboardOverview(),
  overview: (module: string) => {
    if (module === 'learning') return learningOverview();
    if (module === 'resources') return materialOverview();
    if (module === 'agents') return agentOverview();
    if (module === 'video') return videoOverview();
    if (module === 'exam') return examOverview();
    if (module === 'profile') return profileOverview();
    if (module === 'report') return reportOverview();
    if (module === 'qa') return qaOverview();
    if (module === 'notes') return notesOverview();
    if (module === 'artifacts') return getData<GeneratedItem[]>('/api/items/list').then((items) => {
      const artifacts = items.filter(isArtifactRecord);
      return ({
      generatedItems: artifacts,
      stats: [
        { key: 'total', label: '全部产物', value: artifacts.length, tone: 'orange' as const },
        { key: 'documents', label: '讲义与总结', value: artifacts.filter((item) => artifactGroup(item.type) === 'document').length, tone: 'green' as const },
        { key: 'practice', label: '题目与练习', value: artifacts.filter((item) => artifactGroup(item.type) === 'practice').length, tone: 'amber' as const },
        { key: 'media', label: '代码、导图与其他资源', value: artifacts.filter((item) => ['code', 'mindmap', 'presentation', 'other'].includes(artifactGroup(item.type))).length, tone: 'purple' as const }
      ]
    }); });
    return getData<ModulePayload>(`/api/${module}/overview`);
  },
  list: (module: string, query: PageQuery) => {
    if (module === 'resources') return materialList(query);
    return getData<PageResult<ResourceItem>>(`/api/${module}/list`, query);
  },
  create: <T>(module: string, body: T) => postData<unknown, T>(`/api/${module}`, body),
  remove: (module: string, id: string) => deleteData<unknown>(`/api/${module}/${id}`),
  exportReport: (type: string) => postData<{ url: string }, { type: string }>('/api/reports/export', { type }),
  startChapter: (chapterId: string) => postData<{ task_id: number; chapter_id: string; status: string }, { chapter_id: string }>('/api/learning-path/start', { chapter_id: chapterId }),
  learningWorkspace: () => getData<LearningWorkspacePayload>('/api/learning/workspace'),
  startLesson: (lessonId: string) => postData<LearningWorkspacePayload, undefined>(`/api/learning/lessons/${lessonId}/start`),
  completeLesson: (lessonId: string, timeSpentMinutes = 25) => postData<LearningWorkspacePayload, { time_spent_minutes: number }>(`/api/learning/lessons/${lessonId}/complete`, { time_spent_minutes: timeSpentMinutes }),
  updateLearningTask: (taskId: number, status: string) => http.patch<{ success: boolean; data: LearningWorkspacePayload }>(`/api/learning/tasks/${taskId}`, { status }).then((response) => response.data.data),
  materialDetail: (chapterId: string) => getData<MaterialDetailPayload>(`/api/materials/detail/${currentUserId()}/${chapterId}`),
  generateMaterial: (chapterId: string) => postData<unknown, undefined>(`/api/materials/generate/${currentUserId()}/${chapterId}`),
  generateVideo: (topic: string, mode = 'script') => postData<VideoItem, { topic: string; mode: string; duration_sec: number }>('/api/video/generate', { topic, mode, duration_sec: 60 }),
  searchExternalVideos: (params: { q: string; limit?: number; sources?: string[] }) => {
    const search = new URLSearchParams();
    search.set('q', params.q);
    search.set('limit', String(params.limit ?? 10));
    if (params.sources && params.sources.length) search.set('sources', params.sources.join(','));
    return getData<VideoSearchResponse>(`/api/video/search?${search.toString()}`);
  },
  generateDocument: (body: GeneratedArtifactRequest) => postData<unknown, { user_id: string; topic: string; count?: number; style?: string; mode?: string }>('/api/document/generate', {
    user_id: String(currentUserId()),
    topic: body.topic,
    count: body.count,
    style: body.style,
    mode: body.mode
  }),
  generateQuestionArtifact: (body: GeneratedArtifactRequest) => postData<unknown, { user_id: string; topic: string; count?: number; style?: string }>('/api/question/generate', {
    user_id: String(currentUserId()),
    topic: body.topic,
    count: body.count,
    style: body.style
  }),
  generateMindmap: (body: GeneratedArtifactRequest) => postData<unknown, { user_id: string; topic: string; count?: number; style?: string }>('/api/mindmap/generate', {
    user_id: String(currentUserId()),
    topic: body.topic,
    count: body.count,
    style: body.style
  }),
  createExam: (
    examType: string,
    chapterIds: string[],
    options?: { difficulty?: string; questionTypes?: string[]; count?: number; sourceKind?: string; specification?: string }
  ) => postData<{ id: number; exam_type: string; chapter_ids: string[]; questions: Array<Record<string, unknown>>; time_limit_min: number; total_score: number; status: string; mode_contract: Record<string, unknown> }, { user_id: number; exam_type: string; chapter_ids: string[]; difficulty?: string; question_types?: string[]; count?: number; source_kind?: string; specification?: string }>('/api/exam/create', {
    user_id: currentUserId(),
    exam_type: examType,
    chapter_ids: chapterIds,
    difficulty: options?.difficulty,
    question_types: options?.questionTypes,
    count: options?.count,
    source_kind: options?.sourceKind,
    specification: options?.specification
  }),
  getExam: (examId: number) => getData<{ id: number; exam_type: string; chapter_ids: string[]; questions: Array<Record<string, unknown>>; time_limit_min: number; total_score: number; status: string; mode_contract: Record<string, unknown>; answers?: unknown[]; score?: number; time_spent_sec?: number; grading?: Array<Record<string, unknown>> }>(`/api/exam/${examId}`),
  checkExamAnswer: (examId: number, questionIndex: number, answer: unknown) => postData<{ question_index: number; correct: boolean; earned: number; total: number; correct_answer: unknown; explanation: string }, { question_index: number; answer: unknown }>(`/api/exam/${examId}/check`, { question_index: questionIndex, answer }),
  submitExam: (examId: number, answers: unknown[], timeSpentSec = 0) => postData<{ exam_id: number; score: number; total_score: number; grading: Array<{ question_index: number; question_id: string; topic: string; answer: unknown; correct_answer: unknown; explanation?: string; correct: boolean; earned: number; total: number }>; learning_update?: { evaluation_status: string; evaluation_revision?: number | null } }, { answers: unknown[]; time_spent_sec: number }>(`/api/exam/${examId}/submit`, { answers, time_spent_sec: timeSpentSec }),
  invokeAgent: (agentName: string, input: Record<string, unknown> = {}) => postData<unknown, { thread_id: string; input: Record<string, unknown>; upstream: Record<string, unknown>; persist: boolean }>(`/api/agent/${agentName}/invoke`, {
    thread_id: `frontend-${Date.now()}`,
    input,
    upstream: {},
    persist: true
  }),
  askTutor: (message: string, threadId = `qa-${Date.now()}`) => postData<unknown, { user_id: number; message: string; thread_id: string; history: unknown[] }>('/api/chat', { user_id: currentUserId(), message, thread_id: threadId, history: [] }),
  askTutorArtifact: (body: TutorAskRequest) => postData<unknown, { user_id: string; question: string; context?: Record<string, unknown> }>('/api/tutor/ask', {
    user_id: String(currentUserId()),
    question: body.question,
    context: body.context
  }),
  saveItem: (body: { type: string; title: string; chapter?: string; content?: string; raw_data?: Record<string, unknown> }) => postData<{ id: number }, typeof body>('/api/items/save', body),
  updateItem: (id: number, body: { title?: string; chapter?: string; content?: string; raw_data?: Record<string, unknown> }) => http.patch<{ success: boolean; id: number; title: string }>(`/api/items/${id}`, body).then((response) => response.data),
  generatedItems: (params?: { q?: string; type?: string; prefix?: string }) => getData<GeneratedItem[]>('/api/items/list', params),
  deleteItem: (id: number) => deleteData<unknown>(`/api/items/${id}`),
  search: (query: string, limit = 8) => postData<SearchResultItem[], { query: string; limit: number }>('/api/search', { query, limit }),
  notifications: () => getData<DashboardNotification[]>('/api/notifications'),
  markNotificationRead: (id: number) => http.patch<{ success: boolean; data: DashboardNotification }>(`/api/notifications/${id}/read`).then((response) => response.data.data),
  todos: () => getData<DashboardTodo[]>('/api/todos'),
  updateTodo: (id: number, status: string) => http.patch<{ success: boolean; data: DashboardTodo }>(`/api/todos/${id}`, { status }).then((response) => response.data.data),
  activities: () => getData<DashboardActivity[]>('/api/activities'),
  openActivity: (id: number) => postData<DashboardActivity, undefined>(`/api/activities/${id}/open`),
  suggestions: () => getData<DashboardSuggestion[]>('/api/suggestions'),
  applySuggestion: (id: number) => postData<DashboardSuggestion, undefined>(`/api/suggestions/${id}/apply`)
};
