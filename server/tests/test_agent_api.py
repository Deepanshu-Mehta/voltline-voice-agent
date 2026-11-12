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
