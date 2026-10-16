import { create } from "zustand";

interface Workbench {
  tabs: string[]; // open files, in tab order (absolute paths)
  activeTab: string | null;
  dirty: Record<string, boolean>; // path -> has unsaved changes
  cursor: { line: number; column: number };
  language: string; // Monaco language id of the active file
  error: string | null;

  addTab: (path: string) => void;
  removeTab: (path: string) => void;
  setActiveTab: (path: string | null) => void;
  setDirty: (path: string, dirty: boolean) => void;
  setCursor: (line: number, column: number) => void;
  setLanguage: (language: string) => void;
  setError: (error: string | null) => void;
  reset: () => void;
}

export const useWorkbench = create<Workbench>((set) => ({
  tabs: [],
  activeTab: null,
  dirty: {},
  cursor: { line: 1, column: 1 },
  language: "plaintext",
  error: null,

  addTab: (path) => set((s) => ({ tabs: s.tabs.includes(path) ? s.tabs : [...s.tabs, path], activeTab: path })),
  removeTab: (path) =>
    set((s) => {
      const index = s.tabs.indexOf(path);
      const tabs = s.tabs.filter((t) => t !== path);
      // Closing the active tab activates its neighbour.
      const activeTab = s.activeTab === path ? (tabs[Math.min(index, tabs.length - 1)] ?? null) : s.activeTab;
      const dirty = { ...s.dirty };
      delete dirty[path];
      return { tabs, activeTab, dirty };
    }),
  setActiveTab: (activeTab) => set({ activeTab }),
  setDirty: (path, isDirty) => set((s) => (s.dirty[path] === isDirty ? s : { dirty: { ...s.dirty, [path]: isDirty } })),
  setCursor: (line, column) => set({ cursor: { line, column } }),
  setLanguage: (language) => set({ language }),
  setError: (error) => set({ error }),
  reset: () => set({ tabs: [], activeTab: null, dirty: {}, error: null }),
}));