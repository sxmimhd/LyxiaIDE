import { useCallback, useEffect, useRef, useState } from "react";
import { buildIndex, getIndexStatus, type IndexStatus } from "../lib/api";
import { streamChat, type Source } from "../lib/chatStream";
import { getChatContext } from "../lib/editorManager";
import { useWorkbench } from "../state/workbench";

interface Message {
  role: "user" | "assistant";
  content: string;
  error?: string;
  notice?: string;
  sources?: Source[];
}

const baseName = (path: string) => path.split(/[\\/]/).pop() ?? path;

export function ChatPanel({ workspace }: { workspace: string | null }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [indexStatus, setIndexStatus] = useState<IndexStatus | null>(null);
  const [indexing, setIndexing] = useState(false);
  const [indexError, setIndexError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const activeTab = useWorkbench((s) => s.activeTab);
  const selection = useWorkbench((s) => s.selection);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const refreshStatus = useCallback(async () => {
    if (!workspace) return setIndexStatus(null);
    try {
      setIndexStatus(await getIndexStatus(workspace));
    } catch {
      setIndexStatus(null); // backend not reachable
    }
  }, [workspace]);

  useEffect(() => {
    setIndexError(null);
    void refreshStatus();
  }, [refreshStatus]);

  async function indexNow() {
    if (!workspace || indexing) return;
    setIndexing(true);
    setIndexError(null);
    try {
      await buildIndex(workspace); // can take a minute or more the first time
      await refreshStatus();
    } catch (e) {
      setIndexError(e instanceof Error ? e.message : String(e));
    } finally {
      setIndexing(false);
    }
  }

  // Apply a change to the last message (the assistant reply being streamed).
  const updateLast = (fn: (m: Message) => Message) =>
    setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)));

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;

    // Earlier turns go to the model so it remembers the conversation.
    const history = messages.filter((m) => m.content && !m.error).map(({ role, content }) => ({ role, content }));
    const context = getChatContext(); // the active file and selection, captured now

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      for await (const ev of streamChat(text, history, workspace, context, controller.signal)) {
        if (ev.type === "token") updateLast((m) => ({ ...m, content: m.content + ev.token }));
        else if (ev.type === "sources") updateLast((m) => ({ ...m, sources: ev.sources }));
        else updateLast((m) => ({ ...m, notice: ev.notice }));
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        updateLast((m) => ({ ...m, error: e instanceof Error ? e.message : String(e) }));
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  const contextLabel = activeTab
    ? `${baseName(activeTab)}${selection ? ` · lines ${selection.startLine}-${selection.endLine} selected` : ""}`
    : "no file open";

  return (
    <div className="flex h-full flex-col bg-[#252526] text-[13px] text-[#cccccc]">
      <div className="shrink-0 px-3 pt-2 pb-1">
        <div className="text-[11px] font-semibold tracking-wider text-[#858585] uppercase">AI Assistant</div>
        {workspace && (
          <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-[#858585]">
            <span className="truncate">
              {indexing
                ? "Indexing… this can take a minute"
                : indexStatus?.indexed
                  ? `Indexed · ${indexStatus.chunk_count} chunks${indexStatus.complete ? "" : " (incomplete)"}`
                  : indexStatus
                    ? "Folder not indexed"
                    : "Backend not reachable"}
            </span>
            <button
              onClick={() => void indexNow()}
              disabled={indexing || !indexStatus}
              className="shrink-0 rounded bg-[#0e639c] px-2 py-0.5 text-white hover:brightness-110 disabled:opacity-50"
            >
              {indexStatus?.indexed ? "Re-index" : "Index now"}
            </button>
          </div>
        )}
        {indexError && <div className="mt-1 text-[11px] text-red-300">⚠ {indexError}</div>}
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 pb-3">
        {messages.length === 0 && (
          <p className="text-[#858585]">
            Ask about your code. Select some code and ask "Explain this", or ask about the whole project.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i}>
            <div className="mb-0.5 text-[11px] font-semibold text-[#858585] uppercase">
              {m.role === "user" ? "You" : "Lyxia"}
            </div>
            {m.sources && m.sources.length > 0 && (
              <div className="mb-1 text-[11px] text-[#858585]">
                Sources:{" "}
                {m.sources.map((s, k) => (
                  <span key={k} className="mr-2 text-[#4ec9b0]">
                    {s.path}:{s.start_line}-{s.end_line}
                  </span>
                ))}
              </div>
            )}
            {m.notice && <div className="mb-1 text-[11px] text-yellow-500">{m.notice}</div>}
            <div className="whitespace-pre-wrap">{m.content}</div>
            {m.error && <div className="mt-1 rounded bg-red-900/40 px-2 py-1 text-red-300">⚠ {m.error}</div>}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="shrink-0 border-t border-[#3c3c3c] p-2">
        <div className="mb-1 truncate text-[11px] text-[#858585]" title={activeTab ?? undefined}>
          Context: <span className={selection ? "text-[#4ec9b0]" : ""}>{contextLabel}</span>
        </div>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          rows={3}
          placeholder="Ask Lyxia…  (Enter to send, Shift+Enter for a new line)"
          className="w-full resize-none rounded border border-[#3c3c3c] bg-[#3c3c3c] p-2 text-white outline-none focus:border-[#007acc]"
        />
        <div className="mt-1 flex justify-end">
          {streaming ? (
            <button onClick={() => abortRef.current?.abort()} className="rounded bg-[#5a1d1d] px-3 py-1 text-white hover:brightness-125">
              Stop
            </button>
          ) : (
            <button onClick={() => void send()} className="rounded bg-[#0e639c] px-3 py-1 text-white hover:brightness-110">
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  );
}