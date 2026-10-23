import { useEffect, useState } from 'react';
import { Avatar } from 'antd';
import { http } from '../api/client';
import { useAuthStore } from '../stores/authStore';

/**
 * 头像图片需要 Authorization 头，<img> 标签发不出来，
 * 统一走 http 客户端拉 blob -> objectURL，并按 用户+版本 缓存。
 * 顶部导航与画像页共用本组件，保证两处渲染的是同一事实。
 */
const objectUrlCache = new Map<string, string>();

export function useAvatarObjectUrl(): string | null {
  const userId = useAuthStore((state) => state.user?.userId);
  const avatarUrl = useAuthStore((state) => state.user?.avatarUrl);
  const avatarVersion = useAuthStore((state) => state.avatarVersion);
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (!userId || !avatarUrl) {
      setSrc(null);
      return;
    }
    const key = `${userId}:${avatarUrl}:${avatarVersion}`;
    const cached = objectUrlCache.get(key);
    if (cached) {
      setSrc(cached);
      return;
    }
    let cancelled = false;
    http
      .get(avatarUrl, { responseType: 'blob' })
      .then((response) => {
        const objectUrl = URL.createObjectURL(response.data as Blob);
        objectUrlCache.set(key, objectUrl);
        if (!cancelled) setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setSrc(null);
      });
    return () => {
      cancelled = true;
    };
  }, [userId, avatarUrl, avatarVersion]);

  return src;
}

interface UserAvatarProps {
  size?: number;
  className?: string;
}

export function UserAvatar({ size = 42, className }: UserAvatarProps) {
  const username = useAuthStore((state) => state.user?.username);
  const src = useAvatarObjectUrl();
  return (
    <Avatar
      size={size}
      className={className}
      src={src ?? undefined}
      style={src ? undefined : { background: 'linear-gradient(135deg, #e8956a, #ca6b43)', color: '#fff7ee', fontWeight: 700 }}
    >
      {username?.slice(0, 1)?.toUpperCase() || '柿'}
    </Avatar>
  );
}
