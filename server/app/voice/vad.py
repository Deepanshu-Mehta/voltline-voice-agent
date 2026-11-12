"""Server-side Silero VAD (v5, ONNX) on the incoming 16 kHz audio.

It gives every channel (browser, test files, and later the phone) the same, precise view of when
the user is speaking, which the session uses for the silence timeout that ends a turn and for
timing the end of speech. The model runs on 32 ms windows and costs well under a millisecond each.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

MODEL = Path(__file__).parent / "models" / "silero_vad_v5.onnx"
WINDOW = 512  # samples at 16 kHz (32 ms)
CONTEXT = 64  # v5 expects the last 64 samples of the previous window prepended


_SESSION = None


def _session():
    """One ONNX session for all calls (it is thread-safe); each SileroVAD keeps its own state."""
    global _SESSION
    if _SESSION is None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        _SESSION = ort.InferenceSession(str(MODEL), sess_options=opts, providers=["CPUExecutionProvider"])
    return _SESSION


class SileroVAD:
    def __init__(self, start_threshold: float = 0.5, end_threshold: float = 0.35):
        self.session = _session()
        self.start_threshold, self.end_threshold = start_threshold, end_threshold
        self.state = np.zeros((2, 1, 128), np.float32)
        self.context = np.zeros(CONTEXT, np.float32)
        self.sr = np.array(16000, np.int64)
        self.buffer = np.zeros(0, np.float32)
        self.odd = b""  # a stray byte from a frame that split a sample
        self.speaking = False
        self.samples_seen = 0  # audio clock, in samples
        self.last_speech_end_s: float | None = None  # audio time the latest speech stretch ended
        self.speech_start_s: float | None = None

    def process(self, pcm16: bytes) -> list[tuple[str, float]]:
        """Feed PCM16 audio; returns events ("start" | "end", audio time in seconds)."""
        data = self.odd + pcm16
        cut = len(data) - len(data) % 2
        self.odd = data[cut:]
        x = np.frombuffer(data[:cut], "<i2").astype(np.float32) / 32768
        self.buffer = np.concatenate([self.buffer, x])
        events = []
        while len(self.buffer) >= WINDOW:
            window, self.buffer = self.buffer[:WINDOW], self.buffer[WINDOW:]
            inp = np.concatenate([self.context, window])[None, :]
            out, self.state = self.session.run(None, {"input": inp, "state": self.state, "sr": self.sr})
            self.context = window[-CONTEXT:]
            prob = float(out[0, 0])
            t_start = self.samples_seen / 16000
            self.samples_seen += WINDOW
            t_end = self.samples_seen / 16000
            if not self.speaking and prob >= self.start_threshold:
                self.speaking = True
                self.speech_start_s = t_start
                events.append(("start", t_start))
            elif self.speaking and prob < self.end_threshold:
                self.speaking = False
                self.last_speech_end_s = t_start
                events.append(("end", t_start))
            if self.speaking:
                self.last_speech_end_s = t_end
        return events

    def silence_ms(self) -> float:
        """How long it has been silent since speech last ended, on the audio clock."""
        if self.speaking or self.last_speech_end_s is None:
            return 0.0
        return (self.samples_seen / 16000 - self.last_speech_end_s) * 1000
