import { App as AntdApp, Button, Dropdown, Input, Modal, Alert, Skeleton } from 'antd';
import { PanelLeft, ArrowUpLeft, Search, Bell, LogOut, Waves, Leaf } from 'lucide-react';
import { NavLink, Outlet, useLocation, useNavigate, Link } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { bindMessage, httpErrorMessage } from '../api/client';
import { adminRoutes } from './adminRoutes';
import { AdminProvider, useAdmin } from './AdminContext';
import { workbench, type UserRow } from './workbenchApi';
import { clearAdminSession } from '../shared/auth';
interface SearchResult {users: UserRow[]; tasks: {key: string; user_id: number}[]; artifacts: {artifact_id: number; type: string}[]}
function Shell() {
  const {message} = AntdApp.useApp(); const admin = useAdmin(); const navigate = useNavigate(); const location = useLocation();
  const [collapsed, collapse] = useState(false); const [search, openSearch] = useState(false); const [keyword, setKeyword] = useState('');
  const [result, setResult] = useState<SearchResult | null>(null); const [error, setError] = useState(''); const [loading, busy] = useState(false); const [searchNow, setSearchNow] = useState(0);
  useEffect(() => {bindMessage(message);}, [message]);
  useEffect(() => {
    if (!search || !keyword.trim()) {setResult(null); return;}
    const abort = new AbortController(); const timer = setTimeout(() => {
      busy(true); workbench.get<SearchResult>('/search', {keyword: keyword.trim()}, abort.signal).then(r => {setResult(r); setError('');}).catch(e => {if (!abort.signal.aborted) setError(httpErrorMessage(e));}).finally(() => {if (!abort.signal.aborted) busy(false);});
    }, searchNow ? 0 : 300); return () => {clearTimeout(timer); abort.abort();};
  }, [search, keyword, searchNow]);
  const route = adminRoutes.find(r => r.path === location.pathname);
  return <div className="admin-shell" data-collapsed={collapsed}>
    <aside className="admin-sidebar">
      <Link to="/admin/dashboard" className="admin-brand" aria-label="柿子智学 DARYA 管理空间"><img src="/assets/brand/kaki-logo-mark.png" alt=""/><span><strong>柿子智学</strong><small>DARYA 管理空间</small></span></Link>
      <nav aria-label="管理导航">{adminRoutes.map(r => <NavLink key={r.path} to={r.path} title={r.title} aria-label={r.title} data-testid={`admin-nav-${r.module}`}><r.icon size={19}/><span>{r.title}</span></NavLink>)}</nav>
      <div className="admin-sidebar-foot"><NavLink to="/darya" title="返回用户端" aria-label="返回用户端"><ArrowUpLeft size={17}/><span>返回用户端</span></NavLink><small>让每一次处理都有依据</small></div>
    </aside>
    <div className="admin-main">
      <header className="admin-topbar"><Button type="text" aria-label={collapsed ? '展开导航' : '收起导航'} icon={<PanelLeft size={19}/>} onClick={() => collapse(!collapsed)}/><span className="admin-breadcrumb">管理空间 <span>/</span> {route?.title}</span><div className="admin-top-actions"><Button icon={<Search size={16}/>} onClick={() => openSearch(true)}>查找对象</Button><Link to="/admin/dashboard?issues=all" aria-label="待处理事项"><Bell size={19}/></Link><Dropdown trigger={['click']} menu={{items: [
        {key: 'kaki', label: '柿子 Kaki', icon: <Leaf size={15}/>, disabled: !admin.preferences}, {key: 'ocean', label: '海洋 Ocean', icon: <Waves size={15}/>, disabled: !admin.preferences},
        {type: 'divider'}, {key: 'settings', label: '外观与权限'}, {key: 'logout', label: '退出管理端', icon: <LogOut size={15}/>},
      ], onClick: ({key}) => {if (key === 'kaki' || key === 'ocean') void admin.setTheme(key).catch(() => {}); else if (key === 'settings') navigate('/admin/system?tab=appearance'); else {clearAdminSession(); navigate('/darya/login?mode=admin', {replace: true});}}}}><Button className="admin-identity">{admin.preferences?.username || '管理员'} · {admin.preferences?.role === 'observer' ? '观察者' : admin.preferences?.role === 'operations' ? '运营' : '管理'}</Button></Dropdown></div></header>
      {admin.preferenceError && <Alert type="error" showIcon message="管理偏好读取或保存失败" description={admin.preferenceError} action={<Button onClick={admin.retryPreferences}>重试</Button>}/>}
      <main id="admin-content" className="admin-content"><Outlet/></main>
      <footer className="admin-footer">柿子智学 · DARYA <span>运营、支持与质量共同工作</span></footer>
    </div>
    <Modal title="查找对象" open={search} onCancel={() => openSearch(false)} footer={null} getContainer={false} width={640}>
      <Input.Search autoFocus aria-label="对象查找" placeholder="用户名、用户 ID、任务 ID 或产物 ID" value={keyword} onChange={e => {setKeyword(e.target.value); setSearchNow(0);}} onSearch={() => setSearchNow(v => v + 1)}/>
      <p className="admin-note">只检索对象标识，结果按当前管理权限提供。</p>
      {loading && !result && <Skeleton active/>}{error && <Alert type="error" message={error}/>}
      {result && <div className="admin-search-results">{result.users.length + result.tasks.length + result.artifacts.length === 0 ? <p>没有匹配对象，请检查名称或 ID。</p> : <>
        <h3>用户</h3>{result.users.map(r => <Link key={r.user_id} to={`/admin/users?detail=${r.user_id}`} onClick={() => openSearch(false)}>{r.username} <code>#{r.user_id}</code></Link>)}
        <h3>任务</h3>{result.tasks.map(r => <Link key={r.key} to={`/admin/operations?detail=${encodeURIComponent(r.key)}`} onClick={() => openSearch(false)}><code>{r.key}</code> · 用户 {r.user_id}</Link>)}
        <h3>产物</h3>{result.artifacts.map(r => <Link key={r.artifact_id} to={`/admin/quality?detail=${r.artifact_id}`} onClick={() => openSearch(false)}>产物 <code>#{r.artifact_id}</code> · {r.type}</Link>)}
      </>}</div>}
    </Modal>
  </div>;
}
export function AdminLayout() {return <AdminProvider><Shell/></AdminProvider>;}
