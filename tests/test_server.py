"""M5 kiosk server: websocket events, session reset, kill switch, failures. Ollama mocked.

Everything runs through the real pipeline (app.factory.build_pipeline) with the real
guard rules and the real Llama Guard wrapper; only Ollama's HTTP API is faked.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
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
    assert boot["bot_name"] == "Dum-E"
    assert "Dum-E can make mistakes" in boot["ui_strings"]["notice"]
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp
    for asset in (
        "kiosk.js",
        "orb.js",
        "kiosk.css",
        "fonts/atkinson-hyperlegible-latin-400-normal.woff2",
    ):
        assert tc.get(f"/ui/{asset}").status_code == 200, asset
    h = tc.get("/healthz").json()
    assert h["ok"] is True and h["kill_switch"] is False


def test_page_has_no_external_urls(repo_root: Path) -> None:
    for name in ("index.html", "kiosk.js", "orb.js", "kiosk.css"):
        text = (repo_root / "ui" / name).read_text()
        assert "http://" not in text and "https://" not in text, name


def test_render_index_escapes_script_close(tmp_path: Path, repo_root: Path) -> None:
    settings = make_settings(tmp_path, bot_name="</script><b>x")
    html = render_index(settings, repo_root / "ui")
    assert "</script><b>x" not in html


def test_hello_on_connect(kiosk: Callable[..., TestClient]) -> None:
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        hello = recv(ws, "hello")[0]
    assert hello["bot_name"] == "Dum-E"
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
