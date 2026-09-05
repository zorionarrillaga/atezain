"""The same store over Postgres (PLAN.md §4.1).

It is a SUBCLASS of `Store`, not a copy. Everything that is logic rather than SQL —
`audit_verify`, `audit_anomalies`, `audit_orphans`, `_hash`, the fuse's one-transaction rule —
is inherited, so every `# CHECK:` block in this layer still exists exactly once and `make mutate`
still deletes it exactly once. What is overridden is the six places where SQLite and Postgres
actually differ:

1. the connection and the DDL (Postgres wants types; SQLite does not);
2. `_c()`, which translates `?` placeholders to `%s` — so every inherited SQL string, and every
   raw statement a test writes, works unchanged on both;
3. `transaction()`, whose lock is a row lock: `SELECT … FROM audit_head … FOR UPDATE`. On SQLite
   the lock is `BEGIN IMMEDIATE` plus a process lock, which binds one process; this one binds every
   connection to the database, which is what a deployment with more than one web instance needs;
4. nesting, which here is a REAL savepoint (psycopg's `transaction()`), closing the gap an outside
   seat recorded on the SQLite store: an inner failure that is caught no longer leaves inner writes
   inside the outer transaction;
5. `claim_execution`, because a unique violation aborts a Postgres transaction unless it happens
   inside a savepoint — exactly-once is still one `INSERT` against a primary key;
6. `put_proposal`, because `INSERT OR REPLACE` is `ON CONFLICT … DO UPDATE` here.

`schema=` puts one caller's tables in their own schema. That is how a test isolates itself, and it
is the shape `api/app.py` would use to give a session its own namespace (PLAN.md §4.3).
"""
from __future__ import annotations

import contextlib
import threading
from typing import Any, Iterator

from .model import Proposal
from .store import GENESIS, Store


class _Placeholders:
    """`?` in, `%s` out. One place, so the inherited SQL never has to know which database it is on."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql: str, params: Any = ()):
        return self.conn.execute(sql.replace("?", "%s"), tuple(params))


class PgStore(Store):
    def __init__(self, dsn: str, schema: str | None = None):
        import psycopg                                   # optional dependency: only the deployed path needs it

        self.path = "postgres"                           # never the DSN: it carries a password
        self.schema = schema
        self._psycopg = psycopg
        self._conn = psycopg.connect(dsn, autocommit=True, connect_timeout=20)
        self._lock = threading.RLock()
        self._depth = threading.local()
        if schema:
            self._conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            self._conn.execute(f'SET search_path TO "{schema}"')
        self._init()

    # ── the six differences ──────────────────────────────────────────────────────────────────
    def _init(self) -> None:
        c = self._c()
        c.execute("CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, created_at DOUBLE PRECISION, status TEXT, action TEXT, body TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS proposal_requests (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, proposal_id TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS executions (proposal_id TEXT PRIMARY KEY, ts DOUBLE PRECISION, effect TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS audit (seq BIGINT PRIMARY KEY, ts DOUBLE PRECISION, kind TEXT, principal TEXT, proposal_id TEXT, detail TEXT, prev_hash TEXT, hash TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS audit_head (id INTEGER PRIMARY KEY CHECK (id = 1), seq BIGINT, hash TEXT)")
        c.execute("INSERT INTO audit_head (id, seq, hash) VALUES (1, 0, ?) ON CONFLICT (id) DO NOTHING", (GENESIS,))
        c.execute("CREATE TABLE IF NOT EXISTS fuse (id INTEGER PRIMARY KEY CHECK (id = 1), tripped INTEGER, reason TEXT, tripped_at DOUBLE PRECISION, tripped_by TEXT, cleared_at DOUBLE PRECISION, cleared_by TEXT)")
        c.execute("INSERT INTO fuse (id, tripped) VALUES (1, 0) ON CONFLICT (id) DO NOTHING")

    def _c(self) -> Any:
        from .store import StoreUnreachable
        if self._conn is None or self._conn.closed:
            raise StoreUnreachable("store is closed")
        return _Placeholders(self._conn)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """One `BEGIN` and one row lock on `audit_head`, which every connection contends for.
        Re-entrant, and the inner level is a savepoint: an inner failure that the caller catches
        rolls back only the inner writes."""
        with self._lock:
            depth = getattr(self._depth, "n", 0)
            self._depth.n = depth + 1
            try:
                with self._conn.transaction():
                    if depth == 0:
                        self._c().execute("SELECT seq FROM audit_head WHERE id = 1 FOR UPDATE")
                    yield
            finally:
                self._depth.n = depth

    def claim_execution(self, pid: str, ts: float) -> bool:
        """Still one INSERT against a primary key. The savepoint is so that losing the race does
        not abort the transaction the caller is already inside."""
        try:
            with self._conn.transaction():
                self._c().execute("INSERT INTO executions (proposal_id, ts, effect) VALUES (?, ?, ?)", (pid, ts, None))
            return True
        except self._psycopg.errors.UniqueViolation:
            return False

    def put_proposal(self, p: Proposal) -> None:
        self._c().execute(
            "INSERT INTO proposals (id, created_at, status, action, body) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET created_at = EXCLUDED.created_at, status = EXCLUDED.status, "
            "action = EXCLUDED.action, body = EXCLUDED.body",
            (p.id, p.created_at, p.status, p.action, p.to_json()),
        )

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────
    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()

    def drop_schema(self) -> None:
        """For tests and for a session that is finished. Refuses to drop the default schema."""
        if not self.schema or self.schema == "public":
            raise ValueError("refusing to drop the default schema")
        self._conn.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
