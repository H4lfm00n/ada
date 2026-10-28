from __future__ import annotations

import httpx

from app.config import Settings
from app.health import run_health
from app.ollama_client import OllamaClient

from .conftest import FINAL_STATS, FakeOllama


def _status(checks: list, name: str) -> str:
    return next(c.status for c in checks if c.name == name)


async def test_all_healthy(fake: FakeOllama, settings: Settings) -> None:
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        checks = await run_health(settings, client)
    assert not [c for c in checks if c.status == "FAIL"], [str(c) for c in checks]
    names = {c.name for c in checks}
    assert {"chat smoke (think=false)", "guard smoke", "embed smoke"} <= names
    caps = next(c for c in checks if c.name == "chat capabilities")
    assert "tools=yes" in caps.detail and "thinking=yes" in caps.detail


async def test_missing_model_fails_with_pull_hint(fake: FakeOllama, settings: Settings) -> None:
    del fake.models["guard-test:1b"]
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        checks = await run_health(settings, client)
    guard = next(c for c in checks if c.name == "guard model")
    assert guard.status == "FAIL"
    assert "ollama pull guard-test:1b" in guard.detail
    assert "guard smoke" not in {c.name for c in checks}


async def test_embed_model_without_embedding_capability_fails(
    fake: FakeOllama, settings: Settings
) -> None:
    fake.models["emb-test:latest"] = ["completion"]
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        checks = await run_health(settings, client, quick=True)
    assert _status(checks, "embed model") == "FAIL"


async def test_server_down(settings: Settings) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with OllamaClient.from_settings(settings, httpx.MockTransport(boom)) as client:
        checks = await run_health(settings, client)
    assert len(checks) == 1 and checks[0].status == "FAIL"


async def test_thinking_leak_fails_smoke(fake: FakeOllama, settings: Settings) -> None:
    fake.chat_reply = lambda body: [
        {"message": {"thinking": "hmm", "content": "ready"}, "done": False},
        FINAL_STATS,
    ]
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        checks = await run_health(settings, client)
    assert _status(checks, "chat smoke (think=false)") == "FAIL"


async def test_guard_flagging_benign_prompt_fails(fake: FakeOllama, settings: Settings) -> None:
    fake.chat_reply = lambda body: [
        {"message": {"content": "unsafe\nS2"}, "done": False},
        FINAL_STATS,
    ]
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        checks = await run_health(settings, client)
    assert _status(checks, "guard smoke") == "FAIL"


async def test_quick_skips_smoke(fake: FakeOllama, settings: Settings) -> None:
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        checks = await run_health(settings, client, quick=True)
    assert not any("smoke" in c.name for c in checks)
    assert not any(r["path"] == "/api/chat" for r in fake.requests)
