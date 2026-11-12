// Injected before any page script by the browser test driver.
// 1. getUserMedia returns a stream the driver plays test clips into (a stubbed microphone).
// 2. installTap() listens to the agent's audio output and timestamps when sound starts and
//    stops, and keeps the samples so the driver can save both sides of the conversation.
window.__VOLT_DRIVER__ = true;
window.__VOLT_SOURCE__ = "driver";

(() => {
  let micCtx = null;
  let micDest = null;
  const ensureMic = () => {
    if (!micCtx) {
      micCtx = new AudioContext({ sampleRate: 16000 });
      micDest = micCtx.createMediaStreamDestination();
    }
    return micCtx;
  };
  const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (constraints) => {
    if (!constraints || !constraints.audio) return original(constraints);
    await ensureMic().resume();
    return micDest.stream;
  };

  const D = {
    events: [], // {t, kind: "onset" | "offset"} for agent audio, in performance.now() ms
    agentChunks: [],
    agentRate: 0,
    agentT0: null,
    tapped: false,
    current: null,

    async play(url) {
      const ctx = ensureMic();
      await ctx.resume();
      const data = await (await fetch(url)).arrayBuffer();
      const audio = await ctx.decodeAudioData(data);
      const src = ctx.createBufferSource();
      src.buffer = audio;
      src.connect(micDest);
      D.current = src;
      const lead = 0.05;
      const startAt = ctx.currentTime + lead;
      const startPerf = performance.now() + lead * 1000;
      src.start(startAt);
      await new Promise((resolve) => (src.onended = resolve));
      D.current = null;
      return { start: startPerf, end: startPerf + audio.duration * 1000, duration_ms: audio.duration * 1000 };
    },

    installTap() {
      const dbg = window.__voltDebug;
      if (D.tapped) return true;
      if (!dbg || !dbg.player) return false;
      const ctx = dbg.player.ctx;
      const sp = ctx.createScriptProcessor(512, 1, 1);
      const mute = ctx.createGain();
      mute.gain.value = 0;
      dbg.player.output.connect(sp);
      sp.connect(mute);
      mute.connect(ctx.destination);
      D.agentRate = ctx.sampleRate;
      let sounding = false;
      let quietBlocks = 0;
      sp.onaudioprocess = (e) => {
        const x = e.inputBuffer.getChannelData(0);
        // Time at which this block is rendered, on the performance.now() clock.
        const t = performance.now() + (e.playbackTime - ctx.currentTime) * 1000;
        if (D.agentT0 === null) D.agentT0 = t;
        const pcm = new Int16Array(x.length);
        let peak = 0;
        for (let i = 0; i < x.length; i++) {
          const v = Math.max(-1, Math.min(1, x[i]));
          pcm[i] = v * 32767;
          peak = Math.max(peak, Math.abs(v));
        }
        D.agentChunks.push(pcm);
        if (peak > 0.01) {
          if (!sounding) D.events.push({ t, kind: "onset" });
          sounding = true;
          quietBlocks = 0;
        } else if (sounding && ++quietBlocks * (x.length / ctx.sampleRate) > 0.25) {
          sounding = false;
          D.events.push({ t, kind: "offset" });
        }
      };
      D.tapped = true;
      return true;
    },

    state() {
      const el = document.querySelector(".voice .chat-state");
      return el ? el.textContent.trim() : "";
    },

    sessionId() {
      return window.__voltDebug ? window.__voltDebug.client.sessionId : null;
    },

    exportAgent() {
      let n = 0;
      for (const c of D.agentChunks) n += c.length;
      const all = new Int16Array(n);
      let o = 0;
      for (const c of D.agentChunks) {
        all.set(c, o);
        o += c.length;
      }
      const bytes = new Uint8Array(all.buffer);
      let bin = "";
      for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      return { rate: D.agentRate, t0: D.agentT0, pcm: btoa(bin) };
    },
  };
  window.__driver = D;
})();
