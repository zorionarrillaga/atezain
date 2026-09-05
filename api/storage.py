"""Short, atomic control-plane transactions on either supported database."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: str | Path, dsn: str | None = None):
        self.path, self.dsn = str(path), dsn

    @contextmanager
    def transaction(self, namespace: str = "control"):
        if self.dsn:
            import psycopg
            with psycopg.connect(self.dsn, connect_timeout=10) as conn:
                conn.execute("SET LOCAL lock_timeout = '5s'")
                key = int.from_bytes(hashlib.sha256(namespace.encode()).digest()[:8], "big", signed=True)
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
                yield lambda sql, args=(): conn.execute(sql.replace("?", "%s"), args)
        else:
            conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn.execute
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
