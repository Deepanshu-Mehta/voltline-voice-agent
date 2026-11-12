// AudioWorklet: microphone Float32 at the context's rate -> 16 kHz PCM16, posted in 20 ms frames
// (320 samples). Also posts the frame's RMS level for the input meter.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000; // 1 when the context already runs at 16 kHz
    this.pos = 0;
    this.out = new Int16Array(320);
    this.n = 0;
    this.sumSq = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    // Linear resampler: step through the input at `ratio` samples per output sample.
    while (this.pos < input.length) {
      const i = Math.floor(this.pos);
      const frac = this.pos - i;
      const a = input[i];
      const b = i + 1 < input.length ? input[i + 1] : a;
      const s = Math.max(-1, Math.min(1, a + (b - a) * frac));
      this.out[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      this.sumSq += s * s;
      if (this.n === this.out.length) {
        const frame = this.out.slice();
        this.port.postMessage({ pcm: frame.buffer, level: Math.sqrt(this.sumSq / frame.length) }, [frame.buffer]);
        this.n = 0;
        this.sumSq = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= input.length;
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
