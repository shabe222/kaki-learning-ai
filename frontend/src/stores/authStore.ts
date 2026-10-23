import { create } from 'zustand';

export interface CurrentUser {
  userId: number;
  username: string;
  email?: string | null;
  avatarUrl?: string | null;
  hasProfile: boolean;
}

interface AuthState {
  token: string | null;
  user: CurrentUser | null;
  initialized: boolean;
  // 头像 objectURL 缓存版本：上传/恢复默认后 +1，强制所有 UserAvatar 重新拉取
  avatarVersion: number;
  setSession: (token: string, user: CurrentUser) => void;
  setUser: (user: CurrentUser | null) => void;
  setInitialized: (initialized: boolean) => void;
  applyAvatar: (avatarUrl: string | null) => void;
  clearSession: () => void;
}

function readStoredUser(): CurrentUser | null {
  const raw = localStorage.getItem('kaki_user');
  if (!raw) return null;

  try {
    return JSON.parse(raw) as CurrentUser;
  } catch {
    return null;
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem('token'),
  user: readStoredUser(),
  initialized: false,
  avatarVersion: 0,
  setSession: (token, user) => {
    localStorage.setItem('token', token);
    localStorage.setItem('kaki_user', JSON.stringify(user));
    localStorage.setItem('auth_token', token);
    localStorage.setItem('ml_auth_token', token);
    localStorage.setItem('ml_current_user_id', String(user.userId));
    localStorage.setItem('ml_user', JSON.stringify({
      id: user.userId,
      user_id: user.userId,
      username: user.username,
      nickname: user.username,
      email: user.email
    }));
    localStorage.setItem('kaki_profile_status', user.hasProfile ? 'complete' : 'pending');
    set({ token, user, initialized: true });
  },
  setUser: (user) => {
    if (user) {
      localStorage.setItem('kaki_user', JSON.stringify(user));
      localStorage.setItem('kaki_profile_status', user.hasProfile ? 'complete' : 'pending');
    } else {
      localStorage.removeItem('kaki_user');
    }
    set({ user, initialized: true });
  },
  setInitialized: (initialized) => set({ initialized }),
  applyAvatar: (avatarUrl) => set((state) => {
    if (!state.user) return { avatarVersion: state.avatarVersion + 1 };
    const user = { ...state.user, avatarUrl };
    localStorage.setItem('kaki_user', JSON.stringify(user));
    return { user, avatarVersion: state.avatarVersion + 1 };
  }),
  clearSession: () => {
    [
      'token',
      'kaki_user',
      'kaki_profile_status',
      'kaki_learning_profile',
      'auth_token',
      'ml_auth_token',
      'ml_current_user_id',
      'ml_user',
      'ml_thread_id'
    ].forEach((key) => localStorage.removeItem(key));
    set({ token: null, user: null, initialized: true });
  }
}));
