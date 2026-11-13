"""The conversation pipeline: one visitor message in, sentence events out.

Stages (brief section 4):

    kill switch on? ---------------------> out_of_order line (nothing else runs)
    farewell? ---------------------------> goodbye line, fresh context
    1. input guard: rules, then guard model ---- blocked -> mapped line (lines.yaml)
    2. retrieve lab docs                   (optionally in parallel with 1, D35)
    3. build prompt (persona + context)
    4. stream from the chat model
    5. output guard on each sentence, pipelined with generation ---- blocked -> line
    6. emit approved sentences to the OutputSink
    7. transcript (text only)

Guards are required constructor arguments: there is no way to build an unguarded
pipeline. Model text reaches the sink only through `_stream_reply`, after the output
guard approved it; everything else the sink gets is a designer-written canned line.

Grounding (M2): only retrieved lab-doc chunks above the score cutoff reach the prompt.
If retrieval fails or finds nothing, the prompt's <lab_docs> block is empty and the
designer's prompt rule makes the model say "not sure" + "lab member".

Fail safe: if a guard or the model errors, times out, or says nothing, the visitor gets
a canned line from content/lines.yaml instead of an exception or unchecked text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.guards.chain import GuardDecision
from app.guards.normalize import normalize
from app.interfaces import (
    ACTION_WAVE,
    ActionHandler,
    ChatModel,
    Chunk,
    Message,
    OutputSink,
    Retriever,
)
from app.killswitch import KillSwitch
from app.lines import GOODBYE, GUARD_FALLBACK, OUT_OF_ORDER, LineBook
from app.ollama_client import ChatStats
from app.prompt import PromptBuilder
from app.sentences import SentenceSplitter, split_sentences
from app.sessions import InMemorySessionManager, Session
from app.transcripts import TranscriptLogger

log = logging.getLogger(__name__)

# Whole-message farewells (after lowercasing and stripping punctuation). Kept short and
# exact so "bye, but what's a GAN?" still reaches the model. These are input patterns,
# not user-facing wording; the reply itself comes from the `goodbye` lines.
FAREWELLS = frozenset(
    {
        "bye", "bye bye", "byebye", "goodbye", "good bye", "bye now", "ok bye", "okay bye",
        "thanks bye", "thank you bye", "bye thanks", "bye thank you", "see you", "see ya",
        "see you later", "cya", "later", "gotta go", "i gotta go", "i have to go",
        "im leaving", "i am leaving", "goodnight", "good night",
    }
)  # fmt: skip
_PUNCT_RE = re.compile(r"[^\w\s]")


def is_farewell(text: str) -> bool:
    norm = " ".join(_PUNCT_RE.sub("", text.lower()).split())
    return norm in FAREWELLS


# A blocked message never enters the history (the chat model would read it next turn).
# This stand-in goes there instead, so the model knows a turn was answered by a canned
# line. Model-facing only, never shown to anyone (DECISIONS D34).
WITHHELD = "[message withheld by the safety filter]"


class SessionGone(KeyError):
    """The session timed out or was never created; start a new one."""


class KillSwitchTripped(Exception):
    """The kill switch went on while a reply was streaming."""


class TextGuard(Protocol):
    """One side of the guard (app.guards.GuardChain). Never raises; errors block."""

    async def check(self, text: str, context: list[Message] | None = None) -> GuardDecision: ...


@dataclass(slots=True)
class TurnResult:
    session_id: str
    user_text: str
    sentences: list[str] = field(default_factory=list)
    source: str = "model"  # "model" | "line:<category>" | "empty"
    ttft_s: float | None = None  # request start -> first non-empty model token
    first_sentence_s: float | None = None  # request start -> first sentence emitted
    total_s: float = 0.0
    stats: ChatStats | None = None
    error: str | None = None
    session_reset: bool = False
    # Retrieval (M2): which chunks reached the prompt, and how long the search took.
    retrieved: list[Chunk] = field(default_factory=list)
    retrieval_s: float | None = None
    retrieval_query: str | None = None  # the query that produced `retrieved`
    retrieval_error: str | None = None
    # Guards (M3): every decision, for the transcript and the eval runner.
    input_guard: GuardDecision | None = None
    output_guard: list[GuardDecision] = field(default_factory=list)
    output_block: GuardDecision | None = None
    blocked_sentence: str | None = None  # never shown; redacted in the transcript
    kill_switch: bool = False
    added_to_history: bool = False

    @property
    def text(self) -> str:
        return " ".join(self.sentences)


class ChatPipeline:
    def __init__(
        self,
        model: ChatModel,
        prompt: PromptBuilder,
        lines: LineBook,
        sessions: InMemorySessionManager,
        sink: OutputSink,
        actions: ActionHandler,
        input_guard: TextGuard,
        output_guard: TextGuard,
        kill_switch: KillSwitch,
        transcripts: TranscriptLogger | None = None,
        parallel_retrieval: bool = False,
        reply_timeout_s: float | None = None,
        retriever: Retriever | None = None,
        rag_top_k: int = 4,
        retrieval_timeout_s: float | None = 5.0,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.model = model
        self.prompt = prompt
        self.lines = lines
        self.sessions = sessions
        self.sink = sink
        self.actions = actions
        self.input_guard = input_guard
        self.output_guard = output_guard
        self.kill_switch = kill_switch
        self.transcripts = transcripts
        self.parallel_retrieval = parallel_retrieval
        self.reply_timeout_s = reply_timeout_s
        self.retriever = retriever
        self.rag_top_k = rag_top_k
        self.retrieval_timeout_s = retrieval_timeout_s
        self._clock = clock

    async def handle(self, session_id: str, user_text: str) -> TurnResult:
        """Run one visitor message through the pipeline. Never raises for model/guard failures."""
        if self.sessions.session(session_id) is None:
            raise SessionGone(session_id)
        text = normalize(user_text)  # what every guard and the model see (zero-width etc. gone)
        if not text:
            return TurnResult(session_id, user_text, source="empty")
        if self.kill_switch.is_on():
            return await self._out_of_order(session_id, text)
        if is_farewell(text):
            return await self.goodbye(session_id, user_text=text)

        result = TurnResult(session_id, text)
        t0 = self._clock()
        with self.sessions.active(session_id) as session:
            if session.turns == 0:
                await self.actions.on_action(session_id, ACTION_WAVE)

            decision, chunks = await self._guard_input(session.history, text, result)
            if not decision.allowed:
                await self._say(decision.line or GUARD_FALLBACK, session, result, t0)
                self.sessions.append(
                    session_id, Message("user", WITHHELD), Message("assistant", result.text)
                )
            elif self.kill_switch.is_on():  # flipped while the guard ran
                result.kill_switch = True
                await self._say(OUT_OF_ORDER, session, result, t0)
            else:
                await self._model_reply(session, text, chunks, result, t0)
            session.turns += 1
        result.total_s = self._clock() - t0
        if self.transcripts:
            self.transcripts.log_turn(result)
        return result

    async def _model_reply(
        self, session: Session, text: str, chunks: list[Chunk], result: TurnResult, t0: float
    ) -> None:
        messages = self.prompt.build(session.history, text, chunks)
        try:
            async with asyncio.timeout(self.reply_timeout_s):
                await self._stream_reply(messages, text, result, t0)
        except KillSwitchTripped:
            result.kill_switch = True
        except Exception as exc:  # fail safe: any model failure -> canned line
            result.error = f"{type(exc).__name__}: {exc}"
            log.warning("chat model failed (session %s): %s", session.id, result.error)

        if result.kill_switch:
            await self._say(OUT_OF_ORDER, session, result, t0)
        elif result.output_block is not None:
            await self._say(result.output_block.line or GUARD_FALLBACK, session, result, t0)
        elif result.error or not result.sentences:
            await self._say(GUARD_FALLBACK, session, result, t0)

        result.stats = getattr(self.model, "last_stats", None)
        self.sessions.append(session.id, Message("user", text), Message("assistant", result.text))
        result.added_to_history = True

    async def _out_of_order(self, session_id: str, text: str) -> TurnResult:
        """Kill switch on: a canned line, no guards, no model, nothing remembered."""
        t0 = self._clock()
        result = TurnResult(session_id, text, kill_switch=True)
        await self._say(OUT_OF_ORDER, self.sessions.session(session_id), result, t0)
        result.total_s = self._clock() - t0
        if self.transcripts:
            self.transcripts.log_turn(result)
        return result

    async def _say(
        self, category: str, session: Session | None, result: TurnResult, t0: float
    ) -> None:
        """Show a canned line (designer-written, vetted: no guard model call)."""
        if not self.lines.has(category):
            log.warning("lines.yaml has no %r category (TODO designer); using fallback", category)
            category = GUARD_FALLBACK
        result.source = f"line:{category}"
        line = self.lines.pick(category, session.recent_lines if session else None)
        for sentence in split_sentences(line):
            await self._send(result.session_id, sentence, result, t0)

    async def goodbye(self, session_id: str, user_text: str = "") -> TurnResult:
        """Say a goodbye line and wipe the context. Used for "bye" and explicit reset."""
        t0 = self._clock()
        result = TurnResult(session_id, user_text, source=f"line:{GOODBYE}", session_reset=True)
        session = self.sessions.session(session_id)
        line = self.lines.pick(GOODBYE, session.recent_lines if session else None)
        for sentence in split_sentences(line):
            await self._send(session_id, sentence, result, t0)
        self.sessions.reset(session_id)
        result.total_s = self._clock() - t0
        if self.transcripts:
            self.transcripts.log_turn(result)
            self.transcripts.end_visit(session_id, "visit_end", why="goodbye")
        return result

    async def timed_out(self, session_id: str) -> TurnResult:
        """Goodbye for a session the manager already expired (no context left to wipe)."""
        t0 = self._clock()
        result = TurnResult(session_id, "", source=f"line:{GOODBYE}", session_reset=True)
        for sentence in split_sentences(self.lines.pick(GOODBYE)):
            await self._send(session_id, sentence, result, t0)
        result.total_s = self._clock() - t0
        if self.transcripts:
            self.transcripts.end_visit(session_id, "visit_end", why="timeout")
        return result

    # -- stages ---------------------------------------------------------------------

    async def _guard_input(
        self, history: list[Message], text: str, result: TurnResult
    ) -> tuple[GuardDecision, list[Chunk]]:
        """Input guard, then retrieval (or both at once with parallel_retrieval).

        Parallel mode only overlaps the guard with *embedding the question*: no text
        reaches the chat model, the sink, or the history until the guard has allowed it.
        """
        if self.parallel_retrieval and self.retriever is not None:
            retrieval = asyncio.create_task(self._retrieve(history, text, result))
            try:
                decision = await self.input_guard.check(text, history)
            except BaseException:
                retrieval.cancel()
                raise
            if not decision.allowed:
                retrieval.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await retrieval
                result.retrieved = []
                result.input_guard = decision
                return decision, []
            chunks = await retrieval
        else:
            decision = await self.input_guard.check(text, history)
            chunks = await self._retrieve(history, text, result) if decision.allowed else []
        result.input_guard = decision
        return decision, chunks

    async def _retrieve(self, history: list[Message], text: str, result: TurnResult) -> list[Chunk]:
        """Search the lab docs. Never raises: a failure means no docs, so "not sure".

        A follow-up like "and where is it?" retrieves nothing on its own, so if the
        message alone finds nothing, retry once with the previous visitor message
        prepended (DECISIONS D21).
        """
        if self.retriever is None:
            return []
        t0 = self._clock()
        queries = [text]
        prev = next((m.content for m in reversed(history) if m.role == "user"), None)
        if prev:
            queries.append(f"{prev}\n{text}")
        chunks: list[Chunk] = []
        try:
            async with asyncio.timeout(self.retrieval_timeout_s):
                for q in queries:
                    chunks = await self.retriever.search(q, self.rag_top_k)
                    if chunks:
                        result.retrieval_query = q
                        break
        except Exception as exc:  # fail safe: no docs rather than no reply
            chunks = []
            result.retrieval_error = f"{type(exc).__name__}: {exc}"
            log.warning("retrieval failed (session %s): %s", result.session_id, exc)
        result.retrieval_s = self._clock() - t0
        result.retrieved = chunks
        return chunks

    async def _stream_reply(
        self, messages: list[Message], user_text: str, result: TurnResult, t0: float
    ) -> None:
        """Stream the model, guard each sentence, and only then send it.

        Generation and guarding overlap: while the output guard checks sentence N, the
        model keeps writing N+1 into the queue. On a block, a kill-switch flip, or an
        error, generation is cancelled and nothing after that point is shown.
        """
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        failure: list[BaseException] = []

        async def produce() -> None:
            splitter = SentenceSplitter()
            try:
                async for delta in self.model.stream(messages):
                    if result.ttft_s is None and delta.strip():
                        result.ttft_s = self._clock() - t0
                    for sentence in splitter.feed(delta):
                        queue.put_nowait(sentence)
                for sentence in splitter.flush():
                    queue.put_nowait(sentence)
            except Exception as exc:  # reported after the sentences that made it
                failure.append(exc)
            finally:
                queue.put_nowait(None)

        producer = asyncio.create_task(produce())
        context = [Message("user", user_text)]
        shown: list[str] = []
        try:
            while (sentence := await queue.get()) is not None:
                if self.kill_switch.is_on():
                    raise KillSwitchTripped
                # The guard model sees the whole reply so far, so a harmful statement
                # can't hide by being split across two harmless-looking sentences.
                decision = await self.output_guard.check(" ".join([*shown, sentence]), context)
                result.output_guard.append(decision)
                if not decision.allowed:
                    result.output_block = decision
                    result.blocked_sentence = sentence
                    return
                if self.kill_switch.is_on():
                    raise KillSwitchTripped
                shown.append(sentence)
                await self._send(result.session_id, sentence, result, t0)
            if failure:
                raise failure[0]
        finally:
            if not producer.done():
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer

    async def _send(self, session_id: str, sentence: str, result: TurnResult, t0: float) -> None:
        if result.first_sentence_s is None:
            result.first_sentence_s = self._clock() - t0
        result.sentences.append(sentence)
        await self.sink.on_sentence(session_id, sentence)
