import { EditorialHead, IssueBrief, Trend } from './Editorial';
import { Button, Select, Switch } from 'antd';
import { RefreshCw, ArrowUpRight } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { useAdmin } from './AdminContext';
import { workbench, type Overview, type Issue, type Health } from './workbenchApi';
import { adminApi, type AuditLogListData } from '../api/admin';
import { useQuery, useResource, LoadState, Empty, Status, time, number, label, ActionModal, type Action } from './common';
export function issueAction(issue: Issue): Action {
  return {title:`处理事项 #${issue.id}`,description:`${label(issue.category)} · 对象 ${issue.object_id}。登记处理事实；关闭时填写可定位的运行记录或验证结果。`,fields:[
    {name:'state',label:'处理状态',initial:issue.state === 'open' ? 'acknowledged' : issue.state,options:['acknowledged','handling','resolved','closed'].map(value=>({value,label:label(value)}))},
    {name:'result',label:'处理结果 / 原因',initial:issue.result || '',multiline:true},{name:'evidence',label:'证据定位',initial:issue.evidence || '',required:false},
  ],submit: values=>workbench.put(`/issues/${issue.id}`,values)};
}
export function IssueList({items, act}: {items: Issue[]; act:(a:Action)=>void}) {
  const {canOperate}=useAdmin();
  const [expanded,expand]=useState(false);
  if(!items.length) return <div className="admin-empty"><strong>当前没有待处理事项</strong>正常停用账户不会自动创建事项。</div>;
  return <>{items.slice(0,expanded?100:3).map(i=><div className="admin-issue-row" key={i.id}><Status value={i.state}/><div className="admin-issue-body"><strong>{label(i.category)} · 用户 {i.user_id ?? '未关联'}</strong><small className="admin-cell-sub">对象 {i.object_id} · 首次 {time(i.first_seen_at)}</small><small className="admin-cell-sub">最近 {time(i.last_seen_at)} · {i.result || '尚未登记处理结果'}</small><div className="admin-related"><Link to={i.object_type === 'artifact' ? `/admin/quality?detail=${i.object_id}&issue=${i.id}` : `/admin/operations?detail=${encodeURIComponent(i.object_id)}&issue=${i.id}`}>定位对象 <ArrowUpRight size={12}/></Link><Link to={`/admin/users?detail=${i.user_id}`}>用户支持</Link></div></div><Button size="small" disabled={!canOperate} onClick={()=>act(issueAction(i))}>登记处理</Button></div>)}{items.length>3&&<Button type="link" onClick={()=>expand(!expanded)} aria-expanded={expanded}>{expanded?'收起事项':`展开全部 ${items.length} 项`}</Button>}</>;
}
export function OverviewPage() {
  const admin=useAdmin();const {params,set}=useQuery();const scope=params.get('scope') || 'business';
  const overview=useResource<Overview>('/api/admin/workbench/overview',{scope});
  const issues=useResource<Issue[]>('/api/admin/workbench/issues',{state:params.get('issues') || 'active'});
  const health=useResource<Health>('/api/admin/workbench/health');
  const audit=useResource<AuditLogListData>('/api/admin/audit-logs',{page_size:5});
  const [action,setAction]=useState<Action|null>(null);const [newRecords,setNewRecords]=useState(false);
  useEffect(()=> {if(!admin.preferences?.auto_refresh) return;const timer=setInterval(()=> {if(document.visibilityState !== 'visible') return;overview.reload();health.reload();setNewRecords(true);},60000);return()=>clearInterval(timer);},[admin.preferences?.auto_refresh]);
  const metrics=overview.data;
  return <>
    <EditorialHead module="dashboard">{issues.data ? <IssueBrief items={issues.data}/> : <span className="admin-figure-label">正在读取事项索引…</span>}</EditorialHead>
    <div className="admin-toolbar"><Select aria-label="统计账户范围" value={scope} options={[{value:'business',label:'业务账户 · 排除测试与演示'},{value:'all',label:'全部账户 · 技术运行范围'}]} style={{width:270}} onChange={v=>set({scope:v})}/><Switch checked={admin.preferences?.auto_refresh ?? true} disabled={!admin.preferences} onChange={v=>void admin.setPreferences({auto_refresh:v}).catch(()=>{})} aria-label="每60秒自动更新"/><span className="admin-note">每60秒更新概况 · 隐藏时暂停</span><Button icon={<RefreshCw size={14}/>} onClick={()=>{admin.changed();setNewRecords(false);}}>刷新</Button></div>
    <LoadState resource={overview}>{metrics && <><div className="admin-metrics">{[['业务账户',metrics.users,'当前所选范围'],['近七天活跃',metrics.active_users_7d,'最近真实活动'],['近七天产物',metrics.artifacts_7d,'生成数，独立于正式提交'],['近七天任务',metrics.tasks_7d,'调用、工作台任务与持久作业']].map(([name,value,note])=><div className="admin-metric" key={String(name)}><span>{name}</span><strong>{number(Number(value))}</strong><small>{note}</small></div>)}</div><p className="admin-note">{metrics.window} · {metrics.source}<br/>更新：{time(metrics.updated_at)} · {metrics.purpose_note}</p></>}</LoadState>
    <div className="admin-overview-spread"><section className="admin-panel admin-issue-feature"><div className="admin-panel-head"><div><small className="admin-section-kicker">需要接住的问题</small><h2>待处理事项 <span className="admin-section-count">{issues.data?.length ?? '—'}</span></h2></div><Select aria-label="事项状态范围" value={params.get('issues')||'active'} onChange={v=>set({issues:v})} options={[{value:'active',label:'待处理'},{value:'all',label:'全部处理记录'},{value:'closed',label:'已关闭'}]}/></div>{newRecords && <div className="admin-toolbar"><span className="admin-note">概况已更新，列表保持当前位置。</span><Button size="small" onClick={()=>{issues.reload();audit.reload();setNewRecords(false);}}>应用最新事项与操作</Button></div>}<LoadState resource={issues}>{issues.data && <><p className="admin-note">{Object.entries(issues.data.reduce<Record<string,number>>((counts,i)=>({...counts,[i.category]:(counts[i.category]||0)+1}),{})).map(([key,count])=>`${label(key)} ${count} 项`).join(' · ') || '无异常分类'} · 当前状态范围内最近100项</p><IssueList items={issues.data} act={setAction}/></>}</LoadState></section>
    <section className="admin-panel admin-chart-panel"><small className="admin-section-kicker">真实活动的节律</small><h2>近七天的产物与任务</h2><LoadState resource={overview}>{metrics && <Trend data={metrics}/>}</LoadState><p className="admin-note">{metrics?.window}</p></section></div>
    <section className="admin-panel admin-capabilities"><div className="admin-panel-head"><h2>能力依据</h2><Link to="/admin/system">服务与检查依据 →</Link></div><LoadState resource={health}><div className="admin-capability-grid">{health.data?.capabilities.map(c=><div key={c.name}><strong>{c.name}</strong><Status value={c.state}/><small>最近成功：{time(c.last_success_at)}</small></div>)}</div></LoadState><p className="admin-note">基础连接与主动能力试验分别显示。历史成功不能证明当前可用。</p></section>
    <section className="admin-panel"><div className="admin-panel-head"><h2>最近管理操作</h2><Link to="/admin/system?tab=audit">查看审计 →</Link></div><LoadState resource={audit}>{audit.data?.items.length ? audit.data.items.map(r=><div className="admin-issue-row" key={r.id}><div className="admin-issue-body"><strong>{label(r.action)}</strong><small className="admin-cell-sub">管理员 {r.admin_id} · {r.target_type} {r.target_id} · {time(r.created_at)}</small><small className="admin-cell-sub">{r.summary}</small></div><Status value={r.success?'success':'failed'}/></div>):audit.data && <Empty/>}</LoadState></section>
    <ActionModal action={action} close={()=>setAction(null)} done={admin.changed}/>
  </>;
}
