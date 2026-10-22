"""READ tools for Git. Fixed argument lists, no shell, and secrets are excluded from every diff."""
import re
import subprocess
from pathlib import Path

from app.repo.languages import is_sensitive
from app.tools.base import ToolContext, ToolError
from app.tools.safety import clip, filtered_env, resolve_inside

LIMIT = 20_000
_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/~^@{}-]{0,99}$")  # must not start with "-": no option injection

# Even a tracked .env must not leak through `git diff` or `git show`.
SECRET_EXCLUDES = [f":(exclude,glob)**/{p}" for p in (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "secrets.*")]

# A cloned repository's own config can name programs git would run (fsmonitor, external diff drivers).
SAFE_CONFIG = ["-c", "core.fsmonitor=false", "-c", "core.pager=cat", "-c", "diff.external="]


def _git(root: Path, args: list[str]) -> str:
    try:
        p = subprocess.run(["git", "-C", str(root), *SAFE_CONFIG, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20, env=filtered_env(), stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise ToolError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise ToolError("git took too long") from None
    if p.returncode != 0:
        raise ToolError((p.stderr or p.stdout).strip()[:500] or f"git exited with code {p.returncode}")
    return clip(p.stdout, LIMIT, keep_tail=2000)[0] or "(no output)"


def git_status(ctx: ToolContext, args: dict) -> str:
    return _git(ctx.root, ["status", "--short", "--branch"])


def git_diff(ctx: ToolContext, args: dict) -> str:
    spec = "."
    if args["path"]:
        real, spec = resolve_inside(ctx.root, args["path"])
        if is_sensitive(real.name):
            raise ToolError("Secrets and key files are never shared with the AI")
    flags = ["--staged"] if args["staged"] else []
    return _git(ctx.root, ["diff", "--no-ext-diff", "--no-textconv", *flags, "--", spec, *SECRET_EXCLUDES])


def git_log(ctx: ToolContext, args: dict) -> str:
    return _git(ctx.root, ["log", f"-{args['count']}", "--oneline", "--decorate"])


def git_show(ctx: ToolContext, args: dict) -> str:
    rev = args["revision"].strip()
    if not _REVISION.match(rev):
        raise ToolError("Invalid revision")
    return _git(ctx.root, ["show", "--stat", "--patch", "--no-ext-diff", "--no-textconv", rev, "--", ".", *SECRET_EXCLUDES])


def git_branch(ctx: ToolContext, args: dict) -> str:
    return _git(ctx.root, ["branch", "--all", "--verbose"])