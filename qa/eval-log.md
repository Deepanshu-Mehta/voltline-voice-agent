# Evaluation log

One entry per change cycle: measure, change, re-measure, decide by a rule written before the run.

## 2026-11-13: cascade end-of-turn timeout, 700 ms to 400 ms

**Hypothesis.** System A waits 700 ms of silence before it ends the caller's turn. Cutting that to 400 ms should make replies about 300 ms faster, at some risk of cutting callers off mid-sentence.

**Decision rule, written before the run.** Reject if correctness or turn-taking gets worse by more than the noise band, the difference between two runs of the unchanged system. Accept only if the speed gain is larger than the noise band.

**Runs.** All on system A, the same 31 items (15 clean questions and the 16 scripted conversations), same commit, same day, judge DeepSeek:

| Run | Setting | Purpose |
|---|---|---|
| `v1` | 700 ms | The original paired run |
| `a-700-repeat` | 700 ms | Noise band: nothing changed |
| `a-400` | 400 ms | The change |

**Results.** `evals/compare_runs.py`, paired on shared clips:

| | Noise band (v1 to repeat) | Change (repeat to 400 ms) |
|---|---|---|
| Response time, median change | +466 ms [−15, +663] | +61 ms [−303, +525] |
| Correct | 100% to 100% | 100% to 100% |
| Scripted conversations passed | 88% to 88% | 88% to 88% |
| Pass/fail flips | 1 (key facts, F03) | 1 (key terms, F13) |
| Server end of turn, median | 727 to 712 ms | 712 to **663 ms** |

**Decision: reject.** Nothing got worse beyond the noise band, but nothing got better either. The server's own end of turn fell by only 49 ms, and the response time change is far inside the noise band. Even with nothing changed, the language model's first token drifted from 1,286 to 1,657 ms in the same afternoon.

**Why the gain was small.** The timeout is not the bottleneck. At 300 ms of silence the session asks Deepgram to finalise, and the final transcript takes about 400 ms to arrive, so the turn cannot end much before 700 ms whatever the timeout. A shorter timeout only adds the risk of cutting people off.

**Next.** Send Finalize earlier (at 150 ms), or end the turn on a stable interim transcript, and measure with the turn-taking scripts. That attacks the real bottleneck.

**Also learned.** Single-run latency comparisons are not trustworthy on hosted models: the noise band was 466 ms. Comparisons need a repeat run on the same day, and ideally interleaved runs.
