"""Where identity is closed (PLAN.md §4.3).

`README.md` §Trust boundary item 1 says the layer believes a `Principal` when it says it is human,
and that whoever can construct one can approve their own proposal. This module is the deployed
answer to that: **it is the only place in the served application where a HUMAN principal is
constructed**. Enterprise mode requires a short-lived login obtained from a verified OIDC
subject and an enabled workspace membership. Legacy demo/pilot access uses hashed bearer grants.
`tests/test_api.py::test_only_auth_mints_humans` enforces the constructor boundary.

The host, its identity-provider configuration, database and active browser session remain trusted.
A stolen session can act until expiry or local revocation; matching an assurance claim depends on
correct administration of the customer's actual MFA policy. Model input cannot mint a principal.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
import re
import threading
from contextlib import contextmanager, nullcontext
from pathlib import Path

from policy import AGENT, HUMAN, Principal
from api.storage import Database

# the agent, once, for the whole process: no request can choose it
AGENT_PRINCIPAL = Principal("assistant", AGENT)
TOKEN_BYTES = 32
SESSION_ID_BYTES = 8


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class _Result(list):
    def __init__(self, rows, rowcount):
        super().__init__(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self[0] if self else None


class Sessions:
    """The session table: id → the sha256 of the token that owns it.

    On Postgres when a DSN is given, because this table is the ACCESS to everything else: a
    deployment whose records survive a spin-down but whose session table does not has handed the
    visitor a link to data they can no longer open. SQLite otherwise, which is the local shape."""

    def __init__(self, path: str | Path = ":memory:", dsn: str | None = None):
        self.path, self.dsn = str(path), dsn
        self.db = Database(path, dsn)
        self.ttl = 30 * 86400
        self.oidc_only = False
        self.identity = None
        self._lock = threading.RLock()
        if dsn:
            import psycopg
            self._raw = psycopg.connect(dsn, autocommit=True, connect_timeout=20)
            self._pg = True
        else:
            self._raw = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
            self._pg = False
        migration = nullcontext(self._exec) if self.path == ":memory:" and not dsn else self.db.transaction("session_schema")
        with migration as execute:
            execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, "
                    "created_at DOUBLE PRECISION NOT NULL)")
            execute("CREATE TABLE IF NOT EXISTS access_tokens (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
                    "token_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL, role TEXT NOT NULL, "
                    "expires_at DOUBLE PRECISION NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)")
            execute("CREATE INDEX IF NOT EXISTS access_tokens_session ON access_tokens(session_id)")
            execute("CREATE TABLE IF NOT EXISTS deleted_sessions (id TEXT PRIMARY KEY)")
        if self.path != ":memory:" or dsn:
            from api.identity import IdentityStore
            self.identity = IdentityStore(self.db)

    def _exec(self, sql: str, params: tuple = ()):
        with self._lock:
            if self._pg and self._raw.closed:
                import psycopg
                self._raw = psycopg.connect(self.dsn, autocommit=True, connect_timeout=10)
            cursor = self._raw.execute(sql.replace("?", "%s") if self._pg else sql, params)
            return _Result(cursor.fetchall() if cursor.description else [], cursor.rowcount)

    def create(self, clock=time.time, max_sessions: int | None = None) -> tuple[str, str]:
        """A new session. The token is returned ONCE and never stored in the clear."""
        sid, token = secrets.token_hex(SESSION_ID_BYTES), secrets.token_urlsafe(TOKEN_BYTES)
        # The capacity check and insert share a database lock across all workers.
        if self.path == ":memory:" and not self.dsn:
            self._exec("INSERT INTO sessions VALUES (?,?,?)", (sid, _hash(token), clock()))
        else:
            with self.db.transaction("sessions") as execute:
                if max_sessions is not None and execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= max_sessions:
                    raise OverflowError("session capacity reached; the operator must purge expired sessions")
                execute("INSERT INTO sessions VALUES (?,?,?)", (sid, _hash(token), clock()))
        return sid, token

    def exists(self, session_id: str) -> bool:
        return self._exec("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is not None

    def ids(self) -> list[str]:
        return [r[0] for r in self._exec("SELECT id FROM sessions ORDER BY created_at")]

    # ── the only mint in the served application ──────────────────────────────────────────────
    def principal(self, session_id: str, token: str | None) -> Principal | None:
        """The session's human, or None. Nothing else in `api/` may build one of these."""
        access = self.access(session_id, token)
        if access is None:
            return None
        return Principal(access["identity"], HUMAN)

    def access(self, sid: str, token: str | None, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        if not re.fullmatch(r"[a-f0-9]{16}", sid) or not token or len(token) > 256:
            return None
        row = self._exec("SELECT token_hash, created_at FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row is None or row[1] + self.ttl <= now:
            return None
        if self._exec("SELECT 1 FROM deleted_sessions WHERE id = ?", (sid,)).fetchone():
            return None
        if self.identity:
            access = self.identity.access(sid, token)
            if access:
                access["expires_at"] = min(access["expires_at"], row[1] + self.ttl)
                return access
        if self.oidc_only:
            return None
        digest = _hash(token)
        if secrets.compare_digest(row[0], digest):
            return {"id": "owner", "identity": sid, "role": "owner", "expires_at": row[1] + self.ttl}
        grant = self._exec("SELECT id, role, expires_at FROM access_tokens WHERE session_id = ? "
                           "AND token_hash = ? AND revoked = 0", (sid, digest)).fetchone()
        if grant and grant[2] > now:
            return {"id": grant[0], "identity": f"{sid}:{grant[0]}", "role": grant[1],
                    "expires_at": min(grant[2], row[1] + self.ttl)}
        return None

    def grant(self, sid: str, role: str, label: str, days: int) -> dict:
        if role not in {"reviewer", "viewer"} or not 1 <= days <= 30:
            raise ValueError("invalid role or expiry")
        token, key = secrets.token_urlsafe(TOKEN_BYTES), secrets.token_hex(16)
        expires = time.time() + days * 86400
        with self.db.transaction("sessions") as execute:
            count = execute("SELECT COUNT(*) FROM access_tokens WHERE session_id = ? AND revoked = 0",
                            (sid,)).fetchone()[0]
            if count >= 20:
                raise OverflowError("revoke an access token before creating another")
            execute("INSERT INTO access_tokens VALUES (?,?,?,?,?,?,0)",
                    (key, sid, _hash(token), label, role, expires))
        return {"id": key, "token": token, "role": role, "label": label, "expires_at": expires}

    def grants(self, sid: str) -> list[dict]:
        return [dict(zip(("id", "label", "role", "expires_at", "revoked"), r)) for r in self._exec(
            "SELECT id, label, role, expires_at, revoked FROM access_tokens WHERE session_id = ?", (sid,))]

    def revoke(self, sid: str, key: str) -> bool:
        return self._exec("UPDATE access_tokens SET revoked = 1 WHERE session_id = ? AND id = ?",
                          (sid, key)).rowcount > 0

    def rotate(self, sid: str) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._exec("UPDATE sessions SET token_hash = ? WHERE id = ?", (_hash(token), sid))
        return token

    def mark_deleted(self, sid: str) -> None:
        self._exec("INSERT INTO deleted_sessions VALUES (?) ON CONFLICT (id) DO NOTHING", (sid,))

    def forget(self, sid: str) -> None:
        with self.db.transaction("sessions") as execute:
            execute("DELETE FROM access_tokens WHERE session_id = ?", (sid,))
            execute("DELETE FROM sessions WHERE id = ?", (sid,))
            execute("DELETE FROM deleted_sessions WHERE id = ?", (sid,))
            execute("DELETE FROM workforce_members WHERE workspace = ?", (sid,))
            execute("DELETE FROM identity_events WHERE workspace = ?", (sid,))
            table = (execute("SELECT to_regclass('accounting_connections')").fetchone()[0] if self.dsn else
                     execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounting_connections'").fetchone())
            if table:
                execute("DELETE FROM accounting_connections WHERE workspace = ?", (sid,))
            import json
            for state_hash, payload in execute("SELECT state_hash,payload FROM oauth_attempts").fetchall():
                if json.loads(payload).get("workspace") == sid:
                    execute("DELETE FROM oauth_attempts WHERE state_hash = ?", (state_hash,))

    def expired(self) -> list[str]:
        return [r[0] for r in self._exec("SELECT id FROM sessions WHERE created_at <= ? "
                                        "OR id IN (SELECT id FROM deleted_sessions)", (time.time() - self.ttl,))]

    @contextmanager
    def lock(self, sid: str):
        """Exclusive session operation, including auth recheck and deletion, across workers.

        Busy requests fail before side effects. They can safely retry. Postgres uses a dedicated
        session advisory lock (not a transaction held open during a model request).
        """
        if not re.fullmatch(r"[a-f0-9]{16}", sid):
            raise ValueError("invalid session id")
        if self.dsn:
            import psycopg
            with psycopg.connect(self.dsn, autocommit=True, connect_timeout=10) as conn:
                key = int.from_bytes(hashlib.sha256(("session:" + sid).encode()).digest()[:8], "big", signed=True)
                if not conn.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]:
                    raise BlockingIOError("session busy")
                try:
                    yield
                finally:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
        else:
            import fcntl
            directory = Path(self.path).parent / "locks"
            directory.mkdir(exist_ok=True)
            with (directory / f"{sid}.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    yield
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
