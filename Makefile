.PHONY: install server web test eval-text eval-text-fast build-web check

install:            ## Install server and web dependencies
	cd server && uv sync
	cd web && npm install

server:             ## Run the API on http://localhost:8000
	cd server && uv run uvicorn --factory app.main:create_app --reload --port 8000

web:                ## Run the website on http://localhost:3000
	cd web && npm run dev

test:               ## Server unit tests (offline, no API keys)
	cd server && uv run pytest -q

eval-text:          ## 60-question text eval with the LLM judge (needs an API key)
	uv run --project server python evals/run_text_eval.py

eval-text-fast:     ## Same, deterministic checks only (no judge calls)
	uv run --project server python evals/run_text_eval.py --no-judge

build-web:          ## Production build of the website
	cd web && npm run build

check: test build-web  ## Everything that runs without API keys
