import { useEffect, useRef } from "react";
import { monaco } from "../lib/monacoSetup";

const SAMPLE = `export function login(user: string, password: string) {
  if (!user || !password) {
    throw new Error("Missing credentials");
  }
  return { user, token: crypto.randomUUID() };
}
`;

export function Editor() {
  const container = useRef<HTMLDivElement>(null);

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
    return () => editor.dispose();
  }, []);

  return <div ref={container} className="h-full w-full" />;
}