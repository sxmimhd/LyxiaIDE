"""Hybrid retrieval: semantic (vectors) + keyword (BM25), merged with Reciprocal Rank Fusion."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi.concurrency import run_in_threadpool

from app.indexing.keyword import KeywordIndex
from app.indexing.service import NotIndexedError, collection_name
from app.indexing.store import VectorStore
from app.llm.client import LLMError
from app.llm.embeddings import EmbeddingClient

Mode = Literal["hybrid", "semantic", "keyword"]
RRF_K = 60  # standard constant: damps the advantage of rank 1 over rank 2


@dataclass
class RetrievedChunk:
    payload: dict[str, Any]
    score: float  # hybrid: fused RRF score | semantic: cosine similarity | keyword: -bm25
    semantic_rank: int | None = None  # 1-based position in the semantic ranking, if it was found there
    keyword_rank: int | None = None
    semantic_score: float | None = None


@dataclass
class Retrieval:
    hits: list[RetrievedChunk]
    warnings: list[str] = field(default_factory=list)


def rrf(rankings: dict[str, list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: each ranking gives an item 1/(k+rank) points. Needs no score
    calibration between retrievers, which is why it suits mixing cosine similarity with BM25."""
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    for ranking in rankings.values():
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            best_rank[item] = min(best_rank.get(item, rank), rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], best_rank[kv[0]], kv[0]))


async def retrieve(store: VectorStore, keyword: KeywordIndex, embedder: EmbeddingClient, root: Path, query: str,
                   top_k: int, mode: Mode = "hybrid", categories: list[str] | None = None,
                   kinds: list[str] | None = None, query_vector: list[float] | None = None) -> Retrieval:
    name = collection_name(root, embedder.model)
    if await run_in_threadpool(store.count, name) == 0 and await run_in_threadpool(keyword.count, name) == 0:
        raise NotIndexedError("This repository is not indexed yet. Call POST /index/build first.")

    depth = max(top_k * 4, 30)  # candidates taken from each retriever before fusing
    warnings: list[str] = []
    semantic: list[tuple[float, dict[str, Any]]] = []
    keyword_hits: list[tuple[str, float]] = []

    if mode in ("semantic", "hybrid"):
        try:
            vector = query_vector or await embedder.embed_query(query)
            semantic = await run_in_threadpool(store.search, name, vector, depth, categories, kinds)
        except LLMError as e:
            if mode == "semantic":
                raise
            warnings.append(f"Semantic search unavailable ({e}). Showing keyword results only.")
    if mode in ("keyword", "hybrid"):
        keyword_hits = await run_in_threadpool(keyword.search, name, query, depth, categories, kinds)

    sem_ids = [p["chunk_id"] for _, p in semantic]
    kw_ids = [cid for cid, _ in keyword_hits]
    sem_info = {p["chunk_id"]: (rank, score) for rank, (score, p) in enumerate(semantic, 1)}
    kw_info = {cid: (rank, score) for rank, (cid, score) in enumerate(keyword_hits, 1)}

    if mode == "semantic":
        ordered = [(cid, sem_info[cid][1]) for cid in sem_ids]
    elif mode == "keyword" or not sem_ids:
        ordered = [(cid, kw_info[cid][1]) for cid in kw_ids]
    else:
        ordered = rrf({"semantic": sem_ids, "keyword": kw_ids})
    ordered = ordered[:top_k]

    payloads = {p["chunk_id"]: p for _, p in semantic}
    missing = [cid for cid, _ in ordered if cid not in payloads]
    payloads.update(await run_in_threadpool(store.get_payloads, name, missing))

    hits = [
        RetrievedChunk(
            payload=payloads[cid], score=score,
            semantic_rank=sem_info[cid][0] if cid in sem_info else None,
            keyword_rank=kw_info[cid][0] if cid in kw_info else None,
            semantic_score=sem_info[cid][1] if cid in sem_info else None,
        )
        for cid, score in ordered if cid in payloads
    ]
    return Retrieval(hits=hits, warnings=warnings)