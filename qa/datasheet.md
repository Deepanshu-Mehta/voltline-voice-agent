# Datasheet: Voltline voice test set, v1

_13 November 2026. Manifest: `data/manifest.csv`. Quality report: `data/qc-report.csv`. Rebuild and check: `make data-check`._

## What it is for

The set tests two voice agents for an online electronics store against each other: a cascade (speech-to-text, then an LLM, then text-to-speech) and a speech-to-speech model. It covers answering questions, using tools, coping with accents and noise, and turn-taking. It is also used to compare speech-to-text engines on their own (`make stt-compare`).

It is a test set, not training data, and it is small. Treat any difference smaller than its confidence interval as no difference.

## What is in it

135 clips, 7.0 minutes, 16 kHz mono 16-bit WAV. Clips last 0.9 to 8.3 s (median 2.9 s).

| Set | Clips | Content | Speakers |
|---|---|---|---|
| Core | 90 | 30 questions (21 facts, 9 tool tasks) in three conditions: clean, cafe noise, telephone | 4 synthetic voices: US male and female, British male and female |
| Accent | 15 | 15 of the same questions, clean | 2 synthetic Indian English voices, male and female |
| Scenarios | 17 | 8 scripted conversations, one clip per turn | the 4 core voices |
| Turn-taking | 13 | 8 scripts: pauses of 1 s and 2 s mid-sentence, a "mm-hm" backchannel, interruptions at 0.4 s and 4 s, a cough, a self-correction, hesitation fillers | the 4 core voices; the cough is generated noise |
| Recorded | 0 of 20 planned | 20 questions (the 15 accent questions plus 5 more) in the project owner's own voice | 1 real speaker; local only (see Privacy) |

Each question clip has a matching row in `evals/questions.jsonl` with the reference answer, the facts the answer must contain, and the tool and arguments expected. The manifest carries those, plus speaker, gender, accent, condition, signal-to-noise ratio, processing, licence and a checksum.

## How it was made

- **Speech:** Cartesia Sonic 3.6 text-to-speech (`evals/audio/make_clips.py`, `evals/audio/make_extra.py`), 0.3 s of silence before each clip. Order numbers are read digit by digit, as callers do.
- **Cafe noise:** four other synthetic voices from the set at random offsets plus pink noise, mixed at 10 dB signal-to-noise.
- **Telephone:** band-limited to 300 to 3,400 Hz, resampled to 8 kHz, μ-law coded, and back to 16 kHz. This is a **simulation**: no clip was recorded from a real call.
- **Pauses** in turn-taking scripts are inserted silence (marked `|` in the script), not a natural pause.
- **Cough:** two bursts of band-passed noise with a sharp attack. It is a stand-in, and sounds like one.
- **Recorded set:** `make record` prompts each question and records from the default microphone at 16 kHz. The microphone name is stored with each clip.

## Quality checks (`make data-check`)

Every clip, every run:

| Check | Rule | Result on v1 |
|---|---|---|
| Format | 16 kHz, mono, 16-bit | all pass |
| Duration | 0.5 to 20 s | all pass |
| Clipping | under 0.1% of samples at full scale | all pass |
| Silence | under 1.5 s before and after the speech | all pass |
| Loudness | voiced part between -35 and -6 dBFS | all pass (-29.7 to -10.8, median -18.4) |
| Duplicates | no identical audio under two ids | none |
| Content | a local Whisper transcript (small.en) against the reference: word error rate up to 0.25, and every product name and order number heard | 1 warning: accent-F10 |
| Gender | the speaker's label against the median pitch measured by Praat, per speaker, on clips without added noise or telephone processing | 5 speakers agree, 1 undecided |

How the checks were checked:

- **Gender.** Judged per clip with fixed thresholds (male below 150 Hz, female above 170 Hz), the check flagged 9 of 118 clips whose labels are known to be right. Babble noise and the telephone band push measured pitch up, and expressive questions vary. Judged per speaker, there are no false alarms. The Indian English male voice (median 157 Hz) falls in the undecided band, so the check cannot vouch for it.
- **Content.** The first version flagged correct transcripts for spelling: "safe view" against "SafeView", "5GHz" against "5 gigahertz", "PowerBank" against "power bank". The scorer now normalises compounds, units and spoken numbers, and the content check has no false alarms on v1.

## Known problems and gaps

- **accent-F10** (issue QA-011): Whisper hears "same few" and Gnani "safe few" instead of "SafeView", while Deepgram hears it correctly. The synthetic voice may mispronounce it. It needs a listen before results on this clip are trusted.
- **T06 was defective until 13 November** (issue QA-001): the builder read "9999" as "nine thousand nine hundred ninety-nine". It has been fixed and regenerated; results from before then on T06 are not comparable.
- **Synthetic speech is easy.** Every engine tested scores at or near ceiling on it, with overlapping intervals. The recorded set exists to show how real speech differs.
- **One real speaker,** and the synthetic accents are a voice library's idea of an accent, not real accented speech.
- **No real calls, no real noise recordings,** and no children's or older voices.
- **English only.**

## Privacy and licences

- **Synthetic clips:** generated for this project through the Cartesia API; used under the project's API account terms.
- **Generated noise:** made for this project.
- **Recorded set:** the project owner's own voice. It is stored only in `data/private/`, which git ignores. It is not committed, published, used in the demo video or played to listeners. The public manifest lists those clips by id only.

## Changelog

- **v1, 13 November 2026:** core, accent, scenario and turn-taking sets; quality checks; T06 regenerated (digits).
