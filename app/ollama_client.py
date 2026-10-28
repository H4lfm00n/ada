"""Thin async client for the local Ollama HTTP API, plus the ChatModel adapter.

Talks to /api/version, /api/tags, /api/show, /api/chat (streaming NDJSON), and
/api/embed. Tests inject an httpx.MockTransport, so nothing here needs a live server.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings
from app.interfaces import Message


class OllamaError(RuntimeError):
    """Ollama returned an error, or could not be reached."""


@dataclass(slots=True)
class ChatStats:
    """Server-side timings from Ollama's final stream message (durations in ns)."""

    total_duration: int = 0
    load_duration: int = 0
    prompt_eval_count: int = 0
    prompt_eval_duration: int = 0
    eval_count: int = 0
    eval_duration: int = 0
    done_reason: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ChatStats:
        return cls(
            total_duration=int(payload.get("total_duration") or 0),
            load_duration=int(payload.get("load_duration") or 0),
            prompt_eval_count=int(payload.get("prompt_eval_count") or 0),
            prompt_eval_duration=int(payload.get("prompt_eval_duration") or 0),
            eval_count=int(payload.get("eval_count") or 0),
            eval_duration=int(payload.get("eval_duration") or 0),
            done_reason=payload.get("done_reason"),
        )

    @property
    def decode_tok_s(self) -> float:
        return self.eval_count / (self.eval_duration / 1e9) if self.eval_duration else 0.0

    @property
    def prefill_tok_s(self) -> float:
        if not self.prompt_eval_duration:
            return 0.0
        return self.prompt_eval_count / (self.prompt_eval_duration / 1e9)


@dataclass(slots=True)
class ChatDelta:
    content: str = ""
    thinking: str = ""
    done: bool = False
    stats: ChatStats | None = None


@dataclass(slots=True)
class ModelInfo:
    name: str
    capabilities: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def normalize_tag(tag: str) -> str:
    """Ollama lists untagged models as 'name:latest'."""
    return tag if ":" in tag.rsplit("/", 1)[-1] else f"{tag}:latest"


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            transport=transport,
        )

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> OllamaClient:
        return cls(settings.ollama_url, settings.request_timeout_s, transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> OllamaClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- simple JSON endpoints ---------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise OllamaError(f"cannot reach Ollama at {self._http.base_url}: {exc}") from exc
        if resp.status_code >= 400:
            raise OllamaError(f"{method} {path} -> HTTP {resp.status_code}: {_error_text(resp)}")
        return resp.json()

    async def version(self) -> str:
        return str((await self._request("GET", "/api/version")).get("version", "unknown"))

    async def list_models(self) -> list[str]:
        data = await self._request("GET", "/api/tags")
        return [m["name"] for m in data.get("models", []) if "name" in m]

    async def show(self, model: str) -> ModelInfo:
        data = await self._request("POST", "/api/show", json={"model": model})
        return ModelInfo(
            name=model,
            capabilities=list(data.get("capabilities") or []),
            details=dict(data.get("details") or {}),
        )

    async def embed(
        self, model: str, inputs: list[str], keep_alive: str | None = None
    ) -> list[list[float]]:
        body: dict[str, Any] = {"model": model, "input": inputs}
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        data = await self._request("POST", "/api/embed", json=body)
        return data.get("embeddings", [])

    # -- streaming chat ----------------------------------------------------------

    async def chat_stream(
        self,
        model: str,
        messages: list[Message],
        *,
        think: bool = False,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
    ) -> AsyncIterator[ChatDelta]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
            "think": think,
        }
        if options:
            body["options"] = options
        if keep_alive is not None:
            body["keep_alive"] = keep_alive

        try:
            async with self._http.stream("POST", "/api/chat", json=body) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise OllamaError(
                        f"POST /api/chat ({model}) -> HTTP {resp.status_code}: {_error_text(resp)}"
                    )
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if "error" in payload:
                        raise OllamaError(f"/api/chat ({model}): {payload['error']}")
                    msg = payload.get("message") or {}
                    done = bool(payload.get("done"))
                    yield ChatDelta(
                        content=msg.get("content") or "",
                        thinking=msg.get("thinking") or "",
                        done=done,
                        stats=ChatStats.from_payload(payload) if done else None,
                    )
        except httpx.HTTPError as exc:
            raise OllamaError(f"cannot reach Ollama at {self._http.base_url}: {exc}") from exc


def _error_text(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("error", resp.text))
    except (ValueError, AttributeError):
        return resp.text


class OllamaChatModel:
    """ChatModel backed by Ollama. Yields reply text only; thinking text is dropped."""

    def __init__(self, client: OllamaClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self.last_stats: ChatStats | None = None
        self.last_thinking_chars = 0

    def _options(self) -> dict[str, Any]:
        return {
            "temperature": self._settings.temperature,
            "num_ctx": self._settings.num_ctx,
            "num_predict": self._settings.max_reply_tokens,
        }

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        self.last_stats = None
        self.last_thinking_chars = 0
        async for delta in self._client.chat_stream(
            self._settings.chat_model,
            messages,
            think=self._settings.think,
            options=self._options(),
            keep_alive=self._settings.keep_alive,
        ):
            self.last_thinking_chars += len(delta.thinking)
            if delta.content:
                yield delta.content
            if delta.done:
                self.last_stats = delta.stats
