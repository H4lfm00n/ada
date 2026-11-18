"""Voice (M6): text-to-speech out, speech-to-text in. Models run on the GB10's CPU, in memory.

The kiosk browser owns the mic and the speakers (PHASE2-SPEC 1.3a); audio travels over the
kiosk websocket as raw 16-bit PCM. Nothing here writes audio to disk.

Heavy imports (onnxruntime, kokoro_onnx, onnx_asr) happen only when an engine is built, so the
rest of the app and the unit tests never need the voice models.
"""
