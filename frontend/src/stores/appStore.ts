import { create } from 'zustand';

interface AppState {
  collapsed: boolean;
  keyword: string;
  setCollapsed: (value: boolean) => void;
  setKeyword: (value: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  collapsed: false,
  keyword: '',
  setCollapsed: (collapsed) => set({ collapsed }),
  setKeyword: (keyword) => set({ keyword })
}));
