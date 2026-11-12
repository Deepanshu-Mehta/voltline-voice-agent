import asyncio
import datetime as dt
from types import SimpleNamespace as NS

import pytest

from app.config import Settings
from app.content import load_orders, load_pages, load_products
from app.db import Database
from app.retrieval import Retriever
from app.tools import ToolContext

TODAY = dt.date(2026, 11, 12)


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    return Settings(db_path=tmp_path_factory.mktemp("db") / "test.db", retrieval_dense=False,
                    llm_provider="openai", llm_api_key=None, openai_api_key=None, groq_api_key=None, gemini_api_key=None, _env_file=None)


@pytest.fixture(scope="session")
def products(settings):
    return load_products(settings.content_dir)


@pytest.fixture(scope="session")
def retriever(settings, products):
    # Keyword-only keeps the tests fast and offline; dense retrieval is checked by the evals.
    return Retriever(load_pages(settings.content_dir), products, dense=False)


@pytest.fixture
def tool_ctx(settings, products, retriever):
    db = Database(":memory:")
    db.seed_orders(load_orders(settings.content_dir), today=TODAY)
    return ToolContext(db=db, products=products, retriever=retriever, today=lambda: TODAY)


class FakeLLM:
    """Replays scripted streaming responses. Each script item is either a string (streamed as
    text in small pieces) or a list of (tool_name, json_args) tool calls."""

    is_fake = True

    def __init__(self, script, delay: float = 0.0):
        self.script = list(script)
        self.delay = delay
        self.requests = []
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        step = self.script.pop(0)

        async def gen():
            if isinstance(step, tuple) and step[0] == "mixed":
                _, text, (name, args) = step
                yield NS(choices=[NS(delta=NS(content=text, tool_calls=None))], usage=None)
                yield NS(choices=[NS(delta=NS(content=None, tool_calls=[
                    NS(index=0, id="call_0", function=NS(name=name, arguments=args))]))], usage=None)
            elif isinstance(step, str):
                for i in range(0, len(step), 7):
                    if self.delay:
                        await asyncio.sleep(self.delay)
                    yield NS(choices=[NS(delta=NS(content=step[i:i + 7], tool_calls=None))], usage=None)
            else:
                for idx, (name, args) in enumerate(step):
                    # Arguments arrive split across chunks, as real providers stream them.
                    half = len(args) // 2
                    yield NS(choices=[NS(delta=NS(content=None, tool_calls=[
                        NS(index=idx, id=f"call_{idx}", function=NS(name=name, arguments=args[:half]))]))], usage=None)
                    yield NS(choices=[NS(delta=NS(content=None, tool_calls=[
                        NS(index=idx, id=None, function=NS(name=None, arguments=args[half:]))]))], usage=None)
            yield NS(choices=[], usage=NS(prompt_tokens=100, completion_tokens=20,
                                          prompt_tokens_details=NS(cached_tokens=64)))

        return gen()
