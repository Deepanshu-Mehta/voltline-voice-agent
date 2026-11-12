"""HTTP API.

  GET  /health                  status, retrieval mode, model
  POST /retrieve                {query, top_k?} -> top knowledge-base chunks with their source page
  POST /chat                    {messages, stream?} -> Server-Sent Events (or JSON when stream=false)
  GET  /tools                   tool schemas
  POST /tools/{name}            tool arguments as JSON -> tool result
  GET  /turns/recent            last logged turns (feeds the benchmarks page later)

Run: uv run uvicorn --factory app.main:create_app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import Agent, Fallback, TurnConfig, TurnRecord, with_overrides
from .config import REPO_ROOT, Settings, get_settings
from .content import load_orders, load_pages, load_products
from .db import Database
from .retrieval import Retriever
from .tools import TOOLS, ToolContext, run_tool
from .voice.session import VoiceSession

log = logging.getLogger("voltline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class _MissingKeyLLM:
    """Stands in for the LLM client when no API key is configured, so the server still starts
    and /retrieve and the tools still work."""

    class _Completions:
        async def create(self, **_: Any):
            raise RuntimeError("No LLM API key configured. Set the key for LLM_PROVIDER (e.g. GROQ_API_KEY) in .env.")

    def __init__(self):
        self.chat = type("Chat", (), {"completions": self._Completions()})()


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=60)
    stream: bool = True
    session_id: str | None = None
    channel: str = "text"
    mode: str = "standard"
    # Optional per-request overrides, used by the evals and (later) Low Latency mode.
    model: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=10)
    history_turns: int | None = Field(default=None, ge=1, le=30)


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=4, ge=1, le=10)


def _commit_hash() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, timeout=2).stdout.strip() or None
    except Exception:
        return None


def create_app(settings: Settings | None = None, llm: Any = None, retriever: Retriever | None = None,
               stt_factory: Any = None, tts_factory: Any = None) -> FastAPI:
    """Build the app. Tests inject fake LLM, speech-to-text and text-to-speech factories."""
    settings = settings or get_settings()
    t0 = time.perf_counter()

    products = load_products(settings.content_dir)
    pages = load_pages(settings.content_dir)
    db = Database(settings.db_path)
    db.seed_orders(load_orders(settings.content_dir))
    retriever = retriever or Retriever(pages, products, dense=settings.retrieval_dense,
                                       embedding_model=settings.embedding_model)
    if llm is None:
        if settings.resolved_llm_key:
            from openai import AsyncOpenAI

            llm = AsyncOpenAI(api_key=settings.resolved_llm_key, base_url=settings.resolved_llm_base_url,
                              timeout=settings.llm_timeout_s,
                              max_retries=settings.llm_max_retries if settings.llm_max_retries is not None
                              else (0 if settings.fallback_llm else 1))
        else:
            log.warning("No LLM API key set: /chat will return a fallback reply until one is configured.")
            llm = _MissingKeyLLM()

    tool_ctx = ToolContext(db=db, products=products, retriever=retriever)
    default_cfg = TurnConfig(model=settings.resolved_llm_model, max_tokens=settings.llm_max_tokens,
                             reasoning_effort=settings.resolved_reasoning_effort, history_turns=settings.history_turns,
                             top_k=settings.retrieval_top_k, max_tool_rounds=settings.max_tool_rounds)
    fallback = None
    if settings.fallback_llm and not isinstance(llm, _MissingKeyLLM) and not getattr(llm, "is_fake", False):
        from openai import AsyncOpenAI

        key, base_url, fb_model, fb_effort = settings.fallback_llm
        fallback = Fallback(AsyncOpenAI(api_key=key, base_url=base_url, timeout=settings.llm_timeout_s, max_retries=0),
                            fb_model, fb_effort)
    agent = Agent(llm, retriever, tool_ctx, default_cfg, fallback=fallback)
    commit = _commit_hash()
    log.info("Ready in %.1f s: %d products, %d chunks, retrieval=%s, model=%s", time.perf_counter() - t0,
             len(products), len(retriever.chunks), retriever.mode, settings.resolved_llm_model)

    app = FastAPI(title="Voltline voice agent API", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"],
                       allow_headers=["*"])
    app.state.agent, app.state.db, app.state.retriever = agent, db, retriever

    @app.get("/health")
    def health():
        return {"ok": True, "retrieval": retriever.mode, "chunks": len(retriever.chunks), "model": settings.resolved_llm_model, "provider": settings.llm_provider, "fallback": fallback.model if fallback else None, "voice_configured": stt_factory is not None and tts_factory is not None,
                "llm_configured": not isinstance(llm, _MissingKeyLLM), "commit": commit}

    @app.post("/retrieve")
    def retrieve(req: RetrieveRequest):
        t = time.perf_counter()
        results = retriever.search(req.query, req.top_k)
        return {"query": req.query, "ms": round((time.perf_counter() - t) * 1000, 1),
                "chunks": [c.as_dict(score) for c, score in results]}

    @app.get("/tools")
    def list_tools():
        return [{"name": n, **schema} for n, (_, schema) in TOOLS.items()]

    @app.post("/tools/{name}")
    def call_tool(name: str, args: dict[str, Any]):
        if name not in TOOLS:
            raise HTTPException(404, f"Unknown tool {name}")
        return run_tool(tool_ctx, name, args)

    @app.get("/turns/recent")
    def recent_turns(limit: int = 20, session_id: str | None = None):
        return db.recent_turns(min(max(limit, 1), 200), session_id)

    @app.get("/turns/summary")
    def turns_summary(hours: float = 168):
        return db.turn_summary(min(max(hours, 1), 24 * 90))

    if stt_factory is None and settings.deepgram_api_key:
        from .voice.stt import DeepgramSTT

        # Product names from the catalog, plus the brand, so speech-to-text spells them as the site does.
        keyterms = ["Voltline", *(p.name for p in products)]

        async def stt_factory(sample_rate: int):
            return await DeepgramSTT(settings.deepgram_api_key, model=settings.stt_model, language=settings.stt_language,
                                     sample_rate=sample_rate, endpointing_ms=settings.stt_endpointing_ms,
                                     utterance_end_ms=settings.stt_utterance_end_ms, keyterms=keyterms).connect()

    if tts_factory is None and settings.cartesia_api_key:
        from .voice.tts import CartesiaTTS

        async def tts_factory():
            return await CartesiaTTS(settings.cartesia_api_key, version=settings.cartesia_version,
                                     model=settings.tts_model, voice_id=settings.tts_voice_id,
                                     sample_rate=settings.tts_sample_rate).connect()

    @app.websocket("/voice")
    async def voice(ws: WebSocket):
        await ws.accept()
        if stt_factory is None or tts_factory is None:
            await ws.send_text(json.dumps({"type": "error", "message":
                "Voice is not configured on this server: set DEEPGRAM_API_KEY and CARTESIA_API_KEY."}))
            await ws.close()
            return
        session = VoiceSession(ws, agent, db, default_cfg, stt_factory=stt_factory, tts_factory=tts_factory,
                               tts_sample_rate=settings.tts_sample_rate, commit_hash=commit,
                               silence_ms=settings.stt_endpointing_ms)
        await session.run()

    @app.post("/chat")
    async def chat(req: ChatRequest):
        cfg = with_overrides(default_cfg, model=req.model, top_k=req.top_k, history_turns=req.history_turns)
        messages = [m.model_dump() for m in req.messages]
        rec = TurnRecord()

        def log_turn():
            db.log_turn(channel=req.channel, mode=req.mode, session_id=req.session_id,
                        user_text=messages[-1]["content"], reply=rec.reply,
                        tool_calls=[{"name": c["name"], "args": c["args"]} for c in rec.tool_calls],
                        retrieval_ms=rec.timings.get("retrieval_ms"),
                        llm_first_token_ms=rec.timings.get("llm_first_token_ms"),
                        total_ms=rec.timings.get("total_ms"), model=cfg.model, commit_hash=commit,
                        extra={"timings": rec.timings, "usage": rec.usage, "error": rec.error,
                               "model_used": rec.model_used, "fallback_reason": rec.fallback_reason})

        if not req.stream:
            events = [e async for e in agent.stream_turn(messages, cfg, rec)]
            log_turn()
            return events[-1]

        async def sse():
            async for event in agent.stream_turn(messages, cfg, rec):
                yield f"data: {json.dumps(event)}\n\n"
            log_turn()

        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
