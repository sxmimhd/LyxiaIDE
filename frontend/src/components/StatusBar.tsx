interface StatusBarProps {
  workspaceName?: string;
  hasFile: boolean;
  line: number;
  column: number;
  language: string;
  error: string | null;
}

export function StatusBar({ workspaceName, hasFile, line, column, language, error }: StatusBarProps) {
  return (
    <footer className="flex h-6 shrink-0 items-center justify-between bg-[#007acc] px-3 text-xs text-white select-none">
      <div className="flex min-w-0 items-center gap-4">
        <span className="shrink-0">{workspaceName ?? "No folder"}</span>
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