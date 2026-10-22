import { create } from "zustand";

interface Workbench {
  tabs: string[]; // open files, in tab order (absolute paths)
  activeTab: string | null;
  dirty: Record<string, boolean>; // path -> has unsaved changes
  cursor: { line: number; column: number };
  selection: { startLine: number; endLine: number } | null; // selected lines in the active file
  language: string; // Monaco language id of the active file
  error: string | null;
  editBarOpen: boolean; // the "Edit with AI" bar (Ctrl+K)
  projectEditOpen: boolean; // the multi-file edit panel (Ctrl+I)
  historyOpen: boolean; // the AI change history panel
  toolsOpen: boolean; // the AI tools panel

  addTab: (path: string) => void;
  removeTab: (path: string) => void;
  setActiveTab: (path: string | null) => void;
  setDirty: (path: string, dirty: boolean) => void;
  setCursor: (line: number, column: number) => void;
  setSelection: (selection: { startLine: number; endLine: number } | null) => void;
  setLanguage: (language: string) => void;
  setError: (error: string | null) => void;
  openEditBar: () => void;
  closeEditBar: () => void;
  openProjectEdit: () => void;
  closeProjectEdit: () => void;
  openHistory: () => void;
  closeHistory: () => void;
  openTools: () => void;
  closeTools: () => void;
  reset: () => void;
}

export const useWorkbench = create<Workbench>((set) => ({
  tabs: [],
  activeTab: null,
  dirty: {},
  cursor: { line: 1, column: 1 },
  selection: null,
  language: "plaintext",
  error: null,
  editBarOpen: false,
  projectEditOpen: false,
  historyOpen: false,
  toolsOpen: false,
  

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
  setSelection: (selection) => set({ selection }),
  setLanguage: (language) => set({ language }),
  setError: (error) => set({ error }),
  openEditBar: () => set({ editBarOpen: true }),
  closeEditBar: () => set({ editBarOpen: false }),
  openProjectEdit: () => set({ projectEditOpen: true }),
  closeProjectEdit: () => set({ projectEditOpen: false }),
  openHistory: () => set({ historyOpen: true }),
  closeHistory: () => set({ historyOpen: false }),
  openTools: () => set({ toolsOpen: true }),
  closeTools: () => set({ toolsOpen: false }),
  reset: () =>
    set({
      tabs: [], activeTab: null, dirty: {}, selection: null, error: null,
      editBarOpen: false, projectEditOpen: false, historyOpen: false, toolsOpen: false,
    }),
}));