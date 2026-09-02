"""Where identity is closed (PLAN.md §4.3).

`README.md` §Trust boundary item 1 says the layer believes a `Principal` when it says it is human,
and that whoever can construct one can approve their own proposal. This module is the deployed
answer to that: **it is the only place in the served application where a HUMAN principal is
constructed**, and it constructs one only from a session token that hashes to a row in the session
table. `tests/test_api.py::test_only_auth_mints_humans` greps the whole `api/` package and fails if
the word appears anywhere else.

The agent's principal is a module constant. Nothing per-request mints it, so no request body can
choose who the agent is.

What this still does not close: the token is a bearer token. Whoever holds it is the session's
human, exactly as whoever holds the shell is the human in `bin/atezain_cli.py`. It is issued once,
over the connection that asked for it, and never returned again — the table keeps its sha256.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

from policy import AGENT, HUMAN, Principal

# the agent, once, for the whole process: no request can choose it
AGENT_PRINCIPAL = Principal("assistant", AGENT)
TOKEN_BYTES = 32
SESSION_ID_BYTES = 8


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Sessions:
    """The session table: id → the sha256 of the token that owns it."""

    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created_at REAL NOT NULL)")

    def create(self, clock=time.time) -> tuple[str, str]:
        """A new session. The token is returned ONCE and never stored in the clear."""
        sid, token = secrets.token_hex(SESSION_ID_BYTES), secrets.token_urlsafe(TOKEN_BYTES)
        self.conn.execute("INSERT INTO sessions (id, token_hash, created_at) VALUES (?,?,?)",
                          (sid, _hash(token), clock()))
        return sid, token

    def exists(self, session_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is not None

    def ids(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT id FROM sessions ORDER BY created_at")]

    # ── the only mint in the served application ──────────────────────────────────────────────
    def principal(self, session_id: str, token: str | None) -> Principal | None:
        """The session's human, or None. Nothing else in `api/` may build one of these."""
        row = self.conn.execute("SELECT token_hash FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None or not token:
            return None
        if not secrets.compare_digest(row[0], _hash(token)):
            return None
        return Principal(session_id, HUMAN)
