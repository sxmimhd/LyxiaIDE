"""Decides which files a request touches, and enforces what the AI is allowed to edit."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.repo.languages import CONFIG_NAMES, DEFAULT_IGNORED_DIRS, EXTENSIONS, is_sensitive

MAX_FILE_BYTES = 200_000

PLAN_SYSTEM_PROMPT = """You decide which files must change to carry out a request in a codebase.
Reply with ONLY lines of this form:
EDIT: relative/path/to/existing_file
NEW: relative/path/to/new_file
List only the files that truly must change, most important first, at most {max_files} lines.
Use the paths exactly as shown to you. No explanations and no code."""

_LINE = re.compile(r"^[\s\-*\d.)(]*(EDIT|NEW)\s*:\s*`?([^\s`]+)`?", re.IGNORECASE)


@dataclass
class PlannedFile:
    rel: str  # posix path relative to the project root
    abs: Path
    is_new: bool


@dataclass
class Plan:
    files: list[PlannedFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # why some suggestions were dropped


def parse_plan(text: str) -> list[tuple[str, str]]:
    """(kind, path) pairs in the order the model gave them, without duplicates."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = _LINE.match(line)
        if not m:
            continue
        rel = m.group(2).strip().replace("\\", "/").rstrip(",;")
        rel = rel[2:] if rel.startswith("./") else rel
        if rel and rel not in seen:
            seen.add(rel)
            out.append((m.group(1).upper(), rel))
    return out


def _nearest_existing(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def check_path(root: Path, rel: str, *, must_exist: bool) -> tuple[Path | None, str | None]:
    """Returns (absolute path, None) if the AI may edit/create `rel`, else (None, reason).
    This is the safety gate: the model chooses paths, so nothing it says is trusted."""
    if not rel or "\x00" in rel:
        return None, "empty path"
    if rel.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", rel):
        return None, "not a path inside the project"
    parts = PurePosixPath(rel).parts
    if ".." in parts:
        return None, "points outside the project"
    if any(p in DEFAULT_IGNORED_DIRS for p in parts[:-1]):
        return None, "inside a folder the AI never edits (node_modules, .git, build output...)"
    name = parts[-1]
    if is_sensitive(name):
        return None, "secrets and key files are off limits"
    if os.path.splitext(name)[1].lower() not in EXTENSIONS and name not in CONFIG_NAMES:
        return None, "not a source, text or config file type"

    target = root / rel
    anchor = _nearest_existing(target)
    try:
        real_anchor = anchor.resolve()
        real_anchor.relative_to(root)  # symlinks must not lead out of the project
    except (ValueError, OSError):
        return None, "resolves to somewhere outside the project"

    if must_exist:
        if not target.is_file():
            return None, "file does not exist"
        if target.stat().st_size > MAX_FILE_BYTES:
            return None, "file is too large for AI editing"
        try:
            with open(target, "rb") as f:
                if b"\x00" in f.read(8000):
                    return None, "binary file"
        except OSError:
            return None, "cannot be read"
    elif target.exists():
        return None, "already exists"
    return target, None


def build_plan(root: Path, entries: list[tuple[str, str]], candidates: list[str], max_files: int) -> Plan:
    plan = Plan()
    by_name: dict[str, list[str]] = {}
    for c in candidates:
        by_name.setdefault(PurePosixPath(c).name, []).append(c)

    for kind, rel in entries:
        if len(plan.files) >= max_files:
            plan.notes.append(f"Ignored {rel}: at most {max_files} files per request")
            continue
        exists = (root / rel).is_file() if ".." not in PurePosixPath(rel).parts else False
        if not exists and kind == "EDIT":
            same_name = by_name.get(PurePosixPath(rel).name, [])
            if len(same_name) == 1:  # the model garbled the folder but the file name identifies it
                plan.notes.append(f"Corrected {rel} to {same_name[0]}")
                rel, exists = same_name[0], True
        if any(f.rel == rel for f in plan.files):
            continue
        if kind == "EDIT" and not exists:  # only NEW: may create files; a made-up EDIT path must not create strays
            plan.notes.append(f"Skipped {rel}: the plan said EDIT but that file does not exist")
            continue
        target, problem = check_path(root, rel, must_exist=exists)
        if problem or target is None:
            plan.notes.append(f"Skipped {rel}: {problem}")
            continue
        plan.files.append(PlannedFile(rel=rel, abs=target, is_new=not exists))
    return plan