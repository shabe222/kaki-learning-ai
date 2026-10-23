import {
  Activity,
  Bell,
  LayoutDashboard,
  Settings,
  UsersRound,
  BookCheck,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export type AdminModule = 'dashboard' | 'operations' | 'users' | 'quality' | 'content' | 'system';

export interface AdminRoute {
  path: string;
  module: AdminModule;
  title: string;
  subtitle: string;
  icon: LucideIcon;
  /** primary 项进入主导航；secondary 项归入「运行与内容」分组。 */
  group?: 'primary' | 'secondary';
}

export const adminRoutes: AdminRoute[] = [
  { path: '/admin/dashboard', module: 'dashboard', title: '总览', subtitle: '产品运行概况与系统状态', icon: LayoutDashboard, group: 'primary' },
  { path: '/admin/users', module: 'users', title: '用户与支持', subtitle: '查找账户，定位活动与支持记录', icon: UsersRound },
  { path: '/admin/quality', module: 'quality', title: '教学与内容质量', subtitle: '正式产物、版本与已有审查事实', icon: BookCheck },
  { path: '/admin/operations', module: 'operations', title: '任务与运行', subtitle: '执行、交付和追踪状态分别核对', icon: Activity },
  { path: '/admin/content', module: 'content', title: '公告与发布', subtitle: '编辑、双主题预览、发布与修订', icon: Bell },
  { path: '/admin/system', module: 'system', title: '系统与权限', subtitle: '服务依据、配置、权限与审计', icon: Settings },
];
