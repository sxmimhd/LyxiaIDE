import json
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import get_settings
from app.llm.client import LLMError, get_llm

router = APIRouter(prefix="/llm", tags=["llm"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []  # earlier turns, oldest first


class ChatResponse(BaseModel):
    model: str
    reply: str


def build_messages(req: ChatRequest) -> list[dict[str, str]]:
    """system prompt + (trimmed) history + the new user message."""
    settings = get_settings()
    history = req.history[-settings.max_history_messages :] if settings.max_history_messages > 0 else []
    return [
        {"role": "system", "content": settings.system_prompt},
        *[m.model_dump() for m in history],
        {"role": "user", "content": req.message},
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
    """Non-streaming chat."""
    llm = get_llm()
    try:
        reply = await llm.chat(build_messages(req))
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return ChatResponse(model=llm.model, reply=reply)


def _sse(payload: dict[str, str] | str) -> str:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return f"data: {data}\n\n"


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Streams the reply as server-sent events:
    data: {"token": "..."}  (repeated), then data: [DONE]  (or data: {"error": "..."})."""
    llm = get_llm()
    messages = build_messages(req)

    async def events() -> AsyncIterator[str]:
        try:
            async for token in llm.chat_stream(messages):
                yield _sse({"token": token})
        except LLMError as e:
            yield _sse({"error": str(e)})
        yield _sse("[DONE]")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )