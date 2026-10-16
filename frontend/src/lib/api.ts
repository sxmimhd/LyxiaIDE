export const API_URL = "http://127.0.0.1:8765";

/** POST JSON to the backend. Throws an Error carrying the server's message on failure. */
export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `Backend returned ${res.status}`;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      // not JSON: keep the generic message
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export interface IndexStatus {
  indexed: boolean;
  complete: boolean;
  chunk_count: number;
  indexed_at: string | null;
}

export interface IndexReport {
  total_chunks: number;
  added: number;
  updated: number;
  unchanged: number;
  deleted: number;
  total_seconds: number;
}

export const getIndexStatus = (workspace: string) => postJson<IndexStatus>("/index/status", { path: workspace });
export const buildIndex = (workspace: string) => postJson<IndexReport>("/index/build", { path: workspace });