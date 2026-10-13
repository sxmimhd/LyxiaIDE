"""Turns retrieved chunks into a prompt-ready context block within a size budget."""
from app.indexing.retrieval import RetrievedChunk

# ~2,000 tokens. Keep it small: Ollama's default context window is limited, and anything over it is silently cut.
MAX_CONTEXT_CHARS = 8000


def format_context(hits: list[RetrievedChunk], budget: int = MAX_CONTEXT_CHARS) -> str:
    blocks: list[str] = []
    used = 0
    for h in hits:
        p = h.payload
        language = (p.get("language") or "").lower()
        block = (f"### {p['path']} (lines {p['start_line']}-{p['end_line']}) - {p['kind']} {p['name']}\n"
                 f"```{language}\n{p['text']}\n```")
        if used + len(block) > budget:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)