import type { GameActorId } from '../contracts';

export const ACTOR_ASSETS: Record<GameActorId, { name: string; src: string }> = {
  gu: { name: '顾遥', src: '/darya-game/shared/gu.png' },
  zhou: { name: '周野', src: '/darya-game/shared/zhou.png' },
  shen: { name: '沈知', src: '/darya-game/shared/shen.png' },
  player: { name: '你', src: '/darya-game/shared/player.png' },
  darya: { name: 'DARYA', src: '/darya-game/shared/darya.png' },
};

export interface StoryBeat {
  speaker: string;
  text: string;
}
