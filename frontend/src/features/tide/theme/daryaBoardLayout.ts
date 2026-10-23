/**
 * 黑板在对话区里怎么排：设置里选，本机按用户记住。
 * 和主题、声音用的是同一套按用户持久化的路子。
 */

export type DaryaBoardLayout = 'split' | 'full';

const STORAGE_PREFIX = 'darya_board_layout_v1';
const LAST_LAYOUT_KEY = `${STORAGE_PREFIX}:last`;

function storageKey(userId?: number | null) {
  return `${STORAGE_PREFIX}:${userId ?? 'guest'}`;
}

export function readDaryaBoardLayout(userId?: number | null): DaryaBoardLayout {
  const stored = userId == null
    ? localStorage.getItem(LAST_LAYOUT_KEY)
    : localStorage.getItem(storageKey(userId)) ?? localStorage.getItem(LAST_LAYOUT_KEY);
  return stored === 'full' ? 'full' : 'split';
}

export function persistDaryaBoardLayout(layout: DaryaBoardLayout, userId?: number | null) {
  localStorage.setItem(storageKey(userId), layout);
  localStorage.setItem(LAST_LAYOUT_KEY, layout);
}
