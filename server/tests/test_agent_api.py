import json

from fastapi.testclient import TestClient

from app.agent import FALLBACK_REPLY, Agent, TurnConfig, retrieval_query, trim_history
from app.main import create_app

from .conftest import FakeLLM


async def _run(agent, messages):
    return [e async for e in agent.stream_turn(messages)]


async def test_text_turn_streams_and_injects_context(tool_ctx, retriever):
    llm = FakeLLM(["Pulse Buds Pro cost $149.99."])
    agent = Agent(llm, retriever, tool_ctx, TurnConfig(model="fake"))
    events = await _run(agent, [{"role": "user", "content": "How much are the Pulse Buds Pro?"}])
    kinds = [e["type"] for e in events]
    assert kinds[0] == "retrieval" and kinds[-1] == "done" and "token" in kinds
    done = events[-1]
    assert done["reply"] == "Pulse Buds Pro cost $149.99."
    assert done["usage"]["cached_tokens"] == 64
    req = llm.requests[0]
    assert req["messages"][0]["role"] == "system"
    assert "Knowledge base excerpts" in req["messages"][-1]["content"]
    assert {t["function"]["name"] for t in req["tools"]} >= {"get_order_status", "check_stock"}


async def test_tool_round_trip(tool_ctx, retriever):
    llm = FakeLLM([[("get_order_status", json.dumps({"order_id": "4512"}))], "Order 4 5 1 2 has shipped."])
    agent = Agent(llm, retriever, tool_ctx, TurnConfig(model="fake"))
    events = await _run(agent, [{"role": "user", "content": "Where is order 4512?"}])
    done = events[-1]
    assert done["tool_calls"][0]["name"] == "get_order_status"
    assert done["tool_calls"][0]["result"]["status"] == "shipped"
    assert done["reply"] == "Order 4 5 1 2 has shipped."
    assert done["timings"]["llm_rounds"] == 2
    second = llm.requests[1]["messages"]
    assert second[-1]["role"] == "tool" and second[-2]["tool_calls"][0]["function"]["name"] == "get_order_status"


async def test_provider_failure_speaks_fallback(tool_ctx, retriever):
    class Broken:
        class chat:
            class completions:
                @staticmethod
                async def create(**_):
                    raise TimeoutError("provider timed out")

    agent = Agent(Broken, retriever, tool_ctx, TurnConfig(model="fake"))
    done = (await _run(agent, [{"role": "user", "content": "hi"}]))[-1]
    assert done["reply"] == FALLBACK_REPLY and "TimeoutError" in done["error"]


def test_history_and_followup_query():
    msgs = []
    for i in range(12):
        msgs += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    msgs.append({"role": "user", "content": "q12"})
    kept = trim_history(msgs, 4)
    assert kept[0] == {"role": "user", "content": "q9"} and kept[-1]["content"] == "q12"
    assert retrieval_query([{"role": "user", "content": "How much are the Horizon Headphones?"},
                            {"role": "assistant", "content": "$229.99."},
                            {"role": "user", "content": "Are they in stock?"}]).startswith("How much")


