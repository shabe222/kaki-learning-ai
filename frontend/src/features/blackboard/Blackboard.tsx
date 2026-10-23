/**
 * 黑板渲染层。
 *
 * 只做三件事：把后端下发的元素画成粉笔字、把新元素"写"出来、把被擦的元素"擦"掉。
 * 不做几何计算、不发请求、不持有业务状态 —— 因此可以同时嵌进对话区和通话面板。
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { BlackboardElement, BlackboardOp, BlackboardScript, BlackboardStep, Pt, Rect } from './types';
import {
  DEFAULT_BOARD_SIZE,
  DEFAULT_PACE_MS,
  MAX_NARRATION_MS,
  MIN_NARRATION_MS,
  NARRATION_PER_CHAR_MS,
} from './types';
import { charJitter, layoutLine, wobbleEllipse, wobblePath } from './chalk';
import { prefersReducedMotion } from '../../shared/motion';
import styles from './Blackboard.module.css';

const CHALK_FONT = '"KaiTi", "楷体", "STKaiti", "Kaiti SC", "Microsoft YaHei", sans-serif';
/** 落笔/擦拭动画的时长，与 CSS 里保持一致；用于判断"这一批动作做完了" */
const WRITE_ANIM_MS = 520;
const WIPE_ANIM_MS = 560;
const GHOST_TTL_MS = 700;
/** 一句说完了喘口气再写下一句。太紧会像在念稿，太松会让人等。 */
const STEP_BREATH_MS = 260;
/** 上一句话淡出所用的时间，与 CSS 里的 bbCaptionOut 一致 */
const CAPTION_EXIT_MS = 420;

/** 这句大概要说多久：按字数算，两头封住 */
function spokenMs(sentence: string): number {
  return Math.min(MAX_NARRATION_MS, Math.max(MIN_NARRATION_MS, sentence.length * NARRATION_PER_CHAR_MS));
}

/** 这一节停留多久：话要说，笔要落完，取更长的那个 */
function stepHoldMs(step: BlackboardStep, paceMs: number): number {
  const drawing = step.ops.length ? (step.ops.length - 1) * paceMs + WRITE_ANIM_MS : 0;
  const spoken = step.narration ? spokenMs(step.narration) : 0;
  return Math.max(drawing, spoken) + STEP_BREATH_MS;
}

/** 这一节的笔什么时候停 —— 通话模式据此决定什么时候可以打断 */
function stepDrawingMs(step: BlackboardStep, paceMs: number): number {
  return step.ops.length ? (step.ops.length - 1) * paceMs + WRITE_ANIM_MS : 0;
}

/** 正在讲的那一句。key 只用来区分前后两句，让交接时两句能同时待在 DOM 里。 */
export type Narration = { text: string; key: number };

/** 此刻在说的那句 + 刚让位的那句。要两句都在手上，交接处才不空一拍。 */
export type BoardNarration = { saying: Narration | null; leaving: Narration | null };

type Props = {
  script: BlackboardScript | null;
  /** 元素之间落笔的间隔，越大越像一笔一笔写 */
  paceMs?: number;
  /**
   * 要不要把这一板"当场写出来"。
   *
   * false 用于翻回来的历史（刷新页面、切线程、通话中途进来）：直接给最终板面。
   * 边讲边画是当场发生的事 —— 把上一轮从头再演一遍，那不是回放，那是假直播，
   * 而且这时候人根本没在讲。
   */
  animate?: boolean;
  /** 板面是否还在一批动作里 —— 通话模式用它来决定此时不打断。
   *  分节模式下"一批"= 一节（一句话的几笔），句间让位，笔不停在半画上。 */
  onBusyChange?: (busy: boolean) => void;
  /**
   * 正在讲的那一句交给谁画。
   *
   * 不给的时候板子自己把它压在板面下沿。给了就由调用方放到板子上方的说话区 ——
   * 板面是粉笔的地盘，字盖上去会挡住刚写的那一笔。
   */
  onNarrationChange?: (narration: BoardNarration) => void;
  className?: string;
};

