export interface ApiResponse<T> {
  code: number;
  msg: string;
  data: T;
}

export interface PageQuery {
  page?: number;
  pageSize?: number;
  keyword?: string;
  [key: string]: string | number | boolean | undefined;
}

export interface PageResult<T> {
  list: T[];
  total: number;
}

export interface StatCard {
  key: string;
  label: string;
  value?: string | number;
  unit?: string;
  compare?: string;
  /** 图标格色调，用于对齐视觉目标图中的彩色统计卡 */
  tone?: 'orange' | 'green' | 'amber' | 'blue' | 'purple' | 'red';
}

export interface TimelineItem {
  id: string;
  title: string;
  description?: string;
  time?: string;
  status?: 'done' | 'active' | 'locked' | 'pending';
}

export interface ResourceItem {
  id: string;
  name: string;
  chapter?: string;
  type?: string;
  size?: string;
  updatedAt?: string;
  status?: string;
  source?: 'official' | 'user' | 'agent';
  chapterId?: string;
  materialId?: string | number | null;
  hasMaterial?: boolean;
  progress?: number;
}
