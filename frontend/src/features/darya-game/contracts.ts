export type GameSceneId = 'scene01' | 'scene02' | 'scene03' | 'scene04Ready' | 'scene04' | 'scene05Ready';

export type GameActorId = 'gu' | 'zhou' | 'shen' | 'player' | 'darya';

export interface CanonicalStoryState {
  openingCompleted?: boolean;
  inspectedHotspots?: string[];
  scene01ReadyToEnter?: boolean;
  scene01Completed?: boolean;
  scene02Entered?: boolean;
  scene02InspectedHotspots?: string[];
  speakerInspected?: boolean;
  zhouPowerChecked?: boolean;
  unpoweredBroadcastTriggered?: boolean;
  unpoweredBroadcastStep?: number;
  unpoweredBroadcastCompleted?: boolean;
  cameraReviewTriggered?: boolean;
  cameraSequenceStep?: number;
  cameraTangJiSeen?: boolean;
  nakedEyePositionChecked?: boolean;
  cameraObservationConflictConfirmed?: boolean;
  scene03Entered?: boolean;
  scene03Hotspots?: string[];
  evacRosterFound?: boolean;
  evacRequired117Seen?: boolean;
  evacConfirmed116Seen?: boolean;
  temporaryVisitorRecordFound?: boolean;
  visitorFiveFourSeen?: boolean;
  historicalIdentityClueFound?: boolean;
  tangJiNameKnown?: boolean;
  cameraHistoricalLinkSuspected?: boolean;
  humanTruthFragment01?: boolean;
  systemTruthFragment01?: boolean;
  scene03CoreInvestigationCompleted?: boolean;
  scene04Ready?: boolean;
  scene04Entered?: boolean;
  scene04Hotspots?: string[];
  broadcastRoomInspected?: boolean;
  patchPanelInspected?: boolean;
  routeLedgerInspected?: boolean;
  routeConflictObserved?: boolean;
  daryaCognitiveMomentOpened?: boolean;
  daryaSupportDeferred?: boolean;
  daryaSupportReturned?: boolean;
  scene04InvestigationCompleted?: boolean;
  scene05Ready?: boolean;
}

export interface GameStateSnapshot {
  contract_version: 'darya_game_state_v1';
  content_authority_status: string;
  user_id: number;
  current_scene: GameSceneId;
  story_state: CanonicalStoryState;
  presentation_checkpoint: Record<string, unknown>;
  revision: number;
  schema_version: number;
  updated_at: string | null;
}

export interface GameStateUpdate {
  base_revision: number;
  current_scene: GameSceneId;
  story_state: Record<string, unknown>;
  presentation_checkpoint: Record<string, unknown>;
}

export interface ScenePresentation {
  sceneId: GameSceneId;
  available: boolean;
  assetRoot: string;
  title: string;
  subtitle: string;
  background: string | null;
  actors: GameActorPlacement[];
}

export interface GameActorPlacement {
  id: GameActorId;
  left: string;
  height: string;
  depth?: 'back' | 'mid' | 'front';
}

export interface SceneHotspot {
  id: string;
  label: string;
  x: string;
  y: string;
  core?: boolean;
}

export interface SceneDefinition {
  id: GameSceneId;
  nextSceneId: GameSceneId | null;
  assetRoot: string;
  contentAvailable: boolean;
  title: string;
  subtitle: string;
  background: string | null;
  hotspots: SceneHotspot[];
  canTransition: (state: GameStateSnapshot) => boolean;
  derivePresentation: (state: GameStateSnapshot) => ScenePresentation;
}

export interface DaryaGameCognitiveEvent {
  scene_id: GameSceneId;
  event_id: string;
  story_state_refs: string[];
  visible_clue_refs: string[];
  player_utterance?: string;
  player_choice?: string;
  cognitive_need_hint?: string;
}

export interface GameAction {
  type: string;
  hotspotId?: string;
  playerChoice?: string;
  playerUtterance?: string;
}
