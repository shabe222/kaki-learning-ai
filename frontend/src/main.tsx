import { useEffect } from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider, App as AntdApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { bindMessage } from './api/client';
import { RippleProvider } from './components/ripple/RippleProvider';
import './styles/global.css';

const basePath = import.meta.env.VITE_BASE_PATH || '/';

function MessageBinder() {
  const { message } = AntdApp.useApp();
  useEffect(() => {
    bindMessage(message);
  }, [message]);
  return null;
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          // 与 global.css 的 CSS 变量保持同一套柿子智学 Morandi 设计系统（低饱和柿橙主色、暖炭灰文字）
          colorPrimary: '#bd6b3a',
          colorInfo: '#bd6b3a',
          colorSuccess: '#6e8351',
          colorWarning: '#ad7a39',
          colorError: '#b0523f',
          colorText: '#3a332b',
          colorTextSecondary: '#776c5f',
          colorBorder: '#dcd0bd',
          colorBgContainer: 'rgba(255,253,249,0.85)',
          borderRadius: 10,
          fontFamily: '"Microsoft YaHei UI", "PingFang SC", "Noto Sans SC", sans-serif'
        },
        components: {
          Button: { controlHeight: 36, borderRadius: 8 },
          Card: { borderRadiusLG: 14 },
          Table: { headerBg: '#f6efe3', rowHoverBg: '#f5ede1' },
          Input: { controlHeight: 38 },
          Select: { controlHeight: 36 }
        }
      }}
    >
      <AntdApp message={{ maxCount: 1 }}>
        <MessageBinder />
        <RippleProvider>
          <BrowserRouter
            basename={basePath === '/' ? undefined : basePath}
            future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
          >
            <App />
          </BrowserRouter>
        </RippleProvider>
      </AntdApp>
  </ConfigProvider>
);
