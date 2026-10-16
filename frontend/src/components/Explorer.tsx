import { useEffect, useState } from "react";
import type { DirEntry, WorkspaceInfo } from "../lyxia";
import { openFile } from "../lib/editorManager";

const errorText = (e: unknown) =>
  e instanceof Error ? e.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, "") : String(e);

interface NodeProps {
  entry: DirEntry;
  depth: number;
  selected: string | null;
  onSelect: (path: string) => void;
  onError: (message: string | null) => void;
}

function Node({ entry, depth, selected, onSelect, onError }: NodeProps) {
  const [open, setOpen] = useState(false);
  const [children, setChildren] = useState<DirEntry[] | null>(null);

  const onClick = async () => {
    onSelect(entry.path);
        if (!entry.isDir) {
      void openFile(entry.path);
      return;
    }
    try {
      // Folders are read lazily, the first time they are opened.
      if (!open && children === null) setChildren(await window.lyxia.workspace.readDir(entry.path));
      setOpen(!open);
      onError(null);
    } catch (e) {
      onError(errorText(e));
    }
  };

  return (
    <>
      <div
        onClick={() => void onClick()}
        style={{ paddingLeft: 8 + depth * 12 }}
        className={`flex h-[22px] cursor-pointer items-center gap-1.5 truncate pr-2 ${
          selected === entry.path ? "bg-[#37373d] text-white" : "hover:bg-[#2a2d2e]"
        }`}
      >
        <span className="w-3 shrink-0 text-center text-[10px] text-[#858585]">{entry.isDir ? (open ? "▾" : "▸") : ""}</span>
        <span className="truncate">{entry.name}</span>
      </div>
      {open &&
        children?.map((child) => (
          <Node key={child.path} entry={child} depth={depth + 1} selected={selected} onSelect={onSelect} onError={onError} />
        ))}
    </>
  );
}

interface ExplorerProps {
  workspace: WorkspaceInfo | null;
  onOpenFolder: () => void;
}

export function Explorer({ workspace, onOpenFolder }: ExplorerProps) {
  const [roots, setRoots] = useState<DirEntry[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Load the top level whenever a different folder is opened.
  useEffect(() => {
    setSelected(null);
    setError(null);
    if (!workspace) {
      setRoots([]);
      return;
    }
    let cancelled = false;
    window.lyxia.workspace
      .readDir(workspace.root)
      .then((entries) => !cancelled && setRoots(entries))
      .catch((e) => !cancelled && setError(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, [workspace]);

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-[#3c3c3c] bg-[#252526] text-[13px] text-[#cccccc]">
      <div className="flex h-9 shrink-0 items-center justify-between px-3 text-[11px] font-semibold tracking-wider text-[#858585] uppercase">
        <span>Explorer</span>
        <button onClick={onOpenFolder} className="rounded px-1.5 py-0.5 normal-case hover:bg-white/10" title="Open Folder">
          Open…
        </button>
      </div>

      {workspace ? (
        <div className="min-h-0 flex-1 overflow-y-auto pb-4">
          <div className="truncate px-3 py-1 text-[11px] font-bold uppercase" title={workspace.root}>
            {workspace.name}
          </div>
          {roots.map((entry) => (
            <Node key={entry.path} entry={entry} depth={0} selected={selected} onSelect={setSelected} onError={setError} />
          ))}
        </div>
      ) : (
        <div className="p-4 text-[#858585]">
          <p className="mb-3">No folder opened.</p>
          <button onClick={onOpenFolder} className="w-full rounded bg-[#0e639c] py-1.5 text-white hover:brightness-110">
            Open Folder
          </button>
        </div>
      )}

      {error && <div className="shrink-0 border-t border-[#3c3c3c] bg-red-900/40 px-3 py-1.5 text-red-300">⚠ {error}</div>}
    </aside>
  );
}