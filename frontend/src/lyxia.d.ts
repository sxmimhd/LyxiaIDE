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
      };
    };
  }
}