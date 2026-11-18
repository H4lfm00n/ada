"""Kiosk server (M5): the lab screen's web UI, its websocket, and the admin endpoints.

    python -m app.server            # = make kiosk; http://127.0.0.1:8080/ui/

Routes:
  GET  /ui/        the kiosk page (ui/index.html with the UI copy embedded at serve time)
  GET  /ui/*       static files (CSS, JS, bundled fonts)
  WS   /ws         one websocket per screen; events in docs/ui-spec.md section 9
  GET  /healthz    {"ok", "ollama", "kill_switch", "warm", "session_active"}; no secrets
  /admin/*         the M3 kill-switch endpoints (Bearer ADA_ADMIN_TOKEN), loopback binds only

One kiosk = one screen = one active session (brief 5.6). A newer websocket (a page reload)
takes over and the older one is closed with code 4001, which the page doesn't retry.

Everything the visitor sees comes through the same pipeline as `make chat` (built by
app.factory): input guard -> RAG -> chat model -> output guard per sentence. The only text
this module sends on its own is a designer-written canned line (fallback, out of order).

Idle life (M5a, app/idle.py): while nobody is there, the tick sends `idle_act` events from
content/idle_acts.yaml; any interaction (tap = `wake`, typing, a message) restarts the quiet
clock. The page stops a running act on its own the moment someone interacts.

Hooks for M6 voice (not built yet): `Kiosk.audio_level(source, level)` and
`Kiosk.wake(source)` push the events the blob already understands.

Background loops: the idle clock ("Still there?", then goodbye + reset), the kill-switch
check (live "out of order", no reload), the Ollama reachability check (error screen), model
warm-up (retried until Ollama is up), and the websocket ping.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import math
import os
import random
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.admin import TOKEN_ENV, make_router
from app.cards import CardStage, classify_reply
from app.config import LOOPBACK_HOSTS, REPO_ROOT, Settings, load_settings
from app.factory import Built, build_pipeline, warm_up
from app.idle import IdleCatalog, IdlePicker, IdleScheduler, load_idle_acts
from app.lines import GUARD_FALLBACK, IDLE_GREETINGS, OUT_OF_ORDER, SLOT_RE
from app.ollama_client import OllamaClient
from app.sentences import split_sentences
from app.voice.speaker import Speaker

log = logging.getLogger("app.server")

UI_DIR = REPO_ROOT / "ui"
BOOT_MARKER = "<!--BOOT-->"
MAX_MESSAGE_CHARS = 4000  # hard cap before the pipeline; the input guard has its own limit
WS_MAX_BYTES = 64 * 1024
CLOSE_REPLACED = 4001  # a newer screen connected; the page shows "connection lost", no retry
UI_STRING_KEYS = frozenset(
    {
        "notice", "status_listening", "status_thinking", "status_reconnecting",
        "status_out_of_order", "status_connection_lost", "placeholder_idle",
        "placeholder_ready", "placeholder_fallback", "hint_busy", "still_there",
        "connection_lost_line", "talk_idle", "talk_listening", "talk_interrupt", "mic_off",
        "mic_on", "rest_title", "rest_body",
    }
)  # fmt: skip

Clock = Callable[[], float]


class UiStringsError(ValueError):
    """content/ui_strings.yaml is malformed or missing a key."""


def load_ui_strings(settings: Settings) -> dict[str, str]:
    """Designer-owned UI copy, {{bot_name}} filled ({{seconds}} is the screen's)."""
    path = settings.content_path(settings.kiosk_ui_strings_file)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("strings") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        raise UiStringsError(f"{path.name}: no 'strings' mapping")
    missing = sorted(UI_STRING_KEYS - raw.keys())
    if missing:
        raise UiStringsError(f"{path.name}: missing {', '.join(missing)}")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            raise UiStringsError(f"{path.name}: {key!r} must be text")
        out[str(key)] = SLOT_RE.sub(
            lambda m: settings.bot_name if m.group(1) == "bot_name" else m.group(0), value
        )
    # DES54: plain keys are screen-mode copy; in spoken mode `<key>_spoken` replaces `<key>`.
    # The page only ever sees the resolved set.
    spoken = {k[: -len("_spoken")]: v for k, v in out.items() if k.endswith("_spoken")}
    out = {k: v for k, v in out.items() if not k.endswith("_spoken")}
    if settings.output_mode == "spoken":
        out.update(spoken)
    return out


# -- the sink: pipeline events -> the screen ---------------------------------------------


class KioskSink:
    """OutputSink + ActionHandler for the kiosk (and the optional M5 sink extensions, D50).

    Only receives what the pipeline already approved: guarded model sentences and canned
    lines. TTS (M6) and the robot (M7) attach next to this via FanoutSink.
    """

    def __init__(self) -> None:
        self.kiosk: Kiosk | None = None

    async def on_sentence(self, session_id: str, sentence: str) -> None:
        await self.on_sourced_sentence(session_id, sentence, "model")

    async def on_sourced_sentence(self, session_id: str, sentence: str, source: str) -> None:
        if self.kiosk:
            await self.kiosk.sentence(sentence, source)

    async def on_retract(self, session_id: str) -> None:
        if self.kiosk:
            await self.kiosk.retract()

    async def on_action(self, session_id: str, action: str) -> None:
        if self.kiosk:
            await self.kiosk.emit({"type": "action", "name": action})


@dataclass(slots=True)
class Turn:
    id: str
    client_msg_id: str | None
    index: int = 0
    shown: int = 0  # sentences sent and not retracted
    task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class KioskStatus:
    backend_ok: bool = True  # Ollama reachable (unknown at start counts as OK)
    warm: bool = False
    warm_times: dict[str, float] = field(default_factory=dict)
    kill_on: bool = False


class Kiosk:
    """One screen: one websocket, one visitor session, one turn at a time."""

    def __init__(
        self,
        settings: Settings,
        built: Built,
        client: OllamaClient,
        sink: KioskSink,
        clock: Clock = time.monotonic,
        idle: IdleCatalog | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.built = built
        self.pipeline = built.pipeline
        self.sessions = built.sessions
        self.lines = built.pipeline.lines
        self.client = client
        self.clock = clock
        sink.kiosk = self
        self.ws: WebSocket | None = None
        self._send_lock = asyncio.Lock()
        self.sid: str | None = None
        self.turn: Turn | None = None
        self._turn_ids = itertools.count(1)
        self._warned_at: float | None = None  # session.last_active when "Still there?" went out
        self._recent: dict[str, str] = {}  # kiosk-level line picks (out_of_order)
        self.status = KioskStatus(kill_on=built.kill_switch.is_on())
        self._tasks: list[asyncio.Task[None]] = []
        self._pending_reset: str | None = None  # session_reset reason to send after turn_end
        self.idle = IdleScheduler(IdlePicker(idle, rng)) if idle and idle.acts else None
        self.screen_reduced = settings.kiosk_reduced_motion  # updated by the page's `screen`
        # Cards (M5b): the card on screen and its check-in loop (app/cards.py, spec 2.2).
        self.cards = CardStage(rng) if built.cards and built.cards.cards else None
        # Voice (M6): Ada's speech out; latency marks per voice turn (logged as VOICE lines).
        self.speaker = (
            Speaker(settings, self._send_bytes, self._send, self._audio_sent)
            if settings.tts_enabled
            else None
        )
        self._says = itertools.count(1)
        # Ears (M6b, tap-to-talk): one open capture at a time, half-duplex (the page never
        # opens the mic while Ada talks; the server refuses a capture during a turn).
        self.stt: Any = None
        self.vad: Any = None
        self.stt_failed: str | None = None
        self.capture: Any = None
        self._marks: dict[str, dict[str, float]] = {}

    # -- events out ----------------------------------------------------------------------

    async def emit(self, event: dict[str, Any]) -> None:
        # Every path that ends a visit or stops Ada announces it with one of these two
        # events, so a card can never outlive its visitor or survive the kill switch.
        if (
            self.cards
            and self.cards.current
            and (
                event["type"] == "session_reset"
                or (event["type"] == "out_of_order" and event.get("on"))
            )
        ):
            for e in self.cards.clear():
                await self._send(e)
        await self._send(event)

    async def _send(self, event: dict[str, Any]) -> None:
        ws = self.ws
        if ws is None:
            return
        async with self._send_lock:
            try:
                await ws.send_json(event)
            except Exception as exc:  # closed under us; the receive loop cleans up
                log.debug("send failed (%s): %s", event.get("type"), exc)

    def _speaking(self) -> bool:
        return bool(self.speaker and self.speaker.ready and not self.status.kill_on)

    async def sentence(self, text: str, source: str) -> None:
        t = self.turn
        if t is None:
            return
        ref, tts = f"{t.id}:{t.index}", self._speaking()
        await self.emit({"type": "sentence", "turn_id": t.id, "index": t.index, "text": text,
                         "source": source, "ref": ref, "tts": tts})  # fmt: skip
        if tts and self.speaker:
            self.speaker.speak(ref, text, first=t.index == 0)
        self._mark(t.id, "sentence")
        t.index += 1
        t.shown += 1

    async def _send_bytes(self, data: bytes) -> None:
        ws = self.ws
        if ws is None:
            return
        async with self._send_lock:
            try:
                await ws.send_bytes(data)
            except Exception as exc:
                log.debug("send bytes failed: %s", exc)

    def _mark(self, turn_id: str, what: str) -> None:
        m = self._marks.get(turn_id)
        if m is None or what in m:
            return
        m[what] = time.monotonic()
        if what == "playing":
            self._log_voice(turn_id)

    def _audio_sent(self, ref: str) -> None:
        self._mark(ref.split(":", 1)[0], "audio_sent")

    def _log_voice(self, turn_id: str) -> None:
        m = self._marks.pop(turn_id, {})
        eos = m.get("eos")
        if eos is None:
            return
        parts = [f"stt {m.get('stt', 0.0):.2f}s"]
        for key, label in (("sentence", "first sentence"), ("audio_sent", "first audio sent"),
                           ("playing", "first audio PLAYING")):  # fmt: skip
            if key in m:
                parts.append(f"{label} {m[key] - eos:.2f}s")
        target = "PASS" if "playing" in m and m["playing"] - eos < 2.5 else "MISS"
        log.info("VOICE %s end-of-speech -> %s [%s, target 2.5s; includes the %d ms "
                 "end-of-turn wait]", turn_id, " | ".join(parts), target,
                 self.settings.stt_end_silence_ms)  # fmt: skip

    async def retract(self) -> None:
        t = self.turn
        if t is None:
            return
        if self.speaker:
            self.speaker.flush()
        t.shown = 0
        await self.emit({"type": "reply_retracted", "turn_id": t.id})

    def out_of_order_line(self) -> str:
        return self.lines.pick(OUT_OF_ORDER, self._recent)

    def hello(self) -> dict[str, Any]:
        strings = load_ui_strings(self.settings)  # re-read: designer edits show on reload
        greetings = [SLOT_RE.sub(lambda _m: self.settings.bot_name, ln)
                     for ln in self.lines.category(IDLE_GREETINGS).lines]  # fmt: skip
        event: dict[str, Any] = {
            "type": "hello",
            "bot_name": self.settings.bot_name,
            "session_timeout_s": self.settings.session_timeout_s,
            "warn_before_s": self.settings.kiosk_warn_before_s,
            "out_of_order": self.status.kill_on,
            "reduced_motion": self.settings.kiosk_reduced_motion,
            "notice": strings["notice"],
            "ui_strings": strings,
            "idle_greetings": greetings,
            "backend_ok": self.status.backend_ok,
            "ping_s": self.settings.kiosk_ping_s,
            "idle_acts": self.idle is not None,
            "output_mode": self.settings.output_mode,
            "voice": self.voice_info(),
        }
        if self.status.kill_on:
            event["out_of_order_line"] = self.out_of_order_line()
        return event

    def voice_info(self) -> dict[str, Any]:
        s = self.settings
        return {
            "tts": bool(self.speaker),
            "volume": s.tts_volume,
            "stt": s.stt_enabled,
            "wake_word": s.wake_word_enabled,
            "constraints": {
                "echoCancellation": s.voice_echo_cancellation,
                "noiseSuppression": s.voice_noise_suppression,
                "autoGainControl": s.voice_auto_gain,
            },
        }

    # -- connection ----------------------------------------------------------------------

    async def serve(self, ws: WebSocket) -> None:
        await ws.accept()
        await self._check_kill_switch()  # hello must carry the current state, not the last tick's
        old, self.ws = self.ws, ws
        if old is not None:
            log.warning("a second screen connected; closing the older one")
            with contextlib.suppress(Exception):
                await old.close(code=CLOSE_REPLACED, reason="replaced by a newer screen")
        self._interrupt_idle()  # a fresh screen starts a fresh quiet period
        await self.emit(self.hello())
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(msg.get("code", 1000))
                if msg.get("bytes") is not None:
                    await self.on_audio(msg["bytes"])
                elif msg.get("text") is not None:
                    await self.on_client(msg["text"])
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("websocket receive loop failed")
        finally:
            if self.ws is ws:
                self.ws = None

    async def on_audio(self, data: bytes) -> None:
        """Mic audio from the page (16 kHz int16 mono). Dropped unless a capture is open."""
        cap = self.capture
        if cap is None:
            return
        end = cap.feed(data)
        if end is not None and self.capture is cap:
            await self._end_capture(end)

    async def on_mic_start(self) -> None:
        self._interrupt_idle()
        why = None
        if not self.settings.stt_enabled or self.stt is None or self.vad is None:
            why = "unavailable"
        elif self.status.kill_on or self.turn is not None:
            why = "busy"
        if why:
            await self.emit({"type": "mic", "on": False, "reason": why})
            return
        from app.voice.stt import Capture

        self.on_typing()  # keeps the visit alive, no "Still there?" while talking
        self.capture = Capture(self.vad, self.settings)
        await self.emit({"type": "mic", "on": True})

    async def _end_capture(self, reason: str) -> None:
        cap, self.capture = self.capture, None
        if cap is None:
            return
        if reason == "end" and (self.turn is not None or self.status.kill_on):
            reason = "busy"
        await self.emit({"type": "mic", "on": False, "reason": reason})
        if reason != "end":
            return
        audio = cap.audio()  # in memory only; dropped when the turn has transcribed it
        log.info("VOICE heard %.1fs of speech (%.1fs captured)", cap.voiced / 16000,
                 cap.total / 16000)  # fmt: skip
        self._start_turn(None, self._voice_turn(audio, cap.t_last_voice))

    async def _voice_turn(self, audio: Any, t_eos: float) -> str:
        sid = self._live_session()
        t0 = time.monotonic()
        try:
            text = await asyncio.to_thread(self.stt.transcribe, audio)
        finally:
            del audio
        if self.turn is not None:
            self._marks[self.turn.id] = {"eos": t_eos, "stt": time.monotonic() - t0}
            while len(self._marks) > 20:
                self._marks.pop(next(iter(self._marks)))
        await self.emit({"type": "heard", "text": text})  # staff debug panel only
        if not text.strip():
            source = "line:didnt_catch"
            for sent in split_sentences(self.lines.pick("didnt_catch", self._recent)):
                await self.sentence(sent, source)
            return source
        self.sessions.touch(sid)
        # The same routes as typed text: a card's yes/no, else the guarded pipeline.
        if self.cards and self.cards.listening and (answer := classify_reply(text)):
            return await self._card_answer_turn(sid, answer)
        return await self._visitor_turn(sid, text)

    async def on_client(self, raw: str) -> None:
        try:
            data = json.loads(raw)
            kind = data.get("type") if isinstance(data, dict) else None
        except ValueError:
            kind = None
        if kind == "message":
            text = data.get("text")
            cid = data.get("client_msg_id")
            cid = str(cid)[:64] if cid is not None else None
            if isinstance(text, str):
                await self.on_message(text[:MAX_MESSAGE_CHARS], cid)
        elif kind in ("typing", "wake"):  # wake = a tap on the blob / talk button
            self.on_typing()
        elif kind == "card_reply":  # "Yes, got it" / "Not yet" chips on a QR card (PMC3)
            answer = data.get("answer")
            if answer in ("yes", "no"):
                await self.on_card_reply(str(data.get("id") or ""), answer)
        elif kind == "screen":  # the page's prefers-reduced-motion, sent on connect/change
            self.screen_reduced = bool(data.get("reduced_motion")) or (
                self.settings.kiosk_reduced_motion
            )
        elif kind == "reset":
            await self.on_reset(bool(data.get("immediate")))
        elif kind == "pong":
            pass
        elif kind == "mic_start":  # tap-to-talk: the page opened the mic
            await self.on_mic_start()
        elif kind == "mic_stop":  # tapped again: done talking (or never started)
            if self.capture is not None:
                await self._end_capture("end" if self.capture.has_speech else "cancel")
        elif kind == "tts_playing":  # the page started playing a reply's first audio
            ref = str(data.get("ref") or "")
            self._mark(ref.split(":", 1)[0], "playing")
        else:
            log.debug("ignored client event %r", kind)

    # -- client events -------------------------------------------------------------------

    async def on_message(self, text: str, client_msg_id: str | None) -> None:
        self._interrupt_idle()
        if self.status.kill_on:  # the screen is already showing out of order; say it again
            await self.emit({"type": "out_of_order", "on": True, "line": self.out_of_order_line()})
            return
        if self.turn is not None:
            await self.emit({"type": "busy", "client_msg_id": client_msg_id})
            return
        if not text.strip():
            return
        sid = self._live_session()
        # "yes" / "not yet" while Ada is asking "did you scan it?" answers the card, not the
        # model (keywords only; anything else is a new question and the card goes mini).
        if self.cards and self.cards.listening and (answer := classify_reply(text)):
            self._start_turn(client_msg_id, self._card_answer_turn(sid, answer))
            return
        self._start_turn(client_msg_id, self._visitor_turn(sid, text))

    async def on_card_reply(self, card_id: str, answer: str) -> None:
        self._interrupt_idle()
        if self.cards is None or self.status.kill_on:
            return
        if self.sid and self.sessions.session(self.sid):
            self.sessions.touch(self.sid)
            self._warned_at = None
        await self.emit_cards(self.cards.reply(card_id, answer, self.clock()))

    async def emit_cards(self, events: list[dict[str, Any]], *, in_turn: bool = False) -> None:
        """Send card events. A card line is a caption in the current turn, or a `say`."""
        for e in events:
            if e["type"] == "say" and in_turn and self.turn is not None:
                await self.sentence(e["text"], e["source"])
            elif e["type"] == "say":  # a card line outside a reply ("Did you scan it?")
                ref, tts = f"say{next(self._says)}", self._speaking()
                await self.emit({**e, "ref": ref, "tts": tts})
                if tts and self.speaker:
                    self.speaker.speak(ref, e["text"])
            else:
                await self.emit(e)
            if e["type"] in ("show_card", "card_state") and self.sid and self.pipeline.transcripts:
                self.pipeline.transcripts.log_event(
                    self.sid, "card", id=e["id"], step=e.get("state", "shown")
                )

    def on_typing(self) -> None:
        self._interrupt_idle()
        if self.sid and self.turn is None and self.sessions.session(self.sid):
            self.sessions.touch(self.sid)
            self._warned_at = None

    async def on_reset(self, immediate: bool) -> None:
        self._interrupt_idle()
        if self.turn is not None:
            await self._cancel_turn()
        if self.sid is None or self.sessions.session(self.sid) is None or immediate:
            self._drop_session("reset")
            await self.emit({"type": "session_reset", "reason": "visitor"})
            return
        sid = self.sid
        self._start_turn(None, self._goodbye_turn(sid, "visitor"))

    # -- turns ---------------------------------------------------------------------------

    def _start_turn(self, client_msg_id: str | None, body: Any) -> None:
        turn = Turn(f"t{next(self._turn_ids)}", client_msg_id)
        self.turn = turn
        turn.task = asyncio.create_task(self._run_turn(turn, body))

    async def _run_turn(self, turn: Turn, body: Any) -> None:
        source = "empty"
        try:
            await self.emit({"type": "turn_start", "turn_id": turn.id,
                             "client_msg_id": turn.client_msg_id})  # fmt: skip
            source = await body
        except asyncio.CancelledError:
            raise
        except Exception:  # fail safe: never a stack trace, never a half reply
            log.exception("turn %s failed", turn.id)
            if turn.shown:
                await self.retract()
            source = f"line:{GUARD_FALLBACK}"
            for s in split_sentences(self.lines.pick(GUARD_FALLBACK, self._recent)):
                await self.sentence(s, source)
        finally:
            if self.turn is turn:
                self.turn = None
            self._warned_at = None
        await self.emit({"type": "turn_end", "turn_id": turn.id, "source": source})
        await self._after_turn()

    async def _card_answer_turn(self, sid: str, answer: str) -> str:
        self.sessions.touch(sid)
        card = self.cards.current if self.cards else None
        await self.emit_cards(self.cards.reply(None, answer, self.clock()) if self.cards else [],
                              in_turn=True)  # fmt: skip
        return f"card:{card}"

    async def _visitor_turn(self, sid: str, text: str) -> str:
        if self.cards and self.cards.current:  # asking something else: card goes mini
            await self.emit_cards(self.cards.new_question())
        result = await self.pipeline.handle(sid, text)
        if self.cards and result.card and not result.session_reset and not result.kill_switch:
            via = result.card.via
            await self.emit_cards(
                self.cards.show(result.card.card, self.clock(), say_intro=via != "tag"),
                in_turn=True,
            )
        if result.session_reset:  # "bye": the pipeline said goodbye and wiped the context
            self.sessions.end(sid)
            if self.sid == sid:
                self.sid = None
            self._pending_reset = "farewell"
        return result.source

    async def _goodbye_turn(self, sid: str, reason: str) -> str:
        result = await self.pipeline.goodbye(sid)
        self.sessions.end(sid)
        if self.sid == sid:
            self.sid = None
        self._pending_reset = reason
        return result.source

    async def _timeout_turn(self, sid: str) -> str:
        self.sessions.end(sid)
        if self.sid == sid:
            self.sid = None
        result = await self.pipeline.timed_out(sid)
        self._pending_reset = "timeout"
        return result.source

    async def _after_turn(self) -> None:
        reason, self._pending_reset = self._pending_reset, None
        if reason:
            await self.emit({"type": "session_reset", "reason": reason})
        if self.status.kill_on and self.sid:  # switched on mid-turn: next visitor starts fresh
            self._drop_session("kill_switch")

    async def _cancel_turn(self) -> None:
        turn = self.turn
        if turn is None or turn.task is None:
            return
        turn.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await turn.task
        if self.turn is turn:
            self.turn = None
        self._pending_reset = None
        if self.speaker:
            self.speaker.flush()
        if turn.shown:
            await self.emit({"type": "reply_retracted", "turn_id": turn.id})
        await self.emit({"type": "turn_end", "turn_id": turn.id, "source": "cancelled"})

    # -- sessions ------------------------------------------------------------------------

    def _live_session(self) -> str:
        if self.sid is not None:
            if self.sessions.session(self.sid) is not None:
                return self.sid
            self._drop_session("timeout")  # expired between two ticks
        self.sid = self.sessions.create()
        self._warned_at = None
        return self.sid

    def _drop_session(self, why: str) -> None:
        sid, self.sid = self.sid, None
        self._warned_at = None
        if sid is None:
            return
        had_turns = bool((s := self.sessions.peek(sid)) and s.turns)
        self.sessions.end(sid)
        if had_turns and self.pipeline.transcripts:
            self.pipeline.transcripts.end_visit(sid, "visit_end", why=why)

    # -- idle life + voice hooks -----------------------------------------------------------

    def _interrupt_idle(self) -> None:
        if self.idle is not None:
            self.idle.interrupt(self.clock())

    def quiet(self) -> bool:
        """Nobody is there: the screen may play idle acts."""
        return (
            self.ws is not None
            and self.sid is None
            and self.turn is None
            and not self.status.kill_on
            and self.status.backend_ok
            and not (self.cards and self.cards.current)
            and self.capture is None
        )

    async def idle_step(self) -> None:
        if self.idle is None:
            return
        cue = self.idle.step(self.clock(), quiet=self.quiet(), reduced=self.screen_reduced)
        if cue is not None:
            await self.emit(cue.event())

    async def wake(self, source: str) -> None:
        """M6 hook: the wake word fired (source "word"). Same as a tap on the page."""
        self.on_typing()
        await self.emit({"type": "wake", "source": source})

    async def audio_level(self, source: str, level: float) -> None:
        """M6 hook: mic (visitor) or tts (Ada) loudness, 0-1, ~20 Hz. The page smooths it."""
        if source not in ("mic", "tts"):
            raise ValueError(f"audio_level source must be mic or tts, not {source!r}")
        lvl = 0.0 if not math.isfinite(level) else min(1.0, max(0.0, level))
        await self.emit({"type": "audio_level", "source": source, "level": round(lvl, 3)})

    async def tick(self) -> None:
        """One step of the idle clock, the idle acts, and the kill-switch check."""
        await self._check_kill_switch()
        await self.idle_step()
        if self.cards and self.turn is None and not self.status.kill_on:
            await self.emit_cards(self.cards.step(self.clock()))
        if self.status.kill_on or self.turn is not None or self.sid is None:
            return
        s = self.sessions.peek(self.sid)
        if s is None or s.busy:
            return
        if self.sessions.is_expired(self.sid):
            if s.turns:
                self._start_turn(None, self._timeout_turn(self.sid))
            else:
                self._drop_session("timeout")
            return
        left = self.settings.session_timeout_s - (self.clock() - s.last_active)
        warn = self.settings.kiosk_warn_before_s
        if warn > 0 and left <= warn and self._warned_at != s.last_active:
            self._warned_at = s.last_active
            await self.emit({"type": "session_warning", "seconds_left": max(1, math.ceil(left))})

    async def _check_kill_switch(self) -> None:
        on = self.built.kill_switch.is_on()
        if on == self.status.kill_on:
            return
        self.status.kill_on = on
        log.warning("kill switch %s", "ON" if on else "OFF")
        if on:
            if self.speaker:  # the kill switch also mutes Ada (spec 5)
                self.speaker.flush()
            if self.capture is not None:  # ...and stops listening
                self.capture = None
                await self.emit({"type": "mic", "on": False, "reason": "cancel"})
            await self.emit({"type": "out_of_order", "on": True, "line": self.out_of_order_line()})
            if self.turn is None:
                self._drop_session("kill_switch")
        else:
            await self.emit({"type": "out_of_order", "on": False})

    async def check_backend(self) -> None:
        try:
            async with asyncio.timeout(3.0):
                await self.client.version()
            ok = True
        except Exception:
            ok = False
        if ok != self.status.backend_ok:
            self.status.backend_ok = ok
            (log.info if ok else log.error)("Ollama %s", "reachable again" if ok else "unreachable")
            await self.emit({"type": "backend", "ok": ok})

    # -- background loops ----------------------------------------------------------------

    def start(self, *, warm: bool = True) -> None:
        self._tasks = [
            asyncio.create_task(self._loop(self.tick, self.settings.kiosk_tick_s)),
            asyncio.create_task(
                self._loop(self.check_backend, self.settings.kiosk_backend_check_s)
            ),
            asyncio.create_task(self._loop(self._ping, self.settings.kiosk_ping_s)),
        ]
        if warm:
            self._tasks.append(asyncio.create_task(self._warm_up()))
        if self.speaker:
            from app.voice.tts import make_tts

            self.speaker.start()
            self._tasks.append(asyncio.create_task(self.speaker.load(make_tts)))
        if self.settings.stt_enabled:
            self._tasks.append(asyncio.create_task(self._load_stt()))

    async def _load_stt(self) -> None:
        from app.voice import stt

        s = self.settings
        try:
            self.vad = await asyncio.to_thread(stt.SileroVad, s.content_path(s.stt_vad_file))
            self.stt = await asyncio.to_thread(stt.make_stt, s)
        except Exception as exc:  # the talk button falls back to the typing box
            self.stt_failed = str(exc)
            self.stt = None
            log.error("STT unavailable, typing only: %s", exc)

    async def stop(self) -> None:
        if self.turn is not None:
            await self._cancel_turn()
        if self.speaker:
            await self.speaker.stop()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks = []

    @staticmethod
    async def _loop(step: Callable[[], Any], every_s: float) -> None:
        while True:
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("background step %s failed", getattr(step, "__name__", step))
            await asyncio.sleep(every_s)

    async def _ping(self) -> None:
        await self.emit({"type": "ping", "t": round(time.time(), 3)})

    async def _warm_up(self) -> None:
        delay = 5.0
        while True:
            try:
                self.status.warm_times = await warm_up(self.settings, self.client)
                self.status.warm = True
                log.info("models warm: %s", {k: round(v, 2) for k, v in
                                             self.status.warm_times.items()})  # fmt: skip
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("warm-up failed (%s); retrying in %.0fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)


# -- the app --------------------------------------------------------------------------------


def _csp(host: str) -> str:
    ws = f" ws://{host}" if host else ""
    return (
        "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
        f"img-src 'self' data:; connect-src 'self'{ws}; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    )


def _same_origin(ws: WebSocket) -> bool:
    """Browsers send Origin; only this server's own page may open the socket."""
    origin = ws.headers.get("origin")
    if not origin:
        return True  # not a browser (tests, curl): nothing cross-site to defend against
    return urlparse(origin).netloc == ws.headers.get("host", "")


def render_index(settings: Settings, ui_dir: Path = UI_DIR) -> str:
    html = (ui_dir / "index.html").read_text(encoding="utf-8")
    boot = {
        "bot_name": settings.bot_name,
        "output_mode": settings.output_mode,
        "ui_strings": load_ui_strings(settings),
    }
    blob = json.dumps(boot, ensure_ascii=False).replace("</", "<\\/")
    return html.replace(BOOT_MARKER, f'<script type="application/json" id="boot">{blob}</script>')


def create_app(
    settings: Settings,
    client: OllamaClient | None = None,
    *,
    background: bool = True,
    warm: bool = True,
    ui_dir: Path = UI_DIR,
) -> FastAPI:
    """The kiosk app. Tests pass a mocked client and drive `app.state.kiosk` directly."""
    load_ui_strings(settings)  # fail at startup, not on the first page load
    own_client = client is None
    client = client or OllamaClient.from_settings(settings)
    sink = KioskSink()
    built = build_pipeline(settings, client, sink, sink)
    for w in built.warnings:
        log.warning("%s", w)
    idle = None
    if settings.kiosk_idle_enabled:
        idle = load_idle_acts(
            settings.content_path(settings.kiosk_idle_acts_file), REPO_ROOT, settings.bot_name
        )  # fail at startup on a broken file, like ui_strings
        log.info("idle acts: %d playable", len(idle.acts))
        for act_id, why in idle.unavailable.items():
            log.info("idle act %s is off: %s", act_id, why)
    kiosk = Kiosk(settings, built, client, sink, idle=idle)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if background:
            kiosk.start(warm=warm)
        try:
            yield
        finally:
            await kiosk.stop()
            if own_client:
                await client.aclose()

    app = FastAPI(title="AI Lab kiosk", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)  # fmt: skip
    app.state.kiosk = kiosk

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        resp: Response = await call_next(request)
        resp.headers["Content-Security-Policy"] = _csp(request.headers.get("host", ""))
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.exception_handler(Exception)
    async def no_stack_traces(_request: Request, exc: Exception) -> Response:
        log.exception("request failed: %s", exc)
        return JSONResponse({"ok": False, "error": "internal error"}, status_code=500)

    @app.get("/", include_in_schema=False)
    @app.get("/ui", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    @app.api_route("/ui/", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/ui/index.html", methods=["GET", "HEAD"], include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(render_index(settings, ui_dir), headers={"Cache-Control": "no-store"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "ollama": kiosk.status.backend_ok,
            "warm": kiosk.status.warm,
            "kill_switch": built.kill_switch.is_on(),
            "session_active": kiosk.sid is not None,
            "screen_connected": kiosk.ws is not None,
        }

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        if not _same_origin(ws):
            log.warning("rejected websocket from origin %s", ws.headers.get("origin"))
            await ws.close(code=1008)
            return
        await kiosk.serve(ws)

    if settings.kiosk_host in LOOPBACK_HOSTS:
        app.include_router(make_router(built.kill_switch, os.environ.get(TOKEN_ENV)))
    else:
        log.warning("kiosk_host %s is not loopback: admin endpoints NOT mounted",
                    settings.kiosk_host)  # fmt: skip

    app.mount("/ui", StaticFiles(directory=ui_dir), name="ui")
    return app


def main() -> int:
    import uvicorn

    os.environ.setdefault("OTEL_SDK_DISABLED", "true")  # no telemetry
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)
    settings = load_settings()
    if settings.kiosk_host not in LOOPBACK_HOSTS:
        log.warning("kiosk bound to %s: reachable from the network", settings.kiosk_host)
    log.info("kiosk on http://%s:%d/ui/  (%s, guard %s)", settings.kiosk_host,
             settings.kiosk_port, settings.chat_model, settings.guard_model)  # fmt: skip
    uvicorn.run(
        create_app(settings),
        host=settings.kiosk_host,
        port=settings.kiosk_port,
        ws="websockets-sansio",
        ws_max_size=WS_MAX_BYTES,
        ws_ping_interval=None,  # our own app-level ping drives the screen's "connection lost"
        access_log=False,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
