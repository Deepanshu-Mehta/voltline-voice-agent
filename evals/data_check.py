"""Dataset v1: build the manifest and run the quality checks on every clip (`make data-check`).

Sources: evals/audio/manifest.jsonl (core), evals/audio/manifest-extra.jsonl (accent, turn-taking),
data/private/manifest-recorded.jsonl (your own voice; local only, never committed).

Writes
  data/manifest.csv    one row per clip: who, what, how it was made, licence, expected answer
  data/qc-report.csv   one row per clip: every check, its value, and a status
Exits 1 if any clip has an error (unreadable, wrong format, missing reference, duplicate).

Checks
  format       16 kHz, mono, 16-bit PCM
  duration     0.5 s to 20 s
  clipping     share of samples at full scale (warn above 0.1%)
  silence      leading and trailing silence (warn above 1.5 s; the clips carry 0.3 s on purpose)
  loudness     RMS level of the voiced part in dBFS (warn below -35 or above -6)
  duplicate    identical audio under two clip ids (error)
  content      a local Whisper transcript against the reference text (warn when WER > 0.25):
               does the clip say what the manifest says it says?
  gender       the manifest's speaker gender against the median pitch measured by Praat
               (male below 150 Hz, female above 170 Hz, in between undecided; warn on a mismatch)

Usage: uv run --project server python evals/data_check.py [--no-whisper]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / ".cache" / "transcripts.json"
SOURCES = [ROOT / "evals/audio/manifest.jsonl", ROOT / "evals/audio/manifest-extra.jsonl",
           DATA / "private/manifest-recorded.jsonl"]

VOICES = {  # name: (gender, accent, origin)
    "daniel-us": ("male", "US English", "Cartesia Sonic 3.6 voice Daniel"),
    "skylar-us": ("female", "US English", "Cartesia Sonic 3.6 voice Skylar"),
    "archie-gb": ("male", "British English", "Cartesia Sonic 3.6 voice Archie"),
    "gemma-gb": ("female", "British English", "Cartesia Sonic 3.6 voice Gemma"),
    "devansh-in": ("male", "Indian English", "Cartesia Sonic 3.6 voice Devansh"),
    "kiara-in": ("female", "Indian English", "Cartesia Sonic 3.6 voice Kiara"),
}
CONDITIONS = {
    "clean": ("synthetic", None, "as synthesised"),
    "noisy": ("synthetic", 10.0, "cafe-style babble (other synthetic voices) plus pink noise at 10 dB SNR"),
    "phone": ("synthetic", None, "simulated telephone: 300-3400 Hz band, 8 kHz, mu-law, back to 16 kHz"),
    "accent": ("synthetic", None, "as synthesised, Indian English voices"),
    "recorded": ("recorded", None, "recorded by the project owner on a consumer microphone"),
    "scenario": ("synthetic", None, "scripted conversation turn; '|' pauses inserted as silence"),
}
LICENCE = {
    "synthetic": "generated for this project with the Cartesia API; project use",
    "recorded": "project owner's own voice; private, not for distribution",
    "noise": "generated for this project (numpy); project use",
}
MALE_MAX_HZ, FEMALE_MIN_HZ = 150.0, 170.0
NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}


def load_clips() -> list[dict]:
    clips = []
    for src in SOURCES:
        if not src.exists():
            continue
        for line in src.read_text().splitlines():
            if line.strip():
                m = json.loads(line)
                m["source_manifest"] = str(src.relative_to(ROOT))
                clips.append(m)
    return clips


def clip_id(m: dict) -> str:
    if m["kind"] == "scenario":
        return f"{m['id']}-{m['turn']}"
    if m.get("condition") == "recorded":
        return m["rid"]
    return f"{m['condition']}-{m['id']}"


def questions() -> dict[str, dict]:
    items = [json.loads(line) for line in (ROOT / "evals/questions.jsonl").read_text().splitlines() if line]
    return {i["id"]: i for i in items}


def norm_words(text: str) -> list[str]:
    t = text.lower().replace("-", " ")
    t = re.sub(r"(\d),(\d)", r"\1\2", t)
    t = re.sub(r"[^a-z0-9.' ]", " ", t)
    words = []
    for w in t.split():
        w = w.strip(".'")
        if not w:
            continue
        if w in TENS:
            words.append(str(TENS[w]))
        elif w in NUMBER_WORDS and words and words[-1].isdigit() and len(words[-1]) == 2 and words[-1].endswith("0") \
                and int(NUMBER_WORDS[w]) < 10 and words[-2:-1] != ["point"]:
            words[-1] = str(int(words[-1]) + int(NUMBER_WORDS[w]))  # "twenty four" -> 24
        else:
            words.append(NUMBER_WORDS.get(w, w))
    # "4 5 1 2" and "4512" are the same order number
    joined = " ".join(words)
    joined = re.sub(r"\b(\d)(?: (\d))(?: (\d))(?: (\d))\b", lambda m: "".join(m.groups()), joined)
    joined = re.sub(r"\b(\d+)(ghz|k)\b", r"\1 \2", joined)
    joined = re.sub(r"\bhmm\b", "hm", joined)
    return joined.replace("canceling", "cancelling").replace("gigahertz", "ghz").replace("wifi", "wi fi").split()


def merge_compounds(ref: list[str], hyp: list[str]) -> list[str]:
    """"safe view" and "dock line" are "SafeView" and "Dockline" written apart: join a pair of
    words in the transcript when the reference has them as one word."""
    vocab, out, i = set(ref), [], 0
    # The other direction: "pulsebuds" where the reference says "pulse buds".
    pairs = {a + b: (a, b) for a, b in zip(ref, ref[1:])}
    hyp = [w for word in hyp for w in (pairs[word] if word in pairs and word not in vocab else (word,))]
    while i < len(hyp):
        if i + 1 < len(hyp) and hyp[i] + hyp[i + 1] in vocab:
            out.append(hyp[i] + hyp[i + 1])
            i += 2
        else:
            out.append(hyp[i])
            i += 1
    return out


def wer(ref: str, hyp: str) -> float:
    r = norm_words(ref)
    h = merge_compounds(r, norm_words(hyp))
    if not r:
        return 0.0 if not h else 1.0
    d = np.arange(len(h) + 1)
    for i, rw in enumerate(r, 1):
        prev, d[0] = d.copy(), i
        for j, hw in enumerate(h, 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (rw != hw))
    return round(float(d[len(h)]) / len(r), 3)


def read(path: Path) -> tuple[dict, np.ndarray]:
    with wave.open(str(path), "rb") as w:
        fmt = {"rate": w.getframerate(), "channels": w.getnchannels(), "width": w.getsampwidth()}
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768 if fmt["width"] == 2 else np.zeros(0, np.float32)
    if fmt["channels"] > 1:
        x = x.reshape(-1, fmt["channels"]).mean(axis=1)
    return {**fmt, "sha": hashlib.sha256(raw).hexdigest()[:16]}, x


def voiced_bounds(x: np.ndarray, rate: int) -> tuple[float, float, float]:
    """Leading silence, trailing silence (s), and RMS dBFS of the voiced part, from 20 ms frames."""
    hop = int(0.02 * rate)
    n = len(x) // hop
    if n == 0:
        return 0.0, 0.0, -120.0
    frames = x[: n * hop].reshape(n, hop)
    level = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    active = np.nonzero(level > max(level.max() - 35, -55))[0]
    if len(active) == 0:
        return len(x) / rate, 0.0, -120.0
    lead, trail = active[0] * hop / rate, (n - 1 - active[-1]) * hop / rate
    voiced = frames[active[0]: active[-1] + 1].reshape(-1)
    return round(lead, 2), round(trail, 2), round(20 * np.log10(np.sqrt((voiced ** 2).mean()) + 1e-9), 1)


def median_f0(x: np.ndarray, rate: int) -> float | None:
    import parselmouth

    pitch = parselmouth.Sound(x.astype(np.float64), sampling_frequency=rate).to_pitch(pitch_floor=60, pitch_ceiling=400)
    f0 = pitch.selected_array["frequency"]
    f0 = f0[f0 > 0]
    return round(float(np.median(f0)), 1) if len(f0) > 20 else None


def gender_from_f0(f0: float | None) -> str:
    if f0 is None:
        return "unknown"
    return "male" if f0 < MALE_MAX_HZ else "female" if f0 > FEMALE_MIN_HZ else "undecided"


class Transcriber:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        self.model = None

    def __call__(self, sha: str, audio: np.ndarray) -> str | None:
        """Transcribe 16 kHz float audio (passed as an array: this faster-whisper release cannot
        open files with the installed PyAV)."""
        if sha in self.cache:
            return self.cache[sha]
        if not self.enabled:
            return None
        if self.model is None:
            from faster_whisper import WhisperModel

            self.model = WhisperModel("small.en", device="cpu", compute_type="int8")
        segments, _ = self.model.transcribe(audio.astype(np.float32), language="en", beam_size=5, vad_filter=False)
        self.cache[sha] = " ".join(s.text.strip() for s in segments).strip()
        return self.cache[sha]

    def save(self) -> None:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(self.cache, indent=0, sort_keys=True))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-whisper", action="store_true", help="Skip the content check (no model download)")
    args = ap.parse_args()
    clips, qs = load_clips(), questions()
    transcribe = Transcriber(not args.no_whisper)
    manifest_rows, qc_rows, seen = [], [], {}
    errors = warnings = 0

    for m in clips:
        cid = clip_id(m)
        path = ROOT / m["path"]
        cond = "scenario" if m["kind"] == "scenario" else m["condition"]
        noise = m.get("noise")
        source_type, snr, how = CONDITIONS[cond]
        voice = m.get("voice")
        gender, accent, origin = VOICES.get(voice, (m.get("gender", "unspecified"), m.get("accent", "unspecified"),
                                                    "project owner" if voice == "owner" else "none"))
        q = qs.get(m["id"], {})
        expected = q.get("expected_tool") or {}
        manifest_rows.append({
            "clip_id": cid, "set": "turn-taking" if m["id"].startswith("TT") else
            "scenario" if cond == "scenario" else "accent" if cond == "accent" else
            "recorded" if cond == "recorded" else "core",
            "question_id": m["id"], "turn": m.get("turn", ""), "condition": cond, "source_type": "noise" if noise else source_type,
            "origin": "synthetic cough (numpy)" if noise else origin, "licence": LICENCE["noise" if noise else source_type],
            "speaker": voice or "", "gender": gender if not noise else "", "accent": accent if not noise else "",
            "snr_db": snr if snr is not None else "", "processing": how, "reference_text": m.get("text", ""),
            "key_terms": "; ".join(sorted({t for t in re.findall(r"\b\d{4}\b|\b[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z0-9]+)*", m.get("text", ""))
                                          if t not in {"What", "How", "Do", "Does", "Is", "Can", "Are", "Will", "Tell", "And",
                                                       "Where", "The", "Sorry", "Hi", "My", "Yes", "No", "Okay", "Um", "Just"}})),
            "expected_tool": expected.get("name", ""), "expected_args": json.dumps(expected.get("args", {})) if expected else "",
            "path": m["path"], "private": "yes" if cond == "recorded" else "no",
        })
        row = {"clip_id": cid, "path": m["path"]}
        problems: list[str] = []
        status = "ok"
        if not path.exists():
            row.update(status="error", problems="file missing")
            qc_rows.append(row)
            errors += 1
            continue
        fmt, x = read(path)
        rate = fmt["rate"]
        dur = len(x) / rate if rate else 0
        lead, trail, level = voiced_bounds(x, rate)
        clipped = float((np.abs(x) >= 0.999).mean()) if len(x) else 0.0
        row.update(rate=rate, channels=fmt["channels"], bits=fmt["width"] * 8, duration_s=round(dur, 2),
                   lead_silence_s=lead, trail_silence_s=trail, voiced_dbfs=level,
                   clipped_pct=round(clipped * 100, 3), sha256_16=fmt["sha"])
        if (rate, fmt["channels"], fmt["width"]) != (16000, 1, 2):
            problems.append(f"format {rate} Hz/{fmt['channels']} ch/{fmt['width'] * 8} bit")
            status = "error"
        if fmt["sha"] in seen and seen[fmt["sha"]] != cid:
            problems.append(f"same audio as {seen[fmt['sha']]}")
            status = "error"
        seen.setdefault(fmt["sha"], cid)
        if not noise and not m.get("text"):
            problems.append("no reference text")
            status = "error"
        if not 0.5 <= dur <= 20:
            problems.append(f"duration {dur:.1f} s")
            status = "error" if status == "error" else "warn"
        if clipped > 0.001:
            problems.append(f"clipped {clipped:.2%}")
        if lead > 1.5 or trail > 1.5:
            problems.append(f"silence lead {lead} s / trail {trail} s")
        if not noise and not -35 <= level <= -6:
            problems.append(f"level {level} dBFS")
        if not noise:
            f0 = median_f0(x, rate)
            measured = gender_from_f0(f0)
            row.update(median_f0_hz=f0, gender_label=gender, gender_from_pitch=measured)
            # Per-clip pitch is noisy (babble, telephone band, expressive questions), so a clip-level
            # disagreement is information only; the verdict is per speaker, below.
            row["gender_clip_flag"] = ("mismatch" if gender in ("male", "female") and measured in ("male", "female")
                                       and measured != gender else "")
            row["speaker"], row["condition"] = voice or "", cond
            text = transcribe(fmt["sha"], x) if rate == 16000 else None
            if text is not None:
                ref_text = m.get("text", "").replace("|", " ")
                w = wer(ref_text, text)
                heard = "".join(merge_compounds(norm_words(ref_text), norm_words(text)))
                missing = [t for t in manifest_rows[-1]["key_terms"].split("; ")
                           if t and "".join(norm_words(t)) not in heard]
                row.update(whisper_text=text, content_wer=w, key_terms_missing="; ".join(missing))
                if w > 0.25:
                    problems.append(f"content WER {w}")
                if missing:
                    problems.append(f"key term not heard: {', '.join(missing)}")
        if status == "ok" and problems:
            status = "warn"
        row.update(status=status, problems="; ".join(problems))
        errors += status == "error"
        warnings += status == "warn"
        qc_rows.append(row)

    # Gender check per speaker: median pitch over their clips without added noise or telephone
    # processing, against the speaker's label.
    speakers: dict[str, list[float]] = {}
    labels = {r["speaker"]: r["gender"] for r in manifest_rows if r["speaker"]}
    for r in qc_rows:
        if r.get("median_f0_hz") and r.get("condition") not in ("noisy", "phone"):
            speakers.setdefault(r["speaker"], []).append(r["median_f0_hz"])
    speaker_rows = []
    for spk, f0s in sorted(speakers.items()):
        med = round(float(np.median(f0s)), 1)
        measured = gender_from_f0(med)
        verdict = ("mismatch" if labels.get(spk) in ("male", "female") and measured in ("male", "female")
                   and measured != labels[spk] else "undecided" if measured == "undecided" else "ok")
        flagged = sum(1 for r in qc_rows if r.get("speaker") == spk and r.get("gender_clip_flag"))
        speaker_rows.append({"speaker": spk, "label": labels.get(spk, ""), "clips": len(f0s), "median_f0_hz": med,
                             "from_pitch": measured, "verdict": verdict, "clips_flagged_individually": flagged})
        if verdict == "mismatch":
            for r in qc_rows:
                if r.get("speaker") == spk:
                    r["problems"] = "; ".join(filter(None, [r["problems"], f"speaker gender mismatch ({med} Hz)"]))
                    if r["status"] == "ok":
                        r["status"] = "warn"
                        warnings += 1

    transcribe.save()
    DATA.mkdir(exist_ok=True)
    # The owner's recordings: full details only in the git-ignored private copy; the public files
    # keep the clip id and question, not who spoke, how, or what Whisper heard.
    private_ids = {r["clip_id"] for r in manifest_rows if r["private"] == "yes"}
    if private_ids:
        (DATA / "private").mkdir(parents=True, exist_ok=True)
        with open(DATA / "private" / "manifest-private.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(manifest_rows[0]))
            w.writeheader()
            w.writerows(r for r in manifest_rows if r["clip_id"] in private_ids)

    def public(r: dict) -> dict:
        if r.get("clip_id") not in private_ids:
            return r
        hidden = {"speaker", "gender", "accent", "path", "whisper_text", "median_f0_hz", "gender_label",
                  "gender_from_pitch", "sha256_16", "origin"}
        return {k: ("private" if k in hidden and v not in ("", None) else v) for k, v in r.items()}

    manifest_rows = [public(r) for r in manifest_rows]
    qc_rows = [public(r) for r in qc_rows]
    speaker_rows = [r if r["speaker"] != "owner" else {**r, "label": "private", "median_f0_hz": "private", "from_pitch": "private", "verdict": "private"}
                    for r in speaker_rows]
    for name, rows in (("manifest.csv", manifest_rows), ("qc-report.csv", qc_rows), ("speakers.csv", speaker_rows)):
        cols: list[str] = []
        for r in rows:
            cols += [k for k in r if k not in cols]
        with open(DATA / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)

    by_set: dict[str, int] = {}
    for r in manifest_rows:
        by_set[r["set"]] = by_set.get(r["set"], 0) + 1
    print(f"{len(manifest_rows)} clips: " + ", ".join(f"{k} {v}" for k, v in sorted(by_set.items())))
    print(f"quality check: {len(qc_rows) - errors - warnings} ok, {warnings} warnings, {errors} errors")
    flagged = sum(1 for r in qc_rows if r.get("gender_clip_flag"))
    print(f"gender by speaker ({flagged} clips disagree individually; information only):")
    for r in speaker_rows:
        print(f"  {r['speaker']:11} label {r['label']:7} median {r['median_f0_hz']:6} Hz over {r['clips']:2} clips "
              f"-> {r['from_pitch']:9} {r['verdict']}")
    for r in qc_rows:
        if r["status"] != "ok":
            print(f"  {r['status']:5} {r['clip_id']:24} {r['problems']}")
    print("Wrote data/manifest.csv, data/qc-report.csv and data/speakers.csv")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
