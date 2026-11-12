import asyncio
import json
import struct
import time

from fastapi.testclient import TestClient

from app.main import create_app
from app.voice.stt import STTEvent, parse_deepgram
from app.voice.text import SentenceChunker, heard_text
from app.voice.tts import TTSChunk

from .conftest import FakeLLM

WORD_S = 0.1  # fake TTS: 100 ms of audio per word
RATE = 24000


class FakeSTT:
    """Audio frames starting with b"SAY:" become a final transcript plus end of turn."""

    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.received = 0

    async def send_audio(self, pcm: bytes):
        self.received += len(pcm)
        if pcm.startswith(b"SAY:"):
            now = time.perf_counter()
            await self.q.put(STTEvent("final", pcm[4:].decode(), 1.0, now))
            await self.q.put(STTEvent("end_of_turn", "", 1.0, now))

    async def events(self):
        while (e := await self.q.get()) is not None:
            yield e

    async def finalize(self):
        pass

    async def close(self):
        await self.q.put(None)


class FakeTTS:
    def __init__(self, delay: float = 0.0):
        self.queues: dict[str, asyncio.Queue] = {}
        self.offsets: dict[str, float] = {}
        self.cancelled: list[str] = []
        self.delay = delay

    async def start(self, ctx):
        self.queues[ctx] = asyncio.Queue()
        self.offsets[ctx] = 0.0

    async def push(self, ctx, text):
        words = text.split()
        starts = [self.offsets[ctx] + i * WORD_S for i in range(len(words))]
        self.offsets[ctx] += len(words) * WORD_S
        q = self.queues.get(ctx)
        if q:
            await q.put(TTSChunk("timestamps", words=words, starts=starts))
            for _ in words:
                if self.delay:
                    await asyncio.sleep(self.delay)
                await q.put(TTSChunk("audio", audio=b"\x00\x00" * int(RATE * WORD_S), received_at=time.perf_counter()))

    async def finish(self, ctx):
        if ctx in self.queues:
            await self.queues[ctx].put(TTSChunk("done"))

    async def cancel(self, ctx):
        self.cancelled.append(ctx)
        q = self.queues.pop(ctx, None)
        if q:
            await q.put(None)

    async def chunks(self, ctx):
        q = self.queues.get(ctx)
        while q and (c := await q.get()) is not None:
            yield c
            if c.kind == "done":
                break

    async def close(self):
        pass


def make_client(settings, retriever, script, tts=None, llm_delay=0.0):
    llm = FakeLLM(script, delay=llm_delay)
    tts = tts or FakeTTS()

    async def stt_factory(rate):
        return FakeSTT()

    async def tts_factory():
        return tts

    app = create_app(settings, llm=llm, retriever=retriever, stt_factory=stt_factory, tts_factory=tts_factory)
    return TestClient(app), llm, tts


def collect_until(ws, kind, limit=500):
    """Read messages until one of type `kind`; returns (json messages, binary frames)."""
    msgs, frames = [], []
    for _ in range(limit):
        m = ws.receive()
        if m.get("bytes") is not None:
            frames.append(m["bytes"])
            continue
        data = json.loads(m["text"])
        msgs.append(data)
        if data["type"] == kind:
            return msgs, frames
    raise AssertionError(f"no {kind} message; got {[m['type'] for m in msgs]}")


def test_spoken_turn_with_tool_and_filler(settings, retriever):
    client, llm, tts = make_client(settings, retriever, [
        [("get_order_status", json.dumps({"order_id": "4512"}))],
        "Order 4 5 1 2 has shipped. It arrives Saturday.",
    ])
    with client.websocket_connect("/voice") as ws:
        ws.send_text(json.dumps({"type": "start", "sample_rate": 16000}))
        msgs, _ = collect_until(ws, "state")
        assert msgs[0]["type"] == "ready"
        ws.send_bytes(b"SAY:Where is order 4512?")
        msgs, frames = collect_until(ws, "metrics")
        kinds = [m["type"] for m in msgs]
        assert {"transcript", "tool", "audio_start", "audio_end"} <= set(kinds)
        # Every audio frame is tagged with its turn number.
        assert frames and all(struct.unpack("<I", f[:4])[0] == 1 for f in frames)
        metrics = msgs[-1]
        assert metrics["filler"] == "Let me look up that order."
        assert metrics["tools"] == ["get_order_status"]
        assert metrics["server_response_ms"] is not None and metrics["end_of_turn_ms"] is not None
        ws.send_text(json.dumps({"type": "playback", "turn": 1, "event": "started", "client_response_ms": 812}))
        ws.send_text(json.dumps({"type": "stop"}))
    row = client.get("/turns/recent").json()[0]
    assert row["channel"] == "web" and row["reply"].startswith("Let me look up that order.")
    assert json.loads(row["extra"])["client"]["client_response_ms"] == 812
    web = [g for g in client.get("/turns/summary").json() if g["channel"] == "web"]
    assert web and web[0]["response_ms"]["median"] == 812


