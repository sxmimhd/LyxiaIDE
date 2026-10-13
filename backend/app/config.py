from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration. Override any value with an env var or a .env file,
    e.g. LYXIA_PORT=9000 or LYXIA_LLM_MODEL=qwen2.5-coder:14b"""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="LYXIA_", extra="ignore")

    app_name: str = "LyxiaIDE AI Engine"
    version: str = "0.1.0"

    host: str = "127.0.0.1"
    port: int = 8765

    # Origins allowed to call the API (the Vite dev server for now).
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Local LLM through an OpenAI-compatible server (Ollama default).
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = "qwen2.5-coder:7b"
    # Embeddings (also served through Ollama's OpenAI-compatible API).
    embed_base_url: str = "http://127.0.0.1:11434/v1"
    embed_model: str = "qwen3-embedding:0.6b"
    embed_batch_size: int = 16
    # Some models want different prefixes for queries and documents. Qwen3-Embedding takes an
    # instruction on the query only; nomic-embed-text wants "search_query: " / "search_document: ".
    embed_query_prefix: str = (
        "Instruct: Given a question about a codebase, retrieve the code and documentation that answer it\nQuery:"
    )
    embed_document_prefix: str = ""
    system_prompt: str = (
        "You are Lyxia, an AI coding assistant built into the LyxiaIDE desktop IDE. "
        "Be concise and precise. Put code in Markdown fenced blocks with a language tag. "
        "If you are unsure, say so instead of guessing."
    )
    # Only the most recent N history messages are sent (a crude context budget for now).
    max_history_messages: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()