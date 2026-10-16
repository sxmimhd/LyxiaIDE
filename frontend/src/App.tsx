import { useEffect, useState } from "react";
import { ChatPanel } from "./components/ChatPanel";
import { Editor } from "./components/Editor";
import { Explorer } from "./components/Explorer";
import { StatusBar } from "./components/StatusBar";
import type { WorkspaceInfo } from "./lyxia";

export default function App() {
  const [cursor, setCursor] = useState({ line: 1, column: 1 });
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);

  // The main process remembers the last opened folder.
  useEffect(() => {
    void window.lyxia.workspace.get().then(setWorkspace);
  }, []);

  const openFolder = async () => {
    const opened = await window.lyxia.workspace.open();
    if (opened) setWorkspace(opened);
  };

  return (
    <div className="flex h-screen flex-col">
      <div className="flex min-h-0 flex-1">
        <Explorer workspace={workspace} onOpenFolder={() => void openFolder()} />
        <div className="min-w-0 flex-1">
          <Editor onCursorChange={(line, column) => setCursor({ line, column })} />
        </div>
        <aside className="w-96 shrink-0 border-l border-[#3c3c3c]">
          <ChatPanel />
        </aside>
      </div>
      <StatusBar line={cursor.line} column={cursor.column} language="typescript" />
    </div>
  );
}