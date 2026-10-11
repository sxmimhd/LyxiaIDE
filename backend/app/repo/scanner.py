import json
import os
import re
import tomllib
from pathlib import Path

from pydantic import BaseModel

from app.repo import languages as L

MAX_FILES = 50_000
MAX_FILE_BYTES = 1_000_000  # bigger files are skipped (minified bundles, data dumps...)
LIST_CAP = 25


class LanguageStat(BaseModel):
    language: str
    files: int
    bytes: int


class Manifest(BaseModel):
    path: str
    kind: str  # npm | pip | pyproject
    dependency_count: int
    dependencies: list[str]
    scripts: list[str] = []


class ScanResult(BaseModel):
    root: str
    name: str
    total_files: int
    truncated: bool  # True if the MAX_FILES safety limit was hit
    skipped_large: int
    skipped_sensitive: int  # .env, keys... counted but never listed or read
    languages: list[LanguageStat]
    category_counts: dict[str, int]  # source | test | config | docs | other
    frameworks: list[str]
    manifests: list[Manifest]
    entry_points: list[str]
    config_files: list[str]
    test_files: list[str]
    docs: list[str]


def classify(rel_path: str, name: str, language: str | None) -> str:
    parts = {p.lower() for p in rel_path.split("/")[:-1]}
    if L.TEST_NAME.search(name) or parts & L.TEST_DIRS:
        return "test"
    if name in L.CONFIG_NAMES or ".config." in name or name.startswith((".eslintrc", ".prettierrc")) or language in L.CONFIG_LANGUAGES:
        return "config"
    if language in L.DOC_LANGUAGES or name.upper().startswith(("README", "LICENSE", "CHANGELOG")):
        return "docs"
    return "source" if language else "other"


def _norm(dep: str) -> str:
    return dep.strip().lower().replace("_", "-")


def _parse_manifest(path: Path, rel: str) -> Manifest | None:
    try:
        if path.name == "package.json":
            data = json.loads(path.read_text(encoding="utf-8"))
            deps = [*data.get("dependencies", {}), *data.get("devDependencies", {})]
            return Manifest(path=rel, kind="npm", dependency_count=len(deps), dependencies=deps[:100],
                            scripts=list(data.get("scripts", {})))
        if path.name == "requirements.txt":
            deps = []
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
                if m and not line.lstrip().startswith(("-", "#")):
                    deps.append(m.group(1))
            return Manifest(path=rel, kind="pip", dependency_count=len(deps), dependencies=deps[:100])
        if path.name == "pyproject.toml":
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            project = data.get("project", {})
            raw = [*project.get("dependencies", [])]
            for group in project.get("optional-dependencies", {}).values():
                raw.extend(group)
            deps = [m.group(1) for d in raw if (m := re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", d))]
            deps += [d for d in data.get("tool", {}).get("poetry", {}).get("dependencies", {}) if d != "python"]
            return Manifest(path=rel, kind="pyproject", dependency_count=len(deps), dependencies=deps[:100])
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return None  # unreadable or malformed manifest: ignore it
    return None


def scan_repository(root: Path, extra_ignore: list[str] | None = None) -> ScanResult:
    ignored_dirs = L.DEFAULT_IGNORED_DIRS | set(extra_ignore or [])

    lang_files: dict[str, int] = {}
    lang_bytes: dict[str, int] = {}
    counts = {"source": 0, "test": 0, "config": 0, "docs": 0, "other": 0}
    config_files: list[str] = []
    test_files: list[str] = []
    docs: list[str] = []
    entry_points: list[str] = []
    manifest_paths: list[tuple[Path, str]] = []
    total = skipped_large = skipped_sensitive = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root):  # os.walk does not follow symlinked dirs
        dirnames[:] = sorted(d for d in dirnames if d not in ignored_dirs and not d.endswith(".egg-info"))
        base = Path(dirpath)
        for name in sorted(filenames):
            if name in L.IGNORED_FILES:
                continue
            path = base / name
            if path.is_symlink():
                continue
            if L.is_sensitive(name):
                skipped_sensitive += 1
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                skipped_large += 1
                continue
            if total >= MAX_FILES:
                truncated = True
                break

            rel = path.relative_to(root).as_posix()
            language = L.EXTENSIONS.get(path.suffix.lower())
            category = classify(rel, name, language)

            total += 1
            counts[category] += 1
            if language:
                lang_files[language] = lang_files.get(language, 0) + 1
                lang_bytes[language] = lang_bytes.get(language, 0) + size
            if category == "config":
                config_files.append(rel)
            elif category == "test":
                test_files.append(rel)
            elif category == "docs":
                docs.append(rel)
            if name in L.ENTRY_NAMES and category == "source" and rel.count("/") <= 3:
                entry_points.append(rel)
            if name in L.MANIFEST_NAMES and len(manifest_paths) < 20:
                manifest_paths.append((path, rel))
        if truncated:
            break

    manifests = [m for p, rel in manifest_paths if (m := _parse_manifest(p, rel))]
    all_deps = {_norm(d) for m in manifests for d in m.dependencies}
    frameworks = sorted({label for dep, label in L.FRAMEWORKS.items() if dep in all_deps})

    languages = sorted(
        (LanguageStat(language=k, files=v, bytes=lang_bytes[k]) for k, v in lang_files.items()),
        key=lambda s: (-s.files, s.language),
    )
    return ScanResult(
        root=str(root), name=root.name, total_files=total, truncated=truncated,
        skipped_large=skipped_large, skipped_sensitive=skipped_sensitive,
        languages=languages, category_counts=counts, frameworks=frameworks, manifests=manifests,
        entry_points=sorted(entry_points, key=lambda p: (p.count("/"), p))[:LIST_CAP],
        config_files=config_files[:LIST_CAP], test_files=test_files[:LIST_CAP], docs=docs[:LIST_CAP],
    )