import { API_URL, postJson } from "./api";
import type { FileChange } from "./projectEdit";
import type { ApprovalRequest } from "./tools";

export type AgentStatus = "finished" | "step_limit" | "stuck" | "error" | "stopped";

export type AgentEvent =
  | { type: "start"; max_steps: number }
  | { type: "status"; message: string }
  | { type: "step"; n: number; thought: string; action: string | null; args: Record<string, unknown>; error?: string }
  | { type: "observation"; n: number; ok: boolean; output: string }
  | ({ type: "approval_request" } & ApprovalRequest)
  | { type: "proposal"; id: string; instruction: string; changes: FileChange[] }
  | { type: "done"; status: AgentStatus; summary: string; steps: number }
  | { type: "error"; message: string };

export interface AgentBody {
  path: string; // project root
  task: string;
  active_file: string | null;
  open_files: { path: string; content: string }[];
  max_steps: number;
}

/** Streams the agent's trace. Closing the stream (abort) stops the agent on the server. */
export async function* streamAgent(body: AgentBody, signal?: AbortSignal): AsyncGenerator<AgentEvent> {
  const res = await fetch(`${API_URL}/agent/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = `Backend returned ${res.status}`;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      // keep the generic message
    }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const data = event.replace(/^data:\s*/, "").trim();
      if (!data) continue;
      if (data === "[DONE]") return;
      yield JSON.parse(data) as AgentEvent;
    }
  }
}

export interface ProposalAnswer {
  id: string;
  applied: string[]; // relative paths the user applied (and the IDE saved)
  updated_files: { path: string; content: string }[]; // their new text, so the agent's next edit starts from it
  errors: string[];
  rejected: boolean;
}

export const respondProposal = (answer: ProposalAnswer) => postJson<{ ok: boolean }>("/agent/proposals/respond", answer);