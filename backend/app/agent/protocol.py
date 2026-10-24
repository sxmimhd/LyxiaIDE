"""The plain-text action format the agent speaks. Small local models are unreliable at native
function calling, so the format is three labelled lines that are parsed strictly and forgivingly."""
import json
import re
from dataclasses import dataclass, field
from typing import Any


class ProtocolError(Exception):
    """The model's reply could not be understood; the message is sent back so it can correct itself."""


@dataclass
class AgentAction:
    thought: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


_ACTION = re.compile(r"^[ \t>*#-]*ACTION[ \t]*:[ \t]*`?([A-Za-z_][A-Za-z0-9_]*)`?[ \t]*$", re.MULTILINE | re.IGNORECASE)
_THOUGHT = re.compile(r"^[ \t>*#-]*THOUGHT[ \t]*:[ \t]*(.*?)(?=^[ \t>*#-]*(?:ACTION|ARGS)[ \t]*:|\Z)", re.MULTILINE | re.IGNORECASE | re.DOTALL)
_ARGS = re.compile(r"^[ \t>*#-]*ARGS[ \t]*:[ \t]*", re.MULTILINE | re.IGNORECASE)


def parse_reply(text: str) -> AgentAction:
    """Reads THOUGHT / ACTION / ARGS. Only the first action counts. ARGS may span lines or sit in a code fence."""
    match = _ACTION.search(text)
    if not match:
        raise ProtocolError("I could not find an 'ACTION: <tool name>' line. Reply with THOUGHT:, ACTION: and ARGS: lines.")
    thought_match = _THOUGHT.search(text)
    thought = thought_match.group(1).strip() if thought_match else ""

    args: dict[str, Any] = {}
    rest = text[match.end():]
    args_match = _ARGS.search(rest)
    if args_match:
        body = rest[args_match.end():]
        start = body.find("{")
        if start == -1:
            if body.strip().strip("`") not in ("", "none", "None", "null"):
                raise ProtocolError("ARGS must be a JSON object such as {\"path\": \"src\"}.")
        else:
            try:
                parsed, _ = json.JSONDecoder().raw_decode(body[start:])
            except ValueError as e:
                raise ProtocolError(f"ARGS is not valid JSON ({e}). Use double quotes and put it on one line.") from None
            if not isinstance(parsed, dict):
                raise ProtocolError("ARGS must be a JSON object.")
            args = parsed
    return AgentAction(thought=thought, name=match.group(1), args=args)