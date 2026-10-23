import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, App as AntdApp, Button, Empty, Input, Segmented, Spin, Tag } from 'antd';
import { ArrowRight, Clock3, Film, Play, RefreshCw, Search, Sparkles } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { getData, postData } from '../api/client';
import type { GeneratedItem } from '../api/modules';
import { AuthenticatedVideo } from '../components/AuthenticatedVideo';
import branchArt from '../assets/generated/persimmon-branch.png';
import mountainArt from '../assets/generated/hero-mountain-wash.png';
import emptyVideoArt from '../assets/generated/empty-persimmon-basket.jpg';
import styles from './VideoPage.module.css';

type VideoStatus = 'queued' | 'generating_script' | 'generating_audio' | 'generating_avatar' | 'composing' | 'ready' | 'failed';

interface VideoRow extends GeneratedItem {
  metadata: Record<string, any>;
  status: VideoStatus;
}

const ACTIVE_STATES = new Set<VideoStatus>(['queued', 'generating_script', 'generating_audio', 'generating_avatar', 'composing']);
const STATUS_LABEL: Record<VideoStatus, string> = {
  queued: '排队中',   generating_script: '生成讲稿', generating_audio: '合成音频',
  generating_avatar: '正在准备讲师画面', composing: '正在合成视频', ready: '可播放', failed: '需要重新制作'
};

function toVideoRow(item: GeneratedItem): VideoRow {
  const metadata = item.raw_data || {};
  const rawStatus = String(metadata.status || 'failed') as VideoStatus;
  return { ...item, metadata, status: STATUS_LABEL[rawStatus] ? rawStatus : 'failed' };
}

