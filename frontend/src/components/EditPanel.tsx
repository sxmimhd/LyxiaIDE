import { useCallback, useEffect, useRef, useState } from "react";
import { proposeEdit, type EditResponse } from "../lib/api";
import { applyAiEdit, getEditContext, recordChangeSet } from "../lib/editorManager";
import { useWorkbench } from "../state/workbench";
import { DiffReview } from "./DiffReview";

interface Proposal extends EditResponse {
  path: string;
  original: string;
  language: string;
}

export function EditPanel() {
  const open = useWorkbench((s) => s.editBarOpen);
  const closeBar = useWorkbench((s) => s.closeEditBar);
  const selection = useWorkbench((s) => s.selection);

  const [instruction, setInstruction] = useState("");
  const [phase, setPhase] = useState<"input" | "loading" | "review">("input");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const dismiss = useCallback(() => {
    abortRef.current?.abort();
    setPhase("input");
    setProposal(null);
    setError(null);
    closeBar();
  }, [closeBar]);

  // Fresh state and focus every time the bar opens.
  useEffect(() => {
    if (!open) return;
    setPhase("input");
    setProposal(null);
    setError(null);
    setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, dismiss]);

  async function submit() {
    const text = instruction.trim();
    if (!text || phase === "loading") return;
    const ctx = getEditContext(); // captured now: the file as it is at this moment
    if (!ctx) {
      setError("Open a file first.");
      return;
    }
    setError(null);
    setPhase("loading");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const result = await proposeEdit(
        {
          path: ctx.path,
          language: ctx.language,
          content: ctx.content,
          instruction: text,
          selection: ctx.selection && { start_line: ctx.selection.startLine, end_line: ctx.selection.endLine },
          cursor_line: ctx.cursorLine,
        },
        controller.signal,
      );
      setProposal({ ...result, path: ctx.path, original: ctx.content, language: ctx.language });
      setPhase("review");
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : String(e));
        setPhase("input");
      }
    }
  }

  function accept() {
    if (!proposal) return;
    const problem = applyAiEdit(proposal.path, proposal.new_content, proposal.original);
    if (problem) {
            setError(problem);
      return;
    }
    void recordChangeSet(instruction.trim(), "file", [
      { path: proposal.path, status: "modified", before: proposal.original, after: proposal.new_content },
    ]);
    setInstruction("");
    dismiss();
  }

  if (!open) return null;

  const failures = proposal?.outcomes.filter((o) => o.status !== "applied") ?? [];

  return (
    <>
      {phase !== "review" && (
        <div className="absolute inset-x-0 top-0 z-20 border-b border-[#3c3c3c] bg-[#252526] p-2 shadow-lg">
          <div className="mb-1 text-[11px] text-[#858585]">
            Edit with AI ·{" "}
            <span className={selection ? "text-[#4ec9b0]" : ""}>
              {selection ? `selected lines ${selection.startLine}-${selection.endLine}` : "whole file"}
            </span>
          </div>
          <input
            ref={inputRef}
            value={instruction}
            disabled={phase === "loading"}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void submit();
              }
            }}
            placeholder='Describe the change, e.g. "add type hints" or "handle the empty list case"'
            className="w-full rounded border border-[#3c3c3c] bg-[#3c3c3c] px-2 py-1.5 text-[13px] text-white outline-none focus:border-[#007acc] disabled:opacity-60"
          />
          <div className="mt-1 text-[11px] text-[#858585]">
            {phase === "loading" ? "Thinking… a local 7B model can take up to a minute." : "Enter to generate · Esc to cancel"}
          </div>
          {error && <div className="mt-1 text-[12px] text-red-300">⚠ {error}</div>}
        </div>
      )}

      {phase === "review" && proposal && (
        <div className="absolute inset-0 z-30 flex flex-col bg-[#1e1e1e]">
          <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[#3c3c3c] bg-[#252526] px-3 py-2 text-[13px]">
            <div className="min-w-0">
              <div className="font-semibold">
                {proposal.applied > 0
                  ? `${proposal.applied} edit${proposal.applied > 1 ? "s" : ""} ready to apply`
                  : "No changes were proposed"}
              </div>
              {proposal.explanation && <div className="mt-0.5 text-[#cccccc]">{proposal.explanation}</div>}
              {failures.map((o, i) => (
                <div key={i} className="mt-0.5 truncate text-[12px] text-yellow-500" title={o.search_preview}>
                  ⚠ {o.message}
                  {o.search_preview ? `: ${o.search_preview.split("\n")[0]}` : ""}
                </div>
              ))}
              {error && <div className="mt-0.5 text-[12px] text-red-300">⚠ {error}</div>}
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                onClick={accept}
                disabled={proposal.applied === 0}
                className="rounded bg-[#0e639c] px-3 py-1 text-white hover:brightness-110 disabled:opacity-40"
              >
                Accept
              </button>
              <button onClick={() => setPhase("input")} className="rounded bg-[#3c3c3c] px-3 py-1 hover:brightness-125">
                Try again
              </button>
              <button onClick={dismiss} className="rounded bg-[#5a1d1d] px-3 py-1 text-white hover:brightness-125">
                Reject
              </button>
            </div>
          </div>
          <div className="min-h-0 flex-1">
            {proposal.applied > 0 ? (
              <DiffReview original={proposal.original} modified={proposal.new_content} language={proposal.language} />
            ) : (
              <pre className="h-full overflow-auto p-3 text-[12px] whitespace-pre-wrap text-[#858585]">
                {proposal.raw_output || "(the model returned nothing)"}
              </pre>
            )}
          </div>
        </div>
      )}
    </>
  );
}