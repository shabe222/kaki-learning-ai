/**
 * 数字人形态设置：三态。
 *
 * - `auto`（默认）：跟随界面主题 —— 柿子主题配柿小吉，海洋主题配海星。
 * - `persimmon` / `starfish`：用户在设置里明确选定，之后不再跟主题走。
 *
 * 与主题、黑板排版同一套按用户持久化的路子。
 */

export type DaryaMascotForm = 'persimmon' | 'starfish';
export type DaryaMascotFormSetting = DaryaMascotForm | 'auto';
export type DaryaUiTheme = 'kaki' | 'ocean';

const STORAGE_PREFIX = 'darya_mascot_form_v1';
const LAST_FORM_KEY = `${STORAGE_PREFIX}:last`;

function storageKey(userId?: number | null) {
  return `${STORAGE_PREFIX}:${userId ?? 'guest'}`;
}

export function readDaryaMascotFormSetting(userId?: number | null): DaryaMascotFormSetting {
  const stored = userId == null
    ? localStorage.getItem(LAST_FORM_KEY)
    : localStorage.getItem(storageKey(userId)) ?? localStorage.getItem(LAST_FORM_KEY);
  return stored === 'persimmon' || stored === 'starfish' ? stored : 'auto';
}

export function persistDaryaMascotFormSetting(setting: DaryaMascotFormSetting, userId?: number | null) {
  localStorage.setItem(storageKey(userId), setting);
  localStorage.setItem(LAST_FORM_KEY, setting);
}

/** 设置为 auto 时按主题定形态；明确选定则以用户的选择为准。 */
export function resolveMascotForm(setting: DaryaMascotFormSetting, theme: DaryaUiTheme): DaryaMascotForm {
  if (setting === 'auto') return theme === 'ocean' ? 'starfish' : 'persimmon';
  return setting;
}