export function VideoPage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const [rows, setRows] = useState<VideoRow[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<'all' | 'ready' | 'generating'>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retrying, setRetrying] = useState<number | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const data = await getData<GeneratedItem[]>('/api/items/list', { type: 'workspace_video' });
      const next = data.map(toVideoRow);
      setRows(next);
      setSelectedId((current) => current && next.some((item) => item.id === current) ? current : next[0]?.id ?? null);
      setError('');
    } catch {
      setError('视频列表暂时无法加载，请检查服务后重试。');
    } finally { if (!quiet) setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!rows.some((row) => ACTIVE_STATES.has(row.status))) return;
    const timer = window.setInterval(() => void load(true), 2500);
    return () => window.clearInterval(timer);
  }, [load, rows]);

  const visible = useMemo(() => rows.filter((row) => {
    const matchesQuery = `${row.title} ${row.chapter || ''}`.toLowerCase().includes(query.trim().toLowerCase());
    const matchesFilter = filter === 'all' || (filter === 'ready' ? row.status === 'ready' : ACTIVE_STATES.has(row.status));
    return matchesQuery && matchesFilter;
  }), [filter, query, rows]);
  const selected = rows.find((row) => row.id === selectedId) || visible[0] || null;
  const readyCount = rows.filter((row) => row.status === 'ready').length;
  const activeCount = rows.filter((row) => ACTIVE_STATES.has(row.status)).length;

  const createVideo = () => {
    sessionStorage.setItem('kaki_video_generation_intent_v1', '请帮我生成一段 15 到 20 秒的 DARYA 教学视频');
    navigate('/ai-learning');
  };
  const retry = async (item: VideoRow) => {
    setRetrying(item.id);
    try {
      await postData(`/api/chat/tasks/videos/${item.id}/retry`, {});
      message.success('已从原产物重新入队');
      await load(true);
    } catch { /* shared client presents the backend error */ }
    finally { setRetrying(null); }
  };

  return <section className={styles.page} data-testid="video-page">
    <header className={styles.hero}>
      <img className={styles.branch} src={branchArt} alt="" aria-hidden="true" />
      <div><span><Sparkles size={15} /> 同一学习产物体系</span><h1>视频学习</h1><p>播放已有教学视频，也可以让 DARYA 围绕真实学习上下文生成一段短讲。</p></div>
      <div className={styles.summary} aria-label="真实视频摘要">
        <span><strong>{rows.length}</strong>全部视频</span>
        <span><strong>{readyCount}</strong>可播放</span>
        {activeCount > 0 && <span><strong>{activeCount}</strong>生成中</span>}
      </div>
      <Button type="primary" icon={<Sparkles size={16} />} onClick={createVideo}>去 AI 学习生成</Button>
    </header>

    {error && <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => void load()}>重试</Button>} />}
    {loading ? <div className={styles.loading}><Spin /><span>正在读取视频产物…</span></div> : rows.length === 0 ?
      <div className={styles.empty}>
        <img src={mountainArt} alt="" aria-hidden="true" />
        <Empty image={<img src={emptyVideoArt} alt="" aria-hidden="true" style={{ width: 156, height: 'auto', borderRadius: 14, mixBlendMode: 'multiply' }} />} styles={{ image: { height: 'auto' } }} description={<><strong>还没有教学视频</strong><p>从 AI 学习提出主题，生成任务会在这里显示真实阶段和最终视频。</p></>}>
          <Button type="primary" onClick={createVideo}>和 DARYA 生成第一段</Button>
        </Empty>
      </div>
      : <div className={styles.workspace}>
        <aside className={styles.library} aria-label="视频列表">
          <div className={styles.libraryHead}><div><Film size={18} /><strong>全部视频</strong></div><Button type="text" size="small" icon={<RefreshCw size={15} />} onClick={() => void load()}>刷新</Button></div>
          <Input prefix={<Search size={15} />} allowClear value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索视频主题" />
          <Segmented block value={filter} onChange={(value) => setFilter(value as typeof filter)} options={[{ label: '全部', value: 'all' }, { label: '可播放', value: 'ready' }, { label: '生成中', value: 'generating' }]} />
          <div className={styles.videoList}>{visible.map((item) => <button type="button" className={item.id === selected?.id ? styles.activeVideo : ''} onClick={() => setSelectedId(item.id)} key={item.id}>
            <span className={styles.thumb}><Play size={18} fill="currentColor" /></span>
            <span><strong>{item.title}</strong><small><Clock3 size={12} /> {Number(item.metadata.duration_sec || 20)} 秒 · {STATUS_LABEL[item.status]}</small></span>
            {item.metadata.dry_run ? <Tag color="gold">本机讲解</Tag> : <Tag color="orange">AI 生成</Tag>}
          </button>)}</div>
          {!visible.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的视频" />}
        </aside>

        <main className={styles.playerStage}>
          <div className={styles.playerHead}><div>{selected?.metadata.dry_run ? <Tag color="gold">本机讲解</Tag> : <Tag color="orange">AI 生成</Tag>}<h2>{selected?.title}</h2><p>{selected?.chapter || '教学短讲'}</p></div><Button onClick={() => selected && navigate(`/artifacts/${selected.id}`)}>打开同一产物 <ArrowRight size={14} /></Button></div>
          {selected?.status === 'ready' && selected.metadata.video_url ? <AuthenticatedVideo src={String(selected.metadata.video_url)} title={selected.title} /> :
            <div className={styles.processing} data-status={selected?.status}>
              <Film size={38} /><strong>{selected ? STATUS_LABEL[selected.status] : '请选择视频'}</strong>
              <p>{String(selected?.metadata.safe_error || selected?.metadata.summary || (selected && ACTIVE_STATES.has(selected.status) ? '云端正在生成 DARYA 口型画面，通常需要 1 到 4 分钟，请稍候。' : '当前视频媒体不可用。'))}</p>
              {selected?.status === 'failed' && <Button type="primary" loading={retrying === selected.id} onClick={() => void retry(selected)}>从原产物重试</Button>}
            </div>}
          {selected && <section className={styles.transcript}><div><strong>视频讲稿</strong><span>{selected.metadata.provider || 'KAKI Cloud Video'} · {selected.metadata.model || '等待模型信息'}</span></div><p>{String(selected.metadata.transcript || selected.content || '视频就绪后，经过校验的讲稿会显示在这里。')}</p></section>}
        </main>

        <aside className={styles.playlist} aria-label="播放列表">
          <header><strong>播放列表</strong><span>{rows.length} 条真实记录</span></header>
          {rows.slice(0, 8).map((item, index) => <button type="button" key={item.id} onClick={() => setSelectedId(item.id)} data-active={item.id === selected?.id}><b>{String(index + 1).padStart(2, '0')}</b><span><strong>{item.title}</strong><small>{STATUS_LABEL[item.status]}</small></span></button>)}
          <footer><span>每段视频都会保存在你的学习成果中，随时可以继续观看。</span><Button type="link" onClick={() => navigate('/artifacts?type=video')}>查看全部成果</Button></footer>
        </aside>
      </div>}
  </section>;
}
