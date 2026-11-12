"""The agent's tools. Each one is a plain function with a JSON schema; the same registry backs
/chat, the POST /tools/{name} endpoints, and (later) the LiveKit and Vapi tool wrappers, so every
channel sees identical tools."""

from __future__ import annotations

import datetime as dt
import difflib
import re
from dataclasses import dataclass
from typing import Any, Callable

from .content import Product
from .db import Database
from .retrieval import Retriever

STATUS_TEXT = {
    "processing": "processing, not shipped yet",
    "shipped": "shipped",
    "out_for_delivery": "out for delivery today",
    "delivered": "delivered",
    "cancelled": "cancelled and refunded",
    "refunded": "returned and refunded",
}
LOW_STOCK = 10


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text))


def _speakable_date(iso: str | None, today: dt.date) -> str | None:
    if not iso:
        return None
    d = dt.date.fromisoformat(iso)
    label = d.strftime("%A, %B ") + str(d.day)
    if d == today:
        return f"today, {label}"
    if d == today + dt.timedelta(days=1):
        return f"tomorrow, {label}"
    return label


@dataclass
class ToolContext:
    db: Database
    products: list[Product]
    retriever: Retriever | None = None
    today: Callable[[], dt.date] = dt.date.today

    def product_by_sku(self, sku: str) -> Product | None:
        return next((p for p in self.products if p.sku == sku), None)


# ---------------------------------------------------------------- product matching


def match_product(query: str, products: list[Product]) -> tuple[Product | None, list[Product]]:
    """Resolve a spoken or typed product reference. Returns (match, candidates): a single
    confident match, or None plus the closest candidates so the agent can ask which one."""
    q = _norm(query)
    if not q:
        return None, []
    scored: list[tuple[float, Product]] = []
    for p in products:
        sku = _norm(p.sku)
        names = [_norm(p.name)] + [_norm(a) for a in p.aliases]
        if q in (sku, sku[2:]) or q in names:
            return p, [p]
        score = max(difflib.SequenceMatcher(None, q, n).ratio() for n in names + [sku])
        if sku[2:] in q or any(len(n) >= 5 and n in q for n in names):
            score = max(score, 0.9)
        qwords = set(re.findall(r"[a-z0-9]+", query.lower()))
        nwords = set(re.findall(r"[a-z0-9]+", " ".join((p.name,) + p.aliases).lower()))
        if qwords:
            score = max(score, len(qwords & nwords) / len(qwords) * 0.85)
        scored.append((score, p))
    scored.sort(key=lambda sp: -sp[0])
    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= 0.6 and best_score - runner_up >= 0.1:
        return best, [best]
    return None, [p for s, p in scored[:3] if s >= 0.35]


def _not_found(query: str, candidates: list[Product]) -> dict:
    if candidates:
        return {"found": False, "message": f"'{query}' matches more than one product. Ask which one.",
                "candidates": [{"name": p.name, "sku": p.sku} for p in candidates]}
    return {"found": False, "message": f"Voltline does not sell a product matching '{query}'."}


# ---------------------------------------------------------------- tools


def get_order_status(ctx: ToolContext, order_id: str) -> dict:
    oid = _digits(order_id)
    order = ctx.db.get_order(oid) if oid else None
    if order is None:
        return {"found": False, "order_id": oid,
                "message": "No order with that number. Order numbers have four digits; ask the customer to check."}
    today = ctx.today()
    items = []
    for it in order["items"]:
        p = ctx.product_by_sku(it["sku"])
        items.append(f"{it['qty']} x {p.name if p else it['sku']}")
    result = {
        "found": True,
        "order_id": oid,
        "order_id_spoken": " ".join(oid),
        "status": order["status"],
        "status_text": STATUS_TEXT.get(order["status"], order["status"]),
        "items": items,
        "placed": _speakable_date(order["placed_date"], today),
        "customer_first_name": order["customer_first_name"],
    }
    if order["status"] in ("processing", "shipped", "out_for_delivery"):
        result["estimated_delivery"] = _speakable_date(order["eta_date"], today)
    elif order["status"] == "delivered":
        result["delivered_on"] = _speakable_date(order["eta_date"], today)
    if order["carrier"]:
        result["carrier"] = order["carrier"]
        result["tracking_number"] = order["tracking_number"]
    return result


def check_stock(ctx: ToolContext, product: str) -> dict:
    p, candidates = match_product(product, ctx.products)
    if p is None:
        return _not_found(product, candidates)
    result: dict[str, Any] = {"found": True, "name": p.name, "sku": p.sku, "in_stock": p.stock > 0}
    if p.stock == 0:
        result["availability"] = "out of stock"
        if p.restock_date:
            result["restock_expected"] = _speakable_date(p.restock_date, ctx.today())
    elif p.stock < LOW_STOCK:
        result["availability"] = f"low stock, only {p.stock} left"
        result["units"] = p.stock
    else:
        result["availability"] = "in stock"
        result["units"] = p.stock
    return result


