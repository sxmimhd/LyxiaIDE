import { useEffect, useRef } from "react";
import { getModel, getViewState, registerEditor, saveFile, saveViewState } from "../lib/editorManager";
import { monaco } from "../lib/monacoSetup";
import { useWorkbench } from "../state/workbench";

function publishSelection(editor: monaco.editor.IStandaloneCodeEditor) {
  const sel = editor.getSelection();
  useWorkbench
    .getState()
    .setSelection(sel && !sel.isEmpty() ? { startLine: sel.startLineNumber, endLine: sel.endLineNumber } : null);
}

export function Editor() {
  const container = useRef<HTMLDivElement>(null);
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const currentPath = useRef<string | null>(null); // the file the editor is showing right now
  const activeTab = useWorkbench((s) => s.activeTab);

  // Create the editor once.
  useEffect(() => {
    const editor = monaco.editor.create(container.current!, {
      model: null,
      theme: "vs-dark",
      automaticLayout: true,
      minimap: { enabled: true },
      fontSize: 14,
      fontFamily: "Consolas, 'Cascadia Code', monospace",
      scrollBeyondLastLine: false,
    });
    editorRef.current = editor;
    registerEditor(editor);

    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => void saveFile(currentPath.current));
    const cursorSub = editor.onDidChangeCursorPosition((e) =>
      useWorkbench.getState().setCursor(e.position.lineNumber, e.position.column),
    );
    const selectionSub = editor.onDidChangeCursorSelection(() => publishSelection(editor));

    return () => {
      cursorSub.dispose();
      selectionSub.dispose();
      registerEditor(null);
      editor.dispose();
      editorRef.current = null;
      currentPath.current = null;
    };
  }, []);

  // Show the active tab's model, remembering cursor/scroll per file.
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;

    if (currentPath.current) saveViewState(currentPath.current, editor.saveViewState());

    const model = activeTab ? getModel(activeTab) : undefined;
    editor.setModel(model ?? null);
    currentPath.current = model ? activeTab : null;

    if (model && activeTab) {
      const state = getViewState(activeTab);
      if (state) editor.restoreViewState(state);
      editor.focus();
      useWorkbench.getState().setLanguage(model.getLanguageId());
      const position = editor.getPosition();
      if (position) useWorkbench.getState().setCursor(position.lineNumber, position.column);
    }
    publishSelection(editor);
  }, [activeTab]);

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="absolute inset-0" />
      {!activeTab && (
        <div className="absolute inset-0 grid place-content-center bg-[#1e1e1e] text-center text-[#858585]">
          <div className="text-2xl font-light">LyxiaIDE</div>
          <div className="mt-2 text-[13px]">Open a folder, then click a file to start</div>
        </div>
      )}
    </div>
  );
}