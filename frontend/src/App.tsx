import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { lazy, Suspense, useEffect } from 'react';
import { adminRoutes } from './admin/adminRoutes';
import { routes } from './routes';
import { isAdminAuthed } from './shared/auth';
import { authApi } from './api/auth';
import { useAuthStore } from './stores/authStore';

const AdminLayout = lazy(() => import('./layouts/AdminLayout').then((module) => ({ default: module.AdminLayout })));
const MainLayout = lazy(() => import('./layouts/MainLayout').then((module) => ({ default: module.MainLayout })));
const AdminPage = lazy(() => import('./pages/AdminPage').then((module) => ({ default: module.AdminPage })));
const AuthPage = lazy(() => import('./pages/AuthPage').then((module) => ({ default: module.AuthPage })));
const ModulePage = lazy(() => import('./pages/ModulePage').then((module) => ({ default: module.ModulePage })));
const OnboardingPage = lazy(() => import('./pages/OnboardingPage').then((module) => ({ default: module.OnboardingPage })));
const AiLearningPage = lazy(() => import('./pages/AiLearningPage').then((module) => ({ default: module.AiLearningPage })));
const MyLearningPage = lazy(() => import('./pages/MyLearningPage').then((module) => ({ default: module.MyLearningPage })));
const ArtifactDetailPage = lazy(() => import('./pages/ArtifactDetailPage').then((module) => ({ default: module.ArtifactDetailPage })));
const NotesPage = lazy(() => import('./pages/NotesPage').then((module) => ({ default: module.NotesPage })));
const VideoPage = lazy(() => import('./pages/VideoPage').then((module) => ({ default: module.VideoPage })));
const AnnouncementsPage = lazy(() => import('./pages/AnnouncementsPage').then((module) => ({ default: module.AnnouncementsPage })));
const TidePreviewPage = lazy(() => import('./pages/TidePreviewPage').then((module) => ({ default: module.TidePreviewPage })));
// 黑板实验页：不经过登录、不依赖后端，用来验收黑板渲染层与给队友看效果
const BlackboardLab = lazy(() => import('./pages/BlackboardLab'));

function RouteState({ state = 'loading', title, description }: { state?: 'loading' | 'error'; title: string; description?: string }) {
  return (
    <div className="app-route-state" data-testid={`state-${state}`} role={state === 'error' ? 'alert' : 'status'}>
      <strong>{title}</strong>
      {description && <span>{description}</span>}
    </div>
  );
}

function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation();
  const { token, user, initialized } = useAuthStore();

  // Not logged in → redirect to login preserving original target
  if (!token) {
    const redirect = encodeURIComponent(`${location.pathname}${location.search}`);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }

  // Still loading auth state → show loading, don't redirect
  if (!initialized) {
    return <RouteState title="正在校验登录状态" />;
  }

  // Auth check complete. If user has no profile yet, go to onboarding.
  // But NEVER redirect if user is null (API error) — show error instead.
  if (!user) {
    return <RouteState state="error" title="认证状态异常" description="无法获取用户信息，请刷新页面重试" />;
  }

  // The standalone onboarding chat was retired: the main conversation owns
  // profiling. Register/login now marks the profile deferred server-side, so
  // this redirect only fires for genuinely in-progress legacy sessions.
  if (!user.hasProfile) {
    // Preserve current URL as redirect target for after onboarding
    const redirect = encodeURIComponent(`${location.pathname}${location.search}`);
    return <Navigate to={`/onboarding?redirect=${redirect}`} replace />;
  }

  return children;
}

function RequireAdmin({ children }: { children: ReactNode }) {
  if (!isAdminAuthed()) {
    return <Navigate to="/darya/login?mode=admin" replace />;
  }

  return children;
}

function RequireDaryaAuth({ children }: { children: ReactNode }) {
  const location = useLocation();
  const { token, user, initialized } = useAuthStore();
  const redirect = encodeURIComponent(`${location.pathname}${location.search}`);
  if (!token) return <Navigate to={`/darya/login?redirect=${redirect}`} replace />;
  if (!initialized) return <RouteState title="正在接回 DARYA" />;
  if (!user) return <RouteState state="error" title="认证状态异常" description="无法获取用户信息，请刷新页面重试" />;
  return children;
}

