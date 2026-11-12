"""Knowledge-base retrieval: heading-based chunking, BM25 keyword search and local dense
embeddings, merged with reciprocal rank fusion.

Everything runs in process. With about a hundred chunks, a brute-force dot product over a
NumPy matrix is faster than any vector database round trip, and a query costs a few
milliseconds with no network call.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass

import numpy as np

from .content import Page, Product

log = logging.getLogger(__name__)

TARGET_MAX_WORDS = 300
MERGE_BELOW_WORDS = 80


@dataclass(frozen=True)
class Chunk:
    id: str
    url: str
    page_title: str
    section: str
    text: str  # what the model sees, prefixed with page and section

    def as_dict(self, score: float | None = None) -> dict:
        d = {"id": self.id, "url": self.url, "page_title": self.page_title, "section": self.section, "text": self.text}
        if score is not None:
            d["score"] = round(score, 4)
        return d


# ---------------------------------------------------------------- chunking


def _words(text: str) -> int:
    return len(text.split())


def _sections(body: str) -> tuple[str, list[tuple[str, str, list[tuple[str, str]]]]]:
    """Split Markdown into (intro, [(h2, h2_text, [(h3, h3_text), ...]), ...])."""
    intro: list[str] = []
    h2s: list[tuple[str, list[str], list[tuple[str, list[str]]]]] = []
    for line in body.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            h2s.append((line[3:].strip(), [], []))
        elif line.startswith("### ") and h2s:
            h2s[-1][2].append((line[4:].strip(), []))
        elif h2s and h2s[-1][2]:
            h2s[-1][2][-1][1].append(line)
        elif h2s:
            h2s[-1][1].append(line)
        else:
            intro.append(line)
    j = lambda lines: "\n".join(lines).strip()  # noqa: E731
    return j(intro), [(h, j(t), [(sh, j(st)) for sh, st in subs]) for h, t, subs in h2s]


def chunk_page(page: Page) -> list[Chunk]:
    """Chunk by heading, aiming for 150 to 300 words per chunk.

    Each H2 section becomes one chunk with its H3 subsections; an oversized section is split
    into groups of H3 subsections; consecutive small chunks are merged.
    """
    intro, h2s = _sections(page.body)
    pieces: list[tuple[str, str]] = []  # (section label, markdown text)
    if intro:
        pieces.append(("Introduction", intro))
    for h2, h2_text, subs in h2s:
        full = "\n\n".join([h2_text] + [f"{sh}\n{st}" for sh, st in subs]).strip()
        if _words(full) <= TARGET_MAX_WORDS or not subs:
            pieces.append((h2, full))
            continue
        group: list[tuple[str, str]] = []
        if h2_text:
            group.append(("", h2_text))
        for sh, st in subs:
            candidate = group + [(sh, f"{sh}\n{st}")]
            if group and _words(" ".join(t for _, t in candidate)) > TARGET_MAX_WORDS:
                pieces.append((f"{h2}: " + ", ".join(n for n, _ in group if n), "\n\n".join(t for _, t in group)))
                group = [(sh, f"{sh}\n{st}")]
            else:
                group = candidate
        if group:
            pieces.append((f"{h2}: " + ", ".join(n for n, _ in group if n), "\n\n".join(t for _, t in group)))

    merged: list[tuple[str, str]] = []
    for label, text in pieces:
        if merged and (_words(merged[-1][1]) < MERGE_BELOW_WORDS or _words(text) < MERGE_BELOW_WORDS) and (
            _words(merged[-1][1]) + _words(text) <= TARGET_MAX_WORDS
        ):
            prev_label, prev_text = merged[-1]
            merged[-1] = (f"{prev_label}; {label}", f"{prev_text}\n\n{label}\n{text}")
        else:
            merged.append((label, text))

    prefix = page.title
    if page.kind == "product":
        prefix = f"{page.meta['name']} (model {page.meta['sku']}, ${page.meta['price']:.2f})"
    return [
        Chunk(f"{page.url}#{i}", page.url, page.title, label, f"{prefix} | {label}\n{text}")
        for i, (label, text) in enumerate(merged)
    ]


def product_summary_chunk(p: Product) -> Chunk:
    """A short fact card per product, so price, warranty and category questions always have a
    precise hit. Stock is left out on purpose: it changes, so it comes from check_stock."""
    restock = f" Next shipment expected {p.restock_date}." if p.restock_date else ""
    text = (
        f"{p.name} (model {p.sku}) | Summary\n"
        f"{p.name}, model number {p.sku}, is in the {p.category} category. {p.tagline} "
        f"Price: ${p.price:.2f}. Warranty: {p.warranty_months} months. "
        f"Colors: {', '.join(p.colors)}.{restock}"
    )
    return Chunk(f"{p.url}#summary", p.url, p.name, "Summary", text)


# ---------------------------------------------------------------- search

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set(
    "a an and are as at be by can do does for from has have how i if in is it its me my of on or our "
    "the this to was what when where which who will with you your".split()
)


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = [t for t in _TOKEN.findall(text) if t not in _STOP]
    # Model numbers such as "VL-A300" also index as "vla300" and "a300", so "A300",
    # "VL A300" and "VLA300" all match.
    for m in re.finditer(r"\b([a-z]{2})[-\s]?([a-z]\d{3})\b", text):
        tokens += [m.group(1) + m.group(2), m.group(2)]
    return tokens


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.avg = sum(self.lens) / max(len(docs), 1)
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(len(self.tfs))
        for i, tf in enumerate(self.tfs):
            norm = self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg)
            out[i] = sum(self.idf.get(t, 0) * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in query if t in tf)
        return out


class Retriever:
    def __init__(self, pages: list[Page], products: list[Product], dense: bool = True,
                 embedding_model: str = "BAAI/bge-small-en-v1.5"):
        self.chunks: list[Chunk] = [product_summary_chunk(p) for p in products]
        for page in pages:
            self.chunks += chunk_page(page)
        self.bm25 = BM25([tokenize(c.text) for c in self.chunks])
        self.embedder = None
        self.matrix: np.ndarray | None = None
        if dense:
            try:
                from fastembed import TextEmbedding

                t = time.perf_counter()
                self.embedder = TextEmbedding(embedding_model)
                self.matrix = self._normalise(np.array(list(self.embedder.embed([c.text for c in self.chunks]))))
                log.info("Embedded %d chunks in %.0f ms", len(self.chunks), (time.perf_counter() - t) * 1000)
            except Exception:  # model download or ONNX failure: keyword search still works
                log.exception("Dense retrieval unavailable; using BM25 only")
                self.embedder = None

    @staticmethod
    def _normalise(m: np.ndarray) -> np.ndarray:
        return m / np.linalg.norm(m, axis=-1, keepdims=True)

    @property
    def mode(self) -> str:
        return "hybrid" if self.embedder is not None else "bm25"

    def search(self, query: str, top_k: int = 4) -> list[tuple[Chunk, float]]:
        """Reciprocal rank fusion of BM25 and dense rankings; returns (chunk, fused score)."""
        rankings = []
        bm = self.bm25.scores(tokenize(query))
        rankings.append([i for i in np.argsort(-bm) if bm[i] > 0])
        if self.embedder is not None and self.matrix is not None:
            q = self._normalise(np.array(list(self.embedder.query_embed([query])))[0])
            rankings.append(list(np.argsort(-(self.matrix @ q))))
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, i in enumerate(ranking[:50]):
                fused[int(i)] = fused.get(int(i), 0.0) + 1.0 / (60 + rank + 1)
        best = sorted(fused.items(), key=lambda kv: -kv[1])[:top_k]
        return [(self.chunks[i], s) for i, s in best]


def format_context(results: list[tuple[Chunk, float]]) -> str:
    return "\n\n".join(f"[{i + 1}] source: {c.url}\n{c.text}" for i, (c, _) in enumerate(results))
