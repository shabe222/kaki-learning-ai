import type { TideMessage } from '../tideTypes';

// A brief pause or a request to inspect the current board does not end its scene.
const TOPIC_SWITCH = /^\s*(?:我们\s*)?(?:现在|接下来)?\s*(?:先\s*)?(?:换个话题|转到另一个话题|聊点别的)(?=[，,。！？：:\s]|$)/;
const KEEP_BOARD = /(?:保留|沿用|继续用|仍用|对照|参照|继续看).{0,12}(?:黑板|板书)|(?:黑板|板书).{0,12}(?:保留|沿用|继续用|仍用|对照|参照)/;

/** Keep a board through follow-up questions, but release it on an explicit topic switch. */
export function activeBlackboard(messages: TideMessage[]) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === 'user' && TOPIC_SWITCH.test(message.content) && !KEEP_BOARD.test(message.content)) return null;
    if (message.role !== 'assistant') continue;
    const script = message.conversationAction?.blackboard_script;
    if (script?.ops?.length) return { script, ownerId: message.id };
  }
  return null;
}
