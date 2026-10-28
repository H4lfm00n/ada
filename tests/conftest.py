from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.config import Settings


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("RUN_GB10") == "1":
        return
    skip = pytest.mark.skip(reason="needs the GB10 + Ollama (set RUN_GB10=1)")
    for item in items:
        if "gb10" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("ADA_"):
            monkeypatch.delenv(key)


@pytest.fixture
def settings() -> Settings:
    return Settings(chat_model="qwen-test:27b", guard_model="guard-test:1b", embed_model="emb-test")


def ndjson(*objs: dict) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


FINAL_STATS = {
    "done": True,
    "done_reason": "stop",
    "total_duration": 2_000_000_000,
    "load_duration": 10_000_000,
    "prompt_eval_count": 50,
    "prompt_eval_duration": 100_000_000,
    "eval_count": 40,
    "eval_duration": 2_000_000_000,
    "message": {"role": "assistant", "content": ""},
}


class FakeOllama:
    """In-memory stand-in for the Ollama HTTP API, served via httpx.MockTransport."""

    def __init__(self) -> None:
        self.models: dict[str, list[str]] = {
            "qwen-test:27b": ["completion", "tools", "thinking"],
            "guard-test:1b": ["completion"],
            "emb-test:latest": ["embedding"],
        }
        self.chat_reply: Callable[[dict], list[dict]] = self._default_reply
        self.requests: list[dict] = []

    @staticmethod
    def _default_reply(body: dict) -> list[dict]:
        if body["model"].startswith("guard"):
            return [{"message": {"content": "safe"}, "done": False}, FINAL_STATS]
        return [
            {"message": {"role": "assistant", "content": "Hello"}, "done": False},
            {"message": {"role": "assistant", "content": " there!"}, "done": False},
            FINAL_STATS,
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.requests.append({"path": path, **body})
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.32.14"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in self.models]})
        if path == "/api/show":
            name = body["model"] if ":" in body["model"] else f"{body['model']}:latest"
            if name not in self.models:
                return httpx.Response(404, json={"error": f"model '{body['model']}' not found"})
            return httpx.Response(
                200,
                json={
                    "capabilities": self.models[name],
                    "details": {"parameter_size": "27B", "quantization_level": "Q4_K_M"},
                },
            )
        if path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]] * len(body["input"])})
        if path == "/api/chat":
            return httpx.Response(200, content=ndjson(*self.chat_reply(body)))
        return httpx.Response(404, json={"error": "not found"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def fake() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent
