const API_URL = "http://127.0.0.1:8765";

/** Streams a reply from the backend, yielding one token at a time. */
export async function* streamChat(message: string, signal?: AbortSignal): AsyncGenerator<string> {
  const res = await fetch(`${API_URL}/llm/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
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
      const parsed = JSON.parse(data) as { token?: string; error?: string };
      if (parsed.error) throw new Error(parsed.error);
      if (parsed.token) yield parsed.token;
    }
  }
}