def get_price(ctx: ToolContext, product: str) -> dict:
    p, candidates = match_product(product, ctx.products)
    if p is None:
        return _not_found(product, candidates)
    return {"found": True, "name": p.name, "sku": p.sku, "price_usd": p.price, "price_text": f"${p.price:.2f}",
            "care_plus_price_usd": 19 if p.price < 100 else 29,
            "note": "Prices exclude sales tax. Standard delivery is free on orders of $50 or more."}


def book_callback(ctx: ToolContext, name: str, phone: str, preferred_time: str) -> dict:
    digits = _digits(phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return {"booked": False, "message": "That phone number does not have 10 digits. Ask for it again."}
    if not name.strip():
        return {"booked": False, "message": "A name is needed to book a callback."}
    cid = ctx.db.add_callback(name.strip(), digits, preferred_time.strip() or "next available")
    return {"booked": True, "callback_id": cid, "name": name.strip(),
            "phone_spoken": " ".join(digits[:3]) + ", " + " ".join(digits[3:6]) + ", " + " ".join(digits[6:]),
            "preferred_time": preferred_time,
            "note": "The support team calls back Monday to Friday 9 a.m. to 7 p.m. and Saturday 10 a.m. to 4 p.m. Eastern Time."}


def create_support_ticket(ctx: ToolContext, issue: str, order_id: str | None = None, contact: str | None = None) -> dict:
    if not issue.strip():
        return {"created": False, "message": "Describe the issue first."}
    oid = _digits(order_id) if order_id else None
    tid = ctx.db.add_ticket(issue.strip(), oid or None, contact)
    return {"created": True, "ticket_number": tid, "ticket_number_spoken": " ".join(str(tid)),
            "message": "The support team replies within one business day."}


def search_knowledge_base(ctx: ToolContext, query: str) -> dict:
    """For channels that cannot prefetch retrieval (speech-to-speech, Vapi)."""
    if ctx.retriever is None:
        return {"results": []}
    return {"results": [{"source": c.url, "text": c.text} for c, _ in ctx.retriever.search(query, top_k=3)]}


# ---------------------------------------------------------------- registry

_STR = {"type": "string"}
TOOLS: dict[str, tuple[Callable[..., dict], dict]] = {
    "get_order_status": (get_order_status, {
        "description": "Look up an order by its four-digit order number.",
        "parameters": {"type": "object", "properties": {"order_id": {**_STR, "description": "Four-digit order number, digits only"}},
                       "required": ["order_id"]}}),
    "check_stock": (check_stock, {
        "description": "Check whether a product is in stock now, and when it is due back if not.",
        "parameters": {"type": "object", "properties": {"product": {**_STR, "description": "Product name or model number"}},
                       "required": ["product"]}}),
    "get_price": (get_price, {
        "description": "Get a product's current price.",
        "parameters": {"type": "object", "properties": {"product": {**_STR, "description": "Product name or model number"}},
                       "required": ["product"]}}),
    "book_callback": (book_callback, {
        "description": "Book a callback from the human support team. Confirm the phone number with the customer first.",
        "parameters": {"type": "object", "properties": {
            "name": {**_STR, "description": "Customer's name"},
            "phone": {**_STR, "description": "10-digit US phone number"},
            "preferred_time": {**_STR, "description": "When to call, in the customer's words"}},
            "required": ["name", "phone", "preferred_time"]}}),
    "create_support_ticket": (create_support_ticket, {
        "description": "Open a support ticket for a return, warranty claim, order change or any problem the agent cannot solve.",
        "parameters": {"type": "object", "properties": {
            "issue": {**_STR, "description": "One-sentence summary of the problem"},
            "order_id": {**_STR, "description": "Order number, if there is one"},
            "contact": {**_STR, "description": "Email or phone, if given"}},
            "required": ["issue"]}}),
    "search_knowledge_base": (search_knowledge_base, {
        "description": "Search Voltline's website content: products, specs, policies, setup guides and FAQ.",
        "parameters": {"type": "object", "properties": {"query": _STR}, "required": ["query"]}}),
}

# /chat prefetches retrieval before the LLM runs, so it does not need the search tool.
CHAT_TOOLS = ["get_order_status", "check_stock", "get_price", "book_callback", "create_support_ticket"]


def openai_tool_schemas(names: list[str]) -> list[dict]:
    return [{"type": "function", "function": {"name": n, **TOOLS[n][1]}} for n in names]


def run_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"error": f"Unknown tool {name}"}
    fn, schema = TOOLS[name]
    allowed = set(schema["parameters"]["properties"])
    missing = [r for r in schema["parameters"].get("required", []) if r not in args]
    if missing:
        return {"error": f"Missing arguments: {', '.join(missing)}"}
    try:
        return fn(ctx, **{k: v for k, v in args.items() if k in allowed})
    except Exception as exc:  # a tool failure must never crash the conversation
        return {"error": f"{type(exc).__name__}: {exc}"}
