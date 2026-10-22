"""EXECUTE tool: run a command in the project folder, after the user approved it."""
import os
import re
import signal
import subprocess
import threading
from pathlib import Path

from app.tools.base import Classification, Level, ToolContext, ToolOutput
from app.tools.safety import clip, filtered_env

MAX_CAPTURE = 1_000_000  # characters kept in memory; the rest is read and thrown away
OUTPUT_LIMIT = 12_000  # characters returned to the AI (start + end, where errors usually are)

# Never allowed, no matter what the user clicks: the damage is too large and too easy to cause by mistake.
_DENY = [
    (r"\brm\s+(-[a-z]*\s+)*-[a-z]*[rf][a-z]*\s+(-[a-z]+\s+)*(/|~|\$HOME|/\*|\*|\.\.?)(\s|$|[;&|])", "recursive delete of the root, home or whole folder"),
    (r"\b(rd|rmdir)\s+(/s|/q)[^|;&]*\b[A-Za-z]:\\?(\s|$)", "recursive delete of a whole drive"),
    (r"\bdel\b[^|;&]*\s/[sq]\b[^|;&]*[A-Za-z]:\\", "mass delete on a drive"),
    (r"\bformat\s+[A-Za-z]:", "formatting a drive"),
    (r"\bmkfs(\.\w+)?\b|\bdiskpart\b|\bbcdedit\b", "disk or boot configuration"),
    (r"\bdd\s+[^|;&]*\bof=/dev/", "writing directly to a device"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "a fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "shutting the machine down"),
    (r"\breg\s+(delete|add)\b", "editing the Windows registry"),
    (r"(curl|wget|iwr|invoke-webrequest)\b[^|;&]*\|\s*(sudo\s+)?(sh|bash|zsh|iex|invoke-expression|powershell|pwsh)\b", "piping a download into a shell"),
    (r"\b(cat|type|more|less|head|tail|get-content|gc|copy|cp|xcopy|curl|scp)\b[^|;&]*(\.env\b|\.pem\b|\.key\b|id_rsa|id_ed25519|\.p12\b|\.pfx\b)", "reading or sending secret files"),
    (r"\b(printenv|get-childitem\s+env:)\b|^\s*env\s*$", "dumping environment variables"),
]
# Allowed only after an explicit yes, every time (never "always allow").
_DESTRUCTIVE = [
    (r"\b(rm|del|erase|rmdir|rd|remove-item)\b", "deletes files or folders"),
    (r"\bgit\s+(push|reset\s+--hard|clean|rebase|checkout\s+--|branch\s+-D|stash\s+(drop|clear))", "rewrites or publishes Git history, or discards changes"),
    (r"\b(npm|yarn|pnpm)\s+(publish|unpublish|uninstall)\b|\bpip\s+uninstall\b", "publishes or removes packages"),
    (r"\bdrop\s+(table|database|schema)\b|\btruncate\s+table\b", "destroys database data"),
    (r"\b(move|mv|ren|rename)\b", "moves or renames files"),
]
_NETWORK = re.compile(r"\b(curl|wget|iwr|invoke-webrequest|npm\s+install|pip\s+install|git\s+(clone|fetch|pull|push))\b", re.IGNORECASE)


def classify_command(args: dict) -> Classification:
    command = args.get("command", "")
    for pattern, reason in _DENY:
        if re.search(pattern, command, re.IGNORECASE):
            return Classification(Level.DESTRUCTIVE, f"Blocked: {reason}", deny=True)
    for pattern, reason in _DESTRUCTIVE:
        if re.search(pattern, command, re.IGNORECASE):
            return Classification(Level.DESTRUCTIVE, f"This command {reason}")
    if _NETWORK.search(command):
        return Classification(Level.EXECUTE, "This command uses the network")
    return Classification(Level.EXECUTE, "Runs a command in your project folder")


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # the whole process group, not just the shell
        except ProcessLookupError:
            pass


def _drain(stream, buffer: list[str]) -> None:
    kept = 0
    while chunk := stream.read(4096):
        if kept < MAX_CAPTURE:
            buffer.append(chunk[: MAX_CAPTURE - kept])
        kept += len(chunk)  # keep reading even past the cap, so the program never blocks on a full pipe


def run_shell(command: str, cwd: Path, timeout: int) -> tuple[int | None, str, bool]:
    """(exit code or None if killed, combined output, timed out)."""
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, shell=True, cwd=cwd, env=filtered_env(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", **kwargs)
    buffer: list[str] = []
    reader = threading.Thread(target=_drain, args=(proc.stdout, buffer), daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        proc.wait()
    reader.join(timeout=5)
    return (None if timed_out else proc.returncode), "".join(buffer), timed_out


def run_command(ctx: ToolContext, args: dict) -> ToolOutput:
    code, output, timed_out = run_shell(args["command"], ctx.root, args["timeout_seconds"])
    text, _ = clip(output.strip(), OUTPUT_LIMIT, keep_tail=OUTPUT_LIMIT // 2)
    if timed_out:
        return ToolOutput(f"{text}\n[killed after {args['timeout_seconds']} s: timed out]".strip(), ok=False)
    return ToolOutput(f"{text}\n[exit code {code}]".strip(), ok=code == 0)