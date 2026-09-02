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

from typing import Any

from .store import Records


class _Placeholders:
    """`?` in, `%s` out — see the note above about why this is not imported from `policy/`."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql: str, params: Any = ()):
        return self.conn.execute(sql.replace("?", "%s"), tuple(params))


class PgRecords(Records):
    def __init__(self, dsn: str, schema: str | None = None):
        import psycopg

        self.schema = schema
        self._raw = psycopg.connect(dsn, autocommit=True, connect_timeout=20)
        self.conn = _Placeholders(self._raw)
        if schema:
            self._raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            self._raw.execute(f'SET search_path TO "{schema}"')
        self.conn.execute("CREATE TABLE IF NOT EXISTS invoices (id TEXT PRIMARY KEY, customer TEXT, amount DOUBLE PRECISION, currency TEXT, issued TEXT, due TEXT, status TEXT, reminder_text TEXT, reminder_channel TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS notes (seq BIGSERIAL PRIMARY KEY, invoice_id TEXT, ts TEXT, author TEXT, text TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS emails (seq BIGSERIAL PRIMARY KEY, invoice_id TEXT, ts TEXT, direction TEXT, sender TEXT, subject TEXT, body TEXT)")

    def load_seed_row(self, inv: dict) -> None:
        self.conn.execute(
            "INSERT INTO invoices (id, customer, amount, currency, issued, due, status, reminder_text, reminder_channel) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET customer = EXCLUDED.customer, "
            "amount = EXCLUDED.amount, currency = EXCLUDED.currency, issued = EXCLUDED.issued, "
            "due = EXCLUDED.due, status = EXCLUDED.status, reminder_text = NULL, reminder_channel = NULL",
            (inv["id"], inv["customer"], inv["amount"], inv.get("currency", "EUR"), inv["issued"],
             inv["due"], inv.get("status", "open"), None, None))

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────
    def close(self) -> None:
        if not self._raw.closed:
            self._raw.close()

    def drop_schema(self) -> None:
        if not self.schema or self.schema == "public":
            raise ValueError("refusing to drop the default schema")
        self._raw.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
