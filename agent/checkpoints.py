"""Where a held graph waits (PLAN.md §4.2).

The proposals themselves live in the policy store — the checkpointer holds the GRAPH: the thread
that ran `retrieve → think → propose` and stopped at the hold. That is what makes a second `assist`
for the same record free: the thread is already there, with the model's answer in it, so the model
is not asked twice and no second set of proposals appears in the queue.

Render's free web service has no persistent disk and spins down when idle, so an in-memory saver
would lose every one of those threads. Deployed, this is Postgres (the same Neon database as the
policy store); locally it is a SQLite file; in tests it is memory.
"""
from __future__ import annotations

import sqlite3
import hashlib
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver


def make_checkpointer(dsn: str | None = None, path: str | Path | None = None):
    """Postgres if a DSN is given, else SQLite if a path is given, else memory."""
    if dsn:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as e:      # noqa: TRY003 — say exactly what is missing and why
            raise RuntimeError(
                "a DSN was given but langgraph-checkpoint-postgres is not installed; "
                "install it or run without ATEZAIN_DSN (PLAN.md §4.2)") from e
        import psycopg
        # NOT `from_conn_string(...).__enter__()`: that returns the saver out of a context manager
        # nobody holds, and the manager closes the connection the moment it is collected — which
        # showed up as "the connection is closed" inside setup(). Own the connection instead.
        conn = psycopg.connect(dsn, autocommit=True, connect_timeout=20)
        saver = PostgresSaver(conn)
        # Concurrent index migrations wait for older transaction snapshots. A transaction on
        # another connection held around setup would deadlock. Lock this autocommit session.
        key = int.from_bytes(hashlib.sha256(b"atezain:checkpoint_schema").digest()[:8], "big", signed=True)
        try:
            conn.execute("SET lock_timeout = '5s'")
            conn.execute("SET statement_timeout = '30s'")
            conn.execute("SELECT pg_advisory_lock(%s)", (key,))
            try:
                saver.setup()
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
        except BaseException:
            conn.close()
            raise
        return saver
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        saver = SqliteSaver(conn)
        try:
            saver.setup()
        except BaseException:
            conn.close()
            raise
        return saver
    return InMemorySaver()
