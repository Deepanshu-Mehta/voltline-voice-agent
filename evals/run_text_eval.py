"""Text eval: runs evals/questions.jsonl through /chat and scores every layer.

Usage (from the repository root):
  make eval-text                                  # in process, no server needed
  uv run --project server python evals/run_text_eval.py --url http://localhost:8000
  ... --no-judge                                  # deterministic checks only, no judge calls
  ... --only F01,T08                              # a subset

Scores
  retrieval_hit@3   a reference source page is among the top 3 retrieved chunks (last turn)
  keyword_pass      every must_include group has a match in the final reply
  tool_selection    the expected tool was called; tool_args: with the expected arguments
  correctness       LLM judge vs. the reference answer (needs an API key)
  hallucination     LLM judge: the reply states a fact not in the context or tool results
  style             reply length in words and formatting violations (lists, markdown, URLs, emoji)
  latency           retrieval, LLM first token and total time per turn, in milliseconds

Results are written to evals/results/<timestamp>.json and evals/results/latest.json.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from app.config import get_settings  # noqa: E402
from app.content import load_products  # noqa: E402
from app.tools import match_product  # noqa: E402

QUESTIONS = ROOT / "evals" / "questions.jsonl"
RESULTS = ROOT / "evals" / "results"
FORMAT_VIOLATIONS = {
    "list": re.compile(r"^\s*([-*•]|\d+[.)])\s", re.M),
    "markdown": re.compile(r"\*\*|__|^#+\s|`|\|", re.M),
    "url": re.compile(r"https?://|www\.|\.com\b|\.example\b"),
    "emoji": re.compile("[\U0001F300-\U0001FAFF☀-➿]"),
}

JUDGE_PROMPT = """You are grading a customer-service voice agent for Voltline Electronics, a fictional store.

