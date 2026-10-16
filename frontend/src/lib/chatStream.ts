import { API_URL } from "./api";
import type { ChatContext } from "./editorManager";

export interface Source {
  path: string;
  name: string;
  start_line: number;
  end_line: number;
}

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
}

export type StreamEvent =
  | { type: "token"; token: string }
  | { type: "sources"; sources: Source[] }
  | { type: "notice"; notice: string };

/** Streams a reply from the backend: first the sources used, then tokens one at a time. */
export async function* streamChat(
  message: string,
  history: HistoryMessage[],
  workspace: string | null,
  context: ChatContext,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const { activeFile, selection } = context;
  const res = await fetch(`${API_URL}/llm/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      history,
      workspace,
      active_file: activeFile && {
        path: activeFile.path,
        language: activeFile.language,
        cursor_line: activeFile.cursorLine,
        excerpt: activeFile.excerpt,
        excerpt_start_line: activeFile.excerptStartLine,
      },
      selection: selection && {
        text: selection.text,
        start_line: selection.startLine,
        end_line: selection.endLine,
      },
    }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`Backend returned ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    // Network chunks don't line up with events: keep the incomplete tail for next time.
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const event of events) {
      const data = event.replace(/^data:\s*/, "").trim();
      if (!data) continue;
      if (data === "[DONE]") return;
      const parsed = JSON.parse(data) as { token?: string; error?: string; sources?: Source[]; notice?: string };
      if (parsed.error) throw new Error(parsed.error);
      if (parsed.sources) yield { type: "sources", sources: parsed.sources };
      if (parsed.notice) yield { type: "notice", notice: parsed.notice };
      if (parsed.token) yield { type: "token", token: parsed.token };
    }
  }
}