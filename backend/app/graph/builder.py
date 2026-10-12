"""Builds the import graph of a repository and answers questions about it."""
import os
import threading
from collections import deque
from pathlib import Path

from app.graph import resolver as R
from app.parsing.code_parser import EXTENSION_TO_LANGUAGE, MAX_PARSE_BYTES, ParsedFile, parse_file
from app.repo.languages import DEFAULT_IGNORED_DIRS, IGNORED_FILES


class RepoGraph:
    def __init__(self, root: str) -> None:
        self.root = root
        self.files: dict[str, ParsedFile] = {}  # relative posix path -> parsed structure
        self.edges: dict[str, dict[str, set[str]]] = {}  # importer -> {imported file -> names}
        self.imported_by: dict[str, set[str]] = {}
        self.external: dict[str, set[str]] = {}  # file -> third-party package names
        self.unresolved: list[tuple[str, str, str]] = []  # (file, import source, reason)

    @property
    def edge_count(self) -> int:
        return sum(len(t) for t in self.edges.values())

    def _add_edge(self, src: str, dst: str, names: list[str]) -> None:
        if src == dst:
            return
        self.edges.setdefault(src, {}).setdefault(dst, set()).update(names)
        self.imported_by.setdefault(dst, set()).add(src)

    def link(self) -> None:
        files = set(self.files)
        py_roots, package_dirs = R.python_roots({f for f in files if f.endswith((".py", ".pyi"))})
        for path, parsed in self.files.items():
            for imp in parsed.imports:
                if parsed.language == "python":
                    targets = R.resolve_python(path, imp.source, imp.names, files, py_roots, package_dirs)
                    for t in targets:
                        self._add_edge(path, t, imp.names)
                    if not targets:
                        kind = R.python_kind(imp.source)
                        if kind == "package":
                            self.external.setdefault(path, set()).add(R.python_package(imp.source))
                        elif kind == "relative":
                            self.unresolved.append((path, imp.source, "relative import not found"))
                else:
                    target = R.resolve_js(path, imp.source, files)
                    if target:
                        self._add_edge(path, target, imp.names)
                        continue
                    kind = R.js_kind(imp.source)
                    if kind == "package":
                        self.external.setdefault(path, set()).add(R.js_package(imp.source))
                    elif kind == "alias":
                        self.unresolved.append((path, imp.source, "path alias (not resolved yet)"))
                    elif kind == "relative":
                        self.unresolved.append((path, imp.source, "relative import not found"))

    # ---- queries ----
    @staticmethod
    def _bfs(adjacency: dict[str, set[str]] | dict[str, dict[str, set[str]]], start: str,
             max_depth: int | None) -> list[tuple[str, int]]:
        seen = {start: 0}
        queue = deque([start])
        out: list[tuple[str, int]] = []
        while queue:
            current = queue.popleft()
            depth = seen[current]
            if max_depth is not None and depth >= max_depth:
                continue
            for nxt in sorted(adjacency.get(current, ())):
                if nxt not in seen:
                    seen[nxt] = depth + 1
                    queue.append(nxt)
                    out.append((nxt, depth + 1))
        return out

    def dependents(self, path: str, max_depth: int | None = None) -> list[tuple[str, int]]:
        """Everything that (transitively) imports `path`, with distance. This is the blast radius."""
        return self._bfs(self.imported_by, path, max_depth)

    def dependencies(self, path: str, max_depth: int | None = None) -> list[tuple[str, int]]:
        return self._bfs(self.edges, path, max_depth)

    def most_imported(self, n: int = 10) -> list[tuple[str, int]]:
        ranked = sorted(((p, len(s)) for p, s in self.imported_by.items()), key=lambda x: (-x[1], x[0]))
        return ranked[:n]

    def cycles(self) -> list[list[str]]:
        """Groups of files that import each other in a loop (Tarjan's SCC, iterative)."""
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        result: list[list[str]] = []
        counter = 0
        for start in sorted(self.files):
            if start in index:
                continue
            index[start] = low[start] = counter
            counter += 1
            stack.append(start)
            on_stack.add(start)
            work = [(start, iter(sorted(self.edges.get(start, {}))))]
            while work:
                node, it = work[-1]
                descended = False
                for nxt in it:
                    if nxt not in index:
                        index[nxt] = low[nxt] = counter
                        counter += 1
                        stack.append(nxt)
                        on_stack.add(nxt)
                        work.append((nxt, iter(sorted(self.edges.get(nxt, {})))))
                        descended = True
                        break
                    if nxt in on_stack:
                        low[node] = min(low[node], index[nxt])
                if descended:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.append(w)
                        if w == node:
                            break
                    if len(component) > 1:
                        result.append(sorted(component))
        return sorted(result)


def _discover(root: Path, ignored: set[str]) -> dict[str, tuple[int, int]]:
    """Parseable source files -> (mtime_ns, size). Also used as the cache signature."""
    found: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignored and not d.endswith(".egg-info")]
        for name in filenames:
            if name in IGNORED_FILES or os.path.splitext(name)[1].lower() not in EXTENSION_TO_LANGUAGE:
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.islink(full):
                    continue
                st = os.stat(full)
            except OSError:
                continue
            if st.st_size <= MAX_PARSE_BYTES:
                found[os.path.relpath(full, root).replace(os.sep, "/")] = (st.st_mtime_ns, st.st_size)
    return found


_CACHE: dict[tuple[str, tuple[str, ...]], tuple[int, RepoGraph]] = {}
_LOCK = threading.Lock()


def build_graph(root: Path, extra_ignore: list[str] | None = None) -> RepoGraph:
    """Build (or reuse, if no file changed) the import graph for `root`."""
    ignored = DEFAULT_IGNORED_DIRS | set(extra_ignore or [])
    found = _discover(root, ignored)
    signature = hash(tuple(sorted(found.items())))
    key = (str(root), tuple(sorted(ignored)))
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        graph = RepoGraph(str(root))
        for rel in sorted(found):
            try:
                graph.files[rel] = parse_file(root / rel)
            except Exception:  # one unreadable file must not break the whole graph
                continue
        graph.link()
        _CACHE[key] = (signature, graph)
        while len(_CACHE) > 8:
            _CACHE.pop(next(iter(_CACHE)))
        return graph