const baseStroke = {
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  fill: 'none' as const,
};

/** 一笔粉笔：一层更宽更淡的粉笔灰（重描）+ 一层带毛边与颗粒的实笔 */
function ChalkStroke({
  make,
  color,
  width,
  fresh,
  delay = 0,
}: {
  make: (seedOffset: number) => string;
  color: string;
  width: number;
  fresh: boolean;
  delay?: number;
}) {
  const d = make(0);
  if (!d) return null;
  return (
    <g>
      <path d={make(733)} {...baseStroke} stroke={color} strokeWidth={width * 1.8} opacity={0.15} filter="url(#bbChalkDust)" />
      <path
        d={d}
        {...baseStroke}
        stroke={color}
        strokeWidth={width}
        filter="url(#bbChalkStroke)"
        pathLength={1}
        className={fresh ? styles.drawIn : undefined}
        style={fresh ? { animationDelay: `${delay}s` } : undefined}
      />
    </g>
  );
}

function ArrowHead({ points, color, width, seed }: { points: Pt[]; color: string; width: number; seed: number }) {
  const [x1, y1] = points[points.length - 2];
  const [x2, y2] = points[points.length - 1];
  const a = Math.atan2(y2 - y1, x2 - x1);
  const len = Math.max(18, width * 5.5);
  const spread = 0.42;
  return (
    <path
      d={wobblePath(
        [
          [x2 - Math.cos(a - spread) * len, y2 - Math.sin(a - spread) * len],
          [x2, y2],
          [x2 - Math.cos(a + spread) * len, y2 - Math.sin(a + spread) * len],
        ],
        seed + 91,
      )}
      {...baseStroke}
      stroke={color}
      strokeWidth={width}
      filter="url(#bbChalkStroke)"
    />
  );
}

function ChalkText({ el, fresh, delay }: { el: BlackboardElement; fresh: boolean; delay: number }) {
  const r = el.render;
  const size = r.size ?? 30;
  const lineH = size * 1.34;
  const lines = String(r.text ?? '').split('\n');
  const seed = el.seed;
  let order = 0;

  return (
    <g filter="url(#bbChalkText)">
      {lines.map((line, li) => (
        <g key={li}>
          {layoutLine(line, size).map(({ ch, x }, ci) => {
            const j = charJitter(seed, li * 137 + ci);
            const at = fresh ? delay + Math.min(0.55, order * 0.026) : 0;
            order += 1;
            return (
              <text
                key={ci}
                x={(r.x ?? 0) + x + j.dx}
                y={(r.y ?? 0) + li * lineH + size * 0.88 + j.dy}
                fontSize={size}
                fill={el.color ?? '#f3f5f0'}
                opacity={j.opacity}
                className={fresh ? styles.charIn : undefined}
                style={{
                  fontFamily: CHALK_FONT,
                  transformBox: 'fill-box',
                  transformOrigin: 'center',
                  transform: `rotate(${j.rot}deg)`,
                  // 逐字依次出现靠延迟实现，关键帧写在 CSS 里（CSS Module 会给它加作用域哈希）
                  ...(fresh ? { animationDelay: `${at}s` } : {}),
                }}
              >
                {ch}
              </text>
            );
          })}
        </g>
      ))}
    </g>
  );
}

