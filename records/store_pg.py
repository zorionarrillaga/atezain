"""The customer's records over Postgres (PLAN.md §4.3's other half).

Same shape as `policy/store_pg.py`: a SUBCLASS of `Records`, so the observation logic that the
whole re-validation rests on — `snapshot`, `diff`, `search` — exists once and is the same code on
both databases. What is overridden is only where SQLite and Postgres differ:

1. the connection and typed DDL (`BIGSERIAL` for the note and email ordering, `DOUBLE PRECISION`
   for an amount);
2. `self.conn`, which is a thin adapter translating `?` to `%s`, so every inherited statement —
   including the `_apply_*` writes the executor calls — runs unchanged;
3. `load_seed_row`, because `INSERT OR REPLACE` is `ON CONFLICT … DO UPDATE` here.

`schema=` is what gives one visitor's invoices their own namespace: `api/app.py` puts each session
in `s_<session id>`, so two strangers on one deployment cannot see, or collide with, each other.

This file deliberately does NOT import from `policy/`. The record store is the customer's; the
policy is the boundary in front of it, and the dependency runs one way. The five-line placeholder
adapter is duplicated from `policy/store_pg.py` rather than shared, for that reason alone.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any

from .store import Records


class _Placeholders:
    """`?` in, `%s` out — see the note above about why this is not imported from `policy/`.

    It also holds the same lock its SQLite sibling does (`records.store._Serialised`). The round-2
    seat (2026-09-03) demonstrated the unlocked SQLite connection failing under two concurrent
    assists and left the Postgres side explicitly OWED, guessing psycopg's own locking might save
    it. With a DSN this repo can check instead of guessing, and `tests/test_agent.py::
    test_two_assists_on_one_record_at_once_do_not_break_the_store` runs on both stores — so rather
    than exempt the deployed backend from a property the local one has, both keep one discipline:
    one thread inside a statement at a time. Whether psycopg would have survived it anyway is then
    not a thing anyone has to be right about."""

    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.RLock()

    def execute(self, sql: str, params: Any = ()):
        with self._lock:
            return self.conn.execute(sql.replace("?", "%s"), tuple(params))


class PgRecords(Records):
    @contextmanager
    def transaction(self):
        with self.conn._lock, self._raw.transaction():
            yield

    def __init__(self, dsn: str, schema: str | None = None, clock=None):
        import psycopg
        import time

        self.clock = clock or time.time      # `Records.today()` reads it; the store owns the clock
        self.schema = schema
        self._raw = psycopg.connect(dsn, autocommit=True, connect_timeout=20)
        self.conn = _Placeholders(self._raw)
        if schema:
            self._raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            self._raw.execute(f'SET search_path TO "{schema}"')
        self.conn.execute("CREATE TABLE IF NOT EXISTS invoices (id TEXT PRIMARY KEY, customer TEXT, amount DOUBLE PRECISION, currency TEXT, issued TEXT, due TEXT, status TEXT, reminder_text TEXT, reminder_channel TEXT, contact TEXT, reminder_to TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS notes (seq BIGSERIAL PRIMARY KEY, invoice_id TEXT, ts TEXT, author TEXT, text TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS emails (seq BIGSERIAL PRIMARY KEY, invoice_id TEXT, ts TEXT, direction TEXT, sender TEXT, subject TEXT, body TEXT)")
        self._ensure_columns()
        self.init_accounting()

    def _ensure_columns(self) -> None:
        """The deployed shape: every live session already has an `invoices` table, and
        `CREATE TABLE IF NOT EXISTS` does not add a column to one. A visitor who opened a session
        before 2026-09-03 comes back to a table that gains the two columns here.

        It ASKS first, like its SQLite sibling's `PRAGMA`, and not because a read is cheaper:
        `ALTER TABLE … ADD COLUMN IF NOT EXISTS` still takes an ACCESS EXCLUSIVE lock on the table
        when the column is already there, and this runs in the constructor — so a session opened
        while another connection is reading that session's invoices would queue behind it, for a
        statement with nothing to do."""
        have = {r[0] for r in self.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'invoices' "
            "AND table_schema = COALESCE(?, current_schema())", (self.schema,))}
        for col in ("contact", "reminder_to"):
            if col not in have:
                self.conn.execute(f"ALTER TABLE invoices ADD COLUMN IF NOT EXISTS {col} TEXT")

    def load_seed_row(self, inv: dict) -> None:
        self.conn.execute(
            "INSERT INTO invoices (id, customer, amount, currency, issued, due, status, reminder_text, reminder_channel, contact, reminder_to) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET customer = EXCLUDED.customer, "
            "amount = EXCLUDED.amount, currency = EXCLUDED.currency, issued = EXCLUDED.issued, "
            "due = EXCLUDED.due, status = EXCLUDED.status, reminder_text = NULL, reminder_channel = NULL, "
            "contact = EXCLUDED.contact, reminder_to = NULL",
            (inv["id"], inv["customer"], inv["amount"], inv.get("currency", "EUR"), inv["issued"],
             inv["due"], inv.get("status", "open"), None, None, inv.get("contact") or None, None))

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────
    def close(self) -> None:
        if not self._raw.closed:
            self._raw.close()

    def drop_schema(self) -> None:
        if not self.schema or self.schema == "public":
            raise ValueError("refusing to drop the default schema")
        self._raw.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
