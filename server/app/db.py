"""SQLite store: demo orders (reseeded on every start), callbacks, support tickets, and the
turns table that later powers the latency dashboard."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_first_name TEXT, customer_last_name TEXT,
    items TEXT,              -- JSON list of {sku, qty}
    status TEXT,
    placed_date TEXT, eta_date TEXT,
    carrier TEXT, tracking_number TEXT
);
CREATE TABLE IF NOT EXISTS callbacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT, name TEXT, phone TEXT, preferred_time TEXT, status TEXT DEFAULT 'booked'
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT, issue TEXT, order_id TEXT, contact TEXT, status TEXT DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT, channel TEXT, mode TEXT, session_id TEXT,
    user_text TEXT, reply TEXT, tool_calls TEXT,
    retrieval_ms REAL, llm_first_token_ms REAL, total_ms REAL,
    model TEXT, commit_hash TEXT, extra TEXT
);
"""
# Ticket and callback numbers start high so they never sound like four-digit order numbers.
ID_OFFSETS = {"callbacks": 1000, "tickets": 20000}


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def seed_orders(self, orders: list[dict], today: dt.date | None = None) -> None:
        """Order dates are stored as offsets in content/orders.json, so the demo stays current."""
        today = today or dt.date.today()
        rows = []
        for o in orders:
            eta = o.get("eta_days_from_now")
            rows.append((
                o["order_id"], o["customer_first_name"], o["customer_last_name"], json.dumps(o["items"]), o["status"],
                (today - dt.timedelta(days=o["placed_days_ago"])).isoformat(),
                (today + dt.timedelta(days=eta)).isoformat() if eta is not None else None,
                o.get("carrier"), o.get("tracking_number"),
            ))
        with self.lock:
            self.conn.execute("DELETE FROM orders")
            self.conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)", rows)
            self.conn.commit()

    def get_order(self, order_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["items"] = json.loads(d["items"])
        return d

    def _insert(self, table: str, values: dict) -> int:
        values = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **values}
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self.lock:
            # The first row of each table gets id = offset + 1.
            if table in ID_OFFSETS and self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0:
                values = {"id": ID_OFFSETS[table] + 1, **values}
                cols, marks = ", ".join(values), ", ".join("?" for _ in values)
            cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))
            self.conn.commit()
            return int(cur.lastrowid)

    def add_callback(self, name: str, phone: str, preferred_time: str) -> int:
        return self._insert("callbacks", {"name": name, "phone": phone, "preferred_time": preferred_time})

    def add_ticket(self, issue: str, order_id: str | None, contact: str | None) -> int:
        return self._insert("tickets", {"issue": issue, "order_id": order_id, "contact": contact})

    def log_turn(self, **fields) -> int:
        for key in ("tool_calls", "extra"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        return self._insert("turns", fields)

    def recent_turns(self, limit: int = 20) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
