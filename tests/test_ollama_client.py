from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.interfaces import ChatModel, Message
from app.ollama_client import OllamaChatModel, OllamaClient, OllamaError, normalize_tag

from .conftest import FINAL_STATS, FakeOllama


async def test_chat_model_streams_text_and_sends_think_false(
    fake: FakeOllama, settings: Settings
) -> None:
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        model = OllamaChatModel(client, settings)
        assert isinstance(model, ChatModel)
        parts = [p async for p in model.stream([Message("user", "hi")])]

    assert parts == ["Hello", " there!"]
    body = fake.requests[-1]
    assert body["path"] == "/api/chat"
    assert body["think"] is False
    assert body["stream"] is True
    assert body["model"] == settings.chat_model
    assert body["options"]["num_predict"] == settings.max_reply_tokens
    assert model.last_stats is not None
    assert model.last_stats.decode_tok_s == pytest.approx(20.0)
    assert model.last_stats.prefill_tok_s == pytest.approx(500.0)


async def test_thinking_text_is_never_yielded(fake: FakeOllama, settings: Settings) -> None:
    fake.chat_reply = lambda body: [
        {"message": {"thinking": "secret plan", "content": ""}, "done": False},
        {"message": {"content": "Answer."}, "done": False},
        FINAL_STATS,
    ]
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        model = OllamaChatModel(client, settings)
        parts = [p async for p in model.stream([Message("user", "hi")])]
    assert parts == ["Answer."]
    assert model.last_thinking_chars == len("secret plan")


async def test_mid_stream_error_raises(fake: FakeOllama, settings: Settings) -> None:
    fake.chat_reply = lambda body: [
        {"message": {"content": "Hel"}, "done": False},
        {"error": "model runner crashed"},
    ]
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        with pytest.raises(OllamaError, match="runner crashed"):
            async for _ in client.chat_stream(settings.chat_model, [Message("user", "x")]):
                pass


async def test_http_error_status_raises(settings: Settings) -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(404, json={"error": "model 'nope' not found"})
    )
    async with OllamaClient.from_settings(settings, transport) as client:
        with pytest.raises(OllamaError, match="not found"):
            async for _ in client.chat_stream("nope", [Message("user", "x")]):
                pass


async def test_unreachable_server_raises(settings: Settings) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with OllamaClient.from_settings(settings, httpx.MockTransport(boom)) as client:
        with pytest.raises(OllamaError, match="cannot reach"):
            await client.version()
        with pytest.raises(OllamaError, match="cannot reach"):
            async for _ in client.chat_stream("m", [Message("user", "x")]):
                pass


async def test_list_show_embed(fake: FakeOllama, settings: Settings) -> None:
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        assert "qwen-test:27b" in await client.list_models()
        info = await client.show("qwen-test:27b")
        assert "tools" in info.capabilities
        vecs = await client.embed("emb-test", ["a", "b"])
    assert len(vecs) == 2 and len(vecs[0]) == 3


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("emb-test", "emb-test:latest"),
        ("qwen3.8:27b", "qwen3.8:27b"),
        ("user/model", "user/model:latest"),
        ("user/model:1b", "user/model:1b"),
    ],
)
def test_normalize_tag(tag: str, expected: str) -> None:
    assert normalize_tag(tag) == expected
