# Voltline voice agents: cascade against speech-to-speech

Two voice agents for a fictional electronics store, built on the same prompt, knowledge base and tools, and evaluated side by side from what a caller actually hears:

- **A, cascade:** Deepgram Nova-3 speech-to-text, Gemini 3.5 Flash-Lite, Cartesia Sonic 3.6 text-to-speech.
- **B, speech-to-speech:** Gemini 3.1 Flash Live, one model that hears and speaks.

The point of the project is the evaluation: a curated test set, paired black-box runs, a blind listening test, annotation, an issue log with reproduction rates, and checks on the measuring tools themselves. **Results: [qa/findings.md](qa/findings.md).**

Voltline Electronics is fictional.

## Headline results

| | A: cascade | B: speech-to-speech |
|---|---|---|
| Response time, median | 2.53 s | **2.12 s** (344 ms faster) |
| Correct, judged on what was heard | **100%** | 98% |
| Said a different number than it meant | **0%** | 6% |
| Scripted conversations passed | **88%** | 62% |
| Listener preference (2 listeners, 24 pairs) | **19** | 4 |
| DNSMOS predicted quality | 3.95 | **4.05**, the opposite of the listeners |

On synthetic speech every speech-to-text engine tested (Deepgram, Gnani Prisma v2.5 and v3.0, Whisper) scores 0 to 1% word error. On real Indian English speech they range from 13% to 32%.

## What is in the repository

| Path | What it is |
|---|---|
| [qa/findings.md](qa/findings.md) | The results, what they mean, and their limits |
| [qa/issue-log.csv](qa/issue-log.csv) | Every defect found: system, evidence, how to reproduce, reproduction rate, status |
| [qa/datasheet.md](qa/datasheet.md) | The test set: contents, how each part was made, quality checks, known gaps |
| [qa/listening-test-protocol.md](qa/listening-test-protocol.md) | The listening test, fixed before any ratings |
| [qa/annotation-guide.md](qa/annotation-guide.md) | Labelling defects in Audacity |
| `data/manifest.csv` | One row per clip: speaker, accent, condition, licence, expected answer |
| `evals/` | Dataset builder, quality check, paired runner, scorer, hearing comparison, listening-test builder and analysis |
| `server/` | FastAPI backend: the shared agent brain, the cascade session, the speech-to-speech bridge |
| `web/` | Next.js site with the Talk page and its Cascade / Speech-to-speech switch |

The owner's own recordings are kept in `data/private/`, which git ignores. Public files list them by clip id only.

## Running it

```bash
cp .env.example .env     # GEMINI_API_KEY, DEEPGRAM_API_KEY, CARTESIA_API_KEY (GNANI_API_KEY for the hearing comparison)
make install
make server              # API on :8000
make web                 # site on :3000; open /talk and pick Cascade or Speech-to-speech
make test                # 56 offline tests
```

The evaluation:

```bash
make audio-clips         # synthesise the core clips (Cartesia)
make data-check          # build data/manifest.csv and check every clip
make stt-compare         # hearing A/B: Deepgram, Gnani, Whisper on every question clip
make record              # record your own voice into data/private/ (stays local)
make compare RUN=v1      # paired run: every clip through A then B via the real page
make score RUN=v1        # score from the recordings
uv run --project server python evals/listening/build.py --run v1    # build the listening test
uv run --project server python evals/listening/analyse.py           # analyse the responses
uv run --project server python evals/compare_runs.py v1 a-400 --system A   # what changed between runs
```

## How the scoring stays honest

- **Black box:** both systems are driven through the same page and judged from the recorded audio, never from their own text.
- **Two transcripts:** each reply is transcribed by Whisper and by Deepgram. A number counts against an agent only when both agree, and a separate metric catches a spoken number that differs from the system's own text.
- **Same evidence for both:** the judge sees the knowledge-base excerpts and the agent's real tool calls.
- **Uncertainty:** 95% bootstrap intervals and McNemar's test on paired outcomes.
- **Provider failures apart:** a reply whose audio never arrived (for example, credits running out mid-run) is reported as an infrastructure failure, not a wrong answer.
- **Measured instruments:** the judge, Whisper, DNSMOS and the speech-end detector were each checked, and the problems found are in the findings and the issue log.

## Earlier phases

The project began as a single agent served on several channels (phone, LiveKit, Vapi). Phases 1 and 2 (site, knowledge base, tools, text agent, cascade voice agent) are what system A is built on; the build log is in [BUILD_LOG.md](BUILD_LOG.md).
