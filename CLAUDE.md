# Voltline voice agent: notes for coding agents

Read [PLAN.md](PLAN.md) first: it is the phase-by-phase plan with an exit check per phase. Log each working day in [BUILD_LOG.md](BUILD_LOG.md) (what changed, the numbers, what broke).

## Layout

- `content/`: Markdown source of truth (products, policies, guides, FAQ, `orders.json`). The website, the knowledge base and the tools all read it, so change facts here only, and check the eval references in `evals/questions.jsonl` when you do.
- `server/`: FastAPI agent brain (Python, uv). `app/agent.py` is the turn loop, `app/tools.py` the tool registry shared by every channel, `app/prompts/system.md` the speech prompt.
- `evals/`: 60-question text eval; `run_text_eval.py` writes `evals/results/latest.json`, which the Benchmarks page shows.
- `web/`: Next.js 16 with Cache Components. This version differs from older Next.js: read `web/AGENTS.md` and the docs in `web/node_modules/next/dist/docs/` before changing routing, caching or data loading.

## Commands

```bash
make server      # API on :8000 (uvicorn factory: app.main:create_app)
make web         # site on :3000
make test        # server tests, offline
make eval-text   # needs an LLM key in .env
make build-web
```

## Conventions

- Every channel must use the same prompt file and the same tool registry; do not fork them per platform.
- Retrieval runs before the LLM in our own pipeline; channels that cannot do that use the `search_knowledge_base` tool.
- Never put secrets in the browser; `.env` is git-ignored.
- Web: `lib/content.ts` uses `node:fs` and is server-only; client components import helpers from `lib/format.ts`.
- Web design: the "rating plate" look (aluminium, graphite, signal yellow only for live agent elements, Archivo with the width axis). Tokens are at the top of `web/app/globals.css`.
