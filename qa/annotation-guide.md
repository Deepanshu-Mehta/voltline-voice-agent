# Annotation guide: defects in agent audio

_For labelling the 30 agent replies in `qa/annotation/` (15 per system) in Audacity. About 60 to 90 minutes._

## Why

The automatic detectors in `evals/score_run.py` flag clipping, long silences, abrupt endings and low predicted quality. Human labels show how often those flags are right and what they miss. You label without knowing which clips the detectors flagged: half of the 30 were flagged and half were not, and the file names do not say which.

## Setup

1. Open Audacity and drag in a file from `qa/annotation/`. Use headphones.
2. Add a label track: **Tracks > Add New > Label Track**.
3. Play the clip once through without stopping, then go back over anything that sounded wrong.
4. To label a defect, select its time range on the waveform and press **Ctrl+B** (**Cmd+B** on a Mac). Type one label from the list below. For a whole-clip label, select all (Cmd+A) first.
5. When the clip is done: **File > Export > Export Labels**, saved next to the audio with the same name and `.txt` (for example `ann-07.txt`). If the clip has no defect, export an empty label track anyway, so "nothing wrong" is recorded rather than left unknown.

## Labels

Use exactly these words, one label per defect. Two defects at the same time get two labels.

| Label | Use it when | Not when |
|---|---|---|
| `clipping` | Harsh, crackly distortion on loud parts | The voice is merely loud |
| `dropout` | Sound vanishes mid-word or mid-sentence, then returns | A natural pause between sentences |
| `cutoff` | The reply stops in the middle of a word or sentence | It ends at the end of a sentence |
| `long-silence` | A pause inside the reply long enough to make you think it had finished (about 1.5 s or more) | Short pauses between sentences |
| `mispronounced` | A word is said wrongly, such as a product name or "Wi-Fi" | An accent you would accept from a person |
| `wrong-number` | A price, date or number is read in a way a customer would misunderstand ("forty-five twelve" for order 4512) | A number read in an unusual but clear way |
| `glitch` | A click, pop, warble, metallic sound or repeated fragment | Breath sounds |
| `voice-change` | The voice seems to change speaker, gender or character partway through | Normal expressive intonation |
| `noise` | Background hiss, hum or other noise | Silence |
| `robotic` | Flat, mechanical delivery over the whole clip (whole-clip label) | One odd word (use `mispronounced`) |

If something is wrong and none of these fit, use `other: ` followed by a few words.

## Consistency check

At the end of the session, label clips `ann-01` to `ann-10` again into `qa/annotation/second-pass/`, without looking at your first labels. The agreement between the two passes (Cohen's kappa) is reported as a self-consistency figure.

## When you are done

Run `make annotations`. It merges every `.txt` file into `qa/annotations.csv`, and then compares the labels with the detectors.
