"""Cartesia streaming text-to-speech over one persistent WebSocket per voice session.

Each agent reply is one Cartesia context: sentences are pushed with continue=true as the LLM
writes them, and an empty transcript with continue=false closes the context. Audio chunks and
word timestamps come back on the same socket and are routed to the reply's queue.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

log = logging.getLogger(__name__)
CARTESIA_URL = "wss://api.cartesia.ai/tts/websocket"


@dataclass
class TTSChunk:
    kind: str  # audio | timestamps | done | error
    audio: bytes = b""
    words: list[str] = field(default_factory=list)
    starts: list[float] = field(default_factory=list)  # seconds from the start of the context
    message: str = ""
    received_at: float = 0.0


class TTSStream(Protocol):
    async def start(self, context_id: str) -> None: ...
    async def push(self, context_id: str, text: str) -> None: ...
    async def finish(self, context_id: str) -> None: ...
    async def cancel(self, context_id: str) -> None: ...
    def chunks(self, context_id: str) -> AsyncIterator[TTSChunk]: ...
    async def close(self) -> None: ...


class CartesiaTTS:
    def __init__(self, api_key: str, *, version: str, model: str, voice_id: str, sample_rate: int = 24000,
                 language: str = "en"):
        self.api_key, self.version = api_key, version
        self.model, self.voice_id, self.sample_rate, self.language = model, voice_id, sample_rate, language
        self.ws = None
        self.queues: dict[str, asyncio.Queue[TTSChunk | None]] = {}
        self._reader_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> "CartesiaTTS":
        from websockets.asyncio.client import connect

        url = f"{CARTESIA_URL}?cartesia_version={self.version}"
        self.ws = await connect(url, additional_headers={"X-API-Key": self.api_key, "Cartesia-Version": self.version},
                                max_size=None, open_timeout=10)
        self._reader_task = asyncio.create_task(self._reader())
        return self

    async def _ensure(self) -> None:
        async with self._lock:
            if self.ws is None or self.ws.state.name != "OPEN":
                await self.connect()

    def _request(self, context_id: str, transcript: str, more: bool) -> str:
        return json.dumps({
            "model_id": self.model,
            "transcript": transcript,
            "voice": {"mode": "id", "id": self.voice_id},
            "language": self.language,
            "context_id": context_id,
            "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": self.sample_rate},
            "add_timestamps": True,
            "continue": more,
        })

    async def start(self, context_id: str) -> None:
        await self._ensure()
        self.queues[context_id] = asyncio.Queue()

    async def push(self, context_id: str, text: str) -> None:
        if text.strip():
            await self.ws.send(self._request(context_id, text, True))

    async def finish(self, context_id: str) -> None:
        await self.ws.send(self._request(context_id, "", False))

    async def cancel(self, context_id: str) -> None:
        with contextlib.suppress(Exception):
            await self.ws.send(json.dumps({"context_id": context_id, "cancel": True}))
        q = self.queues.pop(context_id, None)
        if q is not None:
            await q.put(None)

    async def chunks(self, context_id: str) -> AsyncIterator[TTSChunk]:
        q = self.queues.get(context_id)
        if q is None:
            return
        while (chunk := await q.get()) is not None:
            yield chunk
            if chunk.kind in ("done", "error"):
                break
        self.queues.pop(context_id, None)

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()
        for q in self.queues.values():
            await q.put(None)

    async def _reader(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                q = self.queues.get(msg.get("context_id", ""))
                if q is None:
                    continue  # a cancelled reply
                now = time.perf_counter()
                kind = msg.get("type")
                if kind == "chunk" and msg.get("data"):
                    await q.put(TTSChunk("audio", audio=base64.b64decode(msg["data"]), received_at=now))
                elif kind == "timestamps":
                    wt = msg.get("word_timestamps") or {}
                    await q.put(TTSChunk("timestamps", words=wt.get("words", []), starts=wt.get("start", []),
                                         received_at=now))
                elif kind == "done":
                    await q.put(TTSChunk("done", received_at=now))
                elif kind == "error":
                    await q.put(TTSChunk("error", message=msg.get("message") or msg.get("title", ""), received_at=now))
        except Exception as exc:
            log.warning("Cartesia socket closed: %s", exc)
            for q in list(self.queues.values()):
                await q.put(TTSChunk("error", message=f"TTS connection lost: {exc}"))
