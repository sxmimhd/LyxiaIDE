"""Index a repository incrementally and search it."""
import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.indexing.chunker import build_chunks
from app.indexing.store import VectorStore
from app.llm.embeddings import EmbeddingClient

BATCH = 64  # chunks embedded and saved together: if something fails, finished batches are kept


class NoIndexableFilesError(Exception):
    pass


class NotIndexedError(Exception):
    pass


class IndexReport(BaseModel):
    collection: str
    model: str
    dimensions: int
    total_chunks: int
    added: int  # new chunks
    updated: int  # chunks whose text changed
    unchanged: int  # reused from the index: no embedding cost
    deleted: int  # chunks whose code no longer exists
    embed_seconds: float
    total_seconds: float


class IndexStatus(BaseModel):
    indexed: bool  # the index contains data
    complete: bool  # False while building, or if the last build failed part-way: results may be partial
    collection: str
    chunk_count: int = 0
    model: str | None = None
    dimensions: int | None = None
    indexed_at: str | None = None


def collection_name(root: Path, model: str) -> str:
    key = f"{os.path.normcase(str(root))}|{model}"
    return "repo_" + hashlib.sha1(key.encode()).hexdigest()[:16]


def _embed_hash(embedder: EmbeddingClient, text: str) -> str:
    # The document prefix is part of what gets embedded, so changing it must invalidate old vectors.
    return hashlib.sha256((embedder.document_prefix + text).encode()).hexdigest()


async def build_index(store: VectorStore, embedder: EmbeddingClient, root: Path,
                      extra_ignore: list[str] | None = None) -> IndexReport:
    started = time.perf_counter()
    chunks = await run_in_threadpool(build_chunks, root, extra_ignore)
    if not chunks:
        raise NoIndexableFilesError("No indexable files found")

    name = collection_name(root, embedder.model)
    texts = {c.id: c.embedding_text() for c in chunks}
    hashes = {cid: _embed_hash(embedder, t) for cid, t in texts.items()}
    existing = await run_in_threadpool(store.state, name) if store.exists(name) else {}

    todo = [c for c in chunks if existing.get(c.id) != hashes[c.id]]
    stale = [cid for cid in existing if cid not in hashes]
    added = sum(1 for c in todo if c.id not in existing)

    meta = store.get_meta(name) or {}
    dimensions = int(meta.get("dimensions", 0))
    # Mark the index incomplete until this build finishes, so a crash is never mistaken for success.
    store.set_meta(name, {**meta, "root": str(root), "model": embedder.model, "dimensions": dimensions,
                          "complete": False})
    embed_started = time.perf_counter()
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        vectors = await embedder.embed_documents([texts[c.id] for c in batch])
        if not dimensions:
            dimensions = len(vectors[0])
        await run_in_threadpool(store.ensure, name, dimensions)
        await run_in_threadpool(store.upsert, name, batch, [hashes[c.id] for c in batch], vectors)
    embed_seconds = time.perf_counter() - embed_started

    await run_in_threadpool(store.delete, name, stale)
    total = await run_in_threadpool(store.count, name)
    store.set_meta(name, {
        "root": str(root), "model": embedder.model, "dimensions": dimensions, "chunk_count": total,
        "complete": True, "indexed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return IndexReport(
        collection=name, model=embedder.model, dimensions=dimensions, total_chunks=len(chunks),
        added=added, updated=len(todo) - added, unchanged=len(chunks) - len(todo), deleted=len(stale),
        embed_seconds=round(embed_seconds, 2), total_seconds=round(time.perf_counter() - started, 2),
    )


def index_status(store: VectorStore, embedder: EmbeddingClient, root: Path) -> IndexStatus:
    name = collection_name(root, embedder.model)
    meta = store.get_meta(name)
    count = store.count(name)
    if not meta or count == 0:
        return IndexStatus(indexed=False, complete=False, collection=name)
    return IndexStatus(indexed=True, complete=bool(meta.get("complete")), collection=name, chunk_count=count,
                       model=meta.get("model"), dimensions=meta.get("dimensions"), indexed_at=meta.get("indexed_at"))


async def search_index(store: VectorStore, embedder: EmbeddingClient, root: Path, query: str, top_k: int,
                       categories: list[str] | None = None,
                       kinds: list[str] | None = None) -> list[tuple[float, dict[str, Any]]]:
    name = collection_name(root, embedder.model)
    if await run_in_threadpool(store.count, name) == 0:
        raise NotIndexedError("This repository is not indexed yet. Call POST /index/build first.")
    query_vector = await embedder.embed_query(query)
    return await run_in_threadpool(store.search, name, query_vector, top_k, categories, kinds)