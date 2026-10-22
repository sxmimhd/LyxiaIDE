import { API_URL, postJson } from "./api";

export type ToolLevel = "READ" | "WRITE" | "EXECUTE" | "DESTRUCTIVE";

export interface ToolInfo {
  name: string;
  description: string;
  level: ToolLevel;
  parameters: { properties?: Record<string, { type?: string; default?: unknown }>; required?: string[] };
  example: Record<string, unknown>;
}

export interface ApprovalRequest {
  id: string;
  tool: string;
  level: ToolLevel;
  reason: string;
  args: Record<string, unknown>;
  can_always_allow: boolean;
}

export type ToolEvent =
  | ({ type: "approval_request" } & ApprovalRequest)
  | { type: "result"; ok: boolean; output: string; truncated: boolean; duration_ms: number }
  | { type: "denied"; reason: string; blocked: boolean };

export interface Permissions {
  commands: string[];
  tools: string[];
}

export interface AuditEntry {
  time: string;
  tool: string;
  level?: string;
  outcome: string;
  ok?: boolean;
  duration_ms?: number;
  reason?: string;
  args?: Record<string, string>;
}

export async function listTools(): Promise<ToolInfo[]> {
  const res = await fetch(`${API_URL}/tools`);
  if (!res.ok) throw new Error(`Backend returned ${res.status}`);
  return (await res.json()) as ToolInfo[];
}

/** Runs a tool as the AI agent would. If the action needs approval the stream pauses until approve() is called. */
export async function* streamToolRun(
  path: string,
  tool: string,
  args: Record<string, unknown>,
  signal?: AbortSignal,
): AsyncGenerator<ToolEvent> {
  const res = await fetch(`${API_URL}/tools/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, tool, arguments: args }),
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
      yield JSON.parse(data) as ToolEvent;
    }
  }
}

export const approve = (id: string, decision: "allow_once" | "allow_always" | "deny") =>
  postJson<{ ok: boolean }>("/tools/approve", { id, decision });
export const getPermissions = (path: string) => postJson<Permissions>("/tools/permissions", { path });
export const revokePermission = (path: string, what: { command?: string; tool?: string }) =>
  postJson<Permissions>("/tools/permissions/revoke", { path, ...what });
export const getAudit = (path: string, limit = 15) => postJson<AuditEntry[]>("/tools/audit", { path, limit });