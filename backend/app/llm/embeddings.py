import math
from functools import lru_cache
from typing import Any

import httpx

from app.config import get_settings
from app.llm.client import LLMError


def _normalize(vector: list[float]) -> list[float]:
    """Scale to unit length so cosine similarity is just a dot product."""
    norm = math.sqrt(sum(x * x for x in vector))
    return vector if norm == 0 else [x / norm for x in vector]


class EmbeddingClient:
    """Embeddings from any OpenAI-compatible server (Ollama, vLLM, ...)."""

    def __init__(self, base_url: str, model: str, *, batch_size: int = 16, query_prefix: str = "",
                 document_prefix: str = "", timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.batch_size = max(batch_size, 1)
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix
        self.timeout = timeout  # generous: the first call loads the model

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, self.document_prefix)

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], self.query_prefix))[0]

    async def _embed(self, texts: list[str], prefix: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as http:
                for i in range(0, len(texts), self.batch_size):
                    batch = [prefix + t for t in texts[i : i + self.batch_size]]
                    response = await http.post("/embeddings", json={"model": self.model, "input": batch})
                    if response.status_code == 404:
                        raise LLMError(f"Embedding model '{self.model}' was not found. Pull it first:  ollama pull {self.model}")
                    response.raise_for_status()
                    vectors.extend(self._parse(response.json(), len(batch)))
        except httpx.ConnectError as e:
            raise LLMError(f"Cannot reach the embedding server at {self.base_url}. Is Ollama running?") from e
        except httpx.TimeoutException as e:
            raise LLMError("The embedding server timed out.") from e
        except httpx.HTTPStatusError as e:
            raise LLMError(f"Embedding server returned {e.response.status_code}: {e.response.text[:300]}") from e
        return vectors

    @staticmethod
    def _parse(payload: dict[str, Any], expected: int) -> list[list[float]]:
        data = payload.get("data", [])
        if len(data) != expected:
            raise LLMError(f"Embedding server returned {len(data)} vectors for {expected} inputs")
        if all("index" in d for d in data):
            data = sorted(data, key=lambda d: d["index"])  # never trust the response order
        return [_normalize(d["embedding"]) for d in data]


@lru_cache
def get_embedder() -> EmbeddingClient:
    s = get_settings()
    return EmbeddingClient(s.embed_base_url, s.embed_model, batch_size=s.embed_batch_size,
                           query_prefix=s.embed_query_prefix, document_prefix=s.embed_document_prefix)