import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { TideChatSurface } from './chat/TideChatSurface';
import { activeBlackboard } from './chat/activeBlackboard';
import { TideHistoryDrawer } from './history/TideHistoryDrawer';
import { TideContextRail } from './rail/TideContextRail';
import { TideThreadSidebar } from './sidebar/TideThreadSidebar';
import { SceneLayerSlot } from './scene/SceneLayerSlot';
import { useTideStore } from './state/tideStore';
import { useAuthStore } from '../../stores/authStore';
import { DARYA_COMPETITION_MARKER } from './buildMarker';
import { DaryaOceanSurface, type DaryaSecondarySurface } from './surfaces/DaryaOceanSurface';
import { persistDaryaTheme, readDaryaTheme, type DaryaTheme } from './theme/daryaTheme';
import { persistDaryaBoardLayout, readDaryaBoardLayout, type DaryaBoardLayout } from './theme/daryaBoardLayout';
import { persistDaryaSpaceMode, readDaryaSpaceMode, type DaryaSpaceMode } from './theme/daryaSpaceMode';
import { persistDaryaMascotFormSetting, readDaryaMascotFormSetting, resolveMascotForm, type DaryaMascotForm, type DaryaMascotFormSetting } from './theme/daryaMascotForm';
import './tideTokens.css';
import './tideTokens-wave01.css';
import styles from './TideShell.module.css';

