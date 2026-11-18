"""Download the voice models into data/voice/ (one time, at setup; never at runtime).

    python -m app.voice.setup            # = make voice-setup: Kokoro + Silero VAD + Parakeet
    python -m app.voice.setup --piper    # also the Piper fallback voice
    python -m app.voice.setup --whisper  # also faster-whisper (needs `uv sync --group whisper`)

Idempotent: files already present are skipped. At runtime the kiosk only reads these files
(onnx_asr is pointed at a local folder, so it never goes online).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from app.config import load_settings

KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
SILERO = "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/"


def fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ok      {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  get     {url}")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.rename(dest)
    print(f"  saved   {dest} ({dest.stat().st_size / 1e6:.0f} MB)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--piper", action="store_true")
    ap.add_argument("--whisper", action="store_true")
    args = ap.parse_args(argv)
    s = load_settings()
    print("TTS (Kokoro-82M):")
    fetch(KOKORO + "kokoro-v1.0.onnx", s.content_path(s.tts_model_file))
    fetch(KOKORO + "voices-v1.0.bin", s.content_path(s.tts_voices_file))
    print("VAD (Silero):")
    fetch(SILERO, s.content_path(s.stt_vad_file))
    print("STT (Parakeet TDT 0.6B v3, ONNX via onnx-asr):")
    path = s.content_path(s.stt_model_dir)
    if path.exists():
        print(f"  ok      {path}")
    else:
        import onnx_asr

        onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", str(path),
                            quantization=s.stt_quantization or None)  # fmt: skip
        print(f"  saved   {path}")
    if args.piper:
        print("TTS fallback (Piper):")
        dest = s.content_path(s.tts_piper_model)
        fetch(PIPER + "en_US-lessac-medium.onnx", dest)
        fetch(PIPER + "en_US-lessac-medium.onnx.json", dest.with_suffix(".onnx.json"))
    if args.whisper:
        print("STT fallback (faster-whisper):")
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]

        WhisperModel(s.stt_whisper_model, device="cpu", compute_type="int8",
                     download_root=str(s.content_path("data/voice/whisper")))  # fmt: skip
        print("  ok")
    print("done. Audio is never stored; these are model files only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
