import { App as AntdApp, Badge, Input } from 'antd';
import { Bell, BookOpen, ChevronLeft, Compass, FileText, FolderOpen, LogOut, NotebookPen, PlaySquare, Search, Settings, Sparkles, UserRound, X } from 'lucide-react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useEffect, useMemo, useRef, useState } from 'react';
import { getData } from '../api/client';
import { moduleApi } from '../api/modules';
import type { BackendProfile, DashboardNotification, SearchResultItem } from '../api/modules';
import { userAnnouncementApi } from '../api/admin';
import { UserAvatar } from '../components/UserAvatar';
import { AiWorkspaceKernel } from '../components/ai-learning/AiWorkspaceKernel';
import { StudyTime } from '../components/study-time/StudyTime';
import { endStudySession, ensureStudySessionStarted } from '../components/study-time/studyTimeStore';
import { useAuthStore } from '../stores/authStore';
import { readDaryaTheme } from '../features/tide/theme/daryaTheme';
import styles from './MainLayout.module.css';
import impressionWindowBg from '../assets/generated/impression-window-bg.png';
import impressionWindowReeds from '../assets/generated/impression-window-reeds.png';
import persimmonLogo from '../assets/generated/persimmon-logo.png';
import toolPracticeArt from '../assets/tool-watercolor/practice.jpg';
import toolLearningArt from '../assets/tool-watercolor/learning.jpg';
import toolNotesArt from '../assets/tool-watercolor/notes.jpg';
import toolResourcesArt from '../assets/tool-watercolor/resources.jpg';
import toolVideoArt from '../assets/tool-watercolor/video.jpg';
import toolExploreArt from '../assets/tool-watercolor/explore.jpg';

type Tool = { label: string; description: string; art: string; icon: typeof BookOpen; target?: string; adventure?: boolean };
type ImpressionNode = { label: string; tone: 'warm' | 'calm' | 'focus' | 'soft' };

const toolGroups: Array<{ title: string; items: Tool[] }> = [
  { title: '学习', items: [
    { label: '练习', description: '用几道题巩固刚学会的内容', art: toolPracticeArt, icon: FileText, target: '/exam' },
    { label: '我的学习', description: '回到为你安排的学习路线', art: toolLearningArt, icon: BookOpen, target: '/learning' },
    { label: '笔记', description: '记下理解、疑问与灵感', art: toolNotesArt, icon: NotebookPen, target: '/notes' },
  ] },
  { title: '资料与创作', items: [
    { label: '资料', description: '整理和调用自己的学习资料', art: toolResourcesArt, icon: FolderOpen, target: '/resources' },
    { label: '视频', description: '让柿小吉用动态课堂讲给你听', art: toolVideoArt, icon: PlaySquare, target: '/video' },
  ] },
  { title: '探索', items: [{ label: '探索', description: '把学习主题变成一段互动旅程', art: toolExploreArt, icon: Compass, adventure: true }] },
];

const workspaceLabels: Record<string, string> = {
  '/learning': '我的学习', '/resources': '资料库', '/notes': '学习笔记',
  '/artifacts': '学习产物', '/exam': '练习与测评', '/video': '视频学习', '/profile': '个人中心',
};

function profileValue(profile: BackendProfile | null, key: string) {
  const raw = profile?.profile_data?.profile_onboarding;
  const entries = raw && typeof raw === 'object' ? (raw as Record<string, unknown>).entries : null;
  const entry = entries && typeof entries === 'object' ? (entries as Record<string, unknown>)[key] : null;
  const value = entry && typeof entry === 'object' ? (entry as Record<string, unknown>).value : null;
  return typeof value === 'string' ? value.trim() : '';
}

function userFacingName(name?: string | null) {
  const value = String(name ?? '').trim();
  if (!value || /^(vi\d+|test|user_?\d+|[\w-]{20,})/i.test(value) || /[_-].*\d/.test(value)) return '柿同学';
  return value.slice(0, 18);
}

