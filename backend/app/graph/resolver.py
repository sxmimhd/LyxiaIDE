"""Resolves import statements to files inside the repository."""
import posixpath
import sys

JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
# TypeScript ESM convention: `import "./x.js"` really means x.ts / x.tsx
JS_REMAP = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
ASSET_EXTS = {".css", ".scss", ".sass", ".less", ".json", ".svg", ".png", ".jpg", ".jpeg", ".gif",
              ".webp", ".ico", ".woff", ".woff2", ".ttf", ".md", ".html", ".txt", ".wasm"}


def resolve_js(importer: str, source: str, files: set[str]) -> str | None:
    """Resolve a relative import ("./x", "../y/index") to a repo file, or None."""
    if not source.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), source))
    stem, ext = posixpath.splitext(base)
    candidates = [base]
    candidates += [stem + e for e in JS_REMAP.get(ext, ())]
    candidates += [base + e for e in JS_EXTS]
    candidates += [posixpath.join(base, "index" + e) for e in JS_EXTS]
    return next((c for c in candidates if c in files), None)


def js_kind(source: str) -> str:
    """Classify a non-resolved JS/TS import: relative | asset | builtin | alias | package."""
    if source.startswith("."):
        return "asset" if posixpath.splitext(source)[1].lower() in ASSET_EXTS else "relative"
    if source.startswith(("node:", "bun:")):
        return "builtin"
    if source.startswith(("@/", "~/", "#", "/")):
        return "alias"
    return "package"


def js_package(source: str) -> str:
    parts = source.split("/")
    return "/".join(parts[:2]) if source.startswith("@") and len(parts) > 1 else parts[0]


def _module_file(module: str, base: str, files: set[str]) -> str | None:
    """module is slash-separated ("app/api"); "" means the package at `base` itself."""
    path = posixpath.join(base, module) if module else base
    candidates = [posixpath.join(path, "__init__.py")] if not module else [
        path + ".py", path + ".pyi", posixpath.join(path, "__init__.py")]
    return next((c for c in candidates if c in files), None)


class PythonResolver:
    """Resolves Python imports to repo files. Works with regular packages (__init__.py)
    and namespace packages (no __init__.py), because Python itself supports both."""

    def __init__(self, files: set[str]) -> None:
        self.files = files
        self.package_dirs = {posixpath.dirname(f) for f in files if posixpath.basename(f) == "__init__.py"}
        roots = {""}
        for d in self.package_dirs:  # the parent of a top-level package is a source root
            parent = posixpath.dirname(d)
            if parent != d and parent not in self.package_dirs:
                roots.add(parent)
        self.roots = sorted(roots)
        # folder name -> the folders that contain a folder of that name
        self.folder_index: dict[str, set[str]] = {}
        for f in files:
            parts = f.split("/")[:-1]
            for i, part in enumerate(parts):
                self.folder_index.setdefault(part, set()).add("/".join(parts[:i]))

    def _bases(self, importer: str, top: str) -> list[str]:
        """Folders that could be on sys.path for this importer, best guess first."""
        bases: list[str] = []
        d = posixpath.dirname(importer)
        while True:  # ancestors, nearest first: "python -m ..." can be run from any of them
            if not d or d not in self.package_dirs:  # a folder inside a package is never a sys.path entry
                bases.append(d)
            if not d:
                break
            d = posixpath.dirname(d)
        bases += self.roots
        containing = self.folder_index.get(top, set())
        if len(containing) == 1:  # e.g. tests/ importing `app.main` that lives in backend/app
            bases += [c for c in containing if not c or c not in self.package_dirs]
        return list(dict.fromkeys(bases))

    def resolve(self, importer: str, source: str, names: list[str]) -> list[str]:
        """Files this import points at: the module itself plus any imported submodules."""
        if source.startswith("."):
            dots = len(source) - len(source.lstrip("."))
            module = source[dots:].replace(".", "/")
            base = posixpath.dirname(importer)
            for _ in range(dots - 1):
                base = posixpath.dirname(base)
            bases = [base]
        else:
            top = source.split(".")[0]
            if top in sys.stdlib_module_names:
                return []  # never bind `import logging` to a local logging.py
            module = source.replace(".", "/")
            bases = self._bases(importer, top)

        for base in bases:
            found: list[str] = []
            module_file = _module_file(module, base, self.files)
            if module_file:
                found.append(module_file)
            for name in names:
                if name != "*":
                    sub = _module_file(posixpath.join(module, name) if module else name, base, self.files)
                    if sub and sub not in found:
                        found.append(sub)
            if found:
                return found
        return []


def python_kind(source: str) -> str:
    if source.startswith("."):
        return "relative"
    return "stdlib" if source.split(".")[0] in sys.stdlib_module_names else "package"


def python_package(source: str) -> str:
    return source.split(".")[0]