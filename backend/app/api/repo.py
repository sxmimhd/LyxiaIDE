from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.repo.scanner import ScanResult, scan_repository

router = APIRouter(prefix="/repo", tags=["repository"])


class ScanRequest(BaseModel):
    path: str
    extra_ignore: list[str] = []  # extra directory names to skip, e.g. ["generated"]


@router.post("/scan", response_model=ScanResult)
def scan(req: ScanRequest) -> ScanResult:
    """Scan a project folder. A plain `def` route: FastAPI runs it in a worker
    thread, so the blocking disk walk never freezes the event loop."""
    root = Path(req.path).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path}")
    return scan_repository(root.resolve(), req.extra_ignore)