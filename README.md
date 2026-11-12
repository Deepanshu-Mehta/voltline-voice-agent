# Voltline voice agent

One agent brain (prompt, knowledge base, tools), exposed through five channels: a website agent with cascaded and speech-to-speech modes, a Twilio phone number, LiveKit and Vapi. A benchmark page compares their latency. It is built in seven phases, from the shared agent brain to a deployed demo; the status table below tracks them.

Voltline Electronics is a fictional online store created for this project.

## Status

| Phase | State |
| --- | --- |
| 1. Foundation: site, knowledge base, tools, text agent, eval set | Built; text eval 98.3% correct, 0% hallucination (target 90%) |
| 2. Cascaded web agent | Built; 8 of 8 browser scenarios pass |
| 3. Low Latency Mode and speech-to-speech | Not started |
| 4. Phone | Not started |
| 5. LiveKit and Vapi | Not started |
| 6. Benchmark dashboard | Not started |
| 7. Hardening, deploy, video | Not started |

## Quick start

```bash
cp .env.example .env          # add GEMINI_API_KEY (or another provider), DEEPGRAM_API_KEY, CARTESIA_API_KEY
make install
make server                   # API on http://localhost:8000
make web                      # website on http://localhost:3000 (second terminal)
make test                     # 52 offline tests, no keys needed
make eval-text                # 60-question eval with an LLM judge
make audio-clips              # synthesise the audio test set (needs CARTESIA_API_KEY)
make eval-audio               # stream the clips into /voice: WER and latency
make eval-scenarios           # browser test driver: 8 scripted voice conversations
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

## Voice pipeline (Phase 2)

Browser microphone -> 16 kHz PCM over one WebSocket (`/voice`) -> Deepgram Nova-3 streaming speech-to-text -> the same agent brain as `/chat` -> sentences streamed into Cartesia Sonic -> PCM back to the browser, played gaplessly.

- **Turn-taking:** a server-side Silero VAD ends the turn after 700 ms of silence (2.5 s when the sentence sounds unfinished, such as "...work with"), or on Deepgram's own end-of-speech signal. A `Finalize` sent at 300 ms of silence has the last words transcribed by the time the turn ends.
- **Barge-in:** Silero VAD in the browser ducks the agent at the first sign of speech and stops it once speech is confirmed; the server cancels the LLM and TTS and keeps only the words the user heard (from Cartesia word timestamps).
- **Fillers:** if a tool runs before anything has been said, a short line ("Let me look up that order.") keeps the line alive.
- **LLM fallback:** if the main LLM fails before answering (rate limit, outage), the turn moves to `LLM_FALLBACK_PROVIDER`. After a rate limit the main model is skipped for as long as the provider asks (up to 5 minutes), so later turns do not pay for a request that will fail.
- **Timings per turn** (to the `turns` table and the Benchmarks page): end of turn, LLM first token, TTS first byte, server response, and the browser's own end-of-speech-to-first-sound measure.

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /chat` | `{messages, stream?}`; Server-Sent Events: `retrieval`, `token`, `tool_call`, `tool_result`, `done` |
| `POST /retrieve` | `{query, top_k?}`; top chunks with their source page |
| `GET /tools`, `POST /tools/{name}` | Tool schemas, and each tool as plain HTTP for LiveKit and Vapi |
| `WS /voice` | Voice call: PCM audio in, events and reply audio out (protocol in `server/app/voice/session.py`) |
| `GET /turns/recent`, `GET /turns/summary` | Latest logged turns; median and 95th percentile latency per channel and mode |
| `GET /health` | Retrieval mode, model, whether an LLM key is configured |

## Evals

`make eval-text` runs 60 questions (30 factual, 10 tool tasks, 10 multi-turn, 10 out-of-scope) through `/chat` and scores retrieval hit rate, key facts, tool choice and arguments, spoken style, latency, and, with an LLM judge, correctness and hallucination. Results go to `evals/results/`, and `latest.json` feeds the website's Benchmarks page.

`make audio-clips` synthesises the voice test set with Cartesia: 30 questions in three conditions (clean, cafe noise at 10 dB, telephone quality) plus the 8 scripted scenarios. `make eval-audio` streams each clip into `/voice` in real time and reports word error rate, key-term accuracy and per-stage latency. `make eval-scenarios` runs the browser test driver: Playwright opens the site with a stubbed microphone, plays each scenario turn, records both sides, measures latency from the audio, and checks the expected tools and answers.
