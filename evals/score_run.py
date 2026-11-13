"""Score a paired run (evals/compare.py) from its recordings, the same way for both systems.

Per call
  speed        response time measured from the audio on the page (end of user speech to the
               agent's first sound); stop time after an interruption
  correctness  a local Whisper transcript of the agent's channel (what the listener heard, not
               the system's own text), checked for the key facts, then graded by an LLM judge
               against the reference answer with the same evidence for both systems: the
               knowledge-base excerpts for the question and the true result of the expected tool
  tools        the expected tool and arguments, from the server's turn log
  hearing      what the system heard (Deepgram for A, the Live model's input transcript for B)
               against the clip's reference text: word error rate, key terms
  speech       agreement between the system's own reply text and Whisper (A: the text sent to
               text-to-speech; B: the model's own transcript of its audio, which is weaker evidence)
  artifacts    clipping, the longest silence inside the reply, a reply that ends abruptly,
               loudness; pitch (Praat) and speaking rate
  quality      DNSMOS (Microsoft's non-intrusive speech quality predictor, ONNX, via torchmetrics):
               P.808 overall and P.835 signal / background / overall. It misses short dropouts
               (a 300 ms gap moved it 3.95 -> 3.62), so it does not replace the signal checks
  turn-taking  the scripted scenarios' expectations, with the final reply read from Whisper

Writes evals/runs/<run>/scored.csv and summary.json, and prints A against B with 95% bootstrap
intervals and McNemar's test for paired right-or-wrong outcomes.

Usage: uv run --project server python evals/score_run.py --run v1 [--no-judge]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import sys
import wave
from pathlib import Path

import httpx
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "driver"))

from browser_driver import CLIPS, check_expectations, normalise, read_wav, speech_end_offset_ms  # noqa: E402
from data_check import Transcriber, median_f0, merge_compounds, norm_words, wer  # noqa: E402
from run_text_eval import JUDGE_PROMPT, args_match, phrase_in  # noqa: E402

ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "server"))
from app.config import get_settings  # noqa: E402
from app.content import load_products  # noqa: E402

RATE = 16000
WRITE_TOOLS = {  # what a successful call returns; the scorer must not create real records
    "create_support_ticket": {"ticket_id": "a new five-digit number starting with 2", "status": "created"},
    "book_callback": {"callback_id": "a new four-digit number", "status": "booked"},
}
JUDGE_CACHE = ROOT / "data" / ".cache" / "judge.json"


# ---------------------------------------------------------------- audio


def old_speech_end_ms(samples: np.ndarray) -> float:
    """The driver's first method: the last sample above 0.02. A key click after the speech counts."""
    loud = np.nonzero(np.abs(samples) > 0.02)[0]
    return (loud[-1] + 1) / RATE * 1000 if len(loud) else len(samples) / RATE * 1000


def correct_turns(record: dict, clip_paths: list[Path]) -> None:
    """Re-measure each turn's response with the click-proof speech end (driver method 2):
    the reply's onset is unchanged, so the response grows by how much earlier speech really ended."""
    for turn, path in zip(record.get("turns") or [], clip_paths):
        if turn.get("speech_end_method") == 2 or turn.get("measured_response_ms") is None or not path.exists():
            continue
        x = read_wav(path)
        shift = old_speech_end_ms(x) - speech_end_offset_ms(x)
        turn["measured_response_ms_v1"] = turn["measured_response_ms"]
        turn["measured_response_ms"] = round(turn["measured_response_ms"] + shift)
        turn["speech_end"] = turn["speech_end"] - shift
        turn["speech_end_method"] = 2


