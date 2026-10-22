import json
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.tools.permissions import ApprovalBroker, AuditLog, PolicyStore
from app.tools.registry import REGISTRY
from app.tools.runner import run_tool

router = APIRouter(prefix="/tools", tags=["tools"])
_broker = ApprovalBroker()


@lru_cache
def _policies() -> PolicyStore:
    return PolicyStore(get_settings().data_dir / "permissions.json")


@lru_cache
def _audit() -> AuditLog:
    return AuditLog(get_settings().data_dir / "tool_audit.jsonl")


def _root(path: str) -> Path:
    root = Path(path).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    return root.resolve()


class ToolInfo(BaseModel):
    name: str
    description: str
    level: str
    parameters: dict[str, Any]
    example: dict[str, Any]


class RunRequest(BaseModel):
    path: str  # project root
    tool: str
    arguments: dict[str, Any] = {}


class ApproveRequest(BaseModel):
    id: str
    decision: Literal["allow_once", "allow_always", "deny"]


class PathRequest(BaseModel):
    path: str


class RevokeRequest(BaseModel):
    path: str
    command: str | None = None
    tool: str | None = None


class AuditRequest(BaseModel):
    path: str
    limit: int = Field(30, ge=1, le=200)


@router.get("", response_model=list[ToolInfo])
def list_tools() -> list[ToolInfo]:
    """Every tool, its permission level and its arguments (this is also what the agent is shown)."""
    return [ToolInfo(name=t.name, description=t.description, level=t.level.value, parameters=t.parameters,
                     example=t.example) for t in REGISTRY.values()]


def _sse(payload: dict[str, Any] | str) -> str:
    return f"data: {payload if isinstance(payload, str) else json.dumps(payload)}\n\n"


@router.post("/run")
async def run(req: RunRequest) -> StreamingResponse:
    """Runs a tool as the AI agent would. Streams events: approval_request (the stream then waits for
    POST /tools/approve), then result or denied."""
    root = _root(req.path)

    async def events() -> AsyncIterator[str]:
        try:
            async for event in run_tool(root, req.tool, req.arguments, _broker, _policies(), _audit()):
                yield _sse(event)
        except Exception as e:
            yield _sse({"type": "result", "ok": False, "output": f"Unexpected error: {e}", "truncated": False, "duration_ms": 0})
        yield _sse("[DONE]")

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/approve")
async def approve(req: ApproveRequest) -> dict[str, bool]:
    if not _broker.resolve(req.id, req.decision):
        raise HTTPException(status_code=404, detail="That request is no longer waiting for an answer")
    return {"ok": True}


@router.get("/pending")
def pending() -> list[dict[str, Any]]:
    return _broker.pending()


@router.post("/permissions")
def permissions(req: PathRequest) -> dict[str, list[str]]:
    return _policies().get(_root(req.path))


@router.post("/permissions/revoke")
def revoke(req: RevokeRequest) -> dict[str, list[str]]:
    root = _root(req.path)
    _policies().revoke(root, command=req.command, tool=req.tool)
    return _policies().get(root)


@router.post("/audit")
def audit(req: AuditRequest) -> list[dict[str, Any]]:
    return _audit().recent(_root(req.path), req.limit)