Conversation so far (customer turns, with the agent's earlier replies):
{conversation}

Reference answer (what a correct final reply must convey): {reference}
Question type: {category}

Knowledge base excerpts the agent was given for its final reply:
{context}

Tool results the agent received:
{tools}

Agent's final reply:
{reply}

Grade the final reply:
- correct: true if it conveys the reference answer's key facts (or, for out_of_scope, declines or corrects the false premise politely and does not invent anything). Extra correct detail is fine. For a tool task where the reference says the agent should ask or confirm something first, asking is correct.
- hallucination: true if it states any price, spec, date, policy, discount or product that is not supported by the excerpts or tool results.

Answer with JSON only: {{"correct": true|false, "hallucination": true|false, "reason": "<one sentence>"}}"""


# ---------------------------------------------------------------- matching helpers


def _spaced(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", s.lower()).strip() + " "


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def phrase_in(phrase: str, text: str) -> bool:
    """Word-boundary match; phrases with digits also match with spacing and punctuation removed,
    so "$149.99" matches "149.99" and "V L A 300" matches "VL-A300"."""
    if _spaced(phrase) in _spaced(text):
        return True
    return any(ch.isdigit() for ch in phrase) and _compact(phrase) in _compact(text)


def args_match(expected: dict, actual: dict, products) -> bool:
    for key, want in expected.items():
        got = str(actual.get(key, ""))
        if key == "product":
            a, _ = match_product(got, products)
            b, _ = match_product(want, products)
            if not a or not b or a.sku != b.sku:
                return False
        elif key in ("order_id", "phone"):
            g = re.sub(r"\D", "", got)
            if key == "phone" and len(g) == 11 and g.startswith("1"):
                g = g[1:]
            if g != re.sub(r"\D", "", want):
                return False
        elif want.lower() not in got.lower():
            return False
    return True


# ---------------------------------------------------------------- running


async def run_conversation(client: httpx.AsyncClient, item: dict, overrides: dict) -> dict:
    history: list[dict] = []
    turns = []
    for text in item["turns"]:
        history.append({"role": "user", "content": text})
        r = await client.post("/chat", json={"messages": history, "stream": False, "channel": "eval",
                                             "session_id": f"eval-{item['id']}", **overrides}, timeout=90)
        r.raise_for_status()
        done = r.json()
        history.append({"role": "assistant", "content": done["reply"]})
        turns.append(done)
    return {"history": history, "turns": turns}


async def judge(llm, model: str, item: dict, run: dict, context: str) -> dict:
    conversation = "\n".join(f"{m['role']}: {m['content']}" for m in run["history"][:-1])
    tools = [{"name": c["name"], "args": c["args"], "result": c["result"]} for t in run["turns"] for c in t["tool_calls"]]
    prompt = JUDGE_PROMPT.format(conversation=conversation, reference=item["reference"], category=item["category"],
                                 context=context or "(none)", tools=json.dumps(tools) if tools else "(none)",
                                 reply=run["history"][-1]["content"])
    resp = await llm.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}],
                                             response_format={"type": "json_object"})
    try:
        return json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, TypeError):
        return {"correct": None, "hallucination": None, "reason": "judge returned invalid JSON"}


def score(item: dict, run: dict, products, retrieved_top3: list[str]) -> dict:
    reply = run["history"][-1]["content"]
    calls = [c for t in run["turns"] for c in t["tool_calls"]]
    out: dict = {"reply": reply, "words": len(reply.split()), "tool_calls": [{"name": c["name"], "args": c["args"]} for c in calls],
                 "sources_top3": retrieved_top3, "error": next((t["error"] for t in run["turns"] if t.get("error")), None),
                 "timings": [t["timings"] for t in run["turns"]]}
    if item["sources"] and item["category"] != "out_of_scope":
        out["retrieval_hit3"] = any(s in retrieved_top3 for s in item["sources"])
    if item["must_include"]:
        out["keyword_pass"] = all(any(phrase_in(p, reply) for p in group) for group in item["must_include"])
    if item["must_not_include"]:
        out["forbidden_found"] = [p for p in item["must_not_include"] if p.lower() in reply.lower()]
    if exp := item.get("expected_tool"):
        named = [c for c in calls if c["name"] == exp["name"]]
        out["tool_selected"] = bool(named)
        out["tool_args_ok"] = any(args_match(exp["args"], c["args"], products) for c in named)
    out["format_violations"] = [k for k, rx in FORMAT_VIOLATIONS.items() if rx.search(reply)]
    return out


def _pct(values: list[bool]) -> float | None:
    values = [v for v in values if v is not None]
    return round(100 * sum(values) / len(values), 1) if values else None


def _median(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 1) if values else None


def summarise(results: list[dict]) -> dict:
    s = {
        "n": len(results),
        "errors": sum(1 for r in results if r.get("error")),
        "retrieval_hit3_pct": _pct([r.get("retrieval_hit3") for r in results]),
        "keyword_pass_pct": _pct([r.get("keyword_pass") for r in results]),
        "tool_selection_pct": _pct([r.get("tool_selected") for r in results]),
        "tool_args_pct": _pct([r.get("tool_args_ok") for r in results]),
        "forbidden_phrase_items": sum(1 for r in results if r.get("forbidden_found")),
        "median_words": _median([r["words"] for r in results]),
        "format_violation_items": sum(1 for r in results if r["format_violations"]),
        "median_retrieval_ms": _median([t.get("retrieval_ms") for r in results for t in r["timings"]]),
        "median_llm_first_token_ms": _median([t.get("llm_first_token_ms") for r in results for t in r["timings"]]),
        "median_total_ms": _median([t.get("total_ms") for r in results for t in r["timings"]]),
    }
    judged = [r for r in results if r.get("judge")]
    if judged:
        s["correctness_pct"] = _pct([r["judge"].get("correct") for r in judged])
        s["hallucination_pct"] = _pct([r["judge"].get("hallucination") for r in judged])
        oos = [r for r in judged if r["category"] == "out_of_scope"]
        s["out_of_scope_handled"] = f"{sum(1 for r in oos if r['judge'].get('correct'))} of {len(oos)}"
        s["correctness_by_category"] = {
            c: _pct([r["judge"].get("correct") for r in judged if r["category"] == c])
            for c in ("factual", "tool", "multi_turn", "out_of_scope")}
    return s


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="Base URL of a running server; default runs the app in process")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--only", help="Comma-separated question ids")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--model", help="Override the agent's LLM model")
    ap.add_argument("--judge-model", default=os.environ.get("JUDGE_MODEL") or get_settings().judge_model)
    args = ap.parse_args()

    settings = get_settings()
    products = load_products(settings.content_dir)
    items = [json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()]
    if args.only:
        wanted = set(args.only.split(","))
        items = [i for i in items if i["id"] in wanted]

    if args.url:
        client = httpx.AsyncClient(base_url=args.url)
    else:
        from app.main import create_app

        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://eval")

    health = (await client.get("/health")).json()
    use_judge = not args.no_judge and health.get("llm_configured") and settings.resolved_llm_key
    judge_llm = None
    if use_judge:
        from openai import AsyncOpenAI

        judge_llm = AsyncOpenAI(api_key=settings.resolved_llm_key, base_url=settings.llm_base_url)
    if not health.get("llm_configured"):
        print("WARNING: the server has no LLM API key; replies will be the fallback line.", file=sys.stderr)

    overrides = {"model": args.model} if args.model else {}
    sem = asyncio.Semaphore(args.concurrency)

    async def one(item: dict) -> dict:
        async with sem:
            run = await run_conversation(client, item, overrides)
            last = run["turns"][-1]  # what the agent actually retrieved for its final reply
            result = {"id": item["id"], "category": item["category"],
                      **score(item, run, products, last["sources"][:3])}
            if judge_llm:
                ctx = (await client.post("/retrieve", json={"query": last["query"] or "-",
                                                            "top_k": max(len(last["sources"]), 1)})).json()
                context = "\n\n".join(c["text"] for c in ctx["chunks"])
                result["judge"] = await judge(judge_llm, args.judge_model, item, run, context)
            mark = "ok " if (result.get("judge", {}).get("correct", result.get("keyword_pass", True))) else "XX "
            print(f"{mark}{item['id']:4} {result['reply'][:110]!r}")
            return result

    results = await asyncio.gather(*(one(i) for i in items))
    await client.aclose()
    results.sort(key=lambda r: r["id"])

    summary = summarise(results)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = ""
    report = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), "commit": commit or None,
              "model": args.model or health.get("model"), "judge_model": args.judge_model if judge_llm else None,
              "retrieval": health.get("retrieval"), "summary": summary, "results": results}
    RESULTS.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    paths = [RESULTS / f"text-{stamp}.json"]
    # A run where every LLM call failed is not a result; keep it out of latest.json, which the
    # benchmarks page shows.
    if summary["errors"] < summary["n"]:
        paths.append(RESULTS / "latest.json")
    else:
        print("\nEvery turn failed, so latest.json was not updated.", file=sys.stderr)
    for path in paths:
        path.write_text(json.dumps(report, indent=2))

    print("\nSummary")
    for k, v in summary.items():
        print(f"  {k:28} {v}")
    print(f"\nWrote {', '.join(str(p) for p in paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
