"""READ tools that use the index and the repository graph."""
from app.graph.builder import build_graph
from app.indexing.keyword import get_keyword_index
from app.indexing.retrieval import retrieve
from app.indexing.service import NotIndexedError
from app.indexing.store import get_store
from app.llm.embeddings import get_embedder
from app.tools.base import ToolContext, ToolError


async def semantic_search(ctx: ToolContext, args: dict) -> str:
    try:
        result = await retrieve(get_store(), get_keyword_index(), get_embedder(), ctx.root, args["query"], args["top_k"])
    except NotIndexedError:
        raise ToolError("This project is not indexed yet. Ask the user to click 'Index now' in the chat panel.") from None
    if not result.hits:
        return "No results."
    lines = []
    for i, h in enumerate(result.hits, 1):
        p = h.payload
        preview = " ".join(ln.strip() for ln in p["text"].split("\n") if ln.strip())[:160]
        lines.append(f"{i}. {p['path']}:{p['start_line']}-{p['end_line']}  {p['kind']} {p['name']}\n   {preview}")
    return "\n".join(lines + [f"note: {w}" for w in result.warnings])


def find_symbol(ctx: ToolContext, args: dict) -> str:
    name = args["name"].strip()
    graph = build_graph(ctx.root)
    exact, partial = [], []
    for path, parsed in graph.files.items():
        for u in parsed.units:
            line = f"{path}:{u.start_line}-{u.end_line}  {u.kind} {u.qualified_name}  — {u.signature[:100]}"
            if name in (u.name, u.qualified_name):
                exact.append(line)
            elif name.lower() in u.qualified_name.lower():
                partial.append(line)
    found = (exact + partial)[:30]
    if not found:
        return f"No symbol named '{name}'."
    return f"{len(found)} result(s):\n" + "\n".join(found)