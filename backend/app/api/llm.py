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
from app.indexing.retrieval import RetrievedChunk, retrieve
from app.indexing.service import NotIndexedError
from app.indexing.store import get_store
from app.llm.client import LLMError, get_llm
from app.llm.embeddings import get_embedder

router = APIRouter(prefix="/llm", tags=["llm"])

# Character budgets (about 4 characters per token). Kept small so the prompt fits the model's context window.
SELECTION_CHARS = 5000
EXCERPT_CHARS = 4000
REPO_CONTEXT_CHARS = 4000


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ActiveFile(BaseModel):
    path: str  # absolute path of the file open in the editor
    language: str = ""
    cursor_line: int = 1
    excerpt: str = ""  # the code around the cursor
    excerpt_start_line: int = 1


class Selection(BaseModel):
    text: str
    start_line: int
    end_line: int


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []  # earlier turns, oldest first
    workspace: str | None = None  # repository root: when set, relevant code is retrieved for the model
    active_file: ActiveFile | None = None
    selection: Selection | None = None


class ChatResponse(BaseModel):
    model: str
    reply: str


def _fence(language: str, text: str) -> str:
    return f"```{language}\n{text}\n```"


def build_user_prompt(req: ChatRequest, repo_context: str | None) -> str:
    """The user's question, preceded by everything the IDE knows about what they are looking at."""
    parts: list[str] = []
    if req.active_file:
        f = req.active_file
        parts.append(f"The user is editing `{f.path}` ({f.language}); the cursor is on line {f.cursor_line}.")
    if req.selection:
        s = req.selection
        language = req.active_file.language if req.active_file else ""
        parts.append(f"Selected code (lines {s.start_line}-{s.end_line}):\n{_fence(language, s.text[:SELECTION_CHARS])}")
    elif req.active_file and req.active_file.excerpt:
        f = req.active_file
        parts.append(f"Code around the cursor (starting at line {f.excerpt_start_line}):\n"
                     f"{_fence(f.language, f.excerpt[:EXCERPT_CHARS])}")
    if repo_context:
        parts.append(f"Related code from the repository:\n\n{repo_context}")
    if not parts:
        return req.message
    return ("\n\n".join(parts)
            + f"\n\nQuestion: {req.message}\n\n"
            "Answer using the code above when it is relevant. Mention file paths and line numbers. "
            "If the code does not contain the answer, say so.")


def build_messages(req: ChatRequest, repo_context: str | None = None) -> list[dict[str, str]]:
    """system prompt + (trimmed) history + the new user message with its context."""
    settings = get_settings()
    history = req.history[-settings.max_history_messages :] if settings.max_history_messages > 0 else []
    return [
        {"role": "system", "content": settings.system_prompt},
        *[m.model_dump() for m in history],
        {"role": "user", "content": build_user_prompt(req, repo_context)},
    ]


def _not_already_visible(hits: list[RetrievedChunk], root: Path, req: ChatRequest) -> list[RetrievedChunk]:
    """Drop retrieved chunks that overlap the code the user already sent (selection or excerpt)."""
    if not req.active_file:
        return hits
    try:
        rel = Path(req.active_file.path).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return hits
    if req.selection:
        lo, hi = req.selection.start_line, req.selection.end_line
    else:
        lo = req.active_file.excerpt_start_line
        hi = lo + req.active_file.excerpt.count("\n")
    return [h for h in hits
            if not (h.payload["path"] == rel and h.payload["start_line"] <= hi and h.payload["end_line"] >= lo)]


@router.get("/models")
async def list_models() -> dict[str, list[str]]:
    """Models available on the LLM server (useful to check connectivity)."""
    try:
        return {"models": await get_llm().list_models()}
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Non-streaming chat (no repository search)."""
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
    """Streams server-sent events: {"sources": [...]} and/or {"notice": "..."} first, then
    {"token": "..."} repeatedly, then [DONE]. Errors arrive as {"error": "..."}."""
    llm = get_llm()

    async def events() -> AsyncIterator[str]:
        repo_context = None
        if req.workspace:
            try:
                root = Path(req.workspace).expanduser().resolve()
                # "Explain this" says nothing by itself, so the selected code helps the search too.
                query = req.message + (f"\n{req.selection.text[:300]}" if req.selection else "")
                result = await retrieve(get_store(), get_keyword_index(), get_embedder(), root, query, 8)
                hits = _not_already_visible(result.hits, root, req)[:5]
                repo_context = format_context(hits, REPO_CONTEXT_CHARS)
                yield _sse({"sources": [
                    {"path": h.payload["path"], "name": h.payload["name"],
                     "start_line": h.payload["start_line"], "end_line": h.payload["end_line"]}
                    for h in hits
                ]})
                for warning in result.warnings:
                    yield _sse({"notice": warning})
            except Exception as e:  # any retrieval problem must not stop the answer
                yield _sse({"notice": f"Answering without repository search: {e}"})
        try:
            async for token in llm.chat_stream(build_messages(req, repo_context)):
                yield _sse({"token": token})
        except LLMError as e:
            yield _sse({"error": str(e)})
        yield _sse("[DONE]")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )