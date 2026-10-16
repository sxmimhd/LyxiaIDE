import { useEffect, useRef } from "react";
import { monaco } from "../lib/monacoSetup";

interface DiffReviewProps {
  original: string;
  modified: string;
  language: string;
}

/** A read-only inline diff: red lines are removed, green lines are added. */
export function DiffReview({ original, modified, language }: DiffReviewProps) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const editor = monaco.editor.createDiffEditor(container.current!, {
      theme: "vs-dark",
      readOnly: true,
      originalEditable: false,
      renderSideBySide: false,
      automaticLayout: true,
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      fontSize: 14,
      fontFamily: "Consolas, 'Cascadia Code', monospace",
    });
    const originalModel = monaco.editor.createModel(original, language);
    const modifiedModel = monaco.editor.createModel(modified, language);
    editor.setModel({ original: originalModel, modified: modifiedModel });

    return () => {
      editor.dispose();
      originalModel.dispose();
      modifiedModel.dispose();
    };
  }, [original, modified, language]);

  return <div ref={container} className="h-full w-full" />;
}