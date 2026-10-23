import type { StoryBeat } from '../shared';

export const unpoweredBroadcastBeats: StoryBeat[] = [
  { speaker: '周野', text: '这个？' },
  { speaker: '顾遥', text: '还能响吗？' },
  { speaker: '周野', text: '等一下。' },
  { speaker: '周野', text: '没有电。' },
  { speaker: '', text: '走廊里的风声慢慢退下去。一声很轻的电流点击之后，旧喇叭响了。' },
  { speaker: '广播', text: '各班请准备确认人数。' },
  { speaker: '', text: '广播结束。DARYA 没有解释，只看着没有电的旧喇叭。' },
  { speaker: '顾遥', text: '……你刚才说没电？' },
  { speaker: '周野', text: '嗯。' },
];

export const scene02ExplorationDialogue: Record<string, StoryBeat> = {
  WALL_CLOCK: { speaker: '', text: '指针停在五点十二分。' },
  CLASSROOM_DOOR: { speaker: '', text: '门锁已经锈死，玻璃上只有旧灰。' },
  WINDOWS: { speaker: '', text: '海风从没关严的窗缝里钻进来。' },
  END_OF_HALLWAY: { speaker: '', text: '走廊尽头还能看见下一段楼梯。' },
  DARYA_S02: { speaker: 'DARYA', text: '这里比外面安静。' },
};
