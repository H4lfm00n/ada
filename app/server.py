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
from app.config import LOOPBACK_HOSTS, REPO_ROOT, Settings, load_settings
from app.factory import Built, build_pipeline, warm_up
from app.lines import GUARD_FALLBACK, IDLE_GREETINGS, OUT_OF_ORDER, SLOT_RE
from app.ollama_client import OllamaClient
from app.sentences import split_sentences

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
        "connection_lost_line",
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

    # -- events out ----------------------------------------------------------------------

    async def emit(self, event: dict[str, Any]) -> None:
        ws = self.ws
        if ws is None:
            return
        async with self._send_lock:
            try:
                await ws.send_json(event)
            except Exception as exc:  # closed under us; the receive loop cleans up
                log.debug("send failed (%s): %s", event.get("type"), exc)

    async def sentence(self, text: str, source: str) -> None:
        t = self.turn
        if t is None:
            return
        await self.emit({"type": "sentence", "turn_id": t.id, "index": t.index, "text": text,
                         "source": source})  # fmt: skip
        t.index += 1
        t.shown += 1

    async def retract(self) -> None:
        t = self.turn
        if t is None:
            return
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
        }
        if self.status.kill_on:
            event["out_of_order_line"] = self.out_of_order_line()
        return event

    # -- connection ----------------------------------------------------------------------

    async def serve(self, ws: WebSocket) -> None:
        await ws.accept()
        await self._check_kill_switch()  # hello must carry the current state, not the last tick's
        old, self.ws = self.ws, ws
        if old is not None:
            log.warning("a second screen connected; closing the older one")
            with contextlib.suppress(Exception):
                await old.close(code=CLOSE_REPLACED, reason="replaced by a newer screen")
        await self.emit(self.hello())
        try:
            while True:
                raw = await ws.receive_text()
                await self.on_client(raw)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("websocket receive loop failed")
        finally:
            if self.ws is ws:
                self.ws = None

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
        elif kind == "typing":
            self.on_typing()
        elif kind == "reset":
            await self.on_reset(bool(data.get("immediate")))
        elif kind == "pong":
            pass
        else:
            log.debug("ignored client event %r", kind)

    # -- client events -------------------------------------------------------------------

    async def on_message(self, text: str, client_msg_id: str | None) -> None:
        if self.status.kill_on:  # the screen is already showing out of order; say it again
            await self.emit({"type": "out_of_order", "on": True, "line": self.out_of_order_line()})
            return
        if self.turn is not None:
            await self.emit({"type": "busy", "client_msg_id": client_msg_id})
            return
        if not text.strip():
            return
        sid = self._live_session()
        self._start_turn(client_msg_id, self._visitor_turn(sid, text))

    def on_typing(self) -> None:
        if self.sid and self.turn is None and self.sessions.session(self.sid):
            self.sessions.touch(self.sid)
            self._warned_at = None

    async def on_reset(self, immediate: bool) -> None:
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

    async def _visitor_turn(self, sid: str, text: str) -> str:
        result = await self.pipeline.handle(sid, text)
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

    async def tick(self) -> None:
        """One step of the idle clock and the kill-switch check."""
        await self._check_kill_switch()
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

    async def stop(self) -> None:
        if self.turn is not None:
            await self._cancel_turn()
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
    boot = {"bot_name": settings.bot_name, "ui_strings": load_ui_strings(settings)}
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
    kiosk = Kiosk(settings, built, client, sink)

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
