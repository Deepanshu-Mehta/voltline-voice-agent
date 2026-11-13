"""Assemble the demo video: title cards, two live calls on the Talk page, and finding cards with
the real recorded audio under them. Synthetic customer voices only; the owner's recordings are
never used.

  uv run --project server python evals/demo/build_video.py            # needs make server + make web
  uv run --project server python evals/demo/build_video.py --no-live  # reuse recorded live segments

Output: evals/demo/out/voltline-demo.mp4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "listening"))

from build import cut_reply, load_right  # noqa: E402
from cards import card_video, concat  # noqa: E402

ROOT = HERE.parents[1]
OUT = HERE / "out"
RUN = ROOT / "evals" / "runs" / "v1"


def records() -> dict:
    out = {}
    for line in (RUN / "records.jsonl").read_text().splitlines():
        r = json.loads(line)
        out[(r["item"], r["system"])] = r
    return out


def write_wav(path: Path, x: np.ndarray, rate: int = 16000, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return path


def reply_audio(recs: dict, item: str, system: str, name: str) -> tuple[Path, float]:
    r = recs[(item, system)]
    start = (r["turns"][-1]["speech_end"] - (r.get("recording_t0_ms") or 0)) / 1000
    x = cut_reply(load_right(ROOT / r["recording"]), start)
    x = x / (np.abs(x).max() + 1e-9) * 0.8
    return write_wav(OUT / f"{name}.wav", x), len(x) / 16000


def call_audio(recs: dict, item: str, system: str, name: str, max_s: float) -> tuple[Path, float]:
    """Both sides of a recorded call, mixed: the caller and the agent."""
    r = recs[(item, system)]
    with wave.open(str(ROOT / r["recording"]), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    x = x.reshape(-1, 2).mean(axis=1)[: int(max_s * 16000)]
    x = x / (np.abs(x).max() + 1e-9) * 0.8
    return write_wav(OUT / f"{name}.wav", x), len(x) / 16000


def table(rows: list[tuple[str, str, str]], head: tuple[str, str, str]) -> str:
    body = "".join(f"<tr><td>{a}</td><td class=num>{b}</td><td class=num>{c}</td></tr>" for a, b, c in rows)
    return f"<table><tr><th>{head[0]}</th><th>{head[1]}</th><th>{head[2]}</th></tr>{body}</table>"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-live", action="store_true", help="Reuse the recorded live segments")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    summary = json.loads((RUN / "summary.json").read_text())["systems"]
    listening = json.loads((ROOT / "qa" / "listening" / "summary.json").read_text())
    recs = records()
    parts: list[Path] = []

    def card(name: str, html: str, seconds: float, audio: Path | None = None) -> None:
        parts.append(card_video(html, seconds, OUT / f"{name}.mp4", audio))

    card("01-title", "<span class=tag>EVALUATION PROJECT</span><h1>Two voice agents,<br>one customer line</h1>"
         "<p class=muted>A cascade and a speech-to-speech model answer the same questions for a fictional "
         "electronics store. Which one should a caller get?</p>", 6)
    card("02-systems", "<h2>The two systems</h2>" + table([
        ("Hears", "Deepgram Nova-3", "Gemini 3.1 Flash Live"),
        ("Thinks", "Gemini 3.5 Flash-Lite", "(the same model)"),
        ("Speaks", "Cartesia Sonic 3.6", "(the same model)"),
        ("Prompt, knowledge base, tools", "shared", "shared")], ("", "A: cascade", "B: speech-to-speech"))
         + "<p class=muted>Both are driven through the same web page and judged only from what a caller hears.</p>", 9)

    if not args.no_live:
        for mode, label in (("standard", "System A: the cascade"), ("s2s", "System B: speech-to-speech")):
            subprocess.run([sys.executable, str(HERE / "record_demo.py"), "--scenario", "S2-stock-then-price",
                            "--mode", mode, "--caption", label, "--out",
                            str((OUT / f"live-{mode}.mp4").relative_to(ROOT))], check=True, cwd=ROOT)
    card("03-live-intro", "<span class=tag>LIVE ON THE TALK PAGE</span><h2>The same two-question call, on each system</h2>"
         "<p class=muted>The caller is a synthetic voice played into the page by the test driver.</p>", 4)
    parts += [OUT / "live-standard.mp4", OUT / "live-s2s.mp4"]

    a, b = summary["A"], summary["B"]
    card("04-results", "<h2>Results: 282 paired calls</h2>" + table([
        ("Response time, median", f"{a['response_ms']['value'] / 1000:.2f} s", f"<span class=good>{b['response_ms']['value'] / 1000:.2f} s</span>"),
        ("Correct, judged on what was heard", f"<span class=good>{a['correct']['value']:.0%}</span>", f"{b['correct']['value']:.0%}"),
        ("Said a different number than it meant", f"<span class=good>{a['number_mismatch']['value']:.0%}</span>", f"<span class=bad>{b['number_mismatch']['value']:.0%}</span>"),
        ("Scripted conversations passed", f"<span class=good>{a['scenario_pass']['value']:.0%}</span>", f"{b['scenario_pass']['value']:.0%}"),
        ("Listener preference", f"<span class=good>{listening['preference']['A']}</span>", f"{listening['preference']['B']}"),
    ], ("", "A: cascade", "B: speech-to-speech")) + "<p class=muted>B is 344 ms faster. A is right more often, and listeners prefer it.</p>", 11)

    b_price, b_s = reply_audio(recs, "clean-T09", "B", "b-price")
    a_price, a_s = reply_audio(recs, "clean-T09", "A", "a-price")
    card("05-price-b", "<span class=tag>FINDING 1</span><h2>B says a price it does not mean</h2>"
         "<div class=quote>B's own transcript: \"...and is $119.99.\"<br>What three recognisers hear: \"...$199.99.\"</div>"
         "<p class=muted>The Keystone keyboard costs $119.99. Listen to B:</p>", b_s + 2.5, b_price)
    card("06-price-a", "<span class=tag>FINDING 1</span><h2>A on the same question</h2>"
         "<p class=muted>B did this in 3 of 3 conditions. A did it in none of 43 replies with numbers. "
         "Scoring B from its own text would have missed it.</p>", a_s + 2.5, a_price)

    pause, pause_s = call_audio(recs, "S7-long-pause", "B", "b-pause", 9.0)
    card("07-pause", "<span class=tag>FINDING 2</span><h2>Turn-taking: a pause is not the end</h2>"
         "<div class=quote>Caller: \"Do the SafeView cameras work with ... five gigahertz Wi-Fi?\"</div>"
         "<p class=muted>B answers into the 1.5 s pause, cutting the caller off: 3 of 3 pause tests. "
         "Both systems also stop talking when the caller says \"mm-hm\".</p>", pause_s + 1.5, pause)

    corr = listening["dnsmos_vs_people"]["pairs only"]
    card("08-dnsmos", "<span class=tag>FINDING 3</span><h2>The quality predictor disagrees with people</h2>" + table([
        ("DNSMOS (predicted)", f"{listening['dnsmos_by_system']['A']:.2f}", f"<span class=bad>{listening['dnsmos_by_system']['B']:.2f}</span>"),
        ("Listeners' naturalness (1 to 5)", f"<span class=good>{listening['naturalness_A']['mean']:.2f}</span>", f"{listening['naturalness_B']['mean']:.2f}"),
    ], ("", "A", "B")) + f"<p class=muted>Correlation with listeners over 24 clips: {corr['spearman']:.2f} "
         f"[{corr['ci'][0]:.2f}, {corr['ci'][1]:.2f}]. Picking by DNSMOS would pick the system people liked less.</p>", 10)

    card("09-real-speech", "<span class=tag>FINDING 4</span><h2>Synthetic speech hides recogniser weaknesses</h2>" + table([
        ("Gnani Prisma v3.0", "0.7%", "12.7%"), ("Deepgram Nova-3", "0.0%", "14.0%"),
        ("Gnani Prisma v2.5", "0.8%", "14.7%"), ("Whisper small.en", "0.4%", "<span class=bad>32.1%</span>"),
    ], ("Word error rate", "synthetic, clean", "real Indian English")) +
         "<p class=muted>Twenty real recordings changed the ranking that 105 synthetic clips could not separate.</p>", 10)

    shot = OUT / "listening-page.png"
    if shot.exists():
        import base64

        img = base64.b64encode(shot.read_bytes()).decode()
        card("10-listening", "<span class=tag>HOW IT WAS MEASURED</span><h2>Blind listening test and an issue log</h2>"
             f"<div style='display:flex;gap:32px;align-items:center'><img src='data:image/png;base64,{img}' "
             "style='width:420px;border:1px solid #cdd3da;border-radius:8px'>"
             "<p class=muted>Pairs loudness-matched and shuffled per listener, damaged anchors to screen listeners, "
             "a hidden repeat for consistency. 23 issues logged with evidence and reproduction rates.</p></div>", 8)
    card("11-lessons", "<span class=tag>LESSONS</span><h2>Check the instruments before the systems</h2>"
         "<p>The first judge was wrong in 10 of 13 hallucination flags. A key click made real recordings look "
         "4 ms fast. A clip builder read 9999 as a cardinal. A text-to-speech account ran dry mid-run and the "
         "error was silent. Each was found, reproduced and fixed, and each is in the issue log.</p>", 11)
    card("12-close", "<h1>Voltline voice agents</h1><p class=muted>Findings, issue log, datasheet and code: "
         "qa/findings.md in the repository.</p>", 5)

    final = concat(parts, OUT / "voltline-demo.mp4")
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(final)],
                         capture_output=True, text=True).stdout.strip()
    print(f"{final.relative_to(ROOT)}: {float(dur):.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
