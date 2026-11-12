// Browser side of the cascaded voice agent: microphone -> 16 kHz PCM over a WebSocket to the
// server, Silero voice-activity detection for barge-in, and gapless playback of the reply.
import type { MicVAD } from "@ricky0123/vad-web";
import { API_URL } from "@/lib/api";
import { PcmPlayer } from "@/lib/voice/player";

export type VoiceState = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "error";

export type TurnMetrics = {
  turn: number;
  end_of_turn_ms?: number | null;
  retrieval_ms?: number | null;
  llm_first_token_ms?: number | null;
  tts_first_byte_ms?: number | null;
  server_response_ms?: number | null;
  client_response_ms?: number | null;
  interrupted?: boolean;
  tools?: string[];
};

export type VoiceEvents = {
  onState: (s: VoiceState) => void;
  onUserText: (text: string, final: boolean) => void;
  onAgentText: (turn: number, delta: string) => void;
  onTool: (turn: number, name: string) => void;
  onMetrics: (m: TurnMetrics) => void;
  onInterrupted: (turn: number) => void;
  onLevel: (level: number) => void;
  onError: (message: string) => void;
};

// Type-only import: erased at build time, so the VAD library still loads lazily in start().
type MicVADType = MicVAD;

const REDEMPTION_MS = 400; // silence the VAD waits for before calling speech over

declare global {
  interface Window {
    __VOLT_DRIVER__?: boolean;
    __VOLT_SOURCE__?: string;
    __voltDebug?: { player: PcmPlayer; client: VoiceClient };
  }
}

export class VoiceClient {
  private ws: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private micCtx: AudioContext | null = null;
  private vad: MicVADType | null = null;
  private player: PcmPlayer | null = null;
  private state: VoiceState = "idle";
  private agentTurn = -1; // the reply currently playing or due
  private speechStartAt = 0;
  private lastSpeechEndAt = 0;
  private reported = new Set<number>();
  sessionId: string | null = null;

  constructor(private ev: VoiceEvents, private mode = "standard") {}

  private setState(s: VoiceState) {
    this.state = s;
    this.ev.onState(s);
  }

