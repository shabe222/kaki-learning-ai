import type { SceneDefinition } from '../../contracts';

export const scene04: SceneDefinition = {
  id: 'scene04', nextSceneId: 'scene05Ready', assetRoot: '/darya-game/shared/', contentAvailable: true,
  title: '旧广播室', subtitle: '纸面记录停在过去，跳线却不是原来的位置', background: null,
  hotspots: [
    { id: 'BROADCAST_CONSOLE', label: '旧播音台', x: '66%', y: '57%', core: true },
    { id: 'PATCH_PANEL', label: '跳线板', x: '76%', y: '30%', core: true },
    { id: 'ROUTE_LEDGER', label: '路由记录', x: '53%', y: '66%', core: true },
    { id: 'COAST_WINDOW', label: '临海窗', x: '18%', y: '27%' },
  ],
  canTransition: (state) => Boolean(state.story_state.scene05Ready),
  derivePresentation: () => ({
    sceneId: 'scene04', available: true, assetRoot: '/darya-game/shared/', title: '旧广播室',
    subtitle: '纸面记录停在过去，跳线却不是原来的位置', background: null,
    actors: [
      { id: 'gu', left: '8%', height: '74%', depth: 'front' },
      { id: 'zhou', left: '28%', height: '70%', depth: 'mid' },
      { id: 'shen', left: '42%', height: '72%', depth: 'front' },
      { id: 'darya', left: '0%', height: '62%', depth: 'back' },
    ],
  }),
};
