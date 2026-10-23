/** The learning stage's geometry, stored with the same per-user scope as theme. */
export type DaryaSpaceMode = 'curve' | 'fold';

const STORAGE_PREFIX = 'darya_space_mode_v1';
const LAST_MODE_KEY = `${STORAGE_PREFIX}:last`;

function storageKey(userId?: number | null) {
  return `${STORAGE_PREFIX}:${userId ?? 'guest'}`;
}

export function readDaryaSpaceMode(userId?: number | null): DaryaSpaceMode {
  const stored = userId == null
    ? localStorage.getItem(LAST_MODE_KEY)
    : localStorage.getItem(storageKey(userId)) ?? localStorage.getItem(LAST_MODE_KEY);
  return stored === 'fold' ? 'fold' : 'curve';
}

export function persistDaryaSpaceMode(mode: DaryaSpaceMode, userId?: number | null) {
  localStorage.setItem(storageKey(userId), mode);
  localStorage.setItem(LAST_MODE_KEY, mode);
}
