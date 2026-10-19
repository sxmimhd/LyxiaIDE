// Types for the API that electron/preload.cjs exposes on `window.lyxia`.
export interface DirEntry {
  name: string;
  path: string; // absolute path
  isDir: boolean;
}

export interface WorkspaceInfo {
  root: string;
  name: string;
}

export interface HistoryFileMeta {
  path: string; // absolute
  status: "modified" | "created";
  rolledBack: boolean;
}

export interface HistoryMeta {
  id: string;
  createdAt: string; // ISO time
  root: string;
  instruction: string;
  source: "project" | "file"; // multi-file edit (Ctrl+I) or single-file edit (Ctrl+K)
  files: HistoryFileMeta[];
}

export interface HistoryFileData {
  path: string;
  before: string | null; // null for a file the AI created
  after: string;
}

export interface HistoryRecord extends HistoryMeta {
  data: HistoryFileData[];
}

export interface HistoryInput {
  instruction: string;
  source: "project" | "file";
  files: { path: string; status: "modified" | "created"; before: string | null; after: string }[];
}

declare global {
  interface Window {
    lyxia: {
      ping(message: string): Promise<{ echo: string; at: number }>;
      workspace: {
        get(): Promise<WorkspaceInfo | null>;
        /** Shows the native folder picker. Resolves to null if the user cancels. */
        open(): Promise<WorkspaceInfo | null>;
        readDir(path: string): Promise<DirEntry[]>;
        readFile(path: string): Promise<string>;
        writeFile(path: string, content: string): Promise<void>;
        /** Creates a NEW file (and missing folders). Rejects if it already exists. */
        createFile(path: string, content: string): Promise<void>;
        /** Moves a file to the Recycle Bin. */
        trash(path: string): Promise<void>;
      };
      history: {
        save(input: HistoryInput): Promise<string>;
        list(): Promise<HistoryMeta[]>;
        get(id: string): Promise<HistoryRecord>;
        markRolledBack(id: string, paths: string[]): Promise<void>;
      };
    };
  }
}