function routeForSearchResult(item: SearchResultItem) {
  const type = [String(item.type ?? ''), String(item.resource_type ?? '')].join(' ').toLowerCase();
  const id = item.id == null ? '' : encodeURIComponent(String(item.id));
  if (id && (type.includes('artifact') || type.includes('lecture') || type.includes('ppt') || type.includes('mindmap') || type.includes('code'))) return '/artifacts/' + id;
  if (type.includes('material') || type.includes('resource')) return '/resources';
  if (type.includes('exam') || type.includes('question')) return '/exam';
  if (type.includes('note')) return '/notes';
  if (type.includes('video')) return '/video';
  if (type.includes('artifact') || type.includes('ppt')) return '/artifacts';
  if (type.includes('tutor') || type.includes('chat')) return '/ai-learning';
  return '/learning';
}

function ImpressionConstellation({ nodes }: { nodes: ImpressionNode[] }) {
  // 16:9 动态理解窗口：一次只显示一句，上一句完全消失后下一句才出现；
  // 每句出现的位置带轻微随机偏移，形成"句子自然浮现"的呼吸感。
  const main = "柿小吉正在从你的每一次学习里，慢慢认识你。";
  const tags = [
    "慢慢来，也是一种节奏",
    "每个问题，都值得认真对待",
    "今天的积累，会连成明天的路",
    "按自己的方式前进就好",
    "愿意开始，就值得被看见",
    "理解，会一点点长出来"
  ];
  const caption = "每一次搜索、提问和学习成果，都会让柿小吉更懂你。";
  const phrases = useMemo(() => {
    const base = nodes.length ? nodes.map((node) => node.label) : [];
    const pool = [main, ...base, ...tags, caption];
    return [...new Set(pool.filter(Boolean))];
  }, [caption, main, nodes, tags]);

  const [phraseIndex, setPhraseIndex] = useState(0);
  const [visible, setVisible] = useState(true);
  const [drift, setDrift] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  useEffect(() => {
    if (phrases.length < 2) return;
    let timer: number | undefined;
    const cycle = () => {
      // 1. 上一句淡出
      setVisible(false);
      timer = window.setTimeout(() => {
        // 2. 换下一句，随机浮动位置
        setPhraseIndex((index) => (index + 1) % phrases.length);
        setDrift({
          x: Math.round((Math.random() - 0.5) * 22),
          y: Math.round((Math.random() - 0.5) * 10),
        });
        // 3. 下一句淡入
        setVisible(true);
        timer = window.setTimeout(cycle, 3400);
      }, 650);
    };
    timer = window.setTimeout(cycle, 2600);
    return () => { if (timer) window.clearTimeout(timer); };
  }, [phrases.length]);

  const phrase = phrases[phraseIndex] || main;

  return <div className={styles.impressionWindow} data-testid="ai-impression-window" aria-label="柿小吉的理解标签" style={{ backgroundImage: `url(${impressionWindowReeds})` }}>
    <div className={styles.impressionWindowHaze} />
    <p className={`${styles.impressionWindowPhrase} ${visible ? styles.phraseVisible : styles.phraseHidden}`}
      style={{ '--drift-x': `${drift.x}px`, '--drift-y': `${drift.y}px` } as React.CSSProperties}>{phrase}</p>
  </div>;
}

