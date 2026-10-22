"""Runs one tool call: validate, check permissions, ask the user if needed, execute, log."""
import inspect
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.tools.base import Level, ToolArgError, ToolContext, ToolError, ToolOutput, validate_args
from app.tools.permissions import ApprovalBroker, AuditLog, PolicyStore, decide
from app.tools.registry import REGISTRY
from app.tools.safety import clip

MAX_OUTPUT = 30_000


def _summary(args: dict[str, Any]) -> dict[str, str]:
    return {k: str(v)[:1000 if k == "command" else 200] for k, v in args.items()}


async def run_tool(root: Path, tool_name: str, raw_args: Any, broker: ApprovalBroker, policies: PolicyStore,
                   audit: AuditLog) -> AsyncIterator[dict[str, Any]]:
    """Yields events: approval_request (only if the user must decide), then exactly one of result / denied."""
    started = time.perf_counter()
    base = {"root": str(root), "tool": tool_name}

    tool = REGISTRY.get(tool_name)
    if tool is None:
        audit.record(**base, outcome="unknown_tool")
        yield {"type": "result", "ok": False, "output": f"Unknown tool '{tool_name}'. Available: {', '.join(REGISTRY)}",
               "truncated": False, "duration_ms": 0}
        return
    try:
        args = validate_args(tool.parameters, raw_args)
    except ToolArgError as e:
        audit.record(**base, outcome="bad_arguments", error=str(e))
        yield {"type": "result", "ok": False, "output": str(e), "truncated": False, "duration_ms": 0}
        return

    decision = decide(tool, args, policies.get(root))
    log = {**base, "level": decision.level.value, "args": _summary(args)}
    if decision.action == "deny":
        audit.record(**log, outcome="blocked", reason=decision.reason)
        yield {"type": "denied", "reason": decision.reason, "blocked": True}
        return

    outcome = "auto_allowed"
    if decision.action == "ask":
        approval_id = broker.create({"tool": tool.name, "level": decision.level.value, "args": _summary(args),
                                     "reason": decision.reason, "root": str(root)})
        choice = "deny"
        try:
            yield {"type": "approval_request", "id": approval_id, "tool": tool.name, "level": decision.level.value,
                   "reason": decision.reason, "args": args, "can_always_allow": decision.level != Level.DESTRUCTIVE}
            choice = await broker.wait(approval_id)
        finally:
            broker.discard(approval_id)
            if choice == "deny":  # declined, expired, or the client went away while we were waiting
                audit.record(**log, outcome="declined")
        if choice == "deny":
            yield {"type": "denied", "reason": "The action was declined.", "blocked": False}
            return
        if choice == "allow_always" and decision.level != Level.DESTRUCTIVE:  # destructive: asked every time
            policies.allow(root, command=args["command"]) if tool.name == "run_command" else policies.allow(root, tool=tool.name)
        outcome = "approved_always" if choice == "allow_always" and decision.level != Level.DESTRUCTIVE else "approved_once"

    ctx = ToolContext(root)
    try:
        if inspect.iscoroutinefunction(tool.run):
            produced = await tool.run(ctx, args)
        else:
            produced = await run_in_threadpool(tool.run, ctx, args)
        output = produced if isinstance(produced, ToolOutput) else ToolOutput(str(produced))
    except ToolError as e:
        output = ToolOutput(str(e), ok=False)
    except Exception as e:  # a bug in a tool must not break the stream
        output = ToolOutput(f"Unexpected error: {e}", ok=False)

    text, truncated = clip(output.text, MAX_OUTPUT, keep_tail=5000)
    elapsed = int((time.perf_counter() - started) * 1000)
    audit.record(**log, outcome=outcome, ok=output.ok, duration_ms=elapsed)
    yield {"type": "result", "ok": output.ok, "output": text, "truncated": truncated, "duration_ms": elapsed}