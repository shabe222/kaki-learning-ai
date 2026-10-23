import { useEffect, useRef, useState } from 'react';

/**
 * 让一段文字"长出来"，而不是啪一下出现。
 *
 * 刻意不做成打字机：打字机是等速的、机械的，一行字敲半天还没完，读者会去数它敲了
 * 几个字。这里走的是一条缓动曲线 —— 起笔快，收笔慢，像粉笔写完最后一笔手停下来
 * 的那个感觉；并且**时长有上限**，再长的回答也不会让人等两秒以上。
 *
 * 三条必须守住的规矩：
 * 1. 正在流式增长的文本要接着显示，不能每来一段就重新从头长一遍。
 * 2. 换一条消息（内容被替换而不是变长）要重新开始，不能拿旧位置当进度。
 * 3. 用户说了减少了动态效果，就立刻显示完 —— 这时候优雅不重要，尊重更重要。
 */

/** 最长不让人等这么久；再长也只是这里的一点延迟，不是拖沓。 */
const MAX_DURATION_MS = 2200;
const MIN_DURATION_MS = 240;
/** 每字大约花多久长出来，乘上字数就是基础时长。 */
const PER_CHARACTER_MS = 5;
/** 一帧最多推进这么多，长文不会一帧吞掉半屏。 */
const MAX_STEP_PER_FRAME = 6;
/** 约 30fps 提交一次，长文重排 markdown 不至于每帧都做一次。 */
const FRAME_BUDGET_MS = 32;

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function useGentleReveal(
  text: string,
  { enabled = true }: { enabled?: boolean } = {},
): { visible: string; revealing: boolean } {
  const full = text ?? '';
  const [shown, setShown] = useState(() => (enabled && !prefersReducedMotion() ? 0 : full.length));
  const shownRef = useRef(shown);
  shownRef.current = shown;
  const previousRef = useRef(full);
  const watchedRef = useRef(shown);
  const stalledSinceRef = useRef(0);
  const fullRef = useRef(full);
  fullRef.current = full;

  /**
   * 看门狗：长出来这件事停住了，就把剩下的直接补上。
   *
   * requestAnimationFrame 是没有保证的 —— 页面切到后台、系统省电、合成器被别的东西占着，
   * 它都可能长时间不发回调。这时候这段文字会一直停在 0 个字上，屏幕上是一个空白的回答，
   * 而且因为"还在流式"的判断也依赖它，连插入符都不会动。动画可以没有，答案不能没有。
   */
  useEffect(() => {
    if (!enabled || prefersReducedMotion()) return;
    // 只依赖 enabled：文本每来一小段就重建一次定时器的话，它永远活不到自己超时，
    // 这个兜底就等于没有。文字从 fullRef 现取。
    const timer = window.setInterval(() => {
      const target = fullRef.current.length;
      if (shownRef.current >= target) {
        stalledSinceRef.current = 0;
        return;
      }
      if (watchedRef.current === shownRef.current) {
        if (!stalledSinceRef.current) stalledSinceRef.current = Date.now();
        else if (Date.now() - stalledSinceRef.current > 700) {
          stalledSinceRef.current = 0;
          setShown(target);
        }
        return;
      }
      watchedRef.current = shownRef.current;
      stalledSinceRef.current = 0;
    }, 200);
    return () => window.clearInterval(timer);
  }, [enabled]);

  useEffect(() => {
    if (!enabled || prefersReducedMotion()) {
      setShown(full.length);
      return;
    }
    const previous = previousRef.current;
    previousRef.current = full;

    // 内容被换掉（而不是接在后面）就重新开始长。
    const replaced = !full.startsWith(previous.slice(0, Math.min(previous.length, 24)))
      || full.length < shownRef.current;
    const from = replaced ? 0 : Math.min(shownRef.current, full.length);
    if (from >= full.length) {
      if (replaced) setShown(0);
      else return;
    }

    const span = full.length - from;
    const duration = Math.min(
      MAX_DURATION_MS,
      Math.max(MIN_DURATION_MS, span * PER_CHARACTER_MS),
    );
    const started = performance.now();
    let frame = 0;
    let lastCommit = 0;

    const tick = (now: number) => {
      const progress = Math.min(1, (now - started) / duration);
      if (progress >= 1) {
        // 收笔必须收到位。每帧上限是为了不让长文一帧吞掉半屏，但它不能管到最后一帧 ——
        // 一旦被它卡住，循环已经结束，剩下的字就永远长不出来了，页面上留一句
        // 半截的话和一个不走的插入符。
        //
        // 这里用 max 而不是直接赋值：文本每来一段这个 effect 就重跑一次，理论上
        // 只有一个循环活着，但旧循环哪怕只多跑一帧，直接赋值就会把它那一版的
        // 旧长度写回去，屏幕上就是文字往后退了一截。宁可不动，也不能回退。
        setShown((current) => Math.max(current, full.length));
        return;
      }
      // easeOutCubic：起笔快，收笔慢。
      const eased = 1 - Math.pow(1 - progress, 3);
      const target = from + Math.round(span * eased);
      if (now - lastCommit >= FRAME_BUDGET_MS) {
        lastCommit = now;
        setShown((current) => {
          const step = Math.min(target, current + MAX_STEP_PER_FRAME);
          return Math.max(current, step);
        });
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [enabled, full]);

  const safeShown = Math.min(shown, full.length);
  return { visible: full.slice(0, safeShown), revealing: safeShown < full.length };
}
