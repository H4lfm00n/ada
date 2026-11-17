"""OutputSink and ActionHandler implementations.

OutputSink receives each completed reply sentence: the console now, the kiosk UI (M5)
and TTS (M6) later. ActionHandler receives named actions like "wave": a no-op now,
the robot arm (M7) later.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from app.interfaces import ActionHandler, OutputSink

log = logging.getLogger(__name__)


# Optional sink extensions (M5, DECISIONS D50). OutputSink.on_sentence stays the stable
# hook (TTS needs nothing more); a sink that also implements these gets the extra detail.


@runtime_checkable
class SourcedSink(Protocol):
    async def on_sourced_sentence(self, session_id: str, sentence: str, source: str) -> None:
        """Like on_sentence, plus where it came from: "model" or "line:<category>"."""
        ...


@runtime_checkable
class RetractingSink(Protocol):
    async def on_retract(self, session_id: str) -> None:
        """Take back this turn's sentences so far (a canned line replaces them)."""
        ...


async def send_sentence(sink: OutputSink, session_id: str, sentence: str, source: str) -> None:
    if isinstance(sink, SourcedSink):
        await sink.on_sourced_sentence(session_id, sentence, source)
    else:
        await sink.on_sentence(session_id, sentence)


async def retract(sink: OutputSink, session_id: str) -> None:
    if isinstance(sink, RetractingSink):
        await sink.on_retract(session_id)


class ConsoleSink:
    """Prints each sentence as it completes, all on one line per reply."""

    def __init__(self, prefix: str = "", write: Callable[[str], None] | None = None) -> None:
        self.prefix = prefix
        self._write = write or (lambda s: print(s, end="", flush=True))
        self._started: set[str] = set()

    async def on_sentence(self, session_id: str, sentence: str) -> None:
        if session_id not in self._started:
            self._started.add(session_id)
            self._write(self.prefix + sentence)
        else:
            self._write(" " + sentence)

    def end_reply(self, session_id: str) -> None:
        if session_id in self._started:
            self._started.discard(session_id)
            self._write("\n")


class CollectingSink:
    """Keeps every sentence, per session. Handy in tests and scripted runs."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def on_sentence(self, session_id: str, sentence: str) -> None:
        self.events.append((session_id, sentence))

    def sentences(self, session_id: str | None = None) -> list[str]:
        return [s for sid, s in self.events if session_id in (None, sid)]


class FanoutSink:
    """Sends every sentence to several sinks in order (e.g. UI + TTS later)."""

    def __init__(self, *sinks: OutputSink) -> None:
        self.sinks = list(sinks)

    async def on_sentence(self, session_id: str, sentence: str) -> None:
        for sink in self.sinks:
            await sink.on_sentence(session_id, sentence)

    async def on_sourced_sentence(self, session_id: str, sentence: str, source: str) -> None:
        for sink in self.sinks:
            await send_sentence(sink, session_id, sentence, source)

    async def on_retract(self, session_id: str) -> None:
        for sink in self.sinks:
            await retract(sink, session_id)


class NoopActionHandler:
    """Robot hook placeholder (M7). Records actions so tests and the CLI can show them."""

    def __init__(self, keep: int = 100) -> None:
        self.actions: deque[tuple[str, str]] = deque(maxlen=keep)

    async def on_action(self, session_id: str, action: str) -> None:
        self.actions.append((session_id, action))
        log.debug("action %s (session %s): no-op until the robot arm lands", action, session_id)


__all__ = [
    "ActionHandler",
    "CollectingSink",
    "ConsoleSink",
    "FanoutSink",
    "NoopActionHandler",
    "OutputSink",
    "RetractingSink",
    "SourcedSink",
    "retract",
    "send_sentence",
]
