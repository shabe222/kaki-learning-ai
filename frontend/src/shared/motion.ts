/**
 * 用户有没有说过"少动一点"。
 *
 * 每个做动画的组件都要问这一句，所以只留一个地方回答。任何"优雅地出现"的代码路径
 * 都必须先过这里 —— 这时候优雅不重要，尊重更重要。
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
