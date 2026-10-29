"""The conversation pipeline: one visitor message in, sentence events out.

M1 stages (brief section 4); later milestones slot in at the marked hooks:

    farewell? ---------------------------> goodbye line, fresh context
    1. input guard                         (M3 hook: _check_input)
    2. retrieve lab docs                   (M2: Retriever, see _retrieve)
    3. build prompt (persona + context)
    4. stream from the chat model
    5. output guard, per sentence          (M3 hook: _emit)
    6. emit sentences to the OutputSink

Grounding (M2): only retrieved lab-doc chunks above the score cutoff reach the prompt.
If retrieval fails or finds nothing, the prompt's <lab_docs> block is empty and the
designer's prompt rule makes the model say "not sure" + "lab member".

Fail safe: if the model errors, times out, or says nothing, the visitor gets a
`guard_fallback` line from content/lines.yaml instead of an exception.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.interfaces import (
    ACTION_WAVE,
    ActionHandler,
    ChatModel,
    Chunk,
    Message,
    OutputSink,
    Retriever,
)
from app.lines import GOODBYE, GUARD_FALLBACK, LineBook
from app.ollama_client import ChatStats
from app.prompt import PromptBuilder
from app.sentences import SentenceSplitter, split_sentences
from app.sessions import InMemorySessionManager

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


class SessionGone(KeyError):
    """The session timed out or was never created; start a new one."""


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
        self.reply_timeout_s = reply_timeout_s
        self.retriever = retriever
        self.rag_top_k = rag_top_k
        self.retrieval_timeout_s = retrieval_timeout_s
        self._clock = clock

    async def handle(self, session_id: str, user_text: str) -> TurnResult:
        """Run one visitor message through the pipeline. Never raises for model failures."""
        if self.sessions.session(session_id) is None:
            raise SessionGone(session_id)
        text = user_text.strip()
        if not text:
            return TurnResult(session_id, user_text, source="empty")
        if is_farewell(text):
            return await self.goodbye(session_id, user_text=text)

        result = TurnResult(session_id, text)
        t0 = self._clock()
        with self.sessions.active(session_id) as session:
            if session.turns == 0:
                await self.actions.on_action(session_id, ACTION_WAVE)

            # M3 hook: input guard runs here, before the model sees anything.
            chunks = await self._retrieve(session.history, text, result)
            messages = self.prompt.build(session.history, text, chunks)

            try:
                async with asyncio.timeout(self.reply_timeout_s):
                    await self._stream_reply(messages, result, t0)
            except Exception as exc:  # fail safe: any model failure -> canned line
                result.error = f"{type(exc).__name__}: {exc}"
                log.warning("chat model failed (session %s): %s", session_id, result.error)

            if result.error or not result.sentences:
                result.source = f"line:{GUARD_FALLBACK}"
                line = self.lines.pick(GUARD_FALLBACK, session.recent_lines)
                for sentence in split_sentences(line):
                    await self._send(session_id, sentence, result, t0)

            result.stats = getattr(self.model, "last_stats", None)
            self.sessions.append(
                session_id, Message("user", text), Message("assistant", result.text)
            )
            session.turns += 1
        result.total_s = self._clock() - t0
        return result

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
        return result

    async def timed_out(self, session_id: str) -> TurnResult:
        """Goodbye for a session the manager already expired (no context left to wipe)."""
        t0 = self._clock()
        result = TurnResult(session_id, "", source=f"line:{GOODBYE}", session_reset=True)
        for sentence in split_sentences(self.lines.pick(GOODBYE)):
            await self._send(session_id, sentence, result, t0)
        result.total_s = self._clock() - t0
        return result

    # -- stages ---------------------------------------------------------------------

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

    async def _stream_reply(self, messages: list[Message], result: TurnResult, t0: float) -> None:
        splitter = SentenceSplitter()
        async for delta in self.model.stream(messages):
            if result.ttft_s is None and delta.strip():
                result.ttft_s = self._clock() - t0
            for sentence in splitter.feed(delta):
                await self._emit(result.session_id, sentence, result, t0)
        for sentence in splitter.flush():
            await self._emit(result.session_id, sentence, result, t0)

    async def _emit(self, session_id: str, sentence: str, result: TurnResult, t0: float) -> None:
        # M3 hook: output guard checks each sentence here, before anything is shown.
        await self._send(session_id, sentence, result, t0)

    async def _send(self, session_id: str, sentence: str, result: TurnResult, t0: float) -> None:
        if result.first_sentence_s is None:
            result.first_sentence_s = self._clock() - t0
        result.sentences.append(sentence)
        await self.sink.on_sentence(session_id, sentence)
