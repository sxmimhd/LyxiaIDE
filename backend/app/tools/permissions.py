"""Who decides what: permission levels, per-project "always allow" rules, and the approval handshake."""
import asyncio
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.tools.base import Level, Tool


@dataclass
class Decision:
    action: str  # allow | ask | deny
    level: Level
    reason: str


def decide(tool: Tool, args: dict[str, Any], policy: dict[str, list[str]]) -> Decision:
    """Defaults: READ is allowed; WRITE and EXECUTE ask; DESTRUCTIVE asks every single time.
    A tool may raise its own level or forbid a call based on its arguments (run_command does both)."""
    level, reason, deny = tool.level, "", False
    if tool.classify:
        c = tool.classify(args)
        level, reason, deny = c.level, c.reason, c.deny
    if deny:
        return Decision("deny", level, reason)  # nothing can override this, not even an old "always allow"
    if level == Level.READ:
        return Decision("allow", level, "")
    if level != Level.DESTRUCTIVE:
        trusted = args.get("command") in policy["commands"] if tool.name == "run_command" else tool.name in policy["tools"]
        if trusted:
            return Decision("allow", level, "Allowed earlier with 'Always allow'")
    return Decision("ask", level, reason or f"{tool.name} needs your approval")


class PolicyStore:
    """Per-project "always allow" rules, kept in one small JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    @staticmethod
    def _key(root: Path) -> str:
        return os.path.normcase(str(root))

    def get(self, root: Path) -> dict[str, list[str]]:
        with self._lock:
            entry = self._read().get(self._key(root), {})
        return {"commands": list(entry.get("commands", [])), "tools": list(entry.get("tools", []))}

    def allow(self, root: Path, *, command: str | None = None, tool: str | None = None) -> None:
        with self._lock:
            data = self._read()
            entry = data.setdefault(self._key(root), {"commands": [], "tools": []})
            if command and command not in entry["commands"]:
                entry["commands"].append(command)
            if tool and tool not in entry["tools"]:
                entry["tools"].append(tool)
            self._write(data)

    def revoke(self, root: Path, *, command: str | None = None, tool: str | None = None) -> None:
        with self._lock:
            data = self._read()
            entry = data.get(self._key(root))
            if entry:
                if command in entry.get("commands", []):
                    entry["commands"].remove(command)
                if tool in entry.get("tools", []):
                    entry["tools"].remove(tool)
                self._write(data)


class ApprovalBroker:
    """Pauses a tool call until the user answers in the UI. Unanswered requests expire as 'deny'."""

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[str]] = {}
        self._info: dict[str, dict[str, Any]] = {}

    def create(self, info: dict[str, Any]) -> str:
        approval_id = uuid.uuid4().hex
        self._futures[approval_id] = asyncio.get_running_loop().create_future()
        self._info[approval_id] = {**info, "id": approval_id, "created": time.time()}
        return approval_id

    async def wait(self, approval_id: str, timeout: float = 300.0) -> str:
        try:
            return await asyncio.wait_for(asyncio.shield(self._futures[approval_id]), timeout)
        except asyncio.TimeoutError:
            return "deny"

    def resolve(self, approval_id: str, decision: str) -> bool:
        future = self._futures.get(approval_id)
        if future is None or future.done():
            return False
        future.set_result(decision)
        return True

    def discard(self, approval_id: str) -> None:
        self._futures.pop(approval_id, None)
        self._info.pop(approval_id, None)

    def pending(self) -> list[dict[str, Any]]:
        return list(self._info.values())


class AuditLog:
    """Every tool call, one JSON line each: what was asked, what was decided, what happened."""

    MAX_BYTES = 2_000_000

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def record(self, **fields: Any) -> None:
        line = json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields})
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists() and self._path.stat().st_size > self.MAX_BYTES:
                keep = self._path.read_text(encoding="utf-8").splitlines()[-1000:]
                self._path.write_text("\n".join(keep) + "\n", encoding="utf-8")
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def recent(self, root: Path, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            try:
                lines = self._path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return []
        out = []
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("root") == str(root):
                out.append(entry)
                if len(out) >= limit:
                    break
        return out