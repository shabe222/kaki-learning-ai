import type { ReactNode } from 'react';
import { useEffect } from 'react';
import styles from './RippleProvider.module.css';

/**
 * 全局点击水波反馈（柿子智学统一交互语言）
 *
 * 监听 pointerdown（桌面）/ touchstart（移动端）：
 * - 命中可交互元素（按钮、链接、卡片、可点击区域）时，在点击坐标处生成一圈柔和涟漪
 * - 450ms 内平滑扩散并淡出，动画结束后自动销毁
 * - 输入框、textarea、select、canvas、拖拽位移均不触发
 */
export function RippleProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    const supportsPointer = typeof window !== 'undefined' && 'PointerEvent' in window;
    const eventName = supportsPointer ? 'pointerdown' : 'touchstart';
    let currentRipple: HTMLElement | null = null;
    let startX = 0;
    let startY = 0;
    let host: HTMLElement | null = null;

    const findRippleTarget = (el: HTMLElement | null): HTMLElement | null => {
      let node: HTMLElement | null = el;
      let depth = 0;
      while (node && node instanceof HTMLElement && depth < 6) {
        const tag = node.tagName.toLowerCase();
        // 输入与绘制区域不触发水波
        if (tag === 'input' || tag === 'textarea' || tag === 'select' || node.isContentEditable) return null;
        if (tag === 'canvas') return null;
        // 悬浮层（fixed 定位）有自己的拖拽/定位交互，改其样式会破坏布局，一律不生成水波
        if (getComputedStyle(node).position === 'fixed') return null;
        if (node.matches?.('button, a, [role="button"], label, summary, [tabindex]:not([tabindex="-1"]), [data-ripple]')) return node;
        // 兜底：带手型光标的可点击卡片 / 标签 / 导航项
        if (getComputedStyle(node).cursor === 'pointer') return node;
        node = node.parentElement;
        depth += 1;
      }
      return null;
    };

    const destroyRipple = () => {
      if (currentRipple && host && currentRipple.parentNode === host) {
        host.removeChild(currentRipple);
      }
      // 恢复临时覆写的样式，避免影响布局
      if (host) {
        if (host.dataset.rippleRestore === 'position') host.style.position = '';
        if (host.dataset.rippleRestore === 'overflow') host.style.overflow = '';
        delete host.dataset.rippleRestore;
      }
      currentRipple = null;
      host = null;
    };

    const spawn = (target: HTMLElement, clientX: number, clientY: number) => {
      const rect = target.getBoundingClientRect();
      if (rect.width < 4 || rect.height < 4) return;
      const size = Math.max(rect.width, rect.height) * 2.2;
      const x = clientX - rect.left;
      const y = clientY - rect.top;

      const ripple = document.createElement('span');
      ripple.className = styles.ripple;
      ripple.style.width = `${size}px`;
      ripple.style.height = `${size}px`;
      ripple.style.left = `${x - size / 2}px`;
      ripple.style.top = `${y - size / 2}px`;
      ripple.style.setProperty('--ripple-size', `${size}px`);

      // 为水波提供定位上下文，动画结束后恢复原样式
      if (target.style.position === '' || target.style.position === 'static') {
        target.style.position = 'relative';
        target.dataset.rippleRestore = 'position';
      }
      if (target.style.overflow !== 'visible' && target.style.overflow !== '') {
        // 已有裁剪保持不动
      } else {
        target.style.overflow = 'hidden';
        target.dataset.rippleRestore = 'overflow';
      }

      target.appendChild(ripple);
      currentRipple = ripple;
      host = target;
      startX = clientX;
      startY = clientY;

      // 动画结束自动销毁（450ms 后）
      window.setTimeout(() => {
        if (currentRipple === ripple) destroyRipple();
      }, 520);
    };

    const onPointerDown = (event: Event) => {
      const e = event as PointerEvent;
      const target = e.target instanceof HTMLElement ? e.target : null;
      const rippleTarget = findRippleTarget(target);
      if (!rippleTarget) return;
      spawn(rippleTarget, e.clientX, e.clientY);
    };

    const onTouchStart = (event: Event) => {
      const e = event as TouchEvent;
      const touch = e.touches?.[0];
      if (!touch) return;
      const target = e.target instanceof HTMLElement ? e.target : null;
      const rippleTarget = findRippleTarget(target);
      if (!rippleTarget) return;
      spawn(rippleTarget, touch.clientX, touch.clientY);
    };

    // 拖拽（位移较大）时取消水波，避免滚动/拖动产生残留反馈
    const onMove = (event: Event) => {
      if (!currentRipple) return;
      const clientX = (event as PointerEvent).clientX;
      const clientY = (event as PointerEvent).clientY;
      if (Math.abs(clientX - startX) > 12 || Math.abs(clientY - startY) > 12) {
        destroyRipple();
      }
    };

    document.addEventListener(eventName, onPointerDown, true);
    if (!supportsPointer) document.addEventListener('touchstart', onTouchStart, true);
    document.addEventListener('pointermove', onMove, true);
    return () => {
      document.removeEventListener(eventName, onPointerDown, true);
      if (!supportsPointer) document.removeEventListener('touchstart', onTouchStart, true);
      document.removeEventListener('pointermove', onMove, true);
    };
  }, []);

  return <>{children}</>;
}
