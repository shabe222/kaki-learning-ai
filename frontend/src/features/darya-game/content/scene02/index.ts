import type { SceneDefinition } from '../../contracts';

export { cameraTangJiBeats } from './camera';
export { scene02ExplorationDialogue, unpoweredBroadcastBeats } from './broadcast';

export const scene02: SceneDefinition = {
  id: 'scene02', nextSceneId: 'scene03', assetRoot: '/darya-game/scene02/', contentAvailable: true,
  title: '长走廊', subtitle: '蓝调时刻 · 教学楼一层',
  background: '/darya-game/scene02/scene02-hallway.png',
  hotspots: [
    { id: 'OLD_SPEAKER', x: '62%', y: '24%', label: '旧广播喇叭', core: true },
    { id: 'WALL_CLOCK', x: '79%', y: '20%', label: '停住的钟' },
    { id: 'CLASSROOM_DOOR', x: '87%', y: '51%', label: '教室门' },
    { id: 'WINDOWS', x: '24%', y: '43%', label: '窗户' },
    { id: 'END_OF_HALLWAY', x: '57%', y: '54%', label: '走廊尽头' },
    { id: 'GU_YAO_CAMERA_S02', x: '22%', y: '55%', label: '顾遥的摄像机', core: true },
    { id: 'DARYA_S02', x: '46%', y: '57%', label: 'DARYA' },
    { id: 'SCENE03_DOOR', x: '90%', y: '60%', label: '前往旧教室', core: true },
  ],
  canTransition: (state) => Boolean(state.story_state.cameraObservationConflictConfirmed),
  derivePresentation: () => ({
    sceneId: 'scene02', available: true, assetRoot: '/darya-game/scene02/',
    title: '长走廊', subtitle: '蓝调时刻 · 教学楼一层',
    background: '/darya-game/scene02/scene02-hallway.png',
    actors: [
      { id: 'gu', left: '21%', height: '79%', depth: 'mid' },
      { id: 'darya', left: '46%', height: '83%', depth: 'mid' },
      { id: 'zhou', left: '68%', height: '88%', depth: 'mid' },
      { id: 'shen', left: '85%', height: '76%', depth: 'back' },
    ],
  }),
};
