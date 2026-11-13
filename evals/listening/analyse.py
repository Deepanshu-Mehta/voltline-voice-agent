"""Analyse listening-test responses exactly as qa/listening-test-protocol.md fixes in advance.

Reads qa/listening/responses/*.json (one per listener: saved from the page's database, its
download, or pasted text), qa/listening/key.json (which clip is which system; local only) and
qa/listening/test.json. L0 is the owner: reported, never averaged.

  uv run --project server python evals/listening/analyse.py

Writes qa/listening/results.csv (one row per rating) and qa/listening/summary.json.
"""

from __future__ import annotations

import csv
import json
import math
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "qa" / "listening"


def dnsmos_scores(clip_ids: list[str]) -> dict[str, float]:
    import torch
    from torchmetrics.functional.audio.dnsmos import deep_noise_suppression_mean_opinion_score as dnsmos

    out = {}
    for cid in clip_ids:
        with wave.open(str(DIR / "clips" / f"{cid}.wav")) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
        out[cid] = round(float(dnsmos(torch.from_numpy(x), fs=16000, personalized=False)[0]), 3)
    return out


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    # average ranks for ties
    for r, v in ((ra, np.asarray(a)), (rb, np.asarray(b))):
        for val in set(v):
            idx = np.where(v == val)[0]
            r[idx] = r[idx].mean()
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def boot_ci(fn, data: list, n: int = 2000, seed: int = 5) -> tuple[float, float] | None:
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        sample = [data[i] for i in rng.integers(0, len(data), len(data))]
        v = fn(sample)
        if v is not None and not math.isnan(v):
            vals.append(v)
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))) if vals else None


