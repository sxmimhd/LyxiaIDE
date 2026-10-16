from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.editing.search_replace import apply_blocks, parse_blocks
from app.llm.client import LLMError, get_llm

router = APIRouter(prefix="/edit", tags=["editing"])

MAX_FILE_CHARS = 200_000  # refuse bigger files
WINDOW_CHARS = 12_000  # how much of the file the model sees (about 3,000 tokens)
SELECTION_CHARS = 5_000

SYSTEM_PROMPT = """You are a precise code editor inside an IDE. Edit the file by writing SEARCH/REPLACE blocks.

Format (exactly):
<<<<<<< SEARCH
exact lines from the file that must change
=======
the new lines
>>>>>>> REPLACE

Rules:
- The SEARCH lines must match the file EXACTLY, including indentation. Copy them; do not retype them.
- Keep SEARCH short, but include enough lines to be unique in the file.
- To insert code, SEARCH for a nearby existing line and repeat it in REPLACE together with the new code.
- To delete code, leave the REPLACE part empty.
- Change only what the instruction asks. Do not reformat or touch unrelated code.
- Never output the whole file. Output at most two sentences of explanation, then the blocks."""


class SelectionRange(BaseModel):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class EditRequest(BaseModel):
    path: str
    language: str = ""
    content: str  # the CURRENT text of the file in the editor, including unsaved changes
    instruction: str
    selection: SelectionRange | None = None
    cursor_line: int = 1


class EditOutcome(BaseModel):
    status: str  # applied | not_found | ambiguous | empty_search | no_edits
    message: str
    search_preview: str


class EditResponse(BaseModel):
    explanation: str
    new_content: str  # the file with every applicable edit applied (equals the input if none applied)
    applied: int
    failed: int
    outcomes: list[EditOutcome]
    raw_output: str  # what the model actually said (truncated), for debugging


def _window(content: str, center_line: int) -> tuple[str, int]:
    """The part of the file shown to the model: all of it if it fits, else lines around `center_line`."""
    if len(content) <= WINDOW_CHARS:
        return content, 1
    lines = content.split("\n")
    lo = hi = min(max(center_line, 1), len(lines)) - 1
    size = len(lines[lo])
    while True:
        grew = False
        if lo > 0 and size + len(lines[lo - 1]) + 1 <= WINDOW_CHARS:
            lo -= 1
            size += len(lines[lo]) + 1
            grew = True
        if hi < len(lines) - 1 and size + len(lines[hi + 1]) + 1 <= WINDOW_CHARS:
            hi += 1
            size += len(lines[hi]) + 1
            grew = True
        if not grew:
            break
    return "\n".join(lines[lo : hi + 1]), lo + 1


def _user_prompt(req: EditRequest) -> str:
    content = req.content.replace("\r\n", "\n")
    center = (req.selection.start_line + req.selection.end_line) // 2 if req.selection else req.cursor_line
    visible, first_line = _window(content, center)

    parts = [f"File: {req.path}" + (f" ({req.language})" if req.language else "")]
    if len(visible) < len(content):
        parts.append(f"This is an excerpt of the file, starting at line {first_line}.")
    if req.selection:
        lines = content.split("\n")
        selected = "\n".join(lines[req.selection.start_line - 1 : req.selection.end_line])
        parts.append(
            f"The user selected lines {req.selection.start_line}-{req.selection.end_line}. "
            f"Make the change within or directly around this code:\n```\n{selected[:SELECTION_CHARS]}\n```"
        )
    parts.append(f"Instruction: {req.instruction.strip()}")
    parts.append(f"File content:\n```{req.language}\n{visible}\n```")
    return "\n\n".join(parts)


@router.post("/propose", response_model=EditResponse)
async def propose(req: EditRequest) -> EditResponse:
    """Ask the model for edits to one file and apply them in memory. Nothing is written to disk."""
    if len(req.content) > MAX_FILE_CHARS:
        raise HTTPException(status_code=413, detail="This file is too large for AI editing")
    if not req.instruction.strip():
        raise HTTPException(status_code=400, detail="The instruction is empty")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": _user_prompt(req)}]
    try:
        raw = await get_llm().chat(messages, temperature=0.1)  # low temperature: edits must be exact
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    blocks, explanation, incomplete = parse_blocks(raw)
    if not blocks:
        note = ("The model's reply was cut off before the edit was complete."
                if incomplete else "The model did not produce any edit blocks.")
        return EditResponse(
            explanation=explanation or note, new_content=req.content, applied=0, failed=0,
            outcomes=[EditOutcome(status="no_edits", message=note, search_preview="")], raw_output=raw[:4000],
        )

    selection = (req.selection.start_line, req.selection.end_line) if req.selection else None
    new_content, results = apply_blocks(req.content, blocks, selection)
    outcomes = [EditOutcome(status=r.status, message=r.message, search_preview=b.search[:160])
                for b, r in zip(blocks, results)]
    applied = sum(1 for r in results if r.status == "applied")
    return EditResponse(
        explanation=explanation, new_content=new_content, applied=applied, failed=len(results) - applied,
        outcomes=outcomes, raw_output=raw[:4000],
    )