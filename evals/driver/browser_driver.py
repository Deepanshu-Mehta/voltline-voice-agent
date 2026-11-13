"""Browser test driver: runs the scripted scenarios (evals/scenarios.json) through the real web
page, with a stubbed microphone, and measures latency from the audio itself.

For each scenario it opens /talk, starts a voice call, plays each recorded user turn once the
agent has finished speaking (or, for an interruption, partway through its reply), and records
both sides. The headline latency is measured from audio: end of the user's speech in the clip
to the first sound of the agent's reply, on the page's own clock. It also checks each scenario's
expectations (tools called, arguments, final reply, interruptions) against the server's turns.

  make eval-scenarios                          # all scenarios, headless
  uv run --project server python evals/driver/browser_driver.py --scenarios S1,S5 --headed

Needs the API (make server), the website (make web), DEEPGRAM/CARTESIA keys and the scenario
clips (make audio-clips). Output: evals/results/driver-<timestamp>/ with a stereo WAV per
scenario (left: user, right: agent) and report.json.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import json
import re
import sys
import wave
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CLIPS = ROOT / "evals" / "audio" / "clips"
PRIVATE_CLIPS = ROOT / "data" / "private" / "recorded"  # the owner's voice: played locally, never published
CLIP_ROOTS = (CLIPS, PRIVATE_CLIPS)


def turn_clip(scenario: dict, n: int, turn: dict) -> Path:
    """A turn's audio: an explicit repo-relative "clip" path, or the scenario's own clip."""
    return ROOT / turn["clip"] if turn.get("clip") else CLIPS / "scenarios" / f"{scenario['id']}-{n}.wav"
STUB = (Path(__file__).parent / "mic_stub.js").read_text()
RATE = 16000


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def speech_end_offset_ms(samples: np.ndarray, threshold: float = 0.02) -> float:
    """Where speech ends inside a clip (clips can carry a little trailing silence)."""
    loud = np.nonzero(np.abs(samples) > threshold)[0]
    return (loud[-1] + 1) / RATE * 1000 if len(loud) else len(samples) / RATE * 1000


async def wait_for(predicate, timeout_s: float, interval_s: float = 0.1):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    while loop.time() < end:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(interval_s)
    return None


_DIGIT_WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine".split())}


def normalise(text: str) -> str:
    """Spoken answers are right in more than one spelling: "24-month" / "24 month",
    "two point four" / "2.4". Compare them in one form."""
    t = re.sub(r"[-\u2010\u2011]", " ", text.lower())
    t = re.sub(r"\b(\w+) point (\w+)\b",
               lambda m: f"{_DIGIT_WORDS.get(m[1], m[1])}.{_DIGIT_WORDS.get(m[2], m[2])}", t)
    return re.sub(r"\s+", " ", t)


def check_expectations(expect: dict, rows: list[dict]) -> list[str]:
    """Problems found; an empty list means the scenario passed."""
    problems = []
    calls = [c for r in rows for c in json.loads(r["tool_calls"] or "[]")]
    for tool in expect.get("tools", []):
        if not any(c["name"] == tool for c in calls):
            problems.append(f"{tool} was not called")
    for key, want in expect.get("args", {}).items():
        if not any(re.sub(r"\D", "", str(c.get("args", {}).get(key, ""))) == re.sub(r"\D", "", want) for c in calls):
            problems.append(f"no tool call with {key}={want}")
    final = rows[-1]["reply"] if rows else ""
    if expect.get("final_includes_any") and not any(normalise(p) in normalise(final) for p in expect["final_includes_any"]):
        problems.append(f"final reply lacks any of {expect['final_includes_any']}: {final[:80]!r}")
    if "interrupted_turns" in expect:
        n = sum(1 for r in rows if json.loads(r["extra"] or "{}").get("voice", {}).get("interrupted"))
        if n < expect["interrupted_turns"]:
            problems.append(f"expected {expect['interrupted_turns']} interrupted turn(s), saw {n}")
    if "max_interrupted_turns" in expect:
        n = sum(1 for r in rows if json.loads(r["extra"] or "{}").get("voice", {}).get("interrupted"))
        if n > expect["max_interrupted_turns"]:
            problems.append(f"the agent was stopped {n} time(s); expected at most {expect['max_interrupted_turns']}")
    if "max_user_turns" in expect and len(rows) > expect["max_user_turns"]:
        problems.append(f"the user was cut off: {len(rows)} turns instead of {expect['max_user_turns']}")
    return problems


def write_stereo(path: Path, clips: list[tuple[float, np.ndarray]], agent: dict) -> float:
    """Left channel: user clips at their play times. Right: everything the agent played."""
    agent_pcm = np.frombuffer(base64.b64decode(agent["pcm"]), "<i2").astype(np.float32) / 32768
    if agent["rate"] and agent["rate"] != RATE and len(agent_pcm):
        n = int(len(agent_pcm) * RATE / agent["rate"])
        agent_pcm = np.interp(np.linspace(0, len(agent_pcm) - 1, n), np.arange(len(agent_pcm)), agent_pcm)
    starts = [t for t, _ in clips] + ([agent["t0"]] if agent["t0"] is not None else [])
    t0 = min(starts) if starts else 0.0
    end = max([t + len(c) / RATE * 1000 for t, c in clips] +
              [(agent["t0"] or t0) + len(agent_pcm) / RATE * 1000]) - t0
    left = np.zeros(int(end / 1000 * RATE) + 1, np.float32)
    right = np.zeros_like(left)
    for t, c in clips:
        i = int((t - t0) / 1000 * RATE)
        left[i:i + len(c)] += c[: len(left) - i]
    if agent["t0"] is not None:
        i = int((agent["t0"] - t0) / 1000 * RATE)
        right[i:i + len(agent_pcm)] = agent_pcm[: len(right) - i]
    stereo = np.stack([left, right], axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(stereo, -1, 1) * 32767).astype("<i2").tobytes())
    return t0  # page-clock ms of the recording's first sample


MODE_LABEL = {"standard": "Cascade", "s2s": "Speech-to-speech"}


async def run_scenario(page, site: str, api: str, scenario: dict, out_dir: Path, mode: str = "standard") -> dict:
    await page.goto(f"{site}/talk")
    # Pick the pipeline with the same switch a person uses.
    await page.get_by_role("radio", name=MODE_LABEL[mode]).click()
    await page.get_by_role("button", name="Start talking").click()
    if not await wait_for(lambda: page.evaluate("__driver.state() === 'Listening'"), 20):
        raise RuntimeError(f"voice call did not start (state: {await page.evaluate('__driver.state()')})")
    await wait_for(lambda: page.evaluate("__driver.installTap()"), 5)

    played: list[tuple[float, np.ndarray]] = []
    turns = []
    for n, turn in enumerate(scenario["turns"], start=1):
        clip_path = turn_clip(scenario, n, turn)
        samples = read_wav(clip_path)
        if turn.get("interrupt_after_ms") and turns:
            # Start talking while the agent is mid-reply.
            prev_end = turns[-1]["speech_end"]
            onset = await wait_for(lambda: page.evaluate(
                f"(__driver.events.find(e => e.kind === 'onset' && e.t > {prev_end}) || {{}}).t"), 30)
            if onset:
                await asyncio.sleep(turn["interrupt_after_ms"] / 1000)
        else:
            await wait_for(lambda: page.evaluate("__driver.state() === 'Listening'"), 45)
            await asyncio.sleep(0.4)
        timing = await page.evaluate(f"__driver.play('/__clips/{clip_path.relative_to(ROOT).as_posix()}')")
        speech_end = timing["start"] + speech_end_offset_ms(samples)
        played.append((timing["start"], samples))
        if turn.get("backchannel") or turn.get("no_reply"):
            # Not a question: the agent should carry on (backchannel) or stay quiet (noise).
            await asyncio.sleep(len(samples) / RATE + 2.5)
            reply_onset = None
        else:
            reply_onset = await wait_for(lambda: page.evaluate(
                f"(__driver.events.find(e => e.kind === 'onset' && e.t > {speech_end}) || {{}}).t"), 30)
        turns.append({"turn": n, "text": turn.get("text") or f"[{turn.get('noise')}]", "speech_end": speech_end,
                      "measured_response_ms": round(reply_onset - speech_end) if reply_onset else None})

    await wait_for(lambda: page.evaluate("__driver.state() === 'Listening'"), 45)
    await asyncio.sleep(1.0)
    session_id = await page.evaluate("__driver.sessionId()")
    agent_audio = await page.evaluate("__driver.exportAgent()")
    await page.get_by_role("button", name="End call").click()

    rows = []
    if session_id:
        async with httpx.AsyncClient() as client:
            rows = (await client.get(f"{api}/turns/recent", params={"session_id": session_id, "limit": 50})).json()
        rows.reverse()
    wav = out_dir / f"{scenario['id']}.wav"
    t0 = write_stereo(wav, played, agent_audio)
    problems = check_expectations(scenario.get("expect", {}), rows)
    server = [{"user": r["user_text"], "reply": r["reply"],
               "server_response_ms": json.loads(r["extra"] or "{}").get("voice", {}).get("server_response_ms"),
               "client_response_ms": json.loads(r["extra"] or "{}").get("client", {}).get("client_response_ms")}
              for r in rows]
    return {"id": scenario["id"], "passed": not problems, "problems": problems, "session_id": session_id,
            "recording_t0_ms": t0, "rows": rows,
            "turns": turns, "server_turns": server, "recording": str(wav.relative_to(ROOT))}


async def open_driver_page(p, headed: bool = False):
    """A Chromium page with the stubbed microphone and the test clips served at /__clips/."""
    browser = await p.chromium.launch(headless=not headed, args=[
        "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
    context = await browser.new_context(permissions=["microphone"])
    await context.add_init_script(STUB)

    async def serve_clip(route):
        name = route.request.url.split("/__clips/", 1)[1]
        path = (ROOT / name).resolve()
        if not any(r.resolve() in path.parents for r in CLIP_ROOTS) or not path.exists():
            await route.fulfill(status=404)
            return
        await route.fulfill(body=path.read_bytes(), content_type="audio/wav")

    await context.route("**/__clips/**", serve_clip)
    return browser, await context.new_page()


async def main() -> int:
    from playwright.async_api import async_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="http://localhost:3000")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--scenarios", help="Comma-separated scenario ids or prefixes, e.g. S1,S5")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--mode", choices=["standard", "s2s"], default="standard",
                    help="standard = cascade (system A), s2s = speech-to-speech (system B)")
    ap.add_argument("--gap", type=float, default=0.0, help="Seconds between scenarios (rate-limited LLM tiers)")
    args = ap.parse_args()

    scenarios = json.loads((ROOT / "evals" / "scenarios.json").read_text())
    if args.scenarios:
        wanted = args.scenarios.split(",")
        scenarios = [s for s in scenarios if any(s["id"].startswith(w) for w in wanted)]
    missing = [s["id"] for s in scenarios if not (CLIPS / "scenarios" / f"{s['id']}-1.wav").exists()]
    if missing:
        print(f"Missing clips for {missing}: run `make audio-clips` first.", file=sys.stderr)
        return 1

    out_dir = ROOT / "evals" / "results" / f"driver-{args.mode}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True)
    results = []
    async with async_playwright() as p:
        browser, page = await open_driver_page(p, args.headed)
        for n, scenario in enumerate(scenarios):
            if n and args.gap:
                await asyncio.sleep(args.gap)
            try:
                result = await run_scenario(page, args.site, args.api, scenario, out_dir, args.mode)
            except Exception as exc:  # keep going; report the failure
                result = {"id": scenario["id"], "passed": False, "problems": [f"driver error: {exc}"], "turns": []}
            results.append(result)
            lat = [t["measured_response_ms"] for t in result["turns"]]
            print(f"{'PASS' if result['passed'] else 'FAIL'} {scenario['id']:24} measured response ms {lat}"
                  + ("" if result["passed"] else f"  {result['problems']}"))
        await browser.close()

    measured = sorted(t["measured_response_ms"] for r in results for t in r["turns"] if t["measured_response_ms"])
    summary = {"mode": args.mode, "scenarios": len(results), "passed": sum(r["passed"] for r in results),
               "measured_response_ms_median": measured[len(measured) // 2] if measured else None,
               "measured_response_ms_p95": measured[min(len(measured) - 1, int(len(measured) * 0.95))] if measured else None}
    (out_dir / "report.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"Recordings and report: {out_dir.relative_to(ROOT)}")
    return 0 if summary["passed"] == summary["scenarios"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
