/**
 * 从一条对话消息里取出黑板脚本。
 *
 * 脚本由后端下发（解算好几何的 ops + 整块板），随这一轮的 conversation action 一起到达。
 * 同时兼容旧的 `[blackboard]…[/blackboard]` 文本块：迁移期老消息还能显示，
 * 但它只被当成纯文本处理，不再产生右栏 Sidecar 内容。
 */

import { useMemo } from 'react';
import type { BlackboardScript } from './types';

const BLACKBOARD_BLOCK = /\[blackboard\]([\s\S]*?)\[\/blackboard\]/gi;
const BLACKBOARD_XML = /<blackboard>([\s\S]*?)<\/blackboard>/gi;

type MaybeMoment = {
  blackboardScript?: BlackboardScript | null;
  conversationAction?: { blackboard_script?: BlackboardScript | null } | null;
  centerResponse?: string;
  userInput?: string;
} | null | undefined;

/** 只认结构化脚本。文本黑板块在迁移期仍会被剥离，但不再当脚本用。 */
export function useBlackboardScript(moment: MaybeMoment): BlackboardScript | null {
  return useMemo(() => {
    if (!moment) return null;
    const script = moment.conversationAction?.blackboard_script ?? moment.blackboardScript ?? null;
    if (!script || !script.ops) return null;
    return script;
  }, [moment]);
}

/** 旧的文本黑板块（迁移期兼容） */
export function extractLegacyBlackboardBlocks(text: string | undefined): string[] {
  if (!text) return [];
  const blocks: string[] = [];
  for (const re of [BLACKBOARD_BLOCK, BLACKBOARD_XML]) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) blocks.push(m[1].trim());
  }
  return blocks;
}

/** 把文本里的黑板块从正文里去掉（正文不再重复显示板上的东西） */
export function stripBlackboardBlocks(text: string | undefined): string {
  if (!text) return '';
  return text.replace(BLACKBOARD_BLOCK, '').replace(BLACKBOARD_XML, '').trim();
}
