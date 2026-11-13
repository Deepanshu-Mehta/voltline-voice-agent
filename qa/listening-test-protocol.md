# Listening test protocol: system A against system B

_Fixed on 13 November 2026, before any ratings were collected. Changes after that date are listed at the end with the reason._

## Question

When the two voice agents answer the same customer question, which one do listeners find more natural, and which defects do they hear? A second question: does DNSMOS, an automatic quality predictor, agree with them?

## Material

- **12 pairs.** In each pair, system A (the cascade) and system B (speech-to-speech) answer the same question, taken from the paired run `evals/runs/v1`. There are 4 short facts, 4 answers containing numbers (prices, order numbers, dates) and 4 longer explanations.
- **Selection rule:** pairs are chosen by a fixed rule before listening (see Changes below for the version used). Only clean-condition calls where both systems answered without a driver error qualify. No clip is chosen or swapped after being heard.
- **4 hidden anchors**, made worse on purpose from A or B replies outside the 12 pairs: hard clipping, a 300 ms dropout mid-word, telephone band (300 to 3,400 Hz) and cafe noise at 5 dB. They appear as single clips among the others.
- **1 hidden repeat:** one pair is played a second time, with A and B in the other order.
- **Only agent audio.** Each clip is the agent's reply alone, cut from the right channel of the recording: no customer voice, and never the owner's recordings.

## Preparation (scripted: `evals/listening/build.py`)

1. Cut the reply: from the agent's first sound to its last, with 150 ms either side.
2. Loudness-match every clip to -23 LUFS (approximated by the RMS of the voiced part), so loudness cannot decide preference.
3. Give each clip a random id. The key from clip to system is stored separately and is not visible on the page.
4. Randomise the order of pairs, and the order within each pair, separately for each listener (seeded by their listener id).

## What each listener does

- Uses headphones in a quiet place, and plays one example first to set the volume.
- For each clip, rates **naturalness** from 1 to 5:
  1. Clearly machine-like or broken
  2. Mostly machine-like
  3. Acceptable
  4. Mostly natural
  5. Like a person
- For each pair, chooses **A, B or no preference** (A and B here are just the left and right positions on the page).
- For each clip, ticks any **defects heard**: glitch or click, cut off, wrong or slurred word, odd pacing or pause, robotic tone, background noise.
- Clips can be replayed. There is no time limit. The test takes about 8 minutes.

## Listeners

- At least 3, recruited by the owner. Each gets a listener id (L1, L2 and so on). No name is stored.
- The owner also takes the test, as **L0**. L0 knows both voices, so is not blind, and is reported separately and left out of the averages.

## Screening, fixed in advance

- A listener who rates **two or more anchors 4 or higher** is reported but left out of the averages: they could not hear obvious damage.
- **Consistency** on the hidden repeat is reported for every listener. It is not used to exclude anyone.

## Analysis

- **Naturalness:** mean score per system with a 95% bootstrap interval (resampling listeners and clips), plus the paired difference B minus A per pair.
- **Preference:** count of A, B and no-preference choices, with a two-sided sign test on A against B (no-preference left out).
- **Agreement between listeners:** Kendall's W on the naturalness scores of the 24 pair clips.
- **Defects:** how often each was ticked, per system.
- **DNSMOS against people:** Spearman correlation between the clip's mean naturalness and its DNSMOS P.808 score, over the 24 pair clips plus the 4 anchors, with a bootstrap interval. It is reported overall and within each system. If it agrees overall but not within a system, DNSMOS is ranking the voice, not the quality.
- With 3 to 5 listeners and 28 clips, intervals will be wide. The report states them as they are.

## Changes after 13 November

- **13 November, before any ratings:** typing by the reference answer gave only 2 "longer explanation" questions, because most answers contain a number. Pairs are now typed by measured reply length, the mean of A and B: the 4 longest are explanations, the 4 shortest of the rest are short facts, then the first 4 by id of the rest whose answer contains a number or a tool result. The rule is still mechanical, and nothing was heard before choosing it.
