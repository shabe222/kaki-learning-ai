import { App as AntdApp, Empty, Input, Modal, Select, Space, Tag } from 'antd';
import { Download, Eye, FileText, Pencil, RefreshCw, Search, Trash2, UploadCloud } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { deleteData, getData, http, patchData, postFormData } from '../api/client';
import styles from './ResourceTable.module.css';

interface WorkspaceMaterial {
  material_id: string; file_name: string; file_type: string; mime_type: string;
  size_bytes: number; parse_status: string; parse_error?: string | null; page_count?: number | null;
  section_count: number; char_count: number; structure: Array<{ heading?: string; page_start?: number | null }>; preview_text: string; origin: string;
  category: string; tags: string[]; learning_node_ids: string[]; created_at: string; updated_at: string;
}

function sizeLabel(value: number) { return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`; }
function statusLabel(value: string) { return value === 'ready' ? '可使用' : value === 'failed' ? '解析失败' : '解析中'; }
function typeLabel(value: string) {
  const known: Record<string, string> = { markdown: 'MD', md: 'MD', docx: 'DOC', pdf: 'PDF', txt: 'TXT', png: 'PNG', jpg: 'JPG', jpeg: 'JPG', webp: 'WEBP' };
  const normalized = String(value || '').toLowerCase();
  return known[normalized] || normalized.toUpperCase().slice(0, 4) || 'FILE';
}

export function ResourceTable({ module: _module }: { module: string }) {
  const { message, modal } = AntdApp.useApp();
  const [rows, setRows] = useState<WorkspaceMaterial[]>([]);
  const [keyword, setKeyword] = useState('');
  const [type, setType] = useState('all');
  const [category, setCategory] = useState('all');
  const [facetRows, setFacetRows] = useState<WorkspaceMaterial[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState<WorkspaceMaterial | null>(null);
  const uploadInput = useRef<HTMLInputElement>(null);

  const filtering = Boolean(keyword.trim()) || type !== 'all' || category !== 'all';

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      setRows(await getData<WorkspaceMaterial[]>('/api/chat/materials/library', {
        ...(keyword.trim() ? { q: keyword.trim() } : {}),
        ...(type !== 'all' ? { file_type: type } : {}),
        ...(category !== 'all' ? { category } : {}),
      }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : '资料库加载失败'); }
    finally { setLoading(false); }
  }, [keyword, type, category]);
  const loadFacets = useCallback(async () => {
    try { setFacetRows(await getData<WorkspaceMaterial[]>('/api/chat/materials/library')); } catch { /* 主列表负责显示错误 */ }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void loadFacets(); }, [loadFacets]);
  const types = Array.from(new Set(facetRows.map(row => row.file_type))).map(value => ({ value, label: value.toUpperCase() }));
  const categories = Array.from(new Set(facetRows.map(row => row.category).filter(value => value && value !== '未分类'))).map(value => ({ value, label: value }));
  const refresh = async () => {
    await Promise.all([load(), loadFacets()]);
    window.dispatchEvent(new CustomEvent('kaki-material-library-changed'));
  };

  const download = async (row: WorkspaceMaterial) => {
    const response = await http.get(`/api/chat/materials/${row.material_id}/download`, { responseType: 'blob' });
    const url = URL.createObjectURL(response.data); const link = document.createElement('a'); link.href = url; link.download = row.file_name; link.click(); URL.revokeObjectURL(url);
  };
  const editMetadata = (row: WorkspaceMaterial) => {
    let nextName = row.file_name; let nextCategory = row.category === '未分类' ? '' : row.category; let nextTags = (row.tags || []).join('，');
    modal.confirm({ title: '编辑资料信息', content: <Space direction="vertical" style={{ width: '100%' }}>
      <Input defaultValue={row.file_name} placeholder="资料标题 / 文件名" onChange={e => { nextName = e.target.value; }} />
      <Input defaultValue={nextCategory} placeholder="分类" onChange={e => { nextCategory = e.target.value; }} />
      <Input defaultValue={nextTags} placeholder="标签，用逗号分隔" onChange={e => { nextTags = e.target.value; }} />
    </Space>, okText: '保存', cancelText: '取消',
      onOk: async () => { await patchData(`/api/chat/materials/${row.material_id}`, { file_name: nextName, category: nextCategory, tags: nextTags.split(/[，,]/).map(value => value.trim()).filter(Boolean) }); message.success('资料信息已更新'); await refresh(); } });
  };
  const openDetail = async (row: WorkspaceMaterial) => {
    setDetailLoading(true);
    try { setDetail(await getData<WorkspaceMaterial>(`/api/chat/materials/${row.material_id}`)); }
    catch (reason) { message.error(reason instanceof Error ? reason.message : '资料详情读取失败'); }
    finally { setDetailLoading(false); }
  };
  const remove = (row: WorkspaceMaterial) => modal.confirm({ title: '删除资料', content: `将删除「${row.file_name}」及其解析内容，操作不可恢复。`, okButtonProps: { danger: true }, okText: '删除', cancelText: '取消', onOk: async () => { await deleteData(`/api/chat/materials/${row.material_id}`); message.success('资料已删除'); if (detail?.material_id === row.material_id) setDetail(null); await refresh(); } });
  const upload = async (file?: File) => {
    if (!file) return;
    const form = new FormData(); form.append('thread_id', sessionStorage.getItem('ai_workspace_test_thread_id') || 'resource_library'); form.append('file', file);
    setUploading(true);
    try { await postFormData('/api/chat/materials', form); message.success('资料已上传并完成解析'); await refresh(); }
    catch (reason) { message.error(reason instanceof Error ? reason.message : '上传失败'); }
    finally { setUploading(false); if (uploadInput.current) uploadInput.current.value = ''; }
  };

  if (error && !rows.length && !facetRows.length) return <Empty description={error}><button className={styles.retryButton} onClick={() => void refresh()}>重试</button></Empty>;
  return <div className={styles.tableBox} data-testid="real-material-library">
    <input ref={uploadInput} hidden type="file" accept=".pdf,.docx,.txt,.md,.markdown,.png,.jpg,.jpeg,.webp" onChange={e => void upload(e.target.files?.[0])} />
    <button type="button" className={styles.uploadCta} data-testid="material-upload-cta" disabled={uploading} onClick={() => uploadInput.current?.click()}>
      <UploadCloud size={22} />
      <strong>{uploading ? '正在上传并解析…' : '导入学习资料'}</strong>
      <span>支持 PDF、Word、Markdown、文本和图片，上传后自动解析，可以在 AI 学习中直接引用</span>
    </button>

    <div className={styles.toolbar}>
      <Select value={type} onChange={setType} options={[{ value: 'all', label: '全部类型' }, ...types]} />
      <Select value={category} onChange={setCategory} options={[{ value: 'all', label: '全部分类' }, ...categories]} />
      <Input prefix={<Search size={16} />} placeholder="搜索资料名称、分类或内容" value={keyword} onChange={e => setKeyword(e.target.value)} allowClear />
      <button type="button" className={styles.toolButton} aria-label="重新加载" onClick={() => void refresh()}><RefreshCw size={16} /></button>
    </div>
    {error && <p className={styles.filterError}>筛选请求失败：{error} <button type="button" onClick={() => void refresh()}>重试</button></p>}

    {loading && !rows.length ? <p className={styles.loadingLine}>正在加载资料…</p>
      : rows.length ? <ul className={styles.list} data-testid="material-list">
        {rows.map(row => <li key={row.material_id} className={styles.item}>
          <span className={styles.fileBadge} data-type={row.file_type}>{typeLabel(row.file_type)}</span>
          <div className={styles.itemMain}>
            <strong>{row.file_name}</strong>
            <span className={styles.itemMeta}>
              {sizeLabel(row.size_bytes)}
              {row.page_count ? ` · ${row.page_count} 页` : ''}
              {row.char_count ? ` · ${row.char_count.toLocaleString()} 字` : ''}
              {` · 更新于 ${String(row.updated_at).replace('T', ' ').slice(0, 16)}`}
            </span>
            {(row.category && row.category !== '未分类') || row.tags?.length ? <span className={styles.itemTags}>
              {row.category && row.category !== '未分类' && <Tag>{row.category}</Tag>}
              {(row.tags || []).slice(0, 3).map(tag => <Tag key={tag}>{tag}</Tag>)}
            </span> : null}
          </div>
          <span className={styles.status} data-status={row.parse_status}>{statusLabel(row.parse_status)}</span>
          <div className={styles.itemActions}>
            <button type="button" aria-label={`查看${row.file_name}`} disabled={detailLoading} onClick={() => void openDetail(row)}><Eye size={15} /></button>
            <button type="button" aria-label={`下载${row.file_name}`} onClick={() => void download(row)}><Download size={15} /></button>
            <button type="button" aria-label={`编辑${row.file_name}`} onClick={() => editMetadata(row)}><Pencil size={15} /></button>
            <button type="button" aria-label={`删除${row.file_name}`} data-danger="true" onClick={() => remove(row)}><Trash2 size={15} /></button>
          </div>
        </li>)}
      </ul>
      : <div className={styles.emptyArea} data-testid="material-empty">
        <FileText size={30} />
        <strong>{filtering ? '没有符合条件的资料' : '还没有上传资料'}</strong>
        <span>{filtering ? '换个关键词或清除筛选条件试试。' : '把讲义、真题或参考资料放进来，柿小吉就能围绕它们陪你学。'}</span>
        {filtering && <button type="button" className={styles.retryButton} onClick={() => { setKeyword(''); setType('all'); setCategory('all'); }}>清除筛选</button>}
      </div>}

    <Modal open={Boolean(detail)} title={detail?.file_name || '资料详情'} footer={null} onCancel={() => setDetail(null)}>
      {detail && <div><Space wrap><Tag>{detail.file_type.toUpperCase()}</Tag><Tag color={detail.parse_status === 'ready' ? 'green' : detail.parse_status === 'failed' ? 'red' : 'orange'}>{statusLabel(detail.parse_status)}</Tag><Tag>{sizeLabel(detail.size_bytes)}</Tag></Space>
        <p style={{ marginTop: 16, lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>{detail.preview_text || (detail.parse_status === 'ready' ? '资料已完成解析，可在 AI 学习中引用。' : detail.parse_error || '正在解析资料。')}</p>
        {detail.structure?.length > 0 && <div><strong>内容结构</strong>{detail.structure.slice(0, 12).map((item, index) => <p key={`${item.heading}-${index}`} style={{ margin: '4px 0' }}>{item.heading || `第 ${index + 1} 节`}{item.page_start ? ` · 第 ${item.page_start} 页` : ''}</p>)}</div>}
      </div>}
    </Modal>
  </div>;
}
