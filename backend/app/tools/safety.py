"""Shared safety rules: which paths the AI may touch, how output is limited, what the environment hides."""
import os
import re
from pathlib import Path, PurePosixPath

from app.repo.languages import is_sensitive
from app.tools.base import ToolError

FORBIDDEN_DIRS = {".git", ".lyxia"}  # .git/config can hold credentials; .lyxia holds our own state
MAX_READ_BYTES = 1_000_000
_SECRET_NAME = re.compile(r"(^|_)(API_?KEY|APIKEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|AUTH)($|_)", re.IGNORECASE)


def resolve_inside(root: Path, raw: str) -> tuple[Path, str]:
    """(real absolute path, posix path relative to the project). Raises ToolError for anything outside."""
    text = (raw or ".").strip().replace("\\", "/")
    if "\x00" in text:
        raise ToolError("Invalid path")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        try:
            text = Path(text).resolve().relative_to(root).as_posix() or "."
        except (ValueError, OSError):
            raise ToolError("Path is outside the project") from None
    if ".." in PurePosixPath(text).parts:
        raise ToolError("Path points outside the project")
    try:
        real = (root / text).resolve()
        rel = real.relative_to(root).as_posix() or "."  # symlinks must not lead out
    except (ValueError, OSError):
        raise ToolError("Path resolves to somewhere outside the project") from None
    if any(part in FORBIDDEN_DIRS for part in PurePosixPath(rel).parts):
        raise ToolError("That folder holds internal tool state or credentials and is off limits")
    return real, rel


def check_readable_file(real: Path, rel: str) -> None:
    if is_sensitive(real.name):
        raise ToolError("Secrets and key files are never shared with the AI")
    if not real.is_file():
        raise ToolError(f"Not a file: {rel}")
    if real.stat().st_size > MAX_READ_BYTES:
        raise ToolError("File is too large to read")
    with open(real, "rb") as f:
        if b"\x00" in f.read(8000):
            raise ToolError("Binary file")


def clip(text: str, limit: int, *, keep_tail: int = 0) -> tuple[str, bool]:
    """Shorten long output, keeping the start (and optionally the end, where build errors usually are)."""
    if len(text) <= limit:
        return text, False
    head = limit - keep_tail
    omitted = len(text) - limit
    tail = text[-keep_tail:] if keep_tail else ""
    return f"{text[:head]}\n… [{omitted} characters omitted] …\n{tail}", True


def filtered_env() -> dict[str, str]:
    """The environment for child processes, without anything that looks like a secret."""
    env = {k: v for k, v in os.environ.items() if not _SECRET_NAME.search(k)}
    env["GIT_TERMINAL_PROMPT"] = "0"  # git must never wait for a password
    env["GIT_PAGER"] = "cat"
    return env