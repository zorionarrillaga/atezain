"""The customer's records: invoices with their notes and emails. SQLite locally; the same interface
over Postgres when deployed.

Reads are open. Writes exist only as `_apply_*` methods that ONE module may call: `agent/executor.py`,
and only from inside `PolicyService.execute`. `tests/test_agent.py::test_no_write_path_bypasses_policy`
greps for it. The records are also the ATTACK SURFACE: an injection is a note or an email that the
assistant reads.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any


class Records:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.execute("CREATE TABLE IF NOT EXISTS invoices (id TEXT PRIMARY KEY, customer TEXT, amount REAL, currency TEXT, issued TEXT, due TEXT, status TEXT, reminder_text TEXT, reminder_channel TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS notes (seq INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT, ts TEXT, author TEXT, text TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS emails (seq INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT, ts TEXT, direction TEXT, sender TEXT, subject TEXT, body TEXT)")

    # ── loading ──────────────────────────────────────────────────────────────────────────────
    def load_seed(self, path: str | Path) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for inv in data["invoices"]:
            self.conn.execute("INSERT OR REPLACE INTO invoices (id, customer, amount, currency, issued, due, status, reminder_text, reminder_channel) VALUES (?,?,?,?,?,?,?,?,?)",
                              (inv["id"], inv["customer"], inv["amount"], inv.get("currency", "EUR"), inv["issued"], inv["due"], inv.get("status", "open"), None, None))
            for n in inv.get("notes", []):
                self.add_note_raw(inv["id"], n["ts"], n.get("author", "staff"), n["text"])
            for e in inv.get("emails", []):
                self.conn.execute("INSERT INTO emails (invoice_id, ts, direction, sender, subject, body) VALUES (?,?,?,?,?,?)",
                                  (inv["id"], e["ts"], e.get("direction", "in"), e.get("sender", ""), e.get("subject", ""), e["body"]))
        return len(data["invoices"])

    def add_note_raw(self, invoice_id: str, ts: str, author: str, text: str) -> None:
        """Seeding / planting only — NOT the agent's write path (that is `_apply_add_note` via policy)."""
        self.conn.execute("INSERT INTO notes (invoice_id, ts, author, text) VALUES (?,?,?,?)", (invoice_id, ts, author, text))

    def plant_email(self, invoice_id: str, sender: str, subject: str, body: str, ts: str = "2026-09-01") -> None:
        """The red-team's verb: an email arrives carrying an injection."""
        self.conn.execute("INSERT INTO emails (invoice_id, ts, direction, sender, subject, body) VALUES (?,?,?,?,?,?)",
                          (invoice_id, ts, "in", sender, subject, body))

    # ── reads ────────────────────────────────────────────────────────────────────────────────
    def invoice(self, invoice_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT id, customer, amount, currency, issued, due, status, reminder_text, reminder_channel FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not row:
            return None
        keys = ("id", "customer", "amount", "currency", "issued", "due", "status", "reminder_text", "reminder_channel")
        inv = dict(zip(keys, row))
        inv["notes"] = [dict(zip(("ts", "author", "text"), r)) for r in self.conn.execute("SELECT ts, author, text FROM notes WHERE invoice_id = ? ORDER BY seq", (invoice_id,))]
        inv["emails"] = [dict(zip(("ts", "direction", "sender", "subject", "body"), r)) for r in self.conn.execute("SELECT ts, direction, sender, subject, body FROM emails WHERE invoice_id = ? ORDER BY seq", (invoice_id,))]
        return inv

    def ids(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT id FROM invoices ORDER BY id")]

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Local retriever: keyword overlap over notes and emails. The deployed retriever is pgvector
        over the same rows (records/pgvector.py, Step 4); the interface is identical."""
        terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 2}
        hits: list[tuple[int, dict]] = []
        for tbl, col in (("notes", "text"), ("emails", "body")):
            for inv_id, txt in self.conn.execute(f"SELECT invoice_id, {col} FROM {tbl}"):
                score = sum(1 for t in terms if t in txt.lower())
                if score:
                    hits.append((score, {"invoice_id": inv_id, "source": tbl, "text": txt}))
        hits.sort(key=lambda h: -h[0])
        return [h[1] for h in hits[:k]]

    # ── writes: reachable only through the policy executor ──────────────────────────────────
    def _apply_update_status(self, invoice_id: str, status: str) -> dict:
        self.conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))
        return {"status": status}

    def _apply_add_note(self, invoice_id: str, note: str) -> dict:
        self.add_note_raw(invoice_id, "now", "assistant", note)
        return {"note": note}

    def _apply_send_reminder(self, invoice_id: str, reminder_text: str, reminder_channel: str) -> dict:
        # Sending is simulated: the record carries what was sent and where. A real channel plugs in here.
        self.conn.execute("UPDATE invoices SET reminder_text = ?, reminder_channel = ? WHERE id = ?", (reminder_text, reminder_channel, invoice_id))
        return {"reminder_text": reminder_text, "reminder_channel": reminder_channel}
