import type { SceneDefinition } from '../../contracts';

export const scene03: SceneDefinition = {
  id: 'scene03', nextSceneId: 'scene04Ready', assetRoot: '/darya-game/scene03/', contentAvailable: true,
  title: '旧教室', subtitle: '蓝时 · 旧疏散确认点',
  background: '/darya-game/scene03/scene03-classroom.png',
  hotspots: [
    { id: 'BLACKBOARD', x: '28%', y: '29%', label: '黑板' },
    { id: 'TEACHER_DESK', x: '49%', y: '59%', label: '教师桌', core: true },
    { id: 'EVAC_ROSTER', x: '58%', y: '62%', label: '疏散记录', core: true },
    { id: 'ARCHIVE_DRAWER', x: '18%', y: '52%', label: '档案柜', core: true },
    { id: 'OLD_NOTICE', x: '10%', y: '37%', label: '旧公告' },
    { id: 'WINDOW', x: '79%', y: '33%', label: '窗边' },
    { id: 'SHEN_ZHI', x: '13%', y: '65%', label: '沈知' },
    { id: 'GU_YAO_CAMERA', x: '35%', y: '54%', label: '顾遥的摄像机', core: true },
    { id: 'DARYA', x: '58%', y: '55%', label: 'DARYA' },
  ],
  canTransition: (state) => Boolean(state.story_state.scene04Ready),
  derivePresentation: () => ({
    sceneId: 'scene03', available: true, assetRoot: '/darya-game/scene03/',
    title: '旧教室', subtitle: '蓝时 · 旧疏散确认点',
    background: '/darya-game/scene03/scene03-classroom.png',
    actors: [
      { id: 'shen', left: '18%', height: '77%', depth: 'mid' },
      { id: 'gu', left: '36%', height: '77%', depth: 'mid' },
      { id: 'darya', left: '61%', height: '81%', depth: 'mid' },
      { id: 'zhou', left: '82%', height: '84%', depth: 'mid' },
    ],
  }),
};
