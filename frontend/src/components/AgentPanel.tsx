import { useCallback, useEffect, useRef, useState } from "react";
import { respondProposal, streamAgent, type AgentStatus } from "../lib/agent";
import { applyFileChange, getOpenFiles, recordChangeSet, saveFileAnyPath } from "../lib/editorManager";
import type { FileChange } from "../lib/projectEdit";
import { approve, type ApprovalRequest } from "../lib/tools";
import { useWorkbench } from "../state/workbench";
import { DiffReview } from "./DiffReview";

type Phase = "input" | "running" | "finished";

interface StepView {
  n: number;
  thought: string;
  action: string | null;
  args: Record<string, unknown>;
  error?: string;
  observation?: string;
  ok?: boolean;
}
interface Proposal {
  id: string;
  instruction: string;
  changes: FileChange[];
}
interface Final {
  status: AgentStatus;
  summary: string;
  steps: number;
}

const canApply = (c: FileChange) => c.status === "modified" || c.status === "created";
const baseName = (path: string) => path.split(/[\\/]/).pop() ?? path;
const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const argsText = (args: Record<string, unknown>) =>
  Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");

const FINAL_STYLE: Record<AgentStatus, { label: string; color: string }> = {
  finished: { label: "Finished", color: "border-[#2d6a4f] bg-[#1b3a2c] text-[#73c991]" },
  step_limit: { label: "Stopped: step limit reached", color: "border-yellow-700 bg-[#3a3318] text-yellow-400" },
  stuck: { label: "Stopped: it was repeating itself", color: "border-yellow-700 bg-[#3a3318] text-yellow-400" },
  error: { label: "Failed", color: "border-red-800 bg-[#3a1d1d] text-red-300" },
  stopped: { label: "Stopped by you", color: "border-[#3c3c3c] bg-[#2a2a2a] text-[#cccccc]" },
};