function AuthBootstrap({ children }: { children: ReactNode }) {
  const { token, initialized, setUser, setInitialized, clearSession } = useAuthStore();

  useEffect(() => {
    if (!token) {
      setInitialized(true);
      return;
    }

    if (initialized) return;

    let cancelled = false;
    authApi.currentUser()
      .then((user) => {
        if (!cancelled) setUser(user);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        // 只有服务端明确判定凭证无效才登出。后端重启、网络中断或超时属于暂时故障，
        // 此时清会话会把一次几十秒的抖动变成强制重新登录。
        const status = (reason as { response?: { status?: number } } | null)?.response?.status;
        if (status === 401 || status === 403) clearSession();
      })
      .finally(() => {
        if (!cancelled) setInitialized(true);
      });

    return () => {
      cancelled = true;
    };
  }, [clearSession, initialized, setInitialized, setUser, token]);

  return children;
}

export default function App() {
  return (
    <AuthBootstrap>
      <Suspense fallback={<RouteState title="正在打开" />}>
      <Routes>
        <Route path="/login" element={<Navigate to="/darya/login" replace />} />
        <Route path="/register" element={<Navigate to="/darya/register" replace />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/darya/login" element={<AuthPage mode="login" product="darya" />} />
        <Route path="/darya/register" element={<AuthPage mode="register" product="darya" />} />
        <Route path="/darya/onboarding" element={<RequireDaryaAuth><OnboardingPage product="darya" /></RequireDaryaAuth>} />
        <Route path="/admin/login" element={<Navigate to="/darya/login?mode=admin" replace />} />
        <Route path="/admin" element={<Navigate to="/admin/dashboard" replace />} />
        <Route path="/" element={<Navigate to="/darya" replace />} />
        {/* 黑板实验页：故意不进登录守卫，方便验收与给队友看效果 */}
        <Route path="/blackboard-lab" element={<Suspense fallback={<RouteState title="正在加载黑板" />}><BlackboardLab /></Suspense>} />
        <Route path="/darya" element={<RequireDaryaAuth><TidePreviewPage /></RequireDaryaAuth>} />
        <Route path="/darya/*" element={<Navigate to="/darya" replace />} />
        <Route path="/tide-preview" element={<Navigate to="/darya" replace />} />
        <Route element={<RequireAdmin><AdminLayout /></RequireAdmin>}>
          {adminRoutes.map((route) => (
            <Route key={route.path} path={route.path.slice(1)} element={<AdminPage route={route} />} />
          ))}
        </Route>
        <Route element={<RequireAuth><MainLayout /></RequireAuth>}>
          {routes
            .filter((route) => route.path !== '/')
            .map((route) => (
              route.path === '/ai-learning'
                ? <Route key={route.path} path="ai-learning" element={<AiLearningPage />} />
                : route.path === '/learning'
                ? <Route key={route.path} path="learning" element={<MyLearningPage />} />
                : route.path === '/notes'
                ? <Route key={route.path} path="notes" element={<NotesPage />} />
                : route.path === '/video'
                ? <Route key={route.path} path="video" element={<VideoPage />} />
                : <Route key={route.path} path={route.path.slice(1)} element={<ModulePage route={route} />} />
            ))}
          <Route path="qa" element={<Navigate to="/ai-learning" replace />} />
          <Route path="agents" element={<Navigate to="/ai-learning" replace />} />
          <Route path="courses/video" element={<Navigate to="/video" replace />} />
          <Route path="report" element={<Navigate to="/profile?section=insights" replace />} />
          <Route path="agent-records" element={<Navigate to="/admin/agent-records" replace />} />
          <Route path="artifacts/:artifactId" element={<ArtifactDetailPage />} />
          <Route path="announcements" element={<AnnouncementsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/darya" replace />} />
      </Routes>
      </Suspense>
    </AuthBootstrap>
  );
}
