"""Splits source files into chunks along code boundaries (functions, classes, doc sections)."""
import hashlib
import os
import re
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel

from app.parsing.code_parser import EXTENSION_TO_LANGUAGE, CodeUnit, parse_source
from app.repo.languages import (
    CONFIG_NAMES, DEFAULT_IGNORED_DIRS, EXTENSIONS, IGNORED_FILES, is_sensitive,
)
from app.repo.scanner import classify

MAX_CHUNK_CHARS = 2400  # ~600 tokens: small enough to embed precisely, large enough to keep meaning
OVERLAP_LINES = 2  # lines repeated between the parts of a split chunk
MAX_FILE_BYTES = 1_000_000

CHUNK_KINDS = {"function", "method", "class", "component", "interface", "type", "enum"}
COMMENT_PREFIXES = ("//", "/*", "*", "#")
IDENTIFIER = re.compile(r"\w")
HEADING = re.compile(r"^#{1,6}\s+(.*\S)")


class Chunk(BaseModel):
    id: str  # stable across line shifts: derived from path + symbol name
    path: str
    language: str | None
    category: str  # source | test | config | docs | other
    kind: str  # function | method | class | component | interface | type | enum | module | doc | config | text
    name: str  # qualified symbol ("AuthService.login"), section title, or the file path
    start_line: int
    end_line: int
    text: str
    content_hash: str  # sha256 of text: lets us skip re-embedding unchanged chunks
    part: int = 1
    parts: int = 1
    # Set when the text was assembled from separate line ranges (module / class leftovers):
    # the exact (start, end) ranges it came from. Empty means start_line..end_line is contiguous.
    spans: list[tuple[int, int]] = []
    signature: str | None = None
    doc: str | None = None

    def embedding_text(self) -> str:
        """What gets embedded: the code plus a small header that tells the model where it lives."""
        lines = [f"File: {self.path}", f"{self.kind}: {self.name}"]
        if self.signature and self.signature != self.name:
            lines.append(f"Signature: {self.signature}")
        if self.doc:
            lines.append(f"Doc: {self.doc}")
        if self.parts > 1:
            lines.append(f"(part {self.part} of {self.parts})")
        return "\n".join(lines) + "\n\n" + self.text


def _windows(lines: list[str], first: int, max_chars: int = MAX_CHUNK_CHARS) -> list[tuple[int, int, str]]:
    """Split lines into pieces of at most max_chars with a little overlap."""
    out: list[tuple[int, int, str]] = []
    i, n = 0, len(lines)
    while i < n:
        j, size = i, 0
        while j < n and (j == i or size + len(lines[j]) + 1 <= max_chars):
            size += len(lines[j]) + 1
            j += 1
        text = "\n".join(lines[i:j])
        if j - i == 1 and len(text) > max_chars:  # a single enormous line (minified code, data)
            out += [(first + i, first + i, text[k : k + max_chars]) for k in range(0, len(text), max_chars)]
        else:
            out.append((first + i, first + j - 1, text))
        if j >= n:
            break
        i = max(j - OVERLAP_LINES, i + 1)
    return out