export function AgentPanel({ workspace }: { workspace: string | null }) {
  const open = useWorkbench((s) => s.agentOpen);
  const close = useWorkbench((s) => s.closeAgent);

  const [task, setTask] = useState("");
  const [phase, setPhase] = useState<Phase>("input");
  const [maxSteps, setMaxSteps] = useState(12);
  const [statusMessage, setStatusMessage] = useState("");
  const [steps, setSteps] = useState<StepView[]>([]);
  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [final, setFinal] = useState<Final | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const running = phase === "running";

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [steps, final, error]);

  const dismiss = useCallback(() => {
    abortRef.current?.abort(); // closing the panel stops the agent, and withdraws any open prompt
    close();
  }, [close]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !applying) dismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, applying, dismiss]);

  async function start() {
    const text = task.trim();
    if (!text || running) return;
    if (!workspace) {
      setError("Open a folder first.");
      return;
    }
    setSteps([]);
    setApproval(null);
    setProposal(null);
    setFinal(null);
    setError(null);
    setStatusMessage("Starting…");
    setPhase("running");
    const controller = new AbortController();
    abortRef.current = controller;
    let stepCount = 0;
    try {
      const body = {
        path: workspace,
        task: text,
        active_file: useWorkbench.getState().activeTab,
        open_files: getOpenFiles(), // current text, unsaved edits included
        max_steps: 12,
      };
      for await (const ev of streamAgent(body, controller.signal)) {
        switch (ev.type) {
          case "start":
            setMaxSteps(ev.max_steps);
            break;
          case "status":
            setStatusMessage(ev.message);
            break;
          case "step":
            stepCount = ev.n;
            setStatusMessage(ev.action ? `Step ${ev.n}: ${ev.action}` : `Step ${ev.n}`);
            setSteps((prev) => [...prev, { n: ev.n, thought: ev.thought, action: ev.action, args: ev.args, error: ev.error }]);
            break;
          case "observation":
            setSteps((prev) => prev.map((s) => (s.n === ev.n ? { ...s, observation: ev.output, ok: ev.ok } : s)));
            break;
          case "approval_request":
            setApproval(ev);
            break;
          case "proposal":
            setProposal({ id: ev.id, instruction: ev.instruction, changes: ev.changes });
            setChecked(Object.fromEntries(ev.changes.map((c) => [c.path, canApply(c)])));
            setSelected((ev.changes.find(canApply) ?? ev.changes[0])?.path ?? null);
            break;
          case "done":
            setFinal({ status: ev.status, summary: ev.summary, steps: ev.steps });
            break;
          case "error":
            setError(ev.message);
            break;
        }
      }
    } catch (e) {
      if (!controller.signal.aborted) setError(errorText(e));
    } finally {
      setApproval(null);
      setProposal(null);
      setPhase("finished");
      if (controller.signal.aborted) setFinal({ status: "stopped", summary: "You stopped the agent.", steps: stepCount });
    }
  }

  async function answerApproval(decision: "allow_once" | "allow_always" | "deny") {
    if (!approval) return;
    try {
      await approve(approval.id, decision);
      setApproval(null);
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function applyProposal() {
    if (!proposal) return;
    const chosen = proposal.changes.filter((c) => checked[c.path] && canApply(c));
    setApplying(true);
    const applied: string[] = [];
    const errors: string[] = [];
    const recorded: { path: string; status: "modified" | "created"; before: string | null; after: string }[] = [];
    for (const c of chosen) {
      const problem = await applyFileChange({ abs_path: c.abs_path, status: c.status, original: c.original, new_content: c.new_content });
      if (problem) {
        errors.push(`${c.path}: ${problem}`);
        continue;
      }
      // The agent's next test run reads the disk, so the change must be saved, not only applied in the editor.
      const saveProblem = await saveFileAnyPath(c.abs_path);
      if (saveProblem) {
        errors.push(`${c.path}: applied in the editor but not saved (${saveProblem})`);
        continue;
      }
      applied.push(c.path);
      recorded.push({ path: c.abs_path, status: c.status as "modified" | "created", before: c.status === "created" ? null : c.original, after: c.new_content });
    }
    await recordChangeSet(proposal.instruction, "project", recorded); // every agent edit can be rolled back
    try {
      await respondProposal({
        id: proposal.id,
        applied,
        updated_files: chosen.filter((c) => applied.includes(c.path)).map((c) => ({ path: c.abs_path, content: c.new_content })),
        errors,
        rejected: false,
      });
    } catch (e) {
      setError(errorText(e));
    }
    setProposal(null);
    setApplying(false);
  }

  async function rejectProposal() {
    if (!proposal) return;
    try {
      await respondProposal({ id: proposal.id, applied: [], updated_files: [], errors: [], rejected: true });
    } catch (e) {
      setError(errorText(e));
    }
    setProposal(null);
  }

  if (!open) return null;

  const current = proposal?.changes.find((c) => c.path === selected) ?? null;
  const selectedCount = proposal ? proposal.changes.filter((c) => checked[c.path] && canApply(c)).length : 0;

  return (
    <div className="absolute inset-0 z-40 flex flex-col bg-[#1e1e1e] text-[13px] text-[#cccccc]">
      <div className="flex shrink-0 items-center justify-between border-b border-[#3c3c3c] bg-[#252526] px-3 py-2">
        <div className="font-semibold">🤖 Agent</div>
        <div className="flex items-center gap-3">
          {running && (
            <span className="text-[12px] text-[#858585]">
              {statusMessage} · max {maxSteps} steps
            </span>
          )}
          <button onClick={dismiss} className="rounded px-2 py-0.5 hover:bg-white/10" title="Closing stops the agent">
            ✕
          </button>
        </div>
      </div>

      <div className="shrink-0 border-b border-[#3c3c3c] p-3">
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void start();
            }
          }}
          disabled={running}
          rows={2}
          placeholder='Give it a task, e.g. "The login test fails. Find out why and fix it."  (Enter to start)'
          className="w-full resize-none rounded border border-[#3c3c3c] bg-[#3c3c3c] p-2 text-white outline-none focus:border-[#007acc] disabled:opacity-60"
        />
        <div className="mt-2 flex items-center gap-3">
          {running ? (
            <button onClick={() => abortRef.current?.abort()} className="rounded bg-[#5a1d1d] px-3 py-1 text-white hover:brightness-125">
              ■ Stop
            </button>
          ) : (
            <button onClick={() => void start()} className="rounded bg-[#0e639c] px-3 py-1 text-white hover:brightness-110">
              Run agent
            </button>
          )}
          <span className="text-[11px] text-[#858585]">
            It investigates with read-only tools on its own. Running commands and changing code always need your approval.
          </span>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {steps.length === 0 && !running && !final && !error && (
          <div className="text-[#858585]">
            The agent works in steps: it looks at your code, runs checks, proposes edits for you to review, then verifies them.
            Everything it does appears here, and every edit it makes can be rolled back from "AI history".
          </div>
        )}
        {steps.map((s) => (
          <div key={s.n} className="mb-2 rounded border border-[#2d2d2d] bg-[#252526] p-2">
            <div className="flex items-baseline gap-2">
              <span className="text-[11px] text-[#858585]">#{s.n}</span>
              {s.action ? (
                <code className="rounded bg-[#1e1e1e] px-1.5 py-0.5 text-[12px] text-[#4ec9b0]">
                  {s.action}
                  {Object.keys(s.args).length > 0 ? `  ${argsText(s.args)}` : ""}
                </code>
              ) : (
                <span className="text-yellow-500">could not understand the model's reply</span>
              )}
            </div>
            {s.thought && <div className="mt-1 text-[#cccccc]">{s.thought}</div>}
            {s.error && <div className="mt-1 text-[12px] text-yellow-500">⚠ {s.error}</div>}
            {s.observation !== undefined && (
              <details className="mt-1" open={s.ok === false}>
                <summary className={`cursor-pointer text-[12px] ${s.ok ? "text-[#73c991]" : "text-red-300"}`}>
                  {s.ok ? "✓ result" : "✕ error"}
                </summary>
                <pre className="mt-1 max-h-60 overflow-auto rounded bg-[#1e1e1e] p-2 text-[12px] whitespace-pre-wrap">{s.observation}</pre>
              </details>
            )}
          </div>
        ))}
        {running && !approval && !proposal && <div className="text-[12px] text-[#858585]">⏳ {statusMessage}</div>}
        {error && <div className="mb-2 rounded border border-red-800 bg-[#3a1d1d] p-2 text-red-300">⚠ {error}</div>}
        {final && (
          <div className={`rounded border p-3 ${FINAL_STYLE[final.status].color}`}>
            <div className="font-semibold">
              {FINAL_STYLE[final.status].label} · {final.steps} step{final.steps === 1 ? "" : "s"}
            </div>
            <div className="mt-1 whitespace-pre-wrap text-[#cccccc]">{final.summary}</div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {approval && (
        <div className="shrink-0 border-t border-yellow-700 bg-[#2d2a1a] p-3">
          <div className="mb-1 font-semibold">⚠ The agent wants to run a command: {approval.reason}</div>
          <pre className="mb-2 overflow-x-auto rounded bg-[#1e1e1e] p-2 text-[12px] whitespace-pre-wrap">
            {approval.tool === "run_command" ? String(approval.args["command"]) : JSON.stringify(approval.args, null, 2)}
          </pre>
          <div className="flex flex-wrap gap-2">
            <button onClick={() => void answerApproval("allow_once")} className="rounded bg-[#0e639c] px-3 py-1 text-white hover:brightness-110">
              Allow once
            </button>
            {approval.can_always_allow && (
              <button onClick={() => void answerApproval("allow_always")} className="rounded bg-[#3c3c3c] px-3 py-1 hover:brightness-125">
                Always allow this exact command
              </button>
            )}
            <button onClick={() => void answerApproval("deny")} className="rounded bg-[#5a1d1d] px-3 py-1 text-white hover:brightness-125">
              Deny
            </button>
          </div>
        </div>
      )}

      {proposal && (
        <div className="flex h-[55%] shrink-0 flex-col border-t border-[#007acc] bg-[#1e1e1e]">
          <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[#3c3c3c] bg-[#252526] px-3 py-2">
            <div className="min-w-0">
              <div className="font-semibold">The agent proposes changes: review them</div>
              <div className="truncate text-[12px] text-[#858585]" title={proposal.instruction}>
                {proposal.instruction}
              </div>
              <div className="text-[11px] text-[#858585]">Applied files are saved to disk so the agent can test them. You can roll them back from AI history.</div>
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                onClick={() => void applyProposal()}
                disabled={selectedCount === 0 || applying}
                className="rounded bg-[#0e639c] px-3 py-1 text-white hover:brightness-110 disabled:opacity-40"
              >
                {applying ? "Applying…" : `Apply and save ${selectedCount} file${selectedCount === 1 ? "" : "s"}`}
              </button>
              <button onClick={() => void rejectProposal()} disabled={applying} className="rounded bg-[#5a1d1d] px-3 py-1 text-white hover:brightness-125 disabled:opacity-40">
                Reject
              </button>
            </div>
          </div>
          <div className="flex min-h-0 flex-1">
            <div className="w-64 shrink-0 overflow-y-auto border-r border-[#3c3c3c] bg-[#252526]">
              {proposal.changes.map((c) => (
                <div
                  key={c.path}
                  onClick={() => setSelected(c.path)}
                  className={`flex cursor-pointer items-center gap-2 px-2 py-1.5 ${selected === c.path ? "bg-[#37373d]" : "hover:bg-[#2a2d2e]"}`}
                  title={c.path}
                >
                  <input
                    type="checkbox"
                    checked={!!checked[c.path]}
                    disabled={!canApply(c)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => setChecked((prev) => ({ ...prev, [c.path]: e.target.checked }))}
                  />
                  <span className={`w-3 shrink-0 text-center font-bold ${c.status === "created" ? "text-[#73c991]" : c.status === "modified" ? "text-[#e2c08d]" : "text-[#f48771]"}`}>
                    {c.status === "created" ? "N" : c.status === "modified" ? "M" : "!"}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{baseName(c.path)}</span>
                </div>
              ))}
            </div>
            <div className="min-w-0 flex-1">
              {current && canApply(current) ? (
                <DiffReview original={current.original} modified={current.new_content} language={current.language} />
              ) : (
                <div className="p-3 text-[#858585]">No usable edit was produced for this file.</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}