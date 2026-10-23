/**
 * 黑板的数据模型。
 *
 * 约定：几何一律由后端解算好再下发（`render` 里已是可直接绘制的坐标），
 * 前端不做任何布局/几何计算 —— 只负责画出来和做动画。
 * 这样前端保持极薄，而且"AI 看到的"和"用户看到的"永远是同一份数据。
 */

export type Pt = [number, number];

export type Rect = { x: number; y: number; w: number; h: number };

export type BlackboardElementKind =
  | 'text'
  | 'line'
  | 'polyline'
  | 'polygon'
  | 'circle'
  | 'arrow'
  | 'stroke'
  | 'annotate';

/** 绘制就绪的几何：后端算好，前端直接用 */
export type BlackboardRender = {
  /** 折线/多边形/线段/箭头的顶点 */
  points?: Pt[];
  /** 文字的左上角 */
  x?: number;
  y?: number;
  text?: string;
  size?: number;
  /** 圆与椭圆 */
  cx?: number;
  cy?: number;
  r?: number;
  rx?: number;
  ry?: number;
  /** annotate 的样式 */
  style?: 'circle' | 'underline' | 'highlight';
};

export type BlackboardElement = {
  id: string;
  type: BlackboardElementKind;
  /** 谁写的：ai | human。前端据此可以区分笔迹来源 */
  source?: 'ai' | 'human';
  render: BlackboardRender;
  /** 板面坐标下的包围盒（后端算好） */
  bbox: Rect;
  /** 颜色，直接给 CSS 颜色值 */
  color?: string;
  /** 线宽（板面单位） */
  width?: number;
  /** 决定粉笔抖动形状的随机种子，后端给，保证每次渲染一致 */
  seed: number;
  /** 元素标签，便于调试与指代 */
  label?: string | null;
};

/** 一次板面操作。后端下发有序 ops，前端按序执行并做动画。 */
export type BlackboardOp =
  | { kind: 'add'; element: BlackboardElement }
  | { kind: 'update'; element: BlackboardElement }
  | { kind: 'remove'; id: string }
  | { kind: 'clear' }
  | { kind: 'pointer'; bbox: Rect; ms?: number };

/** 一节 = DARYA 要说的一句话 + 这句话对应的那几笔。 */
export type BlackboardStep = {
  /** 说这句话的同时落这几笔。为空表示这一节只有动作（擦板、指一下）没有说话。 */
  narration?: string;
  ops: BlackboardOp[];
};

/** 一条消息附带的黑板脚本 */
export type BlackboardScript = {
  /** 板面逻辑尺寸，默认 1600×1000 */
  size?: { w: number; h: number };
  /** 执行完 ops 之后整块板的完整元素。板子是空的时候（重开历史、通话中途进入）
   *  用它直接铺好，不必把前面每一轮都重放一遍。 */
  elements?: BlackboardElement[];
  ops: BlackboardOp[];
  /** 把 ops 按"一句话"分好组。有它的时候前端按节说、按节画；没有（老消息）就照旧
   *  一次性把 ops 排出去。分组由后端按场景归属算，前端不猜。 */
  steps?: BlackboardStep[];
};

export const DEFAULT_BOARD_SIZE = { w: 1600, h: 1000 } as const;

/** 粉笔色板（与后端保持一致；后端也可直接下发任意 CSS 颜色） */
export const CHALK = {
  white: '#f3f5f0',
  amber: '#ffd98a',
  blue: '#a8d3f5',
  pink: '#f5a8b8',
  green: '#b6e2a1',
} as const;

/** 元素落笔的默认节奏（毫秒）：让一批元素依次写出来，而不是一起冒出来 */
export const DEFAULT_PACE_MS = 170;

/**
 * 一句话在板子上停留多久（毫秒）。
 *
 * 比落笔慢：人是把话说完，手才停。按字数算，短句不闪、长句不拖，两头都封住 ——
 * 一节最长也就四秒出头，六七个要点的板子全程二十几秒，和一个人边讲边画差不多。
 */
export const NARRATION_PER_CHAR_MS = 135;
export const MIN_NARRATION_MS = 1100;
export const MAX_NARRATION_MS = 4200;
