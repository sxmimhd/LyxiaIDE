"""The agent loop: think -> act -> observe, with hard limits and a human in control of everything risky."""
import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.agent.prompt import system_prompt
from app.agent.protocol import ProtocolError, parse_reply
from app.editing.project import OpenFile, ProjectEditRequest, run_project_edit
from app.llm.client import LLMError, get_llm
from app.tools.permissions import ApprovalBroker, AuditLog, PolicyStore
from app.tools.runner import run_tool
from app.tools.safety import clip

OBSERVATION_CHARS = 4000  # what the model sees of one result
KEEP_FULL = 6  # the last N messages stay intact; older observations are shortened
PROPOSAL_TIMEOUT = 900.0


class AgentRequest(BaseModel):
    path: str  # project root
    task: str
    active_file: str | None = None
    open_files: list[OpenFile] = []
    max_steps: int = Field(12, ge=1, le=25)


def _compact(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Older observations are cut short so a long run still fits a small model's context window."""
    out = []
    cutoff = len(messages) - KEEP_FULL
    for i, m in enumerate(messages):
        if i >= 2 and i < cutoff and m["role"] == "user" and m["content"].startswith("OBSERVATION"):
            out.append({**m, "content": m["content"][:300] + (" …[older result shortened]" if len(m["content"]) > 300 else "")})
        else:
            out.append(m)
    return out


def _done(status: str, summary: str, steps: int) -> dict[str, Any]:
    return {"type": "done", "status": status, "summary": summary, "steps": steps}


async def _edit_files(req: AgentRequest, root: Path, args: dict, broker: ApprovalBroker,
                      result: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """The edit pipeline from Step 20; the user reviews the proposal before anything is applied."""
    instruction = str(args.get("instruction", "")).strip()
    if not instruction:
        result.update(ok=False, text="edit_files needs an 'instruction' describing the change.")
        return
    changes: list[dict[str, Any]] = []
    notes: list[str] = []
    edit_request = ProjectEditRequest(path=str(root), instruction=instruction,
                                      active_file=req.active_file, open_files=req.open_files)
    async with aclosing(run_project_edit(edit_request)) as edits:
        async for ev in edits:
            if ev["type"] == "file":
                changes.append(ev["change"])
            elif ev["type"] == "plan":
                notes = ev["notes"]
            elif ev["type"] == "status":
                yield {"type": "status", "message": ev["message"]}
            elif ev["type"] == "error":
                result.update(ok=False, text=f"The edit step failed: {ev['message']}")
                return
    usable = [c for c in changes if c["status"] in ("modified", "created")]
    if not usable:
        result.update(ok=False, text="The edit step produced no usable changes. " + " ".join(notes)[:500])
        return

    proposal_id = broker.create({"kind": "proposal", "instruction": instruction})
    raw = "deny"
    try:
        yield {"type": "proposal", "id": proposal_id, "instruction": instruction, "changes": changes}
        raw = await broker.wait(proposal_id, timeout=PROPOSAL_TIMEOUT)
    finally:
        broker.discard(proposal_id)
    try:
        answer = json.loads(raw)
    except ValueError:
        result.update(ok=False, text="The user did not respond to the proposed edits.")
        return
    if answer.get("rejected") or not answer.get("applied"):
        errors = "; ".join(answer.get("errors", []))
        result.update(ok=False, text="The user rejected the proposed edits." + (f" Problems: {errors}" if errors else "")
                      + " Try a smaller or different change, or finish and explain.")
        return
    # The IDE reports the text it applied and saved. Later edits in this run must start from it, not from the
    # snapshot taken when the run began.
    for updated in answer.get("updated_files", []):
        target = Path(updated["path"]).resolve()
        known = [i for i, f in enumerate(req.open_files) if Path(f.path).resolve() == target]
        if known:
            req.open_files[known[0]] = OpenFile(path=updated["path"], content=updated["content"])
        else:
            req.open_files.append(OpenFile(path=updated["path"], content=updated["content"]))
    files = ", ".join(answer["applied"])
    errors = answer.get("errors", [])
    result.update(ok=True, applied=True,
                  text=f"The user applied your edits to: {files}. They are saved to disk."
                       + (f" Some files had problems: {'; '.join(errors)}." if errors else "")
                       + " Now verify with a test/build command if the project has one.")


async def run_agent(req: AgentRequest, broker: ApprovalBroker, policies: PolicyStore,
                    audit: AuditLog) -> AsyncIterator[dict[str, Any]]:
    root = Path(req.path).expanduser().resolve()
    llm = get_llm()
    active = req.active_file or "none"
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt(req.max_steps)},
        {"role": "user", "content": f"Task: {req.task.strip()}\n\nFile open in the editor: {active}"},
    ]
    yield {"type": "start", "max_steps": req.max_steps}

    format_errors = 0
    recent: list[tuple[str, str]] = []
    unverified_edit = False  # files were changed and no command has run since
    nudged = False

    for step in range(1, req.max_steps + 1):
        try:
            reply = await llm.chat(_compact(messages), temperature=0.2)
        except LLMError as e:
            yield {"type": "error", "message": str(e)}
            return
        messages.append({"role": "assistant", "content": reply[:4000]})

        try:
            action = parse_reply(reply)
            format_errors = 0
        except ProtocolError as e:
            format_errors += 1
            yield {"type": "step", "n": step, "thought": "", "action": None, "args": {}, "error": str(e)}
            if format_errors >= 3:
                yield _done("error", "The model could not follow the required reply format.", step)
                return
            messages.append({"role": "user", "content": f"OBSERVATION (error): {e} Reply again in the required format."})
            continue

        yield {"type": "step", "n": step, "thought": action.thought, "action": action.name, "args": action.args}
        recent.append((action.name, json.dumps(action.args, sort_keys=True)))
        if len(recent) >= 3 and len(set(recent[-3:])) == 1:
            yield _done("stuck", f"Stopped: the agent repeated the same action ({action.name}) three times.", step)
            return

        if action.name == "finish":
            summary = str(action.args.get("summary") or action.thought or "Done.")
            if unverified_edit and not nudged:  # one reminder to verify, then trust the agent
                nudged = True
                messages.append({"role": "user", "content": "OBSERVATION (note): You changed files but have not run any "
                                 "check since. Run the project's tests/build if they exist, or call finish again to confirm."})
                yield {"type": "observation", "n": step, "ok": True, "output": "Reminder: verify the change before finishing."}
                continue
            yield _done("finished", summary, step)
            return

        result: dict[str, Any] = {"ok": False, "text": ""}
        # aclosing: if the run is stopped while a prompt is open, the inner generator is closed right away
        # (not whenever the garbage collector gets to it), so its pending approval is withdrawn immediately.
        if action.name == "edit_files":
            async with aclosing(_edit_files(req, root, action.args, broker, result)) as stream:
                async for ev in stream:
                    yield ev
            if result.get("applied"):
                unverified_edit = True
        else:
            async with aclosing(run_tool(root, action.name, action.args, broker, policies, audit)) as stream:
                async for ev in stream:
                    if ev["type"] == "approval_request":
                        yield ev
                    elif ev["type"] == "denied":
                        result.update(ok=False, text=("Blocked: " if ev["blocked"] else "The user declined this action: ") + ev["reason"])
                    elif ev["type"] == "result":
                        result.update(ok=ev["ok"], text=ev["output"])
            if action.name == "run_command":
                unverified_edit = False

        text, _ = clip(result["text"], OBSERVATION_CHARS, keep_tail=OBSERVATION_CHARS // 3)
        messages.append({"role": "user", "content": f"OBSERVATION ({'ok' if result['ok'] else 'error'}): {text}"})
        yield {"type": "observation", "n": step, "ok": result["ok"], "output": text[:1500]}

    yield _done("step_limit", f"Stopped after {req.max_steps} steps without finishing.", req.max_steps)