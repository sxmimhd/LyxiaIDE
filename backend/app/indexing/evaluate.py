"""Retrieval evaluation: run labelled queries through each search mode and score the rankings."""
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.indexing.keyword import KeywordIndex
from app.indexing.retrieval import Mode, retrieve
from app.indexing.store import VectorStore
from app.llm.embeddings import EmbeddingClient

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / ".lyxia" / "eval" / "queries.json"


class Target(BaseModel):
    path: str  # relative to the repository root, posix style
    name: str | None = None  # optional symbol; omit to accept any chunk of the file


class EvalQuery(BaseModel):
    id: str
    query: str
    relevant: list[Target]


class ModeSummary(BaseModel):
    mode: str
    hit_at_1: float  # share of queries whose #1 result is relevant
    recall_at_5: float  # share of the relevant targets found in the top 5 (averaged over queries)
    mrr: float  # mean reciprocal rank of the first relevant result (1.0 = always first)
    misses: list[str]  # ids of queries with nothing relevant in the top_k


class QueryComparison(BaseModel):
    id: str
    query: str
    ranks: dict[str, int | None]  # mode -> rank of the first relevant result (None = not found)
    top1: dict[str, str]  # mode -> what that mode put first


def load_dataset(path: Path = DEFAULT_DATASET) -> list[EvalQuery]:
    return [EvalQuery(**q) for q in json.loads(path.read_text(encoding="utf-8"))]


def _matches(payload: dict[str, Any], target: Target) -> bool:
    if payload["path"] != target.path:
        return False
    if target.name is None:
        return True
    name = payload["name"]
    return name == target.name or name.endswith("." + target.name)


async def evaluate(store: VectorStore, keyword: KeywordIndex, embedder: EmbeddingClient, root: Path,
                   queries: list[EvalQuery], modes: list[Mode], top_k: int, categories: list[str] | None = None,
                   kinds: list[str] | None = None) -> tuple[list[ModeSummary], list[QueryComparison]]:
    totals = {m: {"hit1": 0.0, "recall5": 0.0, "rr": 0.0, "misses": []} for m in modes}
    comparisons: list[QueryComparison] = []

    for q in queries:
        # Embed each query once and share it between the semantic and hybrid runs.
        vector = await embedder.embed_query(q.query) if any(m in ("semantic", "hybrid") for m in modes) else None
        ranks: dict[str, int | None] = {}
        top1: dict[str, str] = {}
        for mode in modes:
            result = await retrieve(store, keyword, embedder, root, q.query, top_k, mode, categories, kinds, vector)
            first = next((i for i, h in enumerate(result.hits, 1)
                          if any(_matches(h.payload, t) for t in q.relevant)), None)
            found5 = sum(1 for t in q.relevant if any(_matches(h.payload, t) for h in result.hits[:5]))
            acc = totals[mode]
            acc["hit1"] += first == 1
            acc["recall5"] += found5 / len(q.relevant)
            acc["rr"] += 1 / first if first else 0.0
            if first is None:
                acc["misses"].append(q.id)
            ranks[mode] = first
            top1[mode] = f"{result.hits[0].payload['path']}::{result.hits[0].payload['name']}" if result.hits else "-"
        comparisons.append(QueryComparison(id=q.id, query=q.query, ranks=ranks, top1=top1))

    n = max(len(queries), 1)
    summaries = [ModeSummary(mode=m, hit_at_1=round(t["hit1"] / n, 3), recall_at_5=round(t["recall5"] / n, 3),
                             mrr=round(t["rr"] / n, 3), misses=t["misses"]) for m, t in totals.items()]
    return summaries, comparisons