"""Audio eval: streams each clip in evals/audio/manifest.jsonl into the /voice WebSocket in real
time, exactly as a microphone would, and scores speech recognition and latency.

  make eval-audio                         # all 90 question clips against http://localhost:8000
  ... --condition clean --limit 10        # a subset
  ... --url http://localhost:8000

Scores per condition (clean, noisy, phone)
  wer                  word error rate of the final transcript against the script
  entity_accuracy      prices, model and order numbers found in the transcript
  end_of_turn_ms       end of speech -> turn committed (endpointing)
  llm_first_token_ms   LLM time to first token
  tts_first_byte_ms    first text sent to TTS -> first audio back
  server_response_ms   end of speech -> first reply audio leaves the server
Browser playback latency is measured by the browser test driver (evals/driver).
Results: evals/results/audio-<timestamp>.json and audio-latest.json.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import re
import statistics
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals" / "audio" / "manifest.jsonl"
RESULTS = ROOT / "evals" / "results"
FRAME_MS = 20
TRAILING_SILENCE_S = 2.5
TURN_TIMEOUT_S = 25


def normalise(text: str) -> list[str]:
    text = digits_for_words(text).replace("-", " ").replace("’", "'")
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # 20,000 -> 20000
    return re.findall(r"[a-z0-9.$']+", re.sub(r"[.,!?;:](\s|$)", " ", text))


def wer(reference: str, hypothesis: str) -> float:
    r, h = normalise(reference), normalise(hypothesis)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(len(r), 1)


NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}


def digits_for_words(text: str) -> str:
    """'five gigahertz' -> '5 gigahertz', so spoken and written numbers compare equal."""
    return re.sub(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", lambda m: NUMBER_WORDS[m.group(1)], text.lower())


def entities(text: str) -> list[str]:
    """Numbers and model numbers, compacted: '$149.99' -> '14999', 'VL-K800' -> 'vlk800'."""
    found = re.findall(r"\b(?:vl[-\s]?)?[a-z]?\d[\d,.]*\b", digits_for_words(text))
    return [re.sub(r"[^a-z0-9]", "", e) for e in found if re.sub(r"[^0-9]", "", e)]


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, path
        return w.readframes(w.getnframes())


async def run_clip(url: str, clip: dict) -> dict:
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    pcm = read_pcm(ROOT / clip["path"])
    frame = 16000 * 2 * FRAME_MS // 1000
    silence = b"\x00" * frame
    out = {"transcript": "", "reply": "", "metrics": None, "audio_bytes": 0, "error": None}
    async with connect(f"{url.replace('http', 'ws', 1)}/voice", max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "sample_rate": 16000, "source": "file", "mode": "standard"}))
        done = asyncio.Event()

        async def reader():
            async for msg in ws:
                if isinstance(msg, bytes):
                    out["audio_bytes"] += len(msg) - 4
                    continue
                m = json.loads(msg)
                if m["type"] == "transcript" and m.get("final"):
                    out["transcript"] = m["text"]
                elif m["type"] == "agent_text":
                    out["reply"] += m["text"]
                elif m["type"] == "metrics":
                    out["metrics"] = m
                    done.set()
                elif m["type"] == "error":
                    out["error"] = m["message"]
                    if "unavailable" in m["message"] or "not configured" in m["message"]:
                        done.set()

        task = asyncio.create_task(reader())
        start = time.perf_counter()
        sent = 0
        # Stream the clip, then silence, at real-time pace until the reply's metrics arrive.
        try:
            while not done.is_set() and time.perf_counter() - start < TURN_TIMEOUT_S:
                chunk = pcm[sent:sent + frame] if sent < len(pcm) else silence
                sent += frame
                await ws.send(chunk)
                target = start + sent / (16000 * 2)
                await asyncio.sleep(max(0, target - time.perf_counter()))
            if not done.is_set():
                out["error"] = out["error"] or "timed out waiting for a reply"
            await ws.send(json.dumps({"type": "stop"}))
        except ConnectionClosed:
            await asyncio.sleep(0.1)  # let the reader record the server's error message
            out["error"] = out["error"] or "the server closed the connection"
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionClosed):
            await task
    return out


def med(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 1) if values else None


def p95(values):
    values = sorted(v for v in values if v is not None)
    return round(values[min(len(values) - 1, int(len(values) * 0.95))], 1) if values else None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--condition", choices=["clean", "noisy", "phone"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--gap", type=float, default=0.0, help="Seconds between clips (rate-limited LLM tiers)")
    args = ap.parse_args()
    if not MANIFEST.exists():
        print("No clips yet: run `make audio-clips` (needs CARTESIA_API_KEY).", file=sys.stderr)
        return 1
    clips = [json.loads(line) for line in MANIFEST.read_text().splitlines() if line]
    clips = [c for c in clips if c["kind"] == "question" and (not args.condition or c["condition"] == args.condition)]
    if args.limit:
        clips = clips[: args.limit]

    results, stopped = [], False
    for n, clip in enumerate(clips):  # one at a time: concurrent calls would distort latency
        if n and args.gap:
            await asyncio.sleep(args.gap)
        r = await run_clip(args.url, clip)
        ref_entities = entities(clip["text"])
        hyp = re.sub(r"[^a-z0-9]", "", digits_for_words(r["transcript"]))
        row = {**clip, **r, "wer": round(wer(clip["text"], r["transcript"]), 3),
               "entities_found": sum(e in hyp for e in ref_entities), "entities_total": len(ref_entities)}
        results.append(row)
        m = r["metrics"] or {}
        print(f"{clip['condition']:5} {clip['id']:4} wer={row['wer']:.2f} resp={m.get('server_response_ms')} "
              f"{r['transcript'][:60]!r}{'  ERROR ' + r['error'] if r['error'] else ''}")
        if r["error"] and "Voice is unavailable" in r["error"]:
            print(f"Stopping: {r['error']}", file=sys.stderr)
            results.pop()  # a provider outage says nothing about this clip
            stopped = True
            break

    summary = {}
    for condition in ("clean", "noisy", "phone"):
        rows = [r for r in results if r["condition"] == condition]
        if not rows:
            continue
        ms = lambda k: [(r["metrics"] or {}).get(k) for r in rows]  # noqa: E731
        ent_total = sum(r["entities_total"] for r in rows)
        summary[condition] = {
            "clips": len(rows), "errors": sum(1 for r in rows if r["error"]),
            "wer": round(sum(r["wer"] for r in rows) / len(rows), 3),
            "entity_accuracy": round(sum(r["entities_found"] for r in rows) / ent_total, 3) if ent_total else None,
            **{f"{k}_median": med(ms(k)) for k in ("end_of_turn_ms", "llm_first_token_ms", "tts_first_byte_ms",
                                                  "server_response_ms")},
            "server_response_ms_p95": p95(ms("server_response_ms")),
        }
    report = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), "url": args.url,
              "summary": summary, "results": results}
    RESULTS.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    paths = [RESULTS / f"audio-{stamp}.json"]
    if results and not stopped:  # a partial run must not replace the last complete one
        paths.append(RESULTS / "audio-latest.json")
    for path in paths:
        path.write_text(json.dumps(report, indent=2))
    print(json.dumps(summary, indent=2))
    return 1 if stopped or not results else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
