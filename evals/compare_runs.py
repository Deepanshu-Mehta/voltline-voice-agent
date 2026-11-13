"""What changed between two scored runs of the same system (a regression check).

  uv run --project server python evals/compare_runs.py v1 v1-eot400 --system A

Prints each metric in both runs with its 95% interval, the paired difference on the clips both
runs share, and every clip that flipped between pass and fail. The decision rule from the plan:
reject the change if correctness or turn-taking gets worse by more than the noise band (the
difference between two runs of the unchanged system).
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from score_run import METRICS, boot, mcnemar  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PASS_FAIL = ["correct", "key_facts", "tool_args_right", "key_terms_heard", "scenario_pass"]


def load(run: str, system: str) -> dict[tuple[str, str], dict]:
    rows = csv.DictReader(open(ROOT / "evals" / "runs" / run / "scored.csv"))
    return {(r["item"], r["rep"]): r for r in rows if r["system"] == system}


def fmt(col: str, v: float) -> str:
    if col in PASS_FAIL or col in ("hallucination", "abrupt_end", "tool_right"):
        return f"{v:.0%}"
    return f"{v:.0f}" if "ms" in col else f"{v:.3f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--system", default="A")
    args = ap.parse_args()
    a, b = load(args.before, args.system), load(args.after, args.system)
    shared = sorted(set(a) & set(b))
    print(f"system {args.system}: {args.before} ({len(a)} calls) -> {args.after} ({len(b)} calls), {len(shared)} shared\n")
    print(f"{'metric':42} {args.before:>22} {args.after:>22}   paired change")
    for col, label, kind in METRICS:
        fn = np.median if kind == "median" else np.mean
        va = [float(a[k][col]) for k in a if a[k].get(col) not in (None, "")]
        vb = [float(b[k][col]) for k in b if b[k].get(col) not in (None, "")]
        ba, bb = boot(va, fn=fn), boot(vb, fn=fn)
        if not ba or not bb:
            continue
        diffs = [float(b[k][col]) - float(a[k][col]) for k in shared
                 if a[k].get(col) not in (None, "") and b[k].get(col) not in (None, "")]
        bd = boot(diffs, fn=fn)
        change = f"{fmt(col, bd[0])} [{fmt(col, bd[1])}, {fmt(col, bd[2])}]" if bd else "-"
        print(f"{label:42} {fmt(col, ba[0]) + ' [' + fmt(col, ba[1]) + ', ' + fmt(col, ba[2]) + ']':>22} "
              f"{fmt(col, bb[0]) + ' [' + fmt(col, bb[1]) + ', ' + fmt(col, bb[2]) + ']':>22}   {change}")
    print("\npass/fail flips on shared clips:")
    for col in PASS_FAIL:
        pairs = [(a[k][col] == "1", b[k][col] == "1") for k in shared
                 if a[k].get(col) not in (None, "") and b[k].get(col) not in (None, "")]
        if not pairs:
            continue
        m = mcnemar(pairs)
        worse = [k[0] for k in shared if a[k].get(col) == "1" and b[k].get(col) == "0"]
        better = [k[0] for k in shared if a[k].get(col) == "0" and b[k].get(col) == "1"]
        print(f"  {col:16} worse {len(worse)} {worse[:6]}  better {len(better)} {better[:6]}  (McNemar p={m['p_value']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
