# Findings: cascade against speech-to-speech

_13 November 2026. Run `evals/runs/v1` (commit 099df01 and later). Every figure has a 95% bootstrap interval in brackets, and a difference is claimed only when its interval excludes zero._

## The question

Two voice agents answer the same customer questions for an electronics store, with the same prompt and the same tools:

- **A, cascade:** Deepgram Nova-3 hears, Gemini 3.5 Flash-Lite thinks, Cartesia Sonic 3.6 speaks.
- **B, speech-to-speech:** Gemini 3.1 Flash Live hears, thinks and speaks.

Which is faster, more correct, better at hearing and turn-taking, and better sounding? And can the automatic measures be trusted to say so?

## Method in five lines

1. **Dataset:** 155 clips: 90 synthetic questions in clean, café-noise and telephone conditions; 15 Indian English synthetic voices; the owner's 20 real recordings; 16 scripted conversations. Every clip passes a quality check; `qa/datasheet.md` describes them.
2. **Paired, black-box runs:** each clip played through the real web page to A, then B, in a fresh call, both sides recorded (282 calls).
3. **Scored from the audio:** two transcripts of what each agent actually said (Whisper and Deepgram), a judge (DeepSeek) given the same evidence for both systems, tool logs, signal checks, and DNSMOS.
4. **Blind listening test:** 12 pairs plus 4 damaged anchors and a hidden repeat, loudness-matched, randomised per listener (2 listeners so far).
5. **Every defect logged** in `qa/issue-log.csv` (23 issues) with a reproduction rate.

## Results

| | A: cascade | B: speech-to-speech |
|---|---|---|
| Response time, median | 2.53 s [2.46, 2.59] | **2.12 s [2.10, 2.17]** |
| B minus A, same clips | | **−344 ms [−454, −263]** |
| Correct (judged on what was heard) | 100% (123 of 123) | 98% [94, 100] |
| Said a different number than its own text | **0% (0 of 43)** | 6% [0, 15] (3 of 48) |
| Hallucination | **0%** | 2% [0, 6] |
| Speech-provider failures (reported, not judged) | 2 of 125 | 0 of 125 |
| All key terms heard, synthetic speech | 99% | 95% |
| All key terms heard, owner's real speech | 71% | 59% |
| Scripted conversations passed | **88% [69, 100]** | 62% [38, 88] |
| Naturalness, listeners (1 to 5) | **3.58 [3.25, 3.92]** | 2.92 [2.67, 3.17] |
| Preference, listeners | **19 of 24** | 4 of 24 (sign test p = 0.003) |
| DNSMOS P.808 (predicted quality) | 3.95 [3.91, 3.98] | **4.05 [4.02, 4.08]** |

## What we found about the systems

**B is faster.** It answers 344 ms sooner on the same clips: it skips the speech-to-text and text-to-speech hops. Neither system meets a 1.5 s target; most of A's time is the language model.

**B can say a number other than the one it means.** For a $119.99 keyboard, B's audio says "$199.99" in all three conditions (3 of 3). Its own transcript says $119.99, so any evaluation that reads the model's text would mark it correct. Whisper, Deepgram and Gnani all hear $199.99. A never did this (0 of 43 replies with numbers). Issue QA-017.

**B invents what it did not hear.** Its input transcript lost two digits of a phone number; B filled in "0199", read it back and booked the callback before the caller confirmed it, against its instructions. Issue QA-018.

**A's speech provider can fail silently.** On 2 of 156 turns (R19, R20), A wrote a correct answer but sent no audio, or stopped mid-word, and the server logged nothing. The scorer caught it because it checks what was heard. Logging was added, and a replay showed the cause: the text-to-speech account had run out of credits mid-run, and the error was swallowed. Such calls are now reported as infrastructure failures rather than wrong answers, and the server records speech errors per turn. Issue QA-023.

