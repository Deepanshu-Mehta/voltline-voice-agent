"""System B (speech-to-speech) over the same /voice protocol, with a scripted fake Live session."""

import asyncio
import contextlib
import json
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import FakeLLM
from .test_voice import collect_until


def msg(**kw):
    base = dict(usage_metadata=None, go_away=None, tool_call=None, server_content=None)
    return NS(**{**base, **kw})


def content(**kw):
    base = dict(input_transcription=None, interrupted=None, model_turn=None, output_transcription=None,
                turn_complete=None)
    return msg(server_content=NS(**{**base, **kw}))


class FakeLive:
    """Answers a typed question with a tool call, then speaks the tool's result."""

    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.tool_responses = []
        self.system_prompt = None
        self.tool_names = []

    async def send_realtime_input(self, **_):
        pass

    async def send_client_content(self, turns, turn_complete):
        await self.q.put(msg(tool_call=NS(function_calls=[
            NS(id="c1", name="get_order_status", args={"order_id": "4512"})])))

    async def send_tool_response(self, function_responses):
        self.tool_responses.extend(function_responses)
        status = function_responses[0].response["result"]["status"]
        await self.q.put(content(output_transcription=NS(text=f"Order 4 5 1 2 has {status}.")))
        await self.q.put(content(model_turn=NS(parts=[NS(inline_data=NS(data=b"\x00\x00" * 2400))])))
        await self.q.put(content(turn_complete=True))

    async def receive(self):
        while True:
            m = await self.q.get()
            yield m
            if m.server_content and m.server_content.turn_complete:
                return


def make_s2s_client(settings, retriever, live):
    def factory(system_prompt, tool_schemas):
        live.system_prompt = system_prompt
        live.tool_names = [t["function"]["name"] for t in tool_schemas]

        @contextlib.asynccontextmanager
        async def cm():
            yield live

        return cm()

    app = create_app(settings, llm=FakeLLM([]), retriever=retriever, live_factory=factory)
    return TestClient(app)


def test_s2s_turn_uses_shared_prompt_and_tools(settings, retriever):
    live = FakeLive()
    client = make_s2s_client(settings, retriever, live)
    with client.websocket_connect("/voice") as ws:
        ws.send_text(json.dumps({"type": "start", "mode": "s2s", "session_id": "s2s-test"}))
        ready, _ = collect_until(ws, "ready")
        assert ready[-1]["mode"] == "s2s"
        ws.send_text(json.dumps({"type": "text", "text": "Where is order 4512?"}))
        msgs, frames = collect_until(ws, "metrics")
        ws.send_text(json.dumps({"type": "stop"}))

    kinds = [m["type"] for m in msgs]
    assert "tool" in kinds and "audio_start" in kinds and "audio_end" in kinds
    assert frames and int.from_bytes(frames[0][:4], "little") == 1  # same framing as the cascade
    assert msgs[-1]["interrupted"] is False and msgs[-1]["tools"] == ["get_order_status"]
    # Same prompt file and the same tool registry, plus knowledge-base search.
    assert "You are Volt" in live.system_prompt
    assert {"get_order_status", "check_stock", "search_knowledge_base"} <= set(live.tool_names)
    assert live.tool_responses[0].response["result"]["status"] == "shipped"

    row = client.get("/turns/recent", params={"session_id": "s2s-test"}).json()[0]
    assert row["mode"] == "s2s" and row["user_text"] == "Where is order 4512?"
    assert row["reply"] == "Order 4 5 1 2 has shipped."
    assert json.loads(row["tool_calls"]) == [{"name": "get_order_status", "args": {"order_id": "4512"}}]


def test_s2s_not_configured(settings, retriever):
    client = TestClient(create_app(settings, llm=FakeLLM([]), retriever=retriever))
    with client.websocket_connect("/voice") as ws:
        ws.send_text(json.dumps({"type": "start", "mode": "s2s"}))
        assert "Speech-to-speech is not configured" in json.loads(ws.receive_text())["message"]