def sign_test(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def kendall_w(matrix: list[list[float]]) -> float | None:
    """Kendall's W (no tie correction): rows are listeners, columns clips."""
    m = len(matrix)
    if m < 2:
        return None
    n = len(matrix[0])
    ranks = np.array([np.argsort(np.argsort(r)) + 1 for r in matrix], float)
    s = ((ranks.sum(axis=0) - m * (n + 1) / 2) ** 2).sum()
    return float(12 * s / (m ** 2 * (n ** 3 - n)))


def main() -> int:
    key = json.loads((DIR / "key.json").read_text())
    test = json.loads((DIR / "test.json").read_text())
    responses = [json.loads(p.read_text()) for p in sorted((DIR / "responses").glob("*.json"))]
    repeat = next((p for p in test["pairs"] if p.get("repeat_of")), None)
    rows, listeners = [], []
    for r in responses:
        lid = r["listener"]
        anchors = [a for a in r["answers"] if a["kind"] == "single"]
        high = [a for a in anchors if max(a["ratings"].values()) >= 4]
        excluded = lid == "L0" or len(high) >= 2
        reason = "owner, not blind" if lid == "L0" else f"rated {len(high)} anchors 4+" if len(high) >= 2 else ""
        by_screen = {a["screen"]: a for a in r["answers"]}
        consistent = None
        if repeat and repeat["pair"] in by_screen and repeat["repeat_of"] in by_screen:
            a1, a2 = by_screen[repeat["repeat_of"]], by_screen[repeat["pair"]]
            same_pref = a1["preference"] == a2["preference"]
            diffs = [abs(a1["ratings"][c] - a2["ratings"][c]) for c in a1["ratings"] if c in a2["ratings"]]
            consistent = {"same_preference": same_pref, "mean_rating_change": round(float(np.mean(diffs)), 2) if diffs else None}
        listeners.append({"listener": lid, "excluded": excluded, "reason": reason,
                          "anchor_ratings": {a["screen"]: list(a["ratings"].values())[0] for a in anchors},
                          "repeat": consistent})
        for a in r["answers"]:
            if a["screen"] == (repeat or {}).get("pair"):
                continue  # the hidden repeat is for consistency only
            for cid, rating in a["ratings"].items():
                k = key[cid]
                rows.append({"listener": lid, "excluded": int(excluded), "screen": a["screen"], "clip": cid,
                             "role": k["role"], "system": k["system"], "item": k["item"],
                             "type": k.get("type", k.get("anchor", "")), "rating": rating,
                             "defects": ";".join(a["defects"].get(cid, [])),
                             "preferred": "" if a["kind"] != "pair" else int(a["preference"] == cid),
                             "no_preference": "" if a["kind"] != "pair" else int(a["preference"] == "none")})

    with open(DIR / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    used = [r for r in rows if not r["excluded"]]
    summary = {"listeners": listeners, "counted": sorted({r["listener"] for r in used})}
    pair_rows = [r for r in used if r["role"] == "pair"]
    for system in ("A", "B"):
        vals = [r["rating"] for r in pair_rows if r["system"] == system]
        if vals:
            ci = boot_ci(lambda s: float(np.mean(s)), vals)
            summary[f"naturalness_{system}"] = {"mean": round(float(np.mean(vals)), 2), "ci": [round(c, 2) for c in ci],
                                                "n": len(vals)}
    # paired difference B - A per (listener, pair)
    diffs = []
    for lid in summary["counted"]:
        for p in test["pairs"]:
            if p.get("repeat_of"):
                continue
            got = {r["system"]: r["rating"] for r in pair_rows if r["listener"] == lid and r["screen"] == p["pair"]}
            if "A" in got and "B" in got:
                diffs.append(got["B"] - got["A"])
    if diffs:
        ci = boot_ci(lambda s: float(np.mean(s)), diffs)
        summary["naturalness_B_minus_A"] = {"mean": round(float(np.mean(diffs)), 2), "ci": [round(c, 2) for c in ci],
                                            "pairs": len(diffs)}
    prefs = {"A": 0, "B": 0, "none": 0}
    for lid in summary["counted"]:
        for p in test["pairs"]:
            if p.get("repeat_of"):
                continue
            chosen = [r for r in pair_rows if r["listener"] == lid and r["screen"] == p["pair"]]
            if not chosen:
                continue
            if chosen[0]["no_preference"] == 1:
                prefs["none"] += 1
            else:
                winner = next((r["system"] for r in chosen if r["preferred"] == 1), None)
                if winner:
                    prefs[winner] += 1
    summary["preference"] = {**prefs, "sign_test_p": round(sign_test(prefs["A"], prefs["B"]), 4)}
    by_type = {}
    for t in ("short", "numbers", "explanation"):
        sel = [r for r in pair_rows if r["type"] == t]
        by_type[t] = {s: round(float(np.mean([r["rating"] for r in sel if r["system"] == s])), 2)
                      for s in ("A", "B") if any(r["system"] == s for r in sel)}
    summary["naturalness_by_type"] = by_type
    defects = {}
    for system in ("A", "B"):
        sel = [r for r in pair_rows if r["system"] == system]
        counts = {}
        for r in sel:
            for d in filter(None, r["defects"].split(";")):
                counts[d] = counts.get(d, 0) + 1
        defects[system] = {"clips_rated": len(sel), "ticked": counts}
    summary["defects"] = defects
    anchors = [r for r in used if r["role"] == "anchor"]
    summary["anchors"] = {r["type"]: r["rating"] for r in anchors} if len(summary["counted"]) == 1 else {
        t: round(float(np.mean([r["rating"] for r in anchors if r["type"] == t])), 2) for t in {r["type"] for r in anchors}}
    if len(summary["counted"]) >= 2:
        clips = sorted({r["clip"] for r in pair_rows})
        matrix = [[next(r["rating"] for r in pair_rows if r["listener"] == lid and r["clip"] == c) for c in clips]
                  for lid in summary["counted"]]
        summary["kendalls_w"] = round(kendall_w(matrix), 3)

    # DNSMOS against people: mean rating per clip (pairs + anchors)
    clip_mean = {}
    for r in used:
        if r["role"] in ("pair", "anchor"):
            clip_mean.setdefault(r["clip"], []).append(r["rating"])
    clip_mean = {c: float(np.mean(v)) for c, v in clip_mean.items()}
    pred = dnsmos_scores(sorted(clip_mean))
    corr = {}
    for name, ids in (("all", list(clip_mean)), ("A", [c for c in clip_mean if key[c]["system"] == "A" and key[c]["role"] == "pair"]),
                      ("B", [c for c in clip_mean if key[c]["system"] == "B" and key[c]["role"] == "pair"]),
                      ("pairs only", [c for c in clip_mean if key[c]["role"] == "pair"])):
        people, model = [clip_mean[c] for c in ids], [pred[c] for c in ids]
        rho = spearman(people, model)
        ci = boot_ci(lambda s: spearman([x for x, _ in s], [y for _, y in s]), list(zip(people, model)))
        corr[name] = {"spearman": round(rho, 3) if rho is not None else None,
                      "ci": [round(c, 2) for c in ci] if ci else None, "clips": len(ids)}
    summary["dnsmos_vs_people"] = corr
    summary["dnsmos_by_system"] = {s: round(float(np.mean([pred[c] for c in clip_mean if key[c]["system"] == s
                                                           and key[c]["role"] == "pair"])), 2) for s in ("A", "B")}
    summary["dnsmos_anchors"] = {key[c]["anchor"]: pred[c] for c in clip_mean if key[c]["role"] == "anchor"}
    (DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
