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
        saver = PostgresSaver.from_conn_string(dsn).__enter__()
        saver.setup()
        return saver
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        saver = SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))
        saver.setup()
        return saver
    return InMemorySaver()
