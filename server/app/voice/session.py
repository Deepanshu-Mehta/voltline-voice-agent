"""One voice call over a browser WebSocket: the cascaded pipeline's orchestrator.

Wire protocol
  browser -> server
    binary                      16 kHz mono PCM16 microphone audio (or a test file streamed in real time)
    {"type":"start", "sample_rate":16000, "mode":"standard", "session_id":..., "source":"mic"|"file"}
    {"type":"barge_in", "turn":N, "played_ms":X, "stop_latency_ms":Y}   user spoke over the agent;
                                                                       the browser already stopped playback
    {"type":"playback", "turn":N, "event":"started"|"finished", "client_response_ms":Z}
    {"type":"text", "text":"..."}  a typed user turn (tests, accessibility)
    {"type":"stop"}
  server -> browser
    {"type":"ready"} {"type":"state","state":"listening"|"thinking"|"speaking"}
    {"type":"transcript","text":...,"final":bool}          the user's words, live
    {"type":"agent_text","turn":N,"text":delta}            the reply as the LLM writes it
    {"type":"tool","turn":N,"name":...}
    {"type":"audio_start","turn":N,"sample_rate":24000}
    binary: 4-byte little-endian turn number + PCM16 audio  (stale turns are dropped by the browser)
    {"type":"audio_end","turn":N}
    {"type":"clear","turn":N}                               stop playing this turn now
    {"type":"metrics","turn":N, ...}                        per-turn timings in milliseconds
    {"type":"error","message":...}

Timings are measured from the end of the user's speech, taken from the speech-to-text word
timings and mapped from the audio clock to the server clock.
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
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from ..agent import Agent, TurnConfig, TurnRecord
from ..db import Database
from .stt import STTEvent, STTStream
from .text import SentenceChunker, clean_for_speech, filler_for, heard_text, sounds_unfinished
from .tts import TTSStream
from .vad import SileroVAD

log = logging.getLogger(__name__)

# The server stops the agent itself if the transcript shows the user talking over it with at
# least this many words (a backup for the browser's own voice-activity barge-in).
SERVER_BARGE_IN_WORDS = 3
PLAYBACK_SLACK_S = 1.0  # network and buffering between sending audio and the browser playing it
STALE_VAD_S = 0.5
STT_MAX_RECONNECTS = 3  # in a row, without a transcript in between  # a VAD speech end this far before the last word's end belongs to older speech


class ProviderStartError(Exception):
    def __init__(self, who: str, exc: BaseException):
        super().__init__(describe_start_error(who, exc))


def describe_start_error(who: str, exc: BaseException) -> str:
    """A provider refusing the connection, in words a person can act on."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        return f"{who} could not connect: {exc}"
    if status == 402:
        return f"{who} is out of credits (HTTP 402). Top up the account or enable overages."
    if status in (401, 403):
        return f"{who} rejected the API key (HTTP {status}). Check the key in .env."
    if status == 429:
        return f"{who} is rate limiting this account (HTTP 429). Try again shortly."
    return f"{who} refused the connection (HTTP {status})."


def speech_truncated(reply: str, audio_s: float, interrupted: bool) -> bool:
    """The reply's audio is far shorter than its text: under a quarter of ~300 ms per word.
    An interrupted reply is short on purpose."""
    words = len(reply.split())
    return bool(words >= 4 and not interrupted and audio_s < 0.25 * 0.3 * words)


def speech_end_s(vad_end: float | None, words_end: float | None) -> float | None:
    """When the user stopped talking, on the audio clock. The VAD is more precise than word
    timings, but it can miss a short, soft word ("okay"); its time then belongs to the previous
    utterance and would make the turn look many seconds slow."""
    if vad_end is None:
        return words_end
    if words_end is not None and vad_end < words_end - STALE_VAD_S:
        return words_end
    return vad_end


