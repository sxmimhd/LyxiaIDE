from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.indexing.evaluate import DEFAULT_DATASET, EvalQuery, ModeSummary, QueryComparison, evaluate, load_dataset
from app.indexing.keyword import get_keyword_index
from app.indexing.retrieval import Mode
from app.indexing.service import NotIndexedError
from app.indexing.store import get_store
from app.llm.client import LLMError
from app.llm.embeddings import get_embedder

router = APIRouter(prefix="/eval", tags=["evaluation"])


class EvalRequest(BaseModel):
    path: str  # repository root (must already be indexed)
    modes: list[Mode] = ["semantic", "keyword", "hybrid"]
    top_k: int = Field(10, ge=5, le=50)
    queries: list[EvalQuery] | None = None  # default: backend/.lyxia/eval/queries.json
    categories: list[str] | None = None
    kinds: list[str] | None = None


class EvalResponse(BaseModel):
    model: str
    query_count: int
    summary: list[ModeSummary]
    per_query: list[QueryComparison]


@router.post("/retrieval", response_model=EvalResponse)
async def eval_retrieval(req: EvalRequest) -> EvalResponse:
    """Score retrieval quality: hit@1, recall@5 and MRR for each search mode on a labelled query set."""
    root = Path(req.path).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path}")
    try:
        queries = req.queries if req.queries is not None else load_dataset()
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Cannot load {DEFAULT_DATASET}: {e}") from e
    embedder = get_embedder()
    try:
        summary, per_query = await evaluate(get_store(), get_keyword_index(), embedder, root.resolve(), queries,
                                            req.modes, req.top_k, req.categories, req.kinds)
    except NotIndexedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return EvalResponse(model=embedder.model, query_count=len(queries), summary=summary, per_query=per_query)