export function TideShell() {
  const navigate = useNavigate();
  const hydrate = useTideStore((state) => state.hydrate);
  const abort = useTideStore((state) => state.abort);
  const threadId = useTideStore((state) => state.currentThreadId);
  const hasBlackboard = useTideStore((state) => Boolean(activeBlackboard(state.messages)));
  const loadState = useTideStore((state) => state.loadState);
  const clearSession = useAuthStore((state) => state.clearSession);
  const userId = useAuthStore((state) => state.user?.userId);
  const [surface, setSurface] = useState<DaryaSecondarySurface | null>(null);
  const [artifactReturn, setArtifactReturn] = useState<DaryaSecondarySurface | null>(null);
  const surfaceOpener = useRef<HTMLElement | null>(null);
  const primaryRef = useRef<HTMLDivElement | null>(null);
  const [theme, setTheme] = useState<DaryaTheme>(() => readDaryaTheme(userId));
  // 黑板在对话区里怎么排：设置里选，这里持有并下发 —— 对话区和设置页都吃这一份。
  const [boardLayout, setBoardLayout] = useState<DaryaBoardLayout>(() => readDaryaBoardLayout(userId));
  const [spaceMode, setSpaceMode] = useState<DaryaSpaceMode>(() => readDaryaSpaceMode(userId));
  // 数字人形态设置：auto=跟随主题；明确选定后固定。生效形态在这里解析。
  const [mascotFormSetting, setMascotFormSetting] = useState<DaryaMascotFormSetting>(() => readDaryaMascotFormSetting(userId));
  const mascotForm = resolveMascotForm(mascotFormSetting, theme);
  // 通话时黑板在语音面板里，而语音面板在主区里。主区一旦被二级界面收掉，
  // 黑板就没有地方显示了 —— 所以通话期间不许收。
  const [voiceCallActive, setVoiceCallActive] = useState(false);
  // 右侧「DARYA 当前工作」默认收起：它是"看一眼就走"的旁栏，常驻会把主区压窄，
  // 而主区里那块黑板是唯一需要装得下的东西。要看得点顶栏那颗按钮。
  const [workbenchOpen, setWorkbenchOpen] = useState(false);

  useEffect(() => {
    document.body.classList.add('tide-preview-active');
    void hydrate();
    return () => {
      document.body.classList.remove('tide-preview-active');
      abort();
    };
  }, [abort, hydrate]);

  useEffect(() => {
    setTheme(readDaryaTheme(userId));
    setBoardLayout(readDaryaBoardLayout(userId));
    setSpaceMode(readDaryaSpaceMode(userId));
    setMascotFormSetting(readDaryaMascotFormSetting(userId));
  }, [userId]);

  useEffect(() => {
    if (!threadId || loadState !== 'ready') return;
    setSurface(null);
    setArtifactReturn(null);
  }, [loadState, threadId]);

  const openPrimarySurface = (next: DaryaSecondarySurface) => {
    const focused = document.activeElement;
    surfaceOpener.current = focused instanceof HTMLElement ? focused : null;
    // Blur before the primary region becomes hidden or inert.
    surfaceOpener.current?.blur();
    setSurface(next);
  };

  useLayoutEffect(() => {
    if (primaryRef.current) primaryRef.current.inert = Boolean(surface) && !voiceCallActive;
    if (surface) {
      const panel = document.querySelector<HTMLElement>(
        '[data-testid="darya-artifact-surface"], [data-testid="darya-my-materials"], [data-testid="darya-self-understanding"], [data-testid="darya-settings"], [data-testid="darya-study-time-surface"]',
      );
      panel?.querySelector<HTMLElement>('button, [href], [tabindex]:not([tabindex="-1"])')?.focus();
    } else if (surfaceOpener.current?.isConnected) {
      surfaceOpener.current.focus();
      surfaceOpener.current = null;
    }
  }, [surface, voiceCallActive]);

  useEffect(() => {
    if (surface?.kind !== 'artifact') return;
    const keepFocusInside = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (artifactReturn) {
          setSurface(artifactReturn);
          setArtifactReturn(null);
        } else {
          setSurface(null);
        }
        return;
      }
      if (event.key !== 'Tab') return;
      const panel = document.querySelector<HTMLElement>('[data-testid="darya-artifact-surface"]');
      const focusable = [...(panel?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])') ?? [])]
        .filter((element) => element.getClientRects().length > 0);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', keepFocusInside);
    return () => document.removeEventListener('keydown', keepFocusInside);
  }, [artifactReturn, surface]);

  return (
    <div
      className={`tide-preview-root ${styles.shell}`}
      data-testid="tide-shell"
      data-darya-product={DARYA_COMPETITION_MARKER.product}
      data-darya-surface={DARYA_COMPETITION_MARKER.surface}
      data-darya-build={DARYA_COMPETITION_MARKER.build}
      data-darya-contract={DARYA_COMPETITION_MARKER.contract}
      data-darya-theme={theme}
      data-board-full={boardLayout === 'full' && hasBlackboard ? 'true' : 'false'}
      data-space-mode={spaceMode}
    >
      <SceneLayerSlot />
      <main className={styles.main}>
        <div
          ref={primaryRef}
          className={styles.primary}
          data-receded={Boolean(surface) && !voiceCallActive}
          data-voice-call={voiceCallActive ? 'active' : undefined}
          aria-hidden={surface && !voiceCallActive ? 'true' : undefined}
        >
          <TideThreadSidebar />
          <TideChatSurface
            onVoiceCallChange={setVoiceCallActive}
            workbenchOpen={workbenchOpen}
            onToggleWorkbench={() => setWorkbenchOpen((open) => !open)}
            boardLayout={boardLayout}
            spaceMode={spaceMode}
            onSpaceModeChange={(mode) => {
              persistDaryaSpaceMode(mode, userId);
              setSpaceMode(mode);
            }}
            onBoardLayoutChange={(layout) => {
              persistDaryaBoardLayout(layout, userId);
              setBoardLayout(layout);
            }}
            mascotForm={mascotForm}
            mascotFormSetting={mascotFormSetting}
            onMascotFormSettingChange={(setting) => {
              persistDaryaMascotFormSetting(setting, userId);
              setMascotFormSetting(setting);
            }}
            onOpenMaterials={() => openPrimarySurface({ kind: 'materials' })}
            onOpenUnderstanding={() => openPrimarySurface({ kind: 'understanding' })}
            onOpenSettings={() => openPrimarySurface({ kind: 'settings' })}
            onOpenTime={() => openPrimarySurface({ kind: 'time' })}
            onOpenArtifact={(artifactId, versionId) => {
              setArtifactReturn(null);
              openPrimarySurface({ kind: 'artifact', artifactId, versionId });
            }}
          />
          {workbenchOpen && (
            <TideContextRail
              onCollapse={() => setWorkbenchOpen(false)}
              onOpenUnderstanding={() => openPrimarySurface({ kind: 'understanding' })}
              onOpenArtifact={(artifactId) => {
                setArtifactReturn(null);
                openPrimarySurface({ kind: 'artifact', artifactId });
              }}
            />
          )}
        </div>
        {surface && (
          <DaryaOceanSurface
            surface={surface}
            threadId={threadId}
            theme={theme}
            onThemeChange={(nextTheme) => {
              persistDaryaTheme(nextTheme, userId);
              setTheme(nextTheme);
            }}
            boardLayout={boardLayout}
            spaceMode={spaceMode}
            onSpaceModeChange={(mode) => {
              persistDaryaSpaceMode(mode, userId);
              setSpaceMode(mode);
            }}
            onBoardLayoutChange={(layout) => {
              persistDaryaBoardLayout(layout, userId);
              setBoardLayout(layout);
            }}
            mascotFormSetting={mascotFormSetting}
            onMascotFormSettingChange={(setting) => {
              persistDaryaMascotFormSetting(setting, userId);
              setMascotFormSetting(setting);
            }}
            onLogout={() => {
              clearSession();
              navigate('/darya/login', { replace: true });
            }}
            onOpenUnderstanding={() => setSurface({ kind: 'understanding' })}
            onOpenArtifact={(artifactId, versionId, versionNumber) => {
              setArtifactReturn(surface);
              setSurface({ kind: 'artifact', artifactId, versionId, versionNumber });
            }}
            onBack={() => {
              if (surface.kind === 'artifact' && artifactReturn) {
                setSurface(artifactReturn);
                setArtifactReturn(null);
              } else {
                setSurface(null);
              }
            }}
          />
        )}
      </main>
      <TideHistoryDrawer />
    </div>
  );
}
