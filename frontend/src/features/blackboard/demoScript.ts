/**
 * 本地演示脚本：不依赖后端就能看到黑板的完整效果。
 *
 * 它的用途有两个：
 * 1. 验收渲染层（落笔、擦除、指一下、粉笔质感）——脱离后端单独跑
 * 2. 给队友一条"拿到就能看"的最小路径：起前端、打开 /blackboard-lab
 *
 * 真实使用时脚本由后端下发（见 backend/services/blackboard），结构完全一样。
 */

import type { BlackboardScript } from './types';

const CHALK = {
  white: '#f3f5f0',
  amber: '#ffd98a',
  blue: '#a8d3f5',
  pink: '#f5a8b8',
};

/** 一份"讲勾股定理"的板书脚本，覆盖多边形/文字/标注/指一下 */
export function buildDemoScript(): BlackboardScript {
  return {
    size: { w: 1600, h: 1000 },
    ops: [
      {
        kind: 'add',
        element: {
          id: 'demo-title',
          type: 'text',
          render: { x: 80, y: 70, text: '勾股定理', size: 48 },
          bbox: { x: 80, y: 70, w: 192, h: 64 },
          color: CHALK.amber,
          seed: 101,
          label: '标题',
        },
      },
      {
        kind: 'add',
        element: {
          id: 'demo-body',
          type: 'text',
          render: { x: 80, y: 170, text: '直角三角形两条直角边的平方和\n等于斜边的平方。', size: 34 },
          bbox: { x: 80, y: 170, w: 476, h: 92 },
          color: CHALK.white,
          seed: 102,
          label: '正文',
        },
      },
      {
        kind: 'add',
        element: {
          id: 'demo-tri',
          type: 'polygon',
          render: { points: [[200, 360], [700, 760], [200, 760]] },
          bbox: { x: 200, y: 360, w: 500, h: 400 },
          color: CHALK.white,
          width: 3.6,
          seed: 103,
          label: '三角形ABC',
        },
      },
      {
        kind: 'add',
        element: {
          id: 'demo-vA',
          type: 'text',
          render: { x: 182, y: 300, text: 'A', size: 32 },
          bbox: { x: 182, y: 300, w: 32, h: 43 },
          color: CHALK.white,
          seed: 104,
          label: '顶点A',
        },
      },
      {
        kind: 'add',
        element: {
          id: 'demo-vB',
          type: 'text',
          render: { x: 722, y: 740, text: 'B', size: 32 },
          bbox: { x: 722, y: 740, w: 32, h: 43 },
          color: CHALK.white,
          seed: 105,
          label: '顶点B',
        },
      },
      {
        kind: 'add',
        element: {
          id: 'demo-vC',
          type: 'text',
          render: { x: 138, y: 762, text: 'C', size: 32 },
          bbox: { x: 138, y: 762, w: 32, h: 43 },
          color: CHALK.white,
          seed: 106,
          label: '顶点C',
        },
      },
      {
        kind: 'add',
        element: {
          id: 'demo-sCA',
          type: 'text',
          render: { x: 60, y: 545, text: 'CA=4', size: 28 },
          bbox: { x: 60, y: 545, w: 112, h: 38 },
          color: CHALK.blue,
          seed: 107,
          label: '边长CA',
        },
      },
      {
        kind: 'add',
        element: {
          id: 'demo-sBC',
          type: 'text',
          render: { x: 380, y: 790, text: 'BC=3', size: 28 },
          bbox: { x: 380, y: 790, w: 112, h: 38 },
          color: CHALK.blue,
          seed: 108,
          label: '边长BC',
        },
      },
      {
        kind: 'add',
        element: {
          id: 'demo-sAB',
          type: 'text',
          render: { x: 470, y: 500, text: 'AB=5', size: 28 },
          bbox: { x: 470, y: 500, w: 112, h: 38 },
          color: CHALK.blue,
          seed: 109,
          label: '边长AB',
        },
      },
      {
        kind: 'add',
        element: {
          id: 'demo-formula',
          type: 'text',
          render: { x: 880, y: 300, text: 'a² + b² = c²', size: 44 },
          bbox: { x: 880, y: 300, w: 352, h: 59 },
          color: CHALK.amber,
          seed: 110,
          label: '公式',
        },
      },
      {
        kind: 'add',
        element: {
          id: 'demo-hl',
          type: 'annotate',
          render: { style: 'highlight', points: [[870, 355], [1240, 355]], size: 44 },
          bbox: { x: 880, y: 300, w: 352, h: 59 },
          color: CHALK.amber,
          seed: 111,
          label: '高亮',
        },
      },
      {
        kind: 'add',
        element: {
          id: 'demo-note',
          type: 'text',
          render: { x: 880, y: 430, text: '3² + 4² = 9 + 16 = 25 = 5²', size: 34 },
          bbox: { x: 880, y: 430, w: 476, h: 46 },
          color: CHALK.blue,
          seed: 112,
          label: '验算',
        },
      },
      { kind: 'pointer', bbox: { x: 880, y: 300, w: 352, h: 59 }, ms: 2400 },
      {
        kind: 'add',
        element: {
          id: 'demo-concl',
          type: 'text',
          render: { x: 880, y: 540, text: '两条直角边的平方和\n等于斜边平方', size: 36 },
          bbox: { x: 880, y: 540, w: 324, h: 97 },
          color: CHALK.white,
          seed: 113,
          label: '结论',
        },
      },
    ],
  };
}

/** 一份小脚本，用来单独看"擦除"动画 */
export function buildEraseDemoScript(): BlackboardScript {
  return {
    ops: [
      {
        kind: 'add',
        element: {
          id: 'erase-demo',
          type: 'text',
          render: { x: 120, y: 120, text: '这一行马上会被擦掉', size: 40 },
          bbox: { x: 120, y: 120, w: 440, h: 54 },
          color: CHALK.pink,
          seed: 201,
          label: '待擦',
        },
      },
      { kind: 'remove', id: 'erase-demo' },
    ],
  };
}
