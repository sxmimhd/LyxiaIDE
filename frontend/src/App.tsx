import { useState } from "react";
import { Editor } from "./components/Editor";
import { StatusBar } from "./components/StatusBar";

export default function App() {
  const [cursor, setCursor] = useState({ line: 1, column: 1 });

  return (
    <div className="flex h-screen flex-col">
      <div className="min-h-0 flex-1">
        <Editor onCursorChange={(line, column) => setCursor({ line, column })} />
      </div>
      <StatusBar line={cursor.line} column={cursor.column} language="typescript" />
    </div>
  );
}