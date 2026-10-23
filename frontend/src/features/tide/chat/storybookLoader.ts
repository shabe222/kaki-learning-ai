/**
 * 视觉绘本组件的按需加载。
 *
 * 组件是宿主页面外挂的 Web Component（frontend/public/agent/darya-widget.js），
 * 不在 Vite 模块图里，必须显式注入脚本；否则 `darya-storybook` 永远不会被注册，
 * 调用 `open()` 会静默失败。
 */
const WIDGET_SRC = '/agent/darya-widget.js';
const ELEMENT_NAME = 'darya-storybook';

let widgetPromise: Promise<void> | null = null;

export function loadStorybookWidget(): Promise<void> {
  if (customElements.get(ELEMENT_NAME)) return Promise.resolve();

  if (!widgetPromise) {
    widgetPromise = new Promise<void>((resolve, reject) => {
      const script = document.createElement('script');
      script.src = WIDGET_SRC;
      script.async = true;
      script.onload = () => {
        if (customElements.get(ELEMENT_NAME)) resolve();
        else reject(new Error('教学绘本组件没有正确注册'));
      };
      script.onerror = () => reject(new Error('教学绘本组件加载失败'));
      document.head.appendChild(script);
    }).catch((error: unknown) => {
      // 允许用户重试：失败后不缓存 rejected promise
      widgetPromise = null;
      throw error;
    });
  }

  return widgetPromise;
}
