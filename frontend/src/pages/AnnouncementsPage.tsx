import { Bell, ChevronRight, Clock3 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Modal, Tag } from 'antd';
import { userAnnouncementApi } from '../api/admin';
import { StateBlock } from '../components/StateBlock';
import styles from './AnnouncementsPage.module.css';
import { AnnouncementContent } from '../components/AnnouncementPresentation';

interface Announcement {
  id: number;
  title: string;
  content: string;
  level: string;
  published_at: string | null;
  expires_at: string | null;
  is_read: boolean;
}

function shortTime(v?: string | null) {
  if (!v) return '-';
  return v.replace('T', ' ').slice(0, 16);
}

function levelColor(l: string) {
  if (l === 'important') return 'red';
  if (l === 'warning') return 'orange';
  if (l === 'success') return 'green';
  return 'blue';
}

function levelLabel(l: string) {
  if (l === 'important') return '重要';
  if (l === 'warning') return '提醒';
  if (l === 'success') return '好消息';
  return '通知';
}

export function AnnouncementsPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [items, setItems] = useState<Announcement[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [selected, setSelected] = useState<Announcement | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const data = await userAnnouncementApi.getActive();
      setItems(data.items);
      setUnreadCount(data.unread_count);

      // Auto-open announcement if ?open=ID is in URL
      const openId = searchParams.get('open');
      if (openId) {
        const target = data.items.find(a => a.id === Number(openId));
        if (target) {
          setSelected(target);
          setDetailOpen(true);
          if (!target.is_read) {
            try { await userAnnouncementApi.markRead(target.id); setItems(prev => prev.map(a => a.id === target.id ? { ...a, is_read: true } : a)); setUnreadCount(c => Math.max(0, c - 1)); }
            catch { /* ignore */ }
          }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally { setLoading(false); }
  }, [searchParams]);

  useEffect(() => { load(); }, [load]);

  const openDetail = async (ann: Announcement) => {
    setSelected(ann);
    setDetailOpen(true);
    if (!ann.is_read) {
      try { await userAnnouncementApi.markRead(ann.id); setItems(prev => prev.map(a => a.id === ann.id ? { ...a, is_read: true } : a)); setUnreadCount(c => Math.max(0, c - 1)); }
      catch { /* ignore */ }
    }
  };

  if (loading) return <StateBlock state="loading" title="正在加载公告..." />;
  if (error) return <StateBlock state="error" title="加载公告失败" description={error} onRetry={load} />;

  return (
    <div className={styles.page} data-testid="announcements-page">
      <section className={styles.hero}>
        <h1><Bell size={24} /> 系统公告</h1>
        {unreadCount > 0 && <Tag color="red">{unreadCount} 条未读</Tag>}
      </section>

      {items.length === 0 ? (
        <StateBlock state="empty" title="暂无公告" description="管理员尚未发布公告，或所有公告已过期。有新的系统消息时，顶部铃铛会提示你。" />
      ) : (
        <section className={styles.list}>
          {items.map(ann => (
            <div
              key={ann.id}
              className={styles.card}
              data-level={ann.level}
              data-read={ann.is_read}
              onClick={() => openDetail(ann)}
            >
              <div className={styles.cardLeft} data-level={ann.level} />
              <div className={styles.cardBody}>
                <div className={styles.cardHeader}>
                  <strong>{ann.is_read ? ann.title : <>{ann.title} <Tag color="red" style={{ fontSize: 10, lineHeight: '16px' }}>NEW</Tag></>}</strong>
                  <Tag color={levelColor(ann.level)}>{levelLabel(ann.level)}</Tag>
                </div>
                <p className={styles.cardPreview}>{ann.content.slice(0, 150)}{ann.content.length > 150 ? '…' : ''}</p>
                <div className={styles.cardMeta}>
                  <span><Clock3 size={12} /> {shortTime(ann.published_at)}</span>
                  {ann.expires_at && <span>到期：{shortTime(ann.expires_at)}</span>}
                </div>
              </div>
              <ChevronRight size={18} className={styles.cardArrow} />
            </div>
          ))}
        </section>
      )}

      <Modal
        title={null}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={<Button onClick={() => setDetailOpen(false)}>关闭</Button>}
        width={640}
      >
        {selected && (
          <div className={styles.detail}>
            <AnnouncementContent announcement={selected}/>
          </div>
        )}
      </Modal>
    </div>
  );
}
