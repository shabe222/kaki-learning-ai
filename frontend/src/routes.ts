import {
  BookOpen,
  ClipboardCheck,
  Home,
  LibraryBig,
  MessageCircleQuestion,
  NotebookPen,
  PackageOpen,
  PlayCircle,
  UserRound
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export type UserRouteKind =
  | 'dashboard'
  | 'ai-learning'
  | 'learning'
  | 'resources'
  | 'notes'
  | 'artifacts'
  | 'video'
  | 'exam'
  | 'profile';

export interface AppRoute {
  path: string;
  module: string;
  title: string;
  subtitle: string;
  icon: LucideIcon;
  reference: string;
  kind: UserRouteKind;
  primary?: boolean;
  primaryTag?: string;
}

/** The complete user-facing information architecture. Compatibility routes live in App.tsx only. */
export const routes: AppRoute[] = [
  { path: '/', module: 'dashboard', title: '个人中心', subtitle: '身份、学习印象、计划与最近学习产物。', icon: Home, reference: '61833757c075cc469830d917015d1e2b.png', kind: 'dashboard' },
  { path: '/ai-learning', module: 'ai-learning', title: 'AI 学习', subtitle: '与 DARYA 对话、学习并生成真实产物。', icon: MessageCircleQuestion, reference: 'c63cdcb64b3a0e2ac7a4ed86da3cee90.png', kind: 'ai-learning', primary: true, primaryTag: '核心' },
  { path: '/learning', module: 'learning', title: '我的学习', subtitle: '学习图谱、当前路径、节点状态与下一步。', icon: BookOpen, reference: '838b19dc4af2d850c659b90baa4ac16d.png', kind: 'learning' },
  { path: '/resources', module: 'resources', title: '资料库', subtitle: '把学习资料放进来，DARYA 会围绕它们陪你学。', icon: LibraryBig, reference: '46378c76aa8ede96b784c853c1339cdf.png', kind: 'resources' },
  { path: '/notes', module: 'notes', title: '学习笔记', subtitle: '记录、整理并校准自己的理解。', icon: NotebookPen, reference: 'c63cdcb64b3a0e2ac7a4ed86da3cee90.png', kind: 'notes' },
  { path: '/artifacts', module: 'artifacts', title: '学习产物', subtitle: '统一查看与继续编辑 AI 生成结果。', icon: PackageOpen, reference: 'ae6061787ab347f5bc352c5bba6ad2ff.png', kind: 'artifacts' },
  { path: '/video', module: 'video', title: '视频学习', subtitle: '播放已有视频，查看 AI 教学短讲的真实生成状态。', icon: PlayCircle, reference: 'ae6061787ab347f5bc352c5bba6ad2ff.png', kind: 'video' },
  { path: '/exam', module: 'exam', title: '练习与测评', subtitle: '练习、正式测评、错题解析与掌握度变化。', icon: ClipboardCheck, reference: '4990c1a1bf201ddd3fe9688a111f2685.png', kind: 'exam' },
  { path: '/profile', module: 'profile', title: '个人中心', subtitle: 'DARYA 逐渐认识你的地方，每一分了解都来自你的真实学习。', icon: UserRound, reference: '57149d70b8313ff5c9a95c5b02ba2b03.png', kind: 'profile' }
];
