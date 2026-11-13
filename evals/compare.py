"""Paired run: the dataset through system A (cascade) and system B (speech-to-speech).

Every clip is played through the real web page by the browser driver, once per system, A then
B back to back, in a fresh call each time, so provider drift hits both systems alike and no
conversation history leaks between samples. Each call is recorded as a stereo WAV (user left,
agent right). Scoring happens afterwards, from the recordings (evals/score_run.py).

The run resumes: results are appended to records.jsonl as they finish, and a re-run skips what
is already there. Clips from the owner's recorded set are written under data/private/runs/.

  uv run --project server python evals/compare.py --run pilot --sets core --limit 5
  uv run --project server python evals/compare.py --run v1 --sets core,accent,turn-taking,scenario
  uv run --project server python evals/compare.py --run v1 --sets core --repeat 3 --only clean-F01,...

Needs the API (make server) and the website (make web).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "driver"))

from browser_driver import ROOT, open_driver_page, run_scenario  # noqa: E402

SYSTEMS = {"A": "standard", "B": "s2s"}


def commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def work_items(sets: list[str], only: set[str] | None, limit: int | None) -> list[dict]:
    """Pseudo-scenarios: one per question clip, plus the scripted scenarios."""
    manifest = list(csv.DictReader(open(ROOT / "data" / "manifest.csv")))
    private = ROOT / "data" / "private" / "manifest-private.csv"
    if private.exists():  # the recorded set's real paths live only in the private copy
        real = {r["clip_id"]: r for r in csv.DictReader(open(private))}
        manifest = [real.get(r["clip_id"], r) for r in manifest]
    items = []
    for m in manifest:
        if m["set"] in sets and m["set"] in ("core", "accent", "recorded"):
            items.append({"id": m["clip_id"], "kind": "question", "set": m["set"], "question_id": m["question_id"],
                          "private": m["private"] == "yes", "turns": [{"clip": m["path"]}], "expect": {}})
    scenarios = json.loads((ROOT / "evals" / "scenarios.json").read_text())
    for sc in scenarios:
        group = "turn-taking" if sc["id"].startswith("TT") else "scenario"
        if group in sets:
            items.append({**sc, "kind": "scenario", "set": group, "question_id": "", "private": False})
    if only:
        items = [i for i in items if i["id"] in only]
    return items[:limit] if limit else items


async def main() -> int:
    from playwright.async_api import async_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="Run name: the folder under evals/runs/")
    ap.add_argument("--sets", default="core,accent,turn-taking,scenario")
    ap.add_argument("--systems", default="A,B")
    ap.add_argument("--only", help="Comma-separated clip or scenario ids")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--repeat", type=int, default=1, help="Runs per clip and system (consistency)")
    ap.add_argument("--site", default="http://localhost:3000")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    items = work_items(args.sets.split(","), set(args.only.split(",")) if args.only else None, args.limit)
    run_dir = ROOT / "evals" / "runs" / args.run
    private_dir = ROOT / "data" / "private" / "runs" / args.run
    records_path = run_dir / "records.jsonl"
    private_records = private_dir / "records.jsonl"  # calls on the owner's voice: what was heard stays local
    run_dir.mkdir(parents=True, exist_ok=True)
    done = set()
    for path in (records_path, private_records):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            r = json.loads(line)
            if any("driver error" in p for p in r.get("problems") or []):
                continue  # retried on the next run; the scorer keeps the latest attempt
            done.add((r["item"], r["system"], r["rep"]))
    meta = {"run": args.run, "commit": commit(), "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sets": args.sets, "systems": args.systems, "repeat": args.repeat}
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    todo = [(i, s, r) for i in items for r in range(1, args.repeat + 1) for s in args.systems.split(",")
            if (i["id"], s, r) not in done]
    print(f"{len(items)} items x {args.systems} x {args.repeat}: {len(todo)} calls to make ({len(done)} done before)")
    async with async_playwright() as p:
        browser, page = await open_driver_page(p, args.headed)
        for n, (item, system, rep) in enumerate(todo, 1):
            out = (private_dir if item["private"] else run_dir) / "wav" / system
            out.mkdir(parents=True, exist_ok=True)
            scenario = {**item, "id": item["id"] if rep == 1 else f"{item['id']}-r{rep}"}
            t = time.perf_counter()
            try:
                result = await run_scenario(page, args.site, args.api, scenario, out, SYSTEMS[system])
            except Exception as exc:
                result = {"id": scenario["id"], "passed": False, "problems": [f"driver error: {exc}"], "turns": [],
                          "rows": []}
                try:  # a stuck page must not poison the next call
                    await page.goto("about:blank")
                except Exception:
                    browser, page = await open_driver_page(p, args.headed)
            record = {"item": item["id"], "system": system, "rep": rep, "set": item["set"], "kind": item["kind"],
                      "question_id": item["question_id"], "private": item["private"], "seconds": round(time.perf_counter() - t, 1),
                      **{k: result.get(k) for k in ("passed", "problems", "session_id", "recording_t0_ms", "turns",
                                                    "rows", "recording")}}
            target = private_records if item["private"] else records_path
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a") as f:
                f.write(json.dumps(record) + "\n")
            lat = [x.get("measured_response_ms") for x in result.get("turns", [])]
            flag = "" if not result.get("problems") or item["kind"] == "question" else f"  {result['problems']}"
            print(f"[{n}/{len(todo)}] {system} {scenario['id']:26} response {lat}{flag}", flush=True)
        await browser.close()
    print(f"Records: {records_path.relative_to(ROOT)}. Score with: evals/score_run.py --run {args.run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
