"""Multi-file AI edit: find the relevant files, plan the change, edit each file, stream progress."""
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.editing.planner import PLAN_SYSTEM_PROMPT, PlannedFile, build_plan, check_path, parse_plan
from app.editing.single import EditOutcome, EditRequest, propose_edit
from app.indexing.keyword import get_keyword_index
from app.indexing.retrieval import retrieve
from app.indexing.store import get_store
from app.llm.client import get_llm
from app.llm.embeddings import get_embedder
from app.repo.languages import EXTENSIONS


class OpenFile(BaseModel):
    path: str  # absolute path of a file open in the editor
    content: str  # its CURRENT text, including unsaved changes


class ProjectEditRequest(BaseModel):
    path: str  # project root
    instruction: str
    active_file: str | None = None  # absolute path of the file the user is looking at
    open_files: list[OpenFile] = []
    max_files: int = Field(5, ge=1, le=8)


class FileChange(BaseModel):
    path: str  # relative to the project root (posix)
    abs_path: str
    language: str
    status: str  # modified | created | unchanged | failed
    original: str  # the text the edit was computed from ("" for a new file)
    new_content: str
    applied: int
    failed: int
    explanation: str
    outcomes: list[EditOutcome]


def _rel_inside(root: Path, path: str) -> str | None:
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return None


def _language(rel: str) -> str:
    return EXTENSIONS.get(os.path.splitext(rel)[1].lower(), "").lower()


async def _candidates(root: Path, instruction: str, active_rel: str | None) -> tuple[list[str], dict[str, list[str]], list[str]]:
    """Files that might need to change, with a few symbol names each so the planner can tell them apart."""
    files: list[str] = [active_rel] if active_rel else []
    symbols: dict[str, list[str]] = {}
    notes: list[str] = []
    try:
        result = await retrieve(get_store(), get_keyword_index(), get_embedder(), root, instruction, 10)
        for h in result.hits:
            path, name = h.payload["path"], h.payload["name"]
            if path not in files:
                files.append(path)
            if h.payload["kind"] != "module" and name not in symbols.setdefault(path, []):
                symbols[path].append(name)
        notes.extend(result.warnings)
    except Exception as e:  # not indexed, or the embedding server is down: plan from what we have
        notes.append(f"Repository search unavailable ({e}); planning from the open file only.")
    return files, symbols, notes


def _plan_prompt(instruction: str, files: list[str], symbols: dict[str, list[str]], active_rel: str | None) -> str:
    lines = []
    for f in files:
        syms = ", ".join(symbols.get(f, [])[:6])
        lines.append(f"- {f}" + (f": {syms}" if syms else "") + ("   [open in the editor]" if f == active_rel else ""))
    listing = "\n".join(lines) if lines else "(none found)"
    return (f"Request: {instruction.strip()}\n\nFiles that may be relevant (path: related symbols):\n{listing}\n\n"
            "You may also name a file that is not listed if the request clearly needs it (use NEW: for files to create).")


def _file_instruction(instruction: str, files: list[PlannedFile], target: PlannedFile) -> str:
    text = (f"{instruction.strip()}\n\nThis request spans {len(files)} file(s): {', '.join(f.rel for f in files)}. "
            f"You are editing {target.rel}. Make only the changes needed in THIS file; the others are handled separately.")
    if target.is_new:
        text += (" This file does not exist yet and is empty. Write its complete content in ONE block "
                 "with an empty SEARCH part.")
    return text


def _status(is_new: bool, original: str, new: str, applied: int) -> str:
    if applied == 0:
        return "failed"
    if is_new:
        return "created" if new.strip() else "failed"
    return "modified" if new != original else "unchanged"


async def run_project_edit(req: ProjectEditRequest) -> AsyncIterator[dict[str, Any]]:
    """Yields progress events: status, plan, file (one per file), done. Writes nothing to disk."""
    root = Path(req.path).expanduser().resolve()
    active_rel = _rel_inside(root, req.active_file) if req.active_file else None
    open_map: dict[str, str] = {}
    for f in req.open_files:
        rel = _rel_inside(root, f.path)
        if rel:
            open_map[rel] = f.content

    yield {"type": "status", "message": "Finding relevant files…"}
    candidates, symbols, notes = await _candidates(root, req.instruction, active_rel)

    yield {"type": "status", "message": "Planning which files to change…"}
    reply = await get_llm().chat(
        [{"role": "system", "content": PLAN_SYSTEM_PROMPT.format(max_files=req.max_files)},
         {"role": "user", "content": _plan_prompt(req.instruction, candidates, symbols, active_rel)}],
        temperature=0.1)
    plan = build_plan(root, parse_plan(reply), candidates, req.max_files)
    plan.notes[:0] = notes

    if not plan.files and active_rel:  # the model gave nothing usable: fall back to the file the user is in
        target, problem = check_path(root, active_rel, must_exist=True)
        if target:
            plan.files.append(PlannedFile(rel=active_rel, abs=target, is_new=False))
            plan.notes.append("The plan named no usable file, so only the open file will be edited.")
    if not plan.files:
        yield {"type": "error", "message": "The AI did not name any file it is allowed to edit. "
               "Try a more specific request, or open the file you want changed.", "notes": plan.notes, "raw": reply[:1000]}
        return

    yield {"type": "plan", "files": [{"path": f.rel, "is_new": f.is_new} for f in plan.files], "notes": plan.notes}

    counts = {"modified": 0, "created": 0, "unchanged": 0, "failed": 0}
    for i, pf in enumerate(plan.files, 1):
        yield {"type": "status", "message": f"Editing {pf.rel} ({i}/{len(plan.files)})…"}
        original = "" if pf.is_new else open_map.get(pf.rel, pf.abs.read_bytes().decode("utf-8", errors="replace"))
        result = await propose_edit(EditRequest(
            path=pf.rel, language=_language(pf.rel), content=original,
            instruction=_file_instruction(req.instruction, plan.files, pf)))
        status = _status(pf.is_new, original, result.new_content, result.applied)
        counts[status] += 1
        change = FileChange(
            path=pf.rel, abs_path=str(pf.abs), language=_language(pf.rel), status=status, original=original,
            new_content=result.new_content, applied=result.applied, failed=result.failed,
            explanation=result.explanation, outcomes=result.outcomes)
        yield {"type": "file", "change": change.model_dump()}

    yield {"type": "done", **counts}