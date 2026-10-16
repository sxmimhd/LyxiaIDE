"""Parses and applies SEARCH/REPLACE edit blocks produced by an LLM."""
import re
from dataclasses import dataclass

SEARCH_START = re.compile(r"^<{5,9}\s*SEARCH\s*$")
DIVIDER = re.compile(r"^={5,9}\s*$")
REPLACE_END = re.compile(r"^>{5,9}\s*REPLACE\s*$")


@dataclass
class EditBlock:
    search: str
    replace: str


@dataclass
class BlockResult:
    status: str  # applied | not_found | ambiguous | empty_search
    message: str


def parse_blocks(text: str) -> tuple[list[EditBlock], str, bool]:
    """Returns (blocks, explanation, incomplete). The explanation is the prose outside the blocks;
    incomplete is True if the reply ended in the middle of a block."""
    blocks: list[EditBlock] = []
    prose: list[str] = []
    search: list[str] = []
    replace: list[str] = []
    state = "outside"
    for raw in text.replace("\r\n", "\n").split("\n"):
        marker = raw.strip()
        if state == "outside":
            if SEARCH_START.match(marker):
                state, search = "search", []
            elif not marker.startswith("```"):  # models often wrap the blocks in a code fence
                prose.append(raw)
        elif state == "search":
            if DIVIDER.match(marker):
                state, replace = "replace", []
            else:
                search.append(raw)
        else:
            if REPLACE_END.match(marker):
                blocks.append(EditBlock("\n".join(search), "\n".join(replace)))
                state = "outside"
            else:
                replace.append(raw)
    return blocks, "\n".join(prose).strip(), state != "outside"


def _selection_span(text: str, selection: tuple[int, int] | None) -> tuple[int, int] | None:
    """Character range covered by the selected lines (1-based, inclusive)."""
    if selection is None:
        return None
    lines = text.split("\n")
    first, last = max(selection[0], 1), min(selection[1], len(lines))
    if first > last:
        return None
    start = sum(len(line) + 1 for line in lines[: first - 1])
    end = sum(len(line) + 1 for line in lines[:last]) - 1
    return start, end


def _find_all(text: str, needle: str) -> list[int]:
    found: list[int] = []
    pos = text.find(needle)
    while pos != -1:
        found.append(pos)
        pos = text.find(needle, pos + 1)
    return found


def _apply_loose(text: str, search: str, replace: str, selection: tuple[int, int] | None) -> tuple[str, BlockResult]:
    """Fallback: match line by line ignoring leading/trailing whitespace, then re-indent the replacement."""
    lines = text.split("\n")
    wanted = search.split("\n")
    n = len(wanted)
    wanted_norm = [line.strip() for line in wanted]
    candidates = [i for i in range(len(lines) - n + 1) if [line.strip() for line in lines[i : i + n]] == wanted_norm]
    if selection and len(candidates) > 1:
        inside = [i for i in candidates if i >= selection[0] - 1 and i + n <= selection[1]]
        if len(inside) == 1:
            candidates = inside
    if not candidates:
        return text, BlockResult("not_found", "The code to change was not found in the file")
    if len(candidates) > 1:
        return text, BlockResult("ambiguous", f"The code to change matches {len(candidates)} places")

    i = candidates[0]
    file_first = next((line for line in lines[i : i + n] if line.strip()), "")
    search_first = next((line for line in wanted if line.strip()), "")
    file_indent = file_first[: len(file_first) - len(file_first.lstrip())]
    search_indent = search_first[: len(search_first) - len(search_first.lstrip())]
    new_lines = replace.split("\n") if replace != "" else []
    if file_indent != search_indent and file_indent.startswith(search_indent):
        extra = file_indent[len(search_indent):]
        new_lines = [extra + line if line.strip() else line for line in new_lines]
    lines[i : i + n] = new_lines
    return "\n".join(lines), BlockResult("applied", "Matched after ignoring whitespace differences")


def _apply_one(text: str, block: EditBlock, selection: tuple[int, int] | None) -> tuple[str, BlockResult]:
    search_lines = block.search.split("\n")
    while search_lines and not search_lines[0].strip():
        search_lines.pop(0)
    while search_lines and not search_lines[-1].strip():
        search_lines.pop()
    search = "\n".join(search_lines)

    if not search.strip():
        if not text.strip():  # empty file: the replacement is the whole new content
            return block.replace, BlockResult("applied", "Wrote the new content")
        return text, BlockResult("empty_search", "The edit did not say which code to change")

    matches = _find_all(text, search)
    span = _selection_span(text, selection)
    if len(matches) > 1 and span:
        inside = [m for m in matches if m >= span[0] and m + len(search) <= span[1]]
        if len(inside) == 1:
            matches = inside
    if len(matches) == 1:
        start = matches[0]
        end = start + len(search)
        if block.replace == "" and text[end : end + 1] == "\n":  # deleting whole lines: no blank line left behind
            end += 1
        return text[:start] + block.replace + text[end:], BlockResult("applied", "Exact match")
    if len(matches) > 1:
        return text, BlockResult("ambiguous", f"The code to change matches {len(matches)} places")
    return _apply_loose(text, search, block.replace, selection)


def apply_blocks(content: str, blocks: list[EditBlock],
                 selection: tuple[int, int] | None = None) -> tuple[str, list[BlockResult]]:
    """Apply the blocks in order. Edits that cannot be applied are reported and skipped."""
    eol = "\r\n" if "\r\n" in content else "\n"
    text = content.replace("\r\n", "\n")
    results: list[BlockResult] = []
    for block in blocks:
        text, result = _apply_one(text, block, selection)
        results.append(result)
    return (text.replace("\n", eol) if eol != "\n" else text), results