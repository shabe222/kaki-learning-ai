import { Alert, Button, Form, Input, Modal, Select, Skeleton, Table as AntTable, type TableProps } from 'antd';
import { useCallback, useEffect, useState, useRef, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { getData, httpErrorMessage } from '../api/client';
import { useAdmin } from './AdminContext';
export function time(value?: string | null) {
  if (!value) return '未记录';
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z');
  return Number.isNaN(date.getTime()) ? '时间无效' : new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(date);
}
export function number(value?: number | null, suffix='') {return value == null ? '未记录' : value.toLocaleString('zh-CN') + suffix;}
const copy: Record<string,string> = {
  generated:'已生成',personalized_handout:'个性化讲义',workspace_interactive_lesson:'互动课程',production:'正式运行',sqlite:'SQLite 数据库',deepseek:'DeepSeek',
  active:'正常',disabled:'停用',learner:'学习账户',qa:'测试账户',demo:'演示账户',inferred_qa:'旧命名识别测试 · 待确认',
  success:'执行成功',succeeded:'执行成功',completed:'执行完成',ready:'交付就绪',failed:'执行失败',error:'执行错误',blocked:'已阻断',running:'运行中',retrying:'重试中',queued:'排队中',planned:'已规划',confirmed:'已确认',awaiting_confirmation:'等待确认',collecting_requirements:'收集需求',cancelled:'已取消',
  artifact_exists:'产物已存在',agent_task:'智能体调用',workspace_task:'工作台任务',durable_job:'持久作业',artifact_only:'仅有产物',complete:'已有调用追踪',job_state:'已有作业状态',missing_call:'调用追踪缺失',
  unreviewed:'未审查',passed:'质量通过',rejected:'质量拒绝',revision_required:'需要修订',partial:'部分交付',generating:'仍在生成',pass:'通过',revise:'修订',block:'阻断',
  open:'待确认',acknowledged:'已确认',handling:'处理中',resolved:'已登记结果',closed:'已关闭',task_failed:'任务执行失败',quality_rejected:'质量阻断',warning:'提醒',important:'重要',info:'通知',draft:'草稿',published:'已发布',revoked:'已撤回',
  searching:'检索中',reading:'阅读来源',synthesizing:'整理中',reviewing:'审查中',generating_script:'生成脚本',generating_audio:'生成音频',generating_avatar:'生成视频',composing:'合成中',not_delivered:'未关联产物',artifact_linked:'已关联产物',model_config:'模型配置',permission_denied:'权限拒绝',
  not_probed:'未主动检查',probe_passed:'本次试验通过',probe_failed:'本次试验失败',connected:'连接已检查',not_configured:'未配置',unknown:'未知',
  document:'资料',workspace_lecture:'讲义',workspace_handout:'学习讲义',workspace_research:'研究',workspace_presentation:'演示课件',workspace_video:'视频',workspace_mindmap:'思维导图',workspace_assessment:'测评',workspace_code:'代码',mindmap:'思维导图',question:'练习',code:'代码实验',video:'视频',tutor:'辅导',profile:'画像',path:'学习路径',
  super_admin:'最高管理员',operations:'运营管理员',observer:'观察者',TRUSTED_REVIEWER:'可信正式评审',OWNER_REQUEST:'学习者请求复核',UNTRUSTED:'非正式记录',
  user_enable:'启用账户',user_disable:'停用账户',support_note:'支持记录',account_purpose:'用途确认',issue_update:'事项处理',task_retry:'任务重试',sample_read:'授权样本读取',sample_grant:'样本授权',admin_role:'角色调整',capability_probe:'能力试验',clear_cache:'清空缓存',update_storybook_glm_key:'绘本凭据更新',announcement_create:'创建公告',announcement_edit:'修订公告',announcement_publish:'发布公告',announcement_revoke:'撤回公告',
};
export function label(value?: string | null) {return value ? copy[value] || copy[value.toLowerCase()] || value : '未记录';}
export function Status({value}: {value?: string | null}) {
  const raw = String(value || '').toLowerCase();
  const tone = ['success','succeeded','completed','ready','passed','probe_passed','active','connected','published','pass'].includes(raw) ? 'success' : ['failed','error','blocked','rejected','block','probe_failed'].includes(raw) ? 'error' : ['running','warning','revision_required','partial','handling','queued','generating'].includes(raw) ? 'warning' : 'neutral';
  return <span className="admin-pill" data-tone={tone}><span aria-hidden="true">{tone === 'success' ? '✓' : tone === 'error' ? '!' : tone === 'warning' ? '◷' : '·'}</span>{label(value)}</span>;
}
export function useQuery() {
  const [params, update] = useSearchParams();
  const set = useCallback((patch: Record<string,string | number | undefined>, replace=false) => {
    update(previous => {const next = new URLSearchParams(previous); Object.entries(patch).forEach(([key,value]) => {if (value == null || value === '') next.delete(key); else next.set(key,String(value));}); return next;}, {replace});
  }, [update]);
  return {params, set, list: Object.fromEntries([...params.entries()].filter(([key]) => key !== 'detail' && key !== 'issue' && key !== 'preview'))};
}
export function useResource<T>(path: string, params: Record<string, unknown> = {}) {
  const admin = useAdmin(); const [revision, bump] = useState(0); const [data, setData] = useState<T | null>(null); const [error, fail] = useState(''); const [busy, loading] = useState(true);
  const serial = JSON.stringify(params); const identity=path+'?'+serial; const identityRef=useRef(identity);
  useEffect(() => {
    if (identityRef.current !== identity) {setData(null);identityRef.current=identity;}
    const abort = new AbortController(); loading(true); fail('');
    getData<T>(path, JSON.parse(serial), abort.signal).then(value => {if (!abort.signal.aborted) setData(value);}).catch(e => {if (!abort.signal.aborted) fail(httpErrorMessage(e));}).finally(() => {if (!abort.signal.aborted) loading(false);});
    return () => abort.abort();
  }, [path, serial, identity, revision, admin.refresh]);
  return {data,error,busy,reload: () => bump(v => v + 1)};
}
export function LoadState({resource, children}: {resource: {data: unknown; error: string; busy: boolean; reload: () => void}; children: ReactNode}) {
  return <>{resource.error && <Alert className="admin-form-error" type="error" showIcon message="这部分数据读取失败" description={resource.error} action={<Button onClick={resource.reload}>重试</Button>}/>}{!resource.data && resource.busy ? <Skeleton active paragraph={{rows:5}}/> : children}</>;
}
export function Empty({filtered=false}: {filtered?: boolean}) {return <div className="admin-empty"><strong>{filtered ? '没有符合筛选条件的记录' : '当前没有记录'}</strong>{filtered ? '调整条件或清除筛选后重试。' : '真实数据产生后会在这里出现。'}</div>;}
export function FilterSearch({value, onSearch, placeholder='用户名或用户 ID'}: {value: string; onSearch: (s:string)=>void; placeholder?: string}) {
  const [draft,setDraft]=useState(value); const onSearchRef=useRef(onSearch); onSearchRef.current=onSearch;
  useEffect(() => setDraft(value), [value]);
  useEffect(() => {if (draft === value) return; const timer=setTimeout(() => onSearchRef.current(draft),300); return () => clearTimeout(timer);}, [draft,value]);
  return <Input.Search style={{width:270}} aria-label={placeholder} placeholder={placeholder} value={draft} onChange={e => setDraft(e.target.value)} onSearch={() => onSearch(draft)}/>;
}
export interface ActionField {name: string; label: string; options?: {value:string;label:string}[]; initial?: string; required?: boolean; type?: 'number' | 'password'; multiline?: boolean}
export interface Action {title: string; description: string; fields: ActionField[]; submit: (values: Record<string,string>) => Promise<unknown>; dangerous?: boolean; button?: string}
export function WindowTitle({children}: {children:ReactNode}) {const admin=useAdmin();return <div style={{display:'flex',alignItems:'center',gap:14,paddingRight:24}}><span style={{flex:1}}>{children}</span><Select size="small" aria-label="当前窗口外观" value={admin.theme} disabled={!admin.preferences} style={{width:124}} options={[{value:'kaki',label:'柿子 Kaki'},{value:'ocean',label:'海洋 Ocean'}]} onChange={theme=>void admin.setTheme(theme).catch(()=>{})}/></div>;}
export function ActionModal({action, close, done}: {action: Action | null; close: () => void; done: () => void}) {
  const admin=useAdmin();const draftKey=action&&admin.preferences?`darya_admin_action_draft:${admin.preferences.admin_id}:${action.title}:${action.description}`:null;
  const [form]=Form.useForm(); const [busy,setBusy]=useState(false); const locked=useRef(false); const [error,setError]=useState('');
  const [restored,setRestored]=useState(false);
  useEffect(() => {if(!action) return; form.resetFields(); setError('');setRestored(false);let saved:Record<string,string>={};try{if(draftKey){saved=JSON.parse(sessionStorage.getItem(draftKey)||'{}');setRestored(Object.keys(saved).length>0);}}catch{saved={};}form.setFieldsValue({...Object.fromEntries(action.fields.map(f => [f.name,f.initial || ''])),...saved});}, [action,form,draftKey]);
  function clearDraft(){if(draftKey)try{sessionStorage.removeItem(draftKey);}catch{/* Storage unavailable; operation can still finish. */}}
  function remember(values:Record<string,string>){if(!draftKey||!action)return;const safe=Object.fromEntries(action.fields.filter(f=>f.type!=='password'&&f.name!=='confirmation').map(f=>[f.name,values[f.name]||'']));try{sessionStorage.setItem(draftKey,JSON.stringify(safe));}catch{/* Preserve the live form when browser storage is unavailable. */}}
  function cancel(){if(!busy){clearDraft();close();}}
  async function submit(values: Record<string,string>) {
    if (!action || locked.current) return; locked.current=true;setBusy(true);setError('');
    remember(values);try {await action.submit(values);clearDraft();done();close();} catch(e) {setError(httpErrorMessage(e));} finally {locked.current=false;setBusy(false);}
  }
  return <Modal title={action?<WindowTitle>{action.title}</WindowTitle>:null} open={!!action} afterClose={()=>form.resetFields()} onCancel={cancel} footer={null} getContainer={false} forceRender width={540} maskClosable={!busy} closable={!busy} keyboard={!busy}>
    <p className="admin-note">{action?.description}</p>{restored&&<Alert className="admin-form-error" type="info" message="已恢复上次未完成的输入，请核对后提交"/>}{error && <Alert className="admin-form-error" type="error" message="操作返回错误，输入已保留" description={error}/>}
    <Form form={form} layout="vertical" onFinish={submit} onValuesChange={(_,values)=>remember(values)}>{action?.fields.map(f => <Form.Item key={f.name} name={f.name} label={f.label} rules={f.required === false ? [] : [{required:true, whitespace:true, message:`请填写${f.label}`}]}>{f.options ? <Select options={f.options}/> : f.multiline ? <Input.TextArea rows={3} maxLength={1000}/> : <Input type={f.type || 'text'} autoComplete={f.type === 'password' ? 'new-password' : undefined}/>}</Form.Item>)}<div className="admin-toolbar"><Button onClick={cancel} disabled={busy}>取消并放弃输入</Button><Button htmlType="submit" type="primary" danger={action?.dangerous} loading={busy}>{action?.button || '确认并保存'}</Button></div></Form>
  </Modal>;
}
export function DateFilters({start,end,change}: {start:string;end:string;change:(values:Record<string,string|number>)=>void}) {return <><input aria-label="起始日期" type="date" value={start} onChange={e=>change({start_date:e.target.value,page:1})}/><span className="admin-note">至</span><input aria-label="结束日期" type="date" value={end} onChange={e=>change({end_date:e.target.value,page:1})}/></>;}
export function DetailFacts({items}: {items: [string,ReactNode][]}) {return <dl>{items.map(([name,value])=><div key={name} style={{display:'contents'}}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>;}
export const reasonField: ActionField={name:'reason',label:'处理原因',multiline:true};
export function Table<Row extends object>(props: TableProps<Row>) {
  const container=useRef<HTMLDivElement>(null);const [scrollable,setScrollable]=useState(false);
  useEffect(()=>{
    const scroller=container.current?.querySelector<HTMLElement>('.ant-table-content');
    if(!scroller)return;
    const measure=()=>{const overflow=scroller.scrollWidth>scroller.clientWidth+1;setScrollable(overflow);scroller.tabIndex=overflow?0:-1;if(overflow){scroller.setAttribute('role','region');scroller.setAttribute('aria-label','记录表格，可用左右方向键滚动查看全部字段');}else{scroller.removeAttribute('role');scroller.removeAttribute('aria-label');}};
    measure();const observer=new ResizeObserver(measure);observer.observe(scroller);return()=>observer.disconnect();
  },[props.dataSource,props.columns]);
  return <div ref={container} className="admin-data-table">{scrollable&&<p className="admin-scroll-hint">左右滚动查看全部字段 · 支持键盘方向键</p>}<AntTable<Row> {...props}/></div>;
}
