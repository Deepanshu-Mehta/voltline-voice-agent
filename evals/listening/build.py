"""Build the listening test from a scored paired run (protocol: qa/listening-test-protocol.md).

Selection is mechanical, never by ear: clean-condition questions where both systems produced a
reply, grouped by type (answers with numbers, longer explanations, short facts), first four by
question id in each. Four anchors are made worse on purpose from other replies. One pair is
repeated with the order swapped.

Writes
  qa/listening/clips/<id>.wav   agent replies only, cut and loudness-matched, random ids
  qa/listening/test.json        what the page shows: pairs and single clips, no system names
  qa/listening/key.json         which clip is which system (kept out of the published page)

Usage: uv run --project server python evals/listening/build.py --run v1
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "qa" / "listening"
RATE = 16000
TARGET_DBFS = -23.0
PAD_S = 0.15


def load_right(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    return x.reshape(-1, 2)[:, 1]


def cut_reply(x: np.ndarray, start_s: float) -> np.ndarray:
    """The agent's reply after the user's last word: first sound to last sound, 150 ms either side."""
    x = x[int(start_s * RATE):]
    hop = 320
    n = len(x) // hop
    lv = 20 * np.log10(np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-9)
    voiced = np.nonzero(lv > max(lv.max() - 35, -55))[0]
    a = max(0, voiced[0] * hop - int(PAD_S * RATE))
    b = min(len(x), (voiced[-1] + 1) * hop + int(PAD_S * RATE))
    return x[a:b]


def loudness_match(x: np.ndarray) -> np.ndarray:
    hop = 320
    n = len(x) // hop
    frames = x[: n * hop].reshape(n, hop)
    lv = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    voiced = frames[lv > lv.max() - 35].reshape(-1)
    gain = 10 ** ((TARGET_DBFS - 20 * np.log10(np.sqrt((voiced ** 2).mean()) + 1e-9)) / 20)
    return np.clip(x * gain, -1, 1)


def anchor(x: np.ndarray, kind: str, rng: np.random.Generator) -> np.ndarray:
    if kind == "clipped":
        return np.clip(x * 6, -0.25, 0.25) / 0.25 * 0.5
    if kind == "dropout":  # 300 ms of silence a third of the way in
        i = len(x) // 3
        return np.concatenate([x[:i], np.zeros(int(0.3 * RATE), np.float32), x[i + int(0.3 * RATE):]])
    if kind == "telephone":
        spec = np.fft.rfft(x)
        f = np.fft.rfftfreq(len(x), 1 / RATE)
        spec[(f < 300) | (f > 3400)] = 0
        return np.fft.irfft(spec, len(x)).astype(np.float32)
    if kind == "noise":  # pink-ish noise at 5 dB SNR
        noise = np.cumsum(rng.standard_normal(len(x))).astype(np.float32)
        noise -= np.convolve(noise, np.ones(400) / 400, mode="same")
        noise *= np.sqrt((x ** 2).mean()) / (np.sqrt((noise ** 2).mean()) + 1e-9) / 10 ** (5 / 20)
        return x + noise
    raise ValueError(kind)


def write(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="v1")
    args = ap.parse_args()
    run = ROOT / "evals" / "runs" / args.run
    scored = list(csv.DictReader(open(run / "scored.csv")))
    records = {}
    for line in (run / "records.jsonl").read_text().splitlines():
        r = json.loads(line)
        records[(r["item"], r["system"])] = r
    questions = {json.loads(x)["id"]: json.loads(x) for x in (ROOT / "evals/questions.jsonl").read_text().splitlines() if x}

    ok = {}
    for row in scored:
        if row["kind"] == "question" and row["condition"] == "clean" and row.get("reply_s") and not row.get("error") \
                and row["rep"] == "1" and float(row["reply_s"]) > 1.0:
            ok.setdefault(row["item"], set()).add(row["system"])
    eligible = sorted(i for i, s in ok.items() if s == {"A", "B"})

    # Types by measured reply length (mean of A and B), fixed before anyone listens: the 4 longest
    # replies are "explanation", the 4 shortest of the rest "short", then the first 4 by id of the rest
    # whose answer has a number or a tool result are "numbers".
    length = {}
    for row in scored:
        if row["item"] in eligible and row["rep"] == "1":
            length.setdefault(row["item"], []).append(float(row["reply_s"]))
    mean_len = {i: sum(v) / len(v) for i, v in length.items()}

    def has_number(item: str) -> bool:
        q = questions[item.split("-", 1)[1]]
        return any(re.search(r"\d", p) for g in q.get("must_include", []) for p in g) or bool(q.get("expected_tool"))

    by_len = sorted(eligible, key=lambda i: mean_len[i])
    pairs = {"explanation": by_len[-4:]}
    rest = [i for i in by_len if i not in pairs["explanation"]]
    pairs["short"] = rest[:4]
    pairs["numbers"] = sorted(i for i in rest if i not in pairs["short"] and has_number(i))[:4]
    groups = pairs
    used = {i for v in pairs.values() for i in v}
    spare = [i for i in eligible if i not in used]
    if any(len(v) < 4 for v in pairs.values()) or len(spare) < 4:
        print(f"Not enough eligible replies: { {k: len(v) for k, v in groups.items()} }, spare {len(spare)}", file=sys.stderr)
        return 1

    rng = np.random.default_rng(20261113)
    if (OUT / "clips").exists():
        shutil.rmtree(OUT / "clips")
    (OUT / "clips").mkdir(parents=True)
    ids = iter(f"c{n:03d}" for n in rng.permutation(np.arange(100, 999))[:64])
    key, test_pairs, singles = {}, [], []

    def make(item: str, system: str) -> np.ndarray:
        r = records[(item, system)]
        start = (r["turns"][-1]["speech_end"] - (r.get("recording_t0_ms") or 0)) / 1000
        return loudness_match(cut_reply(load_right(ROOT / r["recording"]), start))

    for kind, items in pairs.items():
        for item in items:
            pair = []
            for system in ("A", "B"):
                cid = next(ids)
                write(OUT / "clips" / f"{cid}.wav", make(item, system))
                key[cid] = {"role": "pair", "system": system, "item": item, "type": kind}
                pair.append(cid)
            test_pairs.append({"pair": f"p{len(test_pairs) + 1:02d}", "clips": pair, "type": kind})
    first = test_pairs[0]
    test_pairs.append({"pair": "p13", "clips": list(reversed(first["clips"])), "type": first["type"], "repeat_of": first["pair"]})
    for n, (kind, item) in enumerate(zip(("clipped", "dropout", "telephone", "noise"), spare[:4])):
        system = "AB"[n % 2]
        cid = next(ids)
        write(OUT / "clips" / f"{cid}.wav", loudness_match(anchor(make(item, system), kind, rng)))
        key[cid] = {"role": "anchor", "system": system, "item": item, "anchor": kind}
        singles.append(cid)

    test = {"version": 1, "run": args.run, "pairs": test_pairs, "singles": singles,
            "scale": ["Clearly machine-like or broken", "Mostly machine-like", "Acceptable", "Mostly natural",
                      "Like a person"],
            "defects": ["Glitch or click", "Cut off", "Wrong or slurred word", "Odd pacing or pause",
                        "Robotic tone", "Background noise"]}
    (OUT / "test.json").write_text(json.dumps(test, indent=2))
    (OUT / "key.json").write_text(json.dumps(key, indent=2))
    print(f"{len(test_pairs)} pairs (one repeat) and {len(singles)} anchors -> qa/listening/")
    for p in test_pairs:
        print(f"  {p['pair']} {p['type']:11} " + " / ".join(f"{c} {key[c]['system']} {key[c]['item']}" for c in p["clips"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
