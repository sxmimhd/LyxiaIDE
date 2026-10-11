import { useState } from "react";
import { ChatPanel } from "./components/ChatPanel";
import { Editor } from "./components/Editor";
import { StatusBar } from "./components/StatusBar";

export default function App() {
  const [cursor, setCursor] = useState({ line: 1, column: 1 });

  return (
    <div className="flex h-screen flex-col">
      <div className="flex min-h-0 flex-1">
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