def load_stereo(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with wave.open(str(path), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    x = x.reshape(-1, 2)
    return x[:, 0], x[:, 1]


def frame_levels(x: np.ndarray, hop: int = 320) -> np.ndarray:
    n = len(x) // hop
    if n == 0:
        return np.zeros(0)
    f = x[: n * hop].reshape(n, hop)
    return 20 * np.log10(np.sqrt((f ** 2).mean(axis=1)) + 1e-9)


_DNSMOS = None


def dnsmos(seg: np.ndarray) -> dict:
    global _DNSMOS
    if _DNSMOS is None:
        import torch
        from torchmetrics.functional.audio.dnsmos import deep_noise_suppression_mean_opinion_score

        _DNSMOS = (torch, deep_noise_suppression_mean_opinion_score)
    torch, fn = _DNSMOS
    p808, sig, bak, ovrl = fn(torch.from_numpy(seg.astype(np.float32)), fs=RATE, personalized=False).tolist()
    return {"dnsmos_p808": round(p808, 2), "dnsmos_sig": round(sig, 2), "dnsmos_bak": round(bak, 2),
            "dnsmos_ovrl": round(ovrl, 2)}


def artifacts(seg: np.ndarray) -> dict:
    """Signal checks on the agent's reply (20 ms frames)."""
    lv = frame_levels(seg)
    if len(lv) == 0 or lv.max() < -60:
        return {"agent_silent": 1}
    voiced = lv > max(lv.max() - 35, -55)
    idx = np.nonzero(voiced)[0]
    first, last = idx[0], idx[-1]
    gaps, run = [], 0
    for v in voiced[first: last + 1]:
        if v:
            if run:
                gaps.append(run)
            run = 0
        else:
            run += 1
    tail = lv[max(first, last - 2): last + 1].mean()  # the last 60 ms of speech
    body = np.median(lv[voiced])
    voiced_audio = seg[first * 320: (last + 1) * 320]
    return {
        "agent_silent": 0,
        "reply_s": round((last - first + 1) * 0.02, 2),
        "clipped_pct": round(float((np.abs(seg) >= 0.999).mean() * 100), 3),
        "longest_gap_ms": max(gaps, default=0) * 20,
        "abrupt_end": int(tail > body - 3),  # still at full level when the audio stops
        "agent_dbfs": round(float(20 * np.log10(np.sqrt((voiced_audio ** 2).mean()) + 1e-9)), 1),
    }


# ---------------------------------------------------------------- statistics


def boot(values: list[float], n: int = 2000, seed: int = 3, fn=np.mean) -> tuple[float, float, float] | None:
    v = np.asarray([x for x in values if x is not None and not (isinstance(x, float) and math.isnan(x))], float)
    if len(v) == 0:
        return None
    rng = np.random.default_rng(seed)
    stats = [fn(rng.choice(v, len(v))) for _ in range(n)]
    return float(fn(v)), float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))


def mcnemar(pairs: list[tuple[bool, bool]]) -> dict:
    """Exact two-sided McNemar test on paired right-or-wrong outcomes."""
    b = sum(1 for a, c in pairs if a and not c)  # A right, B wrong
    c = sum(1 for a, cc in pairs if not a and cc)  # B right, A wrong
    n = b + c
    p = 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / 2 ** n)
    return {"pairs": len(pairs), "only_A_right": b, "only_B_right": c, "p_value": round(p, 4)}


def numbers(text: str) -> set[str]:
    """Numbers as digit strings: "$119.99" -> "11999", "4 5 1 2" -> "4512", "5.99" -> "599"."""
    import re

    t = re.sub(r"(?<=\d)[ ,](?=\d)", "", text or "")
    return {n.replace(".", "") for n in re.findall(r"\d+(?:\.\d+)?", t) if len(n.replace(".", "")) >= 3}


def number_mismatch(own: str, t1: str, t2: str) -> int | None:
    """1 if the agent said a different number than its own text: a number from the text is in
    neither transcript, and both transcripts agree on a number the text does not have."""
    o, a, b = numbers(own), numbers(t1), numbers(t2)
    if not o or not t2:
        return None
    return int(bool((o - a - b) and ((a & b) - o)))


def deepgram_text(key: str, audio: np.ndarray, cache: dict) -> str | None:
    """A second, independent transcript of the agent's reply (cached by audio checksum)."""
    import io

    sha = "dg:" + hashlib.sha256(audio.tobytes()).hexdigest()[:16]
    if sha in cache:
        return cache[sha]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    for attempt in range(3):
        try:
            r = httpx.post("https://api.deepgram.com/v1/listen", params={"model": "nova-3", "smart_format": "true"},
                           content=buf.getvalue(), headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"},
                           timeout=60)
            r.raise_for_status()
            cache[sha] = r.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
            return cache[sha]
        except Exception as exc:  # one slow request must not stop a 242-call scoring run
            print(f"  Deepgram transcript failed ({exc.__class__.__name__}), attempt {attempt + 1}")
            import time

            time.sleep(2 * (attempt + 1))
    return None


