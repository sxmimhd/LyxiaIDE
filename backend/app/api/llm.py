from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.llm.client import LLMError, get_llm

router = APIRouter(prefix="/llm", tags=["llm"])


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    model: str
    reply: str


@router.get("/models")
async def list_models() -> dict[str, list[str]]:
    """Models available on the LLM server (useful to check connectivity)."""
    try:
        return {"models": await get_llm().list_models()}
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Single-turn, non-streaming chat. Streaming comes in the next step."""
    llm = get_llm()
    try:
        reply = await llm.chat([{"role": "user", "content": req.message}])
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return ChatResponse(model=llm.model, reply=reply)