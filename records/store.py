"""The customer's records: invoices with their notes and emails. SQLite locally; the same interface
over Postgres when deployed.

Reads are open. Writes exist only as `_apply_*` methods that ONE module may call: `agent/executor.py`,
and only from inside `PolicyService.execute`. `tests/test_agent.py::test_no_write_path_bypasses_policy`
greps for it. The records are also the ATTACK SURFACE: an injection is a note or an email that the
assistant reads.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable
from records.accounting import AccountingRecords, canonical


class _Rows(list):
    """What `execute` hands back: the rows, already read, so nothing is fetched off a shared cursor
    after the lock is gone. `fetchone` is kept because the callers here read that way."""

    def fetchone(self):
        return self[0] if self else None

    def fetchall(self):
        return list(self)


class _Serialised:
    """One connection, one lock, statement and fetch inside it.

    `policy/store.py` has had an `RLock` since the budget race. This file did not, and its comment
    claimed it did not need one — *"SQLite serialises writers itself … and a session is one
    visitor"*. The round-2 seat (2026-09-03, D3) showed that claim failing the moment step 6's fold
    made `assist` a write path: two concurrent assists on one record returned a raw
    `sqlite3.InterfaceError` off the shared connection and left a note in the record that the queue
    and the chain did not claim as executed. A visitor double-clicking is enough — `api/demo.html`
    does not disable its button. Neither "database is locked" nor safe, so: a lock, like its sibling.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn, self._lock = conn, threading.RLock()

    def execute(self, sql: str, params=()) -> _Rows:
        with self._lock:
            return _Rows(self._conn.execute(sql, params).fetchall())

    def __getattr__(self, name):
        return getattr(self._conn, name)


class Records(AccountingRecords):
    def __init__(self, path: str = ":memory:", clock: Callable[[], float] = time.time):
        # check_same_thread=False for the same reason `policy/store.py` does it: a served
        # request runs in whatever worker thread the server hands it, and the connection
        # outlives the thread that opened it. What SQLite's own writer serialisation does NOT
        # give is safety for two threads driving ONE connection — see `_Serialised`, which is
        # what this attribute is — so a session being one visitor is not the guarantee this
        # relied on before 2026-09-03.
        self.clock = clock
        self.conn = _Serialised(sqlite3.connect(path, isolation_level=None, check_same_thread=False))
        self.conn.execute("CREATE TABLE IF NOT EXISTS invoices (id TEXT PRIMARY KEY, customer TEXT, amount REAL, currency TEXT, issued TEXT, due TEXT, status TEXT, reminder_text TEXT, reminder_channel TEXT, contact TEXT, reminder_to TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS notes (seq INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT, ts TEXT, author TEXT, text TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS emails (seq INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT, ts TEXT, direction TEXT, sender TEXT, subject TEXT, body TEXT)")
        self._ensure_columns()
        self.init_accounting()

    # ── columns added after a database already existed ───────────────────────────────────────
    def _ensure_columns(self) -> None:
        """`contact` and `reminder_to` arrived on 2026-09-03 (client simulation 1). A session's
        database is created once and outlives the code, so opening an older one adds the columns
        rather than reading a table that has not got them."""
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(invoices)")}
        for col in ("contact", "reminder_to"):
            if col not in have:
                self.conn.execute(f"ALTER TABLE invoices ADD COLUMN {col} TEXT")

    def close(self):
        self.conn.close()

    @contextmanager
    def transaction(self):
        with self.conn._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    # ── loading ──────────────────────────────────────────────────────────────────────────────
    def load_seed(self, path: str | Path) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for inv in data["invoices"]:
            self.load_seed_row(inv)
            for n in inv.get("notes", []):
                self.add_note_raw(inv["id"], n["ts"], n.get("author", "staff"), n["text"])
            for e in inv.get("emails", []):
                self.conn.execute("INSERT INTO emails (invoice_id, ts, direction, sender, subject, body) VALUES (?,?,?,?,?,?)",
                                  (inv["id"], e["ts"], e.get("direction", "in"), e.get("sender", ""), e.get("subject", ""), e["body"]))
        return len(data["invoices"])

    def load_seed_row(self, inv: dict) -> None:
        """One invoice, from a seed file or from a visitor's upload (`api/app.py`). Seeding only —
        this is how a record gets INTO the store, not how the agent changes one. `contact` is the
        customer's address of record: it enters HERE, from the customer's own system, and no
        action in any adapter declares it in `writes`, so nothing the assistant proposes can move
        it — which is what makes it worth checking a reminder's address against."""
        self.conn.execute("INSERT OR REPLACE INTO invoices (id, customer, amount, currency, issued, due, status, reminder_text, reminder_channel, contact, reminder_to) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (inv["id"], inv["customer"], inv["amount"], inv.get("currency", "EUR"), inv["issued"], inv["due"], inv.get("status", "open"), None, None, inv.get("contact") or None, None))

    def add_note_raw(self, invoice_id: str, ts: str, author: str, text: str) -> None:
        """Seeding / planting only — NOT the agent's write path (that is `_apply_add_note` via policy)."""
        self.conn.execute("INSERT INTO notes (invoice_id, ts, author, text) VALUES (?,?,?,?)", (invoice_id, ts, author, text))

    def set_field_raw(self, invoice_id: str, field: str, value: str) -> None:
        """Planting only (red-team `field_value` class) — NOT a write path for the agent. `contact`
        is here because a test needs to load one; in the product it arrives with the upload, and no
        action declares it in `writes`."""
        assert field in ("customer", "currency", "issued", "due", "contact"), field
        self.conn.execute(f"UPDATE invoices SET {field} = ? WHERE id = ?", (value, invoice_id))

    def plant_email(self, invoice_id: str, sender: str, subject: str, body: str, ts: str = "2026-09-01") -> None:
        """The red-team's verb: an email arrives carrying an injection."""
        self.conn.execute("INSERT INTO emails (invoice_id, ts, direction, sender, subject, body) VALUES (?,?,?,?,?,?)",
                          (invoice_id, ts, "in", sender, subject, body))

    # ── reads ────────────────────────────────────────────────────────────────────────────────
    #: what a record has always carried, and what this method has always returned
    FIELDS = ("id", "customer", "amount", "currency", "issued", "due", "status", "reminder_text", "reminder_channel")
    #: added later, and present in the dict only when the record actually carries them — see `invoice`
    LATER = ("contact", "reminder_to")

    def invoice(self, invoice_id: str) -> dict[str, Any] | None:
        """The record as the model is shown it (`agent/graph.py::retrieve`), so what this method
        returns IS the model's context. A field added here is a change to what every cached
        red-team answer was produced from, and `redteam/run.py::case_hash` cannot see this file —
        so `contact` and `reminder_to` appear only when the record carries them, which leaves a
        record that carries neither exactly as it was when the hundred were run
        (`tests/test_retrieval.py::test_the_model_is_shown_the_fields_the_hundred_were_run_with`)."""
        row = self.conn.execute("SELECT id, customer, amount, currency, issued, due, status, reminder_text, reminder_channel, contact, reminder_to FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not row:
            return None
        inv = dict(zip(self.FIELDS, row))
        for k, v in zip(self.LATER, row[len(self.FIELDS):]):
            if v not in (None, ""):
                inv[k] = v
        inv["notes"] = [dict(zip(("ts", "author", "text"), r)) for r in self.conn.execute("SELECT ts, author, text FROM notes WHERE invoice_id = ? ORDER BY seq", (invoice_id,))]
        inv["emails"] = [dict(zip(("ts", "direction", "sender", "subject", "body"), r)) for r in self.conn.execute("SELECT ts, direction, sender, subject, body FROM emails WHERE invoice_id = ? ORDER BY seq", (invoice_id,))]
        plan = self.case(invoice_id)
        if plan["version"]:
            from records.accounting import record_version
            inv["collection_case"] = plan
            self.overlay_source(inv, self.source(invoice_id))
            inv["record_version"] = record_version(inv)
        return self.overlay_source(inv, self.source(invoice_id))

    def ids(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT id FROM invoices ORDER BY id")]

    def summaries(self) -> list[dict]:
        """One query for the work list, without loading every email and note body."""
        rows = self.conn.execute("SELECT i.id, i.customer, i.amount, i.currency, i.issued, i.due, i.status, "
            "i.contact, i.reminder_to, i.reminder_channel, "
            "COALESCE(n.total,0), COALESCE(e.total,0), COALESCE(n.assistant,0) FROM invoices i "
            "LEFT JOIN (SELECT invoice_id, COUNT(*) AS total, "
            "SUM(CASE WHEN author = 'assistant' THEN 1 ELSE 0 END) AS assistant FROM notes GROUP BY invoice_id) n "
            "ON i.id = n.invoice_id LEFT JOIN (SELECT invoice_id, COUNT(*) AS total FROM emails GROUP BY invoice_id) e "
            "ON i.id = e.invoice_id ORDER BY i.due, i.id")
        keys = ("id", "customer", "amount", "currency", "issued", "due", "status", "contact", "reminder_to",
                "reminder_channel", "notes", "emails", "assistant_notes")
        sources = self.source_rows()
        return [self.overlay_source(dict(zip(keys, r)), sources.get(r[0])) for r in rows]

    def customer_context(self, invoice_id: str, k: int = 5) -> list[dict]:
        """Related source material is scoped by the uploaded customer identifier, never keywords."""
        snippets = []
        for table, column in (("notes", "text"), ("emails", "body")):
            rows = self.conn.execute(f"SELECT r.invoice_id, r.{column} FROM {table} r "
                "JOIN invoices i ON i.id = r.invoice_id LEFT JOIN source_snapshots s ON s.invoice_id=i.id "
                "WHERE ((s.customer_key IS NOT NULL AND s.customer_key=(SELECT customer_key FROM source_snapshots WHERE invoice_id=?)) "
                "OR (s.customer_key IS NULL AND NOT EXISTS (SELECT 1 FROM source_snapshots WHERE invoice_id=?) "
                "AND i.customer=(SELECT customer FROM invoices WHERE id=?))) AND i.id <> ? ORDER BY r.seq DESC LIMIT ?",
                (invoice_id, invoice_id, invoice_id, invoice_id, k))
            snippets.extend({"invoice_id": rid, "source": table, "text": text} for rid, text in rows)
        return snippets[:k]

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Keyword overlap over notes and emails, on both stores (the Postgres records store inherits
        it). A vector retriever is planned (PLAN.md §4.4) and not built; the interface would be this one."""
        terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 2}
        hits: list[tuple[int, dict]] = []
        for tbl, col in (("notes", "text"), ("emails", "body")):
            for inv_id, txt in self.conn.execute(f"SELECT invoice_id, {col} FROM {tbl}"):
                score = sum(1 for t in terms if t in txt.lower())
                if score:
                    hits.append((score, {"invoice_id": inv_id, "source": tbl, "text": txt}))
        hits.sort(key=lambda h: -h[0])
        return [h[1] for h in hits[:k]]

    # ── observation: what the record looks like, for re-validation ─────────────────────────
    def snapshot(self, invoice_id: str) -> dict | None:
        """The whole record as the policy will compare it: fields, note texts, email count."""
        inv = self.invoice(invoice_id)
        if inv is None:
            return None
        return {**{k: inv[k] for k in self.FIELDS[1:]}, **{k: inv.get(k) for k in self.LATER},
                "notes": [n["text"] for n in inv["notes"]], "emails": len(inv["emails"]),
                "case_json": canonical(self.case(invoice_id))}

    @staticmethod
    def diff(before: dict | None, after: dict | None) -> dict:
        """What actually changed between two snapshots, in the params vocabulary the policy uses:
        a changed field → {field: new}; exactly one note added → {"note": text}; anything else that
        changed → named as itself, so a write of MORE than was approved never equals the approval."""
        if before is None or after is None:
            return {} if before == after else {"record": "missing" if after is None else "created"}
        out: dict = {}
        for k in before:
            if k in ("notes", "emails"):
                continue
            if before[k] != after[k]:
                out[k] = after[k]
        added = after["notes"][len(before["notes"]):] if after["notes"][:len(before["notes"])] == before["notes"] else None
        if added is None:
            out["notes"] = "rewritten"
        elif len(added) == 1:
            out["note"] = added[0]
        elif added:
            out["notes_added"] = len(added)
        if before["emails"] != after["emails"]:
            out["emails_added"] = after["emails"] - before["emails"]
        return out

    # ── writes: reachable only through the policy executor ──────────────────────────────────
    def _apply_update_status(self, invoice_id: str, status: str) -> None:
        self.conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))

    def today(self) -> str:
        """The date this store stamps a write with, from its OWN clock — the same rule as
        `records/drafts.py`: no caller passes a timestamp, so nothing the model says can choose when
        its note was written. ISO, like the seed's own notes; until 2026-09-03 the stamp was the
        literal word `now` and a reader of the record got a note with no date."""
        return dt.datetime.fromtimestamp(self.clock(), tz=dt.timezone.utc).date().isoformat()

    def _apply_add_note(self, invoice_id: str, note: str) -> None:
        if self.invoice(invoice_id) is not None:
            self.add_note_raw(invoice_id, self.today(), "assistant", note)

    def _apply_send_reminder(self, invoice_id: str, reminder_text: str, reminder_channel: str,
                             reminder_to: str | None = None) -> None:
        # Sending is simulated: the record carries what was sent, on what channel and to whom. A
        # real channel plugs in here — and nothing in this repo opens a connection to one, so
        # "executed" on a reminder means the record says it went out, not that anything left.
        if reminder_to is None:
            self.conn.execute("UPDATE invoices SET reminder_text = ?, reminder_channel = ? WHERE id = ?", (reminder_text, reminder_channel, invoice_id))
        else:
            self.conn.execute("UPDATE invoices SET reminder_text = ?, reminder_channel = ?, reminder_to = ? WHERE id = ?", (reminder_text, reminder_channel, reminder_to, invoice_id))

    # The three below exist so that the red-team's CONTROL arm can actually happen: an assistant
    # with no boundary changes an amount, deletes a record and mails a stranger, and the measurement
    # of "executed with the boundary OFF" is worthless if the store cannot carry those writes
    # (PLAN.md §3.3: the control must be able to fail). `agent/executor.py` implements none of them
    # and the adapter denies all three, so no policy path reaches them; `redteam/off.py` does.
    def _apply_update_amount(self, invoice_id: str, amount: float) -> None:
        self.conn.execute("UPDATE invoices SET amount = ? WHERE id = ?", (float(amount), invoice_id))

    def _apply_delete_invoice(self, invoice_id: str) -> None:
        self.conn.execute("DELETE FROM notes WHERE invoice_id = ?", (invoice_id,))
        self.conn.execute("DELETE FROM emails WHERE invoice_id = ?", (invoice_id,))
        self.conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))

    def _apply_send_to_external(self, invoice_id: str, to: str, body: str) -> None:
        # Also simulated, and deliberately visible in the record: the channel says where it went.
        self.conn.execute("UPDATE invoices SET reminder_text = ?, reminder_channel = ? WHERE id = ?",
                          (body, f"external:{to}", invoice_id))
