"""Dataset v1 additions to the core clips (needs CARTESIA_API_KEY).

  accent:      15 of the core questions in two Indian English voices (one male, one female),
               clean, so each pairs with the same question in the core set
  turn-taking: one clip per turn of the TT* scenarios in evals/scenarios.json; a {"noise": "cough"}
               turn is a synthetic cough, not speech

Existing files are kept (pass --force to remake them), so re-running costs no credits.
Output: evals/audio/clips/{accent,scenarios}/ and evals/audio/manifest-extra.jsonl.

Usage: uv run --project server python evals/audio/make_extra.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_clips import LEAD_SILENCE_S, OUT, RATE, ROOT, read_wav, synthesise, write_wav  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.voice.tts import CartesiaTTS  # noqa: E402

MANIFEST = ROOT / "evals" / "audio" / "manifest-extra.jsonl"
ACCENT_VOICES = {  # Cartesia voice library, language "en", described as Indian accented
    "devansh-in": "1259b7e3-cb8a-43df-9446-30971a46b8b0",
    "kiara-in": "f8f5f1b2-f02d-4d8e-a40d-fd850a487b3d",
}
TT_VOICES = ["daniel-us", "skylar-us", "archie-gb", "gemma-gb"]


def core_questions() -> list[dict]:
    """The same 30 questions make_clips.py uses, in the same order."""
    items = [json.loads(line) for line in (ROOT / "evals" / "questions.jsonl").read_text().splitlines() if line]
    single = [i for i in items if len(i["turns"]) == 1]
    return [i for i in single if i["category"] == "factual"][:21] + [i for i in single if i["category"] == "tool"][:9]


def cough(rng: np.random.Generator) -> np.ndarray:
    """Two short bursts of band-passed noise with a sharp attack: a cough-like non-speech sound."""
    out = [np.zeros(int(0.3 * RATE), np.float32)]
    for length_s, gain in ((0.22, 0.6), (0.16, 0.4)):
        n = int(length_s * RATE)
        noise = rng.standard_normal(n).astype(np.float32)
        spec = np.fft.rfft(noise)
        freqs = np.fft.rfftfreq(n, 1 / RATE)
        spec[(freqs < 300) | (freqs > 3500)] = 0
        burst = np.fft.irfft(spec, n).astype(np.float32)
        env = np.exp(-np.linspace(0, 6, n)) * np.minimum(1, np.linspace(0, 40, n))
        burst = burst / (np.abs(burst).max() + 1e-9) * env * gain
        out += [burst, np.zeros(int(0.12 * RATE), np.float32)]
    return np.concatenate(out)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="Remake clips that already exist")
    args = ap.parse_args()
    s = get_settings()
    if not s.cartesia_api_key:
        print("Set CARTESIA_API_KEY in .env first.", file=sys.stderr)
        return 1
    from make_clips import VOICES

    all_voices = {**VOICES, **ACCENT_VOICES}
    conns: dict[str, CartesiaTTS] = {}

    async def tts(name: str) -> CartesiaTTS:
        if name not in conns:
            conns[name] = await CartesiaTTS(s.cartesia_api_key, version=s.cartesia_version, model=s.tts_model,
                                            voice_id=all_voices[name], sample_rate=RATE).connect()
        return conns[name]

    lead = np.zeros(int(LEAD_SILENCE_S * RATE), np.float32)
    rng = np.random.default_rng(13)
    manifest, made = [], 0

    picked = core_questions()
    accent = [i for i in picked if i["category"] == "factual"][:10] + [i for i in picked if i["category"] == "tool"][:5]
    names = list(ACCENT_VOICES)
    for n, item in enumerate(accent):
        voice = names[n % len(names)]
        path = OUT / "accent" / f"{item['id']}.wav"
        if args.force or not path.exists():
            write_wav(path, np.concatenate([lead, await synthesise(await tts(voice), item["turns"][0])]))
            made += 1
        manifest.append({"kind": "question", "id": item["id"], "condition": "accent", "voice": voice,
                         "text": item["turns"][0], "path": str(path.relative_to(ROOT))})

    scenarios = [sc for sc in json.loads((ROOT / "evals" / "scenarios.json").read_text()) if sc["id"].startswith("TT")]
    for n, sc in enumerate(scenarios):
        voice = TT_VOICES[n % len(TT_VOICES)]
        for t, turn in enumerate(sc["turns"]):
            path = OUT / "scenarios" / f"{sc['id']}-{t + 1}.wav"
            if turn.get("noise"):
                if args.force or not path.exists():
                    write_wav(path, cough(rng))
                    made += 1
                manifest.append({"kind": "scenario", "id": sc["id"], "turn": t + 1, "voice": None,
                                 "text": "", "noise": turn["noise"], "path": str(path.relative_to(ROOT))})
                continue
            parts = [p.strip() for p in turn["text"].split("|")]
            if args.force or not path.exists():
                pieces = [lead]
                for i, part in enumerate(parts):
                    if i:
                        pieces.append(np.zeros(int(turn.get("pause_ms", 1500) / 1000 * RATE), np.float32))
                    pieces.append(await synthesise(await tts(voice), part))
                write_wav(path, np.concatenate(pieces))
                made += 1
            manifest.append({"kind": "scenario", "id": sc["id"], "turn": t + 1, "voice": voice,
                             "text": " ".join(parts), "path": str(path.relative_to(ROOT))})
        print(f"  {sc['id']}")

    for c in conns.values():
        await c.close()
    MANIFEST.write_text("".join(json.dumps(m) + "\n" for m in manifest))
    print(f"{made} clips made, {len(manifest)} listed in {MANIFEST.relative_to(ROOT)}")
    # Sanity: every listed file exists and is readable.
    for m in manifest:
        read_wav(ROOT / m["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
