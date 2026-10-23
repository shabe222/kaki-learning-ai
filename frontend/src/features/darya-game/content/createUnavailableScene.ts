import type { GameSceneId, SceneDefinition } from '../contracts';

export function createUnavailableScene(
  id: Exclude<GameSceneId, 'scene04Ready'>,
  nextSceneId: GameSceneId,
): SceneDefinition {
  return {
    id,
    nextSceneId,
    assetRoot: `/darya-game/${id}/`,
    contentAvailable: false,
    title: id,
    subtitle: '内容不可用',
    background: null,
    hotspots: [],
    canTransition: () => false,
    derivePresentation: () => ({
      sceneId: id,
      available: false,
      assetRoot: `/darya-game/${id}/`,
      title: id,
      subtitle: '内容不可用',
      background: null,
      actors: [],
    }),
  };
}
