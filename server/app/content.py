"""Loads the Markdown content in content/: the single source of truth for the website,
the knowledge base and the product tools."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter


@dataclass(frozen=True)
class Page:
    url: str  # path on the website, e.g. /products/vl-a300-pulse-buds-pro
    title: str
    kind: str  # product | doc | policy
    body: str  # Markdown without front matter
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    slug: str
    category: str
    price: float
    stock: int
    warranty_months: int
    colors: tuple[str, ...]
    aliases: tuple[str, ...]
    tagline: str
    restock_date: str | None = None

    @property
    def url(self) -> str:
        return f"/products/{self.slug}"


def _plain(value):
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def _load(path: Path) -> tuple[dict, str]:
    post = frontmatter.load(path)
    return {k: _plain(v) for k, v in post.metadata.items()}, post.content.strip()


def _title(meta: dict, body: str, fallback: str) -> str:
    if meta.get("title"):
        return meta["title"]
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def load_products(content_dir: Path) -> list[Product]:
    products = []
    for path in sorted((content_dir / "products").glob("*.md")):
        meta, _ = _load(path)
        products.append(
            Product(
                sku=meta["sku"],
                name=meta["name"],
                slug=path.stem,
                category=meta["category"],
                price=float(meta["price"]),
                stock=int(meta["stock"]),
                warranty_months=int(meta["warranty_months"]),
                colors=tuple(meta.get("colors", [])),
                aliases=tuple(meta.get("aliases", [])),
                tagline=meta.get("tagline", ""),
                restock_date=meta.get("restock_date"),
            )
        )
    return products


def load_pages(content_dir: Path) -> list[Page]:
    pages = []
    for path in sorted((content_dir / "products").glob("*.md")):
        meta, body = _load(path)
        pages.append(Page(f"/products/{path.stem}", _title(meta, body, path.stem), "product", body, meta))
    for path in sorted((content_dir / "docs").glob("*.md")):
        meta, body = _load(path)
        slug = meta.get("slug", path.stem)
        pages.append(Page(f"/docs/{slug}", _title(meta, body, slug), "doc", body, meta))
    for path in sorted(content_dir.glob("*.md")):
        meta, body = _load(path)
        slug = meta.get("slug", path.stem)
        pages.append(Page(f"/{slug}", _title(meta, body, slug), "policy", body, meta))
    return pages


def load_orders(content_dir: Path) -> list[dict]:
    return json.loads((content_dir / "orders.json").read_text())
