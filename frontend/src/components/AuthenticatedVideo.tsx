import { useEffect, useState } from 'react';
import { Alert, Spin } from 'antd';
import { http } from '../api/client';

interface AuthenticatedVideoProps {
  src: string;
  className?: string;
  title?: string;
}

export function AuthenticatedVideo({ src, className, title }: AuthenticatedVideoProps) {
  const [objectUrl, setObjectUrl] = useState('');
  const [error, setError] = useState('');
  const protectedMedia = /^\/api\/chat\/tasks\/videos\/\d+\/media$/.test(src);

  useEffect(() => {
    if (!protectedMedia) return;
    let active = true;
    let nextUrl = '';
    setObjectUrl('');
    setError('');
    http.get<Blob>(src, { responseType: 'blob' })
      .then((response) => {
        if (!active) return;
        nextUrl = URL.createObjectURL(response.data);
        setObjectUrl(nextUrl);
      })
      .catch(() => {
        if (active) setError('视频媒体加载失败，请确认产物归属和服务状态后重试。');
      });
    return () => {
      active = false;
      if (nextUrl) URL.revokeObjectURL(nextUrl);
    };
  }, [protectedMedia, src]);

  if (error) return <Alert type="error" showIcon message={error} />;
  if (protectedMedia && !objectUrl) return <div style={{ minHeight: 180, display: 'grid', placeItems: 'center', alignContent: 'center', gap: 10 }}><Spin /><span>正在安全加载视频…</span></div>;
  return <video className={className} controls preload="metadata" src={protectedMedia ? objectUrl : src} title={title} style={{ width: '100%', maxHeight: '70vh' }}>当前浏览器无法播放这个视频资源。</video>;
}
