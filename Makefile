.PHONY: install server web test eval-text eval-text-fast audio-clips eval-audio eval-scenarios build-web check

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

audio-clips:        ## Synthesise the 90 audio eval clips and scenario clips (needs CARTESIA_API_KEY)
	uv run --project server python evals/audio/make_clips.py

eval-audio:         ## Stream the audio clips into /voice; WER and latency per condition (needs make server)
	uv run --project server python evals/run_audio_eval.py

eval-scenarios:     ## Browser test driver: 8 scripted conversations through the real page (needs make server and make web)
	uv run --project server python evals/driver/browser_driver.py

build-web:          ## Production build of the website
	cd web && npm run build

check: test build-web  ## Everything that runs without API keys

data-check:         ## Build data/manifest.csv and run the quality checks on every clip
	uv run --project server python evals/data_check.py

stt-compare:        ## Hearing A/B: the question clips through Deepgram, Gnani and Whisper
	uv run --project server python evals/stt_compare.py

record:             ## Record the 20-question set in your own voice (stays local, data/private/)
	uv run --project server python evals/audio/record.py $(ARGS)

compare:            ## Paired run of both systems through the web page (RUN=name)
	uv run --project server python evals/compare.py --run $(or $(RUN),v1)

score:              ## Score a paired run from its recordings (RUN=name)
	uv run --project server python evals/score_run.py --run $(or $(RUN),v1)

annotations:        ## Merge Audacity labels and check the detectors against them
	uv run --project server python evals/annotation.py merge