@dataclass
class Turn:
    id: int
    user_text: str
    speech_end: float  # perf_counter at the end of the user's speech
    committed_at: float
    ctx: str = ""
    reply: str = ""  # everything sent to TTS, filler included
    words: list[str] = field(default_factory=list)
    starts: list[float] = field(default_factory=list)
    first_tts_text_at: float | None = None
    first_audio_at: float | None = None
    audio_bytes: int = 0
    tools: list[str] = field(default_factory=list)
    filler: str | None = None
    tts_error: str | None = None
    finished: bool = False
    interrupted: bool = False
    record: TurnRecord = field(default_factory=TurnRecord)
    client: dict[str, Any] = field(default_factory=dict)
    db_row: int | None = None
    task: asyncio.Task | None = None

    @property
    def speaking(self) -> bool:
        return self.first_audio_at is not None and not self.finished

    def audible(self, sample_rate: int, now: float | None = None) -> bool:
        """Still being heard: audio was sent and the browser has not finished playing it. A fast
        reply is fully generated long before it finishes playing. Clients that do not report
        playback are covered by the audio's own length."""
        if self.first_audio_at is None or self.interrupted or self.client.get("playback_finished"):
            return False
        audio_s = self.audio_bytes / 2 / sample_rate
        return (now or time.perf_counter()) - self.first_audio_at < audio_s + PLAYBACK_SLACK_S


