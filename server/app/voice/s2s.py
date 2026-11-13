"""One voice call on a speech-to-speech model (system B): the browser's audio goes straight to a
Gemini Live session, and its audio comes straight back.

It speaks the same WebSocket protocol as the cascade (see session.py), so the page, the player,
the browser test driver and the turns table work unchanged. What it shares with the cascade:
the system prompt file and the tool registry. What it does not: retrieval runs only when the
model calls `search_knowledge_base`, and the model decides by itself when the user has finished
(its own voice activity detection), so there is no end-of-turn figure of ours to report.

Timing: the user's speech end comes from our own Silero VAD on the incoming audio, the same
detector the cascade uses, so "server response" means the same thing for both systems.
"""

from __future__ import annotations

import asyncio
import bisect
import contextlib
import json
import logging
import struct
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncContextManager, Callable

from fastapi import WebSocket, WebSocketDisconnect

from ..db import Database
from ..tools import CHAT_TOOLS, ToolContext, openai_tool_schemas, run_tool
from .vad import SileroVAD

log = logging.getLogger(__name__)

S2S_TOOLS = [*CHAT_TOOLS, "search_knowledge_base"]
OUT_RATE = 24000  # Gemini Live returns 24 kHz PCM16, the rate the browser player already uses

# A factory that opens a Live session: (system_prompt, tool_schemas) -> async context manager
# yielding an object with send_realtime_input / send_client_content / send_tool_response / receive.
LiveFactory = Callable[[str, list[dict]], AsyncContextManager[Any]]


@dataclass
class S2STurn:
    id: int
    user_text: str
    speech_end: float | None  # perf_counter time the user stopped talking (our VAD); None if unknown
    opened_at: float
    first_audio_at: float | None = None
    first_tool_at: float | None = None
    audio_bytes: int = 0
    reply: str = ""
    tools: list[dict] = field(default_factory=list)
    interrupted: bool = False
    finished: bool = False
    announced: bool = False  # the caller's final text was sent to the page
    client: dict[str, Any] = field(default_factory=dict)
    db_row: int | None = None


