import { App as AntdApp, Button, Empty, Form, Input, InputNumber, Modal, Progress, Select, Space, Tabs, Tag } from 'antd';
import { Activity, AlertOctagon, ArrowRight, BarChart3, BookOpen, Bot, CalendarDays, Camera, Captions, CheckCircle2, CheckCheck, Circle, CircleCheck, ClipboardCheck, Clock3, Database, Download, FileDown, FileText, Flame, GitBranch, Hourglass, LockKeyhole, Maximize, Network, PackageOpen, Play, Plus, RefreshCw, Search, Settings, Sparkles, Star, Trophy, UploadCloud, UserRoundCheck, Volume2, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { moduleApi } from '../api/modules';
import { authApi } from '../api/auth';
import { createMistakeBook } from '../api/study';
import { useAuthStore } from '../stores/authStore';
import { UserAvatar } from '../components/UserAvatar';
import type { AgentCardItem, ChatSessionItem, ExamItem, GeneratedItem, LearningChapter, LearningLesson, LearningTask, LearningWorkspacePayload, VideoItem, VideoSearchResponse, VideoSearchResultItem, WrongQuestionsPayload } from '../api/modules';
import { PaperCard, RadarChart, TrendChart } from '../components/DataPanel';
import { ArtifactRenderer, downloadUnifiedArtifact, type UnifiedArtifact } from '../components/ArtifactRenderer';
import { PageHeader } from '../components/PageHeader';
import { ResourceTable } from '../components/ResourceTable';
import { StatStrip } from '../components/StatStrip';
import { useRequest } from '../hooks';
import { useModuleActions } from '../hooks/useModuleActions';
import type { AppRoute } from '../routes';
import {
  formatActionEventTime,
  getActionEvents,
  getAgentInteractionEvents,
  getProfileSignals,
  recordActionEvent,
  subscribeActionEvents
} from '../stores/actionEventStore';
import type { ActionEvent } from '../stores/actionEventStore';
import { artifactGroup, isArtifactRecord } from '../utils/artifactInsights';
import { buildProfileDimensions, profilePreferenceTags } from '../utils/profileDimensions';
import recommendReadingArt from '../assets/generated/practice-pack-magnifier.jpg';
import recommendPracticeArt from '../assets/generated/practice-pack-notebook.jpg';
import recommendMindmapArt from '../assets/generated/practice-pack-scroll.jpg';
import emptyPersimmonArt from '../assets/generated/empty-persimmon-basket.jpg';
import knowledgeLibraryArt from '../assets/tool-watercolor/resources.jpg';
import styles from './ModulePage.module.css';

interface ModulePageProps {
  route: AppRoute;
}

function artifactPreview(item: GeneratedItem) {
  const raw = item.raw_data ?? {};
  const candidates = [
    raw.summary,
    raw.tutor_response,
    raw.content,
    raw.title,
    item.chapter,
    item.content
  ].filter(Boolean);
  const value = String(candidates[0] ?? '');
  return value.length > 180 ? `${value.slice(0, 180)}...` : value;
}

function hasArtifactError(item: GeneratedItem) {
  const raw = item.raw_data ?? {};
  if (raw.error || raw.errors) return true;
  try {
    const parsed = item.content ? JSON.parse(item.content) as Record<string, unknown> : null;
    return Boolean(parsed?.error || parsed?.errors);
  } catch {
    return false;
  }
}

type AgentField =
  | { name: string; label: string; type: 'input'; placeholder?: string; required?: boolean }
  | { name: string; label: string; type: 'textarea'; placeholder?: string; required?: boolean; rows?: number }
  | { name: string; label: string; type: 'number'; min?: number; max?: number; required?: boolean; suffix?: string }
  | { name: string; label: string; type: 'select'; options: Array<{ value: string; label: string }>; required?: boolean }
  | { name: string; label: string; type: 'multi-select'; options: Array<{ value: string; label: string }>; required?: boolean };

type AgentMeta = {
  label: string;
  output: string;
  depends: string;
  action: string;
  module: string;
  fileType: string;
  fields: AgentField[];
  defaults: Record<string, unknown>;
};

const CHAPTER_OPTIONS = [
  { value: 'chapter-3', label: '第3章 栈、队列与字符串' },
  { value: 'chapter-4', label: '第4章 链表' },
  { value: 'chapter-5', label: '第5章 递归与分治' },
  { value: 'chapter-6', label: '第6章 树与二叉树' }
];

const ARTIFACT_CHAPTER_LABELS: Record<string, string> = CHAPTER_OPTIONS.reduce<Record<string, string>>((acc, option) => {
  acc[option.value] = option.label;
  return acc;
}, {});

function resolveArtifactChapter(chapter?: string | null): string {
  const value = String(chapter ?? '').trim();
  if (!value || value === 'AI tutor action') return '';
  if (ARTIFACT_CHAPTER_LABELS[value]) return ARTIFACT_CHAPTER_LABELS[value];
  const match = value.match(/^chapter-(\d+)$/);
  if (match) return `第${match[1]}章`;
  return value;
}

function extractNoteContent(item: GeneratedItem): string {
  if (!item) return '后端保存内容，可用于复习、导出和关联学习。';
  const raw = (item as any).raw_data ?? {};
  const contentText = String(item.content ?? '').trim();
  let parsed: any = null;
  if (contentText.startsWith('{') || contentText.startsWith('[')) {
    try { parsed = JSON.parse(contentText); } catch { parsed = null; }
  }
  if (parsed && typeof parsed === 'object') {
    if (typeof parsed.tutor_response === 'string' && parsed.tutor_response.trim()) {
      return parsed.title ? `${parsed.title}：${parsed.tutor_response}` : parsed.tutor_response;
    }
    if (Array.isArray(parsed.questions) && parsed.questions.length) {
      return `${item.title || '课后练习'}：包含 ${parsed.questions.length} 道题，涵盖本章核心概念与易错点。`;
    }
    if (typeof parsed.central === 'string') {
      const branches = Array.isArray(parsed.branches) ? parsed.branches.length : 0;
      return `思维导图《${parsed.central}》：${branches} 个分支，串起本章核心脉络。`;
    }
    if (typeof parsed.document === 'string' && parsed.document.trim()) {
      return parsed.document.slice(0, 280);
    }
  }
  if (typeof raw.content === 'string' && raw.content.trim()) return raw.content;
  if (contentText && !contentText.startsWith('{') && !contentText.startsWith('[')) {
    return contentText;
  }
  return String(item.title ?? '后端保存内容，可用于复习、导出和关联学习。');
}

function composeArtifactTitle(item: GeneratedItem): string {
  const typeLabel = artifactTypeLabel(item.type);
  let chapter = resolveArtifactChapter(item.chapter);
  let rawTitle = String(item.title ?? '').trim().replace(/^(Python)(?:\s+Python)+/i, '$1');
  // 旧数据兜底:title 仅是 chapter-N 形式 → 反向解析出章节,并把 title 视为空
  const bareMatch = rawTitle.match(/^chapter-(\d+)$/i);
  if (bareMatch) {
    const recovered = resolveArtifactChapter(`chapter-${bareMatch[1]}`);
    if (recovered) chapter = chapter || recovered;
    rawTitle = '';
  }
  if (chapter && !rawTitle.includes(chapter) && !rawTitle.startsWith('第')) {
    return `${chapter} · ${rawTitle || typeLabel}`;
  }
  return rawTitle || chapter || typeLabel;
}

function artifactFileMeta(item: GeneratedItem): { ext: string; variant: 'pdf' | 'doc'; size: string } {
  const type = String(item.type ?? '').toLowerCase();
  let ext = '成果';
  let variant: 'pdf' | 'doc' = 'doc';
  if (type.includes('ppt') || type.includes('presentation') || type.includes('interactive')) {
    ext = '课件';
    variant = 'pdf';
  } else if (type.includes('mindmap')) {
    ext = '导图';
    variant = 'doc';
  } else if (type.includes('question') || type.includes('exam')) {
    ext = '题目';
    variant = 'pdf';
  } else if (type.includes('video')) {
    ext = '视频';
    variant = 'pdf';
  } else if (type.includes('lecture') || type.includes('document') || type.includes('report')) {
    ext = '讲义';
    variant = 'doc';
  } else if (type.includes('code')) {
    ext = '代码';
    variant = 'doc';
  } else if (type.includes('tutor')) {
    ext = '总结';
    variant = 'doc';
  } else if (type.includes('note')) {
    ext = '笔记';
    variant = 'doc';
  }
  const len =
    (item.content?.length ?? 0) +
    (item.raw_data ? JSON.stringify(item.raw_data).length : 0);
  let size: string;
  if (len < 1024) size = `${len} B`;
  else if (len < 1024 * 1024) size = `${(len / 1024).toFixed(1)} KB`;
  else size = `${(len / 1024 / 1024).toFixed(1)} MB`;
  return { ext, variant, size };
}

const AGENT_META: Record<string, AgentMeta> = {
  profile: {
    label: '学情分析智能体',
    output: '学生画像',
    depends: '消息、用户问题',
    action: '分析学习行为与薄弱点',
    module: '画像模块',
    fileType: 'PDF',
    fields: [
      { name: 'analysis_scope', label: '分析范围', type: 'select', required: true, options: [
        { value: '近7天', label: '近7天' },
        { value: '近30天', label: '近30天' },
        { value: '本学期', label: '本学期' }
      ] },
      { name: 'include_exam', label: '是否包含考试分析', type: 'select', required: true, options: [
        { value: '是', label: '是 · 纳入考试与错题' },
        { value: '否', label: '否 · 仅学习行为' }
      ] },
      { name: 'focus', label: '重点关注维度（可选）', type: 'input', placeholder: '如：薄弱点诊断 / 学习习惯' }
    ],
    defaults: { analysis_scope: '近30天', include_exam: '是', focus: '薄弱点诊断' }
  },
  document: {
    label: '资料整理智能体',
    output: '学习讲义',
    depends: '用户问题、学生画像',
    action: '生成章节摘要与知识点图谱',
    module: '资料模块',
    fileType: 'DOCX',
    fields: [
      { name: 'topic', label: '生成主题', type: 'input', required: true, placeholder: '例如：3.2 队列的概念与实现' },
      { name: 'chapter', label: '所属章节', type: 'select', required: true, options: CHAPTER_OPTIONS },
      { name: 'depth', label: '整理深度', type: 'select', required: true, options: [
        { value: '简略', label: '简略 · 概念速览' },
        { value: '标准', label: '标准 · 含例题与图示' },
        { value: '详细', label: '详细 · 含推导与扩展' }
      ] },
      { name: 'format', label: '产物格式', type: 'select', required: true, options: [
        { value: 'DOCX', label: 'DOCX' },
        { value: 'PDF', label: 'PDF' },
        { value: 'MD', label: 'Markdown' }
      ] }
    ],
    defaults: { topic: '3.2 队列的概念与实现', chapter: 'chapter-3', depth: '标准', format: 'DOCX' }
  },
  question: {
    label: '试题生成智能体',
    output: '练习题',
    depends: '用户问题、学生画像',
    action: '生成练习卷与测评卷',
    module: '考试模块',
    fileType: 'PDF',
    fields: [
      { name: 'topic', label: '命题主题', type: 'input', required: true, placeholder: '例如：3.2 队列的概念与实现' },
      { name: 'chapter', label: '所属章节', type: 'select', required: true, options: CHAPTER_OPTIONS },
      { name: 'difficulty', label: '难度', type: 'select', required: true, options: [
        { value: '基础', label: '基础' },
        { value: '中等', label: '中等' },
        { value: '进阶', label: '进阶' }
      ] },
      { name: 'count', label: '题目数量', type: 'number', min: 5, max: 50, required: true, suffix: '题' },
      { name: 'question_types', label: '包含题型', type: 'multi-select', required: true, options: [
        { value: '单选题', label: '单选题' },
        { value: '多选题', label: '多选题' },
        { value: '判断题', label: '判断题' },
        { value: '填空题', label: '填空题' },
        { value: '编程题', label: '编程题' }
      ] }
    ],
    defaults: { topic: '3.2 队列的概念与实现', chapter: 'chapter-3', difficulty: '中等', count: 10, question_types: ['单选题', '多选题', '判断题'] }
  },
  mindmap: {
    label: '思维导图智能体',
    output: '思维导图',
    depends: '用户问题、学生画像',
    action: '生成知识结构图',
    module: '资料模块',
    fileType: 'PDF',
    fields: [
      { name: 'topic', label: '导图主题', type: 'input', required: true, placeholder: '例如：栈与队列知识体系' },
      { name: 'chapter', label: '所属章节', type: 'select', required: true, options: CHAPTER_OPTIONS },
      { name: 'depth', label: '结构深度', type: 'select', required: true, options: [
        { value: '简略', label: '简略 · 2 级节点' },
        { value: '标准', label: '标准 · 3 级节点' },
        { value: '详细', label: '详细 · 4 级节点' }
      ] },
      { name: 'style', label: '结构样式', type: 'select', required: true, options: [
        { value: '树状', label: '树状 · 自顶向下' },
        { value: '放射', label: '放射 · 中心向外' },
        { value: '时间线', label: '时间线 · 顺序展开' }
      ] }
    ],
    defaults: { topic: '3.2 队列的概念与实现', chapter: 'chapter-3', depth: '标准', style: '树状' }
  },
  code: {
    label: '代码示例智能体',
    output: '代码示例',
    depends: '用户问题、学生画像',
    action: '生成代码示例与讲解',
    module: '学习模块',
    fileType: 'MD',
    fields: [
      { name: 'topic', label: '示例主题', type: 'input', required: true, placeholder: '例如：用栈实现括号匹配' },
      { name: 'chapter', label: '所属章节', type: 'select', required: true, options: CHAPTER_OPTIONS },
      { name: 'language', label: '编程语言', type: 'select', required: true, options: [
        { value: 'Python', label: 'Python' },
        { value: 'Java', label: 'Java' },
        { value: 'C++', label: 'C++' },
        { value: 'JavaScript', label: 'JavaScript' },
        { value: 'Go', label: 'Go' }
      ] },
      { name: 'difficulty', label: '难度', type: 'select', required: true, options: [
        { value: '入门', label: '入门' },
        { value: '进阶', label: '进阶' },
        { value: '面试', label: '面试' }
      ] }
    ],
    defaults: { topic: '用栈实现括号匹配', chapter: 'chapter-3', language: 'Python', difficulty: '入门' }
  },
  path: {
    label: '路径规划智能体',
    output: '学习路径',
    depends: '学生画像、讲义、题目',
    action: '生成个性化学习路径',
    module: '学习模块',
    fileType: 'PDF',
    fields: [
      { name: 'goal', label: '学习目标', type: 'input', required: true, placeholder: '例如：攻克队列与栈的应用' },
      { name: 'duration', label: '规划时长', type: 'select', required: true, options: [
        { value: '一周', label: '一周' },
        { value: '两周', label: '两周' },
        { value: '一月', label: '一月' },
        { value: '两月', label: '两月' }
      ] },
      { name: 'focus_chapter', label: '重点章节', type: 'select', required: true, options: CHAPTER_OPTIONS },
      { name: 'intensity', label: '学习强度', type: 'select', required: true, options: [
        { value: '轻松', label: '轻松 · 每天 30 分钟' },
        { value: '标准', label: '标准 · 每天 60 分钟' },
        { value: '强化', label: '强化 · 每天 90 分钟+' }
      ] }
    ],
    defaults: { goal: '攻克第3章 栈、队列与字符串', duration: '两周', focus_chapter: 'chapter-3', intensity: '标准' }
  },
  tutor: {
    label: '答疑协作智能体',
    output: '答疑解析',
    depends: '消息、学生画像、讲义、题目',
    action: '解析典型题与追问建议',
    module: '答疑模块',
    fileType: 'DOCX',
    fields: [
      { name: 'question', label: '你的问题', type: 'textarea', required: true, rows: 4, placeholder: '例如：循环队列为什么要预留一个空位？' },
      { name: 'chapter', label: '相关章节（可选）', type: 'select', options: CHAPTER_OPTIONS },
      { name: 'style', label: '讲解风格', type: 'select', required: true, options: [
        { value: '详细解析', label: '详细解析 · 含推导' },
        { value: '简明提示', label: '简明提示 · 关键点' },
        { value: '类比讲解', label: '类比讲解 · 生活化' }
      ] }
    ],
    defaults: { question: '循环队列为什么要预留一个空位？', chapter: 'chapter-3', style: '详细解析' }
  },
  video: {
    label: '课堂视频智能体',
    output: '课堂视频脚本',
    depends: '用户问题、学生画像',
    action: '生成课堂脚本与提纲',
    module: '视频模块',
    fileType: 'DOCX',
    fields: [
      { name: 'topic', label: '课堂主题', type: 'input', required: true, placeholder: '例如：3.2 队列的概念与实现' },
      { name: 'chapter', label: '所属章节', type: 'select', required: true, options: CHAPTER_OPTIONS },
      { name: 'duration', label: '目标时长', type: 'select', required: true, options: [
        { value: '5', label: '5 分钟 · 微课' },
        { value: '10', label: '10 分钟 · 短课' },
        { value: '15', label: '15 分钟 · 标准' },
        { value: '25', label: '25 分钟 · 完整课堂' }
      ] },
      { name: 'style', label: '讲解风格', type: 'select', required: true, options: [
        { value: '概念讲解', label: '概念讲解 · 知识为主' },
        { value: '案例分析', label: '案例分析 · 场景驱动' },
        { value: '原理推导', label: '原理推导 · 数学严谨' }
      ] }
    ],
    defaults: { topic: '3.2 队列的概念与实现', chapter: 'chapter-3', duration: '15', style: '概念讲解' }
  }
};

function agentKey(value?: string) {
  const raw = String(value ?? '').toLowerCase();
  return Object.keys(AGENT_META).find((key) => raw.includes(key)) ?? raw;
}

function displayAgent(agent: AgentCardItem & { icon?: LucideIcon }) {
  const key = agentKey(agent.target || agent.name);
  const meta: AgentMeta = AGENT_META[key] ?? {
    label: agent.name,
    output: agent.produces ?? '学习产物',
    depends: (agent.dependsOn ?? []).join('、') || '上下文',
    action: '生成学习产物',
    module: '智能体模块',
    fileType: 'DOCX',
    fields: [
      { name: 'topic', label: '生成主题', type: 'input', required: true, placeholder: '例如：3.2 队列的概念与实现' },
      { name: 'format', label: '产物格式', type: 'select', required: true, options: [
        { value: 'PDF', label: 'PDF' },
        { value: 'DOCX', label: 'DOCX' },
        { value: 'MD', label: 'Markdown' }
      ] }
    ],
    defaults: { topic: '3.2 队列的概念与实现', format: 'DOCX' }
  };
  return {
    ...agent,
    key,
    name: meta.label,
    desc: `产出 ${meta.output} · 依赖 ${meta.depends}`,
    status: agent.status === 'online' || agent.status === '在线' ? '在线' : agent.status || '在线',
    target: key,
    meta
  };
}

function artifactTypeLabel(type?: string) {
  const value = String(type ?? '').toLowerCase();
  if (value.includes('ppt_interactive')) return '课堂互动PPT';
  if (value.includes('ppt_handout')) return 'PPT讲义';
  if (value.includes('presentation') || value.includes('interactive')) return '课件';
  if (value.includes('mindmap')) return '思维导图';
  if (value.includes('question') || value.includes('exam')) return '题目';
  if (value.includes('tutor')) return '答疑';
  if (value.includes('lecture') || value.includes('document')) return '讲义';
  if (value.includes('video')) return '视频';
  if (value.includes('code')) return '代码';
  if (value.includes('note')) return '笔记';
  return '学习产物';
}

// "2026-07-07 11:35" → "今天 11:35" / "昨天 18:42" / "07-05 09:21"
function formatRelativeTime(iso?: string | null): string {
  const value = String(iso ?? '').trim();
  if (!value) return '';
  const stamp = value.replace('T', ' ').slice(0, 16);
  const m = stamp.match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})/);
  if (!m) return stamp;
  const [, y, mo, d, hh, mm] = m;
  const itemDate = new Date(Number(y), Number(mo) - 1, Number(d));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diffDays = Math.round((today.getTime() - itemDate.getTime()) / 86400000);
  const hm = `${hh}:${mm}`;
  if (diffDays === 0) return `今天 ${hm}`;
  if (diffDays === 1) return `昨天 ${hm}`;
  return `${mo}-${d} ${hm}`;
}

function moduleLabel(module?: string) {
  const map: Record<string, string> = {
    agents: '智能体',
    resources: '资料',
    video: '视频',
    exam: '考试',
    qa: '答疑',
    notes: '笔记',
    report: '报告',
    learning: '学习'
  };
  return map[String(module ?? '')] ?? String(module ?? '系统');
}

function actionLabel(action?: string) {
  const map: Record<string, string> = {
    'trigger-agent': '调用智能体',
    'generate-interactive-ppt': '生成课堂互动PPT',
    'generate-ppt-handout': '生成PPT讲义',
    'generate-question-artifact': '生成题目',
    'generate-mindmap': '生成思维导图',
    'ask-ai-tutor': 'AI答疑'
  };
  return map[String(action ?? '')] ?? String(action ?? '执行任务');
}

