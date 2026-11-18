"""Speech-to-text: Silero VAD end-of-speech + Parakeet TDT 0.6B v3 (faster-whisper fallback).

Tap-to-talk (M6 demo): the page opens the mic on a tap and streams 16 kHz int16 mono. A
`Capture` runs Silero on every 32 ms frame and decides when the visitor is done (700 ms of
silence after real speech), gave up (no speech), or ran too long. Audio lives in memory for
one capture and is dropped after transcription; it is never written to disk (spec 1.4).
The transcript then takes the same guarded path as typed text.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal, Protocol

import numpy as np

from app.config import Settings

log = logging.getLogger("app.voice.stt")

SR = 16000
FRAME = 512  # Silero v5 frame at 16 kHz (32 ms)
CONTEXT = 64
End = Literal["end", "no_speech"]


class SttEngine(Protocol):
    def transcribe(self, audio: np.ndarray) -> str:  # float32 mono 16 kHz
        ...


class SileroVad:
    """Streaming Silero VAD v5 (ONNX, one CPU thread, ~0.2 ms per frame)."""

    def __init__(self, path: Path) -> None:
        import onnxruntime as ort

        if not path.exists():
            raise FileNotFoundError(f"{path} missing: run `make voice-setup`")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.sr = np.array(SR, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(CONTEXT, dtype=np.float32)

    def prob(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame.astype(np.float32)])[None, :]
        out, self.state = self.sess.run(None, {"input": x, "state": self.state, "sr": self.sr})
        self.context = x[0, -CONTEXT:]
        return float(out[0][0])


class Capture:
    """One tap-to-talk utterance: VAD endpointing over the streamed frames, in memory."""

    def __init__(self, vad: SileroVad, s: Settings) -> None:
        vad.reset()
        self.vad = vad
        self.on = s.stt_vad_threshold
        self.off = max(0.05, s.stt_vad_threshold - 0.15)  # hysteresis once speaking
        self.end_silence = int(s.stt_end_silence_ms * SR / 1000)
        self.no_speech = int(s.stt_no_speech_s * SR)
        self.max_len = int(s.stt_max_utterance_s * SR)
        self.min_speech = int(s.stt_min_speech_ms * SR / 1000)
        self.frames: list[np.ndarray] = []
        self.rem = np.zeros(0, dtype=np.float32)
        self.total = 0  # samples processed
        self.voiced = 0  # voiced samples
        self.first_voice: int | None = None
        self.last_voice = 0  # sample index just after the last voiced frame
        self.t_last_voice = time.monotonic()  # when that frame arrived = end of speech
        self.speaking = False
        self.started = time.monotonic()

    @property
    def has_speech(self) -> bool:
        return self.voiced >= self.min_speech

    def feed(self, pcm16: bytes) -> End | None:
        if len(pcm16) % 2:
            pcm16 = pcm16[:-1]
        x = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        self.rem = np.concatenate([self.rem, x]) if self.rem.size else x
        while self.rem.size >= FRAME:
            f, self.rem = self.rem[:FRAME], self.rem[FRAME:]
            self.frames.append(f)
            p = self.vad.prob(f)
            self.total += FRAME
            if p >= (self.off if self.speaking else self.on):
                self.speaking = True
                self.voiced += FRAME
                if self.first_voice is None:
                    self.first_voice = self.total - FRAME
                self.last_voice = self.total
                self.t_last_voice = time.monotonic()
            else:
                self.speaking = False
            if self.has_speech and self.total - self.last_voice >= self.end_silence:
                return "end"
            if not self.has_speech and self.total >= self.no_speech:
                return "no_speech"
            if self.total >= self.max_len:
                return "end" if self.has_speech else "no_speech"
        return None

    def audio(self) -> np.ndarray:
        """The utterance with 300 ms before the first voiced frame and 250 ms after the last."""
        a = np.concatenate(self.frames) if self.frames else np.zeros(0, dtype=np.float32)
        lo = max(0, (self.first_voice or 0) - int(0.3 * SR))
        hi = min(a.size, self.last_voice + int(0.25 * SR))
        return a[lo:hi]


class ParakeetStt:
    def __init__(self, s: Settings) -> None:
        import onnx_asr
        import onnxruntime as ort

        path = s.content_path(s.stt_model_dir)
        if not path.exists():  # an existing folder keeps onnx_asr offline (never downloads)
            raise FileNotFoundError(f"{path} missing: run `make voice-setup`")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = s.stt_threads
        self.model = onnx_asr.load_model(
            "nemo-parakeet-tdt-0.6b-v3",
            str(path),
            quantization=s.stt_quantization or None,
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )

    def transcribe(self, audio: np.ndarray) -> str:
        return str(self.model.recognize(audio.astype(np.float32), sample_rate=SR)).strip()


class WhisperStt:
    """Fallback (spec 1.2): `uv sync --group whisper` + `make voice-setup ARGS=--whisper`."""

    def __init__(self, s: Settings) -> None:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]

        self.model = WhisperModel(
            s.stt_whisper_model,
            device="cpu",
            compute_type="int8",
            cpu_threads=s.stt_threads,
            download_root=str(s.content_path("data/voice/whisper")),
            local_files_only=True,
        )

    def transcribe(self, audio: np.ndarray) -> str:
        segs, _ = self.model.transcribe(audio, language="en", beam_size=1, vad_filter=False)
        return " ".join(seg.text.strip() for seg in segs).strip()


def make_stt(s: Settings) -> SttEngine:
    t0 = time.perf_counter()
    engine: SttEngine = ParakeetStt(s) if s.stt_engine == "parakeet" else WhisperStt(s)
    engine.transcribe(np.zeros(SR // 2, dtype=np.float32))  # warm-up
    log.info("STT %s ready in %.1fs", s.stt_engine, time.perf_counter() - t0)
    return engine
