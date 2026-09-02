"""SQLite-backed store for proposals, executions, the fuse and an APPEND-ONLY audit log.

The audit log is a hash chain: every row carries sha256(previous_hash + canonical row). There is no
method on this class that updates or deletes an audit row, on purpose; `audit_verify()` recomputes
the chain and returns False if any row was altered after the fact.

A Postgres implementation with the same methods replaces this for deployment (the LangGraph
checkpointer and pgvector need Postgres anyway); the policy service never touches SQL directly.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from .model import Proposal

GENESIS = "0" * 64


class StoreUnreachable(RuntimeError):
    pass


class Store:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._conn: sqlite3.Connection | None = sqlite3.connect(path, isolation_level=None)
        self._init()

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────
    def _init(self) -> None:
        c = self._c()
        c.execute("CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, created_at REAL, status TEXT, action TEXT, body TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS executions (proposal_id TEXT PRIMARY KEY, ts REAL, effect TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, principal TEXT, proposal_id TEXT, detail TEXT, prev_hash TEXT, hash TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS fuse (id INTEGER PRIMARY KEY CHECK (id = 1), tripped INTEGER, reason TEXT, tripped_at REAL, tripped_by TEXT, cleared_at REAL, cleared_by TEXT)")
        c.execute("INSERT OR IGNORE INTO fuse (id, tripped) VALUES (1, 0)")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _c(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreUnreachable("store is closed")
        return self._conn

    def ping(self) -> None:
        """Raise if the store cannot be reached. The policy service calls this before deciding."""
        self._c().execute("SELECT 1").fetchone()

    # ── proposals ────────────────────────────────────────────────────────────────────────────
    def put_proposal(self, p: Proposal) -> None:
        self._c().execute(
            "INSERT OR REPLACE INTO proposals (id, created_at, status, action, body) VALUES (?, ?, ?, ?, ?)",
            (p.id, p.created_at, p.status, p.action, p.to_json()),
        )

    def get_proposal(self, pid: str) -> Proposal | None:
        row = self._c().execute("SELECT body FROM proposals WHERE id = ?", (pid,)).fetchone()
        return Proposal.from_json(row[0]) if row else None

    def count_proposals_since(self, ts: float, statuses: tuple[str, ...], action: str | None = None) -> int:
        q = "SELECT COUNT(*) FROM proposals WHERE created_at >= ? AND status IN (%s)" % ",".join("?" * len(statuses))
        args: list[Any] = [ts, *statuses]
        if action is not None:
            q += " AND action = ?"
            args.append(action)
        return int(self._c().execute(q, args).fetchone()[0])

    # ── executions: exactly-once by construction (PRIMARY KEY) ───────────────────────────────
    def claim_execution(self, pid: str, ts: float) -> bool:
        """Return True if this call claimed the single execution slot for `pid`."""
        try:
            self._c().execute("INSERT INTO executions (proposal_id, ts, effect) VALUES (?, ?, ?)", (pid, ts, None))
            return True
        except sqlite3.IntegrityError:
            return False

    def record_effect(self, pid: str, effect: dict) -> None:
        self._c().execute("UPDATE executions SET effect = ? WHERE proposal_id = ?", (json.dumps(effect, sort_keys=True), pid))

    # ── fuse ─────────────────────────────────────────────────────────────────────────────────
    def fuse_get(self) -> dict:
        row = self._c().execute("SELECT tripped, reason, tripped_at, tripped_by, cleared_at, cleared_by FROM fuse WHERE id = 1").fetchone()
        keys = ("tripped", "reason", "tripped_at", "tripped_by", "cleared_at", "cleared_by")
        return dict(zip(keys, row))

    def fuse_set(self, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._c().execute(f"UPDATE fuse SET {cols} WHERE id = 1", tuple(fields.values()))

    # ── audit: append-only hash chain ────────────────────────────────────────────────────────
    def audit_append(self, kind: str, principal: str, proposal_id: str | None, detail: dict, ts: float | None = None) -> str:
        c = self._c()
        prev = c.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = prev[0] if prev else GENESIS
        ts = time.time() if ts is None else ts
        detail_s = json.dumps(detail, sort_keys=True)
        h = self._hash(prev_hash, ts, kind, principal, proposal_id, detail_s)
        c.execute(
            "INSERT INTO audit (ts, kind, principal, proposal_id, detail, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, kind, principal, proposal_id, detail_s, prev_hash, h),
        )
        return h

    def audit_rows(self) -> list[dict]:
        rows = self._c().execute("SELECT seq, ts, kind, principal, proposal_id, detail, prev_hash, hash FROM audit ORDER BY seq").fetchall()
        keys = ("seq", "ts", "kind", "principal", "proposal_id", "detail", "prev_hash", "hash")
        return [dict(zip(keys, r)) for r in rows]

    def audit_verify(self) -> bool:
        prev = GENESIS
        for r in self.audit_rows():
            if r["prev_hash"] != prev:
                return False
            if self._hash(prev, r["ts"], r["kind"], r["principal"], r["proposal_id"], r["detail"]) != r["hash"]:
                return False
            prev = r["hash"]
        return True

    def audit_orphans(self) -> list[str]:
        """Proposal ids that EXECUTED without a DECISION row and were not auto-approved — the
        signature of an approval forged with direct store access. Detectable after the fact; the
        chain cannot prevent root, it can only refuse to hide it."""
        rows = self.audit_rows()
        decided = {r["proposal_id"] for r in rows if r["kind"] == "DECISION"}
        auto = {r["proposal_id"] for r in rows if r["kind"] == "PROPOSAL" and json.loads(r["detail"]).get("status") == "approved"}
        return [r["proposal_id"] for r in rows if r["kind"] == "EXECUTED" and r["proposal_id"] not in decided and r["proposal_id"] not in auto]

    @staticmethod
    def _hash(prev_hash: str, ts: float, kind: str, principal: str, proposal_id: str | None, detail_s: str) -> str:
        payload = json.dumps([prev_hash, ts, kind, principal, proposal_id, detail_s], sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
