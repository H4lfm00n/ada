"""M6a: Ada speaks. Engine faked; the real Kokoro run is `make voice-probe` on the GB10."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.config import Settings
from app.voice.speaker import Speaker, pack_frame, unpack_frame
from app.voice.tts import speech_text, split_parts

from .test_server import fake, kiosk  # noqa: F401  (fixtures)

MODELS = Path(__file__).resolve().parent.parent / "data" / "voice"


class FakeTts:
    sample_rate = 24000

    def __init__(self, delay: float = 0.0) -> None:
        self.said: list[str] = []
        self.delay = delay

    def synth(self, text: str) -> np.ndarray:
        if self.delay:
            time.sleep(self.delay)
        self.said.append(text)
        return np.full(2400 * max(1, len(text.split())), 0.1, dtype=np.float32)


def settings(**over: Any) -> Settings:
    base: dict[str, Any] = {"chat_model": "c", "guard_model": "g", "embed_model": "e"}
    return Settings(**{**base, **over})


def test_tts_turns_on_spoken_mode() -> None:
    assert settings().output_mode == "screen"
    assert settings(tts_enabled=True).output_mode == "spoken"


def test_speech_text_never_reads_links_or_emails() -> None:
    t = speech_text("Email **ai@sfsu.edu** or see https://ai.sfsu.edu/x today.", {})
    assert "@" not in t and "http" not in t and "**" not in t
    assert "the email on the screen" in t and "the link on the screen" in t
    assert speech_text("Hi, I'm Ada.", {"Ada": "Ayy-da"}) == "Hi, I'm Ayy-da."


def test_long_first_sentence_splits_at_a_comma_and_word_counts_add_up() -> None:
    text = "Hi there, I'm Ada, and I help visitors find their way around the AI Lab."
    parts = split_parts(text, {}, first=True, first_part_words=9)
    assert len(parts) == 2 and parts[0].caption.endswith(",")
    assert sum(p.words for p in parts) == len(text.split())
    assert len(split_parts(text, {}, first=False, first_part_words=9)) == 1
    assert len(split_parts("Short and sweet.", {}, first=True, first_part_words=9)) == 1


def test_frame_roundtrip_keeps_pcm_aligned() -> None:
    frame = pack_frame({"type": "tts", "ref": "t1:0"}, b"\x01\x00\x02\x00")
    header, pcm = unpack_frame(frame)
    assert header["ref"] == "t1:0" and pcm == b"\x01\x00\x02\x00"
    (n,) = np.frombuffer(frame[:4], dtype="<u4")
    assert (4 + n) % 2 == 0


async def test_speaker_keeps_order_and_flush_drops_stale_audio() -> None:
    frames: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []

    async def send_bytes(b: bytes) -> None:
        frames.append(unpack_frame(b)[0])

    async def send_json(e: dict[str, Any]) -> None:
        skips.append(e)

    sp = Speaker(settings(tts_enabled=True), send_bytes, send_json)
    await sp.load(lambda _s: FakeTts(delay=0.02))
    sp.start()
    sp.speak("t1:0", "One two three.", first=True)
    sp.speak("t1:1", "Four five.")
    for _ in range(100):
        if len(frames) >= 2:
            break
        await asyncio.sleep(0.01)
    assert [f["ref"] for f in frames] == ["t1:0", "t1:1"]
    assert all(f["last"] and f["sr"] == 24000 for f in frames)
    sp.speak("t2:0", "Never spoken, flushed.")
    sp.flush()
    await asyncio.sleep(0.1)
    assert [f["ref"] for f in frames] == ["t1:0", "t1:1"]
    await sp.stop()


async def test_speaker_failure_is_captions_only() -> None:
    def boom(_s: Settings) -> FakeTts:
        raise FileNotFoundError("data/voice/kokoro-v1.0.onnx missing")

    async def nop(_x: Any) -> None:
        return None

    sp = Speaker(settings(tts_enabled=True), nop, nop)
    await sp.load(boom)
    assert not sp.ready and sp.failed and "missing" in sp.failed


def _recv_mixed(ws: Any, until: str, limit: int = 60) -> tuple[list[dict], list[dict]]:
    events, frames = [], []
    for _ in range(limit):
        m = ws.receive()
        if m.get("bytes") is not None:
            frames.append(unpack_frame(m["bytes"])[0])
            continue
        e = json.loads(m["text"])
        if e["type"] == "ping":
            continue
        events.append(e)
        if e["type"] == until:
            return events, frames
    raise AssertionError(f"no {until!r}")


def test_kiosk_speaks_every_guarded_sentence(kiosk: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    tts = FakeTts()
    monkeypatch.setattr("app.voice.tts.make_tts", lambda _s: tts)
    tc = kiosk(tts_enabled=True)
    with tc.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["output_mode"] == "spoken"
        assert hello["voice"]["tts"] is True and hello["voice"]["volume"] == 0.8
        for _ in range(100):  # the engine loads in the background
            if tc.app.state.kiosk.speaker.ready:
                break
            time.sleep(0.02)
        ws.send_json({"type": "message", "text": "what is a neural net?", "client_msg_id": "m1"})
        events, frames = _recv_mixed(ws, "turn_end")
        sents = [e for e in events if e["type"] == "sentence"]
        assert sents and all(e["tts"] for e in sents)
        want = {e["ref"] for e in sents}
        for _ in range(len(want) * 3):
            if {f["ref"] for f in frames} >= want:
                break
            frames.append(unpack_frame(ws.receive_bytes())[0])
        assert {f["ref"] for f in frames} >= want
        assert tts.said  # every sentence went through the engine, nothing else did
        assert all(s in " ".join(e["text"] for e in sents) for s in tts.said)
        ws.send_json({"type": "tts_playing", "ref": sents[0]["ref"]})  # no voice marks: no-op


@pytest.mark.skipif(not (MODELS / "kokoro-v1.0.onnx").exists(), reason="voice models not set up")
def test_real_kokoro_says_ada_as_ay_duh() -> None:
    from app.voice.tts import AY_DUH, KokoroTts

    k = KokoroTts(settings(tts_enabled=True))
    assert AY_DUH in k.phonemes("Hi, I'm Ada.")  # AY-duh, PHASE2-SPEC 8.2
    audio = k.synth("Hi, I'm Ada.")
    assert audio.dtype == np.float32 and 0.5 < len(audio) / k.sample_rate < 4
