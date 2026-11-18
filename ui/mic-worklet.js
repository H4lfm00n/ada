// Mic capture (M6b): 16 kHz int16 mono frames of 512 samples (32 ms) for the server's VAD.
// Runs in the AudioWorklet thread. The page creates its capture context at 16 kHz, so the
// browser resamples properly; the linear resampler below only runs if it couldn't.
// Nothing is kept: each frame is handed to the page, which sends it and drops it.
class AdaMic extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.t = 0;
    this.last = 0;
    this.buf = new Int16Array(512);
    this.n = 0;
    this.sum = 0;
  }
  push(x) {
    const v = x < -1 ? -1 : x > 1 ? 1 : x;
    this.buf[this.n++] = v < 0 ? v * 32768 : v * 32767;
    this.sum += v * v;
    if (this.n === this.buf.length) {
      const out = this.buf;
      this.port.postMessage({ pcm: out.buffer, rms: Math.sqrt(this.sum / out.length) }, [out.buffer]);
      this.buf = new Int16Array(512);
      this.n = 0;
      this.sum = 0;
    }
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch || !ch.length) return true;
    if (this.ratio === 1) {
      for (let i = 0; i < ch.length; i++) this.push(ch[i]);
      return true;
    }
    let t = this.t;
    while (t < ch.length - 1) {
      const i = Math.floor(t), f = t - i;
      const a = i < 0 ? this.last : ch[i];
      this.push(a + (ch[i + 1] - a) * f);
      t += this.ratio;
    }
    this.t = t - ch.length;
    this.last = ch[ch.length - 1];
    return true;
  }
}
registerProcessor('ada-mic', AdaMic);
