import { AdvancedFilters, EditorialHead, VersionProof } from './Editorial';
import { Button, Drawer, Select, Alert } from 'antd';
import { Link } from 'react-router-dom';
import { useState } from 'react';
import { useAdmin } from './AdminContext';
import { workbench,type List,type QualityRow,type QualityDetail,type Issue } from './workbenchApi';
import { useQuery,useResource,LoadState,Empty,Status,time,number,label,FilterSearch,DateFilters,DetailFacts,ActionModal,type Action,Table } from './common';
import { issueAction } from './OverviewPage';

function AuthorizedText({id,version}: {id:string;version:number}) {
  const resource=useResource<{content_snapshot:string;content_digest:string;version_number:number}>(`/api/admin/workbench/quality/${id}/sample`,{version_id:version});
  return <section className="admin-panel"><h3>版本 ID {version}</h3><LoadState resource={resource}>{resource.data&&<><code>{resource.data.content_digest}</code><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',fontSize:14,lineHeight:1.9}}>{resource.data.content_snapshot}</pre></>}</LoadState></section>;
}
function AuthorizedSamplePage({id,version}: {id:string;version:string|null}) {
  const r=useResource<QualityDetail>(`/api/admin/workbench/quality/${id}`);const [left,setLeft]=useState<number|null>(version?Number(version):null);const [right,setRight]=useState<number|null>(null);
  const options=r.data?.versions.map(v=>({value:v.id,label:`v${v.version_number} · ID ${v.id}`})) || [];
  const selected=left || r.data?.version_id;
  return <><div className="admin-toolbar"><Link to={`/admin/quality?detail=${id}`}>← 返回质量详情</Link><h2>授权质量样本 · 产物 #{id}</h2></div><LoadState resource={r}>{r.data&&(r.data.sample_authorized?<><p className="admin-note">当前身份对该管理维护样本有独立正文授权。原始版本按快照展示；读取留有审计记录。长时间正文复核与双版本对比建议在桌面完成。</p><div className="admin-toolbar"><Select aria-label="阅读样本版本" value={selected} options={options} onChange={setLeft}/><Select aria-label="比较样本版本" placeholder="选择另一版本进行对比" value={right} allowClear options={options} onChange={v=>setRight(v||null)}/></div><div className={right?'admin-grid':''}>{selected&&<AuthorizedText key={`left:${id}:${selected}`} id={id} version={selected}/>} {right&&<AuthorizedText key={`right:${id}:${right}`} id={id} version={right}/>}</div></>:<Alert type="warning" showIcon message="当前身份没有这个样本的正文授权" description="样本必须属于明确标记的测试或演示账户，并另外获得该产物授权。可返回查看脱敏版本与质量元数据。"/>)}</LoadState></>;
}

function QualityDetails({id,issueId,act}: {id:string;issueId:string|null;act:(a:Action)=>void}) {
  const admin=useAdmin();const resource=useResource<QualityDetail>(`/api/admin/workbench/quality/${id}`);const issue=useResource<Issue[]>('/api/admin/workbench/issues',{state:'all'});
  const r=resource.data;
  return <div className="admin-detail"><LoadState resource={resource}>{r&&<>
    <h2>产物 <code>#{r.artifact_id}</code> · {label(r.type)}</h2><Status value={r.quality}/>
    <DetailFacts items={[
      ['关联用户',<Link to={`/admin/users?detail=${r.user_id}`}>用户 {r.user_id}</Link>],['发生时间',time(r.created_at)],['正式版本',r.version_id?`v${r.version_number} · ID ${r.version_id}`:'旧记录未建立版本'],['生成状态',label(r.generation_status)],['质量来源',r.review_source],['审查契约',r.contract?<code>{r.contract}</code>:'未记录'],['审查时间',time(r.reviewed_at)],['测量状态',r.reviews.some(v=>v.clarity!=null)?'存在实际审查维度':r.measured?'存在内容质量测量回执':'未测量'],
    ]}/><div className="admin-related"><Link to={`/admin/operations?artifact_id=${r.artifact_id}`}>关联执行记录</Link><Link to={`/admin/users?detail=${r.user_id}`}>查看支持记录</Link></div>
    <p className="admin-note">{r.privacy_note}</p><h3>版本变化</h3>{r.versions.length?<ol className="admin-timeline">{r.versions.map((v,index)=><li key={v.id}>v{v.version_number} · {label(v.status)}<time>{time(v.created_at)} · 父版本 {v.parent_version_id ?? '无'}</time><small className="admin-cell-sub"><code>{v.content_digest.slice(0,20)}</code> · {index<r.versions.length-1?(v.content_digest===r.versions[index+1].content_digest?'与上一版内容摘要一致':'与上一版内容摘要不同'):'初始版本'}</small>{r.sample_authorized&&<Link to={`/admin/quality?sample=${r.artifact_id}&version=${v.id}`}>进入授权版本阅读页</Link>}</li>)}</ol>:<Empty/>}
    <h3>已有评审记录</h3>{r.reviews.length?r.reviews.map(v=><div key={v.review_id} className="admin-panel"><Status value={v.verdict}/><p className="admin-note">版本 ID {v.artifact_version_id} · 第 {v.round} 轮 · {label(v.authority_kind)} · {time(v.created_at)}</p><code>{v.review_contract_version}</code></div>):<div className="admin-empty"><strong>没有正式版本审查记录</strong>已有内容质量回执与正式版本审查分别保留。</div>}
    <h3>实测维度与结构化缺陷</h3><p className="admin-note">内容审阅轮次 {number(r.content_review_rounds)} · 实际修订轮次 {number(r.revision_rounds)}</p>{r.dimensions.length?r.dimensions.map((d,index)=><div key={index}><p className="admin-note">{d.source} · 版本 {d.version_id ?? '未建立'}</p><DetailFacts items={Object.entries(d.scores).map(([key,value])=>[({goal_fit:'目标匹配',clarity:'清晰性',internal_consistency:'内部一致性',unsupported_claim_risk:'无依据声明风险',artifact_integrity:'产物完整性',topic_fidelity:'主题匹配',expert_depth:'专业深度',structure_fit:'结构匹配',specificity:'具体程度'} as Record<string,string>)[key] || key,number(value)])}/></div>):<p className="admin-note">未记录可展示的实测维度。</p>}{r.defects.length?r.defects.map((d,index)=><p className="admin-note" key={index}>{d.code || d.category || '已记录严重程度'} · {label(d.severity)} · <code>{d.review_id}</code></p>):<p className="admin-note">结构化缺陷分类未记录；私人正文和缺陷描述需要样本授权后复核。</p>}
    <div className="admin-toolbar"><Button disabled={!admin.canOperate} onClick={()=>act({title:'登记质量复核',description:`产物 ${r.artifact_id} · 版本 ${r.version_id ?? '未建立'}。管理复核仅登记处理记录，不改写学习者答案或正式审查结论。`,fields:[{name:'result',label:'复核记录与证据定位',multiline:true}],submit:v=>workbench.post(`/users/${r.user_id}/support`,{result:`质量复核：产物 ${r.artifact_id}，版本 ${r.version_id ?? '未建立'}；${v.result}`})})}>登记复核</Button>
    {issueId&&issue.data?.find(i=>String(i.id)===issueId)&&<Button disabled={!admin.canOperate} onClick={()=>act(issueAction(issue.data!.find(i=>String(i.id)===issueId)!))}>登记关联事项结果</Button>}</div>
  </>}</LoadState></div>;
}
export function QualityPage() {
  const admin=useAdmin();const {params,set,list}=useQuery();const rows=useResource<List<QualityRow>>('/api/admin/workbench/quality',{page:1,page_size:20,...list});const [action,act]=useState<Action|null>(null);
  if(params.get('sample')) return <><EditorialHead module="quality"/><AuthorizedSamplePage key={params.get('sample')} id={params.get('sample')!} version={params.get('version')}/></>;
  return <><EditorialHead module="quality">{rows.data ? <VersionProof row={rows.data.items[0]} open={()=>set({detail:rows.data?.items[0]?.artifact_id})}/> : <span className="admin-figure-label">{rows.error?'产物索引读取失败，可在下方重试':'正在读取正式产物…'}</span>}</EditorialHead><section className="admin-panel"><div className="admin-toolbar"><FilterSearch value={params.get('artifact_id')||''} onSearch={artifact_id=>set({artifact_id,page:1},true)} placeholder="产物 ID"/>
  <Select aria-label="质量判定" value={params.get('quality')||''} options={[{value:'',label:'全部质量判定'},...['passed','rejected','revision_required','unreviewed','partial','generating'].map(value=>({value,label:label(value)}))]} onChange={quality=>set({quality,page:1})}/>
  <Select aria-label="版本追踪" value={params.get('tracking')||''} options={[{value:'',label:'全部版本范围'},{value:'missing',label:'正式版本缺失'}]} onChange={tracking=>set({tracking,page:1})}/>
  <AdvancedFilters active={["type", "defect_code", "generation_status", "start_date", "end_date"].filter(key=>params.has(key)).length}><FilterSearch value={params.get('type')||''} onSearch={type=>set({type,page:1},true)} placeholder="能力类型（如 workspace_research）"/>
  <FilterSearch value={params.get('defect_code')||''} onSearch={defect_code=>set({defect_code,page:1},true)} placeholder="首个结构化缺陷代码"/>
  <Select aria-label="版本生成阶段" value={params.get('generation_status')||''} options={[{value:'',label:'全部版本阶段'},...['draft','generated','reviewing','passed','blocked'].map(value=>({value,label:label(value)}))]} onChange={generation_status=>set({generation_status,page:1})}/>
  <DateFilters start={params.get('start_date')||''} end={params.get('end_date')||''} change={set}/></AdvancedFilters><Button onClick={()=>set(Object.fromEntries(Object.keys(list).map(k=>[k,undefined])))}>清除筛选</Button><Button onClick={rows.reload} loading={rows.busy}>刷新</Button></div>
  <LoadState resource={rows}>{rows.data&&<><Table<QualityRow> rowKey="artifact_id" dataSource={rows.data.items} loading={rows.busy} scroll={{x:1100}} locale={{emptyText:<Empty filtered={Object.keys(list).length>0}/>}} pagination={{current:rows.data.page,pageSize:rows.data.page_size,total:rows.data.total,showSizeChanger:true,pageSizeOptions:[20,50,100],showTotal:n=>`共 ${n} 份产物`,onChange:(page,page_size)=>set({page,page_size})}} columns={[
    {title:'产物 / 能力',key:'identity',width:190,render:(_,r)=><><Button type="link" style={{padding:0}} onClick={()=>set({detail:r.artifact_id})}>产物 #{r.artifact_id}</Button><small className="admin-cell-sub">{label(r.type)}</small></>},
    {title:'质量判定',dataIndex:'quality',width:130,render:v=><Status value={v}/>},
    {title:'依据 / 安全分类',key:'source',width:200,render:(_,r)=><>{r.review_source}<small className="admin-cell-sub">{r.defect_code || (r.quality==='rejected'?'质量阻断；结构化缺陷代码未记录':r.quality==='unreviewed'?'没有已记录的质量判定':label(r.verdict||r.content_verdict||r.research_verdict))}</small></>},
    {title:'发生阶段 / 版本',key:'version',width:170,render:(_,r)=><>{label(r.generation_status)}<small className="admin-cell-sub">{r.version_id?`v${r.version_number} · #${r.version_id}`:'正式版本缺失'}</small></>},
    {title:'时间',dataIndex:'created_at',width:165,render:time},
    {title:'影响范围',key:'user',width:100,render:(_,r)=><Link to={`/admin/users?detail=${r.user_id}`}>用户 {r.user_id}</Link>},
    {title:'操作',key:'actions',width:80,fixed:'right',render:(_,r)=><Button size="small" onClick={()=>set({detail:r.artifact_id})}>详情</Button>},
  ]}/><p className="admin-note">来源：GeneratedItem / GeneratedItemVersion / ArtifactReview / 已存质量回执 · 更新 {time(rows.data.updated_at)}<br/>技术执行成功与质量通过分别判断；未测量的维度不显示评分。</p></>}</LoadState></section>
  <Drawer title={`质量详情 · #${params.get('detail')||''}`} open={!!params.get('detail')} onClose={()=>set({detail:undefined})} getContainer={false} width="min(600px, 100vw)">{params.get('detail')&&<QualityDetails key={params.get('detail')} id={params.get('detail')!} issueId={params.get('issue')} act={act}/>}</Drawer><ActionModal action={action} close={()=>act(null)} done={admin.changed}/></>;
}
