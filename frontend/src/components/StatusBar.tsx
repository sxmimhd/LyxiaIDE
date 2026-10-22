interface StatusBarProps {
  workspaceName?: string;
  hasFile: boolean;
  line: number;
  column: number;
  language: string;
  error: string | null;
  onHistory: () => void;
  onTools: () => void;
}

export function StatusBar({ workspaceName, hasFile, line, column, language, error, onHistory, onTools }: StatusBarProps) {
  return (
    <footer className="flex h-6 shrink-0 items-center justify-between bg-[#007acc] px-3 text-xs text-white select-none">
      <div className="flex min-w-0 items-center gap-4">
        <span className="shrink-0">{workspaceName ?? "No folder"}</span>
        <button onClick={onHistory} className="shrink-0 rounded px-1.5 hover:bg-white/20" title="AI change history and rollback">
          ⟲ AI history
        </button>
        <button onClick={onTools} className="shrink-0 rounded px-1.5 hover:bg-white/20" title="AI tools and permissions">
          🛠 Tools
        </button>
        {error && (
          <span className="truncate rounded bg-red-600 px-2" title={error}>
            ⚠ {error}
          </span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-4">
        {hasFile && (
          <>
            <span>
              Ln {line}, Col {column}
            </span>
            <span className="capitalize">{language}</span>
          </>
        )}
      </div>
    </footer>
  );
}