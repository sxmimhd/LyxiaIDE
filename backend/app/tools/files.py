"""READ tools: look around the project without changing anything."""
import fnmatch
import os
import re

from app.indexing.chunker import iter_text_files
from app.repo.languages import DEFAULT_IGNORED_DIRS, is_sensitive
from app.tools.base import ToolContext, ToolError, ToolOutput
from app.tools.safety import check_readable_file, clip, resolve_inside

MAX_LIST = 200
READ_LINES = 400  # lines returned per call
READ_CHARS = 20_000
MAX_SEARCH = 100
MAX_PATTERN = 200
# Catastrophic-backtracking shape such as (a+)+ or (.*)*: refuse it, a bad pattern must not hang the server.
_NESTED_REPEAT = re.compile(r"\([^()]*[+*][^()]*\)[+*{]")


def list_directory(ctx: ToolContext, args: dict) -> str:
    real, rel = resolve_inside(ctx.root, args["path"])
    if not real.is_dir():
        raise ToolError(f"Not a folder: {rel}")
    entries = []
    for e in sorted(os.scandir(real), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
        if e.name in DEFAULT_IGNORED_DIRS or is_sensitive(e.name):
            continue  # build output, dependencies and secrets are hidden
        entries.append(e.name + ("/" if e.is_dir(follow_symlinks=False) else ""))
    more = f"\n… and {len(entries) - MAX_LIST} more" if len(entries) > MAX_LIST else ""
    return f"{rel}/  ({len(entries)} entries)\n" + "\n".join(entries[:MAX_LIST]) + more


def read_file(ctx: ToolContext, args: dict) -> str:
    real, rel = resolve_inside(ctx.root, args["path"])
    check_readable_file(real, rel)
    lines = [ln.rstrip("\r") for ln in real.read_bytes().decode("utf-8", errors="replace").split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    start = max(args["start_line"], 1)
    if start > max(total, 1):
        raise ToolError(f"{rel} has only {total} lines")
    end = args["end_line"] or start + READ_LINES - 1
    end = min(end, total, start + READ_LINES - 1)
    body = "\n".join(f"{n:>5} | {lines[n - 1]}" for n in range(start, end + 1))
    body, clipped = clip(body, READ_CHARS)
    note = ""
    if end < total or clipped:
        note = f"\n(more available: call read_file again with start_line={end + 1})"
    return f"{rel}  (lines {start}-{end} of {total}){note}\n{body}"


def search_files(ctx: ToolContext, args: dict) -> str:
    pattern = args["pattern"].lower()
    found: list[str] = []
    for rel, _ in iter_text_files(ctx.root):
        if fnmatch.fnmatch(rel.lower(), pattern) or fnmatch.fnmatch(rel.rsplit("/", 1)[-1].lower(), pattern):
            found.append(rel)
            if len(found) >= MAX_SEARCH:
                break
    if not found:
        return "No files match."
    return f"{len(found)} file(s):\n" + "\n".join(found) + ("\n(limit reached)" if len(found) >= MAX_SEARCH else "")


def grep(ctx: ToolContext, args: dict) -> str:
    pattern = args["pattern"]
    if len(pattern) > MAX_PATTERN:
        raise ToolError("Pattern too long")
    if _NESTED_REPEAT.search(pattern):
        raise ToolError("That regular expression could run forever (nested repetition). Please simplify it.")
    try:
        rx = re.compile(pattern, 0 if args["case_sensitive"] else re.IGNORECASE)
    except re.error as e:
        raise ToolError(f"Invalid regular expression: {e}") from None
    _, scope = resolve_inside(ctx.root, args["path"])
    glob = args["glob"].lower() if args["glob"] else None
    limit = args["max_results"]

    hits: list[str] = []
    files_with_hits = 0
    for rel, full in iter_text_files(ctx.root):  # skips ignored folders, secrets, huge and minified files
        if scope != "." and rel != scope and not rel.startswith(scope + "/"):
            continue
        if glob and not (fnmatch.fnmatch(rel.lower(), glob) or fnmatch.fnmatch(rel.rsplit("/", 1)[-1].lower(), glob)):
            continue
        try:
            lines = full.read_bytes().decode("utf-8", errors="replace").split("\n")
        except OSError:
            continue
        matched = False
        for number, line in enumerate(lines, 1):
            if rx.search(line[:1000]):  # a line length cap also bounds the cost of any one match
                hits.append(f"{rel}:{number}: {line.strip()[:200]}")
                matched = True
                if len(hits) >= limit:
                    break
        files_with_hits += matched
        if len(hits) >= limit:
            break
    if not hits:
        return "No matches."
    capped = "\n(limit reached: refine the pattern or narrow with path/glob)" if len(hits) >= limit else ""
    return f"{len(hits)} match(es) in {files_with_hits} file(s):\n" + "\n".join(hits) + capped