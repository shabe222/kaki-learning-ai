import type { GameSceneId, SceneDefinition } from '../contracts';
import { scene01 } from '../content/scene01';
import { scene02 } from '../content/scene02';
import { scene03 } from '../content/scene03';
import { scene04 } from '../content/scene04';

const scene04Ready: SceneDefinition = {
  id: 'scene04Ready',
  nextSceneId: 'scene04',
  assetRoot: '/darya-game/shared/',
  contentAvailable: true,
  title: '调查暂告一段落',
  subtitle: 'Scene 04 尚未开始',
  background: null,
  hotspots: [],
  canTransition: (state) => Boolean(state.story_state.scene04Ready),
  derivePresentation: () => ({
    sceneId: 'scene04Ready',
    available: true,
    assetRoot: '/darya-game/shared/',
    title: '调查暂告一段落',
    subtitle: 'Scene 04 尚未开始',
    background: null,
    actors: [],
  }),
};

const scene05Ready: SceneDefinition = {
  id: 'scene05Ready', nextSceneId: null, assetRoot: '/darya-game/shared/', contentAvailable: true,
  title: '下一段信号已经明确', subtitle: 'Scene 05 尚未开始', background: null, hotspots: [], canTransition: () => false,
  derivePresentation: () => ({ sceneId: 'scene05Ready', available: true, assetRoot: '/darya-game/shared/', title: '下一段信号已经明确', subtitle: 'Scene 05 尚未开始', background: null, actors: [] }),
};

export class SceneRegistry {
  private readonly scenes = new Map<GameSceneId, SceneDefinition>();

  constructor(definitions: SceneDefinition[] = [scene01, scene02, scene03, scene04Ready, scene04, scene05Ready]) {
    definitions.forEach((definition) => {
      if (this.scenes.has(definition.id)) throw new Error(`Duplicate scene id: ${definition.id}`);
      this.scenes.set(definition.id, definition);
    });
  }

  get(id: GameSceneId): SceneDefinition {
    const scene = this.scenes.get(id);
    if (!scene) throw new Error(`Unknown scene id: ${id}`);
    return scene;
  }

  list(): SceneDefinition[] {
    return [...this.scenes.values()];
  }
}

export const canonicalSceneRegistry = new SceneRegistry();
