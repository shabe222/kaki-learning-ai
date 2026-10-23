import { useEffect, useState } from 'react';
import { Alert, Button, Modal, Skeleton } from 'antd';
import { Bell } from 'lucide-react';
import { userAnnouncementApi, type UserAnnouncementData } from '../api/admin';
import { httpErrorMessage } from '../api/client';
import { AnnouncementContent } from './AnnouncementPresentation';

/** The bounded /darya entry uses the same announcement body as admin previews. */
export function DaryaAnnouncements({className}: {className?:string}) {
  const [open,setOpen]=useState(false);const [data,setData]=useState<UserAnnouncementData|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);const [selected,setSelected]=useState<number|null>(null);
  async function load() {setBusy(true);setError('');try{setData(await userAnnouncementApi.getActive());}catch(e){setError(httpErrorMessage(e));}finally{setBusy(false);}}
  useEffect(()=>{if(open)void load();},[open]);
  const row=data?.items.find(a=>a.id===selected);
  async function read(id:number) {setSelected(id);try{await userAnnouncementApi.markRead(id);setData(prev=>prev?{...prev,items:prev.items.map(a=>a.id===id?{...a,is_read:true}:a),unread_count:Math.max(0,prev.unread_count-(prev.items.find(a=>a.id===id)?.is_read?0:1))}:prev);}catch(e){setError(httpErrorMessage(e));}}
  return <><button type="button" className={className} aria-label="公告" data-testid="darya-announcements" onClick={()=>setOpen(true)}><Bell size={15} aria-hidden="true"/><span>公告</span></button>
  <Modal title={row?'公告全文':'产品公告'} open={open} onCancel={()=>{setOpen(false);setSelected(null);}} footer={null} getContainer={false} width={640}>
    {error&&<Alert type="error" message="公告读取失败" description={error} action={<Button onClick={()=>void load()}>重试</Button>}/>}
    <div style={{display:'flex',gap:12,marginBottom:16}}>{row&&<Button onClick={()=>setSelected(null)}>返回公告列表</Button>}<Button loading={busy} onClick={()=>void load()}>刷新公告</Button></div>
    {!data&&busy?<Skeleton active/>:row?<AnnouncementContent announcement={row}/>:<>{data?.items.map(a=><button type="button" key={a.id} onClick={()=>void read(a.id)} style={{display:'block',textAlign:'left',width:'100%',padding:'16px 4px',border:0,borderBottom:'1px solid var(--line,#ddd)',background:'transparent',color:'inherit',cursor:'pointer'}}>{!a.is_read&&<small>未读 · </small>}<strong>{a.title}</strong></button>)}{data&&!data.items.length&&<p>当前没有有效公告。</p>}</>}
  </Modal></>;
}
