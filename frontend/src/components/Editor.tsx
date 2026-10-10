import { useEffect, useRef } from "react";
import { monaco } from "../lib/monacoSetup";

const SAMPLE = `export function login(user: string, password: string) {
  if (!user || !password) {
    throw new Error("Missing credentials");
  }
  return { user, token: crypto.randomUUID() };
}
`;

interface EditorProps {
  onCursorChange?: (line: number, column: number) => void;
}

export function Editor({ onCursorChange }: EditorProps) {
  const container = useRef<HTMLDivElement>(null);

  // Keep the latest callback in a ref so the editor is created only once.
  const onCursorRef = useRef(onCursorChange);
  onCursorRef.current = onCursorChange;

  useEffect(() => {
    const editor = monaco.editor.create(container.current!, {
      value: SAMPLE,
      language: "typescript",
      theme: "vs-dark",
      automaticLayout: true,
      minimap: { enabled: true },
      fontSize: 14,
      fontFamily: "Consolas, 'Cascadia Code', monospace",
      scrollBeyondLastLine: false,
    });

    const sub = editor.onDidChangeCursorPosition((e) => {
      onCursorRef.current?.(e.position.lineNumber, e.position.column);
    });

    return () => {
      sub.dispose();
      editor.dispose();
    };
  }, []);

  return <div ref={container} className="h-full w-full" />;
}