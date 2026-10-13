import { useEffect, useRef, useState } from "react";
import { streamChat, type Source } from "../lib/chatStream";

interface Message {
  role: "user" | "assistant";
  content: string;
  error?: string;
  notice?: string;
  sources?: Source[];
}

export function ChatPanel() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  // Apply a change to the last message (the assistant reply being streamed).
  const updateLast = (fn: (m: Message) => Message) =>
    setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)));

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;

    // Earlier turns go to the model so it remembers the conversation.
    const history = messages.filter((m) => m.content && !m.error).map(({ role, content }) => ({ role, content }));

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      for await (const ev of streamChat(text, history, controller.signal)) {
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

  return (
    <div className="flex h-full flex-col bg-[#252526] text-[13px] text-[#cccccc]">
      <div className="flex h-9 shrink-0 items-center px-3 text-[11px] font-semibold tracking-wider text-[#858585] uppercase">
        AI Assistant
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 pb-3">
        {messages.length === 0 && <p className="text-[#858585]">Ask something about your code to start.</p>}
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