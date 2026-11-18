"""M5 kiosk server: websocket events, session reset, kill switch, failures. Ollama mocked.

Everything runs through the real pipeline (app.factory.build_pipeline) with the real
guard rules and the real Llama Guard wrapper; only Ollama's HTTP API is faked.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.killswitch import KillSwitch
from app.ollama_client import OllamaClient
from app.server import CLOSE_REPLACED, create_app, load_ui_strings, render_index

from .conftest import FINAL_STATS, FakeOllama

Event = dict[str, Any]


def make_settings(tmp_path: Path, **over: Any) -> Settings:
    base: dict[str, Any] = {
        "chat_model": "qwen-test:27b",
        "guard_model": "guard-test:1b",
        "embed_model": "emb-test",
        "kill_switch_file": str(tmp_path / "kill_switch.json"),
        "transcripts_dir": str(tmp_path / "transcripts"),
        "rag_index_dir": str(tmp_path / "rag"),
        "session_timeout_s": 30.0,
        "kiosk_warn_before_s": 10.0,
        "kiosk_tick_s": 0.05,
        "kiosk_backend_check_s": 0.05,
        "kiosk_ping_s": 30.0,
    }
    base.update(over)
    return Settings(**base)


def reply(*deltas: str) -> Callable[[dict], list[dict]]:
    return lambda body: [
        *({"message": {"role": "assistant", "content": d}, "done": False} for d in deltas),
        FINAL_STATS,
    ]


def guard_blocks(word: str) -> Callable[[dict], list[dict]]:
    """Llama Guard stand-in: unsafe if the judged (last) message contains `word`."""

    def judge(body: dict) -> list[dict]:
        verdict = "unsafe\nS1" if word in body["messages"][-1]["content"] else "safe"
        return [{"message": {"content": verdict}, "done": False}, FINAL_STATS]

    return judge


@pytest.fixture
def fake() -> FakeOllama:
    f = FakeOllama()
    f.chat_reply = reply("Neural nets learn ", "from examples. ", "They adjust weights slowly.")
    return f


@pytest.fixture
def kiosk(tmp_path: Path, fake: FakeOllama) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def start(transport: httpx.AsyncBaseTransport | None = None, **over: Any) -> TestClient:
        settings = make_settings(tmp_path, **over)
        client = OllamaClient.from_settings(settings, transport or fake.transport)
        tc = TestClient(create_app(settings, client))
        tc.__enter__()
        clients.append(tc)
        return tc

    yield start
    for tc in clients:
        tc.__exit__(None, None, None)


def recv(ws: Any, until: str, limit: int = 40) -> list[Event]:
    """Events up to and including the first of type `until` (pings skipped)."""
    out: list[Event] = []
    for _ in range(limit):
        e = ws.receive_json()
        if e["type"] == "ping":
            continue
        out.append(e)
        if e["type"] == until:
            return out
    raise AssertionError(f"no {until!r} in {[e['type'] for e in out]}")


def wait_backend(ws: Any, ok: bool, hello: Event | None = None) -> None:
    """The backend check may run before or after the screen connects."""
    if hello is not None and hello["backend_ok"] is ok:
        return
    assert recv(ws, "backend")[-1] == {"type": "backend", "ok": ok}


def types(events: list[Event]) -> list[str]:
    return [e["type"] for e in events]


def ask(ws: Any, text: str, cid: str = "m1") -> list[Event]:
    ws.send_json({"type": "message", "text": text, "client_msg_id": cid})
    return recv(ws, "turn_end")


# -- page + hello ----------------------------------------------------------------------------


def test_page_served_with_boot_copy_and_strict_csp(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    r = tc.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/ui/"
    r = tc.get("/ui/")
    assert r.status_code == 200
    assert '<script type="application/json" id="boot">' in r.text
    assert "<!--BOOT-->" not in r.text
    boot = json.loads(r.text.split('id="boot">')[1].split("</script>")[0])
    assert boot["bot_name"] == "Ada"
    assert "Ada is an AI and can make mistakes" in boot["ui_strings"]["notice"]
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp
    for asset in (
        "kiosk.js",
        "blob.js",
        "kiosk.css",
        "cards.js",
        "cards.css",
        "vendor/qrcode.mjs",
        "vendor/three.module.min.js",
        "fonts/inter-latin-500-normal.woff2",
    ):
        assert tc.get(f"/ui/{asset}").status_code == 200, asset
    h = tc.get("/healthz").json()
    assert h["ok"] is True and h["kill_switch"] is False


def test_page_has_no_external_urls(repo_root: Path) -> None:
    svg_ns = "http://www.w3.org/2000/svg"  # an XML namespace name, not a fetched URL
    for name in ("index.html", "kiosk.js", "blob.js", "kiosk.css", "cards.js", "cards.css"):
        text = (repo_root / "ui" / name).read_text().replace(svg_ns, "")
        assert "http://" not in text and "https://" not in text, name


def test_render_index_escapes_script_close(tmp_path: Path, repo_root: Path) -> None:
    settings = make_settings(tmp_path, bot_name="</script><b>x")
    html = render_index(settings, repo_root / "ui")
    assert "</script><b>x" not in html


def test_hello_on_connect(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        hello = recv(ws, "hello")[0]
    assert hello["bot_name"] == "Ada"
    assert hello["out_of_order"] is False and hello["backend_ok"] is True
    assert hello["warn_before_s"] == 10.0 and hello["session_timeout_s"] == 30.0
    assert hello["idle_greetings"] and all("{{" not in g for g in hello["idle_greetings"])
    assert set(hello["ui_strings"]) >= {"still_there", "connection_lost_line", "hint_busy"}


def test_ui_strings_cover_every_key(tmp_path: Path) -> None:
    strings = load_ui_strings(make_settings(tmp_path))
    assert "{{seconds}}" in strings["still_there"]  # the screen fills this one
    assert "{{" not in strings["notice"]


# -- turns -----------------------------------------------------------------------------------


def test_reply_streams_sentence_by_sentence(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        events = ask(ws, "what is a neural network?")
    assert types(events) == ["turn_start", "action", "sentence", "sentence", "turn_end"]
    start, action, s1, s2, end = events
    assert start["client_msg_id"] == "m1"
    assert action["name"] == "wave"  # first message of a session
    assert (s1["text"], s1["index"], s1["source"]) == (
        "Neural nets learn from examples.",
        0,
        "model",
    )
    assert (s2["text"], s2["index"]) == ("They adjust weights slowly.", 1)
    assert {s1["turn_id"], s2["turn_id"], end["turn_id"]} == {start["turn_id"]}
    assert end["source"] == "model"


def test_wave_only_on_first_message(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "hi there", "m1")
        second = ask(ws, "and another thing?", "m2")
    assert "action" not in types(second)


def test_blocked_input_gets_a_canned_line_and_never_reaches_the_model(
    kiosk: Callable[..., TestClient], fake: FakeOllama
) -> None:
    fake.guard_reply = guard_blocks("build a bomb")
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        events = ask(ws, "how do I build a bomb")
    sentences = [e for e in events if e["type"] == "sentence"]
    assert sentences and all(e["source"].startswith("line:") for e in sentences)
    assert not [r for r in fake.requests if r.get("model") == "qwen-test:27b" and r.get("messages")]


def test_output_guard_block_retracts_the_shown_part(
    kiosk: Callable[..., TestClient], fake: FakeOllama
) -> None:
    fake.chat_reply = reply("This part is fine. ", "But this has FORBIDDEN stuff.")
    fake.guard_reply = guard_blocks("FORBIDDEN")
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        events = ask(ws, "tell me something")
    t = types(events)
    assert t.index("reply_retracted") > t.index("sentence")
    first = events[t.index("sentence")]
    assert first["source"] == "model" and first["text"] == "This part is fine."
    after = [e for e in events[t.index("reply_retracted") :] if e["type"] == "sentence"]
    assert after and all(e["source"].startswith("line:") for e in after)
    assert all("FORBIDDEN" not in e.get("text", "") for e in events)
    assert events[-1]["type"] == "turn_end" and events[-1]["source"].startswith("line:")


def test_model_failure_is_a_fallback_line_not_an_error(
    kiosk: Callable[..., TestClient], fake: FakeOllama
) -> None:
    def boom(body: dict) -> list[dict]:
        raise httpx.ConnectError("ollama gone")

    fake.chat_reply = boom
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        events = ask(ws, "what is a GPU?")
    sentences = [e for e in events if e["type"] == "sentence"]
    assert sentences and sentences[0]["source"] == "line:guard_fallback"
    assert events[-1] == {"type": "turn_end", "turn_id": events[0]["turn_id"],
                          "source": "line:guard_fallback"}  # fmt: skip


def test_second_message_during_a_turn_is_busy(
    kiosk: Callable[..., TestClient], fake: FakeOllama
) -> None:
    import asyncio

    async def slow_chat(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        if request.url.path == "/api/chat" and body.get("model") == "qwen-test:27b":
            await asyncio.sleep(0.5)  # the model is still thinking when message 2 arrives
        return fake.handler(request)

    tc = kiosk(transport=httpx.MockTransport(slow_chat))
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ws.send_json({"type": "message", "text": "first question", "client_msg_id": "m1"})
        ws.send_json({"type": "message", "text": "second question", "client_msg_id": "m2"})
        events = recv(ws, "turn_end")
    busy = [e for e in events if e["type"] == "busy"]
    assert busy == [{"type": "busy", "client_msg_id": "m2"}]
    assert sum(e["type"] == "turn_start" for e in events) == 1


def test_bad_client_events_are_ignored(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ws.send_text("not json")
        ws.send_json({"type": "nope"})
        ws.send_json({"type": "message", "text": 42})
        ws.send_json({"type": "message", "text": "   "})
        ws.send_json({"type": "pong", "t": 1})
        events = ask(ws, "still alive?")
    assert types(events)[0] == "turn_start"


# -- sessions --------------------------------------------------------------------------------


def test_farewell_says_goodbye_and_resets(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    kiosk_obj = tc.app.state.kiosk
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "what is RAG?")
        sid = kiosk_obj.sid
        events = ask(ws, "bye!", "m2")
        events += recv(ws, "session_reset")
    assert [e["source"] for e in events if e["type"] == "sentence"] == ["line:goodbye"] * len(
        [e for e in events if e["type"] == "sentence"]
    )
    assert events[-1] == {"type": "session_reset", "reason": "farewell"}
    assert kiosk_obj.sid is None and kiosk_obj.sessions.peek(sid) is None


def test_new_visitor_gets_fresh_context(kiosk: Callable[..., TestClient], fake: FakeOllama) -> None:
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "my secret word is PINEAPPLE")
        ws.send_json({"type": "reset", "immediate": True})
        recv(ws, "session_reset")
        events = ask(ws, "what was my secret word?", "m2")
    assert "action" in types(events)  # a new session waves again
    chat_calls = [
        r for r in fake.requests if r.get("model") == "qwen-test:27b" and r.get("messages")
    ]
    last = json.dumps(chat_calls[-1]["messages"])
    assert "PINEAPPLE" not in last


def test_idle_timeout_warns_then_says_goodbye_then_resets(
    kiosk: Callable[..., TestClient],
) -> None:
    tc = kiosk(session_timeout_s=0.8, kiosk_warn_before_s=0.5)
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "hello there")
        events = recv(ws, "session_reset")
    t = types(events)
    assert t[0] == "session_warning" and events[0]["seconds_left"] == 1
    assert t[1:] == ["turn_start", *["sentence"] * (len(t) - 4), "turn_end", "session_reset"]
    assert all(e["source"] == "line:goodbye" for e in events if e["type"] == "sentence")
    assert events[-1]["reason"] == "timeout"
    assert tc.app.state.kiosk.sid is None


def test_no_timeout_goodbye_without_a_visitor(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk(session_timeout_s=0.3, kiosk_warn_before_s=0.2, kiosk_ping_s=0.4)
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        e = ws.receive_json()  # nothing but pings while nobody has talked
    assert e["type"] == "ping"


def test_typing_keeps_the_session_alive(kiosk: Callable[..., TestClient]) -> None:
    import time

    tc = kiosk(session_timeout_s=1.0, kiosk_warn_before_s=0.3)
    k = tc.app.state.kiosk
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "hi")
        sid = k.sid
        for _ in range(4):  # 1.2 s of typing > the 1.0 s timeout
            time.sleep(0.3)
            ws.send_json({"type": "typing"})
        time.sleep(0.05)
        assert k.sid == sid and k.sessions.session(sid) is not None


def test_esc_says_goodbye_and_esc_twice_skips_it(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "hi")
        ws.send_json({"type": "reset", "immediate": False})
        polite = recv(ws, "session_reset")
        ask(ws, "hi again", "m2")
        ws.send_json({"type": "reset", "immediate": True})
        quick = recv(ws, "session_reset")
    assert "sentence" in types(polite) and polite[-1]["reason"] == "visitor"
    assert types(quick) == ["session_reset"] and quick[0]["reason"] == "visitor"


def test_reset_without_a_session_is_immediate(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ws.send_json({"type": "reset", "immediate": False})
        assert recv(ws, "session_reset") == [{"type": "session_reset", "reason": "visitor"}]


# -- kill switch -----------------------------------------------------------------------------


def test_kill_switch_drives_out_of_order_live(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    k = tc.app.state.kiosk
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "hi")
        assert k.sid is not None
        k.built.kill_switch.set(True, "test", "pytest")
        on = recv(ws, "out_of_order")[-1]
        assert on["on"] is True and on["line"]
        assert k.sid is None  # the next visitor starts fresh
        ws.send_json({"type": "message", "text": "are you there?", "client_msg_id": "m2"})
        again = recv(ws, "out_of_order")[-1]
        assert again["on"] is True  # no turn, no model call
        k.built.kill_switch.set(False, "", "pytest")
        off = recv(ws, "out_of_order")[-1]
        assert off == {"type": "out_of_order", "on": False}
        assert types(ask(ws, "back?", "m3"))[0] == "turn_start"


def test_hello_reports_kill_switch_on(kiosk: Callable[..., TestClient], tmp_path: Path) -> None:
    tc = kiosk()
    tc.app.state.kiosk.built.kill_switch.set(True, "maintenance", "pytest")
    with tc.websocket_connect("/ws") as ws:
        hello = recv(ws, "hello")[0]
    assert hello["out_of_order"] is True and hello["out_of_order_line"]


def test_admin_endpoint_flips_the_live_screen(
    kiosk: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADA_ADMIN_TOKEN", "x" * 32)
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        r = tc.post("/admin/kill-switch", json={"on": True, "reason": "demo"},
                    headers={"Authorization": "Bearer " + "x" * 32})  # fmt: skip
        assert r.status_code == 200
        assert recv(ws, "out_of_order")[-1]["on"] is True
    assert tc.post("/admin/kill-switch", json={"on": False}).status_code == 401


# -- failures --------------------------------------------------------------------------------


def test_ollama_down_shows_error_state_and_never_crashes(kiosk: Callable[..., TestClient]) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    tc = kiosk(transport=httpx.MockTransport(down))
    with tc.websocket_connect("/ws") as ws:
        wait_backend(ws, False, recv(ws, "hello")[0])
        events = ask(ws, "anyone home?")
    sentences = [e for e in events if e["type"] == "sentence"]
    assert sentences and all(e["source"].startswith("line:") for e in sentences)
    assert tc.get("/healthz").json()["ollama"] is False


def test_ollama_coming_back_is_announced(
    kiosk: Callable[..., TestClient], fake: FakeOllama
) -> None:
    up = {"ok": False}

    def flaky(request: httpx.Request) -> httpx.Response:
        if not up["ok"]:
            raise httpx.ConnectError("connection refused")
        return fake.handler(request)

    tc = kiosk(transport=httpx.MockTransport(flaky))
    with tc.websocket_connect("/ws") as ws:
        wait_backend(ws, False, recv(ws, "hello")[0])
        up["ok"] = True
        wait_backend(ws, True)


def test_a_newer_screen_takes_over(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    with tc.websocket_connect("/ws") as old:
        recv(old, "hello")
        with tc.websocket_connect("/ws") as new:
            recv(new, "hello")
            with pytest.raises(WebSocketDisconnect) as exc:
                while True:
                    old.receive_json()
            assert exc.value.code == CLOSE_REPLACED
            assert types(ask(new, "hi"))[0] == "turn_start"


def test_cross_origin_socket_is_refused(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        tc.websocket_connect("/ws", headers={"origin": "http://evil.example"}) as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 1008


def test_admin_not_mounted_on_a_network_bind(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk(kiosk_host="0.0.0.0")
    assert tc.get("/admin/health").status_code == 404


def test_model_error_mid_reply_retracts_the_half_reply(
    kiosk: Callable[..., TestClient], fake: FakeOllama
) -> None:
    fake.chat_reply = lambda body: [
        {"message": {"role": "assistant", "content": "Half an answer here. "}, "done": False},
        {"message": {"role": "assistant", "content": "And then"}, "done": False},
        {"error": "model crashed"},
    ]
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        events = ask(ws, "explain transformers")
    t = types(events)
    assert "reply_retracted" in t
    assert events[t.index("reply_retracted") + 1]["source"] == "line:guard_fallback"


async def test_fanout_forwards_source_and_retract() -> None:
    from app.sinks import CollectingSink, FanoutSink

    class Rich(CollectingSink):
        def __init__(self) -> None:
            super().__init__()
            self.log: list[str] = []

        async def on_sourced_sentence(self, session_id: str, sentence: str, source: str) -> None:
            self.log.append(f"{source}:{sentence}")

        async def on_retract(self, session_id: str) -> None:
            self.log.append("retract")

    plain, rich = CollectingSink(), Rich()
    fan = FanoutSink(plain, rich)
    await fan.on_sourced_sentence("s", "Hi.", "model")
    await fan.on_retract("s")
    assert plain.sentences() == ["Hi."]  # plain sinks (TTS later) still get on_sentence
    assert rich.log == ["model:Hi.", "retract"]


@pytest.mark.gb10
def test_kiosk_answers_through_the_real_pipeline(tmp_path: Path) -> None:
    """GB10 only: one real question over the websocket, real guards + model."""
    from app.config import load_settings

    settings = load_settings().model_copy(
        update={"kill_switch_file": str(tmp_path / "kill.json"), "transcripts_enabled": False}
    )
    with TestClient(create_app(settings, warm=False)) as tc, tc.websocket_connect("/ws") as ws:
        hello = recv(ws, "hello")[0]
        assert hello["backend_ok"] is True
        ws.send_json({"type": "message", "text": "Hi! What is a neural network?",
                      "client_msg_id": "m1"})  # fmt: skip
        events = recv(ws, "turn_end", limit=200)
    sentences = [e for e in events if e["type"] == "sentence"]
    assert sentences, types(events)
    assert events[-1]["source"] == "model", events


# -- M5a: idle acts + voice hooks ------------------------------------------------------------

IDLE_YAML = (
    "rotation: {min_gap_s: 10, max_gap_s: 10, default_act: breathing}\nacts:\n"
    "  - {id: breathing, duration_s: 0, weight: 0}\n"
    '  - {id: hello_wave, duration_s: 4, weight: 1, caption: "Hi! I\'m {{bot_name}}."}\n'
    "  - {id: spin_on_axis, duration_s: 3, weight: 1, reduced_motion: skip}\n"
)


class FakeScreen:
    """Stands in for the websocket: records what the kiosk sends."""

    def __init__(self) -> None:
        self.sent: list[Event] = []

    async def send_json(self, event: Event) -> None:
        self.sent.append(event)

    def acts(self) -> list[Event]:
        return [e for e in self.sent if e["type"] == "idle_act"]


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def idle_kiosk(tmp_path: Path, fake: FakeOllama) -> Callable[..., Any]:
    def make(**over: Any) -> tuple[Any, FakeScreen, Clock]:
        path = tmp_path / "idle_acts.yaml"
        path.write_text(IDLE_YAML, encoding="utf-8")
        settings = make_settings(tmp_path, kiosk_idle_acts_file=str(path), **over)
        app = create_app(
            settings, OllamaClient.from_settings(settings, fake.transport), background=False
        )
        k = app.state.kiosk
        clock = Clock()
        k.clock = clock
        screen = FakeScreen()
        k.ws = screen
        return k, screen, clock

    return make


def advance(k: Any, clock: Clock, seconds: float, step: float = 0.5) -> None:
    async def go() -> None:
        end = clock.t + seconds
        while clock.t < end:
            clock.t += step
            await k.idle_step()

    asyncio.run(go())


def test_idle_acts_play_only_when_nobody_is_there(idle_kiosk: Callable[..., Any]) -> None:
    k, screen, clock = idle_kiosk()
    advance(k, clock, 9)
    assert screen.acts() == []  # first act waits one full gap
    advance(k, clock, 2)
    (first,) = screen.acts()
    assert first["id"] in {"hello_wave", "spin_on_axis"}
    if first["id"] == "hello_wave":
        assert first["caption"] == "Hi! I'm Ada."
    advance(k, clock, 60)
    ids = [e["id"] for e in screen.acts()]
    assert len(ids) >= 3 and all(a != b for a, b in itertools.pairwise(ids))


@pytest.mark.parametrize("busy", ["session", "turn", "kill", "backend", "no_screen"])
def test_no_idle_acts_while_busy_or_blocked(idle_kiosk: Callable[..., Any], busy: str) -> None:
    k, screen, clock = idle_kiosk()
    if busy == "session":
        k.sid = k.sessions.create()
    elif busy == "turn":
        k.turn = object()
    elif busy == "kill":
        k.status.kill_on = True
    elif busy == "backend":
        k.status.backend_ok = False
    else:
        k.ws = None
    advance(k, clock, 60)
    assert screen.acts() == []


def test_any_interaction_restarts_the_quiet_clock(idle_kiosk: Callable[..., Any]) -> None:
    k, screen, clock = idle_kiosk()
    advance(k, clock, 9)
    for event in ({"type": "wake", "source": "tap"}, {"type": "typing"}):
        asyncio.run(k.on_client(json.dumps(event)))  # each lands just before an act is due
        advance(k, clock, 9)
        assert screen.acts() == [], event
    advance(k, clock, 2)
    assert len(screen.acts()) == 1


def test_reduced_motion_screen_skips_marked_acts(idle_kiosk: Callable[..., Any]) -> None:
    k, screen, clock = idle_kiosk()
    asyncio.run(k.on_client(json.dumps({"type": "screen", "reduced_motion": True})))
    advance(k, clock, 200)
    assert screen.acts() and {e["id"] for e in screen.acts()} == {"hello_wave"}
    # hello_wave twice in a row is never allowed, so it alternates with breathing (waits)
    ids = [e["id"] for e in screen.acts()]
    assert len(ids) == 1


def test_idle_acts_can_be_turned_off(idle_kiosk: Callable[..., Any]) -> None:
    k, screen, clock = idle_kiosk(kiosk_idle_enabled=False)
    assert k.idle is None
    advance(k, clock, 60)
    assert screen.acts() == []
    assert k.hello()["idle_acts"] is False


def test_real_idle_file_loads_at_startup(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        hello = recv(ws, "hello")[0]
    assert hello["idle_acts"] is True
    assert set(hello["ui_strings"]) >= {"talk_idle", "rest_title", "rest_body", "mic_off"}
    assert hello["ui_strings"]["rest_title"] == "Ada is resting"


def test_voice_hooks_emit_what_the_blob_understands(idle_kiosk: Callable[..., Any]) -> None:
    k, screen, clock = idle_kiosk()

    async def go() -> None:
        await k.audio_level("tts", 1.7)
        await k.audio_level("mic", float("nan"))
        await k.wake("word")
        with pytest.raises(ValueError):
            await k.audio_level("speaker", 0.5)

    asyncio.run(go())
    assert screen.sent == [
        {"type": "audio_level", "source": "tts", "level": 1.0},
        {"type": "audio_level", "source": "mic", "level": 0.0},
        {"type": "wake", "source": "word"},
    ]
    advance(k, clock, 9.5)  # the wake word restarted the quiet clock too
    assert screen.acts() == []


def test_state_events_for_a_full_visit(kiosk: Callable[..., TestClient]) -> None:
    """The event sequence the screen turns into blob states (no new events needed)."""
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ws.send_json({"type": "wake", "source": "tap"})  # page: waking -> listening
        ws.send_json({"type": "typing"})
        events = ask(ws, "what is a neural network?")  # thinking -> speaking
        ws.send_json({"type": "message", "text": "bye", "client_msg_id": "m2"})
        bye = recv(ws, "session_reset")  # goodbye plays, then idle
    assert types(events)[0] == "turn_start" and types(events)[-1] == "turn_end"
    assert "sentence" in types(events)
    assert types(bye)[0] == "turn_start" and "sentence" in types(bye)
    assert bye[-1] == {"type": "session_reset", "reason": "farewell"}


# -- M5b: cards through the kiosk (server half) ----------------------------------------------

CARDS_YAML = """
defaults: {confirm_after_s: 0.3, retry_after_s: 0.3, auto_hide_s: 3,
           placeholder_url: "https://ai.sfsu.edu/ai-students"}
