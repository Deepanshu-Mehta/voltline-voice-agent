"""Record the "recorded" set in your own voice: 20 questions, 16 kHz mono WAV.

The files stay on this machine: data/private/ is git-ignored and never published.
Each prompt: press Enter to start, Enter to stop, then keep it, redo it or play it back.
Speak naturally, as you would to a support line; small hesitations are fine and useful.

Usage: uv run --project server python evals/audio/record.py [--from R07]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import queue
import sys
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_extra import core_questions  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "private" / "recorded"
MANIFEST = ROOT / "data" / "private" / "manifest-recorded.jsonl"
RATE = 16000


def prompts() -> list[dict]:
    """The 15 accent-set questions (so real speech pairs with synthetic accents) plus 5 more."""
    picked = core_questions()
    factual = [i for i in picked if i["category"] == "factual"]
    tool = [i for i in picked if i["category"] == "tool"]
    chosen = factual[:10] + tool[:5] + factual[10:15]
    return [{"rid": f"R{n:02d}", "qid": q["id"], "text": q["turns"][0]} for n, q in enumerate(chosen, start=1)]


def record() -> np.ndarray:
    frames: queue.Queue = queue.Queue()
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", callback=lambda d, *_: frames.put(d.copy())):
        input("  recording... press Enter to stop ")
    chunks = []
    while not frames.empty():
        chunks.append(frames.get())
    return np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, np.int16)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", help="Start at this prompt id, e.g. R07")
    args = ap.parse_args()
    items = prompts()
    if args.start:
        items = items[next(i for i, p in enumerate(items) if p["rid"] == args.start):]
    OUT.mkdir(parents=True, exist_ok=True)
    existing = {}
    if MANIFEST.exists():
        existing = {m["rid"]: m for m in map(json.loads, MANIFEST.read_text().splitlines())}
    device = sd.query_devices(kind="input")["name"]
    gender = input("Speaker gender for the manifest (f / m / other / skip): ").strip().lower() or "skip"
    accent = input("Your accent, in your own words (e.g. Indian English; Enter to skip): ").strip() or "unspecified"
    print(f"Microphone: {device}. Hold it as you normally would; a quiet room is best.\n")

    for p in items:
        print(f"{p['rid']}  ({p['qid']})  \"{p['text']}\"")
        while True:
            input("  press Enter, then speak ")
            pcm = record()
            peak = int(np.abs(pcm).max()) if len(pcm) else 0
            note = " (clipped: speak a little softer)" if peak >= 32000 else " (very quiet: move closer)" if peak < 2000 else ""
            print(f"  {len(pcm) / RATE:.1f} s, peak {peak}{note}")
            choice = input("  [Enter] keep  [r] redo  [p] play  [q] quit: ").strip().lower()
            if choice == "p":
                sd.play(pcm, RATE)
                sd.wait()
                choice = input("  [Enter] keep  [r] redo: ").strip().lower()
            if choice == "q":
                return 0
            if choice != "r":
                break
        path = OUT / f"{p['rid']}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm.astype("<i2").tobytes())
        existing[p["rid"]] = {"kind": "question", "rid": p["rid"], "id": p["qid"], "condition": "recorded",
                              "voice": "owner", "gender": gender, "accent": accent, "device": device, "text": p["text"],
                              "recorded_at": dt.datetime.now().isoformat(timespec="seconds"),
                              "path": str(path.relative_to(ROOT))}
        MANIFEST.write_text("".join(json.dumps(existing[k]) + "\n" for k in sorted(existing)))
        print("  saved\n")
    print(f"Done: {len(existing)} recordings in {OUT.relative_to(ROOT)} (local only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