# ---------------------------------------------------------------- judge


async def judge(client, model: str, question: str, reference: str, category: str, evidence: str, tools: str,
                reply: str, cache: dict) -> dict:
    key = hashlib.sha256(json.dumps([model, question, reference, evidence, tools, reply]).encode()).hexdigest()[:20]
    if key in cache:
        return cache[key]
    prompt = JUDGE_PROMPT.format(conversation=f"user: {question}", reference=reference, category=category,
                                 context=evidence or "(none)", tools=tools or "(none)", reply=reply or "(no reply)")
    prompt += ("\n\nNote: the reply is given as two independent speech-recognition transcripts of the agent's "
               "audio. Ignore spelling, punctuation and number formatting (\"$149.99\", \"149.99\" and \"one forty-nine "
               "ninety-nine\" are the same). If the two transcripts disagree about a word or number, the audio is "
               "unclear there: do not count it against the agent. If both agree, that is what the agent said.")
    for attempt in range(8):  # the judge model's rate limit: back off and retry
        try:
            resp = await client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}],
                                                        response_format={"type": "json_object"})
            break
        except Exception as exc:
            if "429" not in str(exc) and "rate" not in str(exc).lower() or attempt == 7:
                raise
            await asyncio.sleep(min(60, 4 * 2 ** attempt))
    try:
        out = json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, TypeError):
        out = {"correct": None, "hallucination": None, "reason": "judge returned invalid JSON"}
    cache[key] = out
    return out


