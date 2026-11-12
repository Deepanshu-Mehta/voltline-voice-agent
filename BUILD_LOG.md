# Build log

Three lines a day: what changed, what the numbers were, what broke. This becomes the video's engineering-decisions script.

## 2026-11-12 (Phase 1)

- **Changed:** content (14 products, policies, 6 guides, 29 FAQs, 20 demo orders), hybrid retrieval, 5 tools + knowledge-base search tool, streaming `/chat` brain, 60-question eval, Next.js site.
- **Numbers:** retrieval hit@3 100% on 47 scored questions; retrieval about 3 ms per query in process (BM25 + local bge-small, 46 chunks); 33 unit tests pass. No LLM score yet: no API key configured.
- **Decisions:** local embeddings instead of a vector database (no network hop, works offline); retrieval before the LLM rather than as a tool (saves a round trip on fact questions); order dates stored as offsets so the demo never goes stale; ticket and callback numbers start at 20001 and 1001 so they never sound like four-digit order numbers.
- **Frontend polish:** line drawings for all 14 products, an Ask Volt drawer on every page (the conversation persists while browsing), product filters and sorting, FAQ accordion, docs sidebar, mobile menu, 404 page. Fixed a client bundle importing `node:fs` by moving formatting helpers to `web/lib/format.ts`.

## 2026-11-12 (Phase 2)

- **Changed:** `/voice` WebSocket pipeline (Deepgram Nova-3 -> agent brain -> Cartesia Sonic 3.6), browser capture worklet + Silero VAD barge-in + gapless player, server-side Silero VAD turn-taking with early Finalize, unfinished-sentence grace, tool fillers, LLM fallback provider, per-turn timings, live latency table, audio test set (90 clips + 17 scenario clips), audio eval, Playwright browser driver. Groq is now the default LLM (user's choice).
- **Numbers:** text eval 81.7% correct, 0% hallucination, 10/10 out of scope, 100% retrieval hit@3 (gpt-oss-20b). Voice, clean clips on gpt-oss-120b: WER 0.00, end of turn 714 ms (was 1,378), LLM first token 618 ms, TTS first byte 118 ms, server response 1,493 ms median. Browser driver: 8/8 scenarios pass; barge-in stopped audio 191 ms after the user started; measured response about 2.2-2.6 s, mostly on the DeepSeek fallback (LLM first token about 1.1 s).
- **Broke / learned:** Groq free tier allows 8,000 tokens a minute and 200,000 a day per model, which voice traffic exceeds within minutes; Gemini 2.5 Flash is closed to new users and newer Flash models returned 503. Deepgram adds about 500 ms after its endpointing and about 400 ms to answer Finalize, so Finalize is now sent early. Deepgram writes "PulseBuds" without keyterms; Cartesia reads 4512 as "forty-five twelve", so clips speak order numbers digit by digit. Judge rubric was too strict for short spoken answers (9 of 11 failures); rubric fixed, re-run pending the rate limit.
