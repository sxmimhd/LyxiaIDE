interface StatusBarProps {
  line: number;
  column: number;
  language: string;
}

export function StatusBar({ line, column, language }: StatusBarProps) {
  return (
    <footer className="flex h-6 shrink-0 items-center justify-between bg-[#007acc] px-3 text-xs text-white select-none">
      <span>LyxiaIDE</span>
      <div className="flex items-center gap-4">
        <span>
          Ln {line}, Col {column}
        </span>
        <span className="capitalize">{language}</span>
      </div>
    </footer>
  );
}