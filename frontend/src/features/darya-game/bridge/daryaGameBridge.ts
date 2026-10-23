import type { DaryaGameCognitiveEvent } from '../contracts';

const DORMANT_SCENES = new Set(['scene01', 'scene02', 'scene03']);

export type DaryaControlPlaneDispatch = (
  event: DaryaGameCognitiveEvent,
) => Promise<{ conversationActionRef?: string }>;

export function cognitiveEventToConversationPrompt(event: DaryaGameCognitiveEvent): string {
  const choice = event.player_choice ? `我选择了“${event.player_choice}”。` : '';
  const utterance = event.player_utterance?.trim() || '两份状态互相冲突时，我不知道该怎样判断哪一个仍然有效。';
  return [
    '我在故事里的旧广播室遇到了一个判断难点：纸面路由记录和实际跳线状态对不上。这是故事情境，不需要查外部资料。',
    choice,
    utterance,
    '请结合你已经知道的当前学习目标，先用一句话点出关键，再给我一个让我自己动手判断的最小练习；不要替我运行或提交。',
  ].filter(Boolean).join('');
}

export function createDaryaGameBridge(dispatchToCanonicalConversation: DaryaControlPlaneDispatch) {
  return {
    async dispatch(event: DaryaGameCognitiveEvent) {
      if (DORMANT_SCENES.has(event.scene_id)) {
        return { status: 'dormant' as const };
      }
      const result = await dispatchToCanonicalConversation(event);
      return { status: 'dispatched' as const, ...result };
    },
  };
}