export function MainLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const { user, clearSession } = useAuthStore();
  // 统一背景：所有旧壳页面跟随当前皮肤（kaki 柿子天空 / ocean 海面），组件层面变化。
  const shellTheme = readDaryaTheme(user?.userId);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchValue, setSearchValue] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResultItem[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [notifications, setNotifications] = useState<DashboardNotification[]>([]);
  const [announcements, setAnnouncements] = useState<Array<{ id: number; title: string; content: string; is_read: boolean }>>([]);
  const [profile, setProfile] = useState<BackendProfile | null>(null);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [mobileMemoryOpen, setMobileMemoryOpen] = useState(false);
  const [mobileImpressionOpen, setMobileImpressionOpen] = useState(false);
  const [adventureActive, setAdventureActive] = useState(false);
  const [artifactPreviewOpen, setArtifactPreviewOpen] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);
  const noticeRef = useRef<HTMLDivElement>(null);
  const userRef = useRef<HTMLDivElement>(null);
  const conversation = location.pathname === '/ai-learning';
  const personal = location.pathname.startsWith('/profile');
  const workspaceKind = personal ? 'personal' : location.pathname.startsWith('/notes') ? 'notes' : location.pathname.startsWith('/exam') ? 'practice' : location.pathname.startsWith('/learning') ? 'learning' : location.pathname.startsWith('/resources') ? 'materials' : location.pathname.startsWith('/artifacts') ? 'artifacts' : 'conversation';
  const workspaceLabel = useMemo(() => Object.entries(workspaceLabels).find(([path]) => location.pathname.startsWith(path))?.[1] ?? '学习空间', [location.pathname]);
  const displayName = userFacingName(user?.username);
  const goal = profileValue(profile, 'learning_goal');
  const preference = profileValue(profile, 'learning_preference');
  const rhythm = profileValue(profile, 'study_rhythm');
  const baseline = profileValue(profile, 'knowledge_baseline');
  const nodes = useMemo<ImpressionNode[]>(() => [
    preference && { label: preference, tone: 'warm' as const }, rhythm && { label: rhythm, tone: 'calm' as const },
    baseline && { label: baseline, tone: 'focus' as const }, profile?.major && { label: profile.major, tone: 'soft' as const },
    profile?.target_role && { label: profile.target_role, tone: 'focus' as const },
  ].filter(Boolean) as ImpressionNode[], [baseline, preference, profile?.major, profile?.target_role, rhythm]);
  const unreadCount = notifications.filter((item) => !item.is_read).length + announcements.filter((item) => !item.is_read).length;

  useEffect(() => { if (user?.userId) getData<BackendProfile | null>(`/api/profile/${user.userId}`).then(setProfile).catch(() => setProfile(null)); }, [user?.userId]);
  // 学习会话计时生命周期：登录进入学习空间即开始，刷新/重开自动恢复；
  // 退出登录或 401 会话失效时把本轮时长结算进累计学习时间。
  const userIdRef = useRef<number | null>(null);
  useEffect(() => {
    const prev = userIdRef.current;
    const next = user?.userId ?? null;
    userIdRef.current = next;
    if (prev && prev !== next) endStudySession(prev);
    if (next) ensureStudySessionStarted(next);
  }, [user?.userId]);
  useEffect(() => { const open = () => setToolsOpen(true); window.addEventListener('kaki:open-learning-tools', open); return () => window.removeEventListener('kaki:open-learning-tools', open); }, []);
  useEffect(() => {
    const onPreviewState = (event: Event) => setArtifactPreviewOpen(Boolean((event as CustomEvent<{ open?: boolean }>).detail?.open));
    window.addEventListener('kaki:artifact-preview-state', onPreviewState);
    return () => { window.removeEventListener('kaki:artifact-preview-state', onPreviewState); setArtifactPreviewOpen(false); };
  }, []);
  useEffect(() => {
    const onAdventureState = (event: Event) => setAdventureActive(Boolean((event as CustomEvent<{ active?: boolean }>).detail?.active));
    window.addEventListener('kaki:adventure-state', onAdventureState);
    return () => { window.removeEventListener('kaki:adventure-state', onAdventureState); setAdventureActive(false); };
  }, []);
  useEffect(() => { setMobileMemoryOpen(false); setMobileImpressionOpen(false); setSearchOpen(false); setNotificationsOpen(false); setUserMenuOpen(false); }, [location.pathname]);
  useEffect(() => {
    const close = (event: MouseEvent) => { const target = event.target as Node; if (!searchRef.current?.contains(target)) setSearchOpen(false); if (!noticeRef.current?.contains(target)) setNotificationsOpen(false); if (!userRef.current?.contains(target)) setUserMenuOpen(false); };
    document.addEventListener('mousedown', close); return () => document.removeEventListener('mousedown', close);
  }, []);

  const runSearch = async () => {
    const query = searchValue.trim(); if (!query) return;
    setSearchLoading(true);
    try {
      const [semantic, generated] = await Promise.all([moduleApi.search(query, 8), moduleApi.generatedItems({ q: query })]);
      setSearchResults([...semantic, ...generated.map((item) => ({ id: item.id, title: item.title, type: item.type, resource_type: 'artifact', content: item.chapter || item.content }))].slice(0, 10));
    }
    catch { message.error('搜索暂时不可用，请稍后再试'); }
    finally { setSearchLoading(false); }
  };
  const openNotifications = async () => {
    setNotificationsOpen((value) => !value); setUserMenuOpen(false);
    try { const [official, active] = await Promise.all([moduleApi.notifications(), userAnnouncementApi.getActive()]); setNotifications(official); setAnnouncements(active.items); } catch { setNotifications([]); setAnnouncements([]); }
  };
  const openTool = (tool: Tool) => {
    setToolsOpen(false);
    // 拾光漫游 · 新冒险演示：输入主题 → LLM 智能生成剧情/题目 → 四阶段闭环（8901 game_agent）
    // 同标签跳转，保证任何环境（含内嵌浏览器）点“探索”都能进入游戏。
    if (tool.adventure) { window.location.href = '/ai-workspace/shiguang-demo.html'; return; }
    if (tool.target) navigate(tool.target);
  };
  const logout = () => {
    if (user?.userId) endStudySession(user.userId);
    clearSession();
    navigate('/login', { replace: true });
  };
  const impression = <aside className={styles.impressionRail} data-testid="student-shell-right-rail" aria-label="柿小吉当前认识">
    <button type="button" className={styles.identity} onClick={() => navigate('/profile')}><UserAvatar size={48} /><span><strong>{displayName}</strong><small>个人中心</small></span><UserRound size={16} /></button>
    <section className={styles.impressionCard}><span className={styles.eyebrow}><Sparkles size={14} />柿小吉眼中的你</span><ImpressionConstellation nodes={nodes} /><strong>{preference ? `你更喜欢「${preference}」` : '柿小吉正在从真实学习里认识你'}</strong><p>{goal ? `现在最重要的目标：${goal}` : '一次对话、一次练习和一条笔记，都会让这里更贴近你。'}</p><div className={styles.observations}>{nodes.slice(0, 3).map((node) => <span key={node.label}>{node.label}</span>)}</div><button type="button" onClick={() => navigate('/profile')}>进入个人中心</button></section>
    <StudyTime userId={user?.userId} />
  </aside>;

  return <div className={`${styles.shell} ${adventureActive ? styles.adventureActive : ''}`} data-shell-theme={shellTheme} data-testid="student-shell" data-shell-mode={workspaceKind} data-mobile-memory={mobileMemoryOpen} data-mobile-impression={mobileImpressionOpen} data-adventure-active={adventureActive}>
    <header className={styles.topline} data-testid="global-topbar"><button className={styles.brand} type="button" onClick={() => navigate('/ai-learning')} aria-label="回到 AI 学习对话"><span className={styles.persimmon} style={{ backgroundImage: `url(${persimmonLogo})`, backgroundSize: 'contain', backgroundRepeat: 'no-repeat', backgroundPosition: 'center' }} /><strong>柿子智学</strong><small>AI 学习空间</small></button>
      {!conversation && <div className={styles.trail} data-testid="workspace-return-trail"><ChevronLeft size={16} /><button type="button" onClick={() => navigate('/ai-learning')}>回到对话</button><span>·</span><strong>{workspaceLabel}</strong></div>}
      <div className={styles.topActions}>
        <div className={styles.topPopover} ref={searchRef}><button type="button" className={styles.topIcon} data-testid="global-search-button" aria-label="搜索" onClick={() => { setSearchOpen((open) => !open); setNotificationsOpen(false); setUserMenuOpen(false); }}><Search size={18} /></button>{searchOpen && <section className={styles.searchPanel}><Input autoFocus value={searchValue} onChange={(event) => setSearchValue(event.target.value)} onPressEnter={() => void runSearch()} placeholder="搜索资料、笔记、题目…" suffix={<Search size={15} />} /><div className={styles.searchResults}>{searchLoading && <small>搜索中…</small>}{searchResults.map((item, index) => <button type="button" key={`${item.id ?? index}-${item.title ?? item.name}`} onClick={() => { navigate(routeForSearchResult(item)); setSearchOpen(false); }}><strong>{item.title || item.name || '学习内容'}</strong><small>{item.content || item.snippet || item.type || '打开查看'}</small></button>)}{!searchLoading && searchValue && !searchResults.length && <small>暂无匹配结果</small>}</div></section>}</div>
        <div className={styles.topPopover} ref={noticeRef}><button type="button" className={styles.topIcon} data-testid="global-message-button" aria-label="通知" onClick={() => void openNotifications()}><Badge count={unreadCount} size="small"><Bell size={18} /></Badge></button>{notificationsOpen && <section className={styles.noticePanel}><strong>通知</strong>{[...announcements, ...notifications].map((item) => <button type="button" key={item.id} onClick={async () => { if ('target_route' in item) { try { await moduleApi.markNotificationRead(item.id); } catch { /* real route still opens */ } navigate(item.target_route || '/ai-learning'); } else { try { await userAnnouncementApi.markRead(item.id); } catch { /* view remains valid */ } navigate(`/announcements?open=${item.id}`); } setNotificationsOpen(false); }}><b>{item.is_read ? item.title : `未读 · ${item.title}`}</b><small>{item.content}</small></button>)}{!notifications.length && !announcements.length && <small className={styles.emptyNotice}>暂无新通知</small>}</section>}</div>
        <div className={styles.topPopover} ref={userRef}><button type="button" className={styles.userButton} data-testid="global-user-menu-button" onClick={() => { setUserMenuOpen((open) => !open); setSearchOpen(false); setNotificationsOpen(false); }}><UserAvatar size={30} /><span>{displayName}</span></button>{userMenuOpen && <section className={styles.userMenu}><button type="button" onClick={() => navigate('/profile')}><UserRound size={15} />个人中心</button><button type="button" onClick={() => navigate('/profile?section=settings')}><Settings size={15} />设置</button><button type="button" data-testid="global-logout-button" onClick={logout}><LogOut size={15} />退出登录</button></section>}</div>
      </div>
    </header>
    <div className={`${styles.shellGrid} ${styles[workspaceKind]} ${conversation ? styles.conversation : ''} ${artifactPreviewOpen ? styles.artifactPreviewOpen : ''}`}>
      {conversation && <section className={styles.kernelStage} aria-label="AI 对话与会话记忆"><AiWorkspaceKernel presentation="stage" /></section>}
      {!conversation && <main className={styles.workspaceStage} data-testid="student-shell-workspace"><Outlet /></main>}
      {!personal && conversation && !artifactPreviewOpen && impression}
    </div>
    {conversation && <div className={styles.mobileRailControls}><button type="button" data-testid="mobile-memory-open" onClick={() => setMobileMemoryOpen(true)}>历史</button><button type="button" data-testid="mobile-impression-open" onClick={() => setMobileImpressionOpen(true)}>认识你</button></div>}
    {toolsOpen && <div className={styles.trayBackdrop} onMouseDown={() => setToolsOpen(false)}><section className={styles.toolTray} data-testid="learning-tool-tray" aria-label="学习工具" onMouseDown={(event) => event.stopPropagation()}><header><div><span>柿小吉的学习工具</span><strong>下一步想做什么？</strong></div><button type="button" onClick={() => setToolsOpen(false)} aria-label="收起学习工具"><X size={18} /></button></header><div className={styles.toolGroups}>{toolGroups.flatMap((group) => group.items).map((tool) => { const Icon = tool.icon; if (tool.adventure) { return <a key={tool.label} href="/ai-workspace/shiguang-demo.html" onClick={() => setToolsOpen(false)}><img src={tool.art} alt="" aria-hidden="true" /><span><strong>{tool.label}</strong><small>{tool.description}</small></span><Icon size={17} aria-hidden="true" /></a>; } return <button type="button" key={tool.label} onClick={() => openTool(tool)}><img src={tool.art} alt="" aria-hidden="true" /><span><strong>{tool.label}</strong><small>{tool.description}</small></span><Icon size={17} aria-hidden="true" /></button>; })}</div></section></div>}
  </div>;
}
