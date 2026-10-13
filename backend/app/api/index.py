import time
from pathlib import Path
from statistics import median

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.indexing.chunker import Chunk, build_chunks, chunk_source
from app.indexing.keyword import get_keyword_index
from app.indexing.retrieval import Mode, retrieve
from app.indexing.service import (
    IndexReport, IndexStatus, NoIndexableFilesError, NotIndexedError,
    build_index, collection_name, index_status,
)
from app.indexing.store import get_store
from app.llm.client import LLMError
from app.llm.embeddings import get_embedder

router = APIRouter(prefix="/index", tags=["indexing"])


# ---------- chunk inspection ----------
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
    """Chunk the whole repository and report statistics (nothing is stored or embedded)."""
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


# ---------- persistent index ----------
class BuildRequest(BaseModel):
    path: str  # repository root
    extra_ignore: list[str] = []


class PathRequest(BaseModel):
    path: str


class SearchRequest(BaseModel):
    path: str  # repository root
    query: str
    top_k: int = Field(5, ge=1, le=50)
    categories: list[str] | None = None  # e.g. ["source"], ["test"], ["docs", "config"]
    kinds: list[str] | None = None  # e.g. ["function", "method", "class"]
    include_text: bool = False  # return each chunk's full text (the AI will use this later)
    mode: Mode = "hybrid"  # hybrid = semantic + keyword fused | semantic | keyword


class SearchHit(BaseModel):
    score: float  # hybrid: fused RRF score | semantic: cosine similarity | keyword: BM25 (higher is better)
    semantic_rank: int | None  # position in the semantic ranking (None = not found there)
    keyword_rank: int | None  # position in the keyword ranking (None = not found there)
    semantic_score: float | None
    path: str
    kind: str
    category: str
    name: str
    start_line: int
    end_line: int
    signature: str | None
    doc: str | None
    preview: str
    text: str | None = None


class SearchResult(BaseModel):
    model: str
    mode: str
    warnings: list[str]  # e.g. semantic search unavailable, so only keyword results are shown
    took_ms: int
    index_complete: bool  # False: the last build did not finish, so some code may be missing from results
    hits: list[SearchHit]


@router.post("/build", response_model=IndexReport)
async def build(req: BuildRequest) -> IndexReport:
    """Index (or update the index of) a repository. Only new or changed chunks are embedded,
    so the first run is slow and later runs are fast. Progress is saved batch by batch."""
    try:
        return await build_index(get_store(), get_keyword_index(), get_embedder(), _root(req.path), req.extra_ignore)
    except NoIndexableFilesError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/status", response_model=IndexStatus)
async def status(req: PathRequest) -> IndexStatus:
    return await run_in_threadpool(index_status, get_store(), get_embedder(), _root(req.path))


@router.post("/clear")
async def clear(req: PathRequest) -> dict[str, str]:
    """Delete the index of a repository (e.g. to rebuild from scratch)."""
    name = collection_name(_root(req.path), get_embedder().model)
    await run_in_threadpool(get_store().drop, name)
    await run_in_threadpool(get_keyword_index().drop, name)
    return {"cleared": name}


@router.post("/search", response_model=SearchResult)
async def search(req: SearchRequest) -> SearchResult:
    """Search the indexed repository: hybrid (default), semantic only, or keyword only."""
    embedder = get_embedder()
    started = time.perf_counter()
    try:
        result = await retrieve(get_store(), get_keyword_index(), embedder, _root(req.path), req.query, req.top_k,
                                req.mode, req.categories, req.kinds)
    except NotIndexedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    hits = [
        SearchHit(
            score=round(h.score, 4), semantic_rank=h.semantic_rank, keyword_rank=h.keyword_rank,
            semantic_score=round(h.semantic_score, 4) if h.semantic_score is not None else None,
            path=h.payload["path"], kind=h.payload["kind"], category=h.payload["category"], name=h.payload["name"],
            start_line=h.payload["start_line"], end_line=h.payload["end_line"],
            signature=h.payload.get("signature"), doc=h.payload.get("doc"),
            preview=h.payload["text"][:240], text=h.payload["text"] if req.include_text else None,
        )
        for h in result.hits
    ]
    complete = (await run_in_threadpool(index_status, get_store(), embedder, _root(req.path))).complete
    return SearchResult(model=embedder.model, mode=req.mode, warnings=result.warnings,
                        took_ms=int((time.perf_counter() - started) * 1000), index_complete=complete, hits=hits)