import { Tag } from 'antd';
import { Clock3 } from 'lucide-react';
export interface AnnouncementPresentationData {title:string;content:string;level:string;published_at?:string|null;expires_at?:string|null}
export function announcementLevel(level:string) {return {info:'通知',important:'重要',warning:'提醒',success:'好消息'}[level] || '通知';}
export function announcementColor(level:string) {return {important:'red',warning:'orange',success:'green'}[level] || 'blue';}
function date(value?:string|null) {return value ? new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'short'}).format(new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value)?value:value+'Z')) : '发布后生效';}
/** Shared by the real learner detail and both administrator previews. */
export function AnnouncementContent({announcement}: {announcement:AnnouncementPresentationData}) {
  return <article data-testid="announcement-presentation" style={{color:'var(--admin-ink,var(--ink,#2e2926))'}}>
    <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:12}}><Tag color={announcementColor(announcement.level)}>{announcementLevel(announcement.level)}</Tag><span style={{fontSize:12,color:'var(--admin-muted,var(--muted))'}}>{date(announcement.published_at)}</span></div>
    <h2 style={{margin:'0 0 16px',fontSize:21}}>{announcement.title}</h2>
    <div style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',lineHeight:1.85,fontSize:15}}>{announcement.content}</div>
    {announcement.expires_at && <p style={{marginTop:20,fontSize:12,color:'var(--admin-muted,var(--muted))'}}><Clock3 size={12}/> 本公告有效期至 {date(announcement.expires_at)}</p>}
  </article>;
}
