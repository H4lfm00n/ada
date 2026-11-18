"""The kiosk's voice: speaks approved sentences in order, streaming PCM to the page.

Binary websocket frame (server -> page), one per spoken part:
    uint32 little-endian N | N bytes of JSON header (space-padded to even) | int16 LE mono PCM
Header: {"type": "tts", "ref", "part", "last", "words", "sr"}. `ref` matches the `ref` of the
`sentence` / `say` event that carried the caption; the page lights `words` caption words over
the part's real audio length. If synthesis fails the page gets `tts_skip {ref}` (JSON) and
shows the caption without sound.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import Settings
from app.voice.tts import TtsEngine, split_parts, to_pcm16

log = logging.getLogger("app.voice.speaker")

SendBytes = Callable[[bytes], Awaitable[None]]
SendJson = Callable[[dict[str, Any]], Awaitable[None]]


def pack_frame(header: dict[str, Any], pcm: bytes) -> bytes:
    h = json.dumps(header, separators=(",", ":")).encode()
    if len(h) % 2:
        h += b" "
    return struct.pack("<I", len(h)) + h + pcm


def unpack_frame(frame: bytes) -> tuple[dict[str, Any], bytes]:
    (n,) = struct.unpack_from("<I", frame)
    return json.loads(frame[4 : 4 + n]), frame[4 + n :]


class Speaker:
    def __init__(
        self,
        settings: Settings,
        send_bytes: SendBytes,
        send_json: SendJson,
        on_audio_sent: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.engine: TtsEngine | None = None
        self.failed: str | None = None
        self._send_bytes = send_bytes
        self._send_json = send_json
        self._on_audio_sent = on_audio_sent
        self._q: asyncio.Queue[tuple[int, str, str, bool]] = asyncio.Queue()
        self._gen = 0
        self._task: asyncio.Task[None] | None = None

    @property
    def ready(self) -> bool:
        return self.engine is not None

    async def load(self, factory: Callable[[Settings], TtsEngine]) -> None:
        try:
            self.engine = await asyncio.to_thread(factory, self.settings)
        except Exception as exc:  # captions still work; the page shows text without sound
            self.failed = str(exc)
            log.error("TTS unavailable, captions only: %s", exc)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def speak(self, ref: str, text: str, *, first: bool = False) -> None:
        self._q.put_nowait((self._gen, ref, text, first))

    def flush(self) -> None:
        """Drop everything queued or in progress (retract, kill switch, cancelled turn)."""
        self._gen += 1
        while not self._q.empty():
            self._q.get_nowait()

    async def _run(self) -> None:
        while True:
            gen, ref, text, first = await self._q.get()
            if gen != self._gen or self.engine is None:
                continue
            try:
                await self._say(gen, ref, text, first)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("TTS failed for %s", ref)
                await self._send_json({"type": "tts_skip", "ref": ref})

    async def _say(self, gen: int, ref: str, text: str, first: bool) -> None:
        s = self.settings
        engine = self.engine
        assert engine is not None
        parts = split_parts(text, s.tts_pronunciations, first=first,
                            first_part_words=s.tts_first_part_words)  # fmt: skip
        for i, part in enumerate(parts):
            t0 = time.perf_counter()
            audio = await asyncio.to_thread(engine.synth, part.speech)
            if gen != self._gen:
                return  # flushed while synthesizing: never send stale audio
            header = {"type": "tts", "ref": ref, "part": i, "last": i == len(parts) - 1,
                      "words": part.words, "sr": engine.sample_rate}  # fmt: skip
            await self._send_bytes(pack_frame(header, to_pcm16(audio)))
            log.debug("tts %s/%d: %.2fs audio in %.2fs", ref, i,
                      len(audio) / engine.sample_rate, time.perf_counter() - t0)  # fmt: skip
            if i == 0 and self._on_audio_sent:
                self._on_audio_sent(ref)
