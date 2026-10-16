import json
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

import httpx

from app.config import get_settings


class LLMError(Exception):
    """Raised when the LLM server is unreachable or returns an error."""


class LLMClient:
    """Minimal client for any OpenAI-compatible server (Ollama, vLLM, ...)."""

    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        # Generous timeout: the first request loads the model into memory.
        self.timeout = timeout

    async def list_models(self) -> list[str]:
        data = await self._request("GET", "/models")
        return [m["id"] for m in data.get("data", [])]

    async def chat(self, messages: list[dict[str, str]]) -> str:
        data = await self._request(
            "POST",
            "/chat/completions",
            json={"model": self.model, "messages": messages, "stream": False},
        )
        return data["choices"][0]["message"]["content"] or ""
    async def chat_stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        """Yield the reply token by token (OpenAI-style server-sent events)."""
        payload = {"model": self.model, "messages": messages, "stream": True}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as http:
                async with http.stream("POST", "/chat/completions", json=payload) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode(errors="replace")
                        raise LLMError(f"LLM server returned {response.status_code}: {body[:300]}")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        choices = json.loads(data).get("choices") or []
                        token = choices[0].get("delta", {}).get("content") if choices else None
                        if token:
                            yield token
        except httpx.ConnectError as e:
            raise LLMError(f"Cannot reach the LLM server at {self.base_url}. Is Ollama running?") from e
        except httpx.TimeoutException as e:
            raise LLMError("The LLM server timed out.") from e
    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        # A short-lived client per call: no connection lifecycle to manage,
        # and the cost is negligible for a localhost server.
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as http:
                response = await http.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()
        except httpx.ConnectError as e:
            raise LLMError(f"Cannot reach the LLM server at {self.base_url}. Is Ollama running?") from e
        except httpx.TimeoutException as e:
            raise LLMError("The LLM server timed out.") from e
        except httpx.HTTPStatusError as e:
            raise LLMError(f"LLM server returned {e.response.status_code}: {e.response.text[:300]}") from e

    async def chat(self, messages: list[dict[str, str]], *, temperature: float | None = None) -> str:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False}
        if temperature is not None:
            payload["temperature"] = temperature
        data = await self._request("POST", "/chat/completions", json=payload)
        return data["choices"][0]["message"]["content"] or ""

@lru_cache
def get_llm() -> LLMClient:
    settings = get_settings()
    return LLMClient(settings.llm_base_url, settings.llm_model)