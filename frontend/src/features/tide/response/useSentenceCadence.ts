import { useEffect, useMemo, useRef, useState } from 'react';
import { prefersReducedMotion } from '../../../shared/motion';

/**
 * 让回答一句一句说出来，而不是一股脑倒在屏幕上。
 *
 * 屏幕上同时只有一句在讲：新的一句出现的时候，上一句还在淡出，交接处不空一拍。
 * 眼睛因此只需要跟着一处走，不用在一整屏刚冒出来的字里找"它现在说到哪了"。
 *
 * 三条纪律：
 * 1. 说完才落定。回答经常是最后一下整段到达的，如果一到就铺开，等于什么都没说 ——
 *    轮次结束以后要把剩下的几句按同样的节奏讲完，再整段落定。落定之后才是可以回头
 *    重读的正文，所以这个尾巴是有上限的：最多再讲三句（TAIL_UNITS），不能让人为了
 *    拿回可读的答案干等。
 * 2. 只在一轮真的在浏览器里长出来过的时候这样播。翻历史翻回来的消息一进来就是全文，
 *    把上一轮重新演一遍，那不是回放，那是假直播。
 * 3. 用户说了减少动态效果，就整段直接给。
 */

/** 一句停留多久：按字数算，两头封住。比黑板快 —— 黑板上那句话还要照着画。 */
const PER_CHARACTER_MS = 70;
const MIN_DWELL_MS = 900;
const MAX_DWELL_MS = 2400;
/** 收尾最多再讲几句，剩下的直接落定 */
const TAIL_UNITS = 3;
/** 上一句淡出所用的时间，与 CSS 里的 tideSentenceOut 一致 */
const HANDOFF_MS = 420;

/** 这些标记说明这一段是"块"（列表、表格、代码、引用、标题），不是一句口语 */
const STRUCTURED = /^\s*(```|~~~|#{1,6}\s|>|[-*+]\s|\d+[.)]\s|\|)/m;
/**
 * 一句话 = 直到真正的句读符，再加上紧跟其后的收尾符号。
 *
 * 句读符必须是。！？；!?;…，**不能是收尾符号本身**：写成"看到右括号/右引号就断句"
 * 会把「网络变好了」，而是它自己补的…… 切成两半 —— 屏幕上就冒出半句从逗号开始的
 * 话，读起来像掉字。
 */
const SENTENCE_SPLIT = /[^。！？!?；;…\n]+[。！？!?；;…]+[」』"”'’）)】]*|[^。！？!?；;…\n]+$/g;

function dwellFor(unit: string): number {
  return Math.min(MAX_DWELL_MS, Math.max(MIN_DWELL_MS, unit.length * PER_CHARACTER_MS));
}

/**
 * 把回答切成"说出来的单位"。
 *
 * 先按空行分段，一段如果不是块结构，再按句号切开 —— 按句号切会毁掉 markdown，
 * 一张表被切成三行就不再是表了。所以块整体当一个单位，跟着长出来，不做句读。
 */
export function splitNarrationUnits(text: string): string[] {
  const blocks = String(text ?? '')
    .split(/\n\s*\n/)
    .map((block) => block.trim())
    .filter(Boolean);
  const units: string[] = [];
  for (const block of blocks) {
    if (block.includes('\n') || STRUCTURED.test(block)) {
      units.push(block);
      continue;
    }
    const sentences = (block.match(SENTENCE_SPLIT) ?? [])
      .map((sentence) => sentence.trim())
      .filter(Boolean);
    units.push(...(sentences.length ? sentences : [block]));
  }
  return units;
}

export type SentenceCadence = {
  /** 此刻该显示的那一句；讲完了就是全文 */
  visible: string;
  /** 刚被顶掉的那一句，还在淡出 */
  leaving: string | null;
  /** 是不是正在一句一句地说 */
  narrating: boolean;
};

export function useSentenceCadence(
  text: string,
  { enabled = true, concluded = false }: { enabled?: boolean; concluded?: boolean } = {},
): SentenceCadence {
  const full = text ?? '';
  const units = useMemo(() => splitNarrationUnits(full), [full]);
  const narrating = enabled && units.length > 1 && !prefersReducedMotion();

  const [index, setIndex] = useState(0);
  const [leaving, setLeaving] = useState<string | null>(null);
  const [settled, setSettled] = useState(false);
  const indexRef = useRef(0);
  const previousFull = useRef(full);
  const previousCount = useRef(units.length);

  // 内容被整段换掉（而不是接着往下长）就重新从第一句说起。
  useEffect(() => {
    const beforeFull = previousFull.current;
    const beforeCount = previousCount.current;
    previousFull.current = full;
    previousCount.current = units.length;
    const replaced = !full.startsWith(beforeFull.slice(0, Math.min(beforeFull.length, 24)))
      || units.length < beforeCount;
    if (!replaced) return;
    indexRef.current = 0;
    setIndex(0);
    setLeaving(null);
    setSettled(false);
  }, [full, units.length]);

  useEffect(() => {
    if (!narrating || settled) return;
    const at = Math.min(indexRef.current, units.length - 1);
    const timer = window.setTimeout(() => {
      const next = indexRef.current + 1;
      if (next < units.length) {
        // 收尾：剩下的太多就只讲最后几句，别让人为了拿回可读的答案干等
        const step = concluded && units.length - next > TAIL_UNITS ? units.length - TAIL_UNITS : next;
        setLeaving(units[indexRef.current] ?? null);
        indexRef.current = step;
        setIndex(step);
        return;
      }
      // 还在流：这一句就多停一会儿，等下一句来
      if (concluded) setSettled(true);
    }, dwellFor(units[at] ?? ''));
    return () => window.clearTimeout(timer);
  }, [narrating, settled, units, index, concluded]);

  useEffect(() => {
    if (leaving == null) return;
    const timer = window.setTimeout(() => setLeaving(null), HANDOFF_MS);
    return () => window.clearTimeout(timer);
  }, [leaving]);

  const focused = units[Math.min(index, units.length - 1)];
  const speaking = narrating && !settled && Boolean(focused);
  return {
    visible: speaking ? focused : full,
    leaving: speaking ? leaving : null,
    narrating: speaking,
  };
}
