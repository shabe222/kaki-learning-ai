import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import { ConfigProvider, App as AntdApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { workbench, readAdminTheme, cacheAdminTheme, type Preferences, type AdminTheme } from './workbenchApi';
import { readDaryaTheme } from '../features/tide/theme/daryaTheme';
import { httpErrorMessage } from '../api/client';
import './workbench.css';
import './editorial.css';
interface AdminContextValue {preferences: Preferences | null; theme: AdminTheme; setTheme: (t: AdminTheme) => Promise<void>; setPreferences: (p: Partial<Preferences>) => Promise<void>; preferenceError: string; retryPreferences: () => void; canOperate: boolean; canConfigure: boolean; refresh: number; changed: () => void}
const Context = createContext<AdminContextValue | null>(null);
function storedIdentity(): number | undefined {try{return JSON.parse(localStorage.getItem('admin_user') || '{}')?.user?.admin_id;}catch{return undefined;}}
export function useAdmin() { const value = useContext(Context); if (!value) throw new Error('AdminProvider missing'); return value; }
export function AdminProvider({children}: {children: ReactNode}) {
  const [preferences, setValue] = useState<Preferences | null>(null); const [preferenceError, setError] = useState('');
  const [revision, setRevision] = useState(0); const [refresh, update] = useState(0);
  const stored = storedIdentity();
  const [theme, applyTheme] = useState<AdminTheme>(() => readAdminTheme(stored) || readDaryaTheme());
  useEffect(() => {
    const abort = new AbortController();
    workbench.get<Preferences>('/preferences', undefined, abort.signal).then(async p => {
      const selected = p.theme || readAdminTheme(p.admin_id) || theme;
      setValue({...p, theme: selected}); applyTheme(selected); cacheAdminTheme(selected, p.admin_id); setError('');
      if (!p.theme) await workbench.put('/preferences', {...p, theme: selected});
    }).catch(e => {if (!abort.signal.aborted) setError(httpErrorMessage(e));});
    return () => abort.abort();
    // Read the entry theme once; theme changes must preserve forms and drawers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision]);
  const save = useCallback(async (patch: Partial<Preferences>) => {
    if (!preferences) throw new Error('管理员偏好尚未读取，请重试');
    const next = {...preferences, ...patch};
    try {await workbench.put('/preferences', next); setValue(next); if (next.theme) {applyTheme(next.theme); cacheAdminTheme(next.theme, next.admin_id);} setError('');}
    catch (e) {setError(httpErrorMessage(e)); throw e;}
  }, [preferences]);
  const ocean = theme === 'ocean';
  return <Context.Provider value={{preferences, theme, setTheme: t => save({theme: t}), setPreferences: save, preferenceError, retryPreferences: () => setRevision(v => v + 1), canOperate: !!preferences?.can_operate, canConfigure: !!preferences?.can_configure, refresh, changed: () => update(v => v + 1)}}>
    <ConfigProvider locale={zhCN} theme={{token: {colorLink: ocean ? '#276777' : '#a7552d', colorLinkHover: ocean ? '#205461' : '#884424', colorLinkActive: ocean ? '#183f49' : '#71371d', colorPrimary: ocean ? '#276777' : '#a7552d', colorText: ocean ? '#263d46' : '#382e27', colorTextSecondary: ocean ? '#526873' : '#746457', colorBgContainer: ocean ? '#fdfefe' : '#fffdf9', colorBgElevated: ocean ? '#fdfefe' : '#fffdf9', colorBorder: ocean ? '#ccdadd' : '#ded5c8', colorSuccess: '#34734b', colorWarning: '#89601d', colorError: '#ad3c3a', borderRadius: 8, controlHeight: 38, fontSize: 14, motionDurationMid: '0.2s'}, components: {Table: {cellPaddingBlock: preferences?.compact ? 10 : 14, headerBg: ocean ? '#eef4f5' : '#f6f0e6'}, Drawer: {paddingLG: 24}, Modal: {padding: 24}}}}>
      <AntdApp><div className="admin-theme" data-admin-theme={theme} data-compact={preferences?.compact}>{children}</div></AntdApp>
    </ConfigProvider>
  </Context.Provider>;
}