def gemini_live_factory(api_key: str, model: str, api_version: str = "v1beta",
                        voice: str | None = None) -> LiveFactory:
    """The real Live connection. The SDK's default API version (v1) has no Live endpoint."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(api_version=api_version))

    def connect(system_prompt: str, tool_schemas: list[dict]):
        decls = [types.FunctionDeclaration(name=t["function"]["name"], description=t["function"]["description"],
                                           parameters_json_schema=t["function"]["parameters"]) for t in tool_schemas]
        speech = (types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))) if voice else None)
        cfg = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=decls)],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=speech,
        )
        return client.aio.live.connect(model=model, config=cfg)

    return connect


class S2SSession:
    def __init__(self, ws: WebSocket, db: Database, tool_ctx: ToolContext, system_prompt: str, *,
                 live_factory: LiveFactory, model: str, commit_hash: str | None = None, channel: str = "web",
                 use_vad: bool = True):
        self.ws, self.db, self.tool_ctx = ws, db, tool_ctx
        self.system_prompt = system_prompt
        self.live_factory, self.model = live_factory, model
        self.commit_hash, self.channel = commit_hash, channel
        self.mode, self.session_id, self.source = "s2s", None, "mic"
        self.sample_rate = 16000
        self.use_vad = use_vad
        self.vad: SileroVAD | None = None
        self.audio_bytes = 0
        self.clock: deque[tuple[float, float]] = deque(maxlen=4000)
        self.live: Any = None
        self.turn_no = 0
        self.turn: S2STurn | None = None
        self.turns: dict[int, S2STurn] = {}
        self.user_buf = ""  # what the user has said since the last turn opened
        self.last_turn_end = 0.0
        self.usage: dict[str, int] = {}
        self._send_lock = asyncio.Lock()
        self.closed = False

    # ------------------------------------------------------------ plumbing

    async def send(self, msg: dict) -> None:
        if self.closed:
            return
        async with self._send_lock:
            with contextlib.suppress(Exception):
                await self.ws.send_text(json.dumps(msg))

    async def send_audio(self, turn: S2STurn, pcm: bytes) -> None:
        if self.closed:
            return
        async with self._send_lock:
            with contextlib.suppress(Exception):
                await self.ws.send_bytes(struct.pack("<I", turn.id) + pcm)

    def wall_at(self, audio_s: float) -> float:
        """perf_counter time at which audio at `audio_s` (audio clock) reached the server."""
        if not self.clock:
            return time.perf_counter()
        ends = [a for a, _ in self.clock]
        i = bisect.bisect_left(ends, audio_s)
        if i >= len(self.clock):
            return self.clock[-1][1]
        chunk_end, arrived = self.clock[i]
        return arrived - (chunk_end - audio_s)

    def _speech_end(self) -> float | None:
        vad = self.vad
        if vad is None or vad.last_speech_end_s is None:
            return None
        end = self.wall_at(vad.last_speech_end_s)
        # The VAD missed this utterance: its last end belongs to speech before the previous turn.
        return end if end > self.last_turn_end else None

    # ------------------------------------------------------------ lifecycle

    async def run(self, start: dict) -> None:
        self.sample_rate = int(start.get("sample_rate", 16000))
        self.session_id = start.get("session_id") or uuid.uuid4().hex[:8]
        self.source = start.get("source", "mic")
        if self.use_vad and self.sample_rate == 16000:
            self.vad = SileroVAD()
        try:
            cm = self.live_factory(self.system_prompt, openai_tool_schemas(S2S_TOOLS))
            self.live = await cm.__aenter__()
        except Exception as exc:
            log.error("Speech-to-speech session could not start: %s", exc)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            why = f"HTTP {status}" if status else str(exc)
            await self.send({"type": "error", "message": f"Voice is unavailable: the speech-to-speech model "
                                                         f"({self.model}) refused the connection ({why})."})
            with contextlib.suppress(Exception):
                await self.ws.close(code=1011)
            return

        await self.send({"type": "ready", "session_id": self.session_id, "sample_rate_out": OUT_RATE, "mode": "s2s"})
        await self.send({"type": "state", "state": "listening"})
        recv = asyncio.create_task(self._live_loop())
        try:
            await self._client_loop(recv)
        finally:
            self.closed = True
            recv.cancel()
            with contextlib.suppress(BaseException):
                await recv
            if self.turn and not self.turn.finished:
                await self._finish(self.turn)
            with contextlib.suppress(Exception):
                await cm.__aexit__(None, None, None)

    async def _client_loop(self, recv: asyncio.Task) -> None:
        from google.genai import types

        while True:
            receive = asyncio.create_task(self.ws.receive())
            done, _ = await asyncio.wait({receive, recv}, return_when=asyncio.FIRST_COMPLETED)
            if recv in done:  # the Live session ended (error, time limit)
                receive.cancel()
                if (exc := recv.exception()) is not None:
                    await self.send({"type": "error", "message": f"The speech-to-speech session ended: {exc}"})
                return
            try:
                msg = receive.result()
            except WebSocketDisconnect:
                return
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("bytes"):
                pcm = msg["bytes"]
                self.audio_bytes += len(pcm)
                self.clock.append((self.audio_bytes / (2 * self.sample_rate), time.perf_counter()))
                if self.vad is not None:
                    self.vad.process(pcm)
                await self.live.send_realtime_input(
                    audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={self.sample_rate}"))
            elif msg.get("text"):
                data = json.loads(msg["text"])
                kind = data.get("type")
                if kind == "stop":
                    return
                if kind == "barge_in":
                    await self._interrupt(data.get("turn"), reason="client", played_ms=data.get("played_ms"),
                                          stop_latency_ms=data.get("stop_latency_ms"))
                elif kind == "playback":
                    await self._on_playback(data)
                elif kind == "text" and str(data.get("text", "")).strip():
                    text = str(data["text"]).strip()
                    self.user_buf = text
                    await self.live.send_client_content(
                        turns=types.Content(role="user", parts=[types.Part(text=text)]), turn_complete=True)

    async def _live_loop(self) -> None:
        while not self.closed:
            got = 0
            async for msg in self.live.receive():  # one model turn per pass
                got += 1
                await self._on_live(msg)
            if not got:  # the connection closed: receive() returns at once from now on
                raise ConnectionError("the Live session closed")

    # ------------------------------------------------------------ model events

    def _open_turn(self) -> S2STurn:
        """The model has started answering: everything heard since the last turn is this turn's input."""
        turn = self.turn
        if turn and not turn.finished:
            return turn
        self.turn_no += 1
        turn = S2STurn(self.turn_no, self.user_buf.strip(), self._speech_end(), time.perf_counter())
        self.user_buf = ""
        self.turn = self.turns[turn.id] = turn
        return turn

    async def _announce(self, turn: S2STurn) -> None:
        """Send the caller's words as final once, before any tool note or audio of the reply, so
        the page shows the question above the answer (and only once)."""
        if not turn.announced:
            turn.announced = True
            await self.send({"type": "transcript", "text": turn.user_text, "final": True})

    async def _on_live(self, msg: Any) -> None:
        if getattr(msg, "usage_metadata", None):
            u = msg.usage_metadata
            for k in ("prompt_token_count", "response_token_count", "total_token_count"):
                if getattr(u, k, None):
                    self.usage[k] = self.usage.get(k, 0) + getattr(u, k)
        if getattr(msg, "go_away", None):
            log.warning("Live session will close soon: %s", msg.go_away)
        if getattr(msg, "tool_call", None):
            await self._on_tool_call(msg.tool_call)
        sc = getattr(msg, "server_content", None)
        if not sc:
            return
        if sc.input_transcription and sc.input_transcription.text:
            turn = self.turn
            if turn and not turn.finished and turn.first_audio_at is None:
                turn.user_text = (turn.user_text + sc.input_transcription.text).strip()  # late words, same turn
                text = turn.user_text
            else:
                self.user_buf += sc.input_transcription.text
                text = self.user_buf.strip()
            await self.send({"type": "transcript", "text": text, "final": False})
        if sc.interrupted and self.turn and not self.turn.finished:
            await self._interrupt(self.turn.id, reason="model")
        if sc.model_turn:
            for part in sc.model_turn.parts or []:
                data = part.inline_data.data if part.inline_data else None
                if not data:
                    continue
                turn = self._open_turn()
                if turn.interrupted:
                    continue
                if turn.first_audio_at is None:
                    turn.first_audio_at = time.perf_counter()
                    await self._announce(turn)
                    await self.send({"type": "audio_start", "turn": turn.id, "sample_rate": OUT_RATE})
                    await self.send({"type": "state", "state": "speaking"})
                turn.audio_bytes += len(data)
                await self.send_audio(turn, data)
        if sc.output_transcription and sc.output_transcription.text:
            turn = self._open_turn()
            await self._announce(turn)
            turn.reply += sc.output_transcription.text
            await self.send({"type": "agent_text", "turn": turn.id, "text": sc.output_transcription.text})
        if sc.turn_complete and self.turn and not self.turn.finished:
            await self._finish(self.turn)

    async def _on_tool_call(self, tool_call: Any) -> None:
        from google.genai import types

        turn = self._open_turn()
        await self._announce(turn)
        if turn.first_tool_at is None:
            turn.first_tool_at = time.perf_counter()
            await self.send({"type": "state", "state": "thinking"})
        responses = []
        for fc in tool_call.function_calls or []:
            args = dict(fc.args or {})
            await self.send({"type": "tool", "turn": turn.id, "name": fc.name})
            t0 = time.perf_counter()
            result = await asyncio.to_thread(run_tool, self.tool_ctx, fc.name, args)
            turn.tools.append({"name": fc.name, "args": args, "result": result,
                               "ms": round((time.perf_counter() - t0) * 1000, 1)})
            log.info("s2s tool %s %s", fc.name, json.dumps(args))
            responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response={"result": result}))
        await self.live.send_tool_response(function_responses=responses)

    async def _interrupt(self, turn_id: int | None, *, reason: str, played_ms: float | None = None,
                         stop_latency_ms: float | None = None) -> None:
        turn = self.turn
        if not turn or turn.finished or turn.interrupted or (turn_id is not None and turn_id != turn.id):
            return
        turn.interrupted = True
        turn.client.update({"interrupted_by": reason, "played_ms": played_ms, "stop_latency_ms": stop_latency_ms})
        await self.send({"type": "clear", "turn": turn.id})
        await self.send({"type": "state", "state": "listening"})
        # The model also stops on its own when it hears the user; the turn is logged when it
        # reports the turn complete, or here if it already has.

    async def _on_playback(self, data: dict) -> None:
        turn = self.turns.get(data.get("turn"))
        if not turn:
            return
        if data.get("event") == "started" and data.get("client_response_ms") is not None:
            turn.client["client_response_ms"] = data["client_response_ms"]
        if data.get("event") == "finished":
            turn.client["playback_finished"] = True
        if turn.db_row is not None:
            self.db.update_turn_extra(turn.db_row, {"client": turn.client})

    # ------------------------------------------------------------ metrics

    def metrics(self, turn: S2STurn) -> dict:
        ms = lambda a, b: round((a - b) * 1000, 1) if a is not None and b is not None else None  # noqa: E731
        return {
            "end_of_turn_ms": None,  # decided inside the model
            "retrieval_ms": next((t["ms"] for t in turn.tools if t["name"] == "search_knowledge_base"), None),
            "llm_first_token_ms": None,
            "first_tool_call_ms": ms(turn.first_tool_at, turn.speech_end),
            "tts_first_byte_ms": None,
            "server_response_ms": ms(turn.first_audio_at, turn.speech_end),
            "audio_ms": round(turn.audio_bytes / 2 / OUT_RATE * 1000),
            "tools": [t["name"] for t in turn.tools],
            "filler": None,
            "interrupted": turn.interrupted,
        }

    async def _finish(self, turn: S2STurn) -> None:
        turn.finished = True
        self.last_turn_end = time.perf_counter()
        m = self.metrics(turn)
        if turn.first_audio_at is not None:
            await self.send({"type": "audio_end", "turn": turn.id})
        await self.send({"type": "metrics", "turn": turn.id, **m})
        if not turn.interrupted:
            await self.send({"type": "state", "state": "listening"})
        turn.db_row = self.db.log_turn(
            channel=self.channel, mode=self.mode, session_id=self.session_id, user_text=turn.user_text,
            reply=turn.reply.strip(), tool_calls=[{"name": t["name"], "args": t["args"]} for t in turn.tools],
            retrieval_ms=m["retrieval_ms"], llm_first_token_ms=None, total_ms=m["server_response_ms"],
            model=self.model, commit_hash=self.commit_hash,
            extra={"voice": m, "client": turn.client, "source": self.source, "error": None,
                   "fallback_reason": None, "usage": dict(self.usage)})
        self.usage = {}
