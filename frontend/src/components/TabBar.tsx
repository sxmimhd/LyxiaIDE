import { closeFile } from "../lib/editorManager";
import { useWorkbench } from "../state/workbench";

const baseName = (path: string) => path.split(/[\\/]/).pop() ?? path;

export function TabBar() {
  const tabs = useWorkbench((s) => s.tabs);
  const activeTab = useWorkbench((s) => s.activeTab);
  const dirty = useWorkbench((s) => s.dirty);
  const setActiveTab = useWorkbench((s) => s.setActiveTab);

  return (
    <div className="flex h-9 shrink-0 overflow-x-auto bg-[#252526] text-[13px]">
      {tabs.map((path) => {
        const active = path === activeTab;
        return (
          <div
            key={path}
            title={path}
            onClick={() => setActiveTab(path)}
            onAuxClick={(e) => e.button === 1 && closeFile(path)} // middle click closes
            className={`group flex shrink-0 cursor-pointer items-center gap-2 border-r border-[#1e1e1e] px-3 ${
              active ? "bg-[#1e1e1e] text-white" : "bg-[#2d2d2d] text-[#858585] hover:text-white"
            }`}
          >
            <span>{baseName(path)}</span>
            <button
              onClick={(e) => {
                e.stopPropagation();
                closeFile(path);
              }}
              className="grid h-5 w-5 place-content-center rounded hover:bg-white/10"
              aria-label="Close tab"
            >
              {dirty[path] ? (
                <span className="text-base leading-none group-hover:hidden">●</span>
              ) : null}
              <span className={dirty[path] ? "hidden group-hover:inline" : active ? "" : "invisible group-hover:visible"}>✕</span>
            </button>
          </div>
        );
      })}
    </div>
  );
}