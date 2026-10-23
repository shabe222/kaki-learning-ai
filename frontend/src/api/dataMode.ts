export type DataMode = 'real' | 'mock' | 'fallback';

export function getDataMode(): DataMode {
  const mode = import.meta.env.VITE_DATA_MODE as DataMode | undefined;
  if (mode === 'real' || mode === 'mock' || mode === 'fallback') {
    return mode;
  }

  return import.meta.env.DEV ? 'fallback' : 'real';
}

export function canUseFallbackData() {
  return import.meta.env.DEV && getDataMode() === 'fallback';
}

export function isMockMode() {
  return getDataMode() === 'mock';
}
