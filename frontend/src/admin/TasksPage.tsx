import { AdvancedFilters, EditorialHead, TaskProof } from './Editorial';
import { Button, Drawer, Select } from 'antd';
import { Link } from 'react-router-dom';
import { useState } from 'react';
import { useAdmin } from './AdminContext';
import { workbench,type List,type TaskRow,type TaskDetail,type Overview,type Issue } from './workbenchApi';
import { useQuery,useResource,LoadState,Empty,Status,time,number,label,FilterSearch,DateFilters,DetailFacts,ActionModal,type Action,Table,reasonField } from './common';
import { issueAction } from './OverviewPage';
function TaskDetails({id,issueId,act}: {id:string;issueId:string|null;act:(a:Action)=>void}) {
  const admin=useAdmin();const resource=useResource<TaskDetail>('/api/admin/workbench/tasks/detail',{key:id});const issues=useResource<Issue[]>('/api/admin/workbench/issues',{state:'all'});const r=resource.data;
  return <div className="admin-detail"><LoadState resource={resource}>{r&&<><h2>{label(r.type)}</h2><code>{r.key}</code><DetailFacts items={[
    ['技术执行',<Status value={r.status}/>],['质量 / 交付',<Status value={r.quality}/>],['记录来源',label(r.record_kind)],['追踪完整性',label(r.tracking)],['开始时间',time(r.created_at)],['实测耗时',number(r.duration_ms,'ms')],['关联用户',<Link to={`/admin/users?detail=${r.user_id}`}>用户 {r.user_id}</Link>],['关联产物',r.artifact_id?<Link to={`/admin/quality?detail=${r.artifact_id}`}>产物 #{r.artifact_id}</Link>:'未关联'],['错误分类',label(r.error_category)],['tokens / 费用','未记录'],
  ]}/><h3>持久化执行阶段</h3>{r.stages.length?<><Status value={r.stages[0]}/><p className="admin-note">这是当前持久化阶段。历史阶段与各阶段时间未记录。</p></>:<div className="admin-empty"><strong>执行阶段未记录</strong>不会从产物存在推断阶段已完成。</div>}
  <h3>重试与停止</h3><p className="admin-note">{r.retry_reason}<br/>{r.cancel_reason}</p>{r.retry_eligible&&<Button disabled={!admin.canOperate} onClick={()=>{const key=crypto.randomUUID();act({title:'创建关联的新尝试',description:`原产物 ${r.artifact_id} 将保留。此操作可能增加检索和模型费用，并向该学习者交付一份新产物；不会写成正式学习提交。重复确认使用同一重试键。`,fields:[reasonField,{name:'confirmation',label:'用量与交付确认',options:[{value:'yes',label:'确认新增用量和新产物交付'}]}],submit:v=>workbench.post('/tasks/retry',{artifact_id:r.artifact_id,request_key:key,reason:v.reason,confirmed_cost:v.confirmation==='yes'})});}}>重试研究任务</Button>}
  {r.retry_links.map((link,index)=><p key={index} className="admin-note"><Link to={`/admin/operations?detail=artifact:${link.source_artifact_id}`}>原尝试 #{link.source_artifact_id}</Link> → <Link to={`/admin/operations?detail=artifact:${link.target_artifact_id}`}>新尝试 #{link.target_artifact_id}</Link> · {time(link.created_at)}</p>)}
  {issueId&&issues.data?.find(i=>String(i.id)===issueId)&&<Button disabled={!admin.canOperate} onClick={()=>act(issueAction(issues.data!.find(i=>String(i.id)===issueId)!))}>登记关联事项结果</Button>}
  <div className="admin-toolbar" style={{marginTop:20}}><Button onClick={resource.reload} loading={resource.busy}>更新当前任务状态</Button><Link to={`/admin/users?detail=${r.user_id}`}>进入用户支持</Link></div>
  </>}</LoadState></div>;
}
export function TasksPage() {
  const admin=useAdmin();const {params,set,list}=useQuery();const rows=useResource<List<TaskRow>>('/api/admin/workbench/tasks',{page:1,page_size:20,...list}); const stats=useResource<Overview>('/api/admin/workbench/overview',{scope:'all'});const [action,act]=useState<Action|null>(null);
  return <><EditorialHead module="operations">{rows.data ? <TaskProof row={rows.data.items[0]} open={()=>set({detail:rows.data?.items[0]?.key})}/> : <span className="admin-figure-label">{rows.error?'任务索引读取失败，可在下方重试':'正在读取执行记录…'}</span>}</EditorialHead><section className="admin-running-basis"><LoadState resource={stats}>{stats.data&&<><span>全部账户 · 近七天</span><span>任务 <b>{stats.data.tasks_7d}</b></span><span>已结束样本 <b>{stats.data.terminal_sample}</b></span><span>执行成功率 <b>{number(stats.data.success_rate,'%')}</b></span><details><summary>统计口径</summary><p className="admin-note">平均耗时 {number(stats.data.avg_duration_ms,'ms')} · 实测耗时样本 {stats.data.duration_sample} · 独立产物不进入成功率分母 · tokens / 费用未记录。</p></details></>}</LoadState></section>
  <section className="admin-panel"><div className="admin-toolbar"><FilterSearch value={params.get('user_id')||''} onSearch={user_id=>set({user_id,page:1},true)} placeholder="用户 ID"/>
  <Select aria-label="执行状态" value={params.get('status')||''} options={[{value:'',label:'全部执行状态'},...['running','queued','completed','ready','success','failed','partial','artifact_exists'].map(value=>({value,label:label(value)}))]} onChange={status=>set({status,page:1})}/>
  <Select aria-label="记录来源" value={params.get('record_kind')||''} options={[{value:'',label:'全部记录来源'},...['agent_task','workspace_task','durable_job','artifact_only'].map(value=>({value,label:label(value)}))]} onChange={record_kind=>set({record_kind,page:1})}/>
  <AdvancedFilters active={["quality", "delivery", "error_category", "type", "start_date", "end_date"].filter(key=>params.has(key)).length}><Select aria-label="任务质量判定" value={params.get('quality')||''} options={[{value:'',label:'全部质量判定'},...['passed','rejected','revision_required','partial','generating','unreviewed'].map(value=>({value,label:label(value)}))]} onChange={quality=>set({quality,page:1})}/>
  <Select aria-label="产物交付" value={params.get('delivery')||''} options={[{value:'',label:'全部交付范围'},{value:'artifact_linked',label:'已关联产物'},{value:'not_delivered',label:'未关联产物'}]} onChange={delivery=>set({delivery,page:1})}/>
  <Select aria-label="失败类型" value={params.get('error_category')||''} options={[{value:'',label:'全部失败范围'},{value:'task_failed',label:'任务执行失败'}]} onChange={error_category=>set({error_category,page:1})}/>
  <FilterSearch value={params.get('type')||''} onSearch={type=>set({type,page:1},true)} placeholder="能力类型"/><DateFilters start={params.get('start_date')||''} end={params.get('end_date')||''} change={set}/></AdvancedFilters><Button onClick={()=>set(Object.fromEntries(Object.keys(list).map(k=>[k,undefined])))}>清除筛选</Button><Button onClick={rows.reload} loading={rows.busy}>刷新</Button></div>
  <LoadState resource={rows}>{rows.data&&<><Table<TaskRow> rowKey="key" dataSource={rows.data.items} loading={rows.busy} scroll={{x:1150}} locale={{emptyText:<Empty filtered={Object.keys(list).length>0}/>}} pagination={{current:rows.data.page,pageSize:rows.data.page_size,total:rows.data.total,showSizeChanger:true,pageSizeOptions:[20,50,100],showTotal:n=>`共 ${n} 条记录`,onChange:(page,page_size)=>set({page,page_size})}} columns={[
    {title:'任务 / 来源',key:'identity',width:220,render:(_,r)=><><Button type="link" style={{padding:0,height:'auto',whiteSpace:'normal',textAlign:'left'}} onClick={()=>set({detail:r.key})}>{label(r.type)}</Button><small className="admin-cell-sub"><code>{r.key}</code> · {label(r.record_kind)}</small></>},
    {title:'技术执行',dataIndex:'status',width:130,render:v=><Status value={v}/>},
    {title:'质量 / 交付 / 追踪',key:'tracking',width:190,render:(_,r)=><><Status value={r.quality}/><small className="admin-cell-sub">{r.artifact_id?<Link to={`/admin/quality?detail=${r.artifact_id}`}>产物 #{r.artifact_id}</Link>:'未关联产物'}</small><small className="admin-cell-sub">{label(r.tracking)}</small></>},
    {title:'当前阶段',dataIndex:'stage',width:130,render:v=>label(v)},
    {title:'开始时间',dataIndex:'created_at',width:165,render:time},
    {title:'耗时',dataIndex:'duration_ms',width:100,render:v=>number(v,'ms')},
    {title:'用户',key:'user',width:90,render:(_,r)=><Link to={`/admin/users?detail=${r.user_id}`}>#{r.user_id}</Link>},
    {title:'操作',key:'action',fixed:'right',width:80,render:(_,r)=><Button size="small" onClick={()=>set({detail:r.key})}>详情</Button>},
  ]}/><p className="admin-note">来源：AgentInvocation、WorkspaceAgentTask、研究 / 视频作业状态、独立产物记录 · 更新 {time(rows.data.updated_at)}</p></>}</LoadState></section>
  <Drawer title="任务详情" open={!!params.get('detail')} onClose={()=>set({detail:undefined})} getContainer={false} width="min(560px, 100vw)">{params.get('detail')&&<TaskDetails key={params.get('detail')} id={params.get('detail')!} issueId={params.get('issue')} act={act}/>}</Drawer><ActionModal action={action} close={()=>act(null)} done={admin.changed}/></>;
}
