import { API_URL } from "./api";

export interface EditOutcome {
  status: string; // applied | not_found | ambiguous | empty_search | no_edits
  message: string;
  search_preview: string;
}

export interface FileChange {
  path: string; // relative to the project root
  abs_path: string;
  language: string;
  status: "modified" | "created" | "unchanged" | "failed";
  original: string;
  new_content: string;
  applied: number;
  failed: number;
  explanation: string;
  outcomes: EditOutcome[];
}

export type ProjectEvent =
  | { type: "status"; message: string }
  | { type: "plan"; files: { path: string; is_new: boolean }[]; notes: string[] }
  | { type: "file"; change: FileChange }
  | { type: "done"; modified: number; created: number; unchanged: number; failed: number }
  | { type: "error"; message: string; notes?: string[]; raw?: string };

export interface ProjectEditBody {
  path: string; // project root
  instruction: string;
  active_file: string | null;
  open_files: { path: string; content: string }[];
}

/** Streams progress from POST /edit/project. Nothing is written to disk by the backend. */
export async function* streamProjectEdit(body: ProjectEditBody, signal?: AbortSignal): AsyncGenerator<ProjectEvent> {
  const res = await fetch(`${API_URL}/edit/project`, {
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
      // not JSON: keep the generic message
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
      yield JSON.parse(data) as ProjectEvent;
    }
  }
}