class VoiceSession:
    def __init__(self, ws: WebSocket, agent: Agent, db: Database, cfg: TurnConfig, *,
                 stt_factory: Callable[[int], Awaitable[STTStream]],
                 tts_factory: Callable[[], Awaitable[TTSStream]],
                 tts_sample_rate: int = 24000, channel: str = "web", commit_hash: str | None = None,
                 silence_ms: int = 700, unfinished_silence_ms: int = 2500, use_vad: bool = True):
        self.ws, self.agent, self.db, self.cfg = ws, agent, db, cfg
        self.stt_factory, self.tts_factory = stt_factory, tts_factory
        self.tts_sample_rate = tts_sample_rate
        self.channel, self.commit_hash = channel, commit_hash
        self.mode, self.session_id, self.source = "standard", None, "mic"
        self.sample_rate = 16000
        self.stt: STTStream | None = None
        self.tts: TTSStream | None = None
        self.history: list[dict] = []
        self.pending: list[str] = []
        self.pending_end_s: float | None = None
        self.turn_no = 0
        self.active: Turn | None = None
        self.turns: dict[int, Turn] = {}
        self.audio_bytes = 0
        self.clock: deque[tuple[float, float]] = deque(maxlen=4000)  # (audio seconds received, perf_counter)
        # Turn-taking: the turn ends after `silence_ms` of silence heard by the server's VAD, or on
        # the speech-to-text provider's own end-of-speech signal, whichever comes first.
        self.silence_ms, self.unfinished_silence_ms = silence_ms, unfinished_silence_ms
        self.use_vad = use_vad
        self.vad: SileroVAD | None = None
        self.interim = ""  # the not-yet-final tail of the user's words
        self.stt_offset_s = 0.0  # audio already sent when the current speech-to-text stream opened
        self.stt_reconnects = 0
        self.finalizing = False  # silence timeout reached; commit as soon as the final transcript lands
        self.finalize_sent = False
        # Deepgram takes ~400 ms to answer a Finalize, so ask early and decide at the timeout.
        self.prefinalize_ms = min(300, silence_ms)
        self._send_lock = asyncio.Lock()
        self.closed = False

    # ------------------------------------------------------------ plumbing

    async def send(self, msg: dict) -> None:
        if self.closed:
            return
        async with self._send_lock:
            with contextlib.suppress(Exception):
                await self.ws.send_text(json.dumps(msg))

    async def send_audio(self, turn: Turn, pcm: bytes) -> None:
        if self.closed:
            return
        async with self._send_lock:
            with contextlib.suppress(Exception):
                await self.ws.send_bytes(struct.pack("<I", turn.id) + pcm)

    def wall_at(self, audio_s: float | None) -> float:
        """perf_counter time at which the audio at `audio_s` (audio clock) reached the server."""
        if audio_s is None or not self.clock:
            return time.perf_counter()
        ends = [a for a, _ in self.clock]
        i = bisect.bisect_left(ends, audio_s)
        if i >= len(self.clock):
            return self.clock[-1][1]
        chunk_end, arrived = self.clock[i]
        return arrived - (chunk_end - audio_s)

    # ------------------------------------------------------------ lifecycle

    async def run(self, start: dict | None = None) -> None:
        try:
            start = start if start is not None else json.loads(await self.ws.receive_text())
            if start.get("type") != "start":
                await self.send({"type": "error", "message": "The first message must be {\"type\": \"start\"}."})
                return
            self.sample_rate = int(start.get("sample_rate", 16000))
            self.mode = start.get("mode", "standard")
            self.session_id = start.get("session_id") or uuid.uuid4().hex[:8]
            self.source = start.get("source", "mic")
            stt, tts = await asyncio.gather(self.stt_factory(self.sample_rate), self.tts_factory(),
                                            return_exceptions=True)
            failed = next((ProviderStartError(who, r) for who, r in
                           (("Speech-to-text (Deepgram)", stt), ("Text-to-speech (Cartesia)", tts))
                           if isinstance(r, BaseException)), None)
            if failed is not None:
                for ok in (stt, tts):  # don't leak the provider connection that did open
                    if not isinstance(ok, BaseException):
                        with contextlib.suppress(Exception):
                            await ok.close()
                raise failed
            self.stt, self.tts = stt, tts
            if self.use_vad and self.sample_rate == 16000:
                self.vad = SileroVAD()
        except Exception as exc:
            log.error("Voice session could not start: %s", exc)
            await self.send({"type": "error", "message": f"Voice is unavailable: {exc}"})
            with contextlib.suppress(Exception):
                await self.ws.close(code=1011)
            return

        await self.send({"type": "ready", "session_id": self.session_id, "sample_rate_out": self.tts_sample_rate})
        await self.send({"type": "state", "state": "listening"})
        stt_task = asyncio.create_task(self._stt_loop())
        try:
            await self._client_loop()
        finally:
            self.closed = True
            stt_task.cancel()
            if self.active and self.active.task:
                self.active.task.cancel()
            for closer in (self.stt, self.tts):
                with contextlib.suppress(Exception):
                    await closer.close()

    async def _client_loop(self) -> None:
        while True:
            try:
                msg = await self.ws.receive()
            except WebSocketDisconnect:
                return
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("bytes"):
                pcm = msg["bytes"]
                self.audio_bytes += len(pcm)
                self.clock.append((self.audio_bytes / (2 * self.sample_rate), time.perf_counter()))
                await self.stt.send_audio(pcm)
                if self.vad is not None:
                    for kind, _ in self.vad.process(pcm):
                        if kind == "start":
                            self.finalizing = self.finalize_sent = False
                    await self._check_silence()
            elif msg.get("text"):
                data = json.loads(msg["text"])
                kind = data.get("type")
                if kind == "stop":
                    return
                if kind == "barge_in":
                    await self.interrupt(data.get("played_ms"), reason="client",
                                         stop_latency_ms=data.get("stop_latency_ms"), turn_id=data.get("turn"))
                elif kind == "playback":
                    await self._on_playback(data)
                elif kind == "text" and str(data.get("text", "")).strip():
                    await self.commit(str(data["text"]).strip(), time.perf_counter())

    async def _stt_loop(self) -> None:
        """Feed transcripts to the turn logic. If the speech-to-text stream dies mid-call (Deepgram
        "keepalive ping timeout" on 13 November), open a new one instead of going deaf."""
        failures = 0
        while not self.closed:
            async for ev in self.stt.events():
                if ev.kind == "error":
                    log.warning("Speech-to-text stream failed: %s", ev.text)
                    continue
                failures = 0
                if ev.audio_end_s is not None and self.stt_offset_s:
                    ev = replace(ev, audio_end_s=ev.audio_end_s + self.stt_offset_s)
                await self._on_stt(ev)
            if self.closed:
                return
            failures += 1
            if failures > STT_MAX_RECONNECTS:
                await self.send({"type": "error", "message": "Speech recognition stopped and could not reconnect."})
                return
            await asyncio.sleep(0.2 * failures)
            try:
                old, self.stt = self.stt, await self.stt_factory(self.sample_rate)
            except Exception as exc:
                log.warning("Speech-to-text reconnect %d failed: %s", failures, exc)
                continue
            with contextlib.suppress(Exception):
                await old.close()
            # The new stream's audio clock starts at zero; ours keeps counting.
            self.stt_offset_s = self.audio_bytes / (2 * self.sample_rate)
            self.interim, self.finalizing, self.finalize_sent = "", False, False
            self.stt_reconnects += 1
            log.warning("Speech-to-text reconnected (%d so far this call)", self.stt_reconnects)

    async def _check_silence(self) -> None:
        """End the turn on our own silence timeout instead of waiting for the provider's, which
        arrives about half a second later. A sentence that stops mid-way gets longer."""
        vad = self.vad
        if vad is None or vad.speaking or self.finalizing or not (self.pending or self.interim):
            return
        silence = vad.silence_ms()
        text = " ".join(self.pending + [self.interim])
        limit = self.unfinished_silence_ms if sounds_unfinished(text) else self.silence_ms
        if self.interim and not self.finalize_sent and silence >= self.prefinalize_ms:
            self.finalize_sent = True
            await self.stt.finalize()  # get the last words' final transcript ready early
        if silence < limit:
            return
        if self.interim:
            self.finalizing = True  # commit when the final transcript arrives
        else:
            await self._commit_pending(None)

    async def _commit_pending(self, word_end_s: float | None) -> None:
        text = " ".join(self.pending).strip()
        if not text:
            return
        words_end = word_end_s if word_end_s is not None else self.pending_end_s
        end_s = speech_end_s(self.vad.last_speech_end_s if self.vad else None, words_end)
        self.pending, self.pending_end_s, self.interim = [], None, ""
        self.finalizing = self.finalize_sent = False
        await self.send({"type": "transcript", "text": text, "final": True})
        await self.commit(text, self.wall_at(end_s))

    async def _on_stt(self, ev: STTEvent) -> None:
        if ev.kind == "interim":
            self.interim = ev.text
            await self.send({"type": "transcript", "text": " ".join(self.pending + [ev.text]), "final": False})
            turn = self.active
            if (turn and (turn.speaking or turn.audible(self.tts_sample_rate))
                    and len(ev.text.split()) >= SERVER_BARGE_IN_WORDS):
                await self.interrupt(None, reason="server")
        elif ev.kind == "final":
            self.pending.append(ev.text)
            self.pending_end_s = ev.audio_end_s
            self.interim = ""
            await self.send({"type": "transcript", "text": " ".join(self.pending), "final": False})
            if self.finalizing and not (self.vad and self.vad.speaking):
                text = " ".join(self.pending)
                if (self.vad and sounds_unfinished(text)
                        and self.vad.silence_ms() < self.unfinished_silence_ms):
                    # The timeout was judged on the interim text ("...Headphones"); the final one
                    # ends mid-sentence ("...Headphones in"): wait for the longer timeout instead.
                    self.finalizing = False
                    return
                await self._commit_pending(ev.audio_end_s)  # the timeout already passed
        elif ev.kind == "end_of_turn" and self.pending:
            vad = self.vad
            if vad is not None and (vad.speaking or (sounds_unfinished(" ".join(self.pending))
                                                     and vad.silence_ms() < self.unfinished_silence_ms)):
                return  # mid-sentence pause: our own longer timeout decides
            await self._commit_pending(self.pending_end_s if self.pending_end_s is not None else ev.audio_end_s)
        elif ev.kind == "error":
            await self.send({"type": "error", "message": f"Speech recognition: {ev.text}"})

    # ------------------------------------------------------------ turns

    async def commit(self, text: str, speech_end: float) -> None:
        prev = self.active
        if prev and not prev.finished:
            if prev.first_audio_at is None:
                # The user kept talking before the agent said anything: treat it as one turn.
                await self._cancel(prev)
                if self.history and self.history[-1] == {"role": "user", "content": prev.user_text}:
                    self.history.pop()
                text = f"{prev.user_text} {text}"
            else:
                await self.interrupt(None, reason="new_turn")
        elif prev and prev.audible(self.tts_sample_rate):
            await self.interrupt(None, reason="new_turn")
        self.turn_no += 1
        turn = Turn(self.turn_no, text, speech_end, time.perf_counter())
        self.turns[turn.id] = turn
        self.active = turn
        self.history.append({"role": "user", "content": text})
        turn.task = asyncio.create_task(self._respond(turn, list(self.history)))

    async def _respond(self, turn: Turn, history: list[dict]) -> None:
        turn.ctx = f"turn-{turn.id}-{uuid.uuid4().hex[:6]}"
        await self.send({"type": "state", "state": "thinking"})
        await self.tts.start(turn.ctx)
        pump = asyncio.create_task(self._pump_audio(turn))
        chunker = SentenceChunker()
        try:
            async for ev in self.agent.stream_turn(history, self.cfg, turn.record):
                if ev["type"] == "token":
                    text = clean_for_speech(ev["text"])
                    await self.send({"type": "agent_text", "turn": turn.id, "text": text})
                    for sentence in chunker.push(text):
                        await self._speak(turn, sentence)
                elif ev["type"] == "tool_call":
                    turn.tools.append(ev["name"])
                    await self.send({"type": "tool", "turn": turn.id, "name": ev["name"]})
                    preamble = chunker.flush()
                    if preamble.strip():
                        await self._speak(turn, preamble)  # the model's own "let me check" line
                    elif turn.first_tts_text_at is None:
                        # Nothing said yet: keep the line alive while the tool runs.
                        turn.filler = filler_for(ev["name"])
                        await self._speak(turn, turn.filler + " ")
                elif ev["type"] == "error":
                    await self.send({"type": "error", "message": ev["message"]})
            await self._speak(turn, chunker.flush())
            await self.tts.finish(turn.ctx)
            await pump
        except asyncio.CancelledError:
            pump.cancel()
            raise
        turn.finished = True
        self.history.append({"role": "assistant", "content": turn.reply.strip()})
        await self.send({"type": "audio_end", "turn": turn.id})
        await self._finish_metrics(turn)

    async def _speak(self, turn: Turn, text: str) -> None:
        if not text.strip():
            return
        if turn.reply and not turn.reply[-1].isspace() and not text[0].isspace():
            text = " " + text  # text from separate LLM rounds needs a space between
        if turn.first_tts_text_at is None:
            turn.first_tts_text_at = time.perf_counter()
        turn.reply += text
        await self.tts.push(turn.ctx, text)

    async def _pump_audio(self, turn: Turn) -> None:
        async for chunk in self.tts.chunks(turn.ctx):
            if chunk.kind == "audio":
                if turn.first_audio_at is None:
                    turn.first_audio_at = chunk.received_at or time.perf_counter()
                    await self.send({"type": "audio_start", "turn": turn.id, "sample_rate": self.tts_sample_rate})
                    await self.send({"type": "state", "state": "speaking"})
                turn.audio_bytes += len(chunk.audio)
                await self.send_audio(turn, chunk.audio)
            elif chunk.kind == "timestamps":
                turn.words += chunk.words
                turn.starts += chunk.starts
            elif chunk.kind == "error":
                # Recorded and logged: a reply that goes silent with no trace cannot be debugged
                # (QA-023: R19 had 0 ms of audio, R20 stopped mid-word, nothing in the log).
                turn.tts_error = chunk.message or "unknown"
                log.warning("Text-to-speech error on turn %d after %d ms of audio: %s", turn.id,
                            round(turn.audio_bytes / 2 / self.tts_sample_rate * 1000), chunk.message)
                await self.send({"type": "error", "message": f"Speech output: {chunk.message}"})
                return

    async def _cancel(self, turn: Turn) -> None:
        turn.interrupted = True
        if turn.task and not turn.task.done():
            turn.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await turn.task
        if turn.ctx:
            await self.tts.cancel(turn.ctx)
        turn.finished = True

    async def interrupt(self, played_ms: float | None, *, reason: str, stop_latency_ms: float | None = None,
                        turn_id: int | None = None) -> None:
        """Stop the agent mid-reply and keep only what the user heard in the history."""
        turn = self.active
        if not turn or (turn_id is not None and turn_id != turn.id):
            return
        if turn.finished:
            if turn.audible(self.tts_sample_rate):
                await self._interrupt_playback(turn, played_ms, reason, stop_latency_ms)
            return
        await self._cancel(turn)
        if played_ms is not None and turn.words:
            spoken = heard_text(turn.words, turn.starts, played_ms)
        else:
            spoken = turn.reply.strip()
        if spoken:
            self.history.append({"role": "assistant", "content": spoken + "..."})
        turn.client.update({"interrupted_by": reason, "played_ms": played_ms, "stop_latency_ms": stop_latency_ms,
                            "heard_text": spoken})
        await self.send({"type": "clear", "turn": turn.id})
        await self.send({"type": "state", "state": "listening"})
        await self._finish_metrics(turn)

    async def _interrupt_playback(self, turn: Turn, played_ms: float | None, reason: str,
                                  stop_latency_ms: float | None) -> None:
        """The reply was fully generated but the user cut in while it was still playing: the
        history must hold only what they heard, not the whole reply."""
        turn.interrupted = True
        if played_ms is None:  # estimate from when the audio started reaching the browser
            played_ms = (time.perf_counter() - turn.first_audio_at) * 1000
        spoken = heard_text(turn.words, turn.starts, played_ms) if turn.words else turn.reply.strip()
        full = {"role": "assistant", "content": turn.reply.strip()}
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i] == full:
                if spoken:
                    self.history[i] = {"role": "assistant", "content": spoken + "..."}
                else:
                    del self.history[i]
                break
        turn.client.update({"interrupted_by": reason, "played_ms": round(played_ms), "stop_latency_ms": stop_latency_ms,
                            "heard_text": spoken})
        await self.send({"type": "clear", "turn": turn.id})
        await self.send({"type": "state", "state": "listening"})
        if turn.db_row is not None:
            self.db.update_turn_extra(turn.db_row, {"voice": self.metrics(turn), "client": turn.client})

    async def _on_playback(self, data: dict) -> None:
        turn = self.turns.get(data.get("turn"))
        if not turn:
            return
        if data.get("event") == "started" and data.get("client_response_ms") is not None:
            turn.client["client_response_ms"] = data["client_response_ms"]
        if data.get("event") == "finished":
            turn.client["playback_finished"] = True
            if turn is self.active and turn.finished:
                await self.send({"type": "state", "state": "listening"})
        if turn.db_row is not None:
            self.db.update_turn_extra(turn.db_row, {"client": turn.client})

    # ------------------------------------------------------------ metrics

    def metrics(self, turn: Turn) -> dict:
        ms = lambda a, b: round((a - b) * 1000, 1) if a is not None and b is not None else None  # noqa: E731
        t = turn.record.timings
        llm_first = turn.committed_at + t["llm_first_token_ms"] / 1000 if "llm_first_token_ms" in t else None
        return {
            "end_of_turn_ms": ms(turn.committed_at, turn.speech_end),
            "retrieval_ms": t.get("retrieval_ms"),
            "llm_first_token_ms": t.get("llm_first_token_ms"),
            "llm_first_token_from_speech_end_ms": ms(llm_first, turn.speech_end),
            "tts_first_byte_ms": ms(turn.first_audio_at, turn.first_tts_text_at),
            "server_response_ms": ms(turn.first_audio_at, turn.speech_end),
            "audio_ms": round(turn.audio_bytes / 2 / self.tts_sample_rate * 1000),
            "tools": turn.tools,
            "filler": turn.filler,
            "interrupted": turn.interrupted,
            "stt_reconnects": self.stt_reconnects,
            "tts_error": turn.tts_error,
            "speech_truncated": speech_truncated(turn.reply, turn.audio_bytes / 2 / self.tts_sample_rate,
                                                 turn.interrupted),
        }

    async def _finish_metrics(self, turn: Turn) -> None:
        m = self.metrics(turn)
        await self.send({"type": "metrics", "turn": turn.id, **m})
        if turn.db_row is None:
            turn.db_row = self.db.log_turn(
                channel=self.channel, mode=self.mode, session_id=self.session_id, user_text=turn.user_text,
                reply=turn.reply.strip(),
                tool_calls=[{"name": c["name"], "args": c["args"]} for c in turn.record.tool_calls],
                retrieval_ms=m["retrieval_ms"], llm_first_token_ms=m["llm_first_token_ms"],
                total_ms=m["server_response_ms"], model=turn.record.model_used or self.cfg.model,
                commit_hash=self.commit_hash,
                extra={"voice": m, "client": turn.client, "source": self.source, "error": turn.record.error,
                       "fallback_reason": turn.record.fallback_reason})