def test_barge_in_keeps_only_heard_words(settings, retriever):
    reply = "Pulse Buds Pro cost $149.99 and come in black or silver with a wireless charging case."
    tts = FakeTTS(delay=0.02)
    client, llm, tts = make_client(settings, retriever, [reply, "Sure, what else?"], tts=tts, llm_delay=0.01)
    with client.websocket_connect("/voice") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        collect_until(ws, "state")
        ws.send_bytes(b"SAY:How much are the Pulse Buds Pro?")
        collect_until(ws, "audio_start")
        # The browser played 0.35 s (words 0-3 started) before the user cut in.
        ws.send_text(json.dumps({"type": "barge_in", "turn": 1, "played_ms": 350, "stop_latency_ms": 120}))
        msgs, _ = collect_until(ws, "metrics")
        assert any(m["type"] == "clear" and m["turn"] == 1 for m in msgs)
        assert msgs[-1]["interrupted"] is True
        ws.send_bytes(b"SAY:Actually, never mind.")
        collect_until(ws, "metrics")
        ws.send_text(json.dumps({"type": "stop"}))
    assert tts.cancelled and tts.cancelled[0].startswith("turn-1-")
    # The second LLM request carries the truncated assistant turn.
    history = llm.requests[1]["messages"]
    assistant = [m for m in history if m["role"] == "assistant"]
    assert assistant[-1]["content"] == "Pulse Buds Pro cost..."


def test_voice_not_configured(settings, retriever):
    client = TestClient(create_app(settings, llm=FakeLLM([]), retriever=retriever))
    assert client.get("/health").json()["voice_configured"] is False
    with client.websocket_connect("/voice") as ws:
        assert "not configured" in json.loads(ws.receive_text())["message"]


def test_sentence_chunker_handles_prices_and_abbreviations():
    c = SentenceChunker()
    out = []
    for tok in ["Pulse Buds Pro cost $149", ".99. Support is open 9 a.m. to", " 7 p.m. Anything else", "?"]:
        out += c.push(tok)
    out.append(c.flush())
    assert out == ["Pulse Buds Pro cost $149.99. ", "Support is open 9 a.m. to 7 p.m. ", "Anything else?"]


def test_heard_text():
    assert heard_text(["a", "b", "c"], [0.0, 0.2, 0.4], 300) == "a b"


def test_parse_deepgram_events():
    res = parse_deepgram({"type": "Results", "is_final": True, "speech_final": True, "channel": {"alternatives": [
        {"transcript": "where is my order", "words": [{"word": "order", "start": 1.0, "end": 1.4}]}]}})
    assert [e.kind for e in res] == ["final", "end_of_turn"] and res[1].audio_end_s == 1.4
    assert parse_deepgram({"type": "UtteranceEnd", "last_word_end": -1}) == []
    assert parse_deepgram({"type": "SpeechStarted", "timestamp": 2.5})[0].kind == "speech_started"


def test_sounds_unfinished():
    from app.voice.text import sounds_unfinished
    assert sounds_unfinished("Do the SafeView cameras work with")
    assert sounds_unfinished("Do the SafeView cameras work with?")
    assert not sounds_unfinished("Do the SafeView cameras work with 5 GHz?")
    assert not sounds_unfinished("Where is order 4512")


def test_clean_for_speech():
    from app.voice.text import clean_for_speech
    assert clean_for_speech("2.4\u202fGHz Wi\u2011Fi") == "2.4 GHz Wi-Fi"


def test_model_preamble_replaces_filler(settings, retriever):
    """If the model says its own line before a tool call, that line is spoken instead of a filler."""
    client, llm, tts = make_client(settings, retriever, [
        "I'll check that for you now.",  # streamed text, then (next item) the tool call in a new round
        "Order 4 5 1 2 has shipped.",
    ])
    # Script the first round as text followed by a tool call in the same response.
    llm.script = [("mixed", "I'll check that for you now.", ("get_order_status", json.dumps({"order_id": "4512"}))),
                  "Order 4 5 1 2 has shipped."]
    with client.websocket_connect("/voice") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        collect_until(ws, "state")
        ws.send_bytes(b"SAY:Where is order 4512?")
        msgs, _ = collect_until(ws, "metrics")
        ws.send_text(json.dumps({"type": "stop"}))
    assert msgs[-1]["filler"] is None
    row = client.get("/turns/recent").json()[0]
    assert row["reply"] == "I'll check that for you now. Order 4 5 1 2 has shipped."
