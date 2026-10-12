from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.parsing.code_parser import FileTooLargeError, ParsedFile, UnsupportedLanguageError, parse_file
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


class ParseRequest(BaseModel):
    path: str  # absolute path of one source file


@router.post("/parse", response_model=ParsedFile)
def parse(req: ParseRequest) -> ParsedFile:
    """Parse one file into functions, classes, methods, imports and exports."""
    path = Path(req.path).expanduser()
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {req.path}")
    try:
        return parse_file(path.resolve())
    except UnsupportedLanguageError as e:
        raise HTTPException(status_code=415, detail=str(e)) from e
    except FileTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e