**Both systems break on pauses and backchannels.** B answers into every mid-sentence pause of 1, 1.5 and 2 s (3 of 3). A waits through the longer ones but cuts in at 1 s when the fragment sounds complete. Both stop talking when the caller says "mm-hm". Issues QA-019, QA-020.

**Listeners prefer A, especially for longer answers** (explanations: A 4.0, B 2.6). The two listeners agree closely (Kendall's W 0.68), passed the anchor screen, and rated the hidden repeat consistently.

## What we found about the measurements

These shaped the result as much as the systems did.

1. **DNSMOS disagrees with people.** It scores B higher; both listeners prefer A. Across the 24 pair clips its correlation with listeners is 0.19 [−0.25, 0.58], effectively none. It also barely moves for a 300 ms dropout (3.95 to 3.62). Choosing a system by DNSMOS would have chosen the one people liked less.
2. **Synthetic speech hides recogniser weaknesses.** All four speech-to-text engines score 0 to 1% word error on synthetic speech, with overlapping intervals. On the owner's real Indian English speech:

   | Engine | Word error, real speech | All key terms heard |
   |---|---|---|
   | Gnani Prisma v3.0 (with key terms) | 12.7% [6.9, 19.0] | 94% |
   | Deepgram Nova-3 | 14.0% [8.0, 20.6] | 94% |
   | Gnani Prisma v2.5 | 14.7% [8.8, 21.6] | 71% |
   | Whisper small.en | 32.1% [22.4, 43.2] | 41% |

   Whisper, the tool used to transcribe the agents, is fine on the agents' synthetic voices but must not be used to judge what users said.
3. **The first judge was wrong in 10 of 13 hallucination flags.** The causes were a scorer bug (it replayed tools with incomplete arguments), missing evidence, and Whisper writing "five ninety-nine" as "$599". The fixes:
   - the judge now sees the agent's real tool calls and evidence retrieved for the reply;
   - a number counts against an agent only when two transcripts agree;
   - a new metric compares the number spoken with the number in the system's text.

   Issues QA-021, QA-022.
4. **Timing from real recordings needs care.** The end of the owner's speech was first taken from the last loud sample, which was the Enter-key click, and gave 4 ms "responses". Speech end now requires 80 ms of sustained sound, and noisy clips use the timing of their clean original.
5. **Data defects looked like model defects.** Deepgram's "9900 and 99" came from our clip builder reading "9999" as a cardinal number (QA-001). The iPhone's wireless microphone link dropped up to 70% of a recording (QA-016). Both were caught by data checks, not by the systems.

## Limits

- **Only 2 listeners.** Intervals resample ratings, not listeners, so they are optimistic.
- **One real speaker,** and synthetic accents are not real accents.
- **The two systems differ in more than architecture:** different language models and different voices. The claim is "A against B as configured".
- **The scripted conversations use one take each.** Turn-taking findings are 1 to 4 runs each.

## Regression cycle

The cascade's end-of-turn timeout was cut from 700 to 400 ms and measured against a repeat run of the unchanged system (the noise band). Details: [eval-log.md](eval-log.md).

- **Rejected.** Correctness and scripted conversations were unchanged. Response time moved +61 ms [−303, +525], far inside the noise band: the unchanged repeat itself drifted +466 ms.
- **The timeout is not the bottleneck.** The server's end of turn fell only from 712 to 663 ms, because the turn waits for Deepgram's final transcript, which arrives about 400 ms after the session asks for it at 300 ms of silence.
- **Hosted-model latency drifts.** The language model's first token moved from 1,286 to 1,657 ms in one afternoon with nothing changed. Latency comparisons need same-day repeat runs.

## What to test next

1. **More listeners** (at least 5) and more real speakers, starting with telephone audio.
2. **Number rendering in B,** with a dedicated set of prices, dates and order numbers.
3. **The real end-of-turn bottleneck:** send Finalize earlier, or end on a stable interim transcript.
4. **A backchannel rule for A,** such as ignoring speech under 600 ms that contains "mm-hm" or "okay" while the agent talks, measured against TT3 and the interruption scenarios.
