import { useCallback, useEffect, useState } from "react";
import { ChatPanel } from "./components/ChatPanel";
import { EditPanel } from "./components/EditPanel";
import { Editor } from "./components/Editor";
import { Explorer } from "./components/Explorer";
import { ProjectEditPanel } from "./components/ProjectEditPanel";
import { StatusBar } from "./components/StatusBar";
import { TabBar } from "./components/TabBar";
import { confirmDiscardAll, resetEditors, saveFile } from "./lib/editorManager";
import type { WorkspaceInfo } from "./lyxia";
import { useWorkbench } from "./state/workbench";
import { HistoryPanel } from "./components/HistoryPanel";

export default function App() {
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const activeTab = useWorkbench((s) => s.activeTab);
  const cursor = useWorkbench((s) => s.cursor);
  const language = useWorkbench((s) => s.language);
  const error = useWorkbench((s) => s.error);

  // The main process remembers the last opened folder.
  useEffect(() => {
    void window.lyxia.workspace.get().then(setWorkspace);
  }, []);

  const openFolder = useCallback(async () => {
    if (!confirmDiscardAll()) return; // ask BEFORE the folder changes, while the old files are still valid
    const opened = await window.lyxia.workspace.open();
    if (opened) {
      resetEditors();
      setWorkspace(opened);
    }
  }, []);

  // Shortcuts that must work even when the editor is not focused.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const key = e.key.toLowerCase();
      if (key === "s") {
        e.preventDefault();
        void saveFile(useWorkbench.getState().activeTab);
      } else if (key === "o") {
        e.preventDefault();
        void openFolder();
      } else if (key === "k") {
        e.preventDefault();
        useWorkbench.getState().openEditBar();
      } else if (key === "i") {
        e.preventDefault();
        useWorkbench.getState().openProjectEdit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openFolder]);

  return (
    <div className="flex h-screen flex-col">
      <div className="flex min-h-0 flex-1">
        <Explorer workspace={workspace} onOpenFolder={() => void openFolder()} />
        <main className="flex min-w-0 flex-1 flex-col">
          <TabBar />
          <div className="relative min-h-0 flex-1">
            <Editor />
            <EditPanel />
            <ProjectEditPanel workspace={workspace?.root ?? null} />
            <HistoryPanel />
          </div>
        </main>
        <aside className="w-96 shrink-0 border-l border-[#3c3c3c]">
          <ChatPanel workspace={workspace?.root ?? null} />
        </aside>
      </div>
      <StatusBar
        workspaceName={workspace?.name}
        hasFile={activeTab !== null}
        line={cursor.line}
        column={cursor.column}
        language={language}
        error={error}
        onHistory={() => useWorkbench.getState().openHistory()}
      />
    </div>
  );
}