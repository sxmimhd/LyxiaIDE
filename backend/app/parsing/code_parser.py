"""Turns a source file into structure (functions, classes, imports...) using Tree-sitter."""
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import tree_sitter_javascript as ts_js
import tree_sitter_python as ts_py
import tree_sitter_typescript as ts_ts
from pydantic import BaseModel
from tree_sitter import Language, Node, Parser

MAX_PARSE_BYTES = 1_000_000

UnitKind = Literal["function", "component", "class", "method", "interface", "type", "enum", "constant", "variable"]

EXTENSION_TO_LANGUAGE = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
}

PY_COMPOUND = {
    "if_statement", "elif_clause", "else_clause", "try_statement", "except_clause", "except_group_clause",
    "finally_clause", "with_statement", "for_statement", "while_statement", "block", "match_statement", "case_clause",
}

FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}


class CodeUnit(BaseModel):
    kind: UnitKind
    name: str
    qualified_name: str  # e.g. "AuthService.login"
    parent: str | None  # qualified name of the enclosing class, if any
    start_line: int  # 1-based, inclusive
    end_line: int
    signature: str  # declaration up to the body, whitespace-collapsed
    exported: bool  # exported (JS/TS) or public, i.e. no leading underscore (Python)
    doc: str | None = None  # first line of the docstring / JSDoc


class ImportInfo(BaseModel):
    source: str  # module path, e.g. "./auth" or "os.path"
    names: list[str]  # imported names ([] for plain `import x`)
    line: int


class ParsedFile(BaseModel):
    path: str
    language: str
    line_count: int
    units: list[CodeUnit]
    imports: list[ImportInfo]
    exports: list[str]
    has_errors: bool  # the file has syntax errors; the result is best-effort


class UnsupportedLanguageError(Exception):
    pass


class FileTooLargeError(Exception):
    pass


@lru_cache
def _language(name: str) -> Language:
    return Language(
        {
            "python": ts_py.language,
            "javascript": ts_js.language,
            "typescript": ts_ts.language_typescript,
            "tsx": ts_ts.language_tsx,
        }[name]()
    )


class _Extractor:
    def __init__(self, src: bytes, language: str) -> None:
        self.src = src
        self.language = language
        self.units: list[CodeUnit] = []
        self.imports: list[ImportInfo] = []
        self.exports: list[str] = []

    # ---- helpers ----
    def text(self, node: Node | None) -> str:
        return "" if node is None else self.src[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def signature(self, node: Node, body: Node | None = None) -> str:
        body = body if body is not None else node.child_by_field_name("body")
        end = node.end_byte if body is node or body is None else body.start_byte
        sig = re.sub(r"\s+", " ", self.src[node.start_byte : end].decode("utf-8", "replace")).strip()
        return sig.rstrip(":{ ").strip()[:200]

    def add(self, kind: UnitKind, name: str, node: Node, parent: str | None, exported: bool,
            sig: str, doc: str | None = None) -> str:
        qualified = f"{parent}.{name}" if parent else name
        self.units.append(CodeUnit(
            kind=kind, name=name, qualified_name=qualified, parent=parent,
            start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
            signature=sig, exported=exported, doc=doc,
        ))
        return qualified

    # ---- Python ----
    def python(self, node: Node, parent: str | None = None, in_class: bool = False) -> None:
        for child in node.children:
            target = child
            if child.type == "decorated_definition":  # keep decorators inside the unit's line range
                target = child.child_by_field_name("definition")
                if target is None:
                    continue
            t = target.type
            if t == "function_definition":
                name = self.text(target.child_by_field_name("name"))
                self.add("method" if in_class else "function", name, child, parent, not name.startswith("_"),
                         self.signature(target), self.py_doc(target))
            elif t == "class_definition":
                name = self.text(target.child_by_field_name("name"))
                qualified = self.add("class", name, child, parent, not name.startswith("_"),
                                     self.signature(target), self.py_doc(target))
                body = target.child_by_field_name("body")
                if body is not None:
                    self.python(body, qualified, in_class=True)
            elif t == "import_statement":
                for n in target.children_by_field_name("name"):
                    mod = n.child_by_field_name("name") if n.type == "aliased_import" else n
                    self.imports.append(ImportInfo(source=self.text(mod), names=[], line=target.start_point[0] + 1))
            elif t == "import_from_statement":
                names = [
                    self.text(n.child_by_field_name("name")) if n.type == "aliased_import" else self.text(n)
                    for n in target.children_by_field_name("name")
                ]
                if any(c.type == "wildcard_import" for c in target.children):
                    names = ["*"]
                self.imports.append(ImportInfo(
                    source=self.text(target.child_by_field_name("module_name")), names=names,
                    line=target.start_point[0] + 1))
            elif t in PY_COMPOUND:
                self.python(target, parent, in_class)  # e.g. `if TYPE_CHECKING:` / `try: ... except ImportError:`
            elif t == "expression_statement" and not in_class:
                assign = target.named_children[0] if target.named_children else None
                if assign is not None and assign.type == "assignment":
                    left = assign.child_by_field_name("left")
                    if left is not None and left.type == "identifier" and re.fullmatch(r"[A-Z][A-Z0-9_]*", self.text(left)):
                        self.add("constant", self.text(left), target, parent, True, self.signature(target, target))

    def py_doc(self, node: Node) -> str | None:
        body = node.child_by_field_name("body")
        first = body.named_children[0] if body is not None and body.named_children else None
        if first is None or first.type != "expression_statement" or not first.named_children:
            return None
        string = first.named_children[0]
        if string.type != "string":
            return None
        raw = re.sub(r"""^[rRuUbBfF]*("{3}|'{3}|"|')""", "", self.text(string))
        for line in raw.splitlines():
            line = line.strip().strip("\"'").strip()
            if line:
                return line[:160]
        return None

    # ---- JavaScript / TypeScript ----
    def is_component(self, name: str) -> bool:
        return name[:1].isupper() and self.language in ("tsx", "javascript")

    def jsdoc(self, anchor: Node) -> str | None:
        prev = anchor.prev_sibling
        if prev is None or prev.type != "comment" or not self.text(prev).startswith("/**"):
            return None
        for line in self.text(prev).splitlines():
            line = line.strip().lstrip("/*").strip().rstrip("*/").strip()
            if line and not line.startswith("@"):
                return line[:160]
        return None

    def js(self, node: Node, parent: str | None = None) -> None:
        for child in node.children:
            if child.type == "export_statement":
                self.js_export(child, parent)
            else:
                self.js_decl(child, parent, False, child)

    def js_export(self, node: Node, parent: str | None) -> None:
        decl = node.child_by_field_name("declaration")
        source = node.child_by_field_name("source")
        names: list[str] = []
        if decl is not None:
            before = len(self.units)
            self.js_decl(decl, parent, True, node)
            names = [u.name for u in self.units[before:] if u.parent == parent]
        else:
            for clause in (c for c in node.named_children if c.type == "export_clause"):
                for spec in clause.named_children:
                    if spec.type == "export_specifier":
                        names.append(self.text(spec.child_by_field_name("alias") or spec.child_by_field_name("name")))
            if any(c.type == "default" for c in node.children):
                value = node.child_by_field_name("value")
                if value is not None and value.type in FUNCTION_VALUES:
                    self.add("function", "default", node, parent, True, self.signature(node, value.child_by_field_name("body")))
            elif source is not None and not names:
                names = ["*"]  # export * from "..."
        if any(c.type == "default" for c in node.children):
            names.append("default")
        self.exports.extend(n for n in names if n not in self.exports)
        if source is not None:  # re-export: also a dependency on another module
            self.imports.append(ImportInfo(source=self.text(source).strip("'\"`"), names=names, line=node.start_point[0] + 1))

    def js_decl(self, node: Node, parent: str | None, exported: bool, anchor: Node) -> None:
        t = node.type
        if t in ("function_declaration", "generator_function_declaration"):
            name = self.text(node.child_by_field_name("name"))
            kind: UnitKind = "component" if self.is_component(name) else "function"
            self.add(kind, name, anchor if exported else node, parent, exported, self.signature(node), self.jsdoc(anchor))
        elif t in ("class_declaration", "abstract_class_declaration"):
            name = self.text(node.child_by_field_name("name"))
            qualified = self.add("class", name, anchor if exported else node, parent, exported, self.signature(node), self.jsdoc(anchor))
            body = node.child_by_field_name("body")
            if body is not None:
                self.js_class_body(body, qualified, exported)
        elif t in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
            kind = {"interface_declaration": "interface", "type_alias_declaration": "type", "enum_declaration": "enum"}[t]
            self.add(kind, self.text(node.child_by_field_name("name")), anchor if exported else node, parent, exported,
                     self.signature(node), self.jsdoc(anchor))
        elif t in ("lexical_declaration", "variable_declaration"):
            declarators = [c for c in node.named_children if c.type == "variable_declarator"]
            for d in declarators:
                name_node, value = d.child_by_field_name("name"), d.child_by_field_name("value")
                if name_node is None or name_node.type != "identifier":
                    continue  # destructuring etc.
                name = self.text(name_node)
                single = len(declarators) == 1
                span = (anchor if exported else node) if single else d
                sig_node = node if single else d  # single: "const X = ..." including the keyword
                if value is not None and value.type in FUNCTION_VALUES:
                    kind = "component" if self.is_component(name) else "function"
                    self.add(kind, name, span, parent, exported,
                             self.signature(sig_node, value.child_by_field_name("body")), self.jsdoc(anchor))
                elif exported or (parent is None and name.isupper()):
                    kind = "constant" if self.text(node).startswith("const") else "variable"
                    self.add(kind, name, span, parent, exported, self.signature(sig_node, sig_node), self.jsdoc(anchor))
        elif t == "import_statement":
            self.js_import(node)

    def js_class_body(self, body: Node, parent: str, exported: bool) -> None:
        for member in body.named_children:
            t = member.type
            is_method = t in ("method_definition", "abstract_method_signature")
            if t in ("public_field_definition", "field_definition"):
                value = member.child_by_field_name("value")
                is_method = value is not None and value.type in FUNCTION_VALUES  # arrow-function class property
            if not is_method:
                continue
            name = self.text(member.child_by_field_name("name"))
            body_node = member.child_by_field_name("body")
            if body_node is None and t in ("public_field_definition", "field_definition"):
                body_node = member.child_by_field_name("value").child_by_field_name("body")  # type: ignore[union-attr]
            self.add("method", name, member, parent, exported and not name.startswith(("_", "#")),
                     self.signature(member, body_node), self.jsdoc(member))

    def js_import(self, node: Node) -> None:
        names: list[str] = []
        for clause in (c for c in node.children if c.type == "import_clause"):
            for part in clause.children:
                if part.type == "identifier":
                    names.append(self.text(part))  # default import
                elif part.type == "named_imports":
                    names.extend(self.text(s.child_by_field_name("name")) for s in part.named_children if s.type == "import_specifier")
                elif part.type == "namespace_import":
                    names.append("* as " + self.text(part.named_children[0]) if part.named_children else "*")
        source = self.text(node.child_by_field_name("source")).strip("'\"`")
        self.imports.append(ImportInfo(source=source, names=names, line=node.start_point[0] + 1))


def parse_source(src: bytes, language: str, path: str = "") -> ParsedFile:
    tree = Parser(_language(language)).parse(src)  # a Parser per call: they are not thread-safe
    extractor = _Extractor(src, language)
    if language == "python":
        extractor.python(tree.root_node)
    else:
        extractor.js(tree.root_node)
    return ParsedFile(
        path=path, language=language, line_count=len(src.splitlines()),
        units=extractor.units, imports=extractor.imports, exports=extractor.exports,
        has_errors=tree.root_node.has_error,
    )


def parse_file(path: Path) -> ParsedFile:
    language = EXTENSION_TO_LANGUAGE.get(path.suffix.lower())
    if language is None:
        raise UnsupportedLanguageError(f"Unsupported file type: {path.suffix or path.name}")
    if path.stat().st_size > MAX_PARSE_BYTES:
        raise FileTooLargeError(f"File is larger than {MAX_PARSE_BYTES} bytes")
    return parse_source(path.read_bytes(), language, str(path))