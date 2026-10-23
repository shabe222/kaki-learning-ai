import type {
  MascotDirective,
  MascotEvent,
  MascotMotionState,
  MascotRouteConfig
} from './mascotCompanionTypes';

export const MASCOT_ASSETS = {
  dailySprite: '/ai-workspace/assets/avatars/kaki-tutor-sprite-normalized.png',
  celebration: '/ai-workspace/assets/avatars/kaki-companion-celebration.png'
} as const;

export const MASCOT_FRAME_MANIFEST: Record<Exclude<MascotMotionState, 'hidden' | 'celebrating'>, readonly number[]> = {
  appearing: [0],
  idle: [0, 1],
  blinking: [2],
  looking: [9, 10],
  waving: [5, 15],
  thinking: [7, 8],
  listening: [9, 10],
  speaking: [3, 4, 5],
  encouraging: [13, 16],
  reacting: [13, 16],
  dragging: [12],
  snapping: [9],
  resting: [17],
  disappearing: [0]
};

export const MASCOT_ROUTE_REGISTRY: readonly MascotRouteConfig[] = [
  {
    routePattern: /^\/$/,
    pageKey: 'dashboard',
    entryAction: 'waving',
    attentionTarget: '[data-testid="dashboard-page"]',
    cooldown: 90_000
  },
  {
    routePattern: /^\/ai-learning/,
    pageKey: 'ai-learning',
    entryAction: 'looking',
    attentionTarget: '#input',
    cooldown: 100_000
  },
  {
    routePattern: /^\/learning/,
    pageKey: 'learning',
    entryAction: 'looking',
    cooldown: 110_000
  },
  {
    routePattern: /^\/notes/,
    pageKey: 'notes',
    entryAction: 'looking',
    cooldown: 100_000
  },
  {
    routePattern: /^\/exam/,
    pageKey: 'exam',
    entryAction: 'idle',
    cooldown: 120_000
  },
  {
    routePattern: /^\/profile/,
    pageKey: 'profile',
    entryAction: 'looking',
    cooldown: 120_000
  },
  {
    routePattern: /^\/resources/,
    pageKey: 'resources',
    entryAction: 'looking',
    cooldown: 120_000
  },
  {
    routePattern: /^\/artifacts/,
    pageKey: 'artifacts',
    entryAction: 'looking',
    cooldown: 120_000
  }
];

export function routeConfigFor(pathname: string) {
  return MASCOT_ROUTE_REGISTRY.find((entry) => entry.routePattern.test(pathname))
    ?? MASCOT_ROUTE_REGISTRY[0];
}

const eventDirectives: Record<string, Omit<MascotDirective, 'message'>> = {
  'chat.started': { state: 'thinking', emotion: 'thinking', durationMs: 30_000, priority: 50, asset: 'daily' },
  'chat.completed': { state: 'encouraging', emotion: 'encouraging', durationMs: 2_200, priority: 50, asset: 'daily' },
  'chat.failed': { state: 'reacting', emotion: 'calm', durationMs: 2_800, priority: 55, asset: 'daily' },
  'artifact.generation.started': { state: 'thinking', emotion: 'thinking', durationMs: 30_000, priority: 50, asset: 'daily' },
  'artifact.generation.completed': { state: 'encouraging', emotion: 'encouraging', durationMs: 2_400, priority: 55, asset: 'daily' },
  'artifact.generation.failed': { state: 'reacting', emotion: 'calm', durationMs: 2_800, priority: 55, asset: 'daily' },
  'note.saved': { state: 'reacting', emotion: 'encouraging', durationMs: 2_300, priority: 55, asset: 'daily' },
  'assessment.completed': { state: 'celebrating', emotion: 'celebrating', durationMs: 3_700, priority: 80, asset: 'celebration' },
  'adventure.completed': { state: 'celebrating', emotion: 'celebrating', durationMs: 3_900, priority: 80, asset: 'celebration' },
  'reward.collected': { state: 'celebrating', emotion: 'celebrating', durationMs: 3_700, priority: 80, asset: 'celebration' },
  'reward.equipped': { state: 'reacting', emotion: 'encouraging', durationMs: 2_300, priority: 55, asset: 'daily' }
};

export function directiveForEvent(event: MascotEvent): MascotDirective {
  const base = eventDirectives[event.type]
    ?? { state: 'reacting', emotion: 'curious', durationMs: 2_000, priority: 45, asset: 'daily' as const };
  // DeepSeek V4 Flash is not present in the verified provider registry. Event
  // reactions therefore remain motion-only; upstream subtitles must not be
  // relabeled as verified companion generation.
  return { ...base };
}

export function actionEventToMascotEvent(detail: {
  module?: string;
  action?: string;
  title?: string;
  status?: string;
}): MascotEvent | null {
  const action = String(detail.action || '');
  const module = String(detail.module || '');
  if (detail.status === 'error') {
    return {
      type: action.includes('agent') ? 'artifact.generation.failed' : 'chat.failed',
      source: 'action-store'
    };
  }
  if (detail.status !== 'success' && detail.status !== 'local-fallback') return null;
  if (module === 'notes' || action.includes('note') || action.includes('save')) {
    return { type: 'note.saved', source: 'action-store' };
  }
  if (module === 'exam' || action.includes('exam') || action.includes('assessment')) {
    return { type: 'assessment.completed', importance: 'milestone', source: 'action-store' };
  }
  if (module === 'agents' || action.includes('agent') || action.includes('generate')) {
    return { type: 'artifact.generation.completed', source: 'action-store' };
  }
  return null;
}

export function framePosition(frame: number) {
  const column = frame % 5;
  const row = Math.floor(frame / 5);
  // The normalized sprite is a strict 5 x 4 grid. Its leaf crown sits closer
  // to the head and paired eyes use the same proportions in every open frame.
  return {
    x: `${(column / 4 * 100).toFixed(4)}%`,
    y: `${(row / 3 * 100).toFixed(4)}%`
  };
}
