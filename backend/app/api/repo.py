from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.graph.builder import build_graph
from app.parsing.code_parser import FileTooLargeError, ParsedFile, UnsupportedLanguageError, parse_file
from app.repo.scanner import ScanResult, classify, scan_repository

router = APIRouter(prefix="/repo", tags=["repository"])


class ScanRequest(BaseModel):
    path: str
    extra_ignore: list[str] = []  # extra directory names to skip, e.g. ["generated"]


@router.post("/scan", response_model=ScanResult)
def scan(req: ScanRequest) -> ScanResult:
    """Scan a project folder. A plain `def` route: FastAPI runs it in a worker
    thread, so the blocking disk walk never freezes the event loop."""
    root = Path(req.path).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path}")
    return scan_repository(root.resolve(), req.extra_ignore)


class ParseRequest(BaseModel):
    path: str  # absolute path of one source file


@router.post("/parse", response_model=ParsedFile)
def parse(req: ParseRequest) -> ParsedFile:
    """Parse one file into functions, classes, methods, imports and exports."""
    path = Path(req.path).expanduser()
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {req.path}")
    try:
        return parse_file(path.resolve())
    except UnsupportedLanguageError as e:
        raise HTTPException(status_code=415, detail=str(e)) from e
    except FileTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e


class GraphRequest(BaseModel):
    path: str  # repository root
    extra_ignore: list[str] = []


class Ranked(BaseModel):
    name: str
    count: int


class UnresolvedImport(BaseModel):
    file: str
    source: str
    reason: str


class GraphSummary(BaseModel):
    root: str
    file_count: int
    edge_count: int
    most_imported: list[Ranked]  # the files everything depends on
    external_packages: list[Ranked]  # third-party packages, by number of files using them
    unresolved_count: int
    unresolved_sample: list[UnresolvedImport]
    circular_groups: list[list[str]]  # files that import each other in a loop


class ImpactRequest(GraphRequest):
    file: str  # path relative to the repository root, or absolute inside it
    max_depth: int | None = None  # None = follow dependents all the way


class Dependent(BaseModel):
    path: str
    distance: int  # 1 = imports the file directly


class ImpactResult(BaseModel):
    file: str
    language: str
    imports: list[str]  # direct dependencies of the file
    imported_by: list[str]  # direct dependents
    affected: list[Dependent]  # transitive dependents (the blast radius)
    affected_count: int
    affected_tests: list[str]  # test files inside the blast radius


def _repo_root(path: str) -> Path:
    root = Path(path).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    return root.resolve()


@router.post("/graph", response_model=GraphSummary)
def graph_summary(req: GraphRequest) -> GraphSummary:
    """Build the import graph and summarize it. The graph is cached until a file changes."""
    graph = build_graph(_repo_root(req.path), req.extra_ignore)
    package_usage: dict[str, int] = {}
    for packages in graph.external.values():
        for pkg in packages:
            package_usage[pkg] = package_usage.get(pkg, 0) + 1
    top_packages = sorted(package_usage.items(), key=lambda x: (-x[1], x[0]))[:30]
    return GraphSummary(
        root=graph.root,
        file_count=len(graph.files),
        edge_count=graph.edge_count,
        most_imported=[Ranked(name=p, count=c) for p, c in graph.most_imported(10)],
        external_packages=[Ranked(name=p, count=c) for p, c in top_packages],
        unresolved_count=len(graph.unresolved),
        unresolved_sample=[UnresolvedImport(file=f, source=s, reason=r) for f, s, r in graph.unresolved[:30]],
        circular_groups=graph.cycles(),
    )


@router.post("/impact", response_model=ImpactResult)
def impact(req: ImpactRequest) -> ImpactResult:
    """What is affected if this file changes? Direct and transitive dependents, plus affected tests."""
    root = _repo_root(req.path)
    graph = build_graph(root, req.extra_ignore)

    rel = req.file.replace("\\", "/")
    if Path(req.file).is_absolute():
        try:
            rel = Path(req.file).resolve().relative_to(root).as_posix()
        except ValueError:
            raise HTTPException(status_code=400, detail="File is outside the repository") from None
    if rel not in graph.files:
        raise HTTPException(status_code=404, detail=f"File not in the graph (unsupported or ignored): {rel}")

    affected = graph.dependents(rel, req.max_depth)
    tests = [p for p, _ in affected if classify(p, p.rsplit("/", 1)[-1], graph.files[p].language) == "test"]
    return ImpactResult(
        file=rel,
        language=graph.files[rel].language,
        imports=sorted(graph.edges.get(rel, {})),
        imported_by=sorted(graph.imported_by.get(rel, set())),
        affected=[Dependent(path=p, distance=d) for p, d in affected],
        affected_count=len(affected),
        affected_tests=tests,
    )