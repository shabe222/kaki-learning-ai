import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

const vendorManualChunks = (id: string) => {
  if (!id.includes('node_modules')) {
    return undefined;
  }
  if (
    id.includes('node_modules\\react\\') ||
    id.includes('node_modules/react/') ||
    id.includes('node_modules\\react-dom\\') ||
    id.includes('node_modules/react-dom/') ||
    id.includes('node_modules\\react-router') ||
    id.includes('node_modules/react-router') ||
    id.includes('node_modules\\scheduler\\') ||
    id.includes('node_modules/scheduler/')
  ) {
    return 'vendor-react';
  }
  if (
    id.includes('node_modules\\antd\\') ||
    id.includes('node_modules/antd/') ||
    id.includes('node_modules\\@ant-design\\') ||
    id.includes('node_modules/@ant-design/') ||
    id.includes('node_modules\\rc-') ||
    id.includes('node_modules/rc-') ||
    id.includes('node_modules\\@rc-component\\') ||
    id.includes('node_modules/@rc-component/')
  ) {
    return 'vendor-antd';
  }
  if (
    id.includes('node_modules\\axios\\') ||
    id.includes('node_modules/axios/') ||
    id.includes('node_modules\\dayjs\\') ||
    id.includes('node_modules/dayjs/') ||
    id.includes('node_modules\\zustand\\') ||
    id.includes('node_modules/zustand/') ||
    id.includes('node_modules\\clsx\\') ||
    id.includes('node_modules/clsx/') ||
    id.includes('node_modules\\lucide-react\\') ||
    id.includes('node_modules/lucide-react/')
  ) {
    return 'vendor-utils';
  }
  if (
    id.includes('node_modules\\classnames\\') ||
    id.includes('node_modules/classnames/') ||
    id.includes('node_modules\\throttle-debounce\\') ||
    id.includes('node_modules/throttle-debounce/') ||
    id.includes('node_modules\\stylis\\') ||
    id.includes('node_modules/stylis/') ||
    id.includes('node_modules\\@emotion\\') ||
    id.includes('node_modules/@emotion/') ||
    id.includes('node_modules\\@babel\\') ||
    id.includes('node_modules/@babel/') ||
    id.includes('node_modules\\scroll-into-view-if-needed\\') ||
    id.includes('node_modules/scroll-into-view-if-needed/') ||
    id.includes('node_modules\\compute-scroll-into-view\\') ||
    id.includes('node_modules/compute-scroll-into-view/') ||
    id.includes('node_modules\\copy-to-clipboard\\') ||
    id.includes('node_modules/copy-to-clipboard/') ||
    id.includes('node_modules\\toggle-selection\\') ||
    id.includes('node_modules/toggle-selection/') ||
    id.includes('node_modules\\shallowequal\\') ||
    id.includes('node_modules/shallowequal/') ||
    id.includes('node_modules\\dom-align\\') ||
    id.includes('node_modules/dom-align/')
  ) {
    return 'vendor-antd-helpers';
  }
  // 其余一律交给 Rollup 自动分块。
  //
  // 以前这里 return 'vendor-misc'，把 mermaid（100MB 源码）、@excalidraw、cytoscape、
  // katex、echarts、three 等只被懒加载旧页面用到的重库，和入口真正需要的几个小库
  // 强行捆成同一个 chunk。结果首页必须下载整个 8MB 的包 —— 这是首屏最大的性能问题。
  // 自动分块会让"只有懒加载路由才用到"的库留在各自的懒包依赖图里。
  return undefined;
};

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const proxyTarget = env.VITE_API_PROXY_TARGET || env.VITE_API_BASE_URL;
  return {
    base: env.VITE_BASE_PATH || '/',
    plugins: [react()],
    server: {
      host: true,
      port: 5173,
      strictPort: true,
      proxy: proxyTarget
        ? {
            '/api': {
              target: proxyTarget,
              changeOrigin: true,
              ws: false
            },
            '/static': {
              target: proxyTarget,
              changeOrigin: true
            }
          }
        : undefined
    },
    build: {
      chunkSizeWarningLimit: 1000,
      rollupOptions: {
        output: {
          manualChunks: vendorManualChunks
        }
      }
    }
  };
});
