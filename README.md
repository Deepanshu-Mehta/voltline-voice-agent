# Voltline voice agent

One agent brain (prompt, knowledge base, tools), exposed through five channels: a website agent with cascaded and speech-to-speech modes, a Twilio phone number, LiveKit and Vapi. A benchmark page compares their latency. The full plan is in [PLAN.md](PLAN.md).

Voltline Electronics is a fictional online store created for this project.

## Status

| Phase | State |
| --- | --- |
| 1. Foundation: site, knowledge base, tools, text agent, eval set | Built; first scored eval needs an API key |
| 2. Cascaded web agent | Not started |
| 3. Low Latency Mode and speech-to-speech | Not started |
| 4. Phone | Not started |
| 5. LiveKit and Vapi | Not started |
| 6. Benchmark dashboard | Not started |
| 7. Hardening, deploy, video | Not started |

## Quick start

```bash
cp .env.example .env          # add OPENAI_API_KEY (or another OpenAI-compatible provider)
make install
make server                   # API on http://localhost:8000
make web                      # website on http://localhost:3000 (second terminal)
make test                     # 33 offline tests, no keys needed
make eval-text                # 60-question eval with an LLM judge
```

Requires Python 3.12+ with [uv](https://docs.astral.sh/uv/), and Node.js 20+.

## Layout

```
content/    Markdown source of truth: 14 products, policies, setup guides, FAQ, 20 demo orders
server/     FastAPI backend: retrieval, tools, the agent brain, SQLite store
evals/      Question set, eval runner, results
web/        Next.js website, built from content/
```

## How the brain works

1. **Retrieval before the LLM.** The latest customer message (prefixed with the previous one for short follow-ups) is searched with BM25 plus local `bge-small` embeddings, fused by reciprocal rank. It runs in process: about 3 ms per query, no network call. Each product also gets a short fact card so price and warranty questions always have a precise hit.
2. **Streaming LLM with tools.** Any OpenAI-compatible Chat Completions model. Tools: `get_order_status`, `check_stock`, `get_price`, `book_callback`, `create_support_ticket`, plus `search_knowledge_base` for channels that cannot prefetch retrieval (speech-to-speech, Vapi).
3. **Cache-friendly prompts.** The system prompt and tool list are identical at the front of every request; retrieved text is attached to the latest user message only.
4. **Timings on every turn**, written to a `turns` table: retrieval, first LLM token, tool time, total.

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /chat` | `{messages, stream?}`; Server-Sent Events: `retrieval`, `token`, `tool_call`, `tool_result`, `done` |
| `POST /retrieve` | `{query, top_k?}`; top chunks with their source page |
| `GET /tools`, `POST /tools/{name}` | Tool schemas, and each tool as plain HTTP for LiveKit and Vapi |
| `GET /turns/recent` | Latest logged turns |
| `GET /health` | Retrieval mode, model, whether an LLM key is configured |

## Evals

`make eval-text` runs 60 questions (30 factual, 10 tool tasks, 10 multi-turn, 10 out-of-scope) through `/chat` and scores retrieval hit rate, key facts, tool choice and arguments, spoken style, latency, and, with an LLM judge, correctness and hallucination. Results go to `evals/results/`, and `latest.json` feeds the website's Benchmarks page.