  private send(msg: object) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  async start() {
    this.setState("connecting");
    try {
      // Microphone first, so the permission prompt appears straight after the click.
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
    } catch {
      this.fail("Volt needs your microphone. Allow microphone access in your browser and try again.");
      return;
    }

    this.player = new PcmPlayer(
      24000,
      (turn, playStartPerf) => {
        if (this.reported.has(turn)) return;
        this.reported.add(turn);
        const latency = this.lastSpeechEndAt ? Math.round(playStartPerf - this.lastSpeechEndAt) : null;
        this.send({ type: "playback", turn, event: "started", client_response_ms: latency });
        this.ev.onMetrics({ turn, client_response_ms: latency });
      },
      (turn) => this.send({ type: "playback", turn, event: "finished" }),
    );
    if (window.__VOLT_DRIVER__) window.__voltDebug = { player: this.player, client: this };
    void this.player.ctx.resume();

    const url = `${API_URL.replace(/^http/, "ws")}/voice`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = (e) => this.onMessage(e.data);
    ws.onclose = () => {
      if (this.state !== "idle" && this.state !== "error") this.fail("The connection to Volt closed.");
    };
    ws.onerror = () => this.fail("Volt can't be reached. Check that the server is running.");
    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      setTimeout(() => reject(new Error("timeout")), 8000);
    }).catch(() => this.fail("Volt can't be reached. Check that the server is running."));
    if (this.state === "error") return;
    this.send({ type: "start", sample_rate: 16000, mode: this.mode, source: window.__VOLT_SOURCE__ ?? "mic" });

    // Capture: 16 kHz context, PCM16 worklet, and the Silero VAD on the same stream.
    this.micCtx = new AudioContext({ sampleRate: 16000 });
    await this.micCtx.audioWorklet.addModule("/worklets/pcm-capture.js");
    const source = this.micCtx.createMediaStreamSource(this.stream);
    const capture = new AudioWorkletNode(this.micCtx, "pcm-capture");
    capture.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
      if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(e.data.pcm);
      this.ev.onLevel(e.data.level);
    };
    source.connect(capture);

    try {
      const { MicVAD } = await import("@ricky0123/vad-web");
      const stream = this.stream;
      this.vad = await MicVAD.new({
        model: "v5",
        audioContext: this.micCtx,
        getStream: async () => stream,
        pauseStream: async () => {},
        resumeStream: async () => stream,
        baseAssetPath: "/vad/",
        onnxWASMBasePath: "/vad/",
        positiveSpeechThreshold: 0.5,
        negativeSpeechThreshold: 0.35,
        redemptionMs: REDEMPTION_MS,
        minSpeechMs: 250,
        startOnLoad: true,
        onSpeechStart: () => this.onSpeechStart(),
        onSpeechRealStart: () => this.onSpeechConfirmed(),
        onVADMisfire: () => this.player?.duck(false),
        onSpeechEnd: () => {
          this.lastSpeechEndAt = performance.now() - REDEMPTION_MS;
        },
      });
    } catch (err) {
      // Without the VAD the call still works; only barge-in falls back to the server's transcript.
      console.warn("Voice activity detection unavailable", err);
    }
  }

  private onSpeechStart() {
    this.speechStartAt = performance.now();
    if (this.player?.isPlaying(this.agentTurn)) this.player.duck(true);
  }

  /** At least 250 ms of speech: if Volt is talking, the user is interrupting. */
  private onSpeechConfirmed() {
    const player = this.player;
    if (!player || !player.isPlaying(this.agentTurn)) return;
    const turn = this.agentTurn;
    const playedMs = player.flush();
    const stopLatency = Math.round(performance.now() - this.speechStartAt);
    this.send({ type: "barge_in", turn, played_ms: Math.round(playedMs), stop_latency_ms: stopLatency });
    this.ev.onInterrupted(turn);
    this.setState("listening");
  }

  private onMessage(data: string | ArrayBuffer) {
    if (typeof data !== "string") {
      const turn = new DataView(data).getUint32(0, true);
      this.player?.enqueue(turn, data.slice(4));
      return;
    }
    const msg = JSON.parse(data);
    switch (msg.type) {
      case "ready":
        this.sessionId = msg.session_id;
        break;
      case "state":
        if (this.state !== "error") this.setState(msg.state);
        break;
      case "transcript":
        this.ev.onUserText(msg.text, msg.final);
        break;
      case "agent_text":
        this.ev.onAgentText(msg.turn, msg.text);
        break;
      case "tool":
        this.ev.onTool(msg.turn, msg.name);
        break;
      case "audio_start":
        this.agentTurn = msg.turn;
        this.player?.beginTurn(msg.turn);
        break;
      case "audio_end":
        this.player?.endTurn(msg.turn);
        break;
      case "clear":
        if (msg.turn === this.agentTurn) this.player?.flush();
        this.ev.onInterrupted(msg.turn);
        break;
      case "metrics":
        this.ev.onMetrics(msg);
        break;
      case "error":
        this.ev.onError(msg.message);
        if (/not configured|unavailable/i.test(msg.message)) this.fail(msg.message);
        break;
    }
  }

  private fail(message: string) {
    if (this.state === "error") return;
    this.ev.onError(message);
    this.setState("error");
    void this.teardown();
  }

  async stop() {
    this.send({ type: "stop" });
    await this.teardown();
    this.setState("idle");
  }

  private async teardown() {
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.close();
    }
    await this.vad?.destroy().catch(() => {});
    this.vad = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    await this.micCtx?.close().catch(() => {});
    this.micCtx = null;
    await this.player?.close().catch(() => {});
    this.player = null;
  }
}
