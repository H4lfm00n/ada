"""Text-to-speech: Kokoro-82M (ONNX, CPU) with Piper as the fallback engine.

Only text the pipeline already approved reaches this module (guarded model sentences and
designer lines), so nothing is spoken that wasn't checked (PHASE2-SPEC 5).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.config import Settings

log = logging.getLogger("app.voice.tts")

URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b[\w.-]+\.(?:com|org|edu|net|io|gg)(?:/\S*)?\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
AY_DUH = "e\u026ad\u0259"  # IPA for "Ada" said AY-duh (PHASE2-SPEC 8.2)
SPLIT_AT = re.compile(r"[,;:]$|^(?:-|\u2013|\u2014)$")


class TtsEngine(Protocol):
    sample_rate: int

    def synth(self, text: str) -> np.ndarray:  # float32 mono, -1..1
        ...


@dataclass(frozen=True, slots=True)
class Part:
    caption: str  # the words as shown on screen (the page lights `words` of them)
    speech: str  # what the engine reads
    words: int


def speech_text(text: str, pronunciations: dict[str, str]) -> str:
    """Caption text -> what the voice says. Never reads a link or an email out loud (1.5)."""
    t = text.replace("**", "")
    t = EMAIL_RE.sub("the email on the screen", t)
    t = URL_RE.sub("the link on the screen", t)
    for word, said in pronunciations.items():
        t = re.sub(rf"\b{re.escape(word)}\b", said, t)
    return t.strip()


def split_parts(
    text: str, pronunciations: dict[str, str], *, first: bool, first_part_words: int
) -> list[Part]:
    """One part per sentence, or two for a long first sentence (earlier first audio)."""
    words = text.split()
    cut = 0
    if first and first_part_words and len(words) > first_part_words:
        for i in range(3, min(len(words) - 3, first_part_words + 4)):
            if SPLIT_AT.search(words[i]):
                cut = i + 1
                break
    chunks = [words[:cut], words[cut:]] if cut else [words]
    out = []
    for c in chunks:
        cap = " ".join(c)
        out.append(Part(cap, speech_text(cap, pronunciations), len(c)))
    kept = [p for p in out if p.speech]
    return kept or [Part(text, speech_text(text, pronunciations), len(words))]


def to_pcm16(audio: np.ndarray) -> bytes:
    a = np.clip(np.asarray(audio, dtype=np.float32).reshape(-1), -1.0, 1.0)
    return (a * 32767.0).astype("<i2").tobytes()


class KokoroTts:
    def __init__(self, settings: Settings) -> None:
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        model = settings.content_path(settings.tts_model_file)
        voices = settings.content_path(settings.tts_voices_file)
        for p in (model, voices):
            if not p.exists():
                raise FileNotFoundError(f"{p} missing: run `make voice-setup`")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = settings.tts_threads
        opts.inter_op_num_threads = 1
        sess = ort.InferenceSession(str(model), opts, providers=["CPUExecutionProvider"])
        self.kokoro = Kokoro.from_session(sess, str(voices))
        if settings.tts_voice not in self.kokoro.get_voices():
            raise ValueError(f"tts_voice {settings.tts_voice!r} is not in {voices.name}")
        self.voice = settings.tts_voice
        self.speed = settings.tts_speed
        self.lang = settings.tts_lang
        self.sample_rate = 24000

    def synth(self, text: str) -> np.ndarray:
        samples, sr = self.kokoro.create(text, voice=self.voice, speed=self.speed, lang=self.lang)
        self.sample_rate = int(sr)
        return np.asarray(samples, dtype=np.float32)

    def phonemes(self, text: str) -> str:
        return str(self.kokoro.tokenizer.phonemize(text, self.lang))


class PiperTts:
    """Fallback (spec 1.2): `uv sync --group piper` + a voice in tts_piper_model."""

    def __init__(self, settings: Settings) -> None:
        from piper import PiperVoice  # type: ignore[import-not-found]

        path = settings.content_path(settings.tts_piper_model)
        if not path.exists():
            raise FileNotFoundError(f"{path} missing: run `make voice-setup ARGS=--piper`")
        self.voice = PiperVoice.load(str(path))
        self.sample_rate = int(self.voice.config.sample_rate)

    def synth(self, text: str) -> np.ndarray:
        chunks = [c.audio_float_array for c in self.voice.synthesize(text)]
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def make_tts(settings: Settings) -> TtsEngine:
    t0 = time.perf_counter()
    kokoro = settings.tts_engine == "kokoro"
    engine: TtsEngine = KokoroTts(settings) if kokoro else PiperTts(settings)
    engine.synth("Hi.")  # warm the session (first call allocates)
    log.info("TTS %s ready in %.1fs", settings.tts_engine, time.perf_counter() - t0)
    return engine
