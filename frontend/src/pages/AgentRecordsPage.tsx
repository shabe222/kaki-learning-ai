import { App as AntdApp, Button, Empty, Input, Pagination, Select, Skeleton, Space, Tag } from 'antd';
import { Activity, ArrowRight, Bot, CheckCircle2, ChevronRight, Clock3, Download, FileText, RefreshCw, Search, ShieldCheck, TrendingUp } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { agentRecordsApi } from '../api/agentRecords';
import type { AgentInvocationDetail, AgentInvocationItem, AgentRecordsStats, AgentTrendPoint, RecentOutputItem } from '../api/agentRecords';
import { PaperCard, TrendChart } from '../components/DataPanel';
import { PageHeader } from '../components/PageHeader';
import { useRequest } from '../hooks';
import styles from './AgentRecordsPage.module.css';

const STATUS_OPTIONS = [
  { value: 'all', label: '全部状态' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
  { value: 'running', label: '进行中' }
];

const AGENT_FILTER_OPTIONS = [
  { value: 'all', label: '全部智能体' },
  { value: 'profile', label: '学情分析' },
  { value: 'document', label: '资料整理' },
  { value: 'question', label: '试题生成' },
  { value: 'mindmap', label: '思维导图' },
  { value: 'code', label: '代码示例' },
  { value: 'path', label: '路径规划' },
  { value: 'tutor', label: '答疑协作' },
  { value: 'video', label: '课堂视频' }
];

function formatTime(iso?: string) {
  if (!iso) return '--';
  const value = iso.replace('T', ' ');
  return value.slice(0, 16);
}

function formatDuration(ms?: number) {
  if (!ms || ms <= 0) return '-';
  if (ms < 1000) return `${ms} ms`;
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec} 秒`;
  return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`;
}

function statusPill(status: string) {
  if (status === 'success') return { label: '成功', state: 'success' as const };
  if (status === 'failed') return { label: '失败', state: 'failed' as const };
  if (status === 'running') return { label: '进行中', state: 'running' as const };
  return { label: status, state: 'pending' as const };
}

function artifactExt(item: RecentOutputItem | { type?: string; ext?: string } | null | undefined) {
  if (!item) return 'PDF';
  if ('ext' in item && item.ext) return item.ext;
  const type = (item.type || '').toLowerCase();
  if (type.includes('ppt')) return 'PPTX';
  if (type.includes('mindmap')) return 'MD';
  if (type.includes('document') || type.includes('report')) return 'PDF';
  if (type.includes('question')) return 'JSON';
  if (type.includes('code')) return 'MD';
  if (type.includes('tutor')) return 'MD';
  return 'PDF';
}

function artifactBody(text: string) {
  if (!text) return '';
  return text.length > 1200 ? `${text.slice(0, 1200)}\n…（已截断）` : text;
}

