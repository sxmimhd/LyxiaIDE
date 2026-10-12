from pathlib import Path
from statistics import median

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.indexing.chunker import Chunk, build_chunks, chunk_source

router = APIRouter(prefix="/index", tags=["indexing"])


class ChunkRequest(BaseModel):
    path: str  # repository root
    extra_ignore: list[str] = []
    sample: int = Field(10, ge=0, le=100)  # how many example chunks to include


class ChunkInfo(BaseModel):
    id: str
    path: str
    kind: str
    name: str
    start_line: int
    end_line: int
    chars: int
    part: int
    parts: int


class ChunkStats(BaseModel):
    root: str
    file_count: int
    chunk_count: int
    total_chars: int
    median_chars: int
    max_chars: int
    by_kind: dict[str, int]
    by_category: dict[str, int]
    largest: list[ChunkInfo]
    sample: list[ChunkInfo]


class FileChunksRequest(BaseModel):
    path: str  # repository root
    file: str  # path relative to the root


def _info(c: Chunk) -> ChunkInfo:
    return ChunkInfo(id=c.id, path=c.path, kind=c.kind, name=c.name, start_line=c.start_line,
                     end_line=c.end_line, chars=len(c.text), part=c.part, parts=c.parts)


def _root(path: str) -> Path:
    root = Path(path).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    return root.resolve()


@router.post("/chunks", response_model=ChunkStats)
def chunk_stats(req: ChunkRequest) -> ChunkStats:
    """Chunk the whole repository and report statistics (nothing is stored or embedded yet)."""
    chunks = build_chunks(_root(req.path), req.extra_ignore)
    sizes = [len(c.text) for c in chunks] or [0]
    by_kind: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for c in chunks:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
        by_category[c.category] = by_category.get(c.category, 0) + 1
    step = max(len(chunks) // req.sample, 1) if req.sample else 1
    return ChunkStats(
        root=str(_root(req.path)),
        file_count=len({c.path for c in chunks}),
        chunk_count=len(chunks),
        total_chars=sum(sizes),
        median_chars=int(median(sizes)),
        max_chars=max(sizes),
        by_kind=dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        by_category=dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        largest=[_info(c) for c in sorted(chunks, key=lambda c: -len(c.text))[:5]],
        sample=[_info(c) for c in chunks[::step][: req.sample]],
    )


@router.post("/chunks/file", response_model=list[Chunk])
def file_chunks(req: FileChunksRequest) -> list[Chunk]:
    """The chunks of a single file, with their text. Handy for judging chunk quality."""
    root = _root(req.path)
    target = (root / req.file).resolve()
    try:
        rel = target.relative_to(root).as_posix()
    except ValueError:
        raise HTTPException(status_code=400, detail="File is outside the repository") from None
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Not a file: {rel}")
    return chunk_source(rel, target.read_bytes())