"""The agent brain every channel reuses: system prompt + retrieval + tools + a streamed LLM.

One turn:
  1. Retrieve knowledge-base chunks for the customer's message (before the LLM runs, so fact
     questions need no tool round trip).
  2. Stream the LLM reply. If it calls tools, run them, append the results, and stream again
     (up to max_tool_rounds).
  3. Emit timing for every stage, so later phases can log them to the turns table.

The system prompt and tool list come first in every request and stay byte-identical within a
day, so provider-side prompt caching can hit. Retrieved text is attached to the latest user
message only, after the cached prefix.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator

from .retrieval import Retriever, format_context
from .tools import CHAT_TOOLS, ToolContext, openai_tool_schemas, run_tool

PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"
FALLBACK_REPLY = "Sorry, I'm having trouble answering right now. Could you try again in a moment?"


@dataclass(frozen=True)
class Fallback:
    """A second provider for when the main one fails before answering (rate limit, outage)."""
    client: Any
    model: str
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class TurnConfig:
    model: str
    max_tokens: int = 300
    reasoning_effort: str | None = None
    history_turns: int = 10
    top_k: int = 4
    max_tool_rounds: int = 3
    tools: tuple[str, ...] = tuple(CHAT_TOOLS)


@dataclass
class _ToolCall:
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class TurnRecord:
    """Everything that happened in one turn; the evals and the turns table read this."""
    reply: str = ""
    query: str = ""
    sources: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    model_used: str | None = None
    fallback_reason: str | None = None


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text().strip()


def trim_history(messages: list[dict], turns: int) -> list[dict]:
    """Keep the last `turns` user turns (and the replies between them)."""
    clean = [{"role": m["role"], "content": str(m.get("content") or "")}
             for m in messages if m.get("role") in ("user", "assistant")]
    user_idx = [i for i, m in enumerate(clean) if m["role"] == "user"]
    if len(user_idx) > turns:
        clean = clean[user_idx[-turns]:]
    return clean


def retrieval_query(messages: list[dict]) -> str:
    """The latest user message; a short follow-up ("is it waterproof?") is prefixed with the
    previous user message so retrieval knows what "it" is."""
    users = [m["content"] for m in messages if m["role"] == "user"]
    if not users:
        return ""
    if len(users[-1].split()) < 8 and len(users) > 1:
        return f"{users[-2]} {users[-1]}"
    return users[-1]


class Agent:
    def __init__(self, llm: Any, retriever: Retriever, tool_ctx: ToolContext, default: TurnConfig,
                 fallback: Fallback | None = None):
        self.llm = llm  # an openai.AsyncOpenAI-compatible client
        self.fallback = fallback
        self.retriever = retriever
        self.tool_ctx = tool_ctx
        self.default = default
        self.base_prompt = load_system_prompt()

    def system_prompt(self, today: dt.date | None = None) -> str:
        today = today or self.tool_ctx.today()
        return f"{self.base_prompt}\n\nToday is {today.strftime('%A, %B')} {today.day}, {today.year}."

    def build_messages(self, history: list[dict], context: str) -> list[dict]:
        *earlier, last = history
        content = (f"Knowledge base excerpts (use only what is relevant):\n{context}\n\n"
                   f"Customer said: {last['content']}")
        return [{"role": "system", "content": self.system_prompt()}, *earlier, {"role": "user", "content": content}]

    async def stream_turn(self, messages: list[dict], cfg: TurnConfig | None = None,
                          record: TurnRecord | None = None) -> AsyncIterator[dict]:
        """Yield events: retrieval, token, tool_call, tool_result, done (or error then done)."""
        cfg = cfg or self.default
        rec = record if record is not None else TurnRecord()
        t0 = time.perf_counter()
        ms = lambda: round((time.perf_counter() - t0) * 1000, 1)  # noqa: E731

        history = trim_history(messages, cfg.history_turns)
        if not history or history[-1]["role"] != "user":
            rec.error = "The last message must be from the user."
            yield {"type": "error", "message": rec.error}
            return

        rec.query = retrieval_query(history)
        results = await asyncio.to_thread(self.retriever.search, rec.query, cfg.top_k)
        rec.timings["retrieval_ms"] = ms()
        rec.sources = [c.url for c, _ in results]
        yield {"type": "retrieval", "ms": rec.timings["retrieval_ms"], "sources": rec.sources}

        convo = self.build_messages(history, format_context(results))
        tools = openai_tool_schemas(list(cfg.tools)) if cfg.tools else None
        reply_parts: list[str] = []
        tool_ms = 0.0
        client, model, effort = self.llm, cfg.model, cfg.reasoning_effort

        try:
            for round_no in range(cfg.max_tool_rounds + 1):
                kwargs: dict[str, Any] = {"model": model, "messages": convo, "stream": True,
                                          "stream_options": {"include_usage": True},
                                          "max_completion_tokens": cfg.max_tokens}
                if tools and round_no < cfg.max_tool_rounds:
                    kwargs["tools"] = tools
                if effort:
                    kwargs["reasoning_effort"] = effort
                rec.timings.setdefault("llm_request_ms", ms())
                try:
                    stream = await client.chat.completions.create(**kwargs)
                except Exception as exc:
                    if self.fallback is None or client is self.fallback.client:
                        raise
                    # Switch provider for the rest of this turn, so tool rounds stay consistent.
                    rec.fallback_reason = f"{type(exc).__name__}: {str(exc)[:160]}"
                    fb = self.fallback
                    client, model, effort = fb.client, fb.model, fb.reasoning_effort
                    kwargs["model"] = model
                    kwargs.pop("reasoning_effort", None)
                    if effort:
                        kwargs["reasoning_effort"] = effort
                    stream = await client.chat.completions.create(**kwargs)
                rec.model_used = model

                calls: dict[int, _ToolCall] = {}
                round_text: list[str] = []
                async for chunk in stream:
                    if getattr(chunk, "usage", None):
                        self._add_usage(rec, chunk.usage)
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        rec.timings.setdefault("llm_first_token_ms", ms())
                        round_text.append(delta.content)
                        yield {"type": "token", "text": delta.content}
                    for tc in delta.tool_calls or []:
                        rec.timings.setdefault("llm_first_token_ms", ms())
                        call = calls.setdefault(tc.index, _ToolCall())
                        call.id = tc.id or call.id
                        if tc.function and tc.function.name:
                            call.name = tc.function.name
                        if tc.function and tc.function.arguments:
                            call.arguments += tc.function.arguments
                reply_parts.extend(round_text)

                if not calls:
                    break

                ordered = [calls[i] for i in sorted(calls)]
                convo.append({"role": "assistant", "content": "".join(round_text) or None, "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments or "{}"}}
                    for c in ordered]})
                for c in ordered:
                    try:
                        args = json.loads(c.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    yield {"type": "tool_call", "id": c.id, "name": c.name, "args": args}
                    t_tool = time.perf_counter()
                    result = await asyncio.to_thread(run_tool, self.tool_ctx, c.name, args)
                    took = (time.perf_counter() - t_tool) * 1000
                    tool_ms += took
                    rec.tool_calls.append({"name": c.name, "args": args, "result": result, "ms": round(took, 1)})
                    yield {"type": "tool_result", "id": c.id, "name": c.name, "result": result, "ms": round(took, 1)}
                    convo.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)})
                rec.timings["llm_rounds"] = round_no + 2
        except Exception as exc:  # provider down, bad key, timeout: speak a fallback, never go silent
            rec.error = f"{type(exc).__name__}: {exc}"
            yield {"type": "error", "message": rec.error}
            if not reply_parts:
                reply_parts.append(FALLBACK_REPLY)
                yield {"type": "token", "text": FALLBACK_REPLY}

        rec.reply = "".join(reply_parts).strip()
        rec.timings["tool_ms"] = round(tool_ms, 1)
        rec.timings["total_ms"] = ms()
        rec.timings.setdefault("llm_rounds", 1)
        yield {"type": "done", "reply": rec.reply, "timings": rec.timings, "query": rec.query, "sources": rec.sources,
               "tool_calls": rec.tool_calls, "usage": rec.usage, "error": rec.error, "model": rec.model_used,
               "fallback_reason": rec.fallback_reason}

    @staticmethod
    def _add_usage(rec: TurnRecord, usage: Any) -> None:
        for key in ("prompt_tokens", "completion_tokens"):
            rec.usage[key] = rec.usage.get(key, 0) + (getattr(usage, key, 0) or 0)
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details else 0
        rec.usage["cached_tokens"] = rec.usage.get("cached_tokens", 0) + (cached or 0)


def with_overrides(cfg: TurnConfig, **overrides: Any) -> TurnConfig:
    return replace(cfg, **{k: v for k, v in overrides.items() if v is not None})
