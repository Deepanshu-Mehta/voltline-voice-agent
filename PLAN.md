# AI Voice Agent: Phase-wise Build & Evaluation Plan

_As of 2026-11-12 · Source: [Claude Doc](https://claude.ai/artifact/PwhEnLYs6Pushc8fW4Z8rq)_

## Summary

Build one agent "brain" (prompt, knowledge base, tools) once, then expose it through the four entry points the guidelines ask for: the custom website agent (cascaded and speech-to-speech modes), Twilio phone, LiveKit and Vapi. That makes five channels, or six benchmark configurations once the cascaded pipeline's Standard and Low Latency modes are counted separately. A latency dashboard compares all six. The plan is 14 days, solo, from 13 to 26 November 2026, with day 14 held as a buffer.

This plan takes the following reading of your answers and the guidelines:

- **Product:** a fictional online electronics store (working name **Voltline Electronics**; rename freely). It gives the richest mix of pricing, specs, documentation and FAQ for retrieval, plus natural tools (order status, stock check, callback booking).
- **Language:** English only.
- **Team:** one person, so phases run in sequence and the agent brain is shared across channels to avoid building it five times.
- **Key design rule:** every channel calls the same backend for retrieval and tools. That is what keeps answers consistent across channels and makes the "same agent in four ways" requirement true. Latency is only partly comparable: speech-to-speech and Vapi fetch knowledge through a tool call, which adds a model round trip on fact questions, and the dashboard shows that as a known difference.
- **Evals are built in from day 2,** not added at the end: a fixed test set of questions and recorded audio runs against every channel after every change.

The guidelines give a deadline of 14 July with one week to build. That date has passed, so confirm the real submission date with the organisers; this plan assumes 26 November.

## Requirements

The uploaded guidelines and the Google Doc are the same text. Everything below is a must-have unless marked stretch; the last column is the phase that delivers it.

| # | Requirement from the guidelines | Evaluation criteria it serves | Phase |
|---|---|---|---|
| 1 | Product website: description, pricing, documentation, FAQ, feature list, company info, detailed enough to verify answers | Accuracy, RAG, user experience | 1 |
| 2 | Knowledge retrieval (RAG) over the site content | RAG implementation, accuracy | 1 |
| 3 | Tools the agent can call | Tool usage | 1 |
| 4 | Custom website agent, cascaded pipeline (speech-to-text, LLM, text-to-speech) | Completeness, voice quality | 2 |
| 5 | Low Latency Mode for the cascaded pipeline, using as many of the 16 listed techniques as apply | Latency optimisation, engineering decisions | 3 |
| 6 | Custom website agent, speech-to-speech pipeline, with a switch between the two modes on the page | Completeness, comparison | 3 |
| 7 | Real phone number via Twilio: call in, or request a callback | Completeness, production readiness | 4 |
| 8 | Same agent on LiveKit, reachable from the website | Completeness, comparison | 5 |
| 9 | Same agent on Vapi, reachable from the website | Completeness, comparison | 5 |
| 10 | Latency benchmark across all five channels: first response, average response, end-of-turn, streaming, plus framework metrics, shown as a table or dashboard | Comparison, latency optimisation | 6 |
| 11 | API keys preconfigured (recommended option) so the evaluator can test directly | Deployment quality | 7 |
| 12 | One deployed URL containing all of the above | Deployment quality | 7 |
| 13 | 15-minute demo video covering product, each channel, latency comparison, decisions, challenges, future work | All | 7 |
| 14 | GitHub repository link (optional in the guidelines; treated as a must here for your portfolio) | Engineering decisions | 7 |
| 15 | Stretch: settings page for the evaluator's own API keys; both call-in and callback (the guidelines require only one) | Production readiness | 7 |

## Architecture

```
 Browser (Next.js on Vercel)                 Phone             Hosted platforms
 ├─ Cascaded  ── WebSocket ──┐               Twilio number     ├─ LiveKit Agents worker
 ├─ Low Latency ─ WebSocket ─┤               Media Streams ─┐  └─ Vapi assistant
 └─ Speech-to-speech (WebRTC │→ OpenAI Realtime)            │        │ tool calls
        │ tool calls relayed │                              │        │
        ▼                    ▼                              ▼        ▼
 ┌──────────────────── FastAPI backend (always-on) ─────────────────────────┐
 │  STT (Deepgram) → LLM → TTS (Cartesia)   /chat   /retrieve   tool APIs   │
 │  vector index (sqlite-vec / FAISS) · seeded orders DB · turns table      │
 └──────────────────────────────────────────────────────────────────────────┘
```

Each channel handles audio its own way, but all five call the same backend endpoints for retrieval and tools. How retrieval reaches the model differs: your own pipeline and LiveKit add the retrieved text before the LLM runs, while speech-to-speech and Vapi request it through a tool call. A change to the prompt or the knowledge base then reaches every channel at once.

Provider names below are starting picks. This field moves fast, so check current model names and free-tier limits on day 1 before committing.

| Layer | Starting pick | Why | Fallback |
|---|---|---|---|
| Website | Next.js, deployed on Vercel | Fast to build product pages, docs and dashboard in one app | Plain React + Vite |
| Backend | Python FastAPI with WebSockets, on an always-on host (Fly.io, Railway or Render) | You know Python; voice needs long-lived connections, which serverless functions drop | Node.js |
| Voice activity and turn detection | Silero VAD in the browser, plus the speech-to-text provider's end-of-speech signal | Local detection saves a network hop and enables barge-in | Server-side VAD |
| Speech-to-text | Deepgram streaming | Interim results and endpointing built for agents | AssemblyAI streaming, OpenAI realtime transcription |
| LLM | A small fast model with streaming and prompt caching (Gemini Flash class or GPT mini class) | Time to first token dominates perceived delay | Groq-hosted open model |
| Text-to-speech | Cartesia streaming | Low time to first audio over WebSocket | ElevenLabs Flash, Deepgram Aura |
| Speech-to-speech | OpenAI Realtime API (mini tier for cost) | Mature tool calling; connects from the browser over WebRTC with a short-lived token; retrieval is a tool call here too | Gemini Live API |
| Phone | Twilio number + Media Streams (two-way audio over WebSocket) into the same cascaded pipeline | Reuses your own pipeline, so the phone numbers measure the phone network, not a different stack | Twilio ConversationRelay, where Twilio runs speech-to-text and text-to-speech |
| LiveKit | LiveKit Agents (Python) on LiveKit Cloud, same three providers | Built-in turn detection and per-turn metrics; retrieved text is added before the LLM runs, as in your own pipeline | Self-hosted worker on the backend host |
| Vapi | Vapi assistant with the web SDK; tools point at your backend's server URL | Fastest to set up; retrieval runs as a tool call to your backend, which costs one extra LLM round trip on fact questions | Vapi Custom LLM pointing at your own endpoint |
| Knowledge base | Site content written once as Markdown; rendered into the website and chunked into a vector index (sqlite-vec or FAISS) | One source of truth, so the site and the agent cannot disagree | Vapi or provider-hosted knowledge base |
| Metrics store | One `turns` table (Postgres or SQLite): channel, mode, per-stage timestamps, commit hash | Powers both the dashboard and regression checks | JSON lines file |

## Phase-wise plan

Seven phases over 13 days, with the video recorded on day 13 and day 14 kept free as a buffer. Each phase ends with an exit check; do not start the next phase until it passes, because every later channel depends on the shared backend being right. The automated test drivers are built alongside the channels (browser driver in Phase 2, phone dialler in Phase 4), so Phase 6 only has to run them.

| Phase | Dates (Nov 2026) | Days | Outcome | Exit check |
|---|---|---|---|---|
| 1. Foundation: site, knowledge base, tools | Fri 13 to Sat 14 | 2 | Text-only agent answers from the site and calls tools | 90% correct on the 60-question test set |
| 2. Cascaded web agent and browser test driver | Sun 15 to Tue 17 | 3 | Talk to the agent in the browser, fully streamed, with interruption; recorded clips can be played through the page automatically | 10-turn spoken conversation with no stalls; every turn logged with stage timings; the driver finishes a 5-clip run unattended |
| 3. Low Latency Mode and speech-to-speech | Wed 18 to Thu 19 | 2 | Mode switch on the page: Standard, Low Latency, Speech-to-Speech; each technique bucket behind its own flag | Low Latency median first response at least 30% below Standard on a quick 10-turn check |
| 4. Phone and phone dialler | Fri 20 to Sat 21 | 2 | Call the number, or enter your number and get called back; a second number that dials in, plays clips and records | 5 inbound and 5 callback calls complete a tool task; one dialler run recorded |
| 5. LiveKit and Vapi | Sun 22 to Mon 23 | 2 | Both reachable from buttons on the site, same prompt and tools | Same 8 scripted scenarios pass on both, run through the browser driver |
| 6. Benchmark, ablation and dashboard | Tue 24 | 1 | Dashboard comparing all six configurations; cumulative ablation of the five technique buckets | 25 or more measured turns per configuration on the dashboard |
| 7. Hardening, deploy, video | Wed 25 | 1 | Final URL, repository, 15-minute video recorded | Fresh-browser and fresh-phone test passes on every channel |
| Buffer | Thu 26 | 1 | Fixes, re-recording if needed, submission | Submitted |

### Phase 1: Foundation (days 1 to 2)

Goal: a correct text agent before any audio. Most "voice" bugs are really prompt, retrieval or tool bugs, and they are far faster to fix in text.

1. **Setup (half day).** Create accounts and keys: Deepgram, Cartesia, OpenAI, Google AI, Twilio, LiveKit Cloud, Vapi. Set a spend cap on each. Create the repository with `web/`, `server/`, `content/`, `evals/`, and a `.env.example`.
1. **Write the product content as Markdown in `content/`.** Have an LLM draft all of it, then check every price, spec and policy by hand so the site has no contradictions. Aim for 12 to 15 products across 4 categories, each with price, specs, warranty and stock. Add pricing and delivery fees, a returns and warranty policy, setup guides (the "documentation"), 25 or more FAQs, and an About page. Include details that are easy to verify by ear: exact prices, model numbers, return window in days.
1. **Build the website from that content.** Pages: Home, Products, Pricing, Docs, FAQ, About, Talk to us (the agent hub), Benchmarks.
1. **Retrieval.** Chunk by heading (about 150 to 300 words), embed, store in the vector index. Expose `POST /retrieve` returning the top chunks with their source page.
1. **Tools, as plain HTTP endpoints backed by a seeded database:** `get_order_status(order_id)`, `check_stock(product)`, `get_price(product)`, `book_callback(name, phone, time)`, `create_support_ticket(issue)`. Seed 20 fake orders so the evaluator can test with listed order IDs; print a few on the Docs page.
1. **System prompt for speech.** One or two short sentences per reply, no lists or symbols, read prices and model numbers naturally, confirm order IDs digit by digit, say so when the answer is not on the site, never invent a price.
1. **Text chat endpoint** `POST /chat` (streaming) that runs prompt + retrieval + tools. This is the brain every channel reuses.
1. **Eval set v1.** Have an LLM draft the 60-question test set from the content (see Evals), check each reference answer by hand, write the 10 trick questions yourself, and write the script that runs it against `/chat`.

Deliverables: live site on a preview URL, `/chat`, `/retrieve`, tool endpoints, eval script with a first score.

### Phase 2: Cascaded web agent (days 3 to 5)

Goal: your own pipeline, streamed at every stage, with timings recorded from the first turn.

1. **Browser audio.** Capture the microphone with echo cancellation on, run VAD locally, stream 16 kHz audio to the backend over one WebSocket. Play returned audio through a queue that can be flushed instantly.
1. **Server orchestration.** Audio in, streaming speech-to-text; on end of turn, call the brain; stream LLM tokens into text-to-speech sentence by sentence; stream audio back.
1. **Turn-taking.** End of turn = provider end-of-speech signal or a silence timeout (start at 700 ms in Standard mode). Barge-in: when VAD hears the user during playback, flush the audio queue, cancel the LLM and text-to-speech requests, and keep only the words actually spoken in the history. Knowing where playback stopped needs word timestamps from the text-to-speech provider; Cartesia provides them, so confirm this for any fallback voice.
1. **Tool calls in voice.** Say a short filler ("Let me check that order") while the tool runs so the line is never silent.
1. **Instrumentation.** Stamp every turn: user speech end, final transcript, LLM first token, text-to-speech first byte, first audio played in the browser. Write one row per turn to the `turns` table.
1. **Interface.** Start and stop button, live transcript, state indicator (listening, thinking, speaking), and a small per-turn latency readout.
1. **Audio test hook.** Let the WebSocket accept a pre-recorded audio file in place of the microphone. The audio eval depends on this. Then build the browser test driver, about half a day: Playwright opens the site with a stubbed microphone (a replaced getUserMedia) that plays clip N once the agent has stopped talking, and records both sides of the conversation. The same driver later runs the speech-to-speech, LiveKit and Vapi buttons, so it must work through the page, not the WebSocket.

Deliverables: working cascaded agent on the site; timings visible per turn; audio eval runs end to end; browser test driver; a bare `/benchmarks` table reading the `turns` table, so Phase 6 only adds to it.

### Phase 3: Low Latency Mode and speech-to-speech (days 6 to 7)

Goal: a visible, measurable gap between Standard and Low Latency, and a second architecture behind the same switch.

**Day 6: Low Latency Mode.** Build the five buckets below, each behind its own flag, and confirm with a quick 10-turn check that Low Latency beats Standard. The bucket-by-bucket measurement runs in Phase 6 on the automated driver, not today.

| Bucket | Technique from the guidelines | What to do | Standard mode | Low Latency mode |
|---|---|---|---|---|
| Baseline | Streaming ASR, LLM, TTS | Already built in Phase 2 | On | On |
| 1. Models | Smaller ASR model | Switch to the provider's fastest model | Most accurate model | Fastest model |
| 1. Models | Smaller LLM | Switch to the smallest model that still passes the eval set | Mid-size model | Smallest passing model |
| 1. Models | Lightweight TTS | Switch to the provider's low-latency voice model | Higher-quality voice | Fastest voice |
| 2. Endpointing | Reduced endpoint detection timeout | Lower the silence timeout; use the provider's turn-detection signal | 700 ms | 250 to 300 ms |
| 3. Buffering | Reduced TTS pre-buffer | Start playback on the first audio chunk; send the first clause to text-to-speech without waiting for a full sentence | Sentence-level | First clause, smallest safe buffer |
| 4. Context and RAG | Prompt optimisation, with prompt and context caching | Trim the system prompt, and keep it and the tool definitions byte-identical at the front of every request so the provider cache hits. These two pull against each other: caching only applies above a minimum prompt length (about 1,024 tokens for OpenAI; check your provider), so a trimmed prompt can lose the cache. Measure trimmed, cached and both, and keep the fastest | Full prompt, cached | Whichever combination measures fastest |
| 4. Context and RAG | Context reduction | Keep the last few turns plus a running summary | Last 10 turns | Last 4 turns |
| 4. Context and RAG | Smaller RAG retrieval size | Fewer, shorter chunks | Top 4 | Top 2 |
| 4. Context and RAG | Response length restriction | Token cap and a one-sentence instruction | 2 sentences | 1 sentence, hard cap |
| 4. Context and RAG | Tool optimisation and efficient tools | Fewer tools with short descriptions; in-memory lookups | Standard | Trimmed and cached |
| 5. Pre-warming | Additional | Keep provider connections open and pre-warmed; start retrieval on stable interim transcripts; cache audio for the greeting and common fillers; host the backend in the same region as the providers | Partly | All on |

Re-run the text and audio evals in Low Latency mode. If accuracy drops more than 5 points, step the LLM back up one size.

**Day 7: Speech-to-speech.**

1. Backend endpoint that issues a short-lived token; the browser connects straight to the realtime model.
1. Same system prompt. Register the same tools, plus a `search_knowledge_base(query)` tool that calls `/retrieve`, since the realtime model cannot see your index otherwise. This costs a second model round trip on fact questions, which your own pipeline avoids; flag those turns in the log so the dashboard can show the difference.
1. Browser relays each tool call to the backend and returns the result.
1. Measure latency from the realtime API's speech-stopped event to first audio received. The server detects the end of speech here, so the browser cannot know that moment on its own; use the event's timing as the start point. Log to the same `turns` table.
1. Add the three-way mode switch to the agent panel.

Deliverables: mode switch live; five technique buckets behind flags; speech-to-speech passing the tool scenarios.

### Phase 4: Phone (days 8 to 9)

1. Buy a Twilio voice number. Check on day 1 which countries you can buy and call from your account (see Risks).
1. **Inbound.** The number's webhook returns instructions that open a two-way media stream to `wss://.../twilio`. Convert Twilio's 8 kHz μ-law audio only where a component cannot take it directly. Deepgram accepts it as is and Cartesia can output it, so skip resampling, which removes delay and a source of bugs. Keep Twilio's format, then reuse the Phase 2 pipeline unchanged.
1. **Barge-in on the phone.** Send Twilio's clear-buffer message when the caller interrupts. Also send a mark message after each chunk of audio: the marks Twilio echoes back show which words the caller actually heard, so the history keeps only those.
1. **Callback.** A form on the site (name, phone number, consent tick box) calls `POST /callback`, which places an outbound call into the same stream. Rate-limit to a few calls per number per hour so the form cannot be abused.
1. **Phone-specific prompt additions.** Spell out order IDs, never refer to "the screen", offer to repeat.
1. Log timings with `channel = phone`. Record calls (with a spoken notice) for the external latency measurement in Evals. Then build the phone dialler, about half a day: a second Twilio number that calls the agent, plays the benchmark clips with a pause after each long enough for the reply, and records both sides as separate channels.

Deliverables: the phone number shown on the site; working callback form.

### Phase 5: LiveKit and Vapi (days 10 to 11)

**Day 10: LiveKit.**

1. Agent worker using the LiveKit Agents SDK with the same speech-to-text, LLM and text-to-speech providers as your cascaded pipeline, so the comparison isolates the framework.
1. Same system prompt (imported from one shared file). Tools are thin wrappers that call your backend endpoints. Add the retrieved text to the turn before the LLM runs, the same way your own pipeline does, so facts need no tool round trip. The SDK has a hook for this that fires when the user's turn completes (on_user_turn_completed at the time of writing; check the current docs).
1. Token endpoint on the backend; a "Talk via LiveKit" button on the site joins a room.
1. Capture LiveKit's per-turn metrics (end-of-turn delay, transcription delay, LLM time to first token, text-to-speech time to first byte) and forward them to the `turns` table. Check the current SDK docs for where these are exposed; the event names changed in recent versions.

**Day 11: Vapi.**

1. Create the assistant through the API from a script in the repository, not by hand in the dashboard, so the configuration is versioned. Retrieval stays a tool call on Vapi, so fact questions take one extra LLM round trip; flag those turns in the log.
1. Same system prompt. Each tool is a function tool whose server URL is your backend, including `search_knowledge_base`.
1. Embed with the Vapi web SDK behind a "Talk via Vapi" button, using the public key only.
1. Subscribe to the end-of-call report on your server URL and store Vapi's reported latency figures. Also time turns in the browser from the SDK's speech-start and speech-end events.

Deliverables: both buttons live; both platforms pass the 8 scripted scenarios, driven by the browser test driver.

### Phase 6: Benchmark dashboard and full eval run (day 12)

1. **Benchmark run (start first thing; it runs unattended).** The same 25 recorded user turns against each of the six configurations: web Standard, web Low Latency, web speech-to-speech, LiveKit, Vapi and phone. The browser driver covers the first five; the dialler covers phone.
1. **Cumulative ablation on the web cascaded pipeline.** Start from Standard, then switch on the buckets one after another in a fixed order: models, endpointing, buffering, context and RAG, pre-warming. Run 25 turns at each step: six runs, about 150 turns. Re-run the text eval at each step so every latency saving has its accuracy cost beside it. The saving credited to each bucket depends on the order, so state the order on the dashboard.
1. **Network round trip.** Measure browser to backend, and backend to each provider, from your test location.
1. **Finish the `/benchmarks` page while the runs go.** One comparison table (median and 95th percentile per metric per configuration, with fact-question turns and no-lookup turns shown separately), a stacked bar of where the time goes per stage, the ablation table, the network round-trip figures, and a live view of the last 20 real turns.
1. **"How we measured" note under the table.** Definitions, sample size, date, test location, and the known differences: speech-to-speech and Vapi fetch knowledge through a tool call, the other channels do not.
1. Run the complete eval suite once on every channel and publish the accuracy and voice-quality results on the same page.

### Phase 7: Hardening, deploy, video (days 13 to 14)

Hardening has one day, so add timeouts, retries and rate limits to each channel as you build it; day 13 is for verifying them, not writing them.

1. **Reliability.** Timeouts and one retry on every provider call; a spoken fallback line when something fails; automatic reconnect on WebSocket drop; a fallback provider for text-to-speech.
1. **Abuse and cost protection** (your keys are live for the evaluator): per-IP session limits, 5-minute maximum session, daily spend caps, callback rate limit, no secret keys in the browser.
1. **User experience.** Microphone permission help, mobile layout, clear labels for the four entry points and for the three modes inside the website agent, sample questions and sample order IDs beside the agent.
1. **Final checks** from a clean browser profile and a different phone; 20 consecutive conversations per channel without a crash, run with the test drivers.
1. **Repository.** README with the architecture diagram, setup steps, the benchmark table and the eval results.
1. **Video, recorded on the afternoon of day 13.** Script it against the guideline's 12 required items, about 75 seconds each. Record the phone call on speaker. Show the dashboard while explaining the three biggest engineering decisions, including why the channels are not perfectly comparable.
1. **Day 14, buffer.** Fix anything the final checks found, re-record video segments if needed, and submit the URL, the video and the repository link. If nothing is broken, submit early.

## Evals

One fixed test set runs against every channel after every meaningful change, and results are stored with the commit hash so regressions show up the same day. The targets below are starting goals; reset them after the first baseline run in Phase 2.

### Test assets (build once, reuse everywhere)

| Asset | Size | Contents | Built in |
|---|---|---|---|
| Question set | 60 questions with reference answers and source page | 30 factual (price, spec, policy), 10 tool tasks, 10 multi-turn follow-ups, 10 out-of-scope or trick questions (a product you do not sell, a discount that does not exist) | Phase 1 |
| Audio set | 30 of those questions as audio, in 3 conditions = 90 clips | Clean; with cafe or street noise mixed in at a fixed level; and telephone quality (8 kHz). Use your own voice plus 2 or 3 friends or synthetic voices for accent variety | Phase 2 |
| Scenario scripts | 8 scripted conversations of 4 to 6 turns | Order status, stock then price, returns policy then ticket, callback booking, an interruption mid-answer, a correction ("no, I said 4512"), a long pause mid-sentence, an out-of-scope request | Phase 2 |
| Benchmark turns | 25 recorded user turns | A mix of short questions, long questions and tool requests, played identically into every channel. Tag each turn as fact question, tool request or no lookup, so latency can be compared with and without the retrieval step | Phase 6 |

### Metrics and targets

| Layer | Metric | How it is measured | Target |
|---|---|---|---|
| Speech-to-text | Word error rate | Transcripts of the audio set against the scripts | 8% or less clean, 15% or less noisy and phone |
| Speech-to-text | Entity accuracy | Exact match on product names, prices, order IDs in the transcript | 95% or more |
| Retrieval | Hit rate at top 3 | Is the reference source page among the retrieved chunks | 95% or more |
| Answer | Factual correctness | LLM judge scores the answer against the reference; you hand-check 20 samples to confirm the judge agrees with you | 90% or more |
| Answer | Hallucination rate | Any stated fact (price, spec, policy) not found in the retrieved text or tool result | 3% or less |
| Answer | Out-of-scope handling | Declines or redirects on the 10 trick questions | 9 of 10 |
| Answer | Spoken-style compliance | Reply length in words; no lists, symbols or URLs | Median 35 words or fewer; 0 formatting violations |
| Tools | Tool selection and arguments | Right tool called with exactly the right arguments on the 10 tool tasks | 95% selection, 90% arguments |
| Tools | Task completion | Scenario reaches the correct end state (ticket exists, callback booked) | 7 of 8 scenarios per channel |
| Voice | Listening score | 5 listeners rate 10 clips per mode from 1 to 5 for naturalness and clarity, blind and in random order (a mean opinion score) | 4.0 or higher; report Standard vs Low Latency vs speech-to-speech |
| Voice | Round-trip intelligibility | Feed the agent's audio back through speech-to-text and compare with the text it meant to say | Word error rate 5% or less |
| Voice | Pronunciation checklist | Prices, model numbers, order IDs and the brand name, checked by ear | All 15 items correct |
| Turn-taking | Interruption response | Time from user speech start to agent audio stopping | 300 ms or less |
| Turn-taking | False cut-ins | Agent starts talking during the mid-sentence-pause clips | 10% or less in Standard, report honestly for Low Latency |
| Latency | Response latency per channel (defined in the Latency benchmark table below) |  |  |
| Reliability | Session success | 20 consecutive conversations per channel with no crash, hang or dead air longer than 5 seconds | 20 of 20 |
| Reliability | Failure handling | Kill one provider mid-call; the agent should speak a fallback line, not go silent | Passes for speech-to-text, LLM, text-to-speech |

### Latency benchmark

All times start at the moment the user stops speaking. Report the median and the 95th percentile, never a single best run.

| Metric (guideline name) | Definition | Where it comes from |
|---|---|---|
| First Response Latency | End of the user's first question to first agent audio heard, so it includes cold connections. The agent's opening greeting is excluded and reported separately as time to greeting | Browser timestamps; call recording for phone |
| Time to greeting | Session or call connected to first greeting audio | Browser timestamps; call recording for phone |
| Average Response Latency | The same question-to-audio gap, averaged over all turns in the session | `turns` table. For speech-to-speech the start point is the realtime API's speech-stopped event, because the server detects the end of speech |
| End-of-turn Latency | User speech end to the system deciding the turn is over | VAD timestamp vs end-of-turn event; LiveKit and Vapi report their own |
| Streaming Latency | Per stage: final transcript delay, LLM time to first token, text-to-speech time to first byte | Server timestamps; LiveKit per-turn metrics; Vapi call report |
| Network round trip | Browser to backend, and backend to each provider, from the test location | Ping measurements, shown separately so distance is not blamed on the pipeline |
| Extra | Time to complete a tool turn; interruption response time; turn duration | Server timestamps |

Starting targets for median response latency: 800 ms for web Low Latency and speech-to-speech, 1,500 ms for web Standard, LiveKit and Vapi, 1,800 ms for phone.

**Fair comparison across platforms.** LiveKit and Vapi define their metrics differently from each other and from your own code. So measure one number the same way everywhere: record each test conversation (both sides), then run a script that finds the silence between the end of the user's speech and the start of the agent's speech. Show that "measured from audio" number as the headline column, and each platform's self-reported numbers beside it. Explaining why the two differ is a strong engineering-decisions point for the video. Two differences remain even then, and the dashboard states both. First, your own pipeline and LiveKit add retrieved text before the LLM runs, while speech-to-speech and Vapi fetch it through a tool call, which adds a model round trip on fact questions; report fact-question turns and no-lookup turns separately. Second, if you test from India against providers hosted in the US, network travel can add a few hundred milliseconds per turn; report the measured round trip beside the results.

**Low Latency ablation.** Cumulative, by bucket. Start from Standard and switch on the five Phase 3 buckets one after another (models, endpointing, buffering, context and RAG, pre-warming), recording median response latency over 25 turns at each step: six runs in total. Publish the table on the dashboard with the accuracy score beside each row, to show what each saving cost in quality. Testing each technique alone would need about 13 settings of 25 turns each and is out of scope.

### How the evals run

| Command | What it does | When to run | Time |
|---|---|---|---|
| `make eval-text` | 60 questions through `/chat`; scores retrieval, correctness, hallucination, tools, style | After every prompt, retrieval or tool change | About 2 minutes |
| `make eval-audio` | 90 clips through the cascaded WebSocket (and the realtime model); adds word error rate and latency | End of each phase; after any provider or mode change | About 10 minutes |
| `make eval-scenarios CHANNEL=...` | 8 scripted conversations on one channel. The browser test driver runs web, LiveKit and Vapi; the dialler runs phone; you review the recordings against a checklist | When a channel is first working, and before submission | 15 to 30 minutes per channel |
| `make bench` | 25 benchmark turns on each of the six configurations through the test drivers; writes the dashboard data | Phase 6, and again on final deploy | About 1 hour |

- **Regression rule:** a change is rejected if correctness falls by more than 3 points. For latency, rerun the last accepted version back to back with the new one on the same day, and reject only if the new median is more than 15% slower in that paired run and again in a repeat. Provider latency drifts by more than 15% from day to day at 25 turns, so comparing against an older run would reject changes that did nothing.
- **Issue log:** keep one sheet of every failure found (clip or question ID, channel, mode, what went wrong, severity, commit, how to reproduce). Group the failures by type at the end; the top three become the "challenges faced" section of the video.
- **Publish the results.** Put the accuracy, voice and latency tables on the `/benchmarks` page and in the README.

## Working solo: daily rhythm and cut order

With one person there is no parallel work, so the protection against overrun is one buffer day (day 14) and a fixed cut order decided now.

- **Daily rhythm:** build in the morning, run `make eval-text` before lunch, run the phase's exit check in the evening, write three lines in a build log (what changed, what the numbers were, what broke). The build log becomes the video's "engineering decisions" script.
- **Checkpoint on day 7 (Thu 19 Nov):** if Phase 3 is not finished, apply the cut order below straight away.

If you fall behind, cut in this order. Items 1 to 4 lose polish only; nothing in the guideline's must-have list is dropped.

1. Settings page for the evaluator's own API keys (the guidelines recommend preconfigured keys anyway).
1. Inbound **and** callback on the phone: keep inbound only, since the guidelines ask for either.
1. Noise and accent variants of the audio set: keep the 30 clean clips.
1. Listening test with 5 raters: reduce to 3 raters and 5 clips per mode.
1. Twilio Media Streams into your own pipeline: switch to Twilio ConversationRelay, which is quicker to wire but uses Twilio's speech stack.
1. Automated audio-based latency measurement: fall back to browser and platform-reported numbers, and say so on the dashboard.

## Risks and mitigations

| Risk | Why it matters | Mitigation | When to check |
|---|---|---|---|
| Phone number and calling rules | Buying a local Indian number and calling Indian mobiles from a trial account can need extra verification; trial accounts usually call only verified numbers | Check on day 1. Buy a US number if that is quickest, upgrade the account, and state the number's country on the site | Day 1 |
| Free-tier credits run out mid-project | Five channels and repeated benchmarks use a lot of audio minutes | List each provider's free credit on day 1; run long benchmarks once, not after every change; set spend caps | Day 1, day 12 |
| Echo causes the agent to interrupt itself | Speaker audio leaks into the microphone and triggers barge-in | Browser echo cancellation on; raise the VAD threshold while the agent speaks; recommend headphones in the interface | Phase 2 |
| Low Latency mode cuts users off | A short silence timeout fires during natural pauses | Measure false cut-ins on the pause clips; use the provider's turn-detection model, not silence alone | Phase 3 |
| Speech-to-speech model answers from its own knowledge | It may skip the knowledge-base tool and invent prices | Prompt it to always search before stating a fact; score hallucination separately for this mode | Phase 3 |
| The "same agent" drifts across platforms | Prompt or tools edited in one dashboard and not the others | One shared prompt file and tool list; platform configs generated by script | Phase 5 |
| Platform latency numbers are not comparable | Each platform measures different start and end points | Headline number measured from recorded audio, the same way for all | Phase 6 |
| Cold starts on the host make the first turn slow | The evaluator's first impression is the first turn | Always-on instance; pre-warm provider connections when the page loads | Phase 7 |
| Live keys get abused | The site is public with your keys behind it | Short-lived tokens, per-IP limits, session time cap, daily spend caps | Phase 7 |
| Evaluator tests from a different region | Latency depends on distance to your backend and the providers | Measure the network round trip (browser to backend, backend to each provider) and show it on the dashboard with the test location and date; host near the providers | Phase 6 |

## Open questions and assumptions

- What is the real submission date? The guidelines say 14 July; this plan assumes 26 November.
- Does the bootcamp provide API credits, or is there a budget? This decides how many benchmark runs you can afford and whether to use the mini realtime model.
- Where will the evaluator test from, and will they call the phone number from India? This decides the number's country and the hosting region.
- Are any providers mandated by the bootcamp (the guidelines list Gemini, OpenAI, ElevenLabs, Deepgram and Cartesia as examples)?
- Is the product name **Voltline Electronics** acceptable, or do you want a different domain?
- Python backend assumed. Say if you would prefer Node.js end to end.

## Sources

Platform details behind the metric and integration choices; check each for current model names and APIs on day 1.

- [LiveKit: session traces and metrics reference](https://docs.livekit.io/reference/agents/session-traces/)
- [LiveKit community: replacement for the deprecated metrics event](https://community.livekit.io/t/metrics-collected-is-deprecated-whats-the-right-replacement/734)
- [The Voice Agent Latency Playbook (Hugging Face blog)](https://huggingface.co/blog/dvalle08/voice-agent-latency-playbook)
- [Twilio ConversationRelay](https://www.twilio.com/en-us/products/conversational-ai/conversationrelay)
- [Vapi: server events and tool calls](https://docs.vapi.ai/server-url/events.md)
- [Vapi: custom LLM](https://docs.vapi.ai/custom-llm)
- [OpenAI Realtime API overview and 2026 model timeline](https://www.dsebastien.net/2026-05-07-gpt-realtime-2-openais-voice-model-gets-gpt-5-class-reasoning/)
