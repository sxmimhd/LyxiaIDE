import { useCallback, useEffect, useRef, useState } from "react";
import { applyFileChange, getOpenFiles, recordChangeSet } from "../lib/editorManager";
import { streamProjectEdit, type FileChange } from "../lib/projectEdit";
import { useWorkbench } from "../state/workbench";
import { DiffReview } from "./DiffReview";

type Phase = "input" | "running" | "review";
type PlannedFile = { path: string; is_new: boolean };

const BADGE: Record<FileChange["status"], { label: string; color: string; title: string }> = {
  modified: { label: "M", color: "text-[#e2c08d]", title: "modified" },
  created: { label: "N", color: "text-[#73c991]", title: "new file" },
  unchanged: { label: "=", color: "text-[#858585]", title: "no change" },
  failed: { label: "!", color: "text-[#f48771]", title: "the AI could not produce a valid edit" },
};

const baseName = (path: string) => path.split(/[\\/]/).pop() ?? path;
const canApply = (c: FileChange) => c.status === "modified" || c.status === "created";

export function ProjectEditPanel({ workspace }: { workspace: string | null }) {
  const open = useWorkbench((s) => s.projectEditOpen);
  const close = useWorkbench((s) => s.closeProjectEdit);

  const [instruction, setInstruction] = useState("");
  const [phase, setPhase] = useState<Phase>("input");
  const [statusMessage, setStatusMessage] = useState("");
  const [plan, setPlan] = useState<PlannedFile[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [changes, setChanges] = useState<FileChange[]>([]);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [applied, setApplied] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const resetRun = () => {
    setPlan([]);
    setNotes([]);
    setChanges([]);
    setChecked({});
    setApplied({});
    setSelected(null);
    setError(null);
  };

  const dismiss = useCallback(() => {
    abortRef.current?.abort();
    setPhase("input");
    close();
  }, [close]);

  // Fresh state and focus every time the panel opens.
  useEffect(() => {
    if (!open) return;
    setPhase("input");
    resetRun();
    setTimeout(() => textRef.current?.focus(), 0);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !applying) dismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, applying, dismiss]);

  async function start() {
    const text = instruction.trim();
    if (!text || phase === "running") return;
    if (!workspace) {
      setError("Open a folder first.");
      return;
    }
    resetRun();
    setStatusMessage("Starting…");
    setPhase("running");
    const controller = new AbortController();
    abortRef.current = controller;

    let received = 0;
    try {
      const body = {
        path: workspace,
        instruction: text,
        active_file: useWorkbench.getState().activeTab,
        open_files: getOpenFiles(), // current text, unsaved edits included
      };
      for await (const ev of streamProjectEdit(body, controller.signal)) {
        if (ev.type === "status") {
          setStatusMessage(ev.message);
        } else if (ev.type === "plan") {
          setPlan(ev.files);
          setNotes(ev.notes);
        } else if (ev.type === "file") {
          received += 1;
          const change = ev.change;
          setChanges((prev) => [...prev, change]);
          setChecked((prev) => ({ ...prev, [change.path]: canApply(change) }));
          setSelected((prev) => prev ?? change.path);
        } else if (ev.type === "error") {
          if (ev.notes?.length) setNotes(ev.notes);
          setError(ev.message);
          setPhase(received > 0 ? "review" : "input"); // keep whatever was finished before the failure
          return;
        } else {
          setPhase("review");
          return;
        }
      }
      setPhase(received > 0 ? "review" : "input");
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : String(e));
        setPhase(received > 0 ? "review" : "input");
      }
    }
  }

  function cancelRun() {
    abortRef.current?.abort();
    setPhase("input");
  }

  async function applySelected() {
    const chosen = changes.filter((c) => checked[c.path] && !applied[c.path] && canApply(c));
    if (chosen.length === 0) return;
    setApplying(true);
    setError(null);
    const problems: string[] = [];
    const done: Record<string, boolean> = {};
    for (const c of chosen) {
      const problem = await applyFileChange({
        abs_path: c.abs_path,
        status: c.status,
        original: c.original,
        new_content: c.new_content,
      });
      if (problem) problems.push(`${c.path}: ${problem}`);
      else done[c.path] = true;
    }
        // Record what was applied so it can be rolled back later.
    await recordChangeSet(
      instruction.trim(),
      "project",
      chosen
        .filter((c) => done[c.path])
        .map((c) => ({
          path: c.abs_path,
          status: c.status as "modified" | "created",
          before: c.status === "created" ? null : c.original,
          after: c.new_content,
        })),
    );
    setApplied((prev) => ({ ...prev, ...done }));
    setApplying(false);
    if (problems.length > 0) {
      setError(problems.join("\n"));
    } else {
      setInstruction("");
      dismiss();
    }
  }

  if (!open) return null;

  const current = changes.find((c) => c.path === selected) ?? null;
  const selectedCount = changes.filter((c) => checked[c.path] && !applied[c.path] && canApply(c)).length;

  return (
    <div className="absolute inset-0 z-40 flex flex-col bg-[#1e1e1e] text-[13px] text-[#cccccc]">
      <div className="flex shrink-0 items-center justify-between border-b border-[#3c3c3c] bg-[#252526] px-3 py-2">
        <div className="font-semibold">Edit across files</div>
        <button onClick={dismiss} disabled={applying} className="rounded px-2 py-0.5 hover:bg-white/10 disabled:opacity-40">
          ✕
        </button>
      </div>

      {phase === "input" && (
        <div className="p-4">
          <p className="mb-2 text-[#858585]">
            Describe a change that may touch several files. The AI finds the relevant files (up to 5), proposes edits
            and new files, and you review each one before anything is applied.
          </p>
          <textarea
            ref={textRef}
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void start();
              }
            }}
            rows={4}
            placeholder='e.g. "Add a logout function and call it from the handler, with a unit test"'
            className="w-full resize-none rounded border border-[#3c3c3c] bg-[#3c3c3c] p-2 text-white outline-none focus:border-[#007acc]"
          />
          <div className="mt-2 flex items-center gap-3">
            <button onClick={() => void start()} className="rounded bg-[#0e639c] px-3 py-1 text-white hover:brightness-110">
              Plan and edit
            </button>
            <span className="text-[11px] text-[#858585]">Enter to start · Shift+Enter for a new line · Esc to close</span>
          </div>
          {error && <div className="mt-2 text-red-300">⚠ {error}</div>}
          {notes.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-[12px] text-yellow-500">
              {notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {phase === "running" && (
        <div className="p-4">
          <div className="mb-3 flex items-center justify-between">
            <span>⏳ {statusMessage}</span>
            <button onClick={cancelRun} className="rounded bg-[#5a1d1d] px-3 py-1 text-white hover:brightness-125">
              Cancel
            </button>
          </div>
          <p className="mb-2 text-[11px] text-[#858585]">A local 7B model takes up to a minute per file.</p>
          {plan.map((f) => {
            const done = changes.find((c) => c.path === f.path);
            return (
              <div key={f.path} className="py-0.5">
                <span className={done ? BADGE[done.status].color : "text-[#858585]"}>{done ? BADGE[done.status].label : "…"}</span>{" "}
                {f.path}
                {f.is_new && <span className="text-[#73c991]"> (new)</span>}
              </div>
            );
          })}
        </div>
      )}

      {phase === "review" && (
        <>
          <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[#3c3c3c] bg-[#252526] px-3 py-2">
            <div className="min-w-0">
              <div className="font-semibold">
                {selectedCount} of {changes.filter(canApply).length} change{changes.length === 1 ? "" : "s"} selected
              </div>
              <div className="text-[11px] text-[#858585]">
                Modified files are edited in the editor (Ctrl+Z undoes, Ctrl+S saves). New files are written to disk when applied.
              </div>
              {notes.map((n, i) => (
                <div key={i} className="truncate text-[12px] text-yellow-500" title={n}>
                  ⚠ {n}
                </div>
              ))}
              {error && <div className="whitespace-pre-wrap text-[12px] text-red-300">⚠ {error}</div>}
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                onClick={() => void applySelected()}
                disabled={selectedCount === 0 || applying}
                className="rounded bg-[#0e639c] px-3 py-1 text-white hover:brightness-110 disabled:opacity-40"
              >
                {applying ? "Applying…" : "Apply selected"}
              </button>
              <button
                onClick={() => {
                  setPhase("input");
                  resetRun();
                }}
                disabled={applying}
                className="rounded bg-[#3c3c3c] px-3 py-1 hover:brightness-125 disabled:opacity-40"
              >
                Try again
              </button>
              <button onClick={dismiss} disabled={applying} className="rounded bg-[#5a1d1d] px-3 py-1 text-white hover:brightness-125 disabled:opacity-40">
                Reject all
              </button>
            </div>
          </div>

          <div className="flex min-h-0 flex-1">
            <div className="w-72 shrink-0 overflow-y-auto border-r border-[#3c3c3c] bg-[#252526]">
              {changes.map((c) => (
                <div
                  key={c.path}
                  onClick={() => setSelected(c.path)}
                  className={`flex cursor-pointer items-center gap-2 px-2 py-1.5 ${selected === c.path ? "bg-[#37373d]" : "hover:bg-[#2a2d2e]"}`}
                  title={c.path}
                >
                  <input
                    type="checkbox"
                    checked={!!checked[c.path] && !applied[c.path]}
                    disabled={!canApply(c) || !!applied[c.path]}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => setChecked((prev) => ({ ...prev, [c.path]: e.target.checked }))}
                  />
                  <span className={`w-3 shrink-0 text-center font-bold ${BADGE[c.status].color}`} title={BADGE[c.status].title}>
                    {BADGE[c.status].label}
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    {baseName(c.path)}
                    <span className="text-[11px] text-[#858585]"> {c.path.includes("/") ? c.path.slice(0, c.path.lastIndexOf("/")) : ""}</span>
                  </span>
                  {applied[c.path] && <span className="text-[#73c991]">✓</span>}
                  {c.failed > 0 && c.applied > 0 && <span className="text-yellow-500" title="Some edits could not be applied">½</span>}
                </div>
              ))}
            </div>

            <div className="flex min-w-0 flex-1 flex-col">
              {current && (
                <>
                  <div className="shrink-0 border-b border-[#3c3c3c] px-3 py-1.5 text-[12px]">
                    {current.explanation && <div className="text-[#cccccc]">{current.explanation}</div>}
                    {current.outcomes
                      .filter((o) => o.status !== "applied")
                      .map((o, i) => (
                        <div key={i} className="truncate text-yellow-500" title={o.search_preview}>
                          ⚠ {o.message}
                          {o.search_preview ? `: ${o.search_preview.split("\n")[0]}` : ""}
                        </div>
                      ))}
                  </div>
                  <div className="min-h-0 flex-1">
                    {canApply(current) ? (
                      <DiffReview original={current.original} modified={current.new_content} language={current.language} />
                    ) : (
                      <div className="p-3 text-[#858585]">No usable edit was produced for this file.</div>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}