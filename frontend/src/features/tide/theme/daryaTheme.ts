export type DaryaTheme = 'kaki' | 'ocean';

const STORAGE_PREFIX = 'darya_theme_v1';
const LAST_THEME_KEY = `${STORAGE_PREFIX}:last`;

function storageKey(userId?: number | null) {
  return `${STORAGE_PREFIX}:${userId ?? 'guest'}`;
}

export function readDaryaTheme(userId?: number | null): DaryaTheme {
  const stored = userId == null
    ? localStorage.getItem(LAST_THEME_KEY)
    : localStorage.getItem(storageKey(userId)) ?? localStorage.getItem(LAST_THEME_KEY);
  return stored === 'ocean' ? 'ocean' : 'kaki';
}

export function persistDaryaTheme(theme: DaryaTheme, userId?: number | null) {
  localStorage.setItem(storageKey(userId), theme);
  localStorage.setItem(LAST_THEME_KEY, theme);
}