class _Chunker:
    def __init__(self, path: str, language: str | None, category: str, lines: list[str]) -> None:
        self.path, self.language, self.category, self.lines = path, language, category, lines
        self.chunks: list[Chunk] = []
        self._ids: dict[str, int] = {}

    def _unique_id(self, base: str) -> str:
        n = self._ids.get(base, 0)
        self._ids[base] = n + 1
        digest = hashlib.sha1((base if n == 0 else f"{base}#{n}").encode()).hexdigest()[:16]
        return digest

    def emit(self, kind: str, name: str, start: int, end: int, text: str, *, id_key: str | None = None,
             unit: CodeUnit | None = None, part: int = 1, parts: int = 1,
             spans: list[tuple[int, int]] | None = None) -> None:
        if not text.strip():
            return
        self.chunks.append(Chunk(
            id=self._unique_id(f"{self.path}::{id_key or f'{kind}:{name}'}::{part}"),
            path=self.path, language=self.language, category=self.category, kind=kind, name=name,
            start_line=start, end_line=end, text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            part=part, parts=parts, spans=spans or [],
            signature=unit.signature if unit else None, doc=unit.doc if unit else None,
        ))

    def emit_range(self, kind: str, name: str, start: int, end: int, unit: CodeUnit | None = None,
                   id_key: str | None = None) -> None:
        """Emit lines start..end (1-based, inclusive), split into parts if too big."""
        segment = self.lines[start - 1 : end]
        pieces = _windows(segment, start) if len("\n".join(segment)) > MAX_CHUNK_CHARS else [(start, end, "\n".join(segment))]
        for idx, (s, e, text) in enumerate(pieces, 1):
            self.emit(kind, name, s, e, text, id_key=id_key, unit=unit, part=idx, parts=len(pieces))

    def extend_start(self, start: int, floor: int) -> int:
        """Pull directly-attached comment lines (JSDoc, // notes, # notes) into the unit."""
        while start > floor and self.lines[start - 2].strip().startswith(COMMENT_PREFIXES):
            start -= 1
        return start

    # ---- code files ----
    def code(self, units: list[CodeUnit]) -> None:
        children: dict[str | None, list[CodeUnit]] = {}
        for u in units:
            if u.kind in CHUNK_KINDS:
                children.setdefault(u.parent, []).append(u)

        def emit_unit(u: CodeUnit, floor: int, out: list[tuple[int, int]]) -> int:
            start, end = self.extend_start(u.start_line, floor), u.end_line
            kids = sorted(children.get(u.qualified_name, []), key=lambda k: k.start_line)
            size = len("\n".join(self.lines[start - 1 : end]))
            if size <= MAX_CHUNK_CHARS or not kids:
                self.emit_range(u.kind, u.qualified_name, start, end, u)
            else:  # a big class: its header (signature, fields) as one chunk, then each member
                inner: list[tuple[int, int]] = []
                first_kid = max(self.extend_start(kids[0].start_line, start + 1), start + 1)
                if first_kid > start:
                    self.emit_range(u.kind, u.qualified_name, start, first_kid - 1, u,
                                    id_key=f"{u.kind}:{u.qualified_name}:header")
                    inner.append((start, first_kid - 1))
                kid_floor = first_kid
                for kid in kids:
                    kid_floor = emit_unit(kid, kid_floor, inner)
                # class-level statements between or after the members (e.g. `__eq__ = object.__eq__`)
                self.leftovers(inner, start, end, u.kind, u.qualified_name,
                               f"{u.kind}:{u.qualified_name}:rest", u)
            out.append((start, end))
            return end + 1

        covered: list[tuple[int, int]] = []
        floor = 1
        for u in sorted(children.get(None, []), key=lambda k: k.start_line):
            floor = emit_unit(u, floor, covered)
        # everything outside a unit (imports, constants, top-level statements) becomes 'module' chunks
        self.leftovers(covered, 1, len(self.lines), "module", self.path, "module")

    def leftovers(self, covered: list[tuple[int, int]], lo: int, hi: int, kind: str, name: str,
                  id_key: str, unit: CodeUnit | None = None) -> None:
        """Chunk the lines in lo..hi that no chunk has taken yet, so no code is ever lost."""
        taken: set[int] = set()
        for s, e in covered:
            taken.update(range(max(s, lo), min(e, hi) + 1))
        runs: list[tuple[int, int]] = []
        i = lo
        while i <= hi:
            if i in taken:
                i += 1
                continue
            j = i
            while j <= hi and j not in taken:
                j += 1
            s, e = i, j - 1
            while s <= e and not self.lines[s - 1].strip():
                s += 1
            while e >= s and not self.lines[e - 1].strip():
                e -= 1
            if s <= e:
                runs.append((s, e))
            i = j

        group: list[tuple[int, int]] = []
        pieces: list[tuple[int, int, str, list[tuple[int, int]]]] = []

        def flush() -> None:
            if group:
                text = "\n\n".join("\n".join(self.lines[s - 1 : e]) for s, e in group)
                pieces.append((group[0][0], group[-1][1], text, list(group) if len(group) > 1 else []))
                group.clear()

        for s, e in runs:
            run_text = "\n".join(self.lines[s - 1 : e])
            if len(run_text) > MAX_CHUNK_CHARS:
                flush()
                pieces.extend((a, b, t, []) for a, b, t in _windows(self.lines[s - 1 : e], s))
                continue
            current = sum(len("\n".join(self.lines[a - 1 : b])) + 2 for a, b in group)
            if group and current + len(run_text) > MAX_CHUNK_CHARS:
                flush()
            group.append((s, e))
        flush()

        kept = [p for p in pieces if IDENTIFIER.search(p[2])]  # drop pure punctuation such as a lone "}"
        for idx, (s, e, text, spans) in enumerate(kept, 1):
            self.emit(kind, name, s, e, text, id_key=id_key, unit=unit, part=idx, parts=len(kept), spans=spans)

    # ---- docs, config and everything we don't parse ----
    def text_file(self) -> None:
        kind = "config" if self.category == "config" else "text"
        if self.language == "Markdown":
            sections: list[tuple[int, str]] = []  # (start line, title)
            for i, line in enumerate(self.lines, 1):
                m = HEADING.match(line)
                if m:
                    sections.append((i, m.group(1)))
            if sections:
                if sections[0][0] > 1:
                    sections.insert(0, (1, self.path))
                for idx, (start, title) in enumerate(sections):
                    end = (sections[idx + 1][0] - 1) if idx + 1 < len(sections) else len(self.lines)
                    self.emit_range("doc", title, start, end, id_key=f"doc:{idx}:{title}")
                return
            kind = "doc"
        for idx, (s, e, text) in enumerate(_windows(self.lines, 1), 1):
            self.emit(kind, self.path, s, e, text, id_key=kind, part=idx, parts=0)
        total = len(self.chunks)
        for c in self.chunks:
            c.parts = total


