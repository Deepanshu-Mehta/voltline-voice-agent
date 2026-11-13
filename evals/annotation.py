"""Annotation round: export agent replies for labelling in Audacity, then merge the labels and
check the automatic detectors against them (guide: qa/annotation-guide.md).

  prepare --run v1   picks 30 replies (15 per system; in each system half flagged by a detector,
                     half not; first by item id, never by ear), cuts them, and writes
                     qa/annotation/ann-01.wav ... with a key kept in qa/annotation/key.json.
                     The file names do not say which system or whether a detector fired.
  merge              reads the Audacity label exports (ann-XX.txt, and second-pass/ann-XX.txt),
                     writes qa/annotations.csv, and prints for each detector how many of its flags
                     were real (precision) and how many labelled problems it caught (recall), plus
                     Cohen's kappa between the two passes.

Usage: uv run --project server python evals/annotation.py prepare --run v1
       uv run --project server python evals/annotation.py merge
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "listening"))

from build import cut_reply, load_right, write  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "qa" / "annotation"
LABELS = ["clipping", "dropout", "cutoff", "long-silence", "mispronounced", "wrong-number", "glitch",
          "voice-change", "noise", "robotic"]
# detector -> labels it is meant to find
DETECTORS = {
    "abrupt_end": (lambda r: r.get("abrupt_end") == "1", {"cutoff"}),
    "long_gap": (lambda r: float(r.get("longest_gap_ms") or 0) >= 1500, {"long-silence", "dropout"}),
    "clipping": (lambda r: float(r.get("clipped_pct") or 0) > 0.1, {"clipping"}),
    "speech_mismatch": (lambda r: float(r.get("own_vs_heard_wer") or 0) > 0.2, {"mispronounced", "wrong-number"}),
    "dnsmos_low": (lambda r: 0 < float(r.get("dnsmos_p808") or 0) < 3.2,
                   {"clipping", "glitch", "noise", "robotic", "dropout"}),
}


def flagged(row: dict) -> list[str]:
    return [name for name, (test, _) in DETECTORS.items() if test(row)]


def prepare(run_name: str) -> int:
    run = ROOT / "evals" / "runs" / run_name
    scored = [r for r in csv.DictReader(open(run / "scored.csv")) if r.get("reply_s") and r["rep"] == "1"
              and not r.get("error")]
    records = {}
    for line in (run / "records.jsonl").read_text().splitlines():
        r = json.loads(line)
        records[(r["item"], r["system"])] = r
    picked = []
    for system in ("A", "B"):
        rows = sorted((r for r in scored if r["system"] == system and not records[(r["item"], system)].get("private")),
                      key=lambda r: r["item"])
        hit = [r for r in rows if flagged(r)][:8]
        miss = [r for r in rows if not flagged(r)][: 15 - len(hit)]
        picked += hit + miss
    rng = np.random.default_rng(1113)
    order = rng.permutation(len(picked))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "second-pass").mkdir(exist_ok=True)
    key = {}
    for n, i in enumerate(order, 1):
        row = picked[i]
        rec = records[(row["item"], row["system"])]
        start = (rec["turns"][-1]["speech_end"] - (rec.get("recording_t0_ms") or 0)) / 1000
        name = f"ann-{n:02d}"
        write(OUT / f"{name}.wav", cut_reply(load_right(ROOT / rec["recording"]), start))
        key[name] = {"system": row["system"], "item": row["item"], "flags": flagged(row)}
    (OUT / "key.json").write_text(json.dumps(key, indent=2))
    counts = {s: sum(1 for k in key.values() if k["system"] == s and k["flags"]) for s in ("A", "B")}
    print(f"{len(key)} clips in qa/annotation/ (flagged by a detector: A {counts['A']}, B {counts['B']}). "
          f"Label them in Audacity following qa/annotation-guide.md.")
    return 0


def read_labels(path: Path) -> set[str]:
    out = set()
    for line in path.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2].strip():
            label = parts[2].strip().lower()
            out.add(label if label in LABELS else "other")
    return out


def kappa(a: list[int], b: list[int]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


def merge() -> int:
    key = json.loads((OUT / "key.json").read_text())
    rows = []
    for name, k in sorted(key.items()):
        txt = OUT / f"{name}.txt"
        if not txt.exists():
            continue
        labels = read_labels(txt)
        rows.append({"clip": name, "system": k["system"], "item": k["item"], "labels": ";".join(sorted(labels)),
                     "detector_flags": ";".join(k["flags"]), **{f"label_{lab}": int(lab in labels) for lab in LABELS}})
    if not rows:
        print("No label files yet: export them from Audacity as qa/annotation/ann-XX.txt.", file=sys.stderr)
        return 1
    with open(ROOT / "qa" / "annotations.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} of {len(key)} clips labelled -> qa/annotations.csv")
    print("\nlabels per system: " + "; ".join(
        f"{s}: " + ", ".join(f"{lab} {sum(r['label_' + lab] for r in rows if r['system'] == s)}" for lab in LABELS
                             if any(r['label_' + lab] for r in rows if r['system'] == s)) for s in ("A", "B")))
    print("\ndetector        flagged  real  precision  labelled  caught  recall")
    for name, (_, targets) in DETECTORS.items():
        fl = [r for r in rows if name in r["detector_flags"].split(";")]
        real = [r for r in fl if targets & set(r["labels"].split(";"))]
        labelled = [r for r in rows if targets & set(r["labels"].split(";"))]
        caught = [r for r in labelled if name in r["detector_flags"].split(";")]
        prec = f"{len(real) / len(fl):.0%}" if fl else "-"
        rec = f"{len(caught) / len(labelled):.0%}" if labelled else "-"
        print(f"{name:15} {len(fl):7} {len(real):5} {prec:>10} {len(labelled):9} {len(caught):7} {rec:>7}")
    second = [(r, OUT / "second-pass" / f"{r['clip']}.txt") for r in rows]
    second = [(r, p) for r, p in second if p.exists()]
    if second:
        a = [int(bool(r["labels"])) for r, _ in second]
        b = [int(bool(read_labels(p))) for _, p in second]
        print(f"\nself-consistency on {len(second)} clips (any defect vs none): Cohen's kappa {kappa(a, b)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--run", default="v1")
    sub.add_parser("merge")
    args = ap.parse_args()
    return prepare(args.run) if args.cmd == "prepare" else merge()


if __name__ == "__main__":
    raise SystemExit(main())
