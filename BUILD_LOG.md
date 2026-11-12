# Build log

Three lines a day: what changed, what the numbers were, what broke. This becomes the video's engineering-decisions script.

## 2026-11-12 (Phase 1)

- **Changed:** content (14 products, policies, 6 guides, 29 FAQs, 20 demo orders), hybrid retrieval, 5 tools + knowledge-base search tool, streaming `/chat` brain, 60-question eval, Next.js site.
- **Numbers:** retrieval hit@3 100% on 47 scored questions; retrieval about 3 ms per query in process (BM25 + local bge-small, 46 chunks); 33 unit tests pass. No LLM score yet: no API key configured.
- **Decisions:** local embeddings instead of a vector database (no network hop, works offline); retrieval before the LLM rather than as a tool (saves a round trip on fact questions); order dates stored as offsets so the demo never goes stale; ticket and callback numbers start at 20001 and 1001 so they never sound like four-digit order numbers.
- **Frontend polish:** line drawings for all 14 products, an Ask Volt drawer on every page (the conversation persists while browsing), product filters and sorting, FAQ accordion, docs sidebar, mobile menu, 404 page. Fixed a client bundle importing `node:fs` by moving formatting helpers to `web/lib/format.ts`.