cards:
  - id: qr_discord
    type: qr
    enabled: true
    approved: yes
    placeholder: true
    placeholder_note: "Discord invite coming soon"
    title: "Join the Discord"
    url: "https://ai.sfsu.edu/ai-students"
    say_before: "I've put the Discord QR code on the screen."
    confirm: "Did you scan it?"
    on_yes: "Nice, see you in there!"
    on_no: "No rush, take your time."
    on_no_or_timeout: "No rush, it'll stay up for a bit."
    triggers: [discord]
  - id: compute_rules
    type: checklist
    enabled: true
    approved: yes
    title: "Computer rules"
    items: ["No remote access"]
"""
TAGGED = reply("You can join through our Discord. ", 'I put a QR code up. <card id="qr_discord"/>')


@pytest.fixture
def card_kiosk(
    kiosk: Callable[..., TestClient], fake: FakeOllama, tmp_path: Path
) -> Callable[[], TestClient]:
    path = tmp_path / "cards.yaml"
    path.write_text(CARDS_YAML, encoding="utf-8")
    fake.chat_reply = TAGGED
    return lambda: kiosk(cards_file=str(path))


def chat_calls(fake: FakeOllama) -> int:
    return sum(
        1 for r in fake.requests if r["path"] == "/api/chat" and r.get("model") == "qwen-test:27b"
    )


def test_tag_shows_the_card_and_never_reaches_the_screen(card_kiosk: Any, fake: FakeOllama) -> None:
    tc = card_kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        events = ask(ws, "how do I join the discord?")
        texts = [e["text"] for e in events if e["type"] == "sentence"]
        assert texts and not any("<card" in t or "card id" in t for t in texts)
        show = next(e for e in events if e["type"] == "show_card")
        assert show["id"] == "qr_discord"
        assert show["data"]["placeholder_note"] == "Discord invite coming soon"
        assert show["data"]["url"] == "https://ai.sfsu.edu/ai-students"
        assert "say_before" not in show["data"] and "triggers" not in show["data"]
        # shown by the model's tag: the model already pointed at it, no extra intro line
        assert not any(e["type"] == "sentence" and "Discord QR" in e["text"] for e in events)
        ask_ev = recv(ws, "say")  # ~0.3 s later Ada asks, outside any turn
        assert {"type": "card_state", "id": "qr_discord", "state": "asking",
                "text": "Did you scan it?"} in ask_ev  # fmt: skip
        assert ask_ev[-1] == {
            "type": "say",
            "text": "Did you scan it?",
            "source": "card:qr_discord",
        }
    # the output guard never saw the tag either
    guard_texts = [str(r["messages"]) for r in fake.requests
                   if r["path"] == "/api/chat" and r.get("model") == "guard-test:1b"]  # fmt: skip
    assert guard_texts and not any("<card" in t for t in guard_texts)


def test_typed_yes_answers_the_card_without_the_model(card_kiosk: Any, fake: FakeOllama) -> None:
    tc = card_kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "how do I join the discord?")
        recv(ws, "say")  # "Did you scan it?"
        before = chat_calls(fake)
        events = ask(ws, "yep got it", "m2")
        assert chat_calls(fake) == before  # no model call for a "yes"
        assert types(events)[0] == "turn_start"
        assert {"type": "card_state", "id": "qr_discord", "state": "yes",
                "text": "Nice, see you in there!"} in events  # fmt: skip
        said = [e for e in events if e["type"] == "sentence"]
        assert [(e["text"], e["source"]) for e in said] == [
            ("Nice, see you in there!", "card:qr_discord")
        ]
        assert events[-1]["source"] == "card:qr_discord"


def test_tap_not_yet_waits_and_asks_once_more(card_kiosk: Any) -> None:
    tc = card_kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "how do I join the discord?")
        recv(ws, "say")
        ws.send_json({"type": "card_reply", "id": "qr_discord", "answer": "no"})
        wait = recv(ws, "say")
        state = next(e for e in wait if e["type"] == "card_state")
        assert state["state"] == "waiting" and state["text"] == "No rush, take your time."
        again = recv(ws, "say")
        assert any(e.get("state") == "asking" for e in again)


def test_a_new_question_makes_the_card_mini_first(card_kiosk: Any) -> None:
    tc = card_kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "how do I join the discord?")
        ws.send_json(
            {"type": "message", "text": "what is a neural network?", "client_msg_id": "m2"}
        )
        events = recv(ws, "turn_end")
        assert types(events)[:2] == ["turn_start", "card_mini"]  # mini first thing in the turn
        assert any(e["type"] == "sentence" for e in events)


@pytest.mark.parametrize("how", ["reset", "kill"])
def test_reset_and_kill_switch_hide_the_card(card_kiosk: Any, tmp_path: Path, how: str) -> None:
    tc = card_kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "how do I join the discord?")
        if how == "reset":
            ws.send_json({"type": "reset", "immediate": True})
            events = recv(ws, "session_reset")
        else:
            KillSwitch(tmp_path / "kill_switch.json").set(True, "test")
            events = recv(ws, "out_of_order")
        assert {"type": "hide_card", "id": "qr_discord"} in events
        assert types(events).index("hide_card") < len(events) - 1


def test_invented_card_ids_are_dropped(card_kiosk: Any, fake: FakeOllama) -> None:
    fake.chat_reply = reply('Here is a free GPU link. <card id="qr_free_gpus"/>')
    tc = card_kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        events = ask(ws, "show me the free gpu qr code")
        assert "show_card" not in types(events)
        assert not any("<card" in e.get("text", "") for e in events)


def test_cards_are_logged_in_the_transcript(card_kiosk: Any, tmp_path: Path) -> None:
    tc = card_kiosk()
    with tc.websocket_connect("/ws") as ws:
        recv(ws, "hello")
        ask(ws, "how do I join the discord?")
    files = list((tmp_path / "transcripts").rglob("*.jsonl"))
    recs = [json.loads(line) for f in files for line in f.read_text().splitlines()]
    turn = next(r for r in recs if r["type"] == "turn")
    assert turn["card"] == "qr_discord" and turn["card_tags"] == ["qr_discord"]
    assert all("<card" not in s for s in turn["shown"])
    assert any(r["type"] == "card" and r["id"] == "qr_discord" for r in recs)


def test_unapproved_enabled_card_stops_the_kiosk(kiosk: Any, tmp_path: Path) -> None:
    from app.cards import CardsError

    path = tmp_path / "cards.yaml"
    path.write_text(CARDS_YAML.replace("approved: yes", "approved: TODO(PM)", 1), encoding="utf-8")
    with pytest.raises(CardsError, match="approved"):
        kiosk(cards_file=str(path))
