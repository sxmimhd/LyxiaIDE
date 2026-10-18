import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.editing.project import ProjectEditRequest, run_project_edit
from app.editing.single import MAX_FILE_CHARS, EditRequest, EditResponse, propose_edit
from app.llm.client import LLMError

router = APIRouter(prefix="/edit", tags=["editing"])


@router.post("/propose", response_model=EditResponse)
async def propose(req: EditRequest) -> EditResponse:
    """Ask the model for edits to ONE file and apply them in memory. Nothing is written to disk."""
    if len(req.content) > MAX_FILE_CHARS:
        raise HTTPException(status_code=413, detail="This file is too large for AI editing")
    if not req.instruction.strip():
        raise HTTPException(status_code=400, detail="The instruction is empty")
    try:
        return await propose_edit(req)
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


def _sse(payload: dict[str, Any] | str) -> str:
    return f"data: {payload if isinstance(payload, str) else json.dumps(payload)}\n\n"


@router.post("/project")
async def project(req: ProjectEditRequest) -> StreamingResponse:
    """Edit across files. Streams events: status, plan, file (one per file, with the full before/after text),
    done. Nothing is written to disk: the IDE shows the changes for review and applies the accepted ones."""
    if not Path(req.path).expanduser().is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path}")
    if not req.instruction.strip():
        raise HTTPException(status_code=400, detail="The instruction is empty")

    async def events() -> AsyncIterator[str]:
        try:
            async for event in run_project_edit(req):
                yield _sse(event)
        except LLMError as e:
            yield _sse({"type": "error", "message": str(e)})
        except Exception as e:  # never leave the client waiting on a half-open stream
            yield _sse({"type": "error", "message": f"Unexpected error: {e}"})
        yield _sse("[DONE]")

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})