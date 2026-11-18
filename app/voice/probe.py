"""Voice check on the GB10, no mic needed (= make voice-probe).

TTS: how Kokoro says "Ada" (should be AY-duh in IPA), and synth time per reply sentence
(first-part time is what the visitor waits for). STT (once set up): Ada's own audio is fed
back through Silero VAD + Parakeet, so the transcription time is real, not guessed.
"""

from __future__ import annotations

import statistics
import sys
import time

import numpy as np

from app.config import load_settings
from app.voice.tts import AY_DUH, make_tts, split_parts

SENTENCES = [
    "Hi, I'm Ada, the AI Lab's assistant, and I'm happy to help.",
    "You can find us in LIB two-sixty.",
    "The lab meets every Thursday at five thirty.",
    "I've put a QR code on the screen.",
    "A transformer reads every word at once, and its big trick is called attention.",
]


def resample(audio: np.ndarray, sr: int, to: int = 16000) -> np.ndarray:
    n = int(len(audio) * to / sr)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(
        np.float32
    )


def main() -> int:
    s = load_settings()
    print(f"TTS engine {s.tts_engine}, voice {s.tts_voice}, threads {s.tts_threads}")
    t0 = time.perf_counter()
    tts = make_tts(s)
    print(f"  load + warm-up {time.perf_counter() - t0:.2f}s")
    if hasattr(tts, "phonemes"):
        ph = tts.phonemes("Hi, I'm Ada.")
        print(f"  'Ada' -> {ph}   [{'OK, AY-duh' if AY_DUH in ph else 'CHECK: not AY-duh'}]")
    firsts, rtfs, clips = [], [], []
    for text in SENTENCES:
        parts = split_parts(text, s.tts_pronunciations, first=True,
                            first_part_words=s.tts_first_part_words)  # fmt: skip
        for i, p in enumerate(parts):
            t = time.perf_counter()
            audio = tts.synth(p.speech)
            dt = time.perf_counter() - t
            dur = len(audio) / tts.sample_rate
            rtfs.append(dt / max(dur, 1e-3))
            if i == 0:
                firsts.append(dt)
            clips.append(audio)
            print(f"  {dt:5.2f}s for {dur:4.1f}s audio  | {p.caption}")
    verdict = "PASS" if max(firsts) < 0.4 else "SLOW"
    print(
        f"  first audio ready (synth of the first part): median "
        f"{statistics.median(firsts):.2f}s, max {max(firsts):.2f}s   [{verdict} vs 0.4s]"
    )
    print(f"  real-time factor median {statistics.median(rtfs):.2f} (below 1 = faster than speech)")

    if not s.content_path(s.stt_model_dir).exists():
        print("STT not set up yet (make voice-setup).")
        return 0
    try:
        from app.voice.stt import SileroVad, make_stt
    except ImportError:
        print("STT: not in this build yet (step 2).")
        return 0

    print(f"STT engine {s.stt_engine} ({s.stt_quantization or 'fp32'}), threads {s.stt_threads}")
    t0 = time.perf_counter()
    stt = make_stt(s)
    vad = SileroVad(s.content_path(s.stt_vad_file))
    print(f"  load + warm-up {time.perf_counter() - t0:.2f}s")
    times = []
    for audio in clips:
        a16 = resample(audio, tts.sample_rate)
        t = time.perf_counter()
        probs = [vad.prob(a16[i : i + 512]) for i in range(0, len(a16) - 511, 512)]
        tv = time.perf_counter() - t
        t = time.perf_counter()
        heard = stt.transcribe(a16)
        dt = time.perf_counter() - t
        times.append(dt)
        vad.reset()
        speech = sum(p > s.stt_vad_threshold for p in probs) * 0.032
        print(f"  {dt:5.2f}s stt, vad {tv * 1000:4.0f}ms ({speech:.1f}s speech) | heard: {heard!r}")
    print(f"  transcribe median {statistics.median(times):.2f}s, max {max(times):.2f}s "
          f"[{'PASS' if max(times) < 0.3 else 'SLOW'} vs 0.3s]")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
