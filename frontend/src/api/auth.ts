import { deleteData, getData, http, postData } from './client';
import type { CurrentUser } from '../stores/authStore';

interface BackendAuthPayload {
  user_id: number;
  username: string;
  email?: string | null;
  avatar_url?: string | null;
  token: string;
  has_profile?: boolean;
}

interface BackendUserPayload {
  user_id: number;
  username: string;
  email?: string | null;
  avatar_url?: string | null;
  has_profile?: boolean;
}

function mapUser(payload: BackendUserPayload): CurrentUser {
  return {
    userId: payload.user_id,
    username: payload.username,
    email: payload.email,
    avatarUrl: payload.avatar_url,
    hasProfile: Boolean(payload.has_profile)
  };
}

export const authApi = {
  async register(body: { username: string; password: string; email?: string | null }) {
    const payload = await postData<BackendAuthPayload, typeof body>('/api/auth/register', body);
    return { token: payload.token, user: mapUser(payload) };
  },

  async login(body: { username: string; password: string }) {
    const payload = await postData<BackendAuthPayload, typeof body>('/api/auth/login', body);
    return { token: payload.token, user: mapUser(payload) };
  },

  async currentUser() {
    return mapUser(await getData<BackendUserPayload>('/api/auth/me'));
  },

  async uploadAvatar(file: File): Promise<string | null> {
    const form = new FormData();
    form.append('file', file);
    // axios v1: 显式声明 multipart 后自动替换为带 boundary 的真实值
    const response = await http.post('/api/auth/avatar', form, {
      headers: { 'Content-Type': 'multipart/form-data' }
    });
    const body = response.data as { data?: { avatar_url?: string | null } };
    return body.data?.avatar_url ?? null;
  },

  async deleteAvatar(): Promise<void> {
    await deleteData('/api/auth/avatar');
  },

  async changePassword(body: { current_password: string; new_password: string }) {
    return postData<{ message?: string }, typeof body>('/api/auth/change-password', body);
  }
};
