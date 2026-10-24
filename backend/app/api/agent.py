import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.loop import AgentRequest, run_agent
from app.editing.project import OpenFile
from app.tools.shared import broker, get_audit, get_policies

router = APIRouter(prefix="/agent", tags=["agent"])


def _sse(payload: dict[str, Any] | str) -> str:
    return f"data: {payload if isinstance(payload, str) else json.dumps(payload)}\n\n"


@router.post("/run")
async def run(req: AgentRequest) -> StreamingResponse:
    """Runs the agent on a task and streams its trace: start, step, observation, approval_request (answer via
    POST /tools/approve), proposal (answer via POST /agent/proposals/respond), then done. Closing the stream stops it."""
    if not Path(req.path).expanduser().is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path}")
    if not req.task.strip():
        raise HTTPException(status_code=400, detail="The task is empty")

    async def events() -> AsyncIterator[str]:
        try:
            async with aclosing(run_agent(req, broker, get_policies(), get_audit())) as stream:
                async for event in stream:
                    yield _sse(event)
        except Exception as e:
            yield _sse({"type": "error", "message": f"Unexpected error: {e}"})
        yield _sse("[DONE]")

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ProposalAnswer(BaseModel):
    id: str
    applied: list[str] = []  # paths the user applied (and the IDE saved)
    updated_files: list[OpenFile] = []  # their new text, so the agent's next edit starts from it
    errors: list[str] = []
    rejected: bool = False


@router.post("/proposals/respond")
async def respond(answer: ProposalAnswer) -> dict[str, bool]:
    if not broker.resolve(answer.id, json.dumps(answer.model_dump(exclude={"id"}))):
        raise HTTPException(status_code=404, detail="That proposal is no longer waiting for an answer")
    return {"ok": True}