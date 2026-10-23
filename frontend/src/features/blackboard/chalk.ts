/**
 * 粉笔笔迹生成。
 *
 * 几何形状由后端给定，这里只负责"画得像粉笔"。所有随机都从 seed 派生 ——
 * 同一个元素每次渲染、以及截图给别人看，形状都完全一致。
 */

import type { Pt } from './types';

export function mulberry32(a: number) {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const r1 = (n: number) => Math.round(n * 10) / 10;

/**
 * 沿折线走一遍，按固定间距取点并叠加"平滑漂移"，得到手画的轻微起伏。
 * 用平滑漂移而不是逐点乱抖 —— 逐点抖动看起来像噪点，不像人手。
 */
export function wobblePath(
  points: Pt[],
  seed: number,
  opts: { closed?: boolean; step?: number; amp?: number } = {},
): string {
  const { closed = false, step = 16, amp = 1.6 } = opts;
  if (!points || points.length < 2) return '';
  const rnd = mulberry32(seed);
  const src: Pt[] = closed ? [...points, points[0]] : points;
  const out: Pt[] = [];
  let drift = 0;

  for (let i = 0; i < src.length - 1; i++) {
    const [x0, y0] = src[i];
    const [x1, y1] = src[i + 1];
    const len = Math.hypot(x1 - x0, y1 - y0);
    const n = Math.max(1, Math.round(len / step));
    const nx = len ? -(y1 - y0) / len : 0;
    const ny = len ? (x1 - x0) / len : 0;
    for (let k = 0; k < n; k++) {
      const t = k / n;
      drift = drift * 0.74 + (rnd() - 0.5) * amp;
      const taper = k === 0 ? 0.35 : 1; // 起笔收笔稍微收一点
      out.push([x0 + (x1 - x0) * t + nx * drift * taper, y0 + (y1 - y0) * t + ny * drift * taper]);
    }
  }
  out.push(src[src.length - 1]);

  let d = `M ${r1(out[0][0])} ${r1(out[0][1])}`;
  for (let i = 1; i < out.length; i++) d += ` L ${r1(out[i][0])} ${r1(out[i][1])}`;
  return d;
}

/** 手画椭圆（圈注用）：不均匀的闭合曲线，起止处留个出头 */
export function wobbleEllipse(cx: number, cy: number, rx: number, ry: number, seed: number): string {
  const rnd = mulberry32(seed);
  const n = 34;
  const start = rnd() * Math.PI * 2;
  const pts: Pt[] = [];
  for (let i = 0; i <= n + 3; i++) {
    const a = start + (i / n) * Math.PI * 2;
    const wob = 1 + (rnd() - 0.5) * 0.035;
    pts.push([cx + Math.cos(a) * rx * wob, cy + Math.sin(a) * ry * wob]);
  }
  let d = `M ${r1(pts[0][0])} ${r1(pts[0][1])}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const [x, y] = pts[i];
    const [xn, yn] = pts[i + 1];
    d += ` Q ${r1(x)} ${r1(y)} ${r1((x + xn) / 2)} ${r1((y + yn) / 2)}`;
  }
  return d;
}

/** 每个字的轻微歪斜，让文字像手写上去的 */
export function charJitter(seed: number, index: number) {
  const rnd = mulberry32(seed * 7919 + index * 104729);
  return {
    dx: (rnd() - 0.5) * 1.6,
    dy: (rnd() - 0.5) * 2.2,
    rot: (rnd() - 0.5) * 2.4,
    opacity: 0.84 + rnd() * 0.16,
  };
}

/** 与服务端一致的字符宽度估算（CJK 按 1em，西文按 0.55em） */
export function charWidth(ch: string, size: number): number {
  return /[\u2E80-\u9FFF\u3000-\u303F\uFF00-\uFFEF]/.test(ch) ? size : size * 0.55;
}

export function layoutLine(line: string, size: number): { ch: string; x: number }[] {
  const out: { ch: string; x: number }[] = [];
  let x = 0;
  for (const ch of line) {
    out.push({ ch, x });
    x += charWidth(ch, size);
  }
  return out;
}
