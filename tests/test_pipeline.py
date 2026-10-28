from __future__ import annotations

import asyncio
import itertools
import random
from collections.abc import AsyncIterator

import pytest

from app.config import Settings
from app.interfaces import ACTION_WAVE, Message
from app.lines import LineBook
from app.ollama_client import OllamaChatModel, OllamaClient, OllamaError
from app.pipeline import ChatPipeline, SessionGone, is_farewell
from app.prompt import PromptBuilder
from app.sessions import InMemorySessionManager
from app.sinks import CollectingSink, FanoutSink, NoopActionHandler

from .conftest import FINAL_STATS, FakeOllama


class ScriptedModel:
    """ChatModel stand-in: yields scripted deltas, records what it was sent."""

    def __init__(self, *replies: list[str] | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[list[Message]] = []
        self.fail_after: int | None = None

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        self.calls.append(messages)
        reply = self.replies.pop(0) if self.replies else ["Okay."]
        if isinstance(reply, Exception):
            raise reply
        for i, d in enumerate(reply):
            if self.fail_after is not None and i == self.fail_after:
                raise OllamaError("runner crashed")
            yield d


class Harness:
    def __init__(self, settings: Settings, model: object, timeout: float | None = None) -> None:
        self.sink = CollectingSink()
        self.actions = NoopActionHandler()
        self.sessions = InMemorySessionManager.from_settings(settings)
        self.lines = LineBook.from_settings(settings, rng=random.Random(1))
        self.pipeline = ChatPipeline(
            model=model,  # type: ignore[arg-type]
            prompt=PromptBuilder.from_settings(settings),
            lines=self.lines,
            sessions=self.sessions,
            sink=self.sink,
            actions=self.actions,
            reply_timeout_s=timeout,
        )


async def test_reply_streams_as_sentences_and_is_remembered(settings: Settings) -> None:
    model = ScriptedModel(["Hi", "! I'm a ro", "bot. Ask me", " anything."])
    h = Harness(settings, model)
    sid = h.sessions.create()

    r = await h.pipeline.handle(sid, "  hello  ")

    assert r.source == "model" and r.error is None
    assert r.sentences == ["Hi!", "I'm a robot.", "Ask me anything."]
    assert h.sink.sentences(sid) == r.sentences
    assert r.ttft_s is not None and r.first_sentence_s is not None
    assert r.ttft_s <= r.first_sentence_s <= r.total_s
    assert h.sessions.get(sid) == [
        Message("user", "hello"),
        Message("assistant", "Hi! I'm a robot. Ask me anything."),
    ]


async def test_prompt_has_system_first_then_history(settings: Settings) -> None:
    model = ScriptedModel(["First."], ["Second."])
    h = Harness(settings, model)
    sid = h.sessions.create()
    await h.pipeline.handle(sid, "one")
    await h.pipeline.handle(sid, "two")

    msgs = model.calls[1]
    assert msgs[0].role == "system"
    assert msgs[0].content.startswith(f"You are {settings.bot_name}")
    assert "{{" not in msgs[0].content
    assert [(m.role, m.content) for m in msgs[1:]] == [
        ("user", "one"),
        ("assistant", "First."),
        ("user", "two"),
    ]


async def test_wave_action_only_on_first_message_of_a_session(settings: Settings) -> None:
    h = Harness(settings, ScriptedModel())
    sid = h.sessions.create()
    await h.pipeline.handle(sid, "hi")
    await h.pipeline.handle(sid, "again")
    assert list(h.actions.actions) == [(sid, ACTION_WAVE)]
    await h.pipeline.goodbye(sid)
    await h.pipeline.handle(sid, "new visitor")
    assert list(h.actions.actions) == [(sid, ACTION_WAVE), (sid, ACTION_WAVE)]


@pytest.mark.parametrize("text", ["bye", "Bye!", "ok bye", "Goodbye.", "see ya", "BYE BYE"])
def test_farewell_detection(text: str) -> None:
    assert is_farewell(text)


@pytest.mark.parametrize("text", ["bye, but what's a GAN?", "byeee robot", "goodbye world in C"])
def test_not_farewell(text: str) -> None:
    assert not is_farewell(text)


async def test_bye_says_goodbye_resets_and_skips_model(settings: Settings) -> None:
    model = ScriptedModel(["Sure."])
    h = Harness(settings, model)
    sid = h.sessions.create()
    await h.pipeline.handle(sid, "my name is Sam")
    calls = len(model.calls)

    r = await h.pipeline.handle(sid, "bye!")

    assert len(model.calls) == calls  # model not called
    assert r.source == "line:goodbye" and r.session_reset
    assert r.text in h.lines.category("goodbye").lines
    assert h.sessions.get(sid) == []

    await h.pipeline.handle(sid, "what's my name?")
    assert "Sam" not in " ".join(m.content for m in model.calls[-1])


async def test_explicit_reset_clears_context(settings: Settings) -> None:
    model = ScriptedModel(["Noted."], ["Hello."])
    h = Harness(settings, model)
    sid = h.sessions.create()
    await h.pipeline.handle(sid, "remember the code word pineapple")
    r = await h.pipeline.goodbye(sid)
    assert r.session_reset and h.sink.sentences(sid)[-1]
    await h.pipeline.handle(sid, "what was the code word?")
    assert [m.role for m in model.calls[-1]] == ["system", "user"]
    assert "pineapple" not in " ".join(m.content for m in model.calls[-1])


async def test_model_error_returns_fallback_line(settings: Settings) -> None:
    h = Harness(settings, ScriptedModel(OllamaError("cannot reach Ollama")))
    sid = h.sessions.create()
    r = await h.pipeline.handle(sid, "hi")
    assert r.source == "line:guard_fallback"
    assert "cannot reach" in (r.error or "")
    assert r.text in h.lines.category("guard_fallback").lines
    assert h.sink.sentences(sid) == r.sentences
    assert "Ollama" not in r.text  # internals never shown to the visitor


async def test_unexpected_exception_is_also_fail_safe(settings: Settings) -> None:
    h = Harness(settings, ScriptedModel(RuntimeError("boom")))
    sid = h.sessions.create()
    r = await h.pipeline.handle(sid, "hi")
    assert r.source == "line:guard_fallback" and "boom" in (r.error or "")


async def test_mid_stream_error_keeps_shown_text_then_falls_back(settings: Settings) -> None:
    model = ScriptedModel(["First sentence. ", "Second", " part"])
    model.fail_after = 2
    h = Harness(settings, model)
    sid = h.sessions.create()
    r = await h.pipeline.handle(sid, "hi")
    assert r.sentences[0] == "First sentence."
    assert any(r.text.endswith(line) for line in h.lines.category("guard_fallback").lines)
    assert "Second" not in r.text  # incomplete sentence never shown
    assert h.sessions.get(sid)[-1].content == r.text  # type: ignore[index]


async def test_empty_model_reply_falls_back(settings: Settings) -> None:
    h = Harness(settings, ScriptedModel(["", "   "]))
    sid = h.sessions.create()
    r = await h.pipeline.handle(sid, "hi")
    assert r.source == "line:guard_fallback" and r.error is None and r.sentences


async def test_stalled_model_times_out_to_fallback(settings: Settings) -> None:
    class Stalls:
        async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
            yield "Hmm"
            await asyncio.sleep(10)

    h = Harness(settings, Stalls(), timeout=0.05)
    sid = h.sessions.create()
    r = await h.pipeline.handle(sid, "hi")
    assert r.source == "line:guard_fallback" and "Timeout" in (r.error or "")


async def test_fallback_does_not_repeat_back_to_back(settings: Settings) -> None:
    h = Harness(settings, ScriptedModel(*[OllamaError("x") for _ in range(10)]))
    sid = h.sessions.create()
    texts = [(await h.pipeline.handle(sid, f"q{i}")).text for i in range(10)]
    assert all(a != b for a, b in itertools.pairwise(texts))


async def test_blank_input_is_ignored(settings: Settings) -> None:
    model = ScriptedModel()
    h = Harness(settings, model)
    sid = h.sessions.create()
    r = await h.pipeline.handle(sid, "   ")
    assert r.source == "empty" and not model.calls and not h.sink.events


async def test_expired_session_raises(settings: Settings) -> None:
    h = Harness(settings, ScriptedModel())
    with pytest.raises(SessionGone):
        await h.pipeline.handle("missing", "hi")


async def test_timed_out_goodbye_is_emitted(settings: Settings) -> None:
    h = Harness(settings, ScriptedModel())
    sid = h.sessions.create()
    h.sessions.end(sid)
    r = await h.pipeline.timed_out(sid)
    assert r.source == "line:goodbye" and h.sink.sentences(sid) == r.sentences


async def test_fanout_reaches_every_sink(settings: Settings) -> None:
    h = Harness(settings, ScriptedModel(["One. Two."]))
    second = CollectingSink()
    h.pipeline.sink = FanoutSink(h.sink, second)
    sid = h.sessions.create()
    await h.pipeline.handle(sid, "count")
    assert h.sink.sentences() == second.sentences() == ["One.", "Two."]


async def test_end_to_end_with_mocked_ollama(fake: FakeOllama, settings: Settings) -> None:
    fake.chat_reply = lambda body: [
        {"message": {"thinking": "plan secretly", "content": ""}, "done": False},
        {"message": {"content": "Totally okay! I'm "}, "done": False},
        {"message": {"content": f"{settings.bot_name}."}, "done": False},
        FINAL_STATS,
    ]
    async with OllamaClient.from_settings(settings, fake.transport) as client:
        model = OllamaChatModel(client, settings)
        h = Harness(settings, model)
        sid = h.sessions.create()
        r = await h.pipeline.handle(sid, "hi?? is it ok to talk to you")

    assert r.sentences == ["Totally okay!", f"I'm {settings.bot_name}."]
    assert "plan secretly" not in r.text
    assert r.stats is not None and r.stats.eval_count == 40
    body = fake.requests[-1]
    assert body["think"] is False
    assert body["messages"][0]["role"] == "system"
    assert "robot helper for the AI Lab at SFSU" in body["messages"][0]["content"]
    assert body["messages"][-1] == {"role": "user", "content": "hi?? is it ok to talk to you"}
