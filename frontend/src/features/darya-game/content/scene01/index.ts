import type { SceneDefinition } from '../../contracts';
import type { StoryBeat } from '../shared';

export const scene01Opening: StoryBeat[] = [
  { speaker: '顾遥', text: '光正好，再晚十分钟就没了。' },
  { speaker: '周野', text: '等你没电的时候别找我。' },
  { speaker: '沈知', text: '我没说是鬼故事。是校史。' },
  { speaker: '顾遥', text: '人齐了。进去以后都别走太散。' },
];

export const scene01Dialogue: Record<string, StoryBeat> = {
  OLD_SCHOOL_SIGN: { speaker: '沈知', text: '校牌换过一次。现在这块，是停用前留下的。' },
  IRON_GATE: { speaker: '周野', text: '锁早没了。门轴倒是还能撑。' },
  GU_YAO_CAMERA: { speaker: '顾遥', text: '刚才那段挺稳的。海面反光正好压在校门后面。' },
  DARYA: { speaker: 'DARYA', text: '风比刚才大了一点。你想先在外面看看也行。' },
  SHEN_ZHI_ARCHIVE: { speaker: '沈知', text: '这是第十三次疏散的记录。细节等进去以后再看。' },
  SECOND_FLOOR_WINDOW: { speaker: '', text: '二楼窗后没有人，只有海风带动旧窗框。' },
};

export const scene01: SceneDefinition = {
  id: 'scene01', nextSceneId: 'scene02', assetRoot: '/darya-game/scene01/', contentAvailable: true,
  title: '旧校门', subtitle: '蓝调时刻 · 停用校舍外',
  background: '/darya-game/scene01/scene01-school-gate.png',
  hotspots: [
    { id: 'OLD_SCHOOL_SIGN', x: '88%', y: '50%', label: '旧校牌' },
    { id: 'IRON_GATE', x: '74%', y: '59%', label: '铁门' },
    { id: 'GU_YAO_CAMERA', x: '23%', y: '47%', label: '顾遥的摄像机' },
    { id: 'DARYA', x: '64%', y: '55%', label: 'DARYA' },
    { id: 'SHEN_ZHI_ARCHIVE', x: '39%', y: '52%', label: '沈知的资料' },
    { id: 'SECOND_FLOOR_WINDOW', x: '76%', y: '23%', label: '二楼窗户' },
    { id: 'ENTER_SCHOOL', x: '70%', y: '68%', label: '进入学校', core: true },
  ],
  canTransition: (state) => Boolean(state.story_state.scene01ReadyToEnter),
  derivePresentation: (state) => ({
    sceneId: 'scene01', available: true, assetRoot: '/darya-game/scene01/',
    title: '旧校门', subtitle: '蓝调时刻 · 停用校舍外',
    background: '/darya-game/scene01/scene01-school-gate.png',
    actors: state.story_state.openingCompleted ? [
      { id: 'gu', left: '22%', height: '82%', depth: 'back' },
      { id: 'darya', left: '65%', height: '84%', depth: 'mid' },
    ] : [],
  }),
};