function ElementView({ el, fresh, delay }: { el: BlackboardElement; fresh: boolean; delay: number }) {
  const r = el.render;
  const color = el.color ?? '#f3f5f0';
  const width = el.width ?? 3.4;

  if (el.type === 'text') return <ChalkText el={el} fresh={fresh} delay={delay} />;

  if (el.type === 'annotate') {
    if (r.rx != null && r.cy != null) {
      return (
        <ChalkStroke
          make={(off) => wobbleEllipse(r.cx ?? 0, r.cy ?? 0, r.rx ?? 0, r.ry ?? r.rx ?? 0, el.seed + off)}
          color={color}
          width={el.width || 3.8}
          fresh={fresh}
          delay={delay}
        />
      );
    }
    if (r.points?.length) {
      if (r.style === 'highlight') {
        return (
          <path
            d={wobblePath(r.points, el.seed)}
            {...baseStroke}
            stroke={color}
            strokeWidth={(r.size ?? 34) * 0.9}
            opacity={0.2}
            filter="url(#bbChalkDust)"
          />
        );
      }
      return (
        <ChalkStroke
          make={(off) => wobblePath(r.points ?? [], el.seed + off)}
          color={color}
          width={el.width || 3.8}
          fresh={fresh}
          delay={delay}
        />
      );
    }
    return null;
  }

  if (r.points?.length) {
    return (
      <g>
        <ChalkStroke
          make={(off) => wobblePath(r.points ?? [], el.seed + off, { closed: el.type === 'polygon' })}
          color={color}
          width={width}
          fresh={fresh}
          delay={delay}
        />
        {el.type === 'arrow' && <ArrowHead points={r.points} color={color} width={width} seed={el.seed} />}
      </g>
    );
  }

  if (r.cx != null && r.r != null) {
    return (
      <ChalkStroke
        make={(off) => wobbleEllipse(r.cx ?? 0, r.cy ?? 0, r.r ?? 0, r.r ?? 0, el.seed + off)}
        color={color}
        width={width}
        fresh={fresh}
        delay={delay}
      />
    );
  }

  return null;
}