def test_api_endpoints(settings, retriever):
    llm = FakeLLM(["Hello from Voltline.", "Second reply."])
    client = TestClient(create_app(settings, llm=llm, retriever=retriever))
    assert client.get("/health").json()["ok"]
    chunks = client.post("/retrieve", json={"query": "return window", "top_k": 2}).json()["chunks"]
    assert len(chunks) == 2 and chunks[0]["url"]
    assert client.post("/tools/check_stock", json={"product": "Glide mouse"}).json()["in_stock"]

    body = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}], "stream": False}).json()
    assert body["type"] == "done" and body["reply"] == "Hello from Voltline."

    with client.stream("POST", "/chat", json={"messages": [{"role": "user", "content": "hi"}]}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert events[-1]["reply"] == "Second reply."
    assert client.get("/turns/recent").json()[0]["reply"] == "Second reply."


async def test_falls_back_when_main_llm_fails(tool_ctx, retriever):
    from app.agent import Fallback

    class RateLimited:
        class chat:
            class completions:
                @staticmethod
                async def create(**_):
                    raise RuntimeError("429 rate limit")

    backup = FakeLLM(["Pulse Buds Pro cost $149.99."])
    agent = Agent(RateLimited, retriever, tool_ctx, TurnConfig(model="main"), fallback=Fallback(backup, "backup"))
    done = (await _run(agent, [{"role": "user", "content": "How much are the Pulse Buds Pro?"}]))[-1]
    assert done["reply"] == "Pulse Buds Pro cost $149.99."
    assert done["model"] == "backup" and "429" in done["fallback_reason"]
    assert backup.requests[0]["model"] == "backup"


def test_rate_limit_pause_parsing():
    from app.agent import MAX_PAUSE_S, rate_limit_pause_s

    class Err(Exception):
        status_code = 429

    assert rate_limit_pause_s(Err("Please try again in 7.5s.")) == 7.5
    assert rate_limit_pause_s(Err("Please try again in 1m30s.")) == 90
    assert rate_limit_pause_s(Err("tokens per day. Please try again in 2h5m")) == MAX_PAUSE_S
    assert rate_limit_pause_s(Err("slow down")) == 30
    assert rate_limit_pause_s(TimeoutError("timed out")) is None


async def test_rate_limited_main_is_skipped_until_pause_ends(tool_ctx, retriever):
    from app.agent import Fallback

    class Limited:
        calls = 0

        class chat:
            class completions:
                @staticmethod
                async def create(**_):
                    Limited.calls += 1
                    raise RuntimeError("429 Rate limit reached. Please try again in 1m0s.")

    backup = FakeLLM(["First.", "Second."])
    agent = Agent(Limited, retriever, tool_ctx, TurnConfig(model="main"), fallback=Fallback(backup, "backup"))
    msgs = [{"role": "user", "content": "hi"}]
    first = (await _run(agent, msgs))[-1]
    second = (await _run(agent, msgs))[-1]
    assert Limited.calls == 1  # the second turn went straight to the fallback
    assert first["reply"] == "First." and second["reply"] == "Second."
    assert "paused" in second["fallback_reason"]
    agent._paused_until["main"] = 0  # pause over: the main model is tried again
    await _run(agent, msgs)
    assert Limited.calls == 2


def test_gemini_tool_calls_keep_thought_signature():
    from types import SimpleNamespace as NS

    from app.agent import _call_slot, _ToolCall

    calls: dict[int, _ToolCall] = {}
    sig = {"google": {"thought_signature": "abc"}}
    # Gemini: no index, each call whole, a signature that must be sent back.
    for cid, name in (("call_1", "check_stock"), ("call_2", "get_price")):
        tc = NS(index=None, id=cid, function=NS(name=name, arguments="{}"), extra_content=sig)
        slot = _call_slot(calls, tc)
        calls.setdefault(slot, _ToolCall(id=cid, name=name, extra_content=tc.extra_content))
    assert [c.name for c in calls.values()] == ["check_stock", "get_price"]
    assert calls[0].message()["extra_content"] == sig
    assert "extra_content" not in _ToolCall(id="x", name="y").message()
    # OpenAI: index given, later deltas carry no id.
    calls = {0: _ToolCall(id="a")}
    assert _call_slot(calls, NS(index=0, id=None)) == 0
    assert _call_slot(calls, NS(index=None, id=None)) == 0


async def test_fallback_mid_turn_drops_provider_fields(tool_ctx, retriever):
    from app.agent import Fallback

    main = FakeLLM([[("check_stock", json.dumps({"product": "Glide mouse"}))]])
    real_create = main.chat.completions.create
    state = {"n": 0}

    async def create(**kw):
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("503 overloaded")
        return await real_create(**kw)

    main.chat.completions.create = create
    backup = FakeLLM(["Yes, it is in stock."])
    agent = Agent(main, retriever, tool_ctx, TurnConfig(model="main"), fallback=Fallback(backup, "backup"))
    events = await _run(agent, [{"role": "user", "content": "Is the Glide mouse in stock?"}])
    assert events[-1]["reply"] == "Yes, it is in stock." and events[-1]["model"] == "backup"
    calls = [c for m in backup.requests[0]["messages"] for c in m.get("tool_calls") or []]
    assert calls and all("extra_content" not in c for c in calls)
