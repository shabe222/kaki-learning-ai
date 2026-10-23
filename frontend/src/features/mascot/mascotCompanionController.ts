import type {
  MascotDirective,
  MascotEdge,
  MascotEvent,
  MascotPositionPreference,
  MascotRouteConfig,
  StoredMascotPreference
} from './mascotCompanionTypes';
import { directiveForEvent } from './mascotCompanionRegistry';

const STORAGE_PREFIX = 'kaki_mascot_companion_v1';
const DEFAULT_PREFERENCE: StoredMascotPreference = {
  // A new companion starts above the action area, outside the memory rail and
  // composer zone. Manually placed companions retain the learner's choice.
  preferredEdge: 'right',
  normalizedY: 0.58,
  lastSafePosition: { x: 0, y: 0 },
  manuallyMoved: false,
  quietUntil: 0,
  hiddenByUser: false
};

const GLOBAL_COOLDOWN = 2_800;
const EVENT_COOLDOWN = 8_000;
const DUPLICATE_TEXT_COOLDOWN = 10 * 60_000;
const SESSION_SPEECH_LIMIT = 14;
const CELEBRATION_COOLDOWN = 90_000;

function storageKey(userId: number | string) {
  return `${STORAGE_PREFIX}:${userId}`;
}

export class MascotPreferenceStore {
  static load(userId: number | string): StoredMascotPreference {
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey(userId)) || 'null') as Partial<StoredMascotPreference> | null;
      return {
        ...DEFAULT_PREFERENCE,
        ...parsed,
        lastSafePosition: parsed?.lastSafePosition ?? DEFAULT_PREFERENCE.lastSafePosition,
        normalizedY: Math.min(1, Math.max(0, Number(parsed?.normalizedY ?? DEFAULT_PREFERENCE.normalizedY)))
      };
    } catch {
      return { ...DEFAULT_PREFERENCE };
    }
  }

  static save(userId: number | string, preference: StoredMascotPreference) {
    try {
      localStorage.setItem(storageKey(userId), JSON.stringify(preference));
    } catch {
      // The live companion still works when browser storage is unavailable.
    }
  }

  static reset(userId: number | string) {
    try {
      localStorage.removeItem(storageKey(userId));
    } catch {
      // Logout must not be blocked by storage failures.
    }
  }
}

export class MascotCompanionController {
  private lastGlobalSpeech = 0;
  private lastCelebration = 0;
  private sessionSpeechCount = 0;
  private readonly routeTimes = new Map<string, number>();
  private readonly eventTimes = new Map<string, number>();
  private readonly textTimes = new Map<string, number>();

  routeDirective(config: MascotRouteConfig): MascotDirective | null {
    const now = Date.now();
    const lastRoute = this.routeTimes.get(config.pageKey) ?? 0;
    if (now - lastRoute < config.cooldown) return null;
    this.routeTimes.set(config.pageKey, now);
    return {
      state: config.entryAction,
      emotion: config.entryAction === 'looking' ? 'curious' : 'calm',
      durationMs: 1_600,
      priority: 35,
      asset: 'daily'
    };
  }

  eventDirective(event: MascotEvent): MascotDirective | null {
    const now = Date.now();
    const directive = directiveForEvent(event);
    const lastEvent = this.eventTimes.get(event.type) ?? 0;
    const isFailure = event.type.endsWith('.failed');
    const isMilestone = directive.asset === 'celebration';
    if (now - lastEvent < EVENT_COOLDOWN && !isFailure) return null;
    if (isMilestone && now - this.lastCelebration < CELEBRATION_COOLDOWN) {
      return {
        ...directive,
        state: 'reacting',
        emotion: 'encouraging',
        asset: 'daily',
        durationMs: 2_300,
        priority: 55
      };
    }
    if (directive.message && !isFailure && !isMilestone && !this.canSpeak(now)) {
      return { ...directive, message: undefined };
    }
    if (directive.message && !isFailure && !isMilestone && !this.canUseText(directive.message, now)) {
      return { ...directive, message: undefined };
    }
    this.eventTimes.set(event.type, now);
    if (isMilestone) this.lastCelebration = now;
    if (directive.message) this.markSpeech(directive.message, now);
    return directive;
  }

  private canSpeak(now: number) {
    return this.sessionSpeechCount < SESSION_SPEECH_LIMIT
      && now - this.lastGlobalSpeech >= GLOBAL_COOLDOWN;
  }

  private canUseText(text: string, now: number) {
    return now - (this.textTimes.get(text) ?? 0) >= DUPLICATE_TEXT_COOLDOWN;
  }

  private markSpeech(text: string, now: number) {
    this.sessionSpeechCount += 1;
    this.lastGlobalSpeech = now;
    this.textTimes.set(text, now);
  }
}

export function viewportPosition(
  preference: MascotPositionPreference,
  viewport: { width: number; height: number },
  size: { width: number; height: number }
) {
  const side = viewport.width <= 1400 ? 12 : 18;
  const top = viewport.width <= 900 ? 62 : 150;
  // Reserve the conversation composer and its primary action at the bottom.
  const bottom = viewport.width <= 900 ? 154 : 172;
  const maxY = Math.max(top, viewport.height - bottom - size.height);
  // A user-positioned companion retains its vertical preference. The default
  // desktop companion belongs low enough to stay clear of the profile rail.
  const normalizedY = preference.manuallyMoved ? preference.normalizedY : 0.78;
  const y = Math.round(top + normalizedY * (maxY - top));
  const x = preference.preferredEdge === 'left'
    ? side
    : Math.max(side, viewport.width - side - size.width);
  return { x, y: Math.min(maxY, Math.max(top, y)) };
}

export function snapPreference(
  position: { x: number; y: number },
  viewport: { width: number; height: number },
  size: { width: number; height: number }
): MascotPositionPreference {
  const edge: MascotEdge = position.x + size.width / 2 < viewport.width / 2 ? 'left' : 'right';
  const top = viewport.width <= 900 ? 62 : 72;
  const bottom = viewport.width <= 900 ? 154 : 172;
  const maxY = Math.max(top, viewport.height - bottom - size.height);
  const y = Math.min(maxY, Math.max(top, position.y));
  const normalizedY = maxY === top ? 0 : (y - top) / (maxY - top);
  return {
    preferredEdge: edge,
    normalizedY,
    lastSafePosition: position,
    manuallyMoved: true
  };
}

export function clampDragPosition(
  position: { x: number; y: number },
  viewport: { width: number; height: number },
  size: { width: number; height: number }
) {
  const side = 8;
  const top = viewport.width <= 900 ? 58 : 68;
  const bottom = 42;
  return {
    x: Math.min(viewport.width - size.width - side, Math.max(side, position.x)),
    y: Math.min(viewport.height - size.height - bottom, Math.max(top, position.y))
  };
}
