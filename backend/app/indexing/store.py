"""Persistent vector store (embedded Qdrant). One collection per repository and embedding model."""
import json
import threading
import uuid
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models

from app.config import get_settings
from app.indexing.chunker import Chunk


def point_id(chunk_id: str) -> str:
    """Qdrant ids must be ints or UUIDs: derive a stable UUID from the chunk id."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


class VectorStore:
    """Thread-safe wrapper. Only ONE instance may open a storage folder (Qdrant locks it)."""

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._client = QdrantClient(path=str(data_dir / "qdrant"))
        self._meta_file = data_dir / "index_meta.json"
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._client.close()

    # ---- collections ----
    def exists(self, name: str) -> bool:
        with self._lock:
            return self._client.collection_exists(name)

    def ensure(self, name: str, dimensions: int) -> None:
        with self._lock:
            if not self._client.collection_exists(name):
                self._client.create_collection(
                    name, vectors_config=models.VectorParams(size=dimensions, distance=models.Distance.COSINE))

    def drop(self, name: str) -> None:
        with self._lock:
            if self._client.collection_exists(name):
                self._client.delete_collection(name)
            meta = self._read_meta()
            if meta.pop(name, None) is not None:
                self._write_meta(meta)

    def count(self, name: str) -> int:
        with self._lock:
            return self._client.count(name).count if self._client.collection_exists(name) else 0

    # ---- points ----
    def state(self, name: str) -> dict[str, str]:
        """chunk_id -> embed_hash for everything stored: lets us embed only what changed."""
        out: dict[str, str] = {}
        with self._lock:
            offset = None
            while True:
                records, offset = self._client.scroll(
                    name, limit=1000, offset=offset, with_payload=["chunk_id", "embed_hash"], with_vectors=False)
                out.update({r.payload["chunk_id"]: r.payload["embed_hash"] for r in records if r.payload})
                if offset is None:
                    return out

    def upsert(self, name: str, chunks: list[Chunk], hashes: list[str], vectors: list[list[float]]) -> None:
        points = [
            models.PointStruct(
                id=point_id(c.id), vector=vector,
                payload={
                    "chunk_id": c.id, "embed_hash": h, "path": c.path, "language": c.language,
                    "category": c.category, "kind": c.kind, "name": c.name,
                    "start_line": c.start_line, "end_line": c.end_line, "text": c.text,
                    "signature": c.signature, "doc": c.doc, "part": c.part, "parts": c.parts,
                    "spans": [list(s) for s in c.spans],
                },
            )
            for c, h, vector in zip(chunks, hashes, vectors)
        ]
        with self._lock:
            self._client.upsert(name, points=points)

    def delete(self, name: str, chunk_ids: list[str]) -> None:
        if chunk_ids:
            with self._lock:
                self._client.delete(name, points_selector=models.PointIdsList(points=[point_id(i) for i in chunk_ids]))

    def search(self, name: str, vector: list[float], limit: int, categories: list[str] | None = None,
               kinds: list[str] | None = None) -> list[tuple[float, dict[str, Any]]]:
        must = []
        if categories:
            must.append(models.FieldCondition(key="category", match=models.MatchAny(any=categories)))
        if kinds:
            must.append(models.FieldCondition(key="kind", match=models.MatchAny(any=kinds)))
        with self._lock:
            result = self._client.query_points(
                name, query=vector, limit=limit, with_payload=True,
                query_filter=models.Filter(must=must) if must else None)
        return [(p.score, p.payload or {}) for p in result.points]

    # ---- small metadata file (when / with which model a repo was indexed) ----
    def _read_meta(self) -> dict[str, Any]:
        try:
            return json.loads(self._meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_meta(self, meta: dict[str, Any]) -> None:
        self._meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def get_meta(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read_meta().get(name)

    def set_meta(self, name: str, info: dict[str, Any]) -> None:
        with self._lock:
            meta = self._read_meta()
            meta[name] = info
            self._write_meta(meta)


_store: VectorStore | None = None
_store_lock = threading.Lock()


def get_store() -> VectorStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = VectorStore(get_settings().data_dir)
        return _store


def close_store() -> None:
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
            _store = None