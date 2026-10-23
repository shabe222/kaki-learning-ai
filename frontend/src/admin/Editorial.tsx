import type { ReactNode } from 'react';
import { ArrowUpRight, FileCheck2, Layers3, Radio, ShieldCheck } from 'lucide-react';
import { useAdmin } from './AdminContext';
import { adminRoutes, type AdminModule } from './adminRoutes';
import type { QualityRow, TaskRow, UserRow, Overview, Issue } from './workbenchApi';
import { Status, label, number } from './common';

const editions: Record<AdminModule, [string, string]> = {
  dashboard: ['运行观察', '看清运行，把问题接住。'],
  users: ['用户索引', '从一个账户，找到支持的起点。'],
  quality: ['质量校样', '每份产物，都有可核对的依据。'],
  operations: ['执行记录', '执行、质量、交付，逐项看清。'],
  content: ['发布样张', '在发布之前，看见用户读到的样子。'],
  system: ['系统图鉴', '让每个状态，都有事实可查。'],
};

export function EditorialHead({module, children}: {module: AdminModule; children?: ReactNode}) {
  const {theme} = useAdmin();
  const route = adminRoutes.find(r => r.module === module)!;
  return <header className="admin-editorial-head" data-edition={module}>
    <div className="admin-editorial-title"><div className="admin-edition-line"><span>DARYA · 管理空间</span><span>{editions[module][0]}</span></div><h1>{route.title}</h1><p>{editions[module][1]}</p><small>{route.subtitle}</small></div>
    <div className="admin-editorial-art"><div className="admin-editorial-landscape" aria-hidden="true"/><div className="admin-editorial-figure">{children}</div><span className="admin-material-caption">{theme === 'kaki' ? '柿子 · 暖纸刊物' : '海洋 · 海岸观察'}</span></div>
  </header>;
}

export function UserIndex({items, total}: {items: UserRow[]; total: number}) {
  const active = items.filter(r => r.is_active).length;
  return <div className="admin-index-figure"><div className="admin-figure-label">当前筛选 · 账户索引</div><div className="admin-index-count"><strong>{number(total)}</strong><span>位匹配账户</span></div><div className="admin-index-strip" role="img" aria-label={`本页 ${items.length} 位：正常 ${active} 位，停用 ${items.length-active} 位`}>{items.map(r=><span key={r.user_id} data-active={r.is_active} title={`#${r.user_id} · ${r.is_active?'正常':'停用'}`}/>)}</div><small>本页 {items.length} 位 · 正常 {active} / 停用 {items.length-active}</small></div>;
}

export function VersionProof({row, open}: {row?: QualityRow; open: () => void}) {
  return <div className="admin-proof-figure"><div className="admin-figure-label"><FileCheck2 size={14}/> 当前列表首条 · 正式产物依据</div>{row ? <><button className="admin-proof-object" onClick={open}><span>产物 <b>#{row.artifact_id}</b></span><ArrowUpRight size={17}/></button><div className="admin-proof-register"><div><Layers3 size={20}/><span>正式版本<strong>{row.version_id ? `v${row.version_number}` : '缺失'}</strong><small>{row.version_id ? `ID ${row.version_id}` : '旧记录未建立'}</small></span></div><div><span>已有质量判定</span><Status value={row.quality}/>{row.review_source!==label(row.quality)&&<small>{row.review_source}</small>}</div></div></> : <p>没有匹配产物，调整筛选后查看。</p>}</div>;
}

export function TaskProof({row, open}: {row?: TaskRow; open: () => void}) {
  return <div className="admin-task-figure"><div className="admin-figure-label"><Radio size={14}/> 当前列表首条 · 三项独立结果</div>{row ? <><button className="admin-proof-object" onClick={open}><span>{label(row.type)} <code>{row.key} · {label(row.record_kind)}</code></span><ArrowUpRight size={17}/></button><div className="admin-task-results"><div><small>技术执行</small><Status value={row.status}/></div><div><small>质量判定</small><Status value={row.quality}/></div><div><small>产物关联</small><strong>{row.artifact_id ? `#${row.artifact_id}` : '未关联'}</strong></div></div></> : <p>没有匹配执行记录，调整筛选后查看。</p>}</div>;
}

export function OverviewFigures({data}: {data: Overview}) {
  return <div className="admin-cover-stat"><span>最近七天 · 真实记录</span><div><strong>{number(data.artifacts_7d)}</strong><span>份产物</span><i aria-hidden="true"/><strong>{number(data.tasks_7d)}</strong><span>条任务</span></div><small>生成产物与正式学习提交分别记录</small></div>;
}

export function IssueBrief({items}: {items: Issue[]}) {
  const groups = Object.entries(items.reduce<Record<string,number>>((counts,row)=>({...counts,[row.category]:(counts[row.category]||0)+1}),{}));
  return <div className="admin-issue-brief"><span className="admin-figure-label">当前事项范围 · 最近最多100项</span><div><strong>{items.length}</strong><span>项处理记录</span></div><div className="admin-issue-distribution" aria-hidden="true">{groups.map(([key,count])=><i key={key} style={{flex:count}}/>)}</div><small>{groups.map(([key,count])=>`${label(key)} ${count}`).join(' / ') || '当前范围没有事项'}</small></div>;
}

export function AdvancedFilters({active, children}: {active: number; children: ReactNode}) {
  return <details className="admin-advanced-filters" open={active>0 || undefined}><summary>更多筛选{active>0 ? ` · 已选 ${active} 项` : ''}</summary><div className="admin-toolbar">{children}</div></details>;
}

export function ServiceKey() {
  return <div className="admin-service-key"><ShieldCheck size={28}/><strong>按依据，逐项核对</strong><div><span>基础连接</span><span>配置来源</span><span>实际试验</span></div><small>检查范围与时间随每条事实展示</small></div>;
}

export function Trend({data}: {data: Overview}) {
  const max = Math.max(1,...data.trend.map(r=>Math.max(r.artifacts,r.tasks)));
  return <><div className="admin-chart-legend"><span><i/>产物</span><span><i/>任务</span><small>上海自然日</small></div><div className="admin-trend" role="img" aria-label={data.trend.map(r=>`${r.date}：产物${r.artifacts}，任务${r.tasks}`).join('；')}>{data.trend.map(row=><div className="admin-trend-day" key={row.date}><small>{row.artifacts} / {row.tasks}</small><div className="admin-trend-bars"><span style={{height:`${row.artifacts/max*100}%`}}/><span style={{height:`${row.tasks/max*100}%`}}/></div><small>{row.date.slice(5)}</small></div>)}</div><div className="admin-chart-foot"><div><span>执行成功率</span><strong>{number(data.success_rate,'%')}</strong><small>已结束任务样本 {data.terminal_sample}</small></div><div><span>实测平均耗时</span><strong>{number(data.avg_duration_ms,'ms')}</strong><small>耗时样本 {data.duration_sample} · 费用未记录</small></div></div></>;
}
