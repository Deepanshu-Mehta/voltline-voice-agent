"""Deepgram streaming speech-to-text over a raw WebSocket.

Events are normalised to STTEvent so the session never sees provider JSON:
  interim         partial transcript of the current segment (for the live caption)
  final           a finalised segment of text
  end_of_turn     the user stopped speaking: speech_final after `endpointing` ms of silence,
                  or UtteranceEnd (word-timing based, robust to background noise)
  speech_started  Deepgram's voice activity detector heard speech
Times are on the audio clock: seconds since the first byte sent to Deepgram.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Protocol
from urllib.parse import urlencode

log = logging.getLogger(__name__)
DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"
KEEPALIVE_S = 4.0


@dataclass
class STTEvent:
    kind: str  # interim | final | end_of_turn | speech_started | error
    text: str = ""
    audio_end_s: float | None = None  # end of the last word, on the audio clock
    received_at: float = 0.0  # perf_counter when the event arrived


class STTStream(Protocol):
    async def send_audio(self, pcm: bytes) -> None: ...
    async def finalize(self) -> None: ...
    def events(self) -> AsyncIterator[STTEvent]: ...
    async def close(self) -> None: ...


class DeepgramSTT:
    def __init__(self, api_key: str, *, model: str = "nova-3", language: str = "en-US", sample_rate: int = 16000,
                 endpointing_ms: int = 700, utterance_end_ms: int = 1000, keyterms: list[str] | None = None):
        self.api_key = api_key
        self.params = {
            "model": model, "language": language, "encoding": "linear16", "sample_rate": sample_rate,
            "channels": 1, "interim_results": "true", "endpointing": endpointing_ms,
            "utterance_end_ms": max(utterance_end_ms, 1000), "vad_events": "true",
            "smart_format": "true", "punctuate": "true",
            # Nova-3 keyterm prompting: product and brand names the model would otherwise split or misspell.
            "keyterm": keyterms or [],
        }
        self.ws = None
        self.queue: asyncio.Queue[STTEvent | None] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._last_send = 0.0

    async def connect(self) -> "DeepgramSTT":
        from websockets.asyncio.client import connect

        url = f"{DEEPGRAM_URL}?{urlencode(self.params, doseq=True)}"
        self.ws = await connect(url, additional_headers={"Authorization": f"Token {self.api_key}"},
                                max_size=None, open_timeout=10)
        self._tasks = [asyncio.create_task(self._reader()), asyncio.create_task(self._keepalive())]
        return self

    async def send_audio(self, pcm: bytes) -> None:
        if self.ws is not None:
            await self.ws.send(pcm)
            self._last_send = time.monotonic()

    async def events(self) -> AsyncIterator[STTEvent]:
        while (event := await self.queue.get()) is not None:
            yield event

    async def finalize(self) -> None:
        """Ask Deepgram to transcribe everything buffered now, instead of waiting for endpointing."""
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.send(json.dumps({"type": "Finalize"}))

    async def close(self) -> None:
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.send(json.dumps({"type": "CloseStream"}))
                await self.ws.close()
        for t in self._tasks:
            t.cancel()
        await self.queue.put(None)

    async def _keepalive(self) -> None:
        # Deepgram closes a stream that receives nothing for 10 s.
        while True:
            await asyncio.sleep(KEEPALIVE_S)
            if self.ws is not None and time.monotonic() - self._last_send > KEEPALIVE_S:
                with contextlib.suppress(Exception):
                    await self.ws.send(json.dumps({"type": "KeepAlive"}))

    async def _reader(self) -> None:
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    continue
                for event in parse_deepgram(json.loads(raw)):
                    await self.queue.put(event)
        except Exception as exc:  # connection dropped
            log.warning("Deepgram stream ended: %s", exc)
            await self.queue.put(STTEvent("error", text=str(exc), received_at=time.perf_counter()))
        finally:
            await self.queue.put(None)


def parse_deepgram(msg: dict) -> list[STTEvent]:
    now = time.perf_counter()
    kind = msg.get("type")
    if kind == "Results":
        alt = (msg.get("channel", {}).get("alternatives") or [{}])[0]
        text = (alt.get("transcript") or "").strip()
        words = alt.get("words") or []
        end = words[-1]["end"] if words else None
        out: list[STTEvent] = []
        if text:
            out.append(STTEvent("final" if msg.get("is_final") else "interim", text, end, now))
        if msg.get("speech_final") and not msg.get("from_finalize"):
            out.append(STTEvent("end_of_turn", "", end, now))
        return out
    if kind == "UtteranceEnd":
        last = msg.get("last_word_end")
        if last is not None and last >= 0:
            return [STTEvent("end_of_turn", "", last, now)]
        return []
    if kind == "SpeechStarted":
        return [STTEvent("speech_started", "", msg.get("timestamp"), now)]
    if kind == "Error":
        return [STTEvent("error", msg.get("description") or msg.get("message") or "Deepgram error", None, now)]
    return []
