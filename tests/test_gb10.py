"""Live checks against the real Ollama on the GB10. Run with: make test-gb10"""

from __future__ import annotations

import pytest

from app.config import load_settings
from app.interfaces import Message
from app.ollama_client import OllamaChatModel, OllamaClient, normalize_tag

pytestmark = pytest.mark.gb10


async def test_configured_models_installed() -> None:
    s = load_settings()
    async with OllamaClient.from_settings(s) as client:
        installed = {normalize_tag(t) for t in await client.list_models()}
    missing = [t for t in s.model_tags.values() if normalize_tag(t) not in installed]
    assert not missing, f"pull these: {missing}"


async def test_chat_streams_without_thinking() -> None:
    s = load_settings()
    async with OllamaClient.from_settings(s) as client:
        model = OllamaChatModel(client, s)
        parts = [p async for p in model.stream([Message("user", "Say hi in five words.")])]
    reply = "".join(parts)
    assert reply.strip()
    assert len(parts) > 1, "expected a streamed reply in several chunks"
    assert model.last_thinking_chars == 0 and "<think>" not in reply
    assert model.last_stats is not None and model.last_stats.eval_count > 0