function parseArtifactJson(value?: string) {
  if (!value) return null;
  try {
    return JSON.parse(value) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function getArtifactQuestions(item: GeneratedItem) {
  const raw = item.raw_data ?? {};
  const content = parseArtifactJson(item.content);
  const questions = raw.questions ?? content?.questions ?? (Array.isArray(content) ? content : null);
  return Array.isArray(questions) ? questions as Array<Record<string, unknown>> : [];
}

function artifactMainText(item: GeneratedItem) {
  const raw = item.raw_data ?? {};
  const content = parseArtifactJson(item.content);
  const value = raw.mindmap ?? raw.tutor_response ?? raw.content ?? content?.mindmap ?? content?.content ?? item.content ?? '';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function downloadArtifactFile(item: GeneratedItem) {
  const normalized: UnifiedArtifact = {
    artifact_id: item.id, kind: String(item.type).replace('workspace_', ''), type: item.type,
    title: composeArtifactTitle(item), topic: item.chapter, content: item.content,
    raw_data: item.raw_data ?? undefined, created_at: item.created_at,
  };
  return downloadUnifiedArtifact(normalized);
}

function ArtifactPanel({ title, items, onSearch, module: moduleName }: { title: string; items: GeneratedItem[]; onSearch?: (keyword: string) => void; module?: string }) {
  const [keyword, setKeyword] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const { message } = AntdApp.useApp();
  const [activeArtifact, setActiveArtifact] = useState<GeneratedItem | null>(null);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [score, setScore] = useState<number | null>(null);
  const normalized = keyword.trim().toLowerCase();
  // 产物卡片按模块筛掉行为历史：tutor_action:* 是 tutor 行为流水（搜索/出题/导PPT），
  // chat_profile 是档案快照，都不该出现在"PPT 与课堂产物"/"题目与思维导图"卡片里。
  const VIDEO_PRODUCT_TYPES = new Set(['ppt_handout', 'ppt_interactive', 'document', 'note', 'code']);
  const EXAM_PRODUCT_TYPES = new Set(['question', 'mindmap']);
  const renderableItems = items.filter((item) => {
    if (!isArtifactRecord(item)) return false;
    const t = String(item.type ?? '').toLowerCase();
    if (t.startsWith('tutor_action:')) return false;
    if (t === 'chat_profile' || t === 'tutor' || t === 'search') return false;
    if (moduleName === 'video') return VIDEO_PRODUCT_TYPES.has(t);
    if (moduleName === 'exam') return EXAM_PRODUCT_TYPES.has(t);
    return true;
  });
  const typeFiltered = typeFilter === 'all' ? renderableItems : renderableItems.filter((item) => {
    if (typeFilter === 'video') return /video/.test(String(item.type ?? '').toLowerCase());
    return artifactGroup(item.type) === typeFilter;
  });
  const filtered = normalized
    ? typeFiltered.filter((item) => `${item.title} ${item.type} ${item.chapter ?? ''} ${item.content ?? ''}`.toLowerCase().includes(normalized))
    : typeFiltered;
  const activeQuestions = activeArtifact ? getArtifactQuestions(activeArtifact) : [];
  const renderedArtifact: UnifiedArtifact | null = activeArtifact ? {
    artifact_id: activeArtifact.id, kind: String(activeArtifact.type).replace('workspace_', ''),
    type: activeArtifact.type, title: composeArtifactTitle(activeArtifact), topic: activeArtifact.chapter,
    content: activeArtifact.content, raw_data: activeArtifact.raw_data ?? undefined, created_at: activeArtifact.created_at,
  } : null;
  const exportArtifact = () => {
    if (!activeArtifact) return;
    downloadArtifactFile(activeArtifact);
  };
  const saveArtifact = async () => {
    if (!activeArtifact) return;
    await moduleApi.saveItem({
      type: 'note',
      title: `${activeArtifact.title}（已保存）`,
      chapter: activeArtifact.chapter,
      content: artifactMainText(activeArtifact),
      raw_data: { source: 'artifact-preview', artifact_id: activeArtifact.id, artifact_type: activeArtifact.type }
    });
    message.success('已保存到笔记');
    window.dispatchEvent(new CustomEvent('kaki-artifact-generated'));
  };
  const deleteArtifact = async () => {
    if (!activeArtifact) return;
    await moduleApi.deleteItem(activeArtifact.id);
    message.success('已删除产物');
    setActiveArtifact(null);
    window.dispatchEvent(new CustomEvent('kaki-artifact-generated'));
  };
  const gradeQuestions = () => {
    if (!activeQuestions.length) return;
    const right = activeQuestions.filter((question, index) => {
      const expected = String(question.answer ?? question.correct_answer ?? '').trim().toLowerCase();
      return expected && answers[index]?.trim().toLowerCase() === expected;
    }).length;
    setScore(Math.round((right / activeQuestions.length) * 100));
  };

  return (
    <PaperCard title={title} extra={<Tag color="green">{filtered.length}</Tag>}>
      <Modal
        getContainer={document.body}
        title={activeArtifact?.title ?? '生成产物'}
        open={Boolean(activeArtifact)}
        width={860}
        okText="导出"
        cancelText="关闭"
        onOk={exportArtifact}
        onCancel={() => setActiveArtifact(null)}
        footer={[
          <Button key="delete" danger onClick={() => void deleteArtifact()}>删除</Button>,
          <Button key="save" onClick={() => void saveArtifact()}>保存到笔记</Button>,
          activeQuestions.length ? <Button key="grade" type="primary" onClick={gradeQuestions}>提交评分</Button> : null,
          <Button key="export" type="primary" onClick={exportArtifact}>导出</Button>
        ]}
      >
        <div className={styles.artifactPreview}>
          <div className={styles.artifactPreviewMeta}>
            <Tag>{activeArtifact ? artifactTypeLabel(activeArtifact.type) : '产物'}</Tag>
            <span>{activeArtifact?.created_at?.replace('T', ' ').slice(0, 16)}</span>
            {score !== null && <b>得分 {score}</b>}
          </div>
          {activeQuestions.length ? (
            <div className={styles.questionPreview}>
              {activeQuestions.map((question, index) => (
                <div key={`question-${index}`}>
                  <strong>{index + 1}. {String(question.question ?? question.content ?? question.title ?? '题目')}</strong>
                  {Array.isArray(question.options) && (
                    <div className={styles.questionOptions}>
                      {(question.options as unknown[]).map((option) => (
                        <button
                          data-active={answers[index] === String(option)}
                          key={String(option)}
                          type="button"
                          onClick={() => setAnswers((current) => ({ ...current, [index]: String(option) }))}
                        >
                          {String(option)}
                        </button>
                      ))}
                    </div>
                  )}
                  {!Array.isArray(question.options) && <Input value={answers[index]} onChange={(event) => setAnswers((current) => ({ ...current, [index]: event.target.value }))} placeholder="输入答案" />}
                  {score !== null && <small>参考答案：{String(question.answer ?? question.correct_answer ?? '暂无')}</small>}
                </div>
              ))}
            </div>
          ) : (
            renderedArtifact ? <ArtifactRenderer artifact={renderedArtifact} compact /> : null
          )}
        </div>
      </Modal>
      <div className={styles.artifactPanel} data-testid="generated-artifacts">
        <div className={styles.artifactToolbar}>
          <div className={styles.artifactTypeFilter} data-testid="artifact-type-filter" aria-label="产物类型筛选">
            {[
              ['all', '全部'], ['document', '讲义'], ['practice', '题目'],
              ['video', '视频'], ['mindmap', '导图']
            ].map(([value, label]) => (
              <button key={value} type="button" data-active={typeFilter === value} onClick={() => setTypeFilter(value)}>{label}</button>
            ))}
          </div>
          <Input allowClear prefix={<Search size={15} />} placeholder="搜索学习产物" value={keyword} onChange={(event) => setKeyword(event.target.value)} onPressEnter={() => onSearch?.(keyword)} />
        </div>
        <div className={styles.outputList} data-testid="artifact-output-list">
          {filtered.map((item) => {
            const { ext, variant } = artifactFileMeta(item);
            const composed = composeArtifactTitle(item);
            // 标题已用语义名(· PPT讲义 / · 思维导图 / · 课后练习)表达类型,
            // 不再额外贴一个会变成"练习题集题目"的类型 chip
            const time = item.created_at?.replace('T', ' ').slice(0, 16) ?? '';
            const downloadKey = `${item.type}-${item.id}`;
            return (
              <div
                key={downloadKey}
                data-artifact-type={item.type}
                data-artifact-id={item.id}
                onClick={() => { setActiveArtifact(item); setAnswers({}); setScore(null); }}
              >
                <span className={variant === 'pdf' ? styles.pdfBadge : styles.docBadge}>{ext}</span>
                <div>
                  <div>
                    <strong>{composed}</strong>
                    <Button
                      type="text"
                      size="small"
                      aria-label={`下载${composed}`}
                      icon={<Download size={14} />}
                      onClick={(event) => {
                        event.stopPropagation();
                        void downloadArtifactFile(item);
                      }}
                    />
                  </div>
                  <small>
                    <span>{artifactTypeLabel(item.type)}</span>
                    <time>{time}</time>
                    <em>打开学习</em>
                  </small>
                </div>
              </div>
            );
          })}
          {!filtered.length && <Empty image={<img src={emptyPersimmonArt} alt="" aria-hidden="true" style={{ width: 132, height: 'auto', borderRadius: 12, mixBlendMode: 'multiply' }} />} styles={{ image: { height: 'auto' } }} description="暂无生成产物，可以在 AI 学习中生成讲义、练习或导图" />}
        </div>
      </div>
    </PaperCard>
  );
}

export function ModulePage({ route }: ModulePageProps) {
  const { data, loading, error, retry } = useRequest(() => moduleApi.overview(route.module), [route.module]);
  const [searchParams] = useSearchParams();
  const compactEmptyResources = route.kind === 'resources'
    && !loading
    && Number(data?.materialLibrary?.total_materials ?? 0) === 0;

  useEffect(() => {
    window.addEventListener('kaki-artifact-generated', retry);
    window.addEventListener('kaki-material-library-changed', retry);
    return () => {
      window.removeEventListener('kaki-artifact-generated', retry);
      window.removeEventListener('kaki-material-library-changed', retry);
    };
  }, [retry]);

  return (
    <div className={styles.page} data-testid={`module-page-${route.module}`}>
      {route.kind !== 'profile' && route.kind !== 'artifacts' && <section className={styles.moduleMasthead} data-compact={compactEmptyResources || undefined}>
        <PageHeader title={route.title} subtitle={route.subtitle} />
        {!compactEmptyResources && route.kind !== 'exam' && <StatStrip stats={data?.stats} loading={loading} />}
      </section>}
      {Boolean(error) && (
        <div className={styles.errorBar}>
          接口请求失败，当前仅显示页面结构。
          <Button icon={<RefreshCw size={16} />} onClick={retry}>重试</Button>
        </div>
      )}
      {renderBody(route, data, loading, searchParams.get('section'))}
    </div>
  );
}

function renderBody(route: AppRoute, data: ReturnType<typeof moduleApi.overview> extends Promise<infer T> ? T | null : never, loading: boolean, section: string | null) {
  switch (route.kind) {
    case 'resources':
      return <ResourcesBody module={route.module} data={data} />;
    case 'learning':
      return <LearningBody data={data} loading={loading} />;
    case 'artifacts':
      return <ArtifactsBody data={data} />;
    case 'video':
      return <VideoBody data={data} loading={loading} />;
    case 'exam':
      return <ExamBody data={data} />;
    case 'profile':
      return <ProfileBody data={data} section={section} />;
    case 'notes':
      return <NotesBody module={route.module} data={data} />;
    default:
      return <GenericBody data={data} />;
  }
}

function ArtifactsBody({ data }: { data: any }) {
  const items = ((data?.generatedItems ?? []) as GeneratedItem[]).filter((item) => !hasArtifactError(item));
  return (
    <section className={styles.artifactLibrary} data-testid="artifacts-body">
      <header className={styles.artifactLibraryHeader}>
        <div><span>个人知识库</span><h1>我的学习成果</h1><p>讲义、练习、视频和导图都会留在这里，随时接着学。</p></div>
        <img src={knowledgeLibraryArt} alt="一位正在整理学习成果的学生" />
        <Link to="/ai-learning">和柿小吉继续创作 <ArrowRight size={15} /></Link>
      </header>
      <ArtifactPanel
        title="全部成果"
        items={items}
        module="artifacts"
      />
    </section>
  );
}

function _LearningOverview({ data }: { data: any }) {
  const actions = { isRunning: (..._args: unknown[]) => false, runAction: (..._args: unknown[]) => undefined };
  const current = data?.stats?.[0];
  const progress = data?.stats?.[1];
  const chapter = data?.stats?.[2];
  const time = data?.stats?.[3];

  return (
    <section className={styles.learningOverview}>
      <div className={styles.courseBlock}>
        <div className={styles.courseCover} />
        <div>
          <span>{current?.label ?? '当前课程'}</span>
          <strong>{current?.value ?? '数据结构与算法（Python版）'}</strong>
          <Button size="small" loading={actions.isRunning('switch-course', '切换课程')} onClick={() => actions.runAction('switch-course', '切换课程', { from: current?.value ?? '数据结构与算法' })}>切换课程</Button>
        </div>
      </div>
      <div className={styles.overviewMetric}>
        <span>{progress?.label ?? '总体进度'}</span>
        <Progress type="circle" percent={Number(progress?.value ?? 42)} size={82} strokeColor="#ff5a14" />
      </div>
      <div className={styles.overviewMetric}>
        <CheckCircle2 size={44} strokeWidth={1.55} />
        <strong>{chapter?.value ?? '3 / 8'}</strong>
        <span>{chapter?.label ?? '已完成章节'}</span>
      </div>
      <div className={styles.overviewMetric}>
        <Flame size={48} strokeWidth={1.45} />
        <strong>12 天</strong>
        <span>连续学习</span>
      </div>
      <div className={styles.overviewMetric}>
        <span className={styles.clockSummary}>◷</span>
        <strong>{time?.value ?? '18h 30m'}</strong>
        <span>{time?.label ?? '预计剩余学习时长'}</span>
      </div>
    </section>
  );
}

type ResourceEvidenceTopic = { topic: string; source: string };

function collectProfileTopicValues(value: unknown, output: string[]) {
  if (typeof value === 'string') {
    value.split(/[、，,;/；]/).map((item) => item.trim()).filter(Boolean).forEach((item) => output.push(item));
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => collectProfileTopicValues(item, output));
    return;
  }
  if (value && typeof value === 'object') {
    Object.values(value as Record<string, unknown>).forEach((item) => collectProfileTopicValues(item, output));
  }
}

function buildResourceEvidenceTopics(data: any): ResourceEvidenceTopic[] {
  const profile = data?.profile;
  const normalized = (profile?.profile_data?.normalized_profile ?? {}) as Record<string, unknown>;
  const motivation = (profile?.motivation_profile ?? {}) as Record<string, unknown>;
  const interestValues: string[] = [];
  [
    normalized.interests,
    normalized.interest_fields,
    normalized.learning_interests,
    motivation.interests,
    motivation.interest_topics
  ].forEach((value) => collectProfileTopicValues(value, interestValues));

  const topics: ResourceEvidenceTopic[] = Array.from(new Set(interestValues))
    .filter((item) => item.length >= 2 && item.length <= 36)
    .slice(0, 2)
    .map((topic) => ({ topic, source: '来自你的兴趣画像' }));

  const chapters = (data?.learningPath?.chapters ?? []) as LearningChapter[];
  const active = chapters.find((chapter) => chapter.task_status === 'in_progress' || chapter.stage === 'active')
    ?? chapters.find((chapter) => Number(chapter.progress_pct ?? 0) < 100)
    ?? chapters[0];
  if (active?.name && !topics.some((entry) => entry.topic === active.name)) {
    topics.push({ topic: active.name, source: '当前学习路径' });
  }

  if (!topics.length && profile?.target_role) {
    topics.push({ topic: String(profile.target_role), source: '来自你的学习目标' });
  }
  return topics.slice(0, 3);
}

function ResourcesBody({ module, data }: { module: string; data: any }) {
  const actions = useModuleActions(module);
  const navigate = useNavigate();
  const library = data?.materialLibrary;
  const profileTags = profilePreferenceTags(data?.profile);
  const evidenceTopics = buildResourceEvidenceTopics(data);
  const generatedItems = ((data?.generatedItems ?? []) as GeneratedItem[]).filter((item) => !hasArtifactError(item));
  const primaryTopic = evidenceTopics[0];
  const hasMaterials = Number(library?.total_materials ?? 0) > 0;
  const recommendationSpecs = [
    { label: '精读讲义', agentName: 'document', matcher: 'document', way: '先读关键结论，再标记疑问', art: recommendReadingArt },
    { label: '巩固练习', agentName: 'question', matcher: 'question', way: '完成小组题目并回看错因', art: recommendPracticeArt },
    { label: '结构导图', agentName: 'mindmap', matcher: 'mindmap', way: '用图形梳理概念之间的联系', art: recommendMindmapArt }
  ];
  return (
    <section className={`${styles.resourcesGrid} ${!hasMaterials ? styles.resourcesEmptyLayout : ''}`} data-empty={!hasMaterials || undefined}>
      <PaperCard title="已上传资料">
        <ResourceTable module={module} />
      </PaperCard>
      <aside className={styles.resourcesSide}>
        {hasMaterials && <PaperCard title="资料处理状态">
          <div className={styles.relatedGrid}>
            <div><Database size={18} /><strong>已上传资料</strong><span>{library?.total_materials ?? 0} 份</span><small>上传后自动解析，可在学习中引用</small></div>
            <div><AlertOctagon size={18} /><strong>待巩固知识点</strong><span>{library?.total_weak_points ?? 0} 个</span><small>用于准备针对性的学习内容</small></div>
          </div>
        </PaperCard>}
        <PaperCard title="为你准备的资料" extra={primaryTopic ? <Tag color="orange">{primaryTopic.source}</Tag> : undefined}>
          {primaryTopic ? (
            <div className={styles.resourceRecommendations} data-testid="profile-resource-recommendations">
              <div className={styles.resourceRecommendationIntro}>
                <span>围绕你正在学的</span>
                <strong>{primaryTopic.topic}</strong>
                <small>依据：{primaryTopic.source}{profileTags.length ? ` · 偏好 ${profileTags.slice(0, 2).join('、')}` : ''}</small>
              </div>
              {recommendationSpecs.map((spec) => {
                const existing = generatedItems.find((item) => item.type.includes(spec.matcher)
                  && `${item.title} ${item.chapter ?? ''}`.toLowerCase().includes(primaryTopic.topic.toLowerCase()));
                const actionTitle = `${primaryTopic.topic} · ${spec.label}`;
                return (
                  <div className={styles.resourceRecommendation} key={spec.matcher} data-status={existing ? 'ready' : 'proposed'}>
                    <img className={styles.resourceRecommendationArt} src={spec.art} alt="" aria-hidden="true" />
                    <div><strong>{spec.label}</strong><span>{spec.way}</span><small>{existing ? `已有：${existing.title}` : '还没有这类资料，可以现在生成'}</small></div>
                    <Button
                      size="small"
                      loading={actions.isRunning('trigger-agent', actionTitle)}
                      onClick={() => existing
                        ? navigate('/artifacts')
                        : actions.runAction('trigger-agent', actionTitle, {
                          agentName: spec.agentName,
                          topic: primaryTopic.topic,
                          source: 'resources-profile-recommendation',
                          evidence: primaryTopic.source,
                          preferenceTags: profileTags.slice(0, 4)
                        }, { successText: `${spec.label}已生成` })}
                    >{existing ? '查看资料' : '生成资料'}</Button>
                  </div>
                );
              })}
              {evidenceTopics.length > 1 && <div className={styles.resourceTopicList}><span>还可复用到</span>{evidenceTopics.slice(1).map((entry) => <Tag key={`${entry.source}-${entry.topic}`}>{entry.topic} · {entry.source}</Tag>)}</div>}
            </div>
          ) : (
            <div className={styles.brandedEmpty} data-testid="profile-resource-insufficient">
              <Sparkles size={28} />
              <strong>个性化资料准备中</strong>
              <span>完善学习画像或开始学习后，这里会围绕你的主题准备讲义、练习和导图。</span>
              <Button size="small" onClick={() => navigate('/profile')}>完善学习画像</Button>
            </div>
          )}
        </PaperCard>
        {hasMaterials && <PaperCard title="继续学习">
          <Space wrap><Button onClick={() => navigate('/ai-learning')}>去 AI 学习</Button><Button onClick={() => navigate('/learning')}>我的学习</Button><Button onClick={() => navigate('/artifacts')}>学习产物</Button></Space>
        </PaperCard>}
      </aside>
    </section>
  );
}

function LearningBody({ data, loading }: { data: any; loading: boolean }) {
  const actions = useModuleActions('learning');
  const { message } = AntdApp.useApp();
  const [workspace, setWorkspace] = useState<LearningWorkspacePayload | null>(data?.learningWorkspace ?? null);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(data?.learningWorkspace?.current_chapter_id ?? null);
  const [runningId, setRunningId] = useState<string | null>(null);

  useEffect(() => {
    if (data?.learningWorkspace) {
      setWorkspace(data.learningWorkspace);
      setSelectedChapterId((current) => current ?? data.learningWorkspace.current_chapter_id);
    }
  }, [data?.learningWorkspace]);

  const backendChapters = (workspace?.chapters ?? data?.learningPath?.chapters ?? []) as LearningChapter[];
  const activeChapter = backendChapters.find((chapter) => chapter.id === selectedChapterId)
    ?? backendChapters.find((chapter) => chapter.id === workspace?.current_chapter_id)
    ?? backendChapters.find((chapter) => chapter.task_status === 'in_progress' || chapter.stage === 'active' || chapter.stage === 'partial')
    ?? backendChapters[0];
  const activeProgress = Math.round(Number(activeChapter?.progress_pct ?? 0));
  const backendChapterItems = backendChapters.map((chapter) => {
    const progress = Math.round(Number(chapter.progress_pct ?? 0));
    const status = progress >= 100 || chapter.task_status === 'completed' || chapter.stage === 'completed'
      ? 'done'
      : chapter.task_status === 'in_progress' || chapter.stage === 'active' || chapter.stage === 'partial'
        ? 'active'
        : chapter.stage === 'inactive' || chapter.stage === 'locked'
          ? 'locked'
          : 'pending';
    const state = status === 'done' ? '已完成' : status === 'active' ? '学习中' : status === 'locked' ? '未解锁' : '未开始';
    return { id: chapter.id, title: chapter.name, state, percent: `${progress}%`, status } as const;
  });
  const chapterItems = backendChapterItems;
  const lessonItems = (workspace?.lessons ?? []) as LearningLesson[];
  const todayTasks = (workspace?.today_tasks ?? []) as LearningTask[];
  const achievements = workspace?.achievements ?? [];
  const statusText: Record<string, string> = {
    completed: '已完成',
    in_progress: '学习中',
    not_started: '未开始',
    pending: '未开始',
    skipped: '已跳过'
  };
  const lessonStatus = (status: string) => status === 'completed' ? 'done' : status === 'in_progress' ? 'active' : status === 'locked' ? 'locked' : 'pending';

  const runWorkspaceAction = async (key: string, action: () => Promise<LearningWorkspacePayload>, successText: string) => {
    setRunningId(key);
    try {
      const updated = await action();
      setWorkspace(updated);
      setSelectedChapterId(updated.current_chapter_id);
      message.success(successText);
    } catch {
      message.error('学习数据写入失败，请稍后重试');
    } finally {
      setRunningId(null);
    }
  };

  const startLesson = (lesson: LearningLesson) => runWorkspaceAction(
    `start:${lesson.id}`,
    () => moduleApi.startLesson(lesson.id),
    `已开始 ${lesson.title}`
  );

  const completeLesson = (lesson: LearningLesson) => runWorkspaceAction(
    `complete:${lesson.id}`,
    () => moduleApi.completeLesson(lesson.id, lesson.estimated_minutes || 25),
    `已完成 ${lesson.title}`
  );

  const updateTask = (task: LearningTask, status: string) => runWorkspaceAction(
    `task:${task.id}:${status}`,
    () => moduleApi.updateLearningTask(task.id, status),
    status === 'completed' ? '今日任务已完成' : '今日任务已跳过'
  );

  return (
    <section className={styles.learningGrid}>
      <PaperCard title="学习路径">
        <div className={styles.chapterPath}>
          {chapterItems.map(({ id, title, state, percent, status }) => (
            <button className={styles.chapterItem} data-status={status} data-active={activeChapter?.id === id} key={id} type="button" onClick={() => setSelectedChapterId(id)}>
              <span className={styles.chapterIcon}>{status === 'locked' ? <LockKeyhole size={14} /> : status === 'done' ? <CheckCircle2 size={16} /> : status === 'active' ? <Play size={14} /> : <Circle size={14} />}</span>
              <div>
                <strong>{title}</strong>
                <small>{state}</small>
              </div>
              {percent && <em>{percent}</em>}
            </button>
          ))}
          {!chapterItems.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={loading ? '正在加载学习路径' : '暂无学习路径'} />}
        </div>
      </PaperCard>
      <PaperCard title={activeChapter?.name ?? '当前学习主题'} extra={<span>{backendChapters.length ? '来自你的学习路径' : '尚未生成学习路径'}</span>}>
        <div className={styles.chapterHead}>
          <span>章节进度</span>
          <strong>{activeProgress}%</strong>
        </div>
        <Progress percent={activeProgress} showInfo={false} strokeColor="#ff5a14" />
        <div className={styles.sectionTitle}>本章小节</div>
        <div className={styles.lessonList}>
          {lessonItems.map((lesson) => {
            const status = lessonStatus(lesson.status);
            return (
            <div className={status === 'active' ? styles.lessonActive : ''} data-status={status} key={lesson.id} data-testid={`learning-lesson-${lesson.id}`}>
              <span className={styles.lessonIcon}>{status === 'locked' ? <LockKeyhole size={14} /> : status === 'active' ? <BarChart3 size={15} /> : status === 'done' ? <CheckCircle2 size={14} /> : <Play size={14} />}</span>
              <strong>{lesson.title}</strong>
              <Progress percent={Number(lesson.progress_pct)} showInfo={false} strokeColor={status === 'done' ? '#53ad5c' : '#ff5a14'} />
              <small>{statusText[lesson.status] ?? lesson.status} · {lesson.time_spent_minutes || 0} / {lesson.estimated_minutes} 分钟</small>
              <Space size={6}>
                <Button data-testid={`learning-lesson-start-${lesson.id}`} size="small" loading={runningId === `start:${lesson.id}`} disabled={lesson.status === 'completed'} onClick={() => void startLesson(lesson)}>开始</Button>
                <Button data-testid={`learning-lesson-complete-${lesson.id}`} size="small" type="primary" ghost loading={runningId === `complete:${lesson.id}`} disabled={lesson.status === 'completed'} onClick={() => void completeLesson(lesson)}>完成</Button>
              </Space>
            </div>
          );})}
          {!lessonItems.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={loading ? '正在加载小节' : '暂无小节'} />}
        </div>
        <Space className={styles.learningActions}>
          <Button type="primary" icon={<Play size={16} />} loading={actions.isRunning('continue-lesson', activeChapter?.name ?? '当前章节')} onClick={() => actions.runAction('continue-lesson', activeChapter?.name ?? '当前章节', { chapterId: activeChapter?.id ?? workspace?.current_chapter_id, source: 'chapter-card' })}>继续学习 {activeChapter?.name ?? '当前章节'}</Button>
          <Button disabled={!activeChapter?.id} loading={actions.isRunning('start-practice', '章节练习')} onClick={() => actions.runAction('start-practice', '章节练习', { chapterId: activeChapter?.id })}>章节练习</Button>
        </Space>
      </PaperCard>
      <aside className={styles.sideStack}>
        <PaperCard title="今日学习目标">
          <div className={styles.todayTaskList}>
            {todayTasks.map((task) => (
              <div key={task.id} data-status={task.status} data-testid={`learning-task-${task.id}`}>
                <strong>{task.title}</strong>
                <span>{statusText[task.status] ?? task.status} · {task.description}</span>
                <Space size={6}>
                  <Button data-testid={`learning-task-complete-${task.id}`} size="small" loading={runningId === `task:${task.id}:completed`} disabled={task.status === 'completed'} onClick={() => void updateTask(task, 'completed')}>完成</Button>
                  <Button data-testid={`learning-task-skip-${task.id}`} size="small" loading={runningId === `task:${task.id}:skipped`} disabled={task.status === 'completed' || task.status === 'skipped'} onClick={() => void updateTask(task, 'skipped')}>跳过</Button>
                </Space>
              </div>
            ))}
            {!todayTasks.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无今日任务" />}
          </div>
        </PaperCard>
        <PaperCard title="本周学习记录"><div className={styles.compactTrend}><TrendChart data={workspace?.weekly_activity ?? data?.trend} /></div></PaperCard>
        <PaperCard title="学习成就">
          <div className={styles.achievementGrid}>
            {achievements.map((item, index) => {
              const Icon = index === 0 ? Flame : index === 1 ? BookOpen : Trophy;
              return <div key={item.key}><Icon size={32} strokeWidth={1.45} /><strong>{item.label}</strong><span>{item.value}{item.unit}</span></div>;
            })}
            {!achievements.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无学习成就" />}
          </div>
        </PaperCard>
      </aside>
    </section>
  );
}

function AgentsBody({ data }: { data: any }) {
  const actions = useModuleActions('agents');
  const notifyArtifactGenerated = actions.notifyArtifactGenerated;
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const [actionEvents, setActionEvents] = useState<ActionEvent[]>(() => getActionEvents());
  const [selectedAgent, setSelectedAgent] = useState<ReturnType<typeof displayAgent> | null>(null);
  const [agentForm] = Form.useForm();
  // 调用流程三态：表单 → 工作中 → 结果
  const [invokePhase, setInvokePhase] = useState<'settings' | 'flow' | 'result'>('settings');
  const [flowSteps, setFlowSteps] = useState<Array<{ id: string; label: string; detail: string; icon: LucideIcon; state: 'pending' | 'running' | 'done' | 'error' }>>([]);
  const [invokeResult, setInvokeResult] = useState<{ title: string; outputType: string; summary: string; fullContent: string; rawPreview: string; durationMs: number; outputId?: number; errorMessage?: string } | null>(null);
  const agentEvents = getAgentInteractionEvents(6, actionEvents);
  useEffect(() => subscribeActionEvents(setActionEvents), []);
  const backendAgents = (data?.agentRegistry ?? []) as AgentCardItem[];
  const generatedItems = (data?.generatedItems ?? []) as GeneratedItem[];
  const context = data?.agentContext?.ctx ?? {};
  const contextDocument = (context.document as { title?: string } | undefined)?.title;
  const contextVideo = (context.video_result as { title?: string; provider?: string } | undefined)?.title;
  const agents: Array<AgentCardItem & { icon?: LucideIcon }> = backendAgents.length ? backendAgents.map((agent) => ({ ...agent })) : [
    { name: '学情分析 Agent', desc: '分析学习行为与薄弱点', status: '在线', tone: 'online', icon: Network, target: 'profile', produces: '学生画像', dependsOn: ['消息', '用户问题'] },
    { name: '资料整理 Agent', desc: '整理资料、生成章节摘要', status: '在线', tone: 'online', icon: FileText, target: 'document', produces: '学习讲义', dependsOn: ['用户问题', '学生画像'] },
    { name: '试题生成 Agent', desc: '生成练习卷与测评卷', status: '忙碌', tone: 'busy', icon: BookOpen, target: 'question', produces: '练习题', dependsOn: ['用户问题', '学生画像'] },
    { name: '思维导图 Agent', desc: '生成知识结构图', status: '在线', tone: 'online', icon: GitBranch, target: 'mindmap', produces: '思维导图', dependsOn: ['用户问题', '学生画像'] },
    { name: '代码示例 Agent', desc: '生成代码示例与讲解', status: '在线', tone: 'online', icon: Network, target: 'code', produces: '代码示例', dependsOn: ['用户问题', '学生画像'] },
    { name: '路径规划 Agent', desc: '生成个性化学习路径', status: '空闲', tone: 'idle', icon: ArrowRight, target: 'path', produces: '学习路径', dependsOn: ['学生画像', '讲义', '题目'] },
    { name: '答疑协作 Agent', desc: '协同解析问题与追问', status: '在线', tone: 'online', icon: Bot, target: 'tutor', produces: '答疑解析', dependsOn: ['消息', '学生画像', '讲义', '题目'] },
    { name: '课堂视频 Agent', desc: '生成课堂脚本与提纲', status: '空闲', tone: 'idle', icon: Play, target: 'video', produces: '课堂视频脚本', dependsOn: ['用户问题', '学生画像'] }
  ] as Array<AgentCardItem & { icon?: LucideIcon }>;
  const displayAgents = agents.map(displayAgent);
  const openAgentSettings = (agent: ReturnType<typeof displayAgent>) => {
    setSelectedAgent(agent);
    setInvokePhase('settings');
    setInvokeResult(null);
    setFlowSteps([]);
    agentForm.setFieldsValue(agent.meta.defaults);
  };
  const closeInvokeModal = () => {
    if (invokePhase === 'flow') {
      // 工作中禁止强制关闭，避免状态机错乱
      return;
    }
    setSelectedAgent(null);
    setInvokePhase('settings');
    setInvokeResult(null);
    setFlowSteps([]);
  };

  const seedFlowSteps = (agent: ReturnType<typeof displayAgent>): Array<{ id: string; label: string; detail: string; icon: LucideIcon; state: 'pending' | 'running' | 'done' | 'error' }> => [
    { id: 'ctx', label: '组装上下文', detail: `注入学生画像与最近 ${agent.meta.depends}`, icon: Sparkles, state: 'pending' },
    { id: 'plan', label: '任务理解与规划', detail: `输出 ${agent.meta.output} · ${agent.meta.module}`, icon: ClipboardCheck, state: 'pending' },
    { id: 'gen', label: '调用大模型生成', detail: agent.meta.action, icon: Bot, state: 'pending' },
    { id: 'persist', label: '持久化产物', detail: '保存到 generated_items 并写入 agent_invocations', icon: Database, state: 'pending' }
  ];

  const updateStep = (id: string, patch: Partial<{ state: 'pending' | 'running' | 'done' | 'error'; detail: string }>) => {
    setFlowSteps((prev) => prev.map((step) => (step.id === id ? { ...step, ...patch } : step)));
  };

  const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

  const submitAgentSettings = async () => {
    if (!selectedAgent) return;
    let values: Record<string, unknown> = {};
    try {
      values = await agentForm.validateFields();
    } catch {
      message.error('请补齐表单');
      return;
    }
    setInvokePhase('flow');
    setInvokeResult(null);
    setFlowSteps(seedFlowSteps(selectedAgent));

    const startedAt = Date.now();
    try {
      // 步骤 1：上下文组装
      updateStep('ctx', { state: 'running', detail: '正在拼装用户上下文与字段…' });
      await delay(360);
      updateStep('ctx', { state: 'done', detail: '已加载学生画像 + 共享上下文池' });

      // 步骤 2：任务理解
      updateStep('plan', { state: 'running', detail: '解析主题与生成参数…' });
      await delay(320);
      updateStep('plan', { state: 'done', detail: `已规划：${selectedAgent.meta.output}（${Object.keys(values).slice(0, 3).join(' / ')}）` });

      // 步骤 3：调用真实 LLM
      updateStep('gen', { state: 'running', detail: '请求后端 LangGraph Agent…' });
      const response = await moduleApi.invokeAgent(selectedAgent.target, {
        ...values,
        agentName: selectedAgent.target,
        target: selectedAgent.target,
        outputType: selectedAgent.meta.output,
        user_id: String(selectedAgent.target === 'profile' ? '0' : '0')
      });
      await delay(200);
      updateStep('gen', { state: 'done', detail: '大模型已返回生成结果' });

      // 步骤 4：持久化
      updateStep('persist', { state: 'running', detail: '写入 generated_items 表…' });
      await delay(280);
      updateStep('persist', { state: 'done', detail: '产物已落库，可在「智能体记录」查看' });

      const summary = pickAgentResultSummary(response, selectedAgent.meta.output);
      setInvokeResult({
        title: summary.title,
        outputType: selectedAgent.meta.output,
        summary: summary.preview,
        fullContent: summary.fullContent,
        rawPreview: summary.preview,
        outputId: (response as Record<string, unknown>)?.output_id as number | undefined,
        durationMs: Date.now() - startedAt
      });
      setInvokePhase('result');
      recordActionEvent({ module: 'agents', action: 'trigger-agent', title: selectedAgent.name, payload: { ...values, target: selectedAgent.target, outputType: selectedAgent.meta.output }, status: 'success', transport: 'backend' });
      notifyArtifactGenerated();
      message.success(`${selectedAgent.name} 已生成${selectedAgent.meta.output}`);
    } catch (error) {
      const message_ = error instanceof Error ? error.message : '调用失败';
      setFlowSteps((prev) => prev.map((step) => (step.state === 'running' ? { ...step, state: 'error', detail: message_ } : step)));
      setInvokeResult({
        title: `${selectedAgent.name} 调用失败`,
        outputType: selectedAgent.meta.output,
        summary: message_,
        fullContent: message_,
        rawPreview: message_,
        durationMs: Date.now() - startedAt,
        errorMessage: message_
      });
      setInvokePhase('result');
      recordActionEvent({ module: 'agents', action: 'trigger-agent', title: selectedAgent.name, payload: { ...values, target: selectedAgent.target, outputType: selectedAgent.meta.output }, status: 'error', transport: 'backend' });
      message.error(`${selectedAgent.name} 调用失败`);
    }
  };
  const recentOutputs = ((generatedItems.length ? generatedItems : []) as GeneratedItem[]).filter((item) => !hasArtifactError(item));
  const fallbackOutputs: GeneratedItem[] = [
    { id: 9101, type: 'document', title: '第3章 栈、队列与字符串 章节总结', chapter: 'chapter-3', content: '', raw_data: { size_label: '3.4 MB' }, created_at: '2026-07-07T10:35' },
    { id: 9102, type: 'document', title: '第3章 练习卷（含解析）', chapter: 'chapter-3', content: '', raw_data: { size_label: '2.1 MB' }, created_at: '2026-07-07T10:32' },
    { id: 9103, type: 'document', title: '第3章 学习报告（进度与薄弱点）', chapter: 'chapter-3', content: '', raw_data: { size_label: '1.8 MB' }, created_at: '2026-07-07T10:28' },
    { id: 9104, type: 'document', title: '第3章 栈、队列与字符串 视频讲稿', chapter: 'chapter-3', content: '', raw_data: { size_label: '1.2 MB' }, created_at: '2026-07-07T10:22' },
    { id: 9105, type: 'document', title: '双端队列与循环队列 讲解视频脚本', chapter: 'chapter-3', content: '', raw_data: { size_label: '1.6 MB' }, created_at: '2026-07-07T09:58' }
  ];
  const recentList = [...recentOutputs, ...fallbackOutputs].slice(0, 5);
  const records = [
    ['2024-05-24 10:30:22', '资料整理 Agent', '学习模块', '生成章节摘要与知识点图谱', '第3章 知识点图谱.pdf', '成功', '1分12秒'],
    ['2024-05-24 10:30:18', '试卷生成 Agent', '学习模块', '生成练习卷与测评卷', '第3章 练习卷（含解析）.pdf', '进行中', '-'],
    ['2024-05-24 10:29:55', '学情分析 Agent', '学习模块', '分析学习行为与薄弱点', '学情分析报告.pdf', '成功', '58秒'],
    ['2024-05-24 10:28:41', '答疑协作 Agent', '答疑模块', '解析典型题与追问建议', '典型题解析集.docx', '成功', '1分35秒'],
    ['2024-05-24 10:25:16', '视频脚本 Agent', '视频模块', '生成课堂脚本与提纲', '第3章 视频脚本.docx', '成功', '2分03秒']
  ];
  const flowStepsStatic: Array<[LucideIcon, string, string]> = [
    [UserRoundCheck, '用户请求', '输入需求'],
    [GitBranch, '路由分发', '识别意图与匹配'],
    [Bot, '多 Agent 协作', '并行处理任务'],
    [CircleCheck, '结果汇总', '整合输出结果']
  ];

  const modalFooter = useMemo(() => {
    if (!selectedAgent) return null;
    if (invokePhase === 'settings') {
      return [
        <Button key="cancel" icon={<X size={14} />} onClick={closeInvokeModal}>取消</Button>,
        <Button key="ok" type="primary" icon={<Play size={14} />} onClick={() => void submitAgentSettings()}>开始生成</Button>
      ];
    }
    if (invokePhase === 'flow') {
      return [
        <Button key="working" type="primary" loading icon={<Hourglass size={14} />}>正在生成…</Button>
      ];
    }
    return [
      <Button key="again" icon={<RefreshCw size={14} />} onClick={() => { setInvokePhase('settings'); setInvokeResult(null); }}>再调一次</Button>,
      <Button key="view" icon={<Sparkles size={14} />} onClick={() => { closeInvokeModal(); navigate('/agent-records'); }}>查看完整记录</Button>,
      <Button key="close" type="primary" onClick={closeInvokeModal}>完成</Button>
    ];
  }, [invokePhase, selectedAgent]);

  return (
    <section className={styles.agentGrid}>
      <Modal
        getContainer={document.body}
        title={selectedAgent ? modalTitle(invokePhase, selectedAgent) : '调用智能体'}
        open={Boolean(selectedAgent)}
        width={invokePhase === 'flow' ? 720 : invokePhase === 'result' ? 760 : 640}
        footer={modalFooter}
        onCancel={closeInvokeModal}
        destroyOnHidden
        maskClosable={invokePhase !== 'flow'}
        closable={invokePhase !== 'flow'}
        className={styles.agentInvokeModal}
      >
        {invokePhase === 'settings' && selectedAgent && (
          <Form form={agentForm} layout="vertical" className={styles.agentSettingForm} key={selectedAgent.key}>
            <div className={styles.agentInvokeHeader}>
              <span className={styles.agentInvokeIcon}>{renderAgentIcon(selectedAgent)}</span>
              <div>
                <strong>{selectedAgent.name}</strong>
                <small>产出 {selectedAgent.meta.output} · 依赖 {selectedAgent.meta.depends}</small>
              </div>
            </div>
            {selectedAgent.meta.fields.map((field) => renderField(field))}
          </Form>
        )}
        {invokePhase === 'flow' && selectedAgent && (
          <div className={styles.agentFlow} data-testid="agent-flow-stage">
            <div className={styles.agentFlowHeader}>
              <span className={styles.agentFlowSpinner} aria-hidden />
              <div>
                <strong>{selectedAgent.name} 正在工作…</strong>
                <small>请稍候，过程可视化</small>
              </div>
              <em>{(flowSteps.filter((s) => s.state === 'done').length)} / {flowSteps.length}</em>
            </div>
            <div className={styles.agentFlowSteps}>
              {flowSteps.map((step, index) => (
                <div className={styles.agentFlowStep} key={step.id} data-state={step.state}>
                  <span className={styles.agentFlowStepIndex} data-state={step.state}>
                    {step.state === 'done' ? <CheckCheck size={14} /> : step.state === 'running' ? <Hourglass size={14} /> : step.state === 'error' ? <AlertOctagon size={14} /> : <span>{index + 1}</span>}
                  </span>
                  <div>
                    <strong>{step.label}</strong>
                    <small>{step.detail}</small>
                  </div>
                  {step.state === 'running' && <i className={styles.agentFlowPulse} aria-hidden />}
                </div>
              ))}
            </div>
          </div>
        )}
        {invokePhase === 'result' && selectedAgent && invokeResult && (
          <div className={styles.agentResult} data-testid="agent-result-stage">
            <div className={styles.agentResultHero} data-state={invokeResult.errorMessage ? 'error' : 'success'}>
              <span className={styles.agentResultBadge}>
                {invokeResult.errorMessage ? <AlertOctagon size={26} /> : <CheckCheck size={26} />}
              </span>
              <div>
                <strong>{invokeResult.title}</strong>
                <small>
                  {selectedAgent.name} · 产物 {invokeResult.outputType} · 耗时 {Math.max(1, Math.round(invokeResult.durationMs / 100) / 10)} 秒
                </small>
              </div>
            </div>
            {!invokeResult.errorMessage && (
              <div className={styles.agentResultBody}>
                <label>产物预览</label>
                <pre data-testid="agent-result-content">{invokeResult.summary}</pre>
              </div>
            )}
            {invokeResult.errorMessage && (
              <div className={styles.agentResultError}>
                <AlertOctagon size={14} />
                <span>{invokeResult.errorMessage}</span>
              </div>
            )}
          </div>
        )}
      </Modal>
      <PaperCard title="智能体列表" extra={<Button icon={<Plus size={16} />} loading={actions.isRunning('create-agent', '新建智能体')} onClick={() => actions.runAction('create-agent', '新建智能体', { template: 'learning-assistant' })}>新建智能体</Button>}>
        <div className={styles.agentList}>
          {displayAgents.map(({ name, desc, status, tone, icon: Icon, target, meta }) => (
            <div data-status={tone} key={name}>
              <span className={styles.agentIcon}>{Icon ? <Icon size={22} strokeWidth={1.55} /> : <Bot size={22} strokeWidth={1.55} />}</span>
              <div>
                <strong>{name}</strong>
                <small>{desc}</small>
              </div>
              <span className={styles.agentState} data-tone={tone}>{status}</span>
              <Button className={styles.agentTrigger} type="text" loading={actions.isRunning('trigger-agent', name)} onClick={() => openAgentSettings({ name, desc, status, tone, icon: Icon, target, meta } as ReturnType<typeof displayAgent>)}>
                <span>调用</span>
                <b>{meta.output}</b>
              </Button>
            </div>
          ))}
        </div>
        <div className={styles.agentMore}>查看全部智能体 <ArrowRight size={14} /></div>
      </PaperCard>
      <div className={styles.agentCenter}>
        <PaperCard title="智能体交互流" extra={<b data-testid="agent-live-count">{agentEvents.length}</b>}>
          <div className={styles.agentLiveStream} data-testid="agent-live-stream">
            {agentEvents.length ? agentEvents.map((event) => (
              <div data-testid="agent-live-event" data-module={event.module} key={event.id}>
                <time>{formatActionEventTime(event.createdAt)}</time>
                <strong>{event.title}</strong>
                <span>{moduleLabel(event.module)} / {actionLabel(event.action)}</span>
                <i data-status={event.status}>{event.status === 'success' ? '已同步' : event.status === 'error' ? '失败' : '本地记录'}</i>
              </div>
            )) : (
              <div className={styles.agentLiveEmpty}>暂无实时触发，点击左侧触发模块后会显示。</div>
            )}
          </div>
        </PaperCard>
        <PaperCard title="协作流程">
          <div className={styles.flow}>
            {flowSteps.map((step, index) => {
              const Icon = step.icon;
              return (
                <div className={styles.flowStep} key={step.id}>
                  <span><Icon size={30} strokeWidth={1.55} /></span>
                  <strong>{step.label}</strong>
                  <small>{step.detail}</small>
                  {index < 3 && <ArrowRight className={styles.flowArrow} size={24} />}
                </div>
              );
            })}
          </div>
        </PaperCard>
        <PaperCard title="当前任务详情">
          <div className={styles.taskDetail}>
            <div className={styles.taskTitle}>
              <strong>{contextDocument || contextVideo || '等待后端智能体上下文'}</strong>
              <Tag color="orange">进行中</Tag>
              <b>50%</b>
            </div>
            <div className={styles.taskMeta}>
              <span>任务来源：后端 SharedContext</span>
              <span>字段数量：{data?.agentContext?.available_fields?.length ?? 0}</span>
            </div>
            <div className={styles.agentChips}>
              {displayAgents.slice(0, 4).map((item) => <span key={item.name}>{item.name}</span>)}
            </div>
            <div className={styles.taskRows}>
              {[
                ['资料整理：章节摘要与图谱', '已完成', 100],
                ['试卷生成：练习卷与测评卷', '进行中', 50],
                ['答疑协作：题解与追问建议', '等待中', 0],
                ['报告生成：学习报告与建议', '等待中', 0]
              ].map(([label, state, progress]) => (
                <div key={String(label)}>
                  <CircleCheck size={15} />
                  <span>{label}</span>
                  <em>{state}</em>
                  <i><b style={{ width: `${progress}%` }} /></i>
                  <small>{progress}%</small>
                </div>
              ))}
            </div>
            <footer><span>预计完成时间：今天 11:20</span><Button type="link" loading={actions.isRunning('view-agent-task', '当前任务详情')} onClick={() => actions.runAction('view-agent-task', '当前任务详情', { taskId: 'agent-task-current' })}>查看详情 <ArrowRight size={14} /></Button></footer>
          </div>
        </PaperCard>
      </div>
      <aside className={styles.agentSide}>
        <PaperCard title="最近产出" extra={<Button type="link" onClick={() => actions.runAction('list-agent-outputs', '全部产出')}>全部产出 <ArrowRight size={14} /></Button>}>
          <div className={styles.recentOutputList}>
            {recentList.map((item) => {
              const { ext, variant, size } = artifactFileMeta(item);
              const displaySize = item.raw_data?.size_label ?? size;
              const composed = composeArtifactTitle(item);
              const time = formatRelativeTime(item.created_at);
              const downloadKey = `${item.type}-${item.id}`;
              const downloadLabel = `下载${composed}`;
              return (
                <div
                  key={downloadKey}
                  className={styles.recentOutputRow}
                  data-artifact-type={item.type}
                  data-artifact-id={item.id}
                >
                  <span className={variant === 'pdf' ? styles.pdfBadge : styles.docBadge}>{ext}</span>
                  <div className={styles.recentOutputBody}>
                    <strong title={composed}>{composed}</strong>
                  </div>
                  <span className={styles.recentOutputTime}>{time}</span>
                  <Button
                    type="text"
                    size="small"
                    aria-label={downloadLabel}
                    className={styles.recentOutputDownload}
                    icon={<Download size={14} />}
                    loading={actions.isRunning('download-agent-output', downloadKey)}
                    onClick={() => actions.runAction('download-agent-output', composed, { type: ext, id: item.id })}
                  />
                </div>
              );
            })}
          </div>
        </PaperCard>
        <PaperCard title="Agent 状态" extra={<Button type="link" onClick={() => navigate('/agent-records')}>查看记录 <ArrowRight size={14} /></Button>}>
          <div className={styles.agentStatus}>
            <div><CheckCircle2 size={28} /><span>在线</span><strong>4 个</strong></div>
            <div><Activity size={28} /><span>忙碌</span><strong>2 个</strong></div>
            <div><Clock3 size={28} /><span>空闲</span><strong>2 个</strong></div>
          </div>
          <div className={styles.agentHealth}><span>整体健康度 <b>良好</b></span><strong>触发成功率：98.5%</strong></div>
        </PaperCard>
      </aside>
      <PaperCard title="使用记录" extra={<Button type="link" onClick={() => navigate('/agent-records')}>全部记录 <ArrowRight size={14} /></Button>}>
        <div className={styles.agentRecords}>
          <div><span>时间</span><span>触发智能体</span><span>触发来源</span><span>执行动作</span><span>输出产物</span><span>状态</span><span>耗时</span></div>
          {records.map((row) => (
            <div key={row.join('-')}>
              {row.map((cell, index) => <span data-state={index === 5 ? cell : undefined} key={`${cell}-${index}`}>{cell}</span>)}
            </div>
          ))}
        </div>
      </PaperCard>
    </section>
  );
}

type ExternalResourceRow = {
  key: string;
  type: string;
  title: string;
  source: string;
  date: string;
  url?: string;
  snippet?: string;
  cover?: string | null;
  platform?: string;
};

const RESOURCE_PLATFORM_FILTERS: Array<{ key: string; label: string; platforms: string[] }> = [
  { key: 'all', label: '全部', platforms: [] },
  { key: 'video', label: '视频', platforms: ['bilibili', 'ytb', 'ted'] },
  { key: 'doc', label: '文档', platforms: ['general', 'edx', 'coursera', 'udemy', 'khan'] },
  { key: 'tiku', label: '题解', platforms: ['general', 'leetcode'] }
];

const RESOURCE_SORT_OPTIONS = [
  { value: 'latest', label: '最新' },
  { value: 'relevance', label: '相关度' },
  { value: 'fastest', label: '响应最快' }
];

const PLATFORM_LABEL: Record<string, string> = {
  bilibili: 'B 站',
  ytb: 'YouTube',
  ted: 'TED',
  coursera: 'Coursera',
  edx: 'edX',
  udemy: 'Udemy',
  khan: 'Khan Academy',
  general: '通用',
  leetcode: 'LeetCode'
};

function platformToResourceType(platform: string | undefined): string {
  if (!platform) return '文档';
  if (['bilibili', 'ytb', 'ted'].includes(platform)) return '视频';
  if (['coursera', 'edx', 'udemy', 'khan'].includes(platform)) return '课程';
  if (['leetcode'].includes(platform)) return '题解';
  return '文档';
}

function useExternalResourceSearch(topic: string, actions: ReturnType<typeof useModuleActions>) {
  const [keyword, setKeyword] = useState<string>('');
  const [submittedKeyword, setSubmittedKeyword] = useState<string>('');
  const [platformKey, setPlatformKey] = useState<string>('all');
  const [sortKey, setSortKey] = useState<string>('latest');
  const [retryNonce, setRetryNonce] = useState<number>(0);
  const [data, setData] = useState<VideoSearchResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [coverErrors, setCoverErrors] = useState<Set<string>>(() => new Set());
  const markCoverError = useCallback((key: string) => {
    setCoverErrors((prev) => {
      if (prev.has(key)) return prev;
      const next = new Set(prev);
      next.add(key);
      return next;
    });
  }, []);
  const resetCoverErrors = useCallback(() => setCoverErrors(new Set()), []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    resetCoverErrors();
    const filter = RESOURCE_PLATFORM_FILTERS.find((item) => item.key === platformKey) ?? RESOURCE_PLATFORM_FILTERS[0];
    const q = (submittedKeyword || topic || '数据结构 算法').trim();
    const fallback = (reason: string): VideoSearchResponse => ({
      success: true,
      results: [
        { title: `${q} · 站内资源速览`, platform: 'general', url: '/resources', snippet: reason },
        { title: `${q} · 必考点速记`, platform: 'general', url: '/learning', snippet: '后端学习路径生成的总结要点' },
        { title: `${q} · 视频讲稿`, platform: 'bilibili', url: 'https://www.bilibili.com', snippet: '后端生成的视频脚本摘要' }
      ],
      count: 3,
      source: 'fallback',
      query: q,
      platforms: filter.platforms
    });
    const timer = setTimeout(() => {
      if (alive) {
        setData(fallback('外部搜索在 8s 内未返回，已切换到站内资源'));
        setLoading(false);
      }
    }, 8000);
    moduleApi
      .searchExternalVideos({ q, limit: 12, sources: filter.platforms })
      .then((response) => {
        if (!alive) return;
        if (!response?.results?.length) {
          setData(fallback('外部资源暂无匹配，已切换到站内资源'));
        } else {
          setData(response);
        }
      })
      .catch((err: any) => {
        if (!alive) return;
        if (err?.response?.status === 401) {
          setData(fallback('当前账号未登录外部资源，已切换到站内推荐'));
        } else if (/timeout/i.test(err?.message ?? '')) {
          setData(fallback('外部搜索连接超时，已切换到站内推荐'));
        } else {
          setData(fallback('外部搜索服务暂不可用，已切换到站内推荐'));
        }
      })
      .finally(() => {
        clearTimeout(timer);
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [topic, submittedKeyword, platformKey, retryNonce, resetCoverErrors]);

  const rows = useMemo<ExternalResourceRow[]>(() => {
    if (!data?.results?.length) return [];
    const list = data.results.map((item, index) => {
      const platform = (item.platform ?? 'general').toLowerCase();
      const platformLabel = PLATFORM_LABEL[platform] ?? item.platform ?? '通用';
      const type = platformToResourceType(platform);
      return {
        key: `${item.url ?? item.title ?? 'item'}-${index}`,
        type,
        title: item.title?.trim() || '外部资源',
        source: `来源：${platformLabel}${item.snippet ? ` · ${item.snippet.slice(0, 24)}` : ''}`,
        date: sortKey === 'fastest' ? `${data.elapsed_ms ?? 0}ms` : (sortKey === 'relevance' ? `匹配 #${index + 1}` : new Date().toISOString().slice(5, 10)),
        url: item.url,
        snippet: item.snippet ?? '',
        cover: item.cover ?? null,
        platform: item.platform ?? platform
      } satisfies ExternalResourceRow;
    });
    if (sortKey === 'latest') {
      return [...list].sort((a, b) => a.key < b.key ? 1 : -1);
    }
    return list;
  }, [data, sortKey]);

  const onOpen = (row: ExternalResourceRow) => {
    actions.runAction('open-external-resource', row.title, { type: row.type, url: row.url, source: row.source });
    if (row.url) {
      try { window.open(row.url, '_blank', 'noopener,noreferrer'); } catch { /* 浏览器拦截时静默 */ }
    }
  };

  return {
    keyword,
    setKeyword,
    onSearch: (value: string) => setSubmittedKeyword(value.trim()),
    onReset: () => { setKeyword(''); setSubmittedKeyword(''); },
    retry: () => setRetryNonce((prev) => prev + 1),
    platformKey,
    setPlatformKey,
    sortKey,
    setSortKey,
    rows,
    loading,
    error,
    source: data?.source ?? 'fallback',
    count: data?.count ?? 0,
    query: data?.query ?? submittedKeyword ?? topic,
    onOpen,
    coverErrors,
    markCoverError
  };
}

function VideoBody({ data, loading }: { data: any; loading: boolean }) {
  const actions = useModuleActions('video');
  const videos = (data?.videos ?? []) as VideoItem[];
  const generatedItems = (data?.generatedItems ?? []) as GeneratedItem[];
  const currentVideo = videos[0];
  const currentTopic = currentVideo?.topic ?? currentVideo?.title ?? '3.2 队列的概念与实现';
  const currentTranscript = currentVideo?.transcript ?? currentVideo?.video_result?.script ?? currentVideo?.video_result?.transcript ?? '';
  const chapters = (data?.timeline?.length ? data.timeline : []).map((item: any, index: number) => ({
    title: item.title,
    time: item.time,
    status: item.status,
    order: `3.${index + 1}`,
    locked: item.status === 'locked'
  }));
  const backendScriptRows = currentTranscript
    ? currentTranscript
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .slice(0, 6)
      .map((line, index) => [`${String(index * 3).padStart(2, '0')}:00`, index === 0 ? '后端课堂脚本' : `脚本片段 ${index + 1}`, line.replace(/^#+\s*/, ''), index === 0 ? 'active' : ''])
    : [];
  const scriptRows = backendScriptRows.length ? backendScriptRows : [
    ['00:00', '课堂导入', '队列在生活中的应用场景引入', 'active'],
    ['02:15', '队列的概念', '定义、特点：先进先出（FIFO）', ''],
    ['06:40', '队列的抽象数据类型', '基本操作：入队（enqueue）、出队（dequeue）', ''],
    ['12:20', '队列的顺序存储实现', '顺序队列的结构、入队与出队操作', ''],
    ['18:05', '队列的链式存储实现', '链式队列的结构与实现方式', ''],
    ['23:50', '小结与练习', '本节重点回顾与随堂练习', '']
  ];
  const notes = [
    ['04:58', '队列遵循先进先出（FIFO）原则', '重点'],
    ['12:47', '顺序队列需要处理“假溢出”问题', '难点'],
    ['18:33', '链式队列节点结构', ''],
    ['22:05', '入队与出队的时间复杂度均为 O(1)', '']
  ];
  const resources = useExternalResourceSearch(currentTopic, actions);

  return (
    <section className={styles.videoGrid}>
      <PaperCard title={currentTopic}>
        <div className={styles.videoTitleMeta}>{currentVideo ? `后端视频 · ${currentVideo.llm_provider ?? 'unknown'} · ${currentVideo.mode ?? 'script'}` : '数据结构与算法（Python版） · 第3章 栈、队列与字符串'}</div>
        <div className={styles.videoBox}>
          <div className={styles.videoPosterText}>{currentTopic}</div>
          <button className={styles.videoPlay} aria-label="播放视频" onClick={() => actions.runAction('play-video', currentTopic, { videoId: currentVideo?.id, time: '04:32' })}><Play size={34} fill="currentColor" /></button>
          <div className={styles.videoControls}>
            <Play size={17} fill="currentColor" />
            <Volume2 size={16} />
            <span className={styles.videoProgress}><i /></span>
            <time>04:32 / 25:19</time>
            <b>1.25x</b>
            <Captions size={15} />
            <Settings size={16} />
            <Maximize size={16} />
          </div>
        </div>
      </PaperCard>
      <PaperCard title="课堂脚本" extra={<Space><Tag color="orange">AI</Tag><Button type="primary" ghost loading={actions.isRunning('generate-script', '课堂脚本')} onClick={() => actions.runAction('generate-script', '课堂脚本', { videoId: currentVideo?.id, topic: currentTopic })}>生成脚本</Button><Button icon={<RefreshCw size={15} />} loading={actions.isRunning('regenerate-script', '课堂脚本')} onClick={() => actions.runAction('regenerate-script', '课堂脚本', { videoId: currentVideo?.id, topic: currentTopic })}>重新生成</Button></Space>}>
        <div className={styles.artifactActions}>
          <Button loading={actions.isRunning('generate-interactive-ppt', '课堂互动 PPT')} onClick={() => actions.runAction('generate-interactive-ppt', '课堂互动 PPT', { topic: currentTopic, chapter: 'chapter-3' })}>课堂互动 PPT</Button>
          <Button loading={actions.isRunning('generate-ppt-handout', 'PPT 讲义')} onClick={() => actions.runAction('generate-ppt-handout', 'PPT 讲义', { topic: currentTopic, chapter: 'chapter-3' })}>PPT 讲义</Button>
        </div>
        <div className={styles.scriptList} aria-busy={loading}>
          {scriptRows.map(([time, title, desc, state]) => (
            <div data-active={state === 'active'} key={time}>
              <time>{time}</time>
              <div><strong>{title}</strong><span>{desc}</span></div>
            </div>
          ))}
        </div>
      </PaperCard>
      <ArtifactPanel title="PPT 与课堂产物" items={generatedItems} module="video" onSearch={(keyword) => actions.runAction('open-ai-artifact', keyword || '视频产物', { keyword, module: 'video' })} />
      <div className={styles.videoLowerLeft}>
        <PaperCard title="章节目录" extra={<span>共 {chapters.length || 8} 节</span>}>
          <div className={styles.chapterCatalog}>
            {chapters.map((item: any) => (
              <div data-active={item.status === 'active'} data-locked={item.locked} key={item.title}>
                <span>{item.status === 'done' ? <CheckCircle2 size={13} /> : item.status === 'active' ? <Play size={14} fill="currentColor" /> : <Circle size={12} />}</span>
                <strong>{item.order} {item.title.replace(/^3\.\d+\s*/, '')}</strong>
                <time>{item.time}</time>
                {item.locked && <LockKeyhole size={12} />}
              </div>
            ))}
          </div>
        </PaperCard>
        <PaperCard title="学习笔记 / 时间戳" extra={<Button size="small" icon={<Plus size={14} />} loading={actions.isRunning('create-video-note', '新建笔记')} onClick={() => actions.runAction('create-video-note', '新建笔记', { videoId: 'lesson-3-2', time: '04:32' })}>新建笔记</Button>}>
          <div className={styles.videoNotes}>
            {notes.map(([time, text, tag]) => (
              <div key={time}>
                <time>{time}</time>
                <span>{text}</span>
                {tag && <Tag color={tag === '重点' ? 'orange' : 'purple'}>{tag}</Tag>}
              </div>
            ))}
          </div>
          <button className={styles.videoMore} onClick={() => actions.runAction('list-video-notes', '查看全部笔记', { videoId: 'lesson-3-2' })}>查看全部笔记（8） <ArrowRight size={14} /></button>
        </PaperCard>
      </div>
      <aside className={styles.videoRightStack}>
        <PaperCard
          title="外部资源搜索"
          extra={<small className={styles.resourceMeta}>共 {resources.count} 条 · 来源 {resources.source === 'agent' || resources.source === 'tavily' ? '后端视频搜索' : '站内推荐'}</small>}
        >
          <div className={styles.resourceSearch}>
            <Input
              prefix={<Search size={15} />}
              placeholder="输入关键词搜索资料、课程、题解..."
              value={resources.keyword}
              onChange={(e) => resources.setKeyword(e.target.value)}
              onPressEnter={(e) => resources.onSearch((e.target as HTMLInputElement).value)}
              allowClear
              onClear={resources.onReset}
              suffix={
                <Button size="small" type="primary" onClick={() => resources.onSearch(resources.keyword)} loading={resources.loading}>
                  搜索
                </Button>
              }
            />
            <div className={styles.resourceFilters}>
              {RESOURCE_PLATFORM_FILTERS.map((filter) => (
                <Button
                  key={filter.key}
                  size="small"
                  type={resources.platformKey === filter.key ? 'primary' : 'default'}
                  ghost={resources.platformKey === filter.key}
                  onClick={() => resources.setPlatformKey(filter.key)}
                >
                  {filter.label}
                </Button>
              ))}
              <Select
                size="small"
                value={resources.sortKey}
                options={RESOURCE_SORT_OPTIONS}
                onChange={(value) => resources.setSortKey(value)}
                className={styles.resourceSort}
              />
            </div>
            {resources.error ? (
              <div className={styles.resourceError}>
                <span><strong>提示</strong> · {resources.error}</span>
                <Button size="small" type="link" onClick={() => resources.retry()}>重试</Button>
              </div>
            ) : resources.source === 'fallback' ? (
              <div className={styles.resourceFallback}>
                <span><strong>提示</strong> · 外部视频搜索服务暂未返回，已为你切换到站内推荐。</span>
                <Button size="small" type="link" onClick={() => resources.retry()}>重试外部</Button>
              </div>
            ) : resources.loading ? (
              <div className={styles.resourceLoading}>正在调用视频搜索服务…</div>
            ) : resources.rows.length ? (
              <div className={styles.videoResources}>
                {resources.rows.map((row) => {
                  const initial = (row.platform ?? row.type ?? '视').trim().charAt(0).toUpperCase() || '视';
                  const safeUrl = typeof row.url === 'string' && /^https?:\/\//i.test(row.url) ? row.url : undefined;
                  const coverFailed = resources.coverErrors.has(row.key);
                  const showFallback = !row.cover || coverFailed;
                  return (
                    <div key={row.key} data-type={row.type}>
                      <a
                        className={styles.videoCover}
                        data-platform={row.platform ?? 'general'}
                        href={safeUrl ?? '#'}
                        target={safeUrl ? '_blank' : undefined}
                        rel={safeUrl ? 'noopener noreferrer' : undefined}
                        title={safeUrl ? `在新页面打开：${row.title}` : '暂无可跳转链接'}
                        onClick={(event) => {
                          // 阻止冒泡以免触发外层按钮；href 自身已能完成跳转
                          event.stopPropagation();
                        }}
                      >
                        {showFallback ? (
                          <span className={styles.videoCoverFallback} aria-hidden="true">
                            <strong>{initial}</strong>
                            <small>{row.platform ?? row.type}</small>
                          </span>
                        ) : (
                          <img
                            src={row.cover ?? undefined}
                            alt={row.title}
                            loading="lazy"
                            onError={() => resources.markCoverError(row.key)}
                          />
                        )}
                        <span className={styles.videoCoverType} data-type={row.type}>{row.type}</span>
                      </a>
                      <div className={styles.videoResourceMain}>
                        <strong title={row.title}>{row.title}</strong>
                        <small>{row.source}</small>
                      </div>
                      <time>{row.date}</time>
                      <Button size="small" type="link" onClick={() => resources.onOpen(row)} disabled={!row.url}>打开</Button>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className={styles.resourceEmpty}>没有匹配的外部资源，试试切换关键词或平台。</div>
            )}
          </div>
        </PaperCard>
        <PaperCard title="AI 学习建议">
          <div className={styles.aiAdvice}>
            {[
              ['巩固薄弱点', '建议回看 18:05 的链式队列实现部分', '去回看'],
              ['练习建议', '完成队列相关练习题 8 道，巩固理解。', '去练习'],
              ['延伸学习', '学习双端队列与循环队列的差异与应用。', '去学习']
            ].map(([title, desc, action]) => (
              <div key={title}>
                <Star size={16} fill="currentColor" />
                <strong>{title}</strong>
                <span>{desc}</span>
                <Button type="link" onClick={() => actions.runAction('apply-ai-advice', title, { action })}>{action} <ArrowRight size={13} /></Button>
              </div>
            ))}
          </div>
        </PaperCard>
      </aside>
    </section>
  );
}

const EXAM_TAB_MAP: Record<string, string> = {
  '章节练习': 'quiz',
  '模拟考试': 'mock',
  '薄弱点强化': 'weak_reinforcement',
  '自定义测评': 'custom'
};

const EXAM_MODE_COPY: Record<string, { title: string; detail: string; feedback: string }> = {
  quiz: { title: '跟着当前章节练', detail: '围绕当前学习章节出题，用于学习中的即时巩固。', feedback: '每题可立即检查，最后统一提交形成学习证据。' },
  mock: { title: '完整模拟考试', detail: '按所选范围计时作答，考试过程中不显示答案。', feedback: '统一交卷后查看完整报告。' },
  weak_reinforcement: { title: '针对真实薄弱点', detail: '结合 Learner State、最近错题与正式评估结果安排题目。', feedback: '交卷后给出需加强点和下一步。' },
  custom: { title: '按你的用途组卷', detail: '选择来源、规格与难度，生成一份边界明确的测评。', feedback: '统一交卷后查看报告。' },
};

const EXAM_CHAPTER_OPTIONS = [
  { value: 'chapter-3', label: '第3章 栈、队列与字符串' },
  { value: 'chapter-4', label: '第4章 链表' },
  { value: 'chapter-5', label: '第5章 递归与分治' },
  { value: 'chapter-6', label: '第6章 树与二叉树' }
];

const EXAM_QUESTION_TYPES = ['单选题', '多选题', '判断题', '填空题', '简答题', '编程题'] as const;
const EXAM_DIFFICULTIES = ['轻松', '标准', '挑战', '自动适应'] as const;

const QUESTION_TYPE_LABEL: Record<string, string> = {
  choice: '单选题',
  multi_choice: '多选题',
  judge: '判断题',
  fill: '填空题',
  short_answer: '简答题',
  code: '编程题',
};

function questionTypeLabel(type: string | null | undefined): string {
  if (!type) return '';
  return QUESTION_TYPE_LABEL[type] ?? type;
}

function ExamBody({ data }: { data: any }) {
  const actions = useModuleActions('exam');
  const { message } = AntdApp.useApp();
  const [activeExamTab, setActiveExamTab] = useState<'章节练习' | '模拟考试' | '薄弱点强化' | '自定义测评'>('章节练习');
  const [difficulty, setDifficulty] = useState<typeof EXAM_DIFFICULTIES[number]>('标准');
  const [questionCount, setQuestionCount] = useState(10);
  const [selectedChapters, setSelectedChapters] = useState<string[]>([]);
  const [questionTypes, setQuestionTypes] = useState<Array<typeof EXAM_QUESTION_TYPES[number]>>(['单选题', '多选题', '判断题', '填空题', '简答题']);
  const [sourceKind, setSourceKind] = useState('current_chapter');
  const [specification, setSpecification] = useState<'quick' | 'standard' | 'full'>('standard');

  // 生成试卷流程：flow 动画 + ExamRunner
  const [generator, setGenerator] = useState<{ phase: 'idle' | 'flow' | 'ready'; examId?: number; flowSteps: Array<{ id: string; label: string; state: 'pending' | 'running' | 'done' | 'error'; detail: string }>; errorMsg?: string }>({ phase: 'idle', flowSteps: [] });
  // ExamRunner 状态
  const [runner, setRunner] = useState<{ open: boolean; examId?: number; phase: 'confirm' | 'running' | 'result'; result?: ExamResultProjection }>({ open: false, phase: 'confirm' });

  const backendExams = (data?.exams ?? []) as ExamItem[];
  const generatedItems = (data?.generatedItems ?? []) as GeneratedItem[];
  const wrongQuestionData = data?.wrongQuestions as WrongQuestionsPayload | undefined;
  const examChapters = ((data?.learningPath?.chapters ?? []) as LearningChapter[]).map((chapter) => ({ value: chapter.id, label: chapter.name }));
  const chapterTitleMap = new Map(examChapters.map((chapter) => [chapter.value, chapter.label]));

  useEffect(() => {
    if (!selectedChapters.length && examChapters.length) setSelectedChapters([examChapters[0].value]);
  }, [examChapters, selectedChapters.length]);

  const examTypeKey = EXAM_TAB_MAP[activeExamTab] ?? 'mock';

  const updateFlowStep = (id: string, patch: Partial<{ state: 'pending' | 'running' | 'done' | 'error'; detail: string }>) => {
    setGenerator((prev) => ({
      ...prev,
      flowSteps: prev.flowSteps.map((s) => (s.id === id ? { ...s, ...patch } : s))
    }));
  };

  const startGenerate = async () => {
    if (selectedChapters.length === 0) {
      message.warning('请至少选择一个章节');
      return;
    }
    if (questionTypes.length === 0) {
      message.warning('请至少选择一种题型');
      return;
    }
    // 选中节点使用真实标题参与出题，保证 LLM prompt 围绕当前学习计划
    const selectedTitles = selectedChapters.map((id) => chapterTitleMap.get(id) ?? id);
    const flowSteps = [
      { id: 'ctx', label: '了解你的学习情况', state: 'pending' as const, detail: '结合学习目标与当前进度' },
      { id: 'plan', label: '规划题目分布', state: 'pending' as const, detail: '按章节、难度与题型安排题量' },
      { id: 'gen', label: 'AI 正在出题', state: 'pending' as const, detail: '围绕所选学习节点命题' },
      { id: 'persist', label: '保存试卷', state: 'pending' as const, detail: '整理成一份完整试卷' }
    ];
    setGenerator({ phase: 'flow', flowSteps });
    const tick = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
    try {
      // 步骤 1
      updateFlowStep('ctx', { state: 'running', detail: '正在了解你的学习目标与进度…' });
      await tick(320);
      updateFlowStep('ctx', { state: 'done', detail: '已结合你的学习目标与基础' });
      // 步骤 2
      updateFlowStep('plan', { state: 'running', detail: '安排章节、难度与题型…' });
      await tick(280);
      updateFlowStep('plan', { state: 'done', detail: `${activeExamTab} · ${difficulty} · ${questionTypes.length} 种题型 · ${questionCount} 题` });
      // 步骤 3：后端依据已确认模式契约生成并持久化试卷
      updateFlowStep('gen', { state: 'running', detail: '围绕所选学习节点命题…' });
      const exam = await moduleApi.createExam(examTypeKey, selectedTitles, { difficulty, questionTypes, count: questionCount, sourceKind, specification });
      updateFlowStep('gen', { state: 'done', detail: '题目已由考试服务生成' });
      updateFlowStep('persist', { state: 'running', detail: '正在保存模式契约与试卷…' });
      updateFlowStep('persist', { state: 'done', detail: `试卷已就绪：${exam.questions.length} 题 / ${exam.total_score} 分 / ${exam.time_limit_min} 分钟` });
      setGenerator({ phase: 'ready', examId: exam.id, flowSteps });
      message.success(`${activeExamTab}已生成，请确认开始考试`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '生成失败';
      setGenerator((prev) => ({ ...prev, phase: 'idle', errorMsg: msg }));
      message.error(`试卷生成失败：${msg}`);
    }
  };

  const startExam = (examId: number, showResult = false) => {
    setGenerator({ phase: 'idle', flowSteps: [] });
    setRunner({ open: true, examId, phase: showResult ? 'result' : 'confirm' });
  };

  const backendExamRows = backendExams.map((exam) => [
    `${EXAM_TAB_MAP_REVERSE[exam.exam_type] ?? '测评'}试卷`,
    exam.exam_type,
    exam.status === 'completed' ? '已评分' : '',
    `共 ${exam.chapter_ids.length} 章 | ${exam.time_limit_min} 分钟 | 总分 ${exam.total_score}`,
    exam.status === 'completed' ? '查看报告' : '开始考试',
    exam.id
  ] as const);
  const availableExams = backendExamRows;
  const backendRecentExams = backendExams.map((exam) => [
    `后端试卷 #${exam.id}`,
    exam.exam_type,
    exam.score == null ? '-' : `${exam.score} 分`,
    exam.created_at?.replace('T', ' ').slice(0, 16) ?? '',
    exam.status === 'completed' ? '已完成' : '未完成',
    exam.id
  ] as const);
  const recentExams = backendRecentExams;
  const backendWeakPoints = Object.entries(wrongQuestionData?.by_topic ?? {}).map(([topic, items]) => [topic, Math.max(1, 100 - items.length * 10)] as const);
  const weakPoints = backendWeakPoints;

  const recommendedTopic = examChapters[0]?.label ?? data?.learningPlan?.title ?? '当前学习内容';

  return (
    <>
      <section className={styles.examRecommended} aria-label="推荐考试">
        <span>推荐考试</span>
        <h2>{recommendedTopic}综合检测</h2>
        <p>根据你当前的学习路线和薄弱点安排，用一份短测确认下一步。</p>
        <div><strong>10 题</strong><strong>约 15 分钟</strong></div>
        <Button type="primary" loading={generator.phase === 'flow'} onClick={startGenerate}>开始检测</Button>
      </section>
      <details className={styles.examAdvanced}>
        <summary>高级设置与历史记录</summary>
      <section className={styles.examGrid}>
      <div className={styles.examLeft}>
        <PaperCard title="考试中心">
          <div className={styles.examCreator}>
            <div className={styles.examTabs}>
              {(Object.keys(EXAM_TAB_MAP) as Array<keyof typeof EXAM_TAB_MAP>).map((tab) => (
                <button data-active={activeExamTab === tab || undefined} key={tab} onClick={() => {
                  setActiveExamTab(tab as typeof activeExamTab);
                  const mode = EXAM_TAB_MAP[tab];
                  setSourceKind(mode === 'quiz' ? 'current_chapter' : mode === 'weak_reinforcement' ? 'current_weak_points' : mode === 'mock' ? 'selected_scope' : sourceKind);
                  setQuestionCount(mode === 'quiz' ? 5 : mode === 'mock' ? 20 : mode === 'weak_reinforcement' ? 10 : specification === 'quick' ? 5 : specification === 'full' ? 20 : 10);
                  void actions.runAction('switch-exam-type', tab, { tab });
                }}>{tab}</button>
              ))}
            </div>
            <div className={styles.examModeContract} data-testid="exam-mode-contract">
              <strong>{EXAM_MODE_COPY[examTypeKey].title}</strong>
              <span>{EXAM_MODE_COPY[examTypeKey].detail}</span>
              <small>{EXAM_MODE_COPY[examTypeKey].feedback}</small>
            </div>
            <Form layout="vertical">
              <Form.Item label="当前学习计划"><Input value={data?.learningPlan ? `${data.learningPlan.title} · ${data.learningPlan.totalNodes} 个学习节点` : '还没有学习计划，先告诉柿小吉你想学什么'} readOnly /></Form.Item>
              <Form.Item label="章节范围（多选）"><Select mode="multiple" value={selectedChapters} onChange={(v) => setSelectedChapters(v as string[])} options={examChapters} placeholder="选择至少一个章节" /></Form.Item>
              {activeExamTab === '自定义测评' && <div className={styles.examCustomRow}>
                <Form.Item label="内容来源"><Select value={sourceKind} onChange={setSourceKind} options={[
                  { value: 'current_chapter', label: '当前章节' }, { value: 'custom_topic', label: '自定义主题' },
                  { value: 'specified_materials', label: '指定资料' }, { value: 'current_lecture', label: '当前讲义' },
                  { value: 'recent_wrong_answers', label: '最近错题' }, { value: 'current_weak_points', label: '当前薄弱点' },
                ]} /></Form.Item>
                <Form.Item label="测评规格"><Select value={specification} onChange={(value) => { setSpecification(value); setQuestionCount(value === 'quick' ? 5 : value === 'standard' ? 10 : 20); }} options={[
                  { value: 'quick', label: '快速 · 5题' }, { value: 'standard', label: '标准 · 10题' }, { value: 'full', label: '完整 · 20题' },
                ]} /></Form.Item>
              </div>}
              <div className={styles.examOptionRow}><span>难度设置</span>{EXAM_DIFFICULTIES.map((item) => <button data-active={difficulty === item} key={item} onClick={() => { setDifficulty(item); void actions.runAction('set-exam-difficulty', item, { difficulty: item }); }}>{item}</button>)}</div>
              <div className={styles.examCheckRow}>
                <span>题型选择</span>
                {EXAM_QUESTION_TYPES.map((item) => (
                  <label key={item}>
                    <input type="checkbox" checked={questionTypes.includes(item)} onChange={(e) => {
                      setQuestionTypes((prev) => e.target.checked ? Array.from(new Set([...prev, item])) : prev.filter((t) => t !== item));
                      void actions.runAction('set-question-types', item, { types: questionTypes });
                    }} />
                    {item}
                  </label>
                ))}
              </div>
              <div className={styles.examNumberRow}>
                <span>题目数量</span>
                <Button size="small" onClick={() => { const next = Math.max(5, questionCount - 5); setQuestionCount(next); void actions.runAction('set-question-count', String(next), { count: next }); }}>-</Button>
                <Input value={`${questionCount} 题`} readOnly />
                <Button size="small" onClick={() => { const next = Math.min(80, questionCount + 5); setQuestionCount(next); void actions.runAction('set-question-count', String(next), { count: next }); }}>+</Button>
                <em>建议 5-80 题</em>
              </div>
              <div className={styles.examActions}>
                <Button type="primary" loading={generator.phase === 'flow'} onClick={startGenerate}>开始生成试卷</Button>
                <Button onClick={() => { setGenerator({ phase: 'idle', flowSteps: [] }); }} disabled={generator.phase === 'idle'}>重置</Button>
              </div>
            </Form>
            <div className={styles.examPlanSummary} aria-label="本次组卷摘要">
              <div><small>本次组卷</small><strong>{questionCount}<span>题</span></strong><em>预计 {Math.max(8, Math.round(questionCount * 1.2))} 分钟</em></div>
              <dl>
                <div><dt>章节范围</dt><dd>{selectedChapters.length ? `${selectedChapters.length} 个已选` : '等待选择'}</dd></div>
                <div><dt>题型范围</dt><dd>{questionTypes.length ? `${questionTypes.length} 类` : '等待选择'}</dd></div>
                <div><dt>当前难度</dt><dd>{difficulty}</dd></div>
              </dl>
              <ol aria-label="测评流程"><li>选择范围</li><li>AI 命题</li><li>完成作答</li><li>看见自己的进步</li></ol>
            </div>
          </div>
        </PaperCard>
        <PaperCard title="可参加考试" className={styles.examAvailableCard} extra={<Button type="link" onClick={() => actions.runAction('list-available-exams', '更多试卷')}>更多试卷 <ArrowRight size={14} /></Button>}>
          <div className={styles.availableExamList}>
            {availableExams.map(([name, type, flag, meta, action, examId]) => (
              <div key={String(examId ?? name)}>
                <div className={styles.examThumb} />
                <div><strong>{name}</strong><p>{meta}</p></div>
                <Tag>{type}</Tag>
                {flag && <Tag color="orange">{flag}</Tag>}
                <Button onClick={() => { if (typeof examId === 'number') startExam(examId, flag === '已评分'); }}>{action}</Button>
              </div>
            ))}
            {!availableExams.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可参加的正式测评，请先生成试卷" />}
          </div>
        </PaperCard>
      </div>
      <div className={styles.examRight}>
        <ArtifactPanel title="题目与思维导图产物" items={generatedItems} module="exam" onSearch={(keyword) => actions.runAction('list-available-exams', keyword || '考试产物', { keyword, module: 'exam' })} />
        <PaperCard title="本周成绩趋势" extra={<Select size="small" defaultValue="7" options={[{ value: '7', label: '近7天' }]} />}>
          <div className={styles.examChart}>
            <TrendChart data={backendExams.length
              ? backendExams
                  .filter((e) => e.status === 'completed' && e.score != null && e.total_score > 0)
                  .slice(0, 7)
                  .reverse()
                  .map((exam) => ({
                    label: (exam.created_at ?? '').slice(5, 10) || `#${exam.id}`,
                    value: Math.round((Number(exam.score) / Number(exam.total_score)) * 100)
                  }))
              : (data?.trend ?? [])
            } />
          </div>
        </PaperCard>
        <PaperCard title="最近考试记录" extra={<Button type="link" onClick={() => actions.runAction('list-exam-records', '全部记录')}>全部记录 <ArrowRight size={14} /></Button>}>
          <div className={styles.examRecentList}>
            {recentExams.map(([name, type, score, time, status]) => (
              <div key={name}>
                <ClipboardCheck size={15} />
                <strong>{name}</strong>
                <Tag>{type}</Tag>
                <b>{score}</b>
                <time>{time}</time>
                <span data-status={status}>{status}</span>
              </div>
            ))}
            {!recentExams.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无考试记录" />}
          </div>
        </PaperCard>
        <PaperCard title="薄弱点诊断" extra={<Button type="link" onClick={() => actions.runAction('list-weak-points', '全部薄弱点')}>全部薄弱点 <ArrowRight size={14} /></Button>}>
          <div className={styles.examWeakList}>
            {weakPoints.map(([label, value]) => (
              <div key={String(label)}>
                <Database size={15} />
                <span>{label}</span>
                <em>正确率 {value}%</em>
                <i><b style={{ width: `${value}%` }} /></i>
                <Button size="small" loading={actions.isRunning('review-weak-point', String(label))} onClick={() => actions.runAction('review-weak-point', String(label), { mastery: value })}>去复习</Button>
              </div>
            ))}
            {!weakPoints.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="完成测评后形成薄弱点诊断" />}
          </div>
        </PaperCard>
      </div>
      <PaperCard title="历史测评记录" extra={<Space><Select size="small" defaultValue="all" options={[{ value: 'all', label: '全部类型' }]} /><Select size="small" defaultValue="30" options={[{ value: '30', label: '近30天' }]} /></Space>}>
        <div className={styles.examHistory}>
          <div><span>考试名称</span><span>类型</span><span>题量</span><span>得分</span><span>正确率</span><span>用时</span><span>完成时间</span><span>状态</span><span>操作</span></div>
          {backendExams.length > 0 ? backendExams.map((exam) => {
            const accuracy = exam.score != null && exam.total_score > 0
              ? Math.round((Number(exam.score) / Number(exam.total_score)) * 100)
              : null;
            const spentMin = exam.time_spent_sec && exam.time_spent_sec > 0
              ? Math.floor(exam.time_spent_sec / 60)
              : null;
            const displayTime = exam.completed_at?.replace('T', ' ').slice(0, 16) ?? exam.created_at?.replace('T', ' ').slice(0, 16) ?? '';
            const statusText = exam.status === 'completed' ? '已完成' : '未完成';
            return (
              <div key={`history-${exam.id}`}>
                <span>{`${EXAM_TAB_MAP_REVERSE[exam.exam_type] ?? '测评'}试卷`}</span>
                <span>{EXAM_TAB_MAP_REVERSE[exam.exam_type] ?? exam.exam_type}</span>
                <span>{exam.question_count ?? '-'} 题</span>
                <span>{exam.score == null ? '-' : `${exam.score} 分`}</span>
                <span>{accuracy == null ? '-' : `${accuracy}%`}</span>
                <span>{spentMin == null ? '-' : `${spentMin} 分钟`}</span>
                <span>{displayTime}</span>
                <span data-status={statusText}>{statusText}</span>
                <Button
                  size="small"
                  onClick={() => {
                    startExam(exam.id, exam.status === 'completed');
                  }}
                >
                  {statusText === '已完成' ? '查看报告' : '继续作答'}
                </Button>
              </div>
            );
          }) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史测评记录" />}
        </div>
      </PaperCard>
      </section>
      </details>

      {/* 生成试卷流程动画弹窗 */}
      <Modal getContainer={document.body} open={generator.phase === 'flow' || generator.phase === 'ready'} footer={null} width={640} closable={false} maskClosable={false} title={<span><Sparkles size={16} style={{ marginRight: 6, verticalAlign: -3 }} />{generator.phase === 'ready' ? '试卷已就绪' : '智能体正在出题…'}</span>}>
        <div className={styles.agentFlow}>
          {generator.flowSteps.map((step) => (
            <div key={step.id} className={styles.agentFlowStep} data-state={step.state}>
              <div className={styles.agentFlowIcon}>
                {step.state === 'done' ? <CheckCircle2 size={16} /> : step.state === 'running' ? <span className={styles.agentFlowPulse} /> : <Circle size={16} />}
              </div>
              <div><strong>{step.label}</strong><p>{step.detail}</p></div>
              {step.state === 'running' && <span className={styles.agentFlowSpin} />}
            </div>
          ))}
        </div>
        {generator.phase === 'ready' && (
          <div className={styles.examActions} style={{ marginTop: 14 }}>
            <Button type="primary" onClick={() => generator.examId && startExam(generator.examId)}>确认开始考试</Button>
            <Button onClick={() => setGenerator({ phase: 'idle', flowSteps: [] })}>稍后</Button>
          </div>
        )}
      </Modal>

      {/* ExamRunner 全屏考试 */}
      {runner.open && runner.examId && (
        <ExamRunner
          examId={runner.examId}
          initialPhase={runner.phase}
          serverResult={runner.result}
          onClose={() => {
            // 关闭时主动广播刷新事件，确保趋势图/最近考试/薄弱点/历史测评
            // 这 4 个组件在 ExamRunner 全程中都可能因新卷入库/提交交卷/暂存离开
            // 等原因需要回拉数据。EventEmitter → useRequest.retry
            window.dispatchEvent(new CustomEvent('kaki-artifact-generated'));
            setRunner({ open: false, phase: 'confirm' });
          }}
          onComplete={(result) => {
            window.dispatchEvent(new CustomEvent('kaki-artifact-generated'));
            setRunner({ open: true, examId: runner.examId, phase: 'result', result });
          }}
        />
      )}
    </>
  );
}

interface ExamQuestion {
  id: string;
  type: 'choice' | 'multi_choice' | 'judge' | 'fill' | 'short_answer' | 'code' | 'essay' | string;
  title?: string;
  content?: string;
  options?: string[];
  points?: number;
  knowledge_point?: string;
}

interface ExamDetail {
  id: number;
  exam_type: string;
  chapter_ids: string[];
  questions: ExamQuestion[];
  time_limit_min: number;
  total_score: number;
  status: string;
  mode_contract?: { feedback_policy?: string; source_kind?: string; specification?: string; solutions_hidden_until?: string };
  learning_update?: { evaluation_status: string; evaluation_revision?: number | null };
}

interface ExamResultRow { index: number; correct: boolean; earned: number; topic: string; correctAnswer: unknown; userAnswer: unknown; explanation?: string }
interface ExamResultProjection { score: number; total: number; grading: ExamResultRow[]; learningUpdate?: { evaluation_status: string; evaluation_revision?: number | null } }

interface ExamRunnerProps {
  examId: number;
  initialPhase: 'confirm' | 'running' | 'result';
  serverResult?: ExamResultProjection;
  onClose: () => void;
  onComplete: (result: ExamResultProjection) => void;
}

function ExamRunner({ examId, initialPhase, serverResult, onClose, onComplete }: ExamRunnerProps) {
  const { message } = AntdApp.useApp();
  const [phase, setPhase] = useState(initialPhase);
  const [exam, setExam] = useState<ExamDetail | null>(null);
  const [answers, setAnswers] = useState<Record<number, unknown>>({});
  const [remainingSec, setRemainingSec] = useState(0);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [checkingIndex, setCheckingIndex] = useState<number | null>(null);
  const [practiceFeedback, setPracticeFeedback] = useState<Record<number, { correct: boolean; correctAnswer: unknown; explanation: string }>>({});
  const [mistakeBookBusy, setMistakeBookBusy] = useState(false);

  // 把本次判分结果交给错题本智能体：只依据真实判分记录，无错题时后端会明确拒绝。
  const generateMistakeBookForExam = async () => {
    if (mistakeBookBusy) return;
    setMistakeBookBusy(true);
    try {
      const book = await createMistakeBook({ exam_id: examId });
      message.success('错题本已生成');
      if (book.artifact_id) window.open(`/artifacts/${book.artifact_id}?kind=mistake_book`, '_blank');
    } catch (err) {
      message.error(err instanceof Error ? err.message : '错题本生成失败，请稍后重试。');
    } finally {
      setMistakeBookBusy(false);
    }
  };

  // 拉取考试详情
  useEffect(() => {
    let active = true;
    moduleApi.getExam(examId)
      .then((data) => {
        if (!active) return;
        const detail = (data as unknown as { data?: ExamDetail } & ExamDetail).data ?? (data as unknown as ExamDetail);
        setExam(detail);
        setRemainingSec(Number(detail.time_limit_min ?? 60) * 60);
      })
      .catch((err) => {
        message.error(`加载试卷失败：${err instanceof Error ? err.message : '未知错误'}`);
        onClose();
      });
    return () => { active = false; };
  }, [examId, message, onClose]);

  // 倒计时
  useEffect(() => {
    if (phase !== 'running' || !exam) return;
    const id = window.setInterval(() => {
      setRemainingSec((s) => {
        if (s <= 1) {
          window.clearInterval(id);
          // 自动交卷
          void submit();
          return 0;
        }
        return s - 1;
      });
      setElapsedSec((s) => s + 1);
    }, 1000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, exam]);

  const setAnswer = (index: number, value: unknown) => {
    setAnswers((prev) => ({ ...prev, [index]: value }));
  };

  const submit = async () => {
    if (submitting || !exam) return;
    setSubmitting(true);
    try {
      const ordered = exam.questions.map((_, i) => answers[i] ?? null);
      const result = await moduleApi.submitExam(examId, ordered, elapsedSec);
       const data = (result as { data?: { score: number; total_score: number; grading: Array<{ question_index: number; correct: boolean; earned: number; topic: string; correct_answer: unknown; answer: unknown; explanation?: string }>; learning_update?: { evaluation_status: string; evaluation_revision?: number | null } } }).data ?? (result as unknown as { score: number; total_score: number; grading: Array<{ question_index: number; correct: boolean; earned: number; topic: string; correct_answer: unknown; answer: unknown; explanation?: string }>; learning_update?: { evaluation_status: string; evaluation_revision?: number | null } });
      onComplete({
        score: data.score,
        total: data.total_score,
        grading: data.grading.map((g) => ({
          index: g.question_index,
          correct: g.correct,
          earned: g.earned,
          topic: g.topic,
          correctAnswer: g.correct_answer,
           userAnswer: g.answer,
           explanation: g.explanation,
         })),
        learningUpdate: data.learning_update,
       });
      setPhase('result');
      // 触发画像刷新广播
      window.dispatchEvent(new CustomEvent('kaki-artifact-generated'));
    } catch (err) {
      message.error(`交卷失败：${err instanceof Error ? err.message : '未知错误'}`);
    } finally {
      setSubmitting(false);
    }
  };

  const checkPracticeQuestion = async (index: number) => {
    if (!exam || checkingIndex !== null || answers[index] == null || answers[index] === '') return;
    setCheckingIndex(index);
    try {
      const result = await moduleApi.checkExamAnswer(exam.id, index, answers[index]);
      setPracticeFeedback((current) => ({ ...current, [index]: { correct: result.correct, correctAnswer: result.correct_answer, explanation: result.explanation } }));
    } catch (err) {
      message.error(err instanceof Error ? err.message : '暂时无法检查本题');
    } finally { setCheckingIndex(null); }
  };

  if (!exam) {
    const loadingView = (
      <div className={styles.examRunnerMask} data-testid="exam-runner">
        <div className={styles.examRunnerCard}>
          <div className={styles.examRunnerLoading}>正在加载试卷…</div>
        </div>
      </div>
    );
    if (typeof document === 'undefined') return loadingView;
    return createPortal(loadingView, document.body);
  }

  const mm = Math.floor(remainingSec / 60).toString().padStart(2, '0');
  const ss = (remainingSec % 60).toString().padStart(2, '0');
  const answeredCount = Object.keys(answers).filter((k) => answers[Number(k)] !== null && answers[Number(k)] !== '').length;

  const runnerView = (
    <div className={styles.examRunnerMask} data-testid="exam-runner" data-phase={phase}>
      <div className={styles.examRunnerCard}>
        {/* 顶栏：标题 + 倒计时 + 关闭 */}
        <header className={styles.examRunnerHeader}>
          <div>
            <h2>{phase === 'result' ? '考试结果' : `${EXAM_TAB_MAP_REVERSE[exam.exam_type] ?? exam.exam_type}试卷`}</h2>
            <p>{phase === 'result' ? '本次考试已结束' : `共 ${exam.questions.length} 题 · 总分 ${exam.total_score} · 用时上限 ${exam.time_limit_min} 分钟`}</p>
          </div>
          {phase === 'running' && (
            <div className={styles.examRunnerTimer} data-warning={remainingSec < 60 ? 'true' : undefined}>
              <Hourglass size={16} />
              <span>剩余 {mm}:{ss}</span>
            </div>
          )}
          {phase === 'result' && (
            <>
              <Button loading={mistakeBookBusy} onClick={() => void generateMistakeBookForExam()}>生成错题本</Button>
              <Button onClick={onClose}>关闭</Button>
            </>
          )}
        </header>

        {/* confirm：确认页 */}
        {phase === 'confirm' && (
          <div className={styles.examRunnerConfirm}>
            <h3>请确认开始考试</h3>
            <ul>
              <li>试卷类型：<b>{EXAM_TAB_MAP_REVERSE[exam.exam_type] ?? exam.exam_type}</b></li>
              <li>章节范围：<b>{exam.chapter_ids.join('、')}</b></li>
              <li>题目数量：<b>{exam.questions.length} 题</b></li>
              <li>总分：<b>{exam.total_score} 分</b></li>
              <li>用时上限：<b>{exam.time_limit_min} 分钟</b></li>
            </ul>
            <div className={styles.examRunnerRules}>
              <strong>考试规则：</strong>
              <ol>
                <li>开始后系统自动计时，超时自动交卷。</li>
                <li>选择题、填空题、判断题为客观题，系统自动判分。</li>
                <li>简答题与编程题由评分标准逐项核对，不按字数给分。</li>
                <li>{exam.exam_type === 'quiz' ? '章节练习可逐题检查；最终仍需统一提交形成学习证据。' : '考试过程中不显示答案，统一交卷后查看报告。'}</li>
              </ol>
            </div>
            <div className={styles.examRunnerConfirmActions}>
              <Button onClick={onClose}>取消</Button>
              <Button type="primary" size="large" onClick={() => setPhase('running')}>确认开始考试</Button>
            </div>
          </div>
        )}

        {/* running：答题页 */}
        {phase === 'running' && (
          <div className={styles.examRunnerBody}>
            <div className={styles.examRunnerProgress}>
              <span>已答 {answeredCount} / {exam.questions.length} 题</span>
              <i><b style={{ width: `${(answeredCount / exam.questions.length) * 100}%` }} /></i>
            </div>
            <ol className={styles.examRunnerQuestions}>
              {exam.questions.map((q, i) => (
                <li key={q.id ?? i} className={styles.examRunnerQuestion} data-answered={answers[i] !== undefined && answers[i] !== null && answers[i] !== '' ? 'true' : undefined}>
                  <header>
                    <Tag color={q.type === 'choice' || q.type === 'multi_choice' || q.type === 'judge' ? 'orange' : 'blue'}>{questionTypeLabel(q.type)}</Tag>
                    <strong>第 {i + 1} 题 · {q.points ?? 10} 分</strong>
                    <em>{q.knowledge_point ?? ''}</em>
                  </header>
                  <div className={styles.examRunnerStem}>{q.title ?? q.content ?? '（题目内容）'}</div>
                  {renderAnswerInput(q, answers[i], (v) => setAnswer(i, v))}
                  {exam.exam_type === 'quiz' && <div className={styles.practiceFeedback} data-state={practiceFeedback[i] ? (practiceFeedback[i].correct ? 'correct' : 'incorrect') : undefined}>
                    <Button size="small" loading={checkingIndex === i} disabled={answers[i] == null || answers[i] === ''} onClick={() => void checkPracticeQuestion(i)}>检查这一题</Button>
                    {practiceFeedback[i] && <p><strong>{practiceFeedback[i].correct ? '回答正确' : '还需要再想一想'}</strong><span>{practiceFeedback[i].explanation}</span>{!practiceFeedback[i].correct && <small>参考答案：{formatAnswerForDisplay(practiceFeedback[i].correctAnswer)}</small>}</p>}
                  </div>}
                </li>
              ))}
            </ol>
            <footer className={styles.examRunnerFooter}>
              <span>已用时 {Math.floor(elapsedSec / 60)} 分 {elapsedSec % 60} 秒</span>
              <Space>
                <Button onClick={onClose}>暂存离开</Button>
                <Button type="primary" loading={submitting} onClick={submit}>提交答卷</Button>
              </Space>
            </footer>
          </div>
        )}

        {/* result：结果页 */}
        {phase === 'result' && (
          <div className={styles.examRunnerResult}>
            <ResultPanel exam={exam} serverResult={serverResult} onClose={onClose} />
          </div>
        )}
      </div>
    </div>
  );
  if (typeof document === 'undefined') return runnerView;
  return createPortal(runnerView, document.body);
}

const EXAM_TAB_MAP_REVERSE: Record<string, string> = {
  mock: '模拟考试',
  real_exam: '能力测评',
  quiz: '章节练习',
  weak_reinforcement: '薄弱点强化',
  custom: '自定义测评',
  derivation: '推导专练',
  compare: '算法对比',
  code_lab: '代码实验',
  report: '实验报告'
};

function renderAnswerInput(q: ExamQuestion, value: unknown, onChange: (v: unknown) => void) {
  const t = q.type;
  if (t === 'choice' || t === 'judge') {
    const options = t === 'judge' ? ['A. 正确', 'B. 错误'] : (q.options ?? []);
    return (
      <div className={styles.examRunnerChoice}>
        {options.map((opt) => (
          <label key={opt}>
            <input type="radio" name={`q-${q.id}`} value={opt} checked={value === opt} onChange={() => onChange(opt)} />
            <span>{opt}</span>
          </label>
        ))}
      </div>
    );
  }
  if (t === 'multi_choice') {
    const selected = Array.isArray(value) ? value : [];
    return (
      <div className={styles.examRunnerChoice}>
        {(q.options ?? []).map((opt) => (
          <label key={opt}>
            <input type="checkbox" checked={selected.includes(opt)} onChange={(e) => {
              onChange(e.target.checked ? Array.from(new Set([...selected, opt])) : selected.filter((s: string) => s !== opt));
            }} />
            <span>{opt}</span>
          </label>
        ))}
      </div>
    );
  }
  if (t === 'fill') {
    return <Input value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} placeholder="请输入答案" />;
  }
  // short_answer / code / essay / 兜底
  return <Input.TextArea value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} placeholder={t === 'code' ? '请输入代码…' : '请输入你的回答…'} autoSize={{ minRows: 3, maxRows: 8 }} />;
}

interface ResultPanelProps {
  exam: ExamDetail;
  serverResult?: ExamResultProjection;
  onClose: () => void;
}

function ResultPanel({ exam, serverResult, onClose }: ResultPanelProps) {
  const { message } = AntdApp.useApp();
  // 优先用 onComplete 传过来的 serverResult(后端真实判分)
  // 仅在缺失/失败时回退到 GET /exams/{id} 二次拉取
  const [fallback, setFallback] = useState<{ score: number; total: number; grading: Array<{ question_index: number; correct: boolean; earned: number; topic: string; correct_answer: unknown; answer: unknown; explanation?: string }> } | null>(null);
  useEffect(() => {
    if (serverResult) return;
    moduleApi.getExam(exam.id).then((data) => {
      const detail = (data as { data?: ExamDetail & { score?: number; total_score?: number; grading?: Array<{ question_index: number; correct: boolean; earned: number; topic: string; correct_answer: unknown; answer: unknown; explanation?: string }> } }).data ?? (data as unknown as ExamDetail & { score?: number; total_score?: number; grading?: Array<{ question_index: number; correct: boolean; earned: number; topic: string; correct_answer: unknown; answer: unknown; explanation?: string }> });
      setFallback({
        score: detail.score ?? 0,
        total: detail.total_score ?? exam.total_score,
        grading: detail.grading ?? []
      });
    }).catch((err) => message.error(`加载结果失败：${err instanceof Error ? err.message : '未知错误'}`));
  }, [exam, serverResult, message]);

  // 把 serverResult 与 fallback 归一化到统一结构
  const normalized: { score: number; total: number; rows: ExamResultRow[] } | null = serverResult
    ? { score: serverResult.score, total: serverResult.total, rows: serverResult.grading }
    : fallback
      ? { score: fallback.score, total: fallback.total, rows: fallback.grading.map((g) => ({ index: g.question_index, correct: g.correct, earned: g.earned, topic: g.topic, correctAnswer: g.correct_answer, userAnswer: g.answer, explanation: g.explanation })) }
      : null;

  if (!normalized) {
    return <div className={styles.examRunnerLoading}>正在加载成绩…</div>;
  }

  const correctCount = normalized.rows.filter((g) => g.correct).length;
  const accuracy = normalized.total > 0 ? Math.round((normalized.score / normalized.total) * 100) : 0;
  const stableTopics = Array.from(new Set(normalized.rows.filter((row) => row.correct && row.topic).map((row) => row.topic))).slice(0, 4);
  const strengthenTopics = Array.from(new Set(normalized.rows.filter((row) => !row.correct && row.topic).map((row) => row.topic))).slice(0, 4);
  const learningUpdate = serverResult?.learningUpdate ?? exam.learning_update;
  const evaluationStatus = learningUpdate?.evaluation_status;

  return (
    <div>
      <div className={styles.examRunnerResultHero}>
        <div><strong>本次得分</strong><h2>{normalized.score}<span> / {normalized.total}</span></h2></div>
        <div><strong>正确率</strong><h2>{accuracy}%</h2></div>
        <div><strong>答对题数</strong><h2>{correctCount}<span> / {exam.questions.length}</span></h2></div>
      </div>
      <div className={styles.examLearningImpact} data-testid="exam-learning-impact">
        <div><small>本次表现稳定</small><strong>{stableTopics.length ? stableTopics.join('、') : '本次还没有形成稳定表现'}</strong></div>
        <div><small>需要继续加强</small><strong>{strengthenTopics.length ? strengthenTopics.join('、') : '本次未发现明确错题主题'}</strong></div>
        <div><small>为什么</small><strong>{strengthenTopics.length ? `这些主题出现了可核验错题；正式掌握判断仍以 Evaluator 为准。` : '依据本次逐题批改记录；单次高分不会直接等同于稳定掌握。'}</strong></div>
        <div><small>下一步</small><strong>{strengthenTopics.length ? `先围绕「${strengthenTopics[0]}」完成一次不同形式的练习。` : '在新情境中再做一次迁移验证。'}</strong></div>
        <div><small>学习状态</small><strong>{evaluationStatus === 'evaluated' ? `正式评估已更新${learningUpdate?.evaluation_revision ? `（第 ${learningUpdate.evaluation_revision} 版）` : ''}` : evaluationStatus === 'evaluation_pending' ? '考试证据已保存，正式评估等待处理' : '考试证据已保存；当前试卷未绑定正式学习节点'}</strong></div>
      </div>
      <ol className={styles.examRunnerResultList}>
        {normalized.rows.map((g) => {
          const q = exam.questions[g.index];
          const stem = q?.title ?? q?.content ?? '（题目内容）';
          const userAns = formatAnswerForDisplay(g.userAnswer);
          const correctAns = formatAnswerForDisplay(g.correctAnswer);
          return (
            <li key={g.index} data-correct={g.correct ? 'true' : 'false'}>
              <div className={styles.examRunnerResultItemMain}>
                <span className={styles.examRunnerResultItemIndex}>第 {g.index + 1} 题</span>
                <strong className={styles.examRunnerResultItemStem}>{stem}</strong>
                {q?.knowledge_point && <em className={styles.examRunnerResultItemTopic}>{q.knowledge_point}</em>}
              </div>
              <div className={styles.examRunnerResultItemMeta}>
                <span className={styles.examRunnerResultItemState}>{g.correct ? '答对' : '答错'}</span>
                <b className={styles.examRunnerResultItemEarned}>+{g.earned} 分</b>
              </div>
              {!g.correct && (userAns || correctAns) && (
                <div className={styles.examRunnerResultItemDiff}>
                  {userAns && <span><b>你的答案：</b><i>{userAns}</i></span>}
                  {correctAns && <span><b>参考答案：</b><i>{correctAns}</i></span>}
                </div>
              )}
              {g.explanation && <p className={styles.examResultExplanation}><b>错因与提示：</b>{g.explanation}</p>}
            </li>
          );
        })}
      </ol>
      <div className={styles.examRunnerResultActions}>
        <Button onClick={onClose}>返回考试中心</Button>
      </div>
    </div>
  );
}

function formatAnswerForDisplay(value: unknown): string {
  if (value == null) return '';
  if (Array.isArray(value)) return value.map((v) => String(v)).join(' / ');
  if (typeof value === 'object') {
    try { return JSON.stringify(value); } catch { return String(value); }
  }
  return String(value);
}

/**
 * 画像页头像：与顶部导航共用 UserAvatar（同一 users.avatar_url 事实），
 * 支持本地选图 -> 预览 -> 保存 / 恢复默认，保存后立即同步全局。
 */
function ProfileAvatarEditor() {
  const { message } = AntdApp.useApp();
  const hasAvatar = useAuthStore((state) => Boolean(state.user?.avatarUrl));
  const applyAvatar = useAuthStore((state) => state.applyAvatar);
  const fileRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<{ url: string; name: string; file: File } | null>(null);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);

  const pickFile = () => fileRef.current?.click();

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
      message.error('头像只支持 PNG / JPG / WebP 图片');
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      message.error('头像图片不能超过 2MB');
      return;
    }
    setPreview({ url: URL.createObjectURL(file), name: file.name, file });
  };

  const closePreview = () => {
    if (preview) URL.revokeObjectURL(preview.url);
    setPreview(null);
  };

  const save = async () => {
    if (!preview) return;
    setSaving(true);
    try {
      const avatarUrl = await authApi.uploadAvatar(preview.file);
      applyAvatar(avatarUrl);
      message.success('头像已更新，顶部导航已同步');
      closePreview();
    } catch {
      /* 拦截器已提示 */
    } finally {
      setSaving(false);
    }
  };

  const restoreDefault = async () => {
    setRemoving(true);
    try {
      await authApi.deleteAvatar();
      applyAvatar(null);
      message.success('已恢复默认头像');
    } catch {
      /* 拦截器已提示 */
    } finally {
      setRemoving(false);
    }
  };

  return (
    <div className={styles.profileAvatarEditor}>
      <button
        type="button"
        className={styles.profileAvatarButton}
        onClick={pickFile}
        title="更换头像"
        data-testid="profile-avatar-change"
      >
        <UserAvatar size={94} className={styles.profileAvatarImage} />
        <span className={styles.profileAvatarHint}><Camera size={13} /> 更换</span>
      </button>
      <input
        ref={fileRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        hidden
        onChange={onFile}
        data-testid="profile-avatar-input"
      />
      <div className={styles.profileAvatarActions}>
        <Button size="small" type="link" onClick={pickFile}>更换头像</Button>
        {hasAvatar && (
          <Button
            size="small"
            type="link"
            loading={removing}
            onClick={() => void restoreDefault()}
            data-testid="profile-avatar-restore"
          >
            恢复默认
          </Button>
        )}
      </div>
      <Modal
        open={Boolean(preview)}
        title="预览新头像"
        onCancel={closePreview}
        onOk={() => void save()}
        confirmLoading={saving}
        okText="保存头像"
        cancelText="取消"
        destroyOnHidden
      >
        {preview && (
          <div className={styles.profileAvatarPreview} data-testid="profile-avatar-preview">
            <img src={preview.url} alt="新头像预览" />
            <small>{preview.name} · 保存后顶部导航会立即同步</small>
          </div>
        )}
      </Modal>
    </div>
  );
}

function profileDimensionSourceLabel(source: string) {
  const known: Record<string, string> = {
    学习路径章节进度: '来自你的学习路径',
    画像认知字段与能力矩阵: '来自画像与能力测评',
    画像认知字段: '来自画像记录',
    复习记录与画像认知字段: '来自你的复习记录',
    正式测评与错题记录: '来自测评与错题',
    学习行为记录: '来自你的学习行为',
    动机画像与任务行为: '来自学习动机与行动',
    学习时长与节奏记录: '来自学习时长与节奏',
    '兴趣、动机与资源偏好': '来自你的兴趣与偏好'
  };
  return known[source] ?? '来自你的学习记录';
}

function AccountSettingsSection() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const user = useAuthStore((state) => state.user);
  const [form] = Form.useForm<{ current_password: string; new_password: string; confirm: string }>();
  const [saving, setSaving] = useState(false);

  const submit = async (values: { current_password: string; new_password: string; confirm: string }) => {
    if (values.new_password !== values.confirm) {
      message.error('两次输入的新密码不一致');
      return;
    }
    setSaving(true);
    try {
      await authApi.changePassword({ current_password: values.current_password, new_password: values.new_password });
      message.success('密码已更新，下次登录请使用新密码');
      form.resetFields();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '密码修改失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className={styles.personalCenter} data-testid="account-settings-section">
      <section className={styles.profilePortrait} data-testid="account-settings-overview">
        <div className={styles.profileMist} aria-hidden="true"><i /><i /><i /></div>
        <header><span>账户设置</span><h1>你的账户</h1><p>管理登录信息，学习内容会照常保留。</p></header>
        <div className={styles.profileFloatingCards}>
          <article><small>用户名</small><strong>{user?.username || '—'}</strong></article>
          <article><small>邮箱</small><strong>{user?.email || '未填写'}</strong></article>
          <article><small>头像</small><strong>个人中心可更换</strong></article>
        </div>
        <footer><span>需要修改学习画像？</span><Link to="/profile">返回个人中心 <ArrowRight size={14} /></Link></footer>
      </section>
      <details className={styles.personalDetails} open>
        <summary>修改密码</summary>
        <section className={styles.profileGrid}>
          <PaperCard title="修改密码">
            <Form form={form} layout="vertical" requiredMark={false} onFinish={submit} className={styles.accountSettingsForm} autoComplete="off">
              <Form.Item name="current_password" label="当前密码" rules={[{ required: true, message: '请输入当前密码' }]}>
                <Input.Password data-testid="settings-current-password" placeholder="请输入当前密码" autoComplete="current-password" />
              </Form.Item>
              <Form.Item name="new_password" label="新密码" rules={[{ required: true, message: '请输入新密码' }, { min: 6, message: '新密码至少需要 6 个字符' }]}>
                <Input.Password data-testid="settings-new-password" placeholder="至少 6 个字符" autoComplete="new-password" />
              </Form.Item>
              <Form.Item name="confirm" label="确认新密码" dependencies={['new_password']} rules={[
                { required: true, message: '请再次输入新密码' },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    if (!value || getFieldValue('new_password') === value) return Promise.resolve();
                    return Promise.reject(new Error('两次输入的新密码不一致'));
                  }
                })
              ]}>
                <Input.Password data-testid="settings-confirm-password" placeholder="请再次输入新密码" autoComplete="new-password" />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={saving} data-testid="settings-change-password-submit">更新密码</Button>
            </Form>
          </PaperCard>
        </section>
      </details>
    </section>
  );
}

function ProfileBody({ data, section }: { data: any; section?: string | null }) {
  const actions = useModuleActions('profile');
  const user = useAuthStore((state) => state.user);
  const [actionEvents, setActionEvents] = useState<ActionEvent[]>(() => getActionEvents());
  const profileSignals = getProfileSignals(actionEvents);
  useEffect(() => subscribeActionEvents(setActionEvents), []);
  const profile = data?.profile;
  const weakPoints: Array<[string, string, number | null]> = data?.weakPoints?.length ? data.weakPoints.map((item: any) => [item.label, item.meta ?? (item.value == null ? '证据不足' : `掌握率 ${item.value}%`), item.value ?? null]) : [];
  const habits: Array<[string, string, string]> = data?.habits?.length ? data.habits.map((item: any) => [item.label, item.value, item.desc]) : [];
  const suggestions: Array<[string, string]> = data?.suggestions?.length ? data.suggestions.map((item: any) => [item.title, item.desc]) : [];
  const paths: Array<[string, string]> = data?.paths?.length ? data.paths.map((item: any) => [item.title, item.meta]) : [];
  const reportMetrics = (data?.reportMetrics ?? []) as Array<{ label: string; value: number }>;
  const aiSummary = (data?.aiSummary ?? []) as string[];
  const tenDimensions = buildProfileDimensions(profile, data?.radar ?? [], data?.profileDimensionEvidence ?? {});
  const coreRadar = tenDimensions.slice(0, 6).map((item) => ({
    label: item.label.slice(0, 4),
    value: item.value ?? 0
  }));
  const preferenceTags = profilePreferenceTags(profile);
  const evidencedDimensions = tenDimensions.filter((item) => item.evidence !== 'insufficient');
  const profileTrend = (data?.trend ?? []) as Array<{ label: string; value: number }>;
  const hasProfileTrend = profileTrend.some((item) => Number(item.value) > 0);
  const onboardingEntries = ((profile?.profile_data as Record<string, unknown> | undefined)?.profile_onboarding as Record<string, unknown> | undefined)?.entries as Record<string, { value?: unknown }> | undefined;
  const confirmedBaseline = typeof onboardingEntries?.knowledge_baseline?.value === 'string' ? onboardingEntries.knowledge_baseline.value : '';
  const confirmedPreference = typeof onboardingEntries?.learning_preference?.value === 'string' ? onboardingEntries.learning_preference.value : '';
  const confirmedRhythm = typeof onboardingEntries?.study_rhythm?.value === 'string' ? onboardingEntries.study_rhythm.value : '';
  const rawIdentityName = String(user?.username || '').trim();
  const identityName = !rawIdentityName || /^(vi\d+|test|user_?\d+|[\w-]{20,})/i.test(rawIdentityName) || /[_-].*\d/.test(rawIdentityName) ? '柿同学' : rawIdentityName;
  const currentPath = paths[0];

  if (section === 'settings') {
    return <AccountSettingsSection />;
  }

  return (
    <section className={styles.personalCenter} data-testid="personal-center-first-screen">
      <section className={styles.profilePortrait} data-testid="kaki-view-of-you">
        <div className={styles.profileMist} aria-hidden="true"><i /><i /><i /></div>
        <header><span>陪伴式学习画像</span><h1>柿小吉眼中的你</h1><p>我正在通过你的每一次学习，慢慢了解你。</p></header>
        <div className={styles.profileFloatingCards}>
          <article><small>学习方式</small><strong>{confirmedPreference || '正在发现你更舒服的学习方式'}</strong></article>
          <article><small>学习节奏</small><strong>{confirmedRhythm || '从真实学习时间里慢慢认识'}</strong></article>
          <article><small>当前目标</small><strong>{currentPath?.[0] || profile?.target_role || '先一起确定一个目标'}</strong></article>
        </div>
        <div className={styles.learningScroll} data-state={profileSignals.total ? 'learning' : 'resting'} aria-label="学习卷轴">
          <div className={styles.scrollBanner} />
          <div className={styles.scrollContent}>
            <small>{profileSignals.total ? '卷轴正在展开' : '卷轴等待你的第一步'}</small>
            <strong>{profileSignals.recentLabel}</strong>
            <span>{profileSignals.lastUpdatedAt ? formatActionEventTime(profileSignals.lastUpdatedAt) : '开始学习后，每次真实变化都会在这里留下微光。'}</span>
          </div>
        </div>
        <footer><span>你好，{identityName}。这里不展示冰冷分数，只记录柿小吉真正了解到的你。</span><Link to="/onboarding?resume=1">告诉柿小吉更多 <ArrowRight size={14} /></Link></footer>
      </section>
      <details className={styles.personalDetails}>
        <summary>查看完整学习画像与成长详情</summary>
        <section className={styles.profileGrid}>
      <PaperCard title="柿小吉眼中的你">
        <div className={styles.profileSummary}>
          <ProfileAvatarEditor />
          <div>
            <h3>{profile?.major ?? '还没有填写专业'} {profile?.grade_level && <Tag color="orange">{profile.grade_level}</Tag>}</h3>
            <span>目标方向</span>
            <strong>{profile?.target_role ?? '学习目标待确认'}</strong>
            <Link to="/onboarding?resume=1">告诉柿小吉更多 <ArrowRight size={14} /></Link>
          </div>
          <div className={styles.profileAiText}>
            <strong>柿小吉的印象</strong>
            <p>{(() => {
              // 优先从规范化画像读取，回退到旧字段（已通过后端适配器填充）
              const np = (profile?.profile_data as Record<string, unknown> | undefined)?.normalized_profile as Record<string, unknown> | undefined;
              const goal = np?.learning_goal as Record<string, unknown> | undefined;
              const mot = (np?.motivation ?? profile?.motivation_profile) as Record<string, unknown> | undefined;
              const careerTarget = (profile?.motivation_profile as Record<string, unknown> | undefined)?.career_target ?? goal?.primary ?? mot?.primary;
              return careerTarget ? `目标职业：${careerTarget}` : '柿小吉还在观察你的学习方式。完成一次练习或测评后，这里会写出对你的具体印象。';
            })()}</p>
            {(confirmedBaseline || confirmedPreference || confirmedRhythm) && <div className={styles.profileConfirmedImpression} data-testid="profile-confirmed-impression">
              <b>这是你确认过的信息</b>
              {confirmedBaseline && <span>当前基础：{confirmedBaseline}</span>}
              {confirmedPreference && <span>更适合的讲法：{confirmedPreference}</span>}
              {confirmedRhythm && <small>学习节奏：{confirmedRhythm}</small>}
            </div>}
            {profile?.updated_at && <small>更新于 {profile.updated_at.replace('T', ' ').slice(0, 16)}</small>}
          </div>
        </div>
      </PaperCard>
      <PaperCard title="能力证据概览" extra={<span className={styles.radarLegend}>分数只来自真实学习记录</span>}>
        <div className={styles.profileEvidenceOverview}>
          <div><strong>{evidencedDimensions.length}</strong><span>已有证据维度</span></div>
          <div><strong>{tenDimensions.length - evidencedDimensions.length}</strong><span>等待证据</span></div>
          <p>只有在学习、练习、测评中积累到真实记录的维度才会显示分数；没有证据的地方会诚实留空，不会用零分冒充。</p>
        </div>
      </PaperCard>
      <PaperCard title="最近的学习足迹" extra={<span className={styles.radarLegend}>来自你的真实操作</span>}>
        <div className={styles.profileNextEvidence}>
          <div className={styles.profileLiveSignals}>
            <div><span>学习动作</span><b data-testid="profile-signal-count">{profileSignals.total}</b></div>
            <div><span>练习与测评</span><b>{profileSignals.practiceCount}</b></div>
            <div><span>与柿小吉协作</span><b>{profileSignals.agentCount}</b></div>
            <small data-testid="profile-last-signal">{profileSignals.lastUpdatedAt ? `${formatActionEventTime(profileSignals.lastUpdatedAt)} ${profileSignals.recentLabel}` : profileSignals.recentLabel}</small>
          </div>
          <div className={styles.profileEvidenceAction}>
            <small>下一步建议</small>
            <strong>{tenDimensions.some((item) => item.evidence === 'insufficient') ? '完成一次练习或测评' : '继续当前的学习内容'}</strong>
            <p>{tenDimensions.some((item) => item.evidence === 'insufficient') ? '结果会真实反映到下面的维度里，不会预填分数。' : '十个维度都已有证据，继续学习可以积累变化趋势。'}</p>
            <Link to={tenDimensions.some((item) => item.evidence === 'insufficient') ? '/exam' : '/learning'}>{tenDimensions.some((item) => item.evidence === 'insufficient') ? '去练习' : '继续学习'} <ArrowRight size={14} /></Link>
          </div>
        </div>
      </PaperCard>
      <PaperCard className={styles.tenDimensionCard} title="能力全景 · 十个维度" extra={<span className={styles.radarLegend}>只展示有真实记录的维度</span>}>
        <section className={styles.tenDimensionLayout} data-testid="profile-ten-dimensions">
          <div className={styles.tenDimensionRadar}>
            <ProfileRadar data={coreRadar} />
            <small>空白的维度代表柿小吉还没有足够依据，完成练习和测评后会逐步点亮。</small>
          </div>
          <div className={styles.dimensionList}>
            {tenDimensions.map((item) => (
              <article key={item.key} data-profile-dimension={item.key} data-evidence={item.evidence}>
                <header><strong>{item.label}</strong><span>{item.value === null ? '数据积累中' : `${item.value} 分`}</span></header>
                <div className={styles.dimensionTrack}><i style={{ width: `${item.value ?? 0}%` }} /></div>
                <footer>
                  <span>{item.evidence === 'verified' ? '证据充分' : item.evidence === 'limited' ? '有一些证据' : '还在积累'}</span>
                  {item.updatedAt && <span>{item.updatedAt.replace('T', ' ').slice(0, 16)}</span>}
                </footer>
                <small>{profileDimensionSourceLabel(item.source)}</small>
              </article>
            ))}
          </div>
          <div className={styles.preferenceEvidence}>
            <strong>兴趣、动机与资源偏好</strong>
            <div>{preferenceTags.length ? preferenceTags.map((tag) => <Tag key={tag}>{tag}</Tag>) : <span>柿小吉还没有足够依据。完善画像或挑选过学习资料后，这里会出现你的偏好。</span>}</div>
          </div>
        </section>
      </PaperCard>
      <PaperCard title="薄弱点分析" extra={<Button type="link" onClick={() => actions.runAction('list-profile-weak-points', '查看全部薄弱点')}>查看全部 <ArrowRight size={14} /></Button>}>
        <div className={styles.profileWeakList}>
          {weakPoints.map(([label, meta, value]) => (
            <div key={String(label)}>
              <span><BookOpen size={17} /></span>
              <strong>{label}</strong>
              <em>{meta}</em>
              <i data-empty={value === null}><b style={{ width: `${value ?? 0}%` }} /></i>
              <Button size="small" loading={actions.isRunning('practice-profile-weak-point', String(label))} onClick={() => actions.runAction('practice-profile-weak-point', String(label), { mastery: value })}>去练习</Button>
            </div>
          ))}
          {!weakPoints.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="完成一次练习或测评后，这里会指出最需要加强的地方" />}
        </div>
      </PaperCard>
      <PaperCard title="学习习惯" extra={<Button type="link" onClick={() => actions.runAction('view-learning-habits', '学习习惯详情')}>查看详情 <ArrowRight size={14} /></Button>}>
        <div className={styles.habitGrid}>
          {habits.map(([name, level, desc]) => (
            <div key={name}><Clock3 size={24} /><span>{name}</span><strong>{level}</strong><em>{desc}</em></div>
          ))}
          {!habits.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有足够的学习习惯记录，再学几次就有了" />}
        </div>
      </PaperCard>
      <PaperCard title="近期测评表现" extra={<Button type="link" onClick={() => actions.runAction('list-profile-assessments', '近期测评表现')}>查看全部 <ArrowRight size={14} /></Button>}>
        {hasProfileTrend ? <div className={styles.profileTrend}><TrendChart data={profileTrend} /></div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="完成一次有效测评后，这里会展示真实趋势" />}
        <div className={styles.trendNote}>{hasProfileTrend ? '按现有测评与能力记录展示，不补齐缺失日期。' : '目前的记录还太少，暂不判断上升或下降趋势。'}</div>
      </PaperCard>
      <PaperCard title="综合学习分析">
        <div className={styles.growthList}>
          {reportMetrics.map((item) => <div key={item.label}><BarChart3 size={17} /><strong>{item.label}</strong><span>{item.value}%</span></div>)}
          {aiSummary.map((item, index) => <div key={`summary-${index}`}><CheckCircle2 size={17} /><strong>AI 分析</strong><span>{item}</span></div>)}
          {!reportMetrics.length && !aiSummary.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="学习、练习和产出会在这里汇总成对你的整体观察" />}
        </div>
      </PaperCard>
      <PaperCard title="成长建议" extra={<Tag color="orange">AI</Tag>}>
        <div className={styles.growthList}>
          {suggestions.map(([title, desc]) => (
            <div key={title}><Star size={17} fill="currentColor" /><strong>{title}</strong><span>{desc}</span></div>
          ))}
          {!suggestions.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="柿小吉还在收集依据，之后会给出具体建议" />}
        </div>
      </PaperCard>
      <PaperCard title="推荐提升路径" extra={<Button type="link" onClick={() => actions.runAction('list-improvement-paths', '推荐提升路径')}>查看全部 <ArrowRight size={14} /></Button>}>
        <div className={styles.pathList}>
          {paths.map(([title, meta], index) => (
            <div key={title}><b>{index + 1}</b><div><strong>{title}</strong><span>{meta}</span></div><Button size="small" loading={actions.isRunning('start-improvement-path', title)} onClick={() => actions.runAction('start-improvement-path', title, { rank: index + 1 })}>去学习</Button></div>
          ))}
          {!paths.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="完善画像并完成一次练习或测评后，这里会生成适合你的提升路径" />}
        </div>
      </PaperCard>
        </section>
      </details>
    </section>
  );
}

function ProfileRadar({ data }: { data?: Array<{ label: string; value: number }> }) {
  if (!data?.length) {
    return <RadarChart data={data} />;
  }

  const size = 320;
  const center = size / 2;
  const radius = 116;
  const averageRadius = radius * 0.76;
  const toPoint = (value: number, index: number, baseRadius = radius) => {
    const angle = (Math.PI * 2 * index) / data.length - Math.PI / 2;
    const pointRadius = baseRadius * (value / 100);
    return {
      x: center + Math.cos(angle) * pointRadius,
      y: center + Math.sin(angle) * pointRadius,
      angle
    };
  };
  const scorePoints = data.map((item, index) => toPoint(item.value, index));
  const averagePoints = data.map((_, index) => toPoint(100, index, averageRadius));

  return (
    <div className={styles.profileRadar}>
      <svg viewBox={`0 0 ${size} ${size}`} aria-label="能力雷达图">
        {[0.25, 0.5, 0.75, 1].map((scale) => (
          <polygon
            key={scale}
            points={data.map((_, index) => {
              const point = toPoint(100, index, radius * scale);
              return `${point.x},${point.y}`;
            }).join(' ')}
            className={styles.profileRadarGrid}
          />
        ))}
        {data.map((_, index) => {
          const edge = toPoint(100, index);
          return <line key={index} x1={center} y1={center} x2={edge.x} y2={edge.y} className={styles.profileRadarSpoke} />;
        })}
        <polygon points={averagePoints.map((point) => `${point.x},${point.y}`).join(' ')} className={styles.profileRadarAverage} />
        <polygon points={scorePoints.map((point) => `${point.x},${point.y}`).join(' ')} className={styles.profileRadarArea} />
        <polygon points={scorePoints.map((point) => `${point.x},${point.y}`).join(' ')} className={styles.profileRadarLine} />
        {scorePoints.map((point, index) => <circle key={data[index].label} cx={point.x} cy={point.y} r="5" className={styles.profileRadarDot} />)}
      </svg>
      {data.map((item, index) => {
        const point = toPoint(100, index, radius + 38);
        return (
          <span
            key={item.label}
            className={styles.profileRadarLabel}
            style={{ left: `${(point.x / size) * 100}%`, top: `${(point.y / size) * 100}%` }}
          >
            {item.label} {item.value > 0 ? item.value : '积累中'}
          </span>
        );
      })}
    </div>
  );
}

function ReportBody({ data }: { data: any }) {
  const actions = useModuleActions('report');
  const summaryMetrics: Array<[string, number, string]> = data?.reportMetrics?.length ? data.reportMetrics.map((item: any) => [item.label, item.value, item.compare ?? '来自学习记录']) : [
    ['课程进度', 68, '+8%'],
    ['作业完成率', 74, '+9%'],
    ['考试通过率', 81, '+7%']
  ];
  const recentRecords: Array<[string, string, LucideIcon]> = data?.reportRecords?.length ? data.reportRecords.map((item: any) => [item.title, item.time ?? item.type ?? '', FileText]) : [
    ['观看视频：3.2 队列的概念与实现', '今天 10:30', Play],
    ['完成作业：数据结构 第3章 课后练习', '今天 09:15', ClipboardCheck],
    ['完成小测验：栈与队列基础测试', '昨天 21:40', CheckCircle2],
    ['整理笔记：队列的实现方式总结', '昨天 20:05', FileText]
  ];
  const aiPoints: string[] = data?.aiSummary?.length ? data.aiSummary : [
    '理解能力表现优秀，建议继续保持，尝试更深度的拓展学习。',
    '应用能力有提升空间，多做综合练习，强化知识迁移。',
    '总结与复习可更系统化，建议定期回顾笔记与错题。'
  ];

  return (
    <section className={styles.reportGrid}>
      <div className={styles.reportLeft}>
        <PaperCard title="本月学习报告">
          <div className={styles.monthReport}>
            <strong>学习趋势（最近30天）</strong>
            <div className={styles.reportTrend}><TrendChart data={data?.trend} /></div>
            <div className={styles.reportMetricCards}>
              {summaryMetrics.map(([label, value, compare]) => (
                <div key={String(label)}>
                  <span>{label}</span>
                  <strong>{value}%</strong>
                  <CircleCheck size={42} />
                  <em>{compare}</em>
                </div>
              ))}
            </div>
            <div className={styles.monthSummary}>
              <b>本月学习总结</b>
              <p>{aiPoints[0] ?? '等待后端报告总结。'}</p>
            </div>
          </div>
        </PaperCard>
        <PaperCard title="最近学习记录" extra={<Button type="link" onClick={() => actions.runAction('list-learning-records', '查看全部记录')}>查看全部记录 <ArrowRight size={14} /></Button>}>
          <div className={styles.reportRecords}>
            {recentRecords.map(([title, time, Icon], index) => (
              <div key={`record-${index}-${title}`}>
                <Icon size={18} />
                <strong>{title}</strong>
                <time>{time}</time>
              </div>
            ))}
          </div>
        </PaperCard>
      </div>
      <div className={styles.reportRight}>
        <PaperCard title="能力概览"><div className={styles.reportRadar}><RadarChart data={data?.radar} /></div></PaperCard>
        <PaperCard title="AI 总结">
          <div className={styles.reportAiSummary}>
            {aiPoints.map((item, index) => <div key={`ai-${index}-${item.slice(0, 20)}`}><CheckCircle2 size={16} /><span>{item}</span></div>)}
          </div>
        </PaperCard>
        <PaperCard title="报告导出">
          <div className={styles.reportExport}>
            <p>选择需要的报告类型，导出并保存到本地。</p>
            <div>
              <button onClick={() => actions.runAction('export-report', 'PDF报告', { type: 'pdf' }, { reportType: 'pdf', successText: 'PDF报告导出任务已创建' })}><FileText size={26} />PDF报告</button>
              <button onClick={() => actions.runAction('export-report', '学习周报', { type: 'weekly' }, { reportType: 'weekly', successText: '学习周报导出任务已创建' })}><CalendarDays size={26} />学习周报</button>
              <button onClick={() => actions.runAction('export-report', '成长摘要', { type: 'growth' }, { reportType: 'growth', successText: '成长摘要导出任务已创建' })}><BarChart3 size={26} />成长摘要</button>
            </div>
            <Button type="primary" block icon={<Download size={16} />} loading={actions.isRunning('export-report', '完整报告')} onClick={() => actions.runAction('export-report', '完整报告', { type: 'full' }, { reportType: 'full', successText: '完整报告导出任务已创建' })}>导出完整报告</Button>
          </div>
        </PaperCard>
      </div>
    </section>
  );
}

function NotesBody({ module, data }: { module: string; data: any }) {
  const { message } = AntdApp.useApp();
  const [keyword, setKeyword] = useState('');
  const [category, setCategory] = useState('全部');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<GeneratedItem | null>(null);
  const [title, setTitle] = useState('');
  const [noteCategory, setNoteCategory] = useState('学习总结');
  const [content, setContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [aiBusy, setAiBusy] = useState('');
  const notes = ((data?.generatedItems ?? []) as GeneratedItem[]).filter((item) => item.type === 'note');
  const categories = ['全部', ...Array.from(new Set(notes.map((item) => item.chapter || '未分类')))];
  const filtered = notes.filter((item) => {
    const categoryMatch = category === '全部' || (item.chapter || '未分类') === category;
    const searchMatch = !keyword.trim() || `${item.title} ${item.content ?? ''} ${item.chapter ?? ''}`.toLowerCase().includes(keyword.trim().toLowerCase());
    return categoryMatch && searchMatch;
  });
  const active = notes.find((item) => item.id === selectedId) ?? filtered[0] ?? null;

  const openEditor = (item?: GeneratedItem | null) => {
    setEditing(item ?? null);
    setTitle(item?.title ?? '');
    setNoteCategory(item?.chapter || '学习总结');
    setContent(item?.content ?? '');
    setEditorOpen(true);
  };
  const save = async () => {
    if (!title.trim() || !content.trim()) {
      message.warning('请填写标题和笔记内容');
      return;
    }
    setSaving(true);
    try {
      const payload = { title: title.trim(), chapter: noteCategory.trim() || '未分类', content: content.trim(), raw_data: { ...(editing?.raw_data ?? {}), source: 'notes-page', category: noteCategory.trim() || '未分类' } };
      if (editing) await moduleApi.updateItem(editing.id, payload);
      else await moduleApi.saveItem({ type: 'note', ...payload });
      message.success(editing ? '笔记已更新' : '笔记已保存');
      setEditorOpen(false);
      window.dispatchEvent(new CustomEvent('kaki-artifact-generated'));
    } catch {
      message.error('笔记保存失败，请重试');
    } finally {
      setSaving(false);
    }
  };
  const remove = async () => {
    if (!active) return;
    await moduleApi.deleteItem(active.id);
    setSelectedId(null);
    message.success('笔记已删除');
    window.dispatchEvent(new CustomEvent('kaki-artifact-generated'));
  };
  const askAi = async (kind: string) => {
    if (!active) return;
    setAiBusy(kind);
    try {
      const instruction = kind === '润色整理'
        ? '请在保留原意的前提下扩写、整理并润色这篇学习笔记。'
        : '请评价这篇笔记中的理解是否准确，指出遗漏与理解偏差，并给出下一步学习建议。';
      await moduleApi.askTutorArtifact({ question: `${instruction}\n\n标题：${active.title}\n内容：${active.content ?? ''}`, context: { source: 'learning_note', note_id: active.id, category: active.chapter } });
      message.success('AI 结果已生成，可在学习产物中查看并决定是否采用');
      window.dispatchEvent(new CustomEvent('kaki-artifact-generated'));
    } catch {
      message.error('AI 处理失败，请稍后重试');
    } finally {
      setAiBusy('');
    }
  };

  return (
    <section className={styles.notesGrid}>
      <Modal title={editing ? '编辑学习笔记' : '新建学习笔记'} open={editorOpen} okText="保存" cancelText="取消" confirmLoading={saving} onOk={save} onCancel={() => setEditorOpen(false)}>
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="笔记标题" maxLength={200} />
          <Input value={noteCategory} onChange={(event) => setNoteCategory(event.target.value)} placeholder="分类，例如：学习总结" maxLength={120} />
          <Input.TextArea value={content} onChange={(event) => setContent(event.target.value)} placeholder="写下感悟、总结或刚学到的内容" autoSize={{ minRows: 8, maxRows: 16 }} />
        </Space>
      </Modal>
      <PaperCard title="我的学习笔记" extra={<Button type="primary" ghost icon={<Plus size={15} />} onClick={() => openEditor()}>新建笔记</Button>}>
        <div className={styles.qaComposer}>
          <Input prefix={<Search size={15} />} value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索标题、分类和正文" allowClear />
          <Select value={category} onChange={setCategory} options={categories.map((value) => ({ value, label: value }))} />
        </div>
        <div className={styles.noteList}>
          {filtered.map((item) => (
            <div data-active={active?.id === item.id} key={item.id} onClick={() => setSelectedId(item.id)}>
              <FileText size={17} />
              <div><strong>{item.title}</strong><p>{(item.content ?? '').slice(0, 110) || '暂无正文'}</p><small>{item.chapter || '未分类'}</small></div>
              <time>{formatRelativeTime(item.created_at)}</time>
              <Tag>我的笔记</Tag>
            </div>
          ))}
          {!filtered.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={notes.length ? '没有符合筛选条件的笔记' : '还没有笔记，先记录今天学到的一件事吧'} />}
        </div>
      </PaperCard>
      <PaperCard title={active?.title ?? '笔记详情'} extra={active && <Button type="link" onClick={() => openEditor(active)}>编辑</Button>}>
        {active ? (
          <div className={styles.noteDetail}>
            <div className={styles.noteMeta}><span>分类：{active.chapter || '未分类'}</span><span>保存时间：{formatRelativeTime(active.created_at)}</span></div>
            <div className={styles.noteContent}><p style={{ whiteSpace: 'pre-wrap' }}>{active.content}</p></div>
            <div className={styles.noteActions}>
              <Button loading={aiBusy === '润色整理'} onClick={() => void askAi('润色整理')}>AI 扩写、整理与润色</Button>
              <Button loading={aiBusy === '理解检查'} onClick={() => void askAi('理解检查')}>AI 理解检查</Button>
              <Button onClick={() => openEditor(active)}>修改笔记</Button>
              <Button danger onClick={() => void remove()}>删除</Button>
            </div>
          </div>
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一篇笔记查看和编辑" />}
      </PaperCard>
      <PaperCard title="AI 能怎样帮助这篇笔记">
        <div className={styles.relatedGrid}>
          <div><Sparkles size={16} /><strong>扩写与整理</strong><span>保留你的原意，改善结构和表达。</span></div>
          <div><CheckCircle2 size={16} /><strong>理解准确性</strong><span>指出遗漏、偏差和缺少的条件。</span></div>
          <div><ArrowRight size={16} /><strong>下一步建议</strong><span>结合笔记内容建议资料、练习或图谱节点。</span></div>
        </div>
      </PaperCard>
      <PaperCard title="关联内容">
        {active ? (
          <div className={styles.relatedGrid}>
            <div><Database size={16} /><strong>来源资料与对话</strong><span>{active.raw_data?.source_material_id || active.raw_data?.source_thread_id ? '已保存关联信息' : '尚未关联'}</span><Button type="link" href="/resources">关联资料</Button></div>
            <div><ClipboardCheck size={16} /><strong>错题与测评</strong><span>{active.raw_data?.exam_id ? '已关联测评' : '尚未关联'}</span><Button type="link" href="/exam">查看练习</Button></div>
            <div><Network size={16} /><strong>学习图谱节点</strong><span>{active.raw_data?.graph_node_id ? '已关联节点' : '尚未关联'}</span><Button type="link" href="/learning">查看图谱</Button></div>
          </div>
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择笔记后查看关联信息" />}
      </PaperCard>
    </section>
  );
}

function GenericBody({ data }: { data: any }) {
  return (
    <section className={styles.genericGrid}>
      <PaperCard title="趋势"><TrendChart data={data?.trend} /></PaperCard>
      <PaperCard title="列表"><ResourceTable module="generic" /></PaperCard>
    </section>
  );
}

// ============= 智能体调用弹窗辅助函数 =============

function renderAgentIcon(agent: ReturnType<typeof displayAgent>) {
  const Icon = agent.icon ?? Bot;
  return <Icon size={22} strokeWidth={1.55} />;
}

function renderField(field: AgentField) {
  if (field.type === 'input') {
    return (
      <Form.Item key={field.name} name={field.name} label={field.label} rules={field.required ? [{ required: true, message: `请输入${field.label}` }] : undefined}>
        <Input placeholder={field.placeholder} allowClear />
      </Form.Item>
    );
  }
  if (field.type === 'textarea') {
    return (
      <Form.Item key={field.name} name={field.name} label={field.label} rules={field.required ? [{ required: true, message: `请输入${field.label}` }] : undefined}>
        <Input.TextArea rows={field.rows ?? 3} placeholder={field.placeholder} allowClear />
      </Form.Item>
    );
  }
  if (field.type === 'number') {
    return (
      <Form.Item key={field.name} name={field.name} label={field.label} rules={field.required ? [{ required: true, message: `请输入${field.label}` }] : undefined}>
        <InputNumber min={field.min} max={field.max} addonAfter={field.suffix} style={{ width: '100%' }} />
      </Form.Item>
    );
  }
  if (field.type === 'select') {
    return (
      <Form.Item key={field.name} name={field.name} label={field.label} rules={field.required ? [{ required: true, message: `请选择${field.label}` }] : undefined}>
        <Select options={field.options} placeholder={`请选择${field.label}`} />
      </Form.Item>
    );
  }
  return (
    <Form.Item key={field.name} name={field.name} label={field.label} rules={field.required ? [{ required: true, message: `请选择${field.label}` }] : undefined}>
      <Select mode="multiple" options={field.options} placeholder={`请选择${field.label}`} maxTagCount="responsive" />
    </Form.Item>
  );
}

function modalTitle(phase: 'settings' | 'flow' | 'result', agent: ReturnType<typeof displayAgent>) {
  if (phase === 'settings') return `调用${agent.name}`;
  if (phase === 'flow') return `${agent.name} · 工作流程`;
  return `${agent.name} · 生成结果`;
}

function pickAgentResultSummary(response: unknown, outputType: string) {
  const fallback = `已生成 ${outputType}，请前往「智能体记录」查看完整产物。`;
  if (!response || typeof response !== 'object') return { title: outputType, preview: fallback, fullContent: fallback };
  const data = response as Record<string, unknown>;
  // getData 已解包 data 层，response 直接是 { agent, field, persisted, output_id, result }
  const result = (data.result as Record<string, unknown> | undefined) ?? data;

  // 尝试从 result 中提取各类型产物的文本
  const profileData = result.student_profile as Record<string, unknown> | undefined;
  const docData = result.document as Record<string, unknown> | undefined;
  const mindmapText = result.mindmap as string | undefined;
  const tutorText = result.tutor_response as string | undefined;
  const codeData = result.code_examples as Array<Record<string, unknown>> | undefined;
  const pathData = result.learning_path as Array<Record<string, unknown>> | undefined;
  const questionData = result.questions as Array<Record<string, unknown>> | undefined;
  const videoData = result.video_result as Record<string, unknown> | undefined;

  let summary = '';
  let fullContent = '';

  if (profileData) {
    const bits: string[] = [];
    if (profileData.major) bits.push(`专业：${profileData.major}`);
    if (profileData.grade_level) bits.push(`年级：${profileData.grade_level}`);
    if (profileData.target_role) bits.push(`目标：${profileData.target_role}`);
    if (profileData.preferred_language) bits.push(`偏好语言：${profileData.preferred_language}`);
    if (profileData.confidence_score != null) bits.push(`置信度：${Math.round(Number(profileData.confidence_score) * 100)}%`);
    const skill = profileData.skill_matrix as Record<string, unknown> | undefined;
    if (skill) {
      const skillLines = Object.entries(skill).slice(0, 6).map(([domain, kps]) => {
        if (typeof kps === 'object' && kps !== null) {
          const entries = Object.entries(kps as Record<string, unknown>);
          const nums = entries.filter(([, v]) => typeof v === 'number').map(([, v]) => Number(v));
          const avg = nums.length ? (nums.reduce((a, b) => a + b, 0) / nums.length).toFixed(1) : '-';
          return `  ${domain}（均分 ${avg}）：${entries.slice(0, 4).map(([k]) => k).join('、')}`;
        }
        return `  ${domain}：${kps}`;
      });
      bits.push(`能力矩阵：\n${skillLines.join('\n')}`);
    }
    const cognitive = profileData.cognitive_profile as Record<string, unknown> | undefined;
    if (cognitive) {
      bits.push(`认知特征：${Object.entries(cognitive).slice(0, 4).map(([k, v]) => `${k}: ${v}`).join('，')}`);
    }
    summary = bits.join('\n');
    fullContent = JSON.stringify(profileData, null, 2);
  } else if (docData) {
    summary = typeof docData.content === 'string' ? docData.content.slice(0, 600) : (docData.title as string ?? fallback);
    fullContent = typeof docData.content === 'string' ? docData.content : JSON.stringify(docData, null, 2);
  } else if (mindmapText) {
    summary = mindmapText.slice(0, 600);
    fullContent = mindmapText;
  } else if (tutorText) {
    summary = tutorText.slice(0, 600);
    fullContent = tutorText;
  } else if (codeData) {
    summary = codeData.map((ex, i) => `${i + 1}. ${ex.title ?? ''}\n${(ex.code as string ?? '').slice(0, 200)}`).join('\n\n');
    fullContent = JSON.stringify(codeData, null, 2);
  } else if (pathData) {
    summary = pathData.map((s, i) => `${i + 1}. ${s.topic ?? ''}: ${(s.description as string ?? '').slice(0, 120)}`).join('\n');
    fullContent = JSON.stringify(pathData, null, 2);
  } else if (questionData) {
    summary = questionData.map((q, i) => `${i + 1}. ${q.question ?? ''}`).join('\n');
    fullContent = JSON.stringify(questionData, null, 2);
  } else if (videoData) {
    summary = `视频：${videoData.title ?? videoData.topic ?? '已生成'}\n时长：${videoData.duration ?? '-'} 秒\n分辨率：${videoData.resolution ?? '-'}`;
    fullContent = JSON.stringify(videoData, null, 2);
  } else {
    // 兜底：从 dataPayload 的旧字段尝试
    const candidates = [
      data.summary, data.content, data.tutor_response, data.mindmap,
    ];
    const firstText = candidates.find((v) => typeof v === 'string' && v.trim().length > 0) as string | undefined;
    summary = firstText ?? fallback;
    fullContent = firstText ?? fallback;
  }

  const preview = summary.length > 800 ? `${summary.slice(0, 800)}…` : summary;
  const title = String(result.title ?? profileData?.major ? `学情分析：${profileData?.major ?? ''}` : outputType);
  return { title, preview, fullContent: fullContent.length > 3000 ? `${fullContent.slice(0, 3000)}…` : fullContent };
}
