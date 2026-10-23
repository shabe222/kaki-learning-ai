import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  actionEventToMascotEvent,
  framePosition,
  MASCOT_ASSETS,
  MASCOT_FRAME_MANIFEST,
  routeConfigFor
} from './mascotCompanionRegistry';
import {
  clampDragPosition,
  MascotCompanionController,
  MascotPreferenceStore,
  snapPreference,
  viewportPosition
} from './mascotCompanionController';
import type {
  MascotDirective,
  MascotEdge,
  MascotEvent,
  MascotMotionState,
  StoredMascotPreference
} from './mascotCompanionTypes';
import styles from './MascotCompanionHost.module.css';
import { MascotInnerWorld } from './MascotInnerWorld';

type DigitalTutorDetail = {
  state?: string;
  previousState?: string;
  subtitle?: string;
};

type PositionedStyle = CSSProperties & {
  '--mascot-frame-x': string;
  '--mascot-frame-y': string;
};

function isInteractiveTarget(target: EventTarget | null) {
  return target instanceof HTMLElement && Boolean(target.closest('button, a, input, textarea, select'));
}

export function MascotCompanionHost({ userId }: { userId: number }) {
  const location = useLocation();
  const navigate = useNavigate();
  const controller = useMemo(() => new MascotCompanionController(), []);
  const rootRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef<MascotMotionState>('appearing');
  const positionRef = useRef({ x: 0, y: 0 });
  const dragRef = useRef<{
    pointerId: number;
    offsetX: number;
    offsetY: number;
    moved: boolean;
    frame: number | null;
    pending: { x: number; y: number };
  } | null>(null);
  const motionToken = useRef(0);
  const activePriority = useRef(0);
  const motionTimer = useRef<number | null>(null);
  const bubbleTimer = useRef<number | null>(null);
  const inputFocused = useRef(false);
  const [motionState, setMotionState] = useState<MascotMotionState>('appearing');
  const [frameIndex, setFrameIndex] = useState(0);
  const [edge, setEdge] = useState<MascotEdge>('right');
  const [bubble, setBubble] = useState('');
  const [controlsOpen, setControlsOpen] = useState(false);
  const [quiet, setQuiet] = useState(false);
  const [hiddenByUser, setHiddenByUser] = useState(false);
  const [hiddenInGame, setHiddenInGame] = useState(false);
  const [asset, setAsset] = useState<'daily' | 'celebration'>('daily');
  const [pageKey, setPageKey] = useState('dashboard');
  const [innerWorldOpen, setInnerWorldOpen] = useState(false);
  const preferenceRef = useRef<StoredMascotPreference>(MascotPreferenceStore.load(userId));

  const setState = useCallback((state: MascotMotionState) => {
    stateRef.current = state;
    setMotionState(state);
    setFrameIndex(0);
  }, []);

  const clearMotionTimer = useCallback(() => {
    if (motionTimer.current !== null) window.clearTimeout(motionTimer.current);
    motionTimer.current = null;
  }, []);

  const clearBubble = useCallback(() => {
    if (bubbleTimer.current !== null) window.clearTimeout(bubbleTimer.current);
    bubbleTimer.current = null;
    setBubble('');
  }, []);

  const showBubble = useCallback((message: string | undefined, durationMs: number) => {
    if (!message || quiet || inputFocused.current || hiddenInGame || hiddenByUser) return;
    clearBubble();
    setBubble(message);
    bubbleTimer.current = window.setTimeout(() => setBubble(''), Math.min(5_600, Math.max(2_400, durationMs)));
  }, [clearBubble, hiddenByUser, hiddenInGame, quiet]);

  const scheduleDirective = useCallback((directive: MascotDirective | null) => {
    if (!directive || hiddenInGame || hiddenByUser || quiet || directive.priority < activePriority.current) return;
    const token = ++motionToken.current;
    clearMotionTimer();
    activePriority.current = directive.priority;
    setAsset(directive.asset);
    setState(directive.state);
    if (directive.message) showBubble(directive.message, directive.durationMs);
    else clearBubble();
    motionTimer.current = window.setTimeout(() => {
      if (motionToken.current !== token) return;
      activePriority.current = 0;
      setAsset('daily');
      setState(document.hidden ? 'resting' : 'idle');
    }, directive.durationMs);
  }, [clearBubble, clearMotionTimer, hiddenByUser, hiddenInGame, quiet, setState, showBubble]);

  const applyPosition = useCallback((position: { x: number; y: number }) => {
    positionRef.current = position;
    const root = rootRef.current;
    if (root) root.style.transform = `translate3d(${Math.round(position.x)}px, ${Math.round(position.y)}px, 0)`;
  }, []);

  const currentSize = useCallback(() => {
    const root = rootRef.current;
    return {
      width: root?.offsetWidth || 120,
      height: root?.offsetHeight || 120
    };
  }, []);

  const restorePosition = useCallback(() => {
    const size = currentSize();
    const position = viewportPosition(
      preferenceRef.current,
      { width: window.innerWidth, height: window.innerHeight },
      size
    );
    applyPosition(position);
    setEdge(preferenceRef.current.preferredEdge);
  }, [applyPosition, currentSize]);

  const persistPreference = useCallback((next: Partial<StoredMascotPreference>) => {
    preferenceRef.current = { ...preferenceRef.current, ...next };
    MascotPreferenceStore.save(userId, preferenceRef.current);
  }, [userId]);

  const moveToEdge = useCallback((preferredEdge: MascotEdge) => {
    const next = { ...preferenceRef.current, preferredEdge, manuallyMoved: true };
    preferenceRef.current = next;
    setEdge(preferredEdge);
    setState('snapping');
    restorePosition();
    persistPreference(next);
    window.setTimeout(() => setState('idle'), 420);
  }, [persistPreference, restorePosition, setState]);

  const receiveEvent = useCallback((event: MascotEvent) => {
    scheduleDirective(controller.eventDirective(event));
  }, [controller, scheduleDirective]);

  useEffect(() => {
    const preference = MascotPreferenceStore.load(userId);
    preferenceRef.current = preference;
    setEdge(preference.preferredEdge);
    setQuiet(preference.quietUntil > Date.now());
    setHiddenByUser(preference.hiddenByUser);
    const frame = window.requestAnimationFrame(restorePosition);
    const appearTimer = window.setTimeout(() => {
      if (stateRef.current === 'appearing') setState('idle');
    }, 720);
    const resize = () => restorePosition();
    window.addEventListener('resize', resize);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(appearTimer);
      window.removeEventListener('resize', resize);
    };
  }, [restorePosition, setState, userId]);

  useEffect(() => {
    const image = new Image();
    image.src = MASCOT_ASSETS.celebration;
  }, []);

  useEffect(() => {
    const config = routeConfigFor(location.pathname);
    setPageKey(config.pageKey);
    const timer = window.setTimeout(() => scheduleDirective(controller.routeDirective(config)), 650);
    return () => window.clearTimeout(timer);
  }, [controller, location.pathname, scheduleDirective]);

  useEffect(() => {
    if (asset === 'celebration' || motionState === 'hidden') return;
    const frames = MASCOT_FRAME_MANIFEST[motionState as keyof typeof MASCOT_FRAME_MANIFEST] ?? [0];
    if (frames.length < 2 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const interval = window.setInterval(
      () => setFrameIndex((current) => (current + 1) % frames.length),
      motionState === 'speaking' ? 180 : 760
    );
    return () => window.clearInterval(interval);
  }, [asset, motionState]);

  useEffect(() => {
    let timer = 0;
    const scheduleLife = () => {
      timer = window.setTimeout(() => {
        if (stateRef.current === 'idle' && !quiet && !hiddenInGame && !hiddenByUser) {
          const next: MascotMotionState = Math.random() > 0.42 ? 'blinking' : 'looking';
          setState(next);
          window.setTimeout(() => {
            if (stateRef.current === next) setState('idle');
          }, next === 'blinking' ? 280 : 1_200);
        }
        scheduleLife();
      }, 4_800 + Math.random() * 5_600);
    };
    scheduleLife();
    return () => window.clearTimeout(timer);
  }, [hiddenByUser, hiddenInGame, quiet, setState]);

  useEffect(() => {
    const onDirectEvent = (event: Event) => receiveEvent((event as CustomEvent<MascotEvent>).detail || { type: 'unknown' });
    const onActionEvent = (event: Event) => {
      const mapped = actionEventToMascotEvent((event as CustomEvent).detail || {});
      if (mapped) receiveEvent(mapped);
    };
    const onTutorState = (event: Event) => {
      const detail = (event as CustomEvent<DigitalTutorDetail>).detail || {};
      if (detail.state === 'thinking') {
        receiveEvent({ type: 'chat.started' });
      } else if (detail.state === 'correction') {
        receiveEvent({ type: 'chat.failed' });
      } else if (detail.state === 'praise' || detail.state === 'complete') {
        receiveEvent({ type: 'chat.completed' });
      } else if (detail.state === 'idle' && ['thinking', 'speaking', 'teaching'].includes(detail.previousState || '')) {
        receiveEvent({ type: 'chat.completed' });
      } else if (detail.state === 'speaking') {
        scheduleDirective({
          state: 'speaking',
          emotion: 'encouraging',
          durationMs: 12_000,
          priority: 45,
          asset: 'daily'
        });
      }
    };
    const onAdventure = (event: Event) => {
      const detail = (event as CustomEvent<{ active?: boolean; completed?: boolean }>).detail || {};
      clearBubble();
      clearMotionTimer();
      activePriority.current = 100;
      setHiddenInGame(Boolean(detail.active));
      setState(detail.active ? 'hidden' : 'appearing');
      if (!detail.active) {
        activePriority.current = 0;
        window.setTimeout(() => setState('idle'), 620);
        if (detail.completed) window.setTimeout(() => receiveEvent({ type: 'adventure.completed', importance: 'milestone' }), 700);
      }
    };
    const onVoiceCall = (event: Event) => {
      const detail = (event as CustomEvent<{ active?: boolean }>).detail || {};
      // 数字人收口：通话期间由通话内的正面固定形象承接柿小吉，
      // 全局陪伴暂时退场，任何时刻只存在一个柿小吉。
      clearBubble();
      clearMotionTimer();
      activePriority.current = 100;
      setHiddenInGame(Boolean(detail.active));
      setState(detail.active ? 'hidden' : 'appearing');
      if (!detail.active) {
        activePriority.current = 0;
        window.setTimeout(() => setState('idle'), 620);
      }
    };
    const onReset = () => {
      MascotPreferenceStore.reset(userId);
      preferenceRef.current = MascotPreferenceStore.load(userId);
    };
    const onVisibility = () => {
      if (hiddenInGame || hiddenByUser) return;
      setState(document.hidden ? 'resting' : 'idle');
    };
    const onFocusIn = (event: FocusEvent) => {
      inputFocused.current = event.target instanceof HTMLElement
        && Boolean(event.target.closest('input, textarea, [contenteditable="true"], .ant-modal'));
      if (inputFocused.current) clearBubble();
    };
    const onFocusOut = () => {
      inputFocused.current = false;
    };
    window.addEventListener('kaki:mascot-event', onDirectEvent);
    window.addEventListener('kaki:digital-tutor-state', onTutorState);
    window.addEventListener('kaki:adventure-state', onAdventure);
    window.addEventListener('kaki:voice-call-state', onVoiceCall);
    window.addEventListener('kaki-action-event', onActionEvent);
    window.addEventListener('kaki:mascot-reset', onReset);
    document.addEventListener('visibilitychange', onVisibility);
    document.addEventListener('focusin', onFocusIn);
    document.addEventListener('focusout', onFocusOut);
    return () => {
      window.removeEventListener('kaki:mascot-event', onDirectEvent);
      window.removeEventListener('kaki:digital-tutor-state', onTutorState);
      window.removeEventListener('kaki:adventure-state', onAdventure);
      window.removeEventListener('kaki:voice-call-state', onVoiceCall);
      window.removeEventListener('kaki-action-event', onActionEvent);
      window.removeEventListener('kaki:mascot-reset', onReset);
      document.removeEventListener('visibilitychange', onVisibility);
      document.removeEventListener('focusin', onFocusIn);
      document.removeEventListener('focusout', onFocusOut);
    };
  }, [clearBubble, clearMotionTimer, hiddenByUser, hiddenInGame, receiveEvent, scheduleDirective, setState, userId]);

  useEffect(() => () => {
    clearMotionTimer();
    clearBubble();
    if (dragRef.current?.frame) window.cancelAnimationFrame(dragRef.current.frame);
  }, [clearBubble, clearMotionTimer]);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (innerWorldOpen || hiddenInGame || hiddenByUser || isInteractiveTarget(event.target)) return;
    const root = rootRef.current;
    if (!root) return;
    const box = root.getBoundingClientRect();
    root.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - box.left,
      offsetY: event.clientY - box.top,
      moved: false,
      frame: null,
      pending: positionRef.current
    };
    clearMotionTimer();
    clearBubble();
    activePriority.current = 90;
    setControlsOpen(false);
    setState('dragging');
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const next = clampDragPosition(
      { x: event.clientX - drag.offsetX, y: event.clientY - drag.offsetY },
      { width: window.innerWidth, height: window.innerHeight },
      currentSize()
    );
    drag.pending = next;
    drag.moved ||= Math.abs(next.x - positionRef.current.x) > 3 || Math.abs(next.y - positionRef.current.y) > 3;
    if (drag.frame !== null) return;
    drag.frame = window.requestAnimationFrame(() => {
      drag.frame = null;
      applyPosition(drag.pending);
    });
  };

  const finishDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (innerWorldOpen) return;
    const drag = dragRef.current;
    const root = rootRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !root) return;
    if (root.hasPointerCapture(event.pointerId)) root.releasePointerCapture(event.pointerId);
    if (drag.frame !== null) {
      window.cancelAnimationFrame(drag.frame);
      applyPosition(drag.pending);
    }
    dragRef.current = null;
    activePriority.current = 0;
    if (!drag.moved) {
      setInnerWorldOpen(true);
      setControlsOpen(false);
      setState('idle');
      return;
    }
    const snapped = snapPreference(
      positionRef.current,
      { width: window.innerWidth, height: window.innerHeight },
      currentSize()
    );
    const nextPreference: StoredMascotPreference = { ...preferenceRef.current, ...snapped };
    preferenceRef.current = nextPreference;
    setEdge(snapped.preferredEdge);
    setState('snapping');
    restorePosition();
    MascotPreferenceStore.save(userId, nextPreference);
    window.setTimeout(() => setState('idle'), 430);
  };

  const pause = () => {
    const quietUntil = Date.now() + 30 * 60_000;
    setQuiet(true);
    clearBubble();
    persistPreference({ quietUntil });
    setControlsOpen(false);
    setState('resting');
  };

  const resume = () => {
    setQuiet(false);
    persistPreference({ quietUntil: 0 });
    setControlsOpen(false);
    setState('appearing');
    window.setTimeout(() => setState('idle'), 520);
  };

  const hideTemporarily = () => {
    setHiddenByUser(true);
    clearBubble();
    persistPreference({ hiddenByUser: true });
    setState('hidden');
  };

  const restoreHidden = () => {
    setHiddenByUser(false);
    persistPreference({ hiddenByUser: false });
    setState('appearing');
    window.setTimeout(() => setState('idle'), 520);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setInnerWorldOpen(true);
      clearBubble();
    } else if (event.key === 'Escape') {
      setControlsOpen(false);
      clearBubble();
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      moveToEdge('left');
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      moveToEdge('right');
    }
  };

  const openRelax = () => window.dispatchEvent(new CustomEvent('kaki:open-relax'));
  const openCall = () => {
    const call = document.getElementById('callTool') as HTMLButtonElement | null;
    if (call) call.click();
    else {
      window.sessionStorage.setItem('kaki_open_call_v1', '1');
      navigate('/ai-learning');
    }
  };
  const onQuickActionPointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => event.stopPropagation();

  const frames = motionState === 'hidden'
    ? [0]
    : MASCOT_FRAME_MANIFEST[motionState as keyof typeof MASCOT_FRAME_MANIFEST] ?? [0];
  const frame = frames[frameIndex % frames.length] ?? 0;
  const framePositionValue = framePosition(frame);
  const positionedStyle: PositionedStyle = {
    '--mascot-frame-x': framePositionValue.x,
    '--mascot-frame-y': framePositionValue.y
  };
  const exposedState = hiddenInGame || hiddenByUser ? 'hidden' : motionState;

  return (
    <div
      ref={rootRef}
      className={[
        styles.host,
        hiddenInGame ? styles.gameHidden : '',
        hiddenByUser ? styles.userHidden : '',
        styles[`state_${motionState}`]
      ].filter(Boolean).join(' ')}
      style={positionedStyle}
      data-testid="mascot-companion"
      data-state={exposedState}
      data-page-key={pageKey}
      data-asset={asset === 'celebration' ? 'kaki-companion-celebration' : 'kaki-tutor-sprite'}
      data-edge={edge}
      data-quiet={quiet ? 'true' : 'false'}
      role="button"
      tabIndex={0}
      aria-label="柿小吉陪伴角色。点击进入轻松一下，按回车打开陪伴控制，方向键左右移动。"
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finishDrag}
      onPointerCancel={finishDrag}
      onDoubleClick={() => setControlsOpen(true)}
    >
      {hiddenByUser && !hiddenInGame ? (
        <button className={styles.restoreButton} type="button" onClick={restoreHidden}>
          唤回柿小吉
        </button>
      ) : (
        <>
          {innerWorldOpen && <MascotInnerWorld userId={userId} onClose={() => setInnerWorldOpen(false)} />}
          {bubble && !controlsOpen && (
            <div className={styles.bubble} data-testid="mascot-bubble" role="status" aria-live="polite">
              <span>{bubble}</span>
              <button type="button" aria-label="收起柿小吉气泡" onClick={clearBubble}>×</button>
            </div>
          )}
          <div className={styles.visual} data-testid="mascot-visual" aria-hidden="true">
            {asset === 'celebration' ? (
              <img
                src={MASCOT_ASSETS.celebration}
                alt=""
                draggable={false}
                onError={() => {
                  setAsset('daily');
                  setState('idle');
                }}
              />
            ) : (
              <div className={styles.spriteStage}>
                <div
                  key={frame}
                  className={styles.sprite}
                  data-testid="mascot-sprite-current"
                />
              </div>
            )}
          </div>
          <div className={styles.quickActions} data-testid="mascot-quick-actions" onPointerDown={(event) => event.stopPropagation()}>
            <button type="button" data-testid="mascot-relax-button" aria-label="轻松一下" title="轻松一下" onClick={openRelax}><i aria-hidden="true">✦</i>轻松一下</button>
            <button type="button" data-testid="mascot-call-button" aria-label="和我通话" title="和我通话" onClick={openCall}><i aria-hidden="true">◉</i>通话</button>
          </div>
          {controlsOpen && (
            <div className={styles.controls} aria-label="柿小吉陪伴控制">
              <button data-testid="mascot-move-left" type="button" onClick={() => moveToEdge('left')}>移到左侧</button>
              <button data-testid="mascot-move-right" type="button" onClick={() => moveToEdge('right')}>移到右侧</button>
              {quiet
                ? <button data-testid="mascot-resume" type="button" onClick={resume}>恢复陪伴</button>
                : <button data-testid="mascot-pause" type="button" onClick={pause}>安静半小时</button>}
              <button type="button" onClick={hideTemporarily}>暂时隐身</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
