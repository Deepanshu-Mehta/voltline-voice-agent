"""Build the audio test set with Cartesia voices (needs CARTESIA_API_KEY).

  questions: 30 single-turn questions from evals/questions.jsonl, in three conditions
             clean   - as synthesised
             noisy   - with cafe-style babble and pink noise mixed in at 10 dB SNR
             phone   - band-limited to 300-3400 Hz, 8 kHz, mu-law, back to 16 kHz
  scenarios: one clip per scripted turn in evals/scenarios.json ("|" marks a mid-sentence pause)

Voices rotate across US and British speakers for accent variety. Output: 16 kHz mono WAV files
under evals/audio/clips/ and evals/audio/manifest.jsonl.

Usage: uv run --project server python evals/audio/make_clips.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

from app.config import get_settings  # noqa: E402
from app.voice.tts import CartesiaTTS  # noqa: E402

OUT = ROOT / "evals" / "audio" / "clips"
MANIFEST = ROOT / "evals" / "audio" / "manifest.jsonl"
RATE = 16000
VOICES = {  # Cartesia voice ids from the Sonic voice guide
    "daniel-us": "47c38ca4-5f35-497b-b1a3-415245fb35e1",
    "skylar-us": "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4",
    "archie-gb": "ef191366-f52f-447a-a398-ed8c0f2943a1",
    "gemma-gb": "62ae83ad-4f6a-430b-af41-a9bede9286ca",
}
SNR_DB = 10.0
LEAD_SILENCE_S = 0.3


def write_wav(path: Path, samples: np.ndarray, rate: int = RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x**2)) + 1e-9)


def pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n)
    spectrum[1:] /= np.sqrt(freqs[1:])
    noise = np.fft.irfft(spectrum, n)
    return noise / rms(noise)


def add_noise(speech: np.ndarray, babble_pool: list[np.ndarray], rng: np.random.Generator) -> np.ndarray:
    """Cafe-like background: four other voices at random offsets plus pink noise, at SNR_DB."""
    n = len(speech)
    babble = np.zeros(n, np.float32)
    for other in rng.choice(len(babble_pool), size=min(4, len(babble_pool)), replace=False):
        clip = np.resize(babble_pool[other], n)
        babble += np.roll(clip, int(rng.integers(0, n)))
    background = babble / rms(babble) + 0.5 * pink_noise(n, rng)
    background *= rms(speech) / rms(background) / (10 ** (SNR_DB / 20))
    return speech + background


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    n = int(len(x) * dst / src)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def telephone(x: np.ndarray) -> np.ndarray:
    narrow = resample(x, RATE, 8000)
    spectrum = np.fft.rfft(narrow)
    freqs = np.fft.rfftfreq(len(narrow), 1 / 8000)
    spectrum[(freqs < 300) | (freqs > 3400)] = 0
    narrow = np.fft.irfft(spectrum, len(narrow))
    mu = 255.0  # G.711 mu-law companding, quantised to 8 bits
    companded = np.sign(narrow) * np.log1p(mu * np.abs(np.clip(narrow, -1, 1))) / np.log1p(mu)
    companded = np.round(companded * 127) / 127
    narrow = np.sign(companded) * (np.power(1 + mu, np.abs(companded)) - 1) / mu
    return resample(narrow.astype(np.float32), 8000, RATE)


def spoken(text: str) -> str:
    """Callers read order numbers digit by digit: "4512" -> "4 5 1 2". All four-digit numbers:
    until 2026-11-13 only those starting with 4 were split, so "9999" (T06) was read as "nine
    thousand nine hundred ninety-nine" (issue QA-001)."""
    return re.sub(r"\b\d{4}\b", lambda m: " ".join(m.group(0)), text)


async def synthesise(tts: CartesiaTTS, text: str) -> np.ndarray:
    text = spoken(text)
    ctx = uuid.uuid4().hex
    await tts.start(ctx)
    await tts.push(ctx, text)
    await tts.finish(ctx)
    audio = bytearray()
    async for chunk in tts.chunks(ctx):
        if chunk.kind == "audio":
            audio += chunk.audio
        elif chunk.kind == "error":
            raise RuntimeError(chunk.message)
    return np.frombuffer(bytes(audio), "<i2").astype(np.float32) / 32768


async def main() -> int:
    s = get_settings()
    if not s.cartesia_api_key:
        print("Set CARTESIA_API_KEY in .env first.", file=sys.stderr)
        return 1
    voices = list(VOICES.items())
    connections = {}
    for name, voice_id in voices:
        connections[name] = await CartesiaTTS(s.cartesia_api_key, version=s.cartesia_version, model=s.tts_model,
                                              voice_id=voice_id, sample_rate=RATE).connect()
    lead = np.zeros(int(LEAD_SILENCE_S * RATE), np.float32)
    rng = np.random.default_rng(7)
    manifest = []

    items = [json.loads(line) for line in (ROOT / "evals" / "questions.jsonl").read_text().splitlines() if line]
    single = [i for i in items if len(i["turns"]) == 1]
    # 21 factual questions plus 9 tool tasks, so order numbers and model numbers are spoken too.
    picked = [i for i in single if i["category"] == "factual"][:21] + [i for i in single if i["category"] == "tool"][:9]
    clean: dict[str, np.ndarray] = {}
    for n, item in enumerate(picked):
        name, _ = voices[n % len(voices)]
        audio = np.concatenate([lead, await synthesise(connections[name], item["turns"][0])])
        clean[item["id"]] = audio
        print(f"  {item['id']} ({name})")
    pool = list(clean.values())
    for qid, audio in clean.items():
        item = next(i for i in picked if i["id"] == qid)
        voice = voices[picked.index(item) % len(voices)][0]
        for condition, samples in (("clean", audio), ("noisy", add_noise(audio, [p for p in pool if p is not audio], rng)),
                                   ("phone", telephone(audio))):
            path = OUT / condition / f"{qid}.wav"
            write_wav(path, samples)
            manifest.append({"kind": "question", "id": qid, "condition": condition, "voice": voice,
                             "text": item["turns"][0], "path": str(path.relative_to(ROOT))})

    scenarios = json.loads((ROOT / "evals" / "scenarios.json").read_text())
    for n, sc in enumerate(scenarios):
        name, _ = voices[n % len(voices)]
        for t, turn in enumerate(sc["turns"]):
            parts = [p.strip() for p in turn["text"].split("|")]
            pieces = [lead]
            for p_i, part in enumerate(parts):
                if p_i:
                    pieces.append(np.zeros(int(turn.get("pause_ms", 1500) / 1000 * RATE), np.float32))
                pieces.append(await synthesise(connections[name], part))
            path = OUT / "scenarios" / f"{sc['id']}-{t + 1}.wav"
            write_wav(path, np.concatenate(pieces))
            manifest.append({"kind": "scenario", "id": sc["id"], "turn": t + 1, "voice": name,
                             "text": " ".join(parts), "path": str(path.relative_to(ROOT))})
        print(f"  {sc['id']}")

    for tts in connections.values():
        await tts.close()
    MANIFEST.write_text("".join(json.dumps(m) + "\n" for m in manifest))
    print(f"Wrote {len(manifest)} clips and {MANIFEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