def looks_minified(source: str, line_count: int) -> bool:
    return len(source) > 20_000 and len(source) / max(line_count, 1) > 400


def chunk_source(rel_path: str, source: bytes) -> list[Chunk]:
    """Chunk one file. `rel_path` is the posix path relative to the repository root."""
    name = rel_path.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1].lower()
    language = EXTENSIONS.get(ext)
    category = classify(rel_path, name, language)
    text = source.decode("utf-8", "replace")
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]  # match tree-sitter's row numbering
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or looks_minified(text, len(lines)):
        return []

    chunker = _Chunker(rel_path, language, category, lines)
    parse_language = EXTENSION_TO_LANGUAGE.get(ext)
    if parse_language:
        parsed = parse_source(source, parse_language, rel_path)
        chunker.code(parsed.units)
    else:
        chunker.text_file()
    return chunker.chunks


def iter_text_files(root: Path, extra_ignore: list[str] | None = None) -> Iterator[tuple[str, Path]]:
    """Files worth indexing: known text types, no secrets, no generated or vendored folders."""
    ignored = DEFAULT_IGNORED_DIRS | set(extra_ignore or [])
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in ignored and not d.endswith(".egg-info"))
        for name in sorted(filenames):
            if name in IGNORED_FILES or is_sensitive(name) or name.endswith((".min.js", ".min.css")):
                continue
            if os.path.splitext(name)[1].lower() not in EXTENSIONS and name not in CONFIG_NAMES:
                continue
            full = Path(dirpath) / name
            try:
                if full.is_symlink() or full.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield full.relative_to(root).as_posix(), full


def build_chunks(root: Path, extra_ignore: list[str] | None = None) -> list[Chunk]:
    chunks: list[Chunk] = []
    for rel, path in iter_text_files(root, extra_ignore):
        try:
            chunks.extend(chunk_source(rel, path.read_bytes()))
        except Exception:  # one bad file must not stop indexing
            continue
    return chunks