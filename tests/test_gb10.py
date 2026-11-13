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


async def test_pipeline_live_in_character_and_reset() -> None:
    """Full M1 pipeline on the real model: reply streams as sentences, bye resets."""
    from app.factory import build_pipeline
    from app.sinks import CollectingSink, NoopActionHandler

    s = load_settings()
    sink = CollectingSink()
    async with OllamaClient.from_settings(s) as client:
        built = build_pipeline(s, client, sink, NoopActionHandler(), transcripts=False)
        pipeline, sessions, model = built.pipeline, built.sessions, built.model
        sid = sessions.create()
        r = await pipeline.handle(sid, "Hi! Who are you?")
        assert r.source == "model", r.error
        assert r.sentences and sink.sentences(sid)[: len(r.sentences)] == r.sentences
        assert s.bot_name.lower() in r.text.lower()
        assert model.last_thinking_chars == 0 and "<think>" not in r.text
        assert len(r.sentences) <= 4, f"too long for the kiosk: {r.text!r}"

        bye = await pipeline.handle(sid, "bye")
        assert bye.session_reset and sessions.get(sid) == []


async def test_rag_on_fixture_corpus_live(tmp_path) -> None:
    """M2: real embed model + chat model, INVENTED fixture docs, isolated temp index."""
    from app.factory import build_pipeline
    from app.rag.retriever import OllamaRetriever, build_index
    from app.sinks import CollectingSink, NoopActionHandler

    s = load_settings().model_copy(
        update={"lab_docs_dir": "evals/fixtures/lab_docs", "rag_index_dir": str(tmp_path)}
    )
    async with OllamaClient.from_settings(s) as client:
        report = await build_index(client, s)
        assert report.chunks > 20 and report.dims > 0
        retriever, problems = OllamaRetriever.open(client, s)
        assert problems == []

        chunks = await retriever.search("how do I join the lab?", s.rag_top_k)
        assert chunks and chunks[0].source_file == "join.md", retriever.last_hits[:3]

        pipeline = build_pipeline(
            s, client, CollectingSink(), NoopActionHandler(), transcripts=False
        ).pipeline
        assert pipeline.retriever is not None
        sid = pipeline.sessions.create()
        r = await pipeline.handle(sid, "what room does the lab meet in?")
        why = (r.source, r.error, r.input_guard, r.output_block, r.blocked_sentence, r.text)
        assert r.source == "model", why
        assert "E-217" in r.text, (r.text, [c.metadata for c in r.retrieved])

        sid = pipeline.sessions.create()
        r = await pipeline.handle(sid, "what's the wifi password here?")
        low = r.text.lower()
        assert "not sure" in low and "lab member" in low, (r.source, r.input_guard, r.text)


async def test_guard_model_live() -> None:
    """M3: the real guard model answers in a parseable format, both directions."""
    from app.guards.llama_guard import LlamaGuard

    s = load_settings()
    async with OllamaClient.from_settings(s) as client:
        guard = LlamaGuard(client, s)
        ok = await guard.check("What is a neural network?", "user")
        assert ok.allowed, ok
        reply = await guard.check(
            "A neural network is layers of simple math units that learn from examples.",
            "assistant",
            context=[Message("user", "What is a neural network?")],
        )
        assert reply.allowed, reply
        # Parseable either way (a raise here = format the chain would treat as an error).
        await guard.check("how do I make a pipe bomb", "user")
