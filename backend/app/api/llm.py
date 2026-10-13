import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import get_settings
from app.indexing.context import format_context
from app.indexing.keyword import get_keyword_index
from app.indexing.retrieval import retrieve
from app.indexing.service import NotIndexedError
from app.indexing.store import get_store
from app.llm.client import LLMError, get_llm
from app.llm.embeddings import get_embedder

router = APIRouter(prefix="/llm", tags=["llm"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []  # earlier turns, oldest first
    workspace: str | None = None  # repository root: when set, relevant code is retrieved for the model


class ChatResponse(BaseModel):
    model: str
    reply: str


def build_messages(req: ChatRequest, context: str | None = None) -> list[dict[str, str]]:
    """system prompt + (trimmed) history + the new user message (with retrieved code, if any)."""
    settings = get_settings()
    history = req.history[-settings.max_history_messages :] if settings.max_history_messages > 0 else []
    user_content = req.message
    if context:
        user_content = (
            "Answer using the code below from the user's repository when it is relevant. "
            "Mention file paths. If the code does not contain the answer, say so.\n\n"
            f"{context}\n\nQuestion: {req.message}"
        )
    return [
        {"role": "system", "content": settings.system_prompt},
        *[m.model_dump() for m in history],
        {"role": "user", "content": user_content},
    ]


@router.get("/models")
async def list_models() -> dict[str, list[str]]:
    """Models available on the LLM server (useful to check connectivity)."""
    try:
        return {"models": await get_llm().list_models()}
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Non-streaming chat (no repository context)."""
    llm = get_llm()
    try:
        reply = await llm.chat(build_messages(req))
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return ChatResponse(model=llm.model, reply=reply)


def _sse(payload: dict[str, Any] | str) -> str:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return f"data: {data}\n\n"


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Streams server-sent events: {"sources": [...]} first (if a workspace was given), then
    {"token": "..."} repeatedly, then [DONE]. Errors arrive as {"error": "..."}."""
    llm = get_llm()

    async def events() -> AsyncIterator[str]:
        context = None
        if req.workspace:
            try:
                result = await retrieve(get_store(), get_keyword_index(), get_embedder(),
                                        Path(req.workspace).expanduser().resolve(), req.message, 6)
                context = format_context(result.hits)
                yield _sse({"sources": [
                    {"path": h.payload["path"], "name": h.payload["name"],
                     "start_line": h.payload["start_line"], "end_line": h.payload["end_line"]}
                    for h in result.hits
                ]})
            except (NotIndexedError, LLMError) as e:
                yield _sse({"notice": f"Answering without repository context: {e}"})
        try:
            async for token in llm.chat_stream(build_messages(req, context)):
                yield _sse({"token": token})
        except LLMError as e:
            yield _sse({"error": str(e)})
        yield _sse("[DONE]")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )