// Gapless playback of streamed PCM16 chunks for one reply at a time, with instant flush for
// barge-in and accurate "how much has the listener heard" accounting.

export class PcmPlayer {
  readonly ctx: AudioContext;
  private gain: GainNode;
  private sources: AudioBufferSourceNode[] = [];
  private turn = -1;
  private turnStartAt = 0; // context time the turn's first sample plays
  private nextAt = 0; // context time the next chunk should start
  private ended = false; // the server has sent all audio for the turn
  private finishTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private sampleRate: number,
    private onStarted: (turn: number, playStartPerf: number) => void,
    private onFinished: (turn: number) => void,
    tap?: AudioNode | null,
  ) {
    this.ctx = new AudioContext({ latencyHint: "interactive" });
    this.gain = this.ctx.createGain();
    this.gain.connect(this.ctx.destination);
    if (tap) this.gain.connect(tap);
  }

  /** Extra output latency of the device, in seconds. */
  private get outputLatency() {
    return (this.ctx as AudioContext & { outputLatency?: number }).outputLatency || this.ctx.baseLatency || 0;
  }

  /** The node all agent audio passes through (the test driver records from it). */
  get output(): AudioNode {
    return this.gain;
  }

  get activeTurn() {
    return this.turn;
  }

  isPlaying(turn = this.turn) {
    return turn === this.turn && this.sources.length > 0 && this.ctx.currentTime < this.nextAt;
  }

  /** Milliseconds of the current turn the listener has heard so far. */
  playedMs() {
    if (this.turn < 0 || !this.sources.length && this.nextAt === 0) return 0;
    const heard = (this.ctx.currentTime - this.outputLatency - this.turnStartAt) * 1000;
    const scheduled = (this.nextAt - this.turnStartAt) * 1000;
    return Math.max(0, Math.min(heard, scheduled));
  }

  beginTurn(turn: number) {
    this.flush();
    this.turn = turn;
    this.turnStartAt = 0;
    this.nextAt = 0;
    this.ended = false;
    this.gain.gain.value = 1;
  }

  enqueue(turn: number, pcm: ArrayBuffer) {
    if (turn !== this.turn) return; // audio from a reply that was interrupted
    const samples = new Int16Array(pcm);
    if (!samples.length) return;
    const buf = this.ctx.createBuffer(1, samples.length, this.sampleRate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < samples.length; i++) ch[i] = samples[i] / 0x8000;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.gain);
    const first = this.nextAt === 0;
    const at = Math.max(this.ctx.currentTime + (first ? 0.015 : 0), this.nextAt);
    src.start(at);
    if (first) {
      this.turnStartAt = at;
      const playStartPerf = performance.now() + (at - this.ctx.currentTime + this.outputLatency) * 1000;
      this.onStarted(turn, playStartPerf);
    }
    this.nextAt = at + buf.duration;
    this.sources.push(src);
    src.onended = () => {
      this.sources = this.sources.filter((s) => s !== src);
      this.checkFinished();
    };
  }

  /** The server has sent every chunk for this turn. */
  endTurn(turn: number) {
    if (turn !== this.turn) return;
    this.ended = true;
    this.checkFinished();
  }

  private checkFinished() {
    if (!this.ended || this.sources.length) return;
    if (this.finishTimer) clearTimeout(this.finishTimer);
    const turn = this.turn;
    this.finishTimer = setTimeout(() => this.onFinished(turn), this.outputLatency * 1000);
    this.ended = false;
  }

  /** Lower the volume while we find out whether the user is really talking. */
  duck(on: boolean) {
    this.gain.gain.setTargetAtTime(on ? 0.2 : 1, this.ctx.currentTime, 0.02);
  }

  /** Stop everything now. Returns how much of the turn had been heard. */
  flush(): number {
    const heard = this.playedMs();
    for (const s of this.sources) {
      s.onended = null;
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources = [];
    this.nextAt = 0;
    this.ended = false;
    this.gain.gain.cancelScheduledValues(this.ctx.currentTime);
    this.gain.gain.value = 1;
    return heard;
  }

  async close() {
    this.flush();
    await this.ctx.close();
  }
}
