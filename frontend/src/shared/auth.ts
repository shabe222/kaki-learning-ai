const USER_TOKEN_KEY = 'token';
const ADMIN_TOKEN_KEY = 'admin_token';
const ADMIN_ROLE_KEY = 'admin_role';

export function getUserToken() {
  return localStorage.getItem(USER_TOKEN_KEY);
}

export function setUserSession(token: string, user: unknown) {
  localStorage.setItem(USER_TOKEN_KEY, token);
  localStorage.setItem('kaki_user', JSON.stringify(user));
}

export function getAdminToken() {
  return localStorage.getItem(ADMIN_TOKEN_KEY);
}

export function isAdminAuthed() {
  return Boolean(getAdminToken()) && localStorage.getItem(ADMIN_ROLE_KEY) === 'admin';
}

export function setAdminSession(account: string, token: string, user?: unknown) {
  localStorage.setItem(ADMIN_TOKEN_KEY, token);
  localStorage.setItem(ADMIN_ROLE_KEY, 'admin');
  localStorage.setItem('admin_user', JSON.stringify({ account, role: 'admin', name: '管理员', user }));
}

export function clearAdminSession() {
  localStorage.removeItem(ADMIN_TOKEN_KEY);
  localStorage.removeItem(ADMIN_ROLE_KEY);
  localStorage.removeItem('admin_user');
}
