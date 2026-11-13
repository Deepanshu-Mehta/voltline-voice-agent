"""README figures, rebuilt from the committed results (make figures).

Each figure is written twice, for GitHub's light and dark themes:
docs/images/<name>-light.png and <name>-dark.png. Colours: categorical slots 1 and 2 of the
reference palette (A blue, B orange), validated for colour-vision deficiency in both modes.
Every series is also labelled directly, so identity never rests on colour alone.

Sources: evals/runs/v1/summary.json and scored.csv, qa/listening/summary.json,
evals/results/stt-compare-summary.csv.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"

THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "grid": "#e4e3df",
              "A": "#2a78d6", "B": "#eb6834", "neutral": "#9a9890"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "grid": "#33332f",
             "A": "#3987e5", "B": "#d95926", "neutral": "#77766f"},
}
LABEL = {"A": "A  cascade", "B": "B  speech-to-speech"}


def style(fig, axes, t):
    fig.patch.set_facecolor(t["surface"])
    for ax in np.atleast_1d(axes):
        ax.set_facecolor(t["surface"])
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(t["grid"])
        ax.tick_params(colors=t["ink2"], labelsize=10, length=0)
        ax.xaxis.label.set_color(t["ink2"])
        ax.yaxis.label.set_color(t["ink2"])
        ax.grid(axis="x", color=t["grid"], linewidth=0.8)
        ax.set_axisbelow(True)


def title(fig, t, text, sub):
    fig.text(0.02, 0.97, text, ha="left", va="top", fontsize=14, fontweight="bold", color=t["ink"])
    fig.text(0.02, 0.97 - 0.33 / fig.get_figheight(), sub, ha="left", va="top", fontsize=10, color=t["ink2"])


def legend(fig, t, y=0.905, x=0.98):
    """A legend top right, marker before name (always present for two series)."""
    from matplotlib.lines import Line2D

    handles = [Line2D([], [], marker="o", linestyle="", markersize=7, color=t[k]) for k in ("A", "B")]
    leg = fig.legend(handles, [LABEL["A"], LABEL["B"]], loc="upper right", bbox_to_anchor=(x, y + 0.02), ncol=2,
                     frameon=False, fontsize=10, handletextpad=0.3, columnspacing=1.6)
    for text in leg.get_texts():
        text.set_color(t["ink2"])


def save(fig, name, mode):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}-{mode}.png", dpi=200, facecolor=fig.get_facecolor())
    plt.close(fig)


# ---------------------------------------------------------------- figures


def scorecard(mode):
    t = THEMES[mode]
    s = json.loads((ROOT / "evals/runs/v1/summary.json").read_text())["systems"]
    rows = [  # (label, metric, invert): all shown as "higher is better"
        ("Correct, judged on what was heard", "correct", False),
        ("Spoke the number it meant", "number_mismatch", True),
        ("Heard every key term the caller said", "key_terms_heard", False),
        ("Scripted conversations passed", "scenario_pass", False),
    ]
    fig, ax = plt.subplots(figsize=(9, 3.9))
    fig.subplots_adjust(left=0.36, right=0.97, top=0.76, bottom=0.14)
    style(fig, ax, t)
    for i, (label, key, invert) in enumerate(rows):
        y = len(rows) - 1 - i
        for s_name, dy in (("A", 0.14), ("B", -0.14)):
            v = s[s_name][key]
            val, lo, hi = v["value"], v["ci"][0], v["ci"][1]
            if invert:
                val, lo, hi = 1 - val, 1 - hi, 1 - lo
            ax.plot([lo * 100, hi * 100], [y + dy, y + dy], color=t[s_name], linewidth=2, solid_capstyle="round")
            ax.scatter([val * 100], [y + dy], s=64, color=t[s_name], zorder=3, edgecolors=t["surface"], linewidths=2)
            ax.text(min(hi * 100 + 1.2, 101.5), y + dy, f"{val:.0%}", va="center", fontsize=9.5, color=t["ink"])
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in reversed(rows)], fontsize=10.5, color=t["ink"])
    ax.set_xlim(30, 105)
    ax.set_xticks([40, 60, 80, 100])
    ax.set_xticklabels(["40%", "60%", "80%", "100%"])
    title(fig, t, "Where each system holds up", "Share of calls, with 95% bootstrap intervals · 282 paired calls")
    legend(fig, t, y=0.83)
    save(fig, "scorecard", mode)


def response_times(mode):
    t = THEMES[mode]
    rows = [r for r in csv.DictReader(open(ROOT / "evals/runs/v1/scored.csv"))
            if r["kind"] == "question" and r.get("response_ms") not in ("", None)]
    fig, ax = plt.subplots(figsize=(9, 3.2))
    fig.subplots_adjust(left=0.2, right=0.97, top=0.72, bottom=0.2)
    style(fig, ax, t)
    rng = np.random.default_rng(1)
    for i, s_name in enumerate(("B", "A")):
        vals = np.array([float(r["response_ms"]) / 1000 for r in rows if r["system"] == s_name])
        y = i + rng.uniform(-0.18, 0.18, len(vals))
        ax.scatter(vals, y, s=14, color=t[s_name], alpha=0.55, linewidths=0)
        med = float(np.median(vals))
        ax.plot([med, med], [i - 0.3, i + 0.3], color=t["ink"], linewidth=2, solid_capstyle="round")
        ax.text(med, i + 0.36, f"median {med:.2f} s", ha="center", va="bottom", fontsize=9.5, color=t["ink"])
    ax.set_yticks([0, 1])
    ax.set_yticklabels([LABEL["B"], LABEL["A"]], fontsize=10.5, color=t["ink"])
    ax.set_xlabel("Seconds from the end of the caller's speech to the agent's first sound")
    ax.set_ylim(-0.6, 1.75)
    lo = min(float(r["response_ms"]) for r in rows) / 1000
    hi = max(float(r["response_ms"]) for r in rows) / 1000
    ax.set_xlim(max(0, lo - 0.2), hi + 0.2)
    title(fig, t, "B answers 344 ms sooner", "Every question call, measured from the recorded audio · paired difference "
                                             "−344 ms [−454, −263]")
    save(fig, "response-time", mode)


def people_vs_dnsmos(mode):
    t = THEMES[mode]
    lis = json.loads((ROOT / "qa/listening/summary.json").read_text())
    run = json.loads((ROOT / "evals/runs/v1/summary.json").read_text())["systems"]
    panels = [
        ("Listeners: naturalness", {s: (lis[f"naturalness_{s}"]["mean"], *lis[f"naturalness_{s}"]["ci"]) for s in "AB"},
         f"{len(lis['counted'])} listeners, 24 pairs, blind"),
        ("DNSMOS: predicted quality", {s: (run[s]["dnsmos_p808"]["value"], *run[s]["dnsmos_p808"]["ci"]) for s in "AB"},
         "Microsoft's model, every reply"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.3), sharey=False)
    fig.subplots_adjust(left=0.14, right=0.97, top=0.7, bottom=0.17, wspace=0.55)
    style(fig, axes, t)
    for ax, (name, vals, note) in zip(axes, panels):
        for i, s_name in enumerate(("B", "A")):
            v, lo, hi = vals[s_name]
            ax.plot([lo, hi], [i, i], color=t[s_name], linewidth=2, solid_capstyle="round")
            ax.scatter([v], [i], s=70, color=t[s_name], zorder=3, edgecolors=t["surface"], linewidths=2)
            ax.text(hi + 0.06, i, f"{v:.2f}", va="center", fontsize=10, color=t["ink"])
        ax.set_yticks([0, 1])
        ax.set_yticklabels(["B", "A"], fontsize=11, color=t["ink"])
        ax.set_xlim(2.4, 4.4)
        ax.set_ylim(-0.6, 1.6)
        ax.set_title(name, loc="left", fontsize=11, fontweight="bold", color=t["ink"], pad=10)
        ax.set_xlabel(f"1 to 5 · {note}", fontsize=9)
    title(fig, t, "People prefer A; the quality predictor prefers B",
          "Means with 95% intervals · correlation of DNSMOS with listeners across clips: 0.19 [−0.25, 0.58]")
    save(fig, "listeners-vs-dnsmos", mode)


def hearing(mode):
    t = THEMES[mode]
    rows = {(r["engine"], r["group"]): r for r in csv.DictReader(open(ROOT / "evals/results/stt-compare-summary.csv"))}
    engines = [("deepgram", "Deepgram Nova-3"), ("gnani-v3.0-kt", "Gnani Prisma v3.0"),
               ("gnani-v2.5", "Gnani Prisma v2.5"), ("whisper", "Whisper small.en")]
    fig, ax = plt.subplots(figsize=(9, 3.5))
    fig.subplots_adjust(left=0.22, right=0.95, top=0.72, bottom=0.17)
    style(fig, ax, t)
    for i, (key, name) in enumerate(reversed(engines)):
        syn = float(rows[(key, "clean")]["wer_mean"]) * 100
        real = rows[(key, "recorded")]
        r, lo, hi = (float(real[k]) * 100 for k in ("wer_mean", "wer_ci_low", "wer_ci_high"))
        ax.plot([syn, r], [i, i], color=t["grid"], linewidth=3, solid_capstyle="round", zorder=1)
        ax.plot([lo, hi], [i, i], color=t["A"], linewidth=1.5, alpha=0.6, zorder=2)
        ax.scatter([syn], [i], s=60, color=t["neutral"], zorder=3, edgecolors=t["surface"], linewidths=2)
        ax.scatter([r], [i], s=70, color=t["A"], zorder=4, edgecolors=t["surface"], linewidths=2)
        ax.text(hi + 0.8, i, f"{r:.1f}%", va="center", fontsize=10, color=t["ink"])
        if i == len(engines) - 1:
            ax.text(syn, i + 0.32, "synthetic", ha="center", fontsize=9, color=t["ink2"])
            ax.text(r, i + 0.32, "real speech", ha="center", fontsize=9, color=t["ink2"])
    ax.set_yticks(range(len(engines)))
    ax.set_yticklabels([n for _, n in reversed(engines)], fontsize=10.5, color=t["ink"])
    ax.set_xlim(-1.5, 49)
    ax.set_xlabel("Word error rate, %  (synthetic: 30 clean clips · real: 20 recordings of Indian English, 95% interval)")
    ax.set_ylim(-0.6, len(engines) - 0.25)
    title(fig, t, "Synthetic speech hides what real speech reveals",
          "Every engine is near 0% on synthetic speech; on a real speaker they separate")
    save(fig, "hearing", mode)


def main() -> int:
    plt.rcParams["font.family"] = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
    for mode in THEMES:
        scorecard(mode)
        response_times(mode)
        people_vs_dnsmos(mode)
        hearing(mode)
    print("\n".join(sorted(p.name for p in OUT.glob("*.png"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
