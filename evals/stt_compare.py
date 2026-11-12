"""Hearing A/B: the same question clips through several speech-to-text engines.

Engines
  deepgram      Deepgram Nova-3, pre-recorded API, with the same key terms the live cascade uses
  deepgram-nokt     the same without key terms (what does key-term prompting buy?)
  deepgram-nosmart  the same without smart_format (is "9999" -> "9900 and 99" formatting?)
  gnani-v2.5    Gnani Prisma v2.5 (en-IN), an India-built model
  gnani-v3.0    Gnani Prisma v3.0 (en-IN)
  gnani-v3.0-kt     the same with the key terms Deepgram gets, so the comparison is fair
  whisper       local faster-whisper small.en: the transcriber Phase 5 uses to score the agents,
                so its own accuracy here is the measuring instrument's error

Clips: the question clips of data/manifest.csv (core clean/noisy/phone, accent, recorded).
Scores per clip: word error rate against the reference text (normalised: case, punctuation,
spoken numbers, "safe view" = "SafeView"), and whether every key term (product names, order
numbers) was heard. Results are cached by engine and audio checksum, so re-runs are free.

Writes evals/results/stt-compare.csv (one row per clip and engine) and stt-compare-summary.csv,
and prints medians with 95% bootstrap intervals by engine and by condition.

Usage: uv run --project server python evals/stt_compare.py [--engines deepgram,gnani-v3.0]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_check import Transcriber, merge_compounds, norm_words, read, wer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from app.config import get_settings  # noqa: E402
from app.content import load_products  # noqa: E402

CACHE = ROOT / "data" / ".cache" / "stt.json"
OUT = ROOT / "evals" / "results"
ENGINES = ["deepgram", "deepgram-nokt", "deepgram-nosmart", "gnani-v2.5", "gnani-v3.0", "gnani-v3.0-kt", "whisper"]


def deepgram(client: httpx.Client, key: str, keyterms: list[str], audio: bytes, smart: bool = True) -> str:
    params = [("model", "nova-3"), ("language", "en-US"), ("smart_format", str(smart).lower()), ("punctuate", "true")]
    params += [("keyterm", k) for k in keyterms]
    r = client.post("https://api.deepgram.com/v1/listen", params=params, content=audio,
                    headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"}, timeout=60)
    r.raise_for_status()
    return r.json()["results"]["channels"][0]["alternatives"][0]["transcript"]


def gnani(client: httpx.Client, key: str, model: str | None, audio: bytes, keyterms: list[str] | None = None) -> str:
    data = {"language_code": "en-IN", "format": "transcribe"}
    if model:
        data["model"] = model
    if keyterms:
        data["keyterms"] = json.dumps(keyterms)
    for attempt in range(5):  # the API allows one request at a time per account
        r = client.post("https://api.vachana.ai/stt/v3", headers={"X-API-Key-ID": key}, data=data,
                        files={"audio_file": ("clip.wav", audio, "audio/wav")}, timeout=60)
        if r.status_code != 429:
            break
        time.sleep(1 + attempt)
    r.raise_for_status()
    body = r.json()
    if not body.get("success", True):
        raise RuntimeError(body.get("error"))
    return body.get("transcript", "")


def bootstrap(values: list[float], fn=np.mean, n: int = 2000, seed: int = 1) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    v = np.asarray(values, float)
    stats = [fn(rng.choice(v, len(v))) for _ in range(n)]
    return float(fn(v)), float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default=",".join(ENGINES))
    args = ap.parse_args()
    engines = args.engines.split(",")
    s = get_settings()
    import os

    gnani_key = os.environ.get("GNANI_API_KEY") or _env_value("GNANI_API_KEY")
    keyterms = ["Voltline", *(p.name for p in load_products(s.content_dir))]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    whisper = Transcriber(True)

    manifest = list(csv.DictReader(open(ROOT / "data" / "manifest.csv")))
    clips = [m for m in manifest if m["set"] in ("core", "accent", "recorded")]
    rows = []
    with httpx.Client() as client:
        for n, m in enumerate(clips, 1):
            path = ROOT / m["path"]
            if not path.exists():
                continue
            audio = path.read_bytes()
            fmt, x = read(path)
            for engine in engines:
                key = f"{engine}:{fmt['sha']}"
                if key not in cache:
                    try:
                        if engine == "deepgram":
                            cache[key] = deepgram(client, s.deepgram_api_key, keyterms, audio)
                        elif engine == "deepgram-nokt":
                            cache[key] = deepgram(client, s.deepgram_api_key, [], audio)
                        elif engine == "deepgram-nosmart":
                            cache[key] = deepgram(client, s.deepgram_api_key, keyterms, audio, smart=False)
                        elif engine.startswith("gnani"):
                            cache[key] = gnani(client, gnani_key, None if engine == "gnani-v2.5" else "prisma-v3.0", audio,
                                               keyterms if engine.endswith("-kt") else None)
                        elif engine == "whisper":
                            cache[key] = whisper(fmt["sha"], x) or ""
                    except Exception as exc:
                        print(f"  {engine} failed on {m['clip_id']}: {exc}")
                        continue
                hyp = cache[key]
                ref = m["reference_text"]
                heard = "".join(merge_compounds(norm_words(ref), norm_words(hyp)))
                terms = [t for t in m["key_terms"].split("; ") if t]
                missed = [t for t in terms if "".join(norm_words(t)) not in heard]
                rows.append({"clip_id": m["clip_id"], "question_id": m["question_id"], "set": m["set"],
                             "condition": m["condition"], "accent": m["accent"], "gender": m["gender"],
                             "speaker": m["speaker"], "engine": engine, "reference": ref, "transcript": hyp,
                             "wer": wer(ref, hyp), "key_terms": len(terms), "key_terms_missed": "; ".join(missed),
                             "all_key_terms_heard": int(not missed)})
            if n % 15 == 0:
                CACHE.write_text(json.dumps(cache, indent=0, sort_keys=True))
                print(f"  {n}/{len(clips)} clips")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=0, sort_keys=True))
    whisper.save()

    OUT.mkdir(exist_ok=True)
    with open(OUT / "stt-compare.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    groups = [("all", lambda r: True)] + [(c, (lambda c: lambda r: r["condition"] == c)(c))
                                          for c in ("clean", "noisy", "phone", "accent", "recorded")]
    summary = []
    print(f"\n{'engine':11} {'group':9} {'n':>3}  {'mean WER [95% CI]':24} {'key terms heard [95% CI]':26}")
    for engine in engines:
        for name, keep in groups:
            sel = [r for r in rows if r["engine"] == engine and keep(r)]
            if not sel:
                continue
            w_mean, w_lo, w_hi = bootstrap([r["wer"] for r in sel])
            sel_terms = [r for r in sel if r["key_terms"]]
            k_mean, k_lo, k_hi = bootstrap([r["all_key_terms_heard"] for r in sel_terms]) if sel_terms else (0, 0, 0)
            summary.append({"engine": engine, "group": name, "clips": len(sel), "wer_mean": round(w_mean, 3),
                            "wer_ci_low": round(w_lo, 3), "wer_ci_high": round(w_hi, 3),
                            "key_terms_heard": round(k_mean, 3), "key_ci_low": round(k_lo, 3),
                            "key_ci_high": round(k_hi, 3), "key_term_clips": len(sel_terms)})
            print(f"{engine:11} {name:9} {len(sel):3}  {w_mean:5.3f} [{w_lo:.3f}, {w_hi:.3f}]       "
                  f"{k_mean:5.1%} [{k_lo:.0%}, {k_hi:.0%}] of {len(sel_terms)}")
    with open(OUT / "stt-compare-summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    print("\nWrote evals/results/stt-compare.csv and stt-compare-summary.csv")
    return 0


def _env_value(name: str) -> str | None:
    env = ROOT / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return None


if __name__ == "__main__":
    raise SystemExit(main())
