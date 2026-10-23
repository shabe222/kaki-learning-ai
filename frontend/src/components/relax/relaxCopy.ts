/**
 * 「先歇一小会儿」夜景页 · 左下角文字配置
 *
 * 文案池与动效逻辑分离：改文案 / 节奏只动这个文件即可，
 * 不需要碰烟花引擎或打字机逻辑。
 */

export interface RelaxCopyLine {
  /** 主句（大字，逐字打出） */
  main: string;
  /** 该句完整显示后的停留毫秒数，缺省用 RELAX_HOLD_MS */
  holdMs?: number;
}

/** 打字机：平均每字毫秒数（带随机抖动，更接近自然手写） */
export const RELAX_TYPE_MS = 55;
/** 完整显示后默认停留毫秒数（1~2 秒） */
export const RELAX_HOLD_MS = 2000;
/** 淡出毫秒数 */
export const RELAX_FADE_MS = 900;

/**
 * 文案池：随机两句，各打一遍后停住。
 * 要求：简短、安静、有陪伴感；不鸡汤、不幼稚、不像安慰弹窗。
 */
export const RELAX_COPY_LINES: RelaxCopyLine[] = [
  { main: '先歇一小会儿……' },
  { main: '看一会儿烟花，再回到刚才的学习里。' },
];

/** 结束后最后补的一句提醒 */
export const RELAX_CLOSING_LINE: RelaxCopyLine = {
  main: '夜色安静下来了，带着这点光继续走吧。',
};