export function AgentRecordsPage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const [statusFilter, setStatusFilter] = useState('all');
  const [agentFilter, setAgentFilter] = useState('all');
  const [keyword, setKeyword] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 12;
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<AgentInvocationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const statsReq = useRequest(() => agentRecordsApi.stats(), []);
  const trendReq = useRequest(() => agentRecordsApi.trend(), []);
  const recentReq = useRequest(() => agentRecordsApi.recentOutputs(6), []);
  const listReq = useRequest(
    () => agentRecordsApi.list({
      status: statusFilter === 'all' ? undefined : statusFilter,
      agent_name: agentFilter === 'all' ? undefined : agentFilter,
      keyword: keyword.trim() || undefined,
      page,
      page_size: pageSize
    }),
    [statusFilter, agentFilter, keyword, page]
  );

  useEffect(() => {
    setPage(1);
  }, [statusFilter, agentFilter, keyword]);

  useEffect(() => {
    const first = listReq.data?.list?.[0];
    if (!selectedId && first) {
      setSelectedId(first.id);
    }
  }, [listReq.data, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    agentRecordsApi
      .detail(selectedId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => {
        if (!cancelled) {
          setDetail(null);
          message.warning('调用详情加载失败');
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, message]);

  const stats: AgentRecordsStats | null = statsReq.data ?? null;
  const trend: AgentTrendPoint[] = trendReq.data?.series ?? [];
  const recent: RecentOutputItem[] = recentReq.data?.list ?? [];
  const list: AgentInvocationItem[] = listReq.data?.list ?? [];
  const total = listReq.data?.total ?? 0;

  const trendSeries = useMemo(
    () => trend.map((point) => ({ label: point.date.slice(5), value: point.total })),
    [trend]
  );
  const failedSeries = useMemo(
    () => trend.map((point) => ({ label: point.date.slice(5), value: point.failed })),
    [trend]
  );

  const retryAll = () => {
    statsReq.retry();
    trendReq.retry();
    recentReq.retry();
    listReq.retry();
  };

  return (
    <div className={styles.page} data-testid="agent-records-page">
      <section className={styles.statRow} data-testid="agent-records-stats">
        <div className={styles.statHero}>
          <h1>Agent 记录</h1>
          <p>查看智能体状态、触发记录与最近产出</p>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statIcon}><CheckCircle2 size={26} strokeWidth={1.6} /></div>
          <span className={styles.statLabel}>在线</span>
          <strong className={styles.statValue}>{stats?.online ?? 0}<span className={styles.statUnit}> 个</span></strong>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statIcon}><Activity size={26} strokeWidth={1.6} /></div>
          <span className={styles.statLabel}>忙碌</span>
          <strong className={styles.statValue}>{stats?.busy ?? 0}<span className={styles.statUnit}> 个</span></strong>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statIcon}><Clock3 size={26} strokeWidth={1.6} /></div>
          <span className={styles.statLabel}>空闲</span>
          <strong className={styles.statValue}>{stats?.idle ?? 0}<span className={styles.statUnit}> 个</span></strong>
        </div>
        <div className={styles.statCell} data-tone="health">
          <div className={styles.statIcon}><ShieldCheck size={26} strokeWidth={1.6} /></div>
          <span className={styles.statLabel}>整体健康度</span>
          <strong className={styles.statValue}>{stats?.health ?? '良好'}</strong>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statIcon}><TrendingUp size={26} strokeWidth={1.6} /></div>
          <span className={styles.statLabel}>触发成功率</span>
          <strong className={styles.statValue}>{stats ? `${stats.success_rate}` : '--'}<span className={styles.statUnit}>%</span></strong>
        </div>
      </section>

      <section className={styles.filterBar}>
        <Select
          data-testid="agent-records-filter-status"
          value={statusFilter}
          onChange={setStatusFilter}
          options={STATUS_OPTIONS}
        />
        <Select
          data-testid="agent-records-filter-agent"
          value={agentFilter}
          onChange={setAgentFilter}
          options={AGENT_FILTER_OPTIONS}
        />
        <Input
          data-testid="agent-records-filter-keyword"
          allowClear
          prefix={<Search size={15} />}
          placeholder="按任务摘要或智能体名搜索"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          onPressEnter={() => setPage(1)}
        />
        <Button
          data-testid="agent-records-retry"
          icon={<RefreshCw size={15} />}
          onClick={retryAll}
        >
          刷新
        </Button>
        <Button
          type="link"
          data-testid="agent-records-go-agents"
          onClick={() => navigate('/agents')}
        >
          回到智能体页 <ArrowRight size={14} />
        </Button>
      </section>

      {Boolean(statsReq.error || trendReq.error || recentReq.error || listReq.error) && (
        <div className={styles.errorBar}>
          <span>部分数据加载失败，已显示空状态。可点击「刷新」重试。</span>
          <Button icon={<RefreshCw size={15} />} onClick={retryAll}>重试</Button>
        </div>
      )}

      <section className={styles.split}>
        <PaperCard
          className={styles.listCard}
          title="调用列表"
          extra={<span>{total} 条</span>}
        >
          <div className={styles.listHeader}>
            <span>时间</span>
            <span>智能体 / 任务</span>
            <span>状态</span>
            <span>耗时</span>
            <span>产物类型</span>
            <span>操作</span>
          </div>
          <div className={styles.listScroll} data-testid="agent-records-list">
            {listReq.loading ? (
              <div style={{ padding: 18 }}>
                <Skeleton active paragraph={{ rows: 5 }} />
              </div>
            ) : list.length ? (
              list.map((row) => {
                const pill = statusPill(row.status);
                return (
                  <button
                    key={row.id}
                    type="button"
                    data-testid={`agent-records-row-${row.id}`}
                    data-active={selectedId === row.id}
                    className={styles.listRow}
                    onClick={() => setSelectedId(row.id)}
                  >
                    <time>{formatTime(row.created_at)}</time>
                    <span>
                      <span className="agentName" style={{ display: 'block', color: '#1d1511', fontWeight: 600, fontSize: 13 }}>
                        {row.agent_label || row.agent_name}
                      </span>
                      <span style={{ display: 'block', color: '#6f6259', fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {row.input_summary || '—'}
                      </span>
                    </span>
                    <span className={styles.statusPill} data-state={pill.state}>{pill.label}</span>
                    <span>{formatDuration(row.duration_ms)}</span>
                    <span>{row.output_type ? artifactExt({ type: row.output_type }) : '—'}</span>
                    <span>
                      <ChevronRight size={14} />
                    </span>
                  </button>
                );
              })
            ) : (
              <div className={styles.empty}>暂无调用记录 · 试着在「智能体」页发起一次调用</div>
            )}
          </div>
          <div className={styles.pagination}>
            <span>第 {page} 页 · 共 {total} 条</span>
            <Pagination
              size="small"
              current={page}
              pageSize={pageSize}
              total={total}
              showSizeChanger={false}
              onChange={(next) => {
                setPage(next);
                setSelectedId(null);
              }}
            />
          </div>
        </PaperCard>

        <PaperCard
          title="调用详情"
          extra={detail ? <Tag color="orange">#{detail.id}</Tag> : null}
        >
          {detailLoading ? (
            <div style={{ padding: 18 }}>
              <Skeleton active paragraph={{ rows: 6 }} />
            </div>
          ) : detail ? (
            <div className={styles.detail} data-testid="agent-records-detail">
              <div className={styles.detailHeader}>
                <h2>{detail.agent_label || detail.agent_name}</h2>
                <p className={styles.summary}>{detail.input_summary || '未提供任务摘要'}</p>
                <div className={styles.detailMeta}>
                  <span>状态：<b>{statusPill(detail.status).label}</b></span>
                  <span>耗时：<b>{formatDuration(detail.duration_ms)}</b></span>
                  <span>产物类型：<b>{detail.output_type ? artifactExt({ type: detail.output_type }) : '—'}</b></span>
                  <span>时间：<b>{formatTime(detail.created_at)}</b></span>
                </div>
              </div>
              <div className={styles.detailBody}>
                {detail.error_message && (
                  <div className={styles.fieldGroup}>
                    <label>失败原因</label>
                    <pre style={{ color: '#c2410c' }}>{detail.error_message}</pre>
                  </div>
                )}
                {detail.output ? (
                  <div className={styles.artifactBox}>
                    <header>
                      <strong>{detail.output.title || '生成产物'}</strong>
                      <span>{detail.output.ext} · {detail.output.size_label}</span>
                    </header>
                    <pre data-testid="agent-records-artifact-preview">{artifactBody(detail.output.content || '')}</pre>
                  </div>
                ) : (
                  <div className={styles.empty}>
                    {detail.status === 'failed' ? '本次调用失败，未生成产物' : '本次调用未生成可预览的产物'}
                  </div>
                )}
                <Space>
                  <Button
                    type="primary"
                    ghost
                    icon={<Download size={15} />}
                    disabled={!detail.output}
                    onClick={() => {
                      if (!detail.output) return;
                      const blob = new Blob([detail.output.content || ''], { type: 'text/plain;charset=utf-8' });
                      const url = URL.createObjectURL(blob);
                      const link = document.createElement('a');
                      link.href = url;
                      link.download = `${detail.output.title || 'agent-output'}.txt`;
                      link.click();
                      URL.revokeObjectURL(url);
                    }}
                  >
                    下载产物
                  </Button>
                  <Button onClick={() => navigate('/agents')}>回到智能体</Button>
                </Space>
              </div>
            </div>
          ) : (
            <div className={styles.empty}>
              <Bot size={28} />
              <p style={{ marginTop: 8 }}>选择左侧一条记录查看完整上下文与产物预览</p>
            </div>
          )}
        </PaperCard>
      </section>

      <section className={styles.bottomRow}>
        <PaperCard
          className={styles.trendCard}
          title="近 7 日调用趋势"
          extra={
            <div className={styles.trendLegend}>
              <span><i style={{ background: 'var(--primary)' }} />总调用</span>
              <span><i style={{ background: '#c64a4a' }} />失败</span>
            </div>
          }
        >
          {trendReq.loading ? (
            <Skeleton active paragraph={{ rows: 4 }} />
          ) : trendSeries.length ? (
            <TrendChart data={trendSeries} />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="近 7 日暂无调用" />
          )}
          {failedSeries.some((point) => point.value > 0) && (
            <div className={styles.trendLegend}>
              <span>失败趋势</span>
              <TrendChart data={failedSeries} />
            </div>
          )}
        </PaperCard>

        <PaperCard
          className={styles.recentCard}
          title="最近产出"
          extra={<Button type="link" onClick={() => navigate('/resources')}>全部产出 <ArrowRight size={14} /></Button>}
        >
          {recentReq.loading ? (
            <Skeleton active paragraph={{ rows: 5 }} />
          ) : recent.length ? (
            <div className={styles.recentList} data-testid="agent-records-recent">
              {recent.map((item) => (
                <div
                  key={item.output_id}
                  className="item"
                  onClick={() => {
                    setSelectedId(item.invocation_id);
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                  }}
                >
                  <span
                    className="badge"
                    data-type={item.ext.toLowerCase()}
                  >
                    {item.ext}
                  </span>
                  <div>
                    <strong>{item.title || '生成产物'}</strong>
                    <small>{item.agent_label} · {item.size_label}</small>
                  </div>
                  <time>{formatTime(item.created_at)}</time>
                </div>
              ))}
            </div>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无最近产出" />
          )}
        </PaperCard>
      </section>
    </div>
  );
}
