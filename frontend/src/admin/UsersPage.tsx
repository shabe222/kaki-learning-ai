import { AdvancedFilters, EditorialHead, UserIndex } from './Editorial';
import { Button, Drawer, Select } from 'antd';
import { Link } from 'react-router-dom';
import { useState } from 'react';
import { putData } from '../api/client';
import { useAdmin } from './AdminContext';
import { workbench, type List, type UserRow, type UserDetail, type Issue } from './workbenchApi';
import { useQuery,useResource,LoadState,Empty,Status,time,number,label,FilterSearch,DateFilters,DetailFacts,ActionModal,reasonField,type Action,Table } from './common';
import { IssueList } from './OverviewPage';

function UserDetails({id,act}: {id:string;act:(a:Action)=>void}) {
  const admin=useAdmin();const detail=useResource<UserDetail>(`/api/admin/workbench/users/${id}`);const issues=useResource<Issue[]>('/api/admin/workbench/issues',{user_id:id,state:'all'}); const r=detail.data;
  return <div className="admin-detail"><LoadState resource={detail}>{r && <>
    <h2>{r.username}</h2><Status value={r.is_active?'active':'disabled'}/><DetailFacts items={[
      ['用户 ID',<code>{r.user_id}</code>],['账户用途',label(r.purpose)],['邮箱',r.email || '未填写'],['注册时间',time(r.created_at)],['真实活动',time(r.last_active_at)],['画像存在',r.has_profile?'存在':'未建立'],['画像完成',r.profile_completion==null?'未测量':number(r.profile_completion,'%')],
    ]}/><div className="admin-related"><Link to={`/admin/operations?user_id=${r.user_id}&execution_only=yes`}>关联任务 {r.task_count}</Link><Link to={`/admin/quality?user_id=${r.user_id}`}>生成产物 {r.item_count}</Link></div>
    <DetailFacts items={[
      ['对话会话数',number(r.chat_count)],['累计消息数',number(r.message_count)],['近七天会话',number(r.week_sessions)],['近七天产物',number(r.week_items)],['正式题目提交',number(r.formal_attempt_count)],['答题记录数',number(r.answer_record_count)],['代码运行数',number(r.code_run_count)],
    ]}/><p className="admin-note">会话统计使用已存元数据；不按产物数量推断掌握程度。正式提交、答题记录和代码运行分别计数。</p>
    <div className="admin-toolbar"><Button disabled={!admin.canOperate} danger={r.is_active} onClick={()=>act({title:`${r.is_active?'停用':'恢复'}账户`,description:`用户 ${r.username} · ID ${r.user_id}。${r.is_active?'停用后现有令牌在下一次用户 API 请求时被拒绝，已经进入供应商的执行不自动中止。':'恢复后账户可继续登录并访问已有数据。'}`,dangerous:r.is_active,fields:[reasonField],submit:v=>putData(`/api/admin/users/${r.user_id}/status`,{is_active:!r.is_active,reason:v.reason})})}>{r.is_active?'停用账户':'恢复账户'}</Button>
    <Button disabled={!admin.canOperate} onClick={()=>act({title:'登记支持处理',description:`用户 ID ${r.user_id}。记录处理事实与证据定位，避免录入学生私人问题和正文。`,fields:[{name:'result',label:'处理记录',multiline:true}],submit:v=>workbench.post(`/users/${r.user_id}/support`,v)})}>登记支持</Button>
    <Button disabled={!admin.canConfigure} onClick={()=>act({title:'确认账户用途',description:'此标签影响业务统计范围和管理维护样本的授权资格。请依据该账户实际用途确认。',fields:[{name:'purpose',label:'账户用途',initial:r.purpose==='inferred_qa'?'qa':r.purpose,options:['learner','qa','demo'].map(value=>({value,label:label(value)}))},reasonField],submit:v=>workbench.put(`/users/${r.user_id}/purpose`,v)})}>确认用途</Button></div>
    <h3>最近真实活动</h3>{r.activity_timeline.length?<ol className="admin-timeline">{r.activity_timeline.map((e,index)=><li key={`${e.label}:${e.id}:${index}`}>{e.label} <code>#{e.id}</code><time>{time(e.time)}</time></li>)}</ol>:<Empty/>}
    <h3>关联事项</h3><LoadState resource={issues}>{issues.data && <IssueList items={issues.data} act={act}/>}</LoadState>
    <h3>管理处理记录</h3>{r.management_records.length?r.management_records.map(n=><div key={n.id} className="admin-issue-row"><div><strong>{label(n.action)}</strong><p>{n.summary}</p><small className="admin-note">管理员 {n.admin_id} · {time(n.created_at)}</small></div></div>):<Empty/>}
  </>}</LoadState></div>;
}
export function UsersPage() {
  const admin=useAdmin();const {params,set,list}=useQuery(); const users=useResource<List<UserRow>>('/api/admin/workbench/users',{page:1,page_size:20,...list});const [action,act]=useState<Action|null>(null);
  return <>
    <EditorialHead module="users">{users.data ? <UserIndex items={users.data.items} total={users.data.total}/> : <span className="admin-figure-label">正在读取账户索引…</span>}</EditorialHead>
    <section className="admin-panel"><div className="admin-toolbar"><FilterSearch value={params.get('keyword')||''} onSearch={keyword=>set({keyword,page:1},true)}/>
    <Select aria-label="账户状态" value={params.get('status')||''} options={[{value:'',label:'全部状态'},{value:'active',label:'正常'},{value:'disabled',label:'停用'}]} onChange={status=>set({status,page:1})}/>
    <Select aria-label="关联异常" value={params.get('errors')||''} options={[{value:'',label:'全部异常范围'},{value:'yes',label:'存在执行异常'}]} onChange={errors=>set({errors,page:1})}/>
    <AdvancedFilters active={["purpose", "sort", "start_date", "end_date"].filter(key=>params.has(key)).length}><Select aria-label="账户用途" value={params.get('purpose')||''} options={[{value:'',label:'全部用途'},...['learner','qa','demo','inferred_qa'].map(value=>({value,label:label(value)}))]} onChange={purpose=>set({purpose,page:1})}/>
    <Select aria-label="用户排序" value={params.get('sort')||'activity'} options={[{value:'activity',label:'最近真实活动'},{value:'created',label:'注册时间'}]} onChange={sort=>set({sort,page:1})}/>
    <DateFilters start={params.get('start_date')||''} end={params.get('end_date')||''} change={set}/></AdvancedFilters><Button onClick={()=>set(Object.fromEntries(Object.keys(list).map(k=>[k,undefined])))}>清除筛选</Button><Button onClick={users.reload} loading={users.busy}>刷新</Button></div>
    <LoadState resource={users}>{users.data && <><Table<UserRow> rowKey="user_id" size={admin.preferences?.compact?'small':'middle'} dataSource={users.data.items} loading={users.busy} scroll={{x:1050}} locale={{emptyText:<Empty filtered={Object.keys(list).length>0}/>}} pagination={{current:users.data.page,pageSize:users.data.page_size,total:users.data.total,showSizeChanger:true,pageSizeOptions:[20,50,100],showTotal:total=>`共 ${total} 位用户`,onChange:(page,page_size)=>set({page,page_size})}} columns={[
      {title:'用户',key:'user',width:235,render:(_,r)=><><Button type="link" style={{padding:0,height:'auto',maxWidth:225,whiteSpace:'normal',textAlign:'left'}} onClick={()=>set({detail:r.user_id})}>{r.username}</Button><small className="admin-cell-sub"><code>#{r.user_id}</code> · {label(r.purpose)}</small></>},
      {title:'账户状态',key:'status',width:95,render:(_,r)=><Status value={r.is_active?'active':'disabled'}/>},
      {title:'最近真实活动',dataIndex:'last_active_at',width:165,render:time},
      {title:'近七天活动',key:'activity',width:140,render:(_,r)=><>会话 {r.week_sessions}<small className="admin-cell-sub">产物 {r.week_items}</small></>},
      {title:'关联执行异常',dataIndex:'error_count',width:110,render:(v:number,r)=><Link to={`/admin/operations?user_id=${r.user_id}&error_category=task_failed`}>{v} 项</Link>},
      {title:'注册时间',dataIndex:'created_at',width:165,render:time},
      {title:'操作',key:'actions',width:80,fixed:'right',render:(_,r)=><Button size="small" onClick={()=>set({detail:r.user_id})}>详情</Button>},
    ]}/><p className="admin-note">来源：账户、会话活动、产物、调用、工作台任务及提交元数据 · 更新 {time(users.data.updated_at)} · 活动日期按上海时间查看</p></>}</LoadState></section>
    <Drawer title={`用户详情 · #${params.get('detail')||''}`} open={!!params.get('detail')} onClose={()=>set({detail:undefined})} getContainer={false} width="min(560px, 100vw)">{params.get('detail')&&<UserDetails id={params.get('detail')!} act={act}/>}</Drawer>
    <ActionModal action={action} close={()=>act(null)} done={admin.changed}/>
  </>;
}