# ---------------------------------------------------------------- scoring


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--judge-provider", help="groq | gemini | deepseek | openai (default: the agent's provider). "
                                            "One judge must grade every call of a run.")
    args = ap.parse_args()
    run_dir = ROOT / "evals" / "runs" / args.run
    records = {}
    private_dir = ROOT / "data" / "private" / "runs" / args.run
    for path in (run_dir / "records.jsonl", private_dir / "records.jsonl"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            r = json.loads(line)
            records[(r["item"], r["system"], r["rep"])] = r  # the last attempt wins
    questions = {json.loads(x)["id"]: json.loads(x) for x in (ROOT / "evals/questions.jsonl").read_text().splitlines() if x}
    scenarios = {s["id"]: s for s in json.loads((ROOT / "evals/scenarios.json").read_text())}
    manifest = {m["clip_id"]: m for m in csv.DictReader(open(ROOT / "data/manifest.csv"))}
    private = ROOT / "data/private/manifest-private.csv"
    if private.exists():
        manifest.update({m["clip_id"]: m for m in csv.DictReader(open(private))})
    products = load_products(get_settings().content_dir)
    whisper = Transcriber(True)
    cache = json.loads(JUDGE_CACHE.read_text()) if JUDGE_CACHE.exists() else {}
    s = get_settings()
    judge_client = None
    from app.config import LLM_PRESETS

    provider = args.judge_provider or s.llm_provider
    preset = LLM_PRESETS[provider]
    judge_key = getattr(s, preset["key_env"], None)
    judge_model = preset["judge"] if args.judge_provider else s.resolved_judge_model
    if not args.no_judge and judge_key:
        from openai import AsyncOpenAI

        judge_client = AsyncOpenAI(api_key=judge_key, base_url=preset["base_url"], max_retries=2)
    print(f"judge: {judge_model if judge_client else 'off'}")
    http = httpx.AsyncClient(base_url=args.api, timeout=30)
    dg_key = s.deepgram_api_key

    rows = []
    for (item, system, rep), r in sorted(records.items()):
        out = {"item": item, "system": system, "rep": rep, "set": r["set"], "kind": r["kind"],
               "question_id": r["question_id"], "condition": manifest.get(item, {}).get("condition", r["set"]),
               "accent": manifest.get(item, {}).get("accent", ""), "error": "; ".join(r.get("problems") or [])
               if r["kind"] == "question" else ""}
        if r["kind"] == "question":
            paths = [ROOT / manifest.get(item, {}).get("path", "missing")]
        else:
            base = item.split("-r")[0] if "-r" in item[-3:] else item
            paths = [CLIPS / "scenarios" / f"{base}-{n}.wav" for n in range(1, len(r.get("turns") or []) + 1)]
        correct_turns(r, paths)
        turns, srv = r.get("turns") or [], r.get("rows") or []
        rec_path = ROOT / r["recording"] if r.get("recording") else None
        if not turns or not rec_path or not rec_path.exists():
            out["error"] = out["error"] or "no recording"
            rows.append(out)
            continue
        left, right = load_stereo(rec_path)
        t0 = r.get("recording_t0_ms") or 0
        last_end = turns[-1]["speech_end"]
        start = max(0, int((last_end - t0) / 1000 * RATE))
        reply_audio = right[start:]
        out["response_ms"] = turns[0].get("measured_response_ms") if r["kind"] == "question" else None
        out["responses_ms"] = json.dumps([t.get("measured_response_ms") for t in turns])
        out.update(artifacts(reply_audio))
        sha = hashlib.sha256(reply_audio.tobytes()).hexdigest()[:16]
        spoken = whisper(sha, reply_audio) if len(reply_audio) > RATE * 0.3 else ""
        out["spoken"] = spoken or ""
        if out.get("reply_s"):
            out.update(dnsmos(reply_audio))
            words = len(norm_words(spoken or ""))
            out["words_per_s"] = round(words / out["reply_s"], 2) if out["reply_s"] else None
            out["agent_f0_hz"] = median_f0(reply_audio, RATE)
        own = (srv[-1]["reply"] if srv else "") or ""
        out["own_text"] = own
        out["own_vs_heard_wer"] = wer(own, spoken or "") if own and spoken else None
        out["model"] = srv[-1]["model"] if srv else ""

        if r["kind"] == "question":
            q = questions.get(r["question_id"], {})
            ref_text = manifest.get(item, {}).get("reference_text", "")
            heard = srv[0]["user_text"] if srv else ""
            out["heard"] = heard
            out["hearing_wer"] = wer(ref_text, heard) if heard else None
            terms = [t for t in manifest.get(item, {}).get("key_terms", "").split("; ") if t]
            got = "".join(merge_compounds(norm_words(ref_text), norm_words(heard)))
            out["key_terms_heard"] = int(all("".join(norm_words(t)) in got for t in terms)) if terms else None
            facts = q.get("must_include") or []
            out["key_facts"] = int(all(any(phrase_in(p, spoken or "") or normalise(p) in normalise(spoken or "")
                                           for p in group) for group in facts)) if facts else None
            exp = q.get("expected_tool")
            calls = [c for row in srv for c in json.loads(row["tool_calls"] or "[]")]
            out["tools_used"] = ",".join(c["name"] for c in calls)
            if exp:
                out["tool_right"] = int(any(c["name"] == exp["name"] for c in calls))
                out["tool_args_right"] = int(any(c["name"] == exp["name"] and args_match(exp.get("args", {}), c["args"], products)
                                                 for c in calls))
            if spoken and dg_key:
                out["spoken_deepgram"] = deepgram_text(dg_key, reply_audio, cache)
            if out.get("spoken_deepgram") is not None:
                out["number_mismatch"] = number_mismatch(own, spoken or "", out["spoken_deepgram"])
            if judge_client and q:
                chunks, seen_urls = [], set()
                for query in (q["turns"][0], spoken or ""):  # facts for the question, and for what was said
                    if not query:
                        continue
                    for c in (await http.post("/retrieve", json={"query": query, "top_k": 4})).json()["chunks"]:
                        if c["id"] not in seen_urls:
                            seen_urls.add(c["id"])
                            chunks.append(c)
                evidence = "\n".join(f"[{c['url']}] {c['text']}" for c in chunks)
                tool_ev = []
                for c in calls:  # the calls the agent really made
                    if c["name"] in WRITE_TOOLS:  # re-running would create a record
                        tool_ev.append({"name": c["name"], "args": c["args"], "result": WRITE_TOOLS[c["name"]]})
                    else:
                        res = (await http.post(f"/tools/{c['name']}", json=c["args"])).json()
                        tool_ev.append({"name": c["name"], "args": c["args"], "result": res})
                transcript = spoken or ""
                if out.get("spoken_deepgram"):
                    transcript = (f"Transcript 1 (Whisper): {spoken}\nTranscript 2 (Deepgram): {out['spoken_deepgram']}")
                v = await judge(judge_client, judge_model, q["turns"][0], q["reference"], q["category"], evidence,
                                json.dumps(tool_ev) if tool_ev else "", transcript, cache)
                out["correct"] = int(bool(v.get("correct"))) if v.get("correct") is not None else None
                out["hallucination"] = int(bool(v.get("hallucination"))) if v.get("hallucination") is not None else None
                out["judge_reason"] = v.get("reason", "")
        else:
            sc = scenarios[item.split("-r")[0] if "-r" in item[-3:] else item]
            heard_rows = [dict(x) for x in srv]
            if heard_rows:
                heard_rows[-1]["reply"] = spoken or ""  # what was actually heard, for both systems
            problems = check_expectations(sc.get("expect", {}), heard_rows)
            out["scenario_pass"] = int(not problems)
            out["scenario_problems"] = "; ".join(problems)
            out["user_turns_logged"] = len(srv)
            out["interrupted_turns"] = sum(1 for x in srv if json.loads(x["extra"] or "{}").get("voice", {}).get("interrupted"))
            stops = [json.loads(x["extra"] or "{}").get("client", {}).get("stop_latency_ms") for x in srv]
            out["stop_ms"] = next((x for x in stops if x), None)
        rows.append(out)
        if len(rows) % 20 == 0:
            whisper.save()
            JUDGE_CACHE.write_text(json.dumps(cache, indent=0))
            print(f"  scored {len(rows)}/{len(records)}", flush=True)
    whisper.save()
    JUDGE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    JUDGE_CACHE.write_text(json.dumps(cache, indent=0))
    await http.aclose()

    cols: list[str] = []
    for row in rows:
        cols += [k for k in row if k not in cols]
    public = [r for r in rows if r["set"] != "recorded"]
    hidden = [r for r in rows if r["set"] == "recorded"]  # transcripts of the owner's voice stay local
    for path, subset in ((run_dir / "scored.csv", public), (private_dir / "scored-recorded.csv", hidden)):
        if subset:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                w.writerows(subset)

    summary = summarise(rows)
    summary["judge_model"] = judge_model if judge_client else None
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print_summary(summary)
    print(f"\nWrote {(run_dir / 'scored.csv').relative_to(ROOT)} and summary.json")
    return 0


METRICS = [  # (column, label, kind) kind: rate | ms | value
    ("response_ms", "response time, median ms (questions)", "median"),
    ("correct", "correct (judge, on what was heard)", "rate"),
    ("hallucination", "hallucination", "rate"),
    ("key_facts", "key facts spoken", "rate"),
    ("tool_right", "right tool", "rate"),
    ("tool_args_right", "right tool and arguments", "rate"),
    ("hearing_wer", "hearing: word error rate", "mean"),
    ("key_terms_heard", "hearing: all key terms heard", "rate"),
    ("own_vs_heard_wer", "speech: own text vs Whisper WER", "mean"),
    ("number_mismatch", "speech: said a different number than its text", "rate"),
    ("longest_gap_ms", "longest silence inside reply, ms", "median"),
    ("abrupt_end", "reply ends abruptly", "rate"),
    ("clipped_pct", "clipped samples %", "mean"),
    ("words_per_s", "speaking rate, words/s", "median"),
    ("dnsmos_p808", "DNSMOS P.808 (predicted quality, 1-5)", "mean"),
    ("dnsmos_sig", "DNSMOS signal (1-5)", "mean"),
    ("scenario_pass", "scripted scenarios passed", "rate"),
    ("stop_ms", "stop time after interruption, median ms", "median"),
]


def summarise(rows: list[dict]) -> dict:
    out: dict = {"systems": {}, "paired": {}, "by_condition": {}}
    for system in sorted({r["system"] for r in rows}):
        sel = [r for r in rows if r["system"] == system]
        res = {}
        for col, _, kind in METRICS:
            vals = [r.get(col) for r in sel if r.get(col) not in (None, "")]
            b = boot([float(v) for v in vals], fn=np.median if kind == "median" else np.mean)
            if b:
                res[col] = {"value": round(b[0], 3), "ci": [round(b[1], 3), round(b[2], 3)], "n": len(vals)}
        out["systems"][system] = res
    systems = sorted(out["systems"])
    if len(systems) == 2:
        a, b = systems
        by = {(r["item"], r["rep"], r["system"]): r for r in rows}
        for col in ("correct", "key_facts", "tool_args_right", "key_terms_heard", "scenario_pass"):
            pairs = [(bool(int(by[(i, rep, a)][col])), bool(int(by[(i, rep, b)][col])))
                     for (i, rep, sys_) in by if sys_ == a and (i, rep, b) in by
                     and by[(i, rep, a)].get(col) not in (None, "") and by[(i, rep, b)].get(col) not in (None, "")]
            if pairs:
                out["paired"][col] = mcnemar(pairs)
        diffs = [float(by[(i, rep, b)]["response_ms"]) - float(by[(i, rep, a)]["response_ms"])
                 for (i, rep, sys_) in by if sys_ == a and (i, rep, b) in by
                 and by[(i, rep, a)].get("response_ms") and by[(i, rep, b)].get("response_ms")]
        bd = boot(diffs, fn=np.median)
        if bd:
            out["paired"]["response_ms_B_minus_A"] = {"median": round(bd[0]), "ci": [round(bd[1]), round(bd[2])],
                                                      "pairs": len(diffs)}
    for cond in sorted({r["condition"] for r in rows if r["kind"] == "question"}):
        out["by_condition"][cond] = {}
        for system in systems:
            sel = [r for r in rows if r["system"] == system and r["condition"] == cond]
            cell = {}
            for col, kind in (("correct", "rate"), ("key_terms_heard", "rate"), ("hearing_wer", "mean"), ("response_ms", "median")):
                vals = [float(r[col]) for r in sel if r.get(col) not in (None, "")]
                b = boot(vals, fn=np.median if kind == "median" else np.mean)
                if b:
                    cell[col] = {"value": round(b[0], 3), "ci": [round(b[1], 3), round(b[2], 3)], "n": len(vals)}
            out["by_condition"][cond][system] = cell
    return out


def print_summary(s: dict) -> None:
    systems = sorted(s["systems"])
    print(f"\n{'metric':42} " + "  ".join(f"{x:>26}" for x in systems))
    for col, label, kind in METRICS:
        cells = []
        for system in systems:
            v = s["systems"][system].get(col)
            if not v:
                cells.append(f"{'-':>26}")
                continue
            fmt = (lambda x: f"{x:.0%}") if kind == "rate" else (lambda x: f"{x:.0f}") if "ms" in col else (lambda x: f"{x:.3f}")
            cells.append(f"{fmt(v['value']) + ' [' + fmt(v['ci'][0]) + ', ' + fmt(v['ci'][1]) + ']' + ' n=' + str(v['n']):>26}")
        print(f"{label:42} " + "  ".join(cells))
    if s["paired"]:
        print("\npaired (same clips):")
        for col, v in s["paired"].items():
            print(f"  {col:24} {v}")
    print("\nby condition (correct / all key terms heard / response ms):")
    for cond, cells in s["by_condition"].items():
        parts = []
        for system, c in cells.items():
            parts.append(f"{system}: " + " / ".join(
                (f"{c[k]['value']:.0%}" if k != "response_ms" else f"{c[k]['value']:.0f}") if k in c else "-"
                for k in ("correct", "key_terms_heard", "response_ms")))
        print(f"  {cond:9} " + "   ".join(parts))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