export function Blackboard({ script, paceMs = DEFAULT_PACE_MS, animate = true, onBusyChange, onNarrationChange, className }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [elements, setElements] = useState<BlackboardElement[]>([]);
  const [ghosts, setGhosts] = useState<{ el: BlackboardElement; key: string }[]>([]);
  const [fresh, setFresh] = useState<Map<string, number>>(new Map());
  const [pointer, setPointer] = useState<Rect | null>(null);
  const [scale, setScale] = useState(1);
  /** 正在说的那句 + 刚被顶掉的那句 */
  const [saying, setSaying] = useState<{ text: string; key: number } | null>(null);
  const [leaving, setLeaving] = useState<{ text: string; key: number } | null>(null);

  const timers = useRef<number[]>([]);
  const writeStart = useRef(0);
  const busyUntil = useRef(0);
  /** 当前板上的元素。用 ref 而不是只读 state：执行 ops 时要判断"这块是不是已经写过了"，
   *  setState 更新函数里做副作用在严格模式下会被调用两次。 */
  const boardRef = useRef<BlackboardElement[]>([]);
  const lastScriptKey = useRef<string | null>(null);
  const boardSize = script?.size ?? DEFAULT_BOARD_SIZE;
  /** 有几节的笔还悬着。分节模式下"还在写"要按节计数，否则最后一节写完之前，
   *  所有中间节都让不出位子。 */
  const openSteps = useRef(0);
  const captionSeq = useRef(0);
  const sayingRef = useRef<{ text: string; key: number } | null>(null);
  /** 板子上方那块说话区。走 ref 而不是把 onNarrationChange 塞进 say 的依赖 ——
   *  say 一变，排脚本的 effect 就会重跑，板子会从头再演一遍。 */
  const narrationSink = useRef(onNarrationChange);

  useEffect(() => {
    narrationSink.current = onNarrationChange;
    if (!onNarrationChange) return;
    onNarrationChange({ saying: sayingRef.current, leaving: null });
  }, [onNarrationChange]);

  // 板子没了，它那句话也不能留在外面。
  useEffect(
    () => () => {
      narrationSink.current?.({ saying: null, leaving: null });
    },
    [],
  );

  /** 清理所有待执行的定时器（组件卸载或脚本被替换时） */
  const clearTimers = useCallback(() => {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current = [];
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  /**
   * 换一句话说。
   *
   * 新的一句出现时，上一句还在淡出 —— 两句同时在 DOM 里，"消失"和"出现"是同一个瞬间，
   * 而不是先空一下再冒出下一句。板子上正在被讲的那一部分因此始终有焦点。
   */
  const say = useCallback((text: string) => {
    const clean = text.trim();
    if (sayingRef.current?.text === clean) return;
    const previous = sayingRef.current;
    captionSeq.current += 1;
    const next = clean ? { text: clean, key: captionSeq.current } : null;
    sayingRef.current = next;
    setSaying(next);
    if (!previous) {
      narrationSink.current?.({ saying: next, leaving: null });
      return;
    }
    setLeaving(previous);
    narrationSink.current?.({ saying: next, leaving: previous });
    timers.current.push(
      window.setTimeout(
        () => {
          setLeaving((current) => (current && current.key === previous.key ? null : current));
          narrationSink.current?.({ saying: sayingRef.current, leaving: null });
        },
        CAPTION_EXIT_MS,
      ),
    );
  }, []);

  const commit = useCallback((list: BlackboardElement[]) => {
    boardRef.current = list;
    setElements(list);
  }, []);

  /** 让这个元素"正在被写出来"，写完动画结束后摘掉标记 */
  const markFresh = useCallback((id: string, base: number) => {
    setFresh((m) => new Map(m).set(id, Math.min(base, 2.2)));
    timers.current.push(
      window.setTimeout(() => {
        setFresh((m) => {
          const next = new Map(m);
          next.delete(id);
          return next;
        });
      }, 1800),
    );
  }, []);

  const applyOp = useCallback((op: BlackboardOp) => {
    const base = (Date.now() - writeStart.current) / 1000;
    switch (op.kind) {
      case 'add': {
        const el = op.element;
        const existing = boardRef.current.find((e) => e.id === el.id);
        // 已经写着同样的内容了就不重描：冷启动时整块板是铺上去的，
        // 再把每个元素重写一遍会像有人在黑板上反复描。
        if (existing && JSON.stringify(existing) === JSON.stringify(el)) break;
        commit(existing
          ? boardRef.current.map((e) => (e.id === el.id ? el : e))
          : [...boardRef.current, el]);
        markFresh(el.id, base);
        break;
      }
      case 'update': {
        const el = op.element;
        commit(
          boardRef.current.some((e) => e.id === el.id)
            ? boardRef.current.map((e) => (e.id === el.id ? el : e))
            : [...boardRef.current, el],
        );
        markFresh(el.id, base);
        break;
      }
      case 'remove': {
        const gone = boardRef.current.find((e) => e.id === op.id);
        if (gone) {
          const key = `${gone.id}@${Date.now()}`;
          setGhosts((g) => [...g, { el: gone, key }]);
          timers.current.push(
            window.setTimeout(() => setGhosts((g) => g.filter((x) => x.key !== key)), GHOST_TTL_MS),
          );
        }
        commit(boardRef.current.filter((e) => e.id !== op.id));
        break;
      }
      case 'clear': {
        commit([]);
        setGhosts([]);
        setFresh(new Map());
        break;
      }
      case 'pointer': {
        const ms = op.ms ?? 2200;
        setPointer(op.bbox);
        timers.current.push(window.setTimeout(() => setPointer(null), ms));
        break;
      }
    }
  }, [commit, markFresh]);

  /** 板面按容器自适应缩放：居中、留边 */
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const fit = () => {
      const { width, height } = wrap.getBoundingClientRect();
      if (!width || !height) return;
      const pad = 12;
      setScale(Math.min((width - pad * 2) / boardSize.w, (height - pad * 2) / boardSize.h));
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [boardSize.w, boardSize.h]);

  /**
   * 把脚本排出去。
   *
   * 有分节的时候一节一节来：说一句，画这一句对应的那几笔，说完了才轮到下一句，
   * 板子上的焦点始终只有一处。没有分节（老消息、系统里存下来的旧脚本）或者用户
   * 说了减少动态效果，就照旧把 ops 一口气排出去 —— 不能因为多了个新字段就不画了。
   */
  useEffect(() => {
    if (!script) return;
    // 同一条消息在历史刷新后会重新构造对象；内容没变就不要重放动画。
    const key = JSON.stringify(script);
    if (lastScriptKey.current === key) return;
    lastScriptKey.current = key;
    clearTimers();
    openSteps.current = 0;
    say('');

    // 板子是空的但脚本不是从零开始（重开历史、通话中途进来）：
    // 先把整块板铺好，再执行这一轮的改动，否则那些 update 找不到落点。
    if (boardRef.current.length === 0 && script.elements?.length) {
      boardRef.current = script.elements;
      setElements(script.elements);
    }

    if (!animate) {
      // 翻回来的板子：整块铺好，一个字都不重写。
      if (script.elements?.length) {
        boardRef.current = script.elements;
        setElements(script.elements);
        return;
      }
      (script.ops ?? []).forEach((op) => applyOp(op));
      return;
    }

    const now = Date.now();
    if (now - writeStart.current > 1200) writeStart.current = now;

    const steps = (script.steps ?? []).filter((step) => step.ops?.length || step.narration?.trim());
    if (steps.length && !prefersReducedMotion()) {
      // "还在写"按节计数：中间节写完就让出位子，最后一节写完才算这一板结束。
      // 笔不会停在半画上 —— 一节里的几笔是排完的。
      const busyOn = () => {
        openSteps.current += 1;
        if (openSteps.current === 1) onBusyChange?.(true);
      };
      const busyOff = () => {
        openSteps.current = Math.max(0, openSteps.current - 1);
        if (openSteps.current === 0) onBusyChange?.(false);
      };
      let cursor = 0;
      steps.forEach((step) => {
        const startedAt = cursor;
        const drawingMs = stepDrawingMs(step, paceMs);
        timers.current.push(window.setTimeout(() => say(step.narration ?? ''), startedAt));
        step.ops.forEach((op, index) => {
          const at = startedAt + index * paceMs;
          if (at === 0) applyOp(op);
          else timers.current.push(window.setTimeout(() => applyOp(op), at));
        });
        if (drawingMs > 0) {
          timers.current.push(window.setTimeout(busyOn, startedAt));
          timers.current.push(window.setTimeout(busyOff, startedAt + drawingMs));
        }
        cursor += stepHoldMs(step, paceMs);
      });
      // 最后一句也要留够读完的时间再收起，别让要点一闪而过
      timers.current.push(window.setTimeout(() => say(''), cursor));
      return;
    }

    if (!script.ops?.length) return;

    const markBusy = (ms: number) => {
      busyUntil.current = Math.max(busyUntil.current, Date.now() + ms);
      onBusyChange?.(true);
    };
    const settle = (ms: number) => {
      const t = window.setTimeout(() => {
        if (Date.now() >= busyUntil.current) onBusyChange?.(false);
      }, ms);
      timers.current.push(t);
    };

    script.ops.forEach((op, i) => {
      const delay = i * paceMs;
      const run = () => applyOp(op);
      if (delay === 0) run();
      else timers.current.push(window.setTimeout(run, delay));
    });

    // 最后一个元素落笔 + 写完动画 = 这一批结束
    const total = script.ops.length * paceMs + WRITE_ANIM_MS;
    markBusy(total);
    settle(total + 40);
  }, [script, paceMs, animate, applyOp, say, clearTimers, onBusyChange]);

  // 卸载时不能把"还在写"留在外面，否则通话那边会一直等一个永远不会来的收笔。
  useEffect(
    () => () => {
      openSteps.current = 0;
      onBusyChange?.(false);
    },
    [onBusyChange],
  );

  const frame = useMemo(() => ({ w: boardSize.w + 32, h: boardSize.h + 32, ox: -16, oy: -16 }), [boardSize]);

  return (
    <div ref={wrapRef} className={[styles.wrap, className].filter(Boolean).join(' ')}>
      <svg
        className={styles.svg}
        viewBox={`${-20} ${-20} ${boardSize.w + 40} ${boardSize.h + 40}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="黑板"
      >
        <defs>
          {/* 粉笔实笔：边缘毛糙 + 颗粒镂空 + 一层极淡的灰晕。
              线条可以比文字"破"一些，所以颗粒留得更多（均值约 0.45）——
              粉笔线本来就该有断口，而字不行。 */}
          <filter id="bbChalkStroke" x="-14%" y="-14%" width="128%" height="128%">
            <feTurbulence type="fractalNoise" baseFrequency="0.72" numOctaves="3" seed="9" result="fray" />
            <feDisplacementMap in="SourceGraphic" in2="fray" scale="2.4" xChannelSelector="R" yChannelSelector="G" result="disp" />
            <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="4" seed="31" result="n" />
            <feColorMatrix
              in="n"
              type="matrix"
              values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1.9 -0.5"
              result="mask"
            />
            <feComposite in="disp" in2="mask" operator="in" result="grainy" />
            <feGaussianBlur in="disp" stdDeviation="0.6" result="soft" />
            <feMerge>
              <feMergeNode in="soft" />
              <feMergeNode in="grainy" />
            </feMerge>
          </filter>

          <filter id="bbChalkDust" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="2.8" />
          </filter>

          <filter id="bbChalkText" x="-8%" y="-8%" width="116%" height="116%">
            <feTurbulence type="fractalNoise" baseFrequency="1.1" numOctaves="3" seed="5" result="t" />
            <feDisplacementMap in="SourceGraphic" in2="t" scale="1.7" xChannelSelector="R" yChannelSelector="G" result="d" />
            <feTurbulence type="fractalNoise" baseFrequency="1.4" numOctaves="3" seed="13" result="n2" />
            {/*
              文字上的颗粒要克制：这里的 alpha 映射决定"字还读不读得出来"。
              取 2.2×噪声 − 0.35 → 平均不透明度约 0.75，只在噪声低的地方留一点断笔；
              之前用 1.5×噪声 − 0.42（均值 0.33）时，浅色字会暗到看不清 —— 那是拿可读性换质感，不值。
            */}
            <feColorMatrix
              in="n2"
              type="matrix"
              values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 2.2 -0.35"
              result="m2"
            />
            <feComposite in="d" in2="m2" operator="in" />
          </filter>

          <filter id="bbBoardDust" x="0" y="0" width="100%" height="100%">
            <feTurbulence type="fractalNoise" baseFrequency="0.75" numOctaves="4" seed="2" />
            <feColorMatrix type="matrix" values="0 0 0 0 0.87  0 0 0 0 0.9  0 0 0 0 0.84  0 0 0 0.16 0" />
          </filter>

          <filter id="bbBoardMottle" x="0" y="0" width="100%" height="100%">
            <feTurbulence type="fractalNoise" baseFrequency="0.0055" numOctaves="3" seed="17" />
            <feColorMatrix type="matrix" values="0 0 0 0 0.9  0 0 0 0 0.93  0 0 0 0 0.88  0 0 0 0.45 -0.2" />
          </filter>

          <filter id="bbSmudge" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="30" />
          </filter>

          <radialGradient id="bbSurface" cx="50%" cy="42%" r="78%">
            <stop offset="0%" stopColor="#354a3f" />
            <stop offset="52%" stopColor="#2a3a2f" />
            <stop offset="100%" stopColor="#1e2c25" />
          </radialGradient>
          <radialGradient id="bbVignette" cx="50%" cy="45%" r="74%">
            <stop offset="52%" stopColor="rgba(0,0,0,0)" />
            <stop offset="100%" stopColor="rgba(0,0,0,0.36)" />
          </radialGradient>
        </defs>

        {/* 板面：木框 + 板体 + 斑驳 + 擦痕 + 粉笔灰 + 暗角 */}
        <rect x={frame.ox} y={frame.oy} width={frame.w} height={frame.h} rx={10} fill="#7a5a41" opacity={0.85} />
        <rect x={0} y={0} width={boardSize.w} height={boardSize.h} fill="url(#bbSurface)" />
        <rect x={0} y={0} width={boardSize.w} height={boardSize.h} filter="url(#bbBoardMottle)" opacity={0.42} />
        <g style={{ mixBlendMode: 'screen' }}>
          {SMUDGES.map((s, i) => (
            <ellipse key={i} cx={s[0]} cy={s[1]} rx={s[2]} ry={s[3]} fill="#cfd8cc" opacity={0.045} filter="url(#bbSmudge)" />
          ))}
        </g>
        <rect x={0} y={0} width={boardSize.w} height={boardSize.h} filter="url(#bbBoardDust)" />
        <rect x={0} y={0} width={boardSize.w} height={boardSize.h} fill="url(#bbVignette)" />

        {/* 内容 */}
        {elements.map((el) => (
          <ElementView key={el.id} el={el} fresh={fresh.has(el.id)} delay={fresh.get(el.id) ?? 0} />
        ))}

        {/* 刚被擦掉的：把"擦"这个动作演完再消失 */}
        {ghosts.map((g) => (
          <g key={g.key} className={styles.wipeGroup}>
            <defs>
              <clipPath id={`bbWipe-${g.key}`}>
                <rect
                  className={styles.wipeRect}
                  style={
                    {
                      '--bbwx': `${g.el.bbox.x - 12}px`,
                      '--bbww': `${g.el.bbox.w + 24}px`,
                      x: `${g.el.bbox.x - 12}px`,
                      y: `${g.el.bbox.y - 12}px`,
                      width: `${g.el.bbox.w + 24}px`,
                      height: `${g.el.bbox.h + 24}px`,
                    } as React.CSSProperties
                  }
                />
              </clipPath>
            </defs>
            <g clipPath={`url(#bbWipe-${g.key})`}>
              <ElementView el={g.el} fresh={false} delay={0} />
            </g>
          </g>
        ))}

        {/* 「指一下」：闪几下一会儿就没，不在板上留东西 */}
        {pointer && (
          <rect className={styles.pointerRing} x={pointer.x - 16} y={pointer.y - 16} width={pointer.w + 32} height={pointer.h + 32} rx={12} />
        )}
      </svg>

      {/* 正在讲的那一句。上一句一边淡出一边让位，所以"消失"和"下一句出现"
          是同一个瞬间 —— 眼睛始终只需跟着一处。
          调用方接手（onNarrationChange）时这里就不画了，由它放到板子上方。 */}
      {!onNarrationChange && (saying || leaving) && (
        <div className={styles.captionLayer} aria-hidden="true">
          {leaving && <p key={leaving.key} className={`${styles.caption} ${styles.captionOut}`}>{leaving.text}</p>}
          {saying && <p key={saying.key} className={styles.caption}>{saying.text}</p>}
        </div>
      )}

      {/* 读屏软件要的是完整的几句话，不是一句一句插播进来的字幕 */}
      <ol className={styles.srOnly}>
        {(script?.steps ?? [])
          .map((step) => step.narration?.trim() ?? '')
          .filter(Boolean)
          .map((sentence, index) => <li key={index}>{sentence}</li>)}
      </ol>
    </div>
  );
}

/** 固定种子的擦痕：让板子看起来是用过的（不用随机，避免每次刷新都变） */
const SMUDGES: [number, number, number, number][] = [
  [280, 200, 190, 70],
  [1180, 260, 220, 90],
  [700, 760, 260, 80],
  [1380, 720, 150, 60],
  [420, 620, 170, 65],
];
