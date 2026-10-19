import { useCallback, useEffect, useState } from "react";
import type { HistoryMeta, HistoryRecord } from "../lyxia";
import { rollbackFile, type RollbackResult } from "../lib/editorManager";
import { useWorkbench } from "../state/workbench";
import { DiffReview } from "./DiffReview";

const LANGUAGES: Record<string, string> = {
  ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
  py: "python", json: "json", md: "markdown", css: "css", html: "html", cs: "csharp", java: "java",
  go: "go", rs: "rust", sql: "sql", yml: "yaml", yaml: "yaml", php: "php", cpp: "cpp", c: "c",
};
const languageOf = (path: string) => LANGUAGES[path.split(".").pop()?.toLowerCase() ?? ""] ?? "plaintext";
const baseName = (path: string) => path.split(/[\\/]/).pop() ?? path;
const errorText = (e: unknown) =>
  e instanceof Error ? e.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, "") : String(e);

export function HistoryPanel() {
  const open = useWorkbench((s) => s.historyOpen);
  const close = useWorkbench((s) => s.closeHistory);

  const [items, setItems] = useState<HistoryMeta[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [record, setRecord] = useState<HistoryRecord | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [results, setResults] = useState<RollbackResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadList = useCallback(async (select?: string | null) => {
    try {
      const list = await window.lyxia.history.list();
      setItems(list);
      setSelectedId((prev) => select ?? prev ?? list[0]?.id ?? null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setResults([]);
    setError(null);
    setSelectedId(null);
    void loadList(null);
  }, [open, loadList]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, close]);

  useEffect(() => {
    if (!selectedId) {
      setRecord(null);
      return;
    }
    let cancelled = false;
    window.lyxia.history
      .get(selectedId)
      .then((r) => {
        if (cancelled) return;
        setRecord(r);
        setSelectedFile(r.files[0]?.path ?? null);
        setResults([]);
      })
      .catch((e) => !cancelled && setError(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  async function rollback(force: boolean, only?: Set<string>) {
    if (!record) return;
    setBusy(true);
    setError(null);
    const status = new Map(record.files.map((f) => [f.path, f]));
    const out: RollbackResult[] = [];
    const succeeded: string[] = [];
    for (const f of record.data) {
      const meta = status.get(f.path);
      if (!meta || meta.rolledBack || (only && !only.has(f.path))) continue;
      const result = await rollbackFile({ path: f.path, status: meta.status, before: f.before, after: f.after }, force);
      out.push(result);
      if (result.ok) succeeded.push(f.path);
    }
    try {
      if (succeeded.length > 0) await window.lyxia.history.markRolledBack(record.id, succeeded);
      setRecord(await window.lyxia.history.get(record.id));
      await loadList(record.id);
    } catch (e) {
      setError(errorText(e));
    }
    setResults(out);
    setBusy(false);
  }

  if (!open) return null;

  const allDone = record?.files.every((f) => f.rolledBack) ?? false;
  const conflicts = results.filter((r) => r.conflict);
  const current = record?.data.find((f) => f.path === selectedFile) ?? null;

  return (
    <div className="absolute inset-0 z-40 flex flex-col bg-[#1e1e1e] text-[13px] text-[#cccccc]">
      <div className="flex shrink-0 items-center justify-between border-b border-[#3c3c3c] bg-[#252526] px-3 py-2">
        <div className="font-semibold">AI change history</div>
        <button onClick={close} disabled={busy} className="rounded px-2 py-0.5 hover:bg-white/10 disabled:opacity-40">
          ✕
        </button>
      </div>

      {items.length === 0 ? (
        <div className="p-4 text-[#858585]">
          No AI changes recorded for this folder yet. Changes you apply with Ctrl+K or Ctrl+I appear here, and can be rolled back.
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <div className="w-80 shrink-0 overflow-y-auto border-r border-[#3c3c3c] bg-[#252526]">
            {items.map((it) => {
              const rolled = it.files.every((f) => f.rolledBack);
              return (
                <div
                  key={it.id}
                  onClick={() => setSelectedId(it.id)}
                  className={`cursor-pointer border-b border-[#2d2d2d] px-3 py-2 ${selectedId === it.id ? "bg-[#37373d]" : "hover:bg-[#2a2d2e]"}`}
                >
                  <div className="truncate" title={it.instruction}>
                    {it.instruction || "(no description)"}
                  </div>
                  <div className="mt-0.5 flex items-center justify-between text-[11px] text-[#858585]">
                    <span>{new Date(it.createdAt).toLocaleString()}</span>
                    <span>
                      {it.files.length} file{it.files.length > 1 ? "s" : ""}
                      {rolled && <span className="ml-1 text-yellow-500">↩ rolled back</span>}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="flex min-w-0 flex-1 flex-col">
            {record && (
              <>
                <div className="shrink-0 border-b border-[#3c3c3c] bg-[#252526] px-3 py-2">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-semibold">{record.instruction}</div>
                      <div className="text-[11px] text-[#858585]">
                        {new Date(record.createdAt).toLocaleString()} · {record.source === "project" ? "multi-file edit" : "single-file edit"}
                      </div>
                    </div>
                    <button
                      onClick={() => void rollback(false)}
                      disabled={busy || allDone}
                      className="shrink-0 rounded bg-[#0e639c] px-3 py-1 text-white hover:brightness-110 disabled:opacity-40"
                    >
                      {busy ? "Rolling back…" : allDone ? "Rolled back" : "Roll back this change"}
                    </button>
                  </div>

                  {results.map((r, i) => (
                    <div key={i} className={`mt-1 text-[12px] ${r.ok ? "text-[#73c991]" : r.conflict ? "text-yellow-500" : "text-red-300"}`}>
                      {r.ok ? "✓" : r.conflict ? "⚠" : "✕"} {baseName(r.path)}: {r.message}
                    </div>
                  ))}
                  {conflicts.length > 0 && (
                    <div className="mt-2 flex items-center gap-3">
                      <button
                        onClick={() => void rollback(true, new Set(conflicts.map((c) => c.path)))}
                        disabled={busy}
                        className="rounded bg-[#5a1d1d] px-3 py-1 text-white hover:brightness-125 disabled:opacity-40"
                      >
                        Overwrite my changes and roll back
                      </button>
                      <span className="text-[11px] text-[#858585]">Your edits to those files will be lost.</span>
                    </div>
                  )}
                  {error && <div className="mt-1 text-[12px] text-red-300">⚠ {error}</div>}
                </div>

                <div className="flex min-h-0 flex-1">
                  <div className="w-56 shrink-0 overflow-y-auto border-r border-[#3c3c3c]">
                    {record.files.map((f) => (
                      <div
                        key={f.path}
                        onClick={() => setSelectedFile(f.path)}
                        title={f.path}
                        className={`flex cursor-pointer items-center gap-2 px-2 py-1.5 ${selectedFile === f.path ? "bg-[#37373d]" : "hover:bg-[#2a2d2e]"}`}
                      >
                        <span className={`w-3 shrink-0 text-center font-bold ${f.status === "created" ? "text-[#73c991]" : "text-[#e2c08d]"}`}>
                          {f.status === "created" ? "N" : "M"}
                        </span>
                        <span className="min-w-0 flex-1 truncate">{baseName(f.path)}</span>
                        {f.rolledBack && <span className="text-yellow-500">↩</span>}
                      </div>
                    ))}
                  </div>
                  <div className="min-w-0 flex-1">
                    {current && (
                      <DiffReview original={current.before ?? ""} modified={current.after} language={languageOf(current.path)} />
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}