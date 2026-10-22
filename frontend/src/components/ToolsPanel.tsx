import { useCallback, useEffect, useRef, useState } from "react";
import {
  approve, getAudit, getPermissions, listTools, revokePermission, streamToolRun,
  type ApprovalRequest, type AuditEntry, type Permissions, type ToolInfo, type ToolLevel,
} from "../lib/tools";
import { useWorkbench } from "../state/workbench";

const LEVEL_STYLE: Record<ToolLevel, string> = {
  READ: "bg-[#1f4d36] text-[#73c991]",
  WRITE: "bg-[#5a3d12] text-[#e2c08d]",
  EXECUTE: "bg-[#5a4d12] text-[#e5c07b]",
  DESTRUCTIVE: "bg-[#5a1d1d] text-[#f48771]",
};

type Outcome =
  | { kind: "result"; ok: boolean; output: string; truncated: boolean; ms: number }
  | { kind: "denied"; reason: string; blocked: boolean };

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function ToolsPanel({ workspace }: { workspace: string | null }) {
  const open = useWorkbench((s) => s.toolsOpen);
  const close = useWorkbench((s) => s.closeTools);

  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [argsText, setArgsText] = useState("{}");
  const [running, setRunning] = useState(false);
  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [permissions, setPermissions] = useState<Permissions>({ commands: [], tools: [] });
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const tool = tools.find((t) => t.name === selected) ?? null;

  const refresh = useCallback(async () => {
    if (!workspace) return;
    try {
      setPermissions(await getPermissions(workspace));
      setAudit(await getAudit(workspace));
    } catch {
      // the backend may be down; the run itself will report it
    }
  }, [workspace]);

  useEffect(() => {
    if (!open) return;
    setOutcome(null);
    setError(null);
    listTools()
      .then((list) => {
        setTools(list);
        setSelected((prev) => prev ?? list[0]?.name ?? null);
      })
      .catch((e) => setError(errorText(e)));
    void refresh();
  }, [open, refresh]);

  useEffect(() => {
    if (tool) setArgsText(JSON.stringify(tool.example, null, 2));
    setOutcome(null);
  }, [tool]);

  const dismiss = useCallback(() => {
    abortRef.current?.abort(); // a pending approval is cancelled on the server too
    close();
  }, [close]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, dismiss]);

  async function run() {
    if (!workspace || !tool || running) return;
    let args: Record<string, unknown>;
    try {
      args = JSON.parse(argsText || "{}") as Record<string, unknown>;
    } catch {
      setError("The arguments are not valid JSON.");
      return;
    }
    setRunning(true);
    setOutcome(null);
    setApproval(null);
    setError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      for await (const ev of streamToolRun(workspace, tool.name, args, controller.signal)) {
        if (ev.type === "approval_request") setApproval(ev);
        else if (ev.type === "denied") setOutcome({ kind: "denied", reason: ev.reason, blocked: ev.blocked });
        else setOutcome({ kind: "result", ok: ev.ok, output: ev.output, truncated: ev.truncated, ms: ev.duration_ms });
      }
    } catch (e) {
      if (!controller.signal.aborted) setError(errorText(e));
    } finally {
      setRunning(false);
      setApproval(null);
      void refresh();
    }
  }

  async function answer(decision: "allow_once" | "allow_always" | "deny") {
    if (!approval) return;
    try {
      await approve(approval.id, decision);
      setApproval(null);
    } catch (e) {
      setError(errorText(e));
    }
  }

  if (!open) return null;

  return (
    <div className="absolute inset-0 z-40 flex flex-col bg-[#1e1e1e] text-[13px] text-[#cccccc]">
      <div className="flex shrink-0 items-center justify-between border-b border-[#3c3c3c] bg-[#252526] px-3 py-2">
        <div className="font-semibold">AI tools and permissions</div>
        <button onClick={dismiss} className="rounded px-2 py-0.5 hover:bg-white/10">
          ✕
        </button>
      </div>

      {!workspace ? (
        <div className="p-4 text-[#858585]">Open a folder first. Tools always work inside the opened project.</div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <div className="w-60 shrink-0 overflow-y-auto border-r border-[#3c3c3c] bg-[#252526]">
            {tools.map((t) => (
              <div
                key={t.name}
                onClick={() => setSelected(t.name)}
                className={`cursor-pointer px-3 py-1.5 ${selected === t.name ? "bg-[#37373d]" : "hover:bg-[#2a2d2e]"}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate">{t.name}</span>
                  <span className={`shrink-0 rounded px-1 text-[10px] ${LEVEL_STYLE[t.level]}`}>{t.level}</span>
                </div>
              </div>
            ))}
          </div>

          <div className="flex min-w-0 flex-1 flex-col overflow-y-auto p-3">
            {tool && (
              <>
                <div className="mb-1 flex items-center gap-2">
                  <span className="font-semibold">{tool.name}</span>
                  <span className={`rounded px-1 text-[10px] ${LEVEL_STYLE[tool.level]}`}>{tool.level}</span>
                </div>
                <p className="mb-2 text-[#858585]">{tool.description}</p>
                <textarea
                  value={argsText}
                  onChange={(e) => setArgsText(e.target.value)}
                  rows={5}
                  spellCheck={false}
                  className="w-full resize-y rounded border border-[#3c3c3c] bg-[#3c3c3c] p-2 font-mono text-[12px] text-white outline-none focus:border-[#007acc]"
                />
                <div className="mt-2 flex items-center gap-3">
                  <button
                    onClick={() => void run()}
                    disabled={running}
                    className="rounded bg-[#0e639c] px-3 py-1 text-white hover:brightness-110 disabled:opacity-50"
                  >
                    {running ? "Running…" : "Run as the AI would"}
                  </button>
                  <span className="text-[11px] text-[#858585]">Permission rules apply exactly as they will to the AI agent.</span>
                </div>
              </>
            )}

            {approval && (
              <div className="mt-3 rounded border border-yellow-600 bg-[#2d2a1a] p-3">
                <div className="mb-1 flex items-center gap-2 font-semibold">
                  ⚠ Approval needed
                  <span className={`rounded px-1 text-[10px] ${LEVEL_STYLE[approval.level]}`}>{approval.level}</span>
                </div>
                <div className="mb-1">{approval.reason}</div>
                <pre className="mb-2 overflow-x-auto rounded bg-[#1e1e1e] p-2 text-[12px] whitespace-pre-wrap">
                  {approval.tool === "run_command" ? String(approval.args["command"]) : JSON.stringify(approval.args, null, 2)}
                </pre>
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => void answer("allow_once")} className="rounded bg-[#0e639c] px-3 py-1 text-white hover:brightness-110">
                    Allow once
                  </button>
                  {approval.can_always_allow && (
                    <button onClick={() => void answer("allow_always")} className="rounded bg-[#3c3c3c] px-3 py-1 hover:brightness-125">
                      Always allow this exact command
                    </button>
                  )}
                  <button onClick={() => void answer("deny")} className="rounded bg-[#5a1d1d] px-3 py-1 text-white hover:brightness-125">
                    Deny
                  </button>
                </div>
              </div>
            )}

            {error && <div className="mt-3 text-red-300">⚠ {error}</div>}

            {outcome?.kind === "denied" && (
              <div className={`mt-3 rounded border p-2 ${outcome.blocked ? "border-red-700 bg-[#3a1d1d]" : "border-[#3c3c3c]"}`}>
                {outcome.blocked ? "⛔ Blocked: " : "Declined: "}
                {outcome.reason}
              </div>
            )}
            {outcome?.kind === "result" && (
              <div className="mt-3">
                <div className={`mb-1 text-[12px] ${outcome.ok ? "text-[#73c991]" : "text-red-300"}`}>
                  {outcome.ok ? "✓ Success" : "✕ Failed"} · {outcome.ms} ms{outcome.truncated ? " · output shortened" : ""}
                </div>
                <pre className="max-h-96 overflow-auto rounded bg-[#252526] p-2 text-[12px] whitespace-pre-wrap">{outcome.output}</pre>
              </div>
            )}

            <div className="mt-4 border-t border-[#3c3c3c] pt-3">
              <div className="mb-1 text-[11px] font-semibold tracking-wider text-[#858585] uppercase">Always allowed in this project</div>
              {permissions.commands.length + permissions.tools.length === 0 && <div className="text-[#858585]">Nothing yet. Every command asks first.</div>}
              {permissions.commands.map((c) => (
                <div key={c} className="flex items-center justify-between gap-2 py-0.5">
                  <code className="truncate">{c}</code>
                  <button
                    onClick={() => workspace && void revokePermission(workspace, { command: c }).then(setPermissions)}
                    className="shrink-0 rounded px-2 text-[11px] text-red-300 hover:bg-white/10"
                  >
                    Revoke
                  </button>
                </div>
              ))}
              {permissions.tools.map((t) => (
                <div key={t} className="flex items-center justify-between gap-2 py-0.5">
                  <code>{t}</code>
                  <button
                    onClick={() => workspace && void revokePermission(workspace, { tool: t }).then(setPermissions)}
                    className="shrink-0 rounded px-2 text-[11px] text-red-300 hover:bg-white/10"
                  >
                    Revoke
                  </button>
                </div>
              ))}
            </div>

            <div className="mt-4 border-t border-[#3c3c3c] pt-3">
              <div className="mb-1 text-[11px] font-semibold tracking-wider text-[#858585] uppercase">Recent activity</div>
              {audit.length === 0 && <div className="text-[#858585]">No tool calls yet.</div>}
              {audit.map((a, i) => (
                <div key={i} className="flex items-baseline gap-2 py-0.5 text-[12px]">
                  <span className="shrink-0 text-[#858585]">{a.time.slice(11)}</span>
                  <span className="shrink-0">{a.tool}</span>
                  <span
                    className={`shrink-0 ${
                      a.outcome === "blocked" ? "text-red-300" : a.outcome === "declined" ? "text-yellow-500" : "text-[#73c991]"
                    }`}
                  >
                    {a.outcome}
                  </span>
                  <span className="truncate text-[#858585]" title={a.args?.["command"] ?? JSON.stringify(a.args ?? {})}>
                    {a.args?.["command"] ?? Object.values(a.args ?? {}).join(" ")}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}