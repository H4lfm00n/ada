"""M6b: Ada hears (tap-to-talk). VAD and STT faked; the real run is `make voice-probe`."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.config import Settings
from app.voice.stt import FRAME, Capture

from .test_server import fake, kiosk  # noqa: F401  (fixtures)

MODELS = Path(__file__).resolve().parent.parent / "data" / "voice"


class LoudVad:
    """Speech = any frame louder than 0.05."""

    def reset(self) -> None:
        pass

    def prob(self, frame: np.ndarray) -> float:
        return 0.95 if float(np.abs(frame).max()) > 0.05 else 0.02


class FakeStt:
    def __init__(self, text: str = "what is this lab about?") -> None:
        self.text = text
        self.heard: list[float] = []

    def transcribe(self, audio: np.ndarray) -> str:
        self.heard.append(len(audio) / 16000)
        return self.text


def s(**over: Any) -> Settings:
    base: dict[str, Any] = {"chat_model": "c", "guard_model": "g", "embed_model": "e"}
    return Settings(**{**base, **over})


def pcm(seconds: float, amp: float) -> bytes:
    n = int(seconds * 16000)
    return (np.full(n, amp) * 32767).astype("<i2").tobytes()


def feed(cap: Capture, data: bytes, chunk: int = 1024) -> str | None:
    for i in range(0, len(data), chunk):
        end = cap.feed(data[i : i + chunk])
        if end:
            return end
    return None


def test_capture_ends_after_the_silence_gap_and_trims() -> None:
    cap = Capture(LoudVad(), s(stt_end_silence_ms=700))  # type: ignore[arg-type]
    assert feed(cap, pcm(1.0, 0.0)) is None  # waiting for speech
    assert feed(cap, pcm(1.5, 0.3)) is None  # talking
    assert feed(cap, pcm(0.5, 0.0)) is None  # a pause shorter than 700 ms
    assert feed(cap, pcm(0.4, 0.0)) == "end"
    audio = cap.audio()
    assert 1.5 <= len(audio) / 16000 <= 1.5 + 0.3 + 0.25 + 2 * FRAME / 16000
    assert cap.has_speech


def test_capture_gives_up_without_speech_and_caps_length() -> None:
    cap = Capture(LoudVad(), s(stt_no_speech_s=2.0))  # type: ignore[arg-type]
    assert feed(cap, pcm(2.1, 0.0)) == "no_speech"
    cap = Capture(LoudVad(), s(stt_max_utterance_s=3.0))  # type: ignore[arg-type]
    assert feed(cap, pcm(3.2, 0.3)) == "end"
    cap = Capture(LoudVad(), s(stt_min_speech_ms=300, stt_no_speech_s=2.0))  # type: ignore[arg-type]
    assert feed(cap, pcm(0.1, 0.3) + pcm(2.0, 0.0)) == "no_speech"  # a click is not speech


def _events(ws: Any, until: str, limit: int = 80) -> list[dict]:
    out = []
    for _ in range(limit):
        m = ws.receive()
        if m.get("bytes") is not None:
            continue
        e = json.loads(m["text"])
        if e["type"] == "ping":
            continue
        out.append(e)
        if e["type"] == until:
            return out
    raise AssertionError(f"no {until!r} in {[e['type'] for e in out]}")


@pytest.fixture
def voice_kiosk(kiosk: Any, monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: F811
    stt = FakeStt()
    monkeypatch.setattr("app.voice.stt.SileroVad", lambda _p: LoudVad())
    monkeypatch.setattr("app.voice.stt.make_stt", lambda _s: stt)

    def start(**over: Any) -> Any:
        tc = kiosk(stt_enabled=True, output_mode="spoken", **over)
        k = tc.app.state.kiosk
        for _ in range(100):
            if k.stt is not None:
                break
            time.sleep(0.02)
        return tc, stt

    return start


def test_spoken_question_goes_through_the_guarded_pipeline(voice_kiosk: Any) -> None:
    tc, stt = voice_kiosk()
    with tc.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["voice"]["stt"] is True and hello["voice"]["wake_word"] is False
        assert hello["ui_strings"]["talk_idle_tap"]  # tap-only copy until the wake word
        ws.send_json({"type": "mic_start"})
        assert _events(ws, "mic")[-1] == {"type": "mic", "on": True}
        ws.send_bytes(pcm(0.5, 0.0) + pcm(1.2, 0.3))
        ws.send_bytes(pcm(1.0, 0.0))
        ev = _events(ws, "turn_end")
        assert {"type": "mic", "on": False, "reason": "end"} in ev
        assert {"type": "heard", "text": "what is this lab about?"} in ev
        assert [e for e in ev if e["type"] == "sentence"]
        assert stt.heard and 1.2 <= stt.heard[0] < 2.0
        # audio frames after the capture closed are dropped
        ws.send_bytes(pcm(0.5, 0.3))
        assert tc.app.state.kiosk.capture is None


def test_tap_with_nothing_said_and_mic_during_a_turn(voice_kiosk: Any) -> None:
    tc, _ = voice_kiosk(stt_no_speech_s=1.0)
    with tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "mic_start"})
        _events(ws, "mic")
        ws.send_bytes(pcm(1.1, 0.0))
        assert _events(ws, "mic")[-1] == {"type": "mic", "on": False, "reason": "no_speech"}
        ws.send_json({"type": "mic_start"})
        _events(ws, "mic")
        ws.send_json({"type": "mic_stop"})  # tapped again before saying anything
        assert _events(ws, "mic")[-1]["reason"] == "cancel"
        ws.send_json({"type": "message", "text": "hi", "client_msg_id": "m1"})
        ws.send_json({"type": "mic_start"})  # half-duplex: not while Ada answers
        ev = _events(ws, "turn_end")
        assert {"type": "mic", "on": False, "reason": "busy"} in ev


def test_empty_transcript_asks_again(voice_kiosk: Any) -> None:
    tc, stt = voice_kiosk()
    stt.text = ""
    with tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "mic_start"})
        _events(ws, "mic")
        ws.send_bytes(pcm(1.0, 0.3) + pcm(1.0, 0.0))
        ev = _events(ws, "turn_end")
        assert ev[-1]["source"] == "line:didnt_catch"


def test_no_stt_means_typing(kiosk: Any) -> None:  # noqa: F811
    tc = kiosk()
    with tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "mic_start"})
        assert _events(ws, "mic")[-1] == {"type": "mic", "on": False, "reason": "unavailable"}


@pytest.mark.skipif(not (MODELS / "silero_vad.onnx").exists(), reason="voice models not set up")
def test_real_silero_hears_kokoro_and_not_silence() -> None:
    from app.voice.stt import SileroVad

    vad = SileroVad(MODELS / "silero_vad.onnx")
    silence = [vad.prob(np.zeros(FRAME, dtype=np.float32)) for _ in range(20)]
    assert max(silence) < 0.2
    if not (MODELS / "kokoro-v1.0.onnx").exists():
        return
    from app.voice.probe import resample
    from app.voice.tts import KokoroTts

    k = KokoroTts(s(tts_enabled=True))
    a = resample(k.synth("What is this lab about?"), k.sample_rate)
    vad.reset()
    probs = [vad.prob(a[i : i + FRAME]) for i in range(0, len(a) - FRAME + 1, FRAME)]
    assert sum(p > 0.5 for p in probs) * FRAME / 16000 > 0.5


def test_spoken_yes_answers_the_card_not_the_model(
    kiosk: Any,  # noqa: F811
    fake: Any,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .test_server import CARDS_YAML, TAGGED, chat_calls

    stt = FakeStt("how do I join the discord?")
    monkeypatch.setattr("app.voice.stt.SileroVad", lambda _p: LoudVad())
    monkeypatch.setattr("app.voice.stt.make_stt", lambda _s: stt)
    path = tmp_path / "cards.yaml"
    path.write_text(CARDS_YAML, encoding="utf-8")
    fake.chat_reply = TAGGED
    tc = kiosk(cards_file=str(path), stt_enabled=True, output_mode="spoken")
    k = tc.app.state.kiosk
    for _ in range(100):
        if k.stt is not None:
            break
        time.sleep(0.02)

    def say_it(ws: Any) -> list[dict]:
        ws.send_json({"type": "mic_start"})
        _events(ws, "mic")
        ws.send_bytes(pcm(1.0, 0.3) + pcm(1.0, 0.0))
        return _events(ws, "turn_end")

    with tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ev = say_it(ws)
        assert any(e["type"] == "show_card" for e in ev)
        _events(ws, "say")  # "Did you scan it?"
        assert k.cards.listening
        calls = chat_calls(fake)
        stt.text = "Yeah, got it."
        ev = say_it(ws)
        assert any(e["type"] == "card_state" and e["state"] == "yes" for e in ev)
        assert ev[-1]["source"] == "card:qr_discord"
        assert chat_calls(fake) == calls  # the model never saw the "yes"
