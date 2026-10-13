"""Temporary in-memory vector cache + brute-force search. Replaced by a real vector database next step."""
import hashlib
import heapq

from app.indexing.chunker import Chunk
from app.llm.embeddings import EmbeddingClient

MAX_CACHED_VECTORS = 50_000

# (model, sha256 of the exact text that was embedded) -> unit-length vector
_VECTORS: dict[tuple[str, str], list[float]] = {}


def _key(model: str, chunk: Chunk) -> tuple[str, str]:
    return model, hashlib.sha256(chunk.embedding_text().encode()).hexdigest()


async def embed_chunks(client: EmbeddingClient, chunks: list[Chunk]) -> tuple[list[list[float]], int]:
    """Vectors for `chunks` (same order) and how many had to be newly embedded.
    Unchanged chunks are served from the cache: only new or edited code costs GPU time."""
    keys = [_key(client.model, c) for c in chunks]
    missing: dict[tuple[str, str], Chunk] = {}
    for key, chunk in zip(keys, chunks):
        if key not in _VECTORS:
            missing.setdefault(key, chunk)  # identical texts are embedded once
    if missing:
        vectors = await client.embed_documents([c.embedding_text() for c in missing.values()])
        for key, vector in zip(missing, vectors):
            _VECTORS[key] = vector
        while len(_VECTORS) > MAX_CACHED_VECTORS:
            _VECTORS.pop(next(iter(_VECTORS)))
    return [_VECTORS[k] for k in keys], len(missing)


def top_k(query: list[float], vectors: list[list[float]], k: int) -> list[tuple[int, float]]:
    """Indexes and cosine scores of the k most similar vectors (all vectors are unit length)."""
    scores = [sum(a * b for a, b in zip(query, v)) for v in vectors]
    best = heapq.nlargest(k, range(len(scores)), key=scores.__getitem__)
    return [(i, scores[i]) for i in best]