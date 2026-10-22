"""Tool definitions: what the AI may call, with permission levels and argument validation."""
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Level(str, Enum):
    READ = "READ"  # looks at things: allowed automatically
    WRITE = "WRITE"  # changes files: asks
    EXECUTE = "EXECUTE"  # runs programs: asks
    DESTRUCTIVE = "DESTRUCTIVE"  # can lose data: asks every single time, never "always allow"


class ToolError(Exception):
    """A failure to report back to the AI as text (bad arguments, forbidden path, ...)."""


class ToolArgError(ToolError):
    pass


@dataclass
class ToolContext:
    root: Path  # the project folder; tools never leave it


@dataclass
class ToolOutput:
    text: str
    ok: bool = True


@dataclass
class Classification:
    """A tool can look at its arguments and raise its own permission level, or forbid the call."""
    level: Level
    reason: str = ""
    deny: bool = False


@dataclass
class Tool:
    name: str
    description: str
    level: Level
    parameters: dict[str, Any]  # JSON-schema subset: properties, required
    run: Callable[..., Any]  # (ctx, args) -> str | ToolOutput, sync or async
    classify: Callable[[dict[str, Any]], Classification] | None = None
    example: dict[str, Any] = field(default_factory=dict)


def validate_args(schema: dict[str, Any], args: Any) -> dict[str, Any]:
    """Checks types/required/unknown keys and fills defaults. Lenient about "10" for integers,
    because small models often quote numbers."""
    if not isinstance(args, dict):
        raise ToolArgError("Arguments must be a JSON object")
    props: dict[str, Any] = schema.get("properties", {})
    unknown = [k for k in args if k not in props]
    if unknown:
        raise ToolArgError(f"Unknown argument(s): {', '.join(unknown)}. Allowed: {', '.join(props) or 'none'}")
    out: dict[str, Any] = {}
    for name, spec in props.items():
        if args.get(name) is not None:
            value = args[name]
        elif "default" in spec:
            value = spec["default"]
        elif name in schema.get("required", []):
            raise ToolArgError(f"Missing required argument: {name}")
        else:
            continue
        kind = spec.get("type")
        if kind == "integer":
            if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
                value = int(value)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ToolArgError(f"'{name}' must be an integer")
            if "minimum" in spec and value < spec["minimum"] or "maximum" in spec and value > spec["maximum"]:
                raise ToolArgError(f"'{name}' must be between {spec.get('minimum')} and {spec.get('maximum')}")
        elif kind == "boolean":
            if isinstance(value, str) and value.lower() in ("true", "false"):
                value = value.lower() == "true"
            if not isinstance(value, bool):
                raise ToolArgError(f"'{name}' must be true or false")
        elif kind == "string":
            if not isinstance(value, str):
                raise ToolArgError(f"'{name}' must be a string")
            if len(value) > spec.get("maxLength", 10_000):
                raise ToolArgError(f"'{name}' is too long")
        out[name] = value
    return out