export type MascotMotionState =
  | 'hidden'
  | 'appearing'
  | 'idle'
  | 'blinking'
  | 'looking'
  | 'waving'
  | 'thinking'
  | 'listening'
  | 'speaking'
  | 'encouraging'
  | 'reacting'
  | 'dragging'
  | 'snapping'
  | 'celebrating'
  | 'resting'
  | 'disappearing';

export type MascotEmotion =
  | 'calm'
  | 'curious'
  | 'thinking'
  | 'encouraging'
  | 'celebrating'
  | 'resting';

export type MascotEdge = 'left' | 'right';

export interface MascotPositionPreference {
  preferredEdge: MascotEdge;
  normalizedY: number;
  lastSafePosition: { x: number; y: number };
  manuallyMoved: boolean;
}

export interface MascotRouteConfig {
  routePattern: RegExp;
  pageKey: string;
  entryAction: Extract<MascotMotionState, 'idle' | 'looking' | 'waving'>;
  attentionTarget?: string;
  cooldown: number;
}

export interface MascotEvent {
  type: string;
  importance?: 'normal' | 'important' | 'milestone';
  message?: string;
  source?: string;
}

export interface MascotDirective {
  state: MascotMotionState;
  emotion: MascotEmotion;
  message?: string;
  durationMs: number;
  priority: number;
  asset: 'daily' | 'celebration';
}

export interface StoredMascotPreference extends MascotPositionPreference {
  quietUntil: number;
  hiddenByUser: boolean;
}
