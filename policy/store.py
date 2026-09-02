"""SQLite-backed store for proposals, executions, the fuse and an APPEND-ONLY audit log.

The audit log is a hash chain: row `n` carries sha256(hash of row n-1 + n + the canonical row).
There is no method on this class that updates or deletes an audit row, on purpose. A separate
`audit_head` row, written in the same transaction as every append, records the last seq and hash;
`audit_verify()` recomputes the chain and checks it ends at the head. `audit_head()` is meant to
be published out-of-band (a log line, a mail, a file the agent cannot write): pass it back as
`anchor=` and a chain that was truncated and re-headed after that point fails too.

What the chain proves, and what it does not (README §Trust boundary): anything holding this
object can write proposals, fuse state and audit rows directly. The chain cannot stop that. It
can only make the write show — `audit_anomalies()` lists every shape of it this layer knows.

Every count-then-write the policy service does runs inside `transaction()` (a process lock plus
`BEGIN IMMEDIATE`), so a budget cannot be raced past by concurrent proposals.

A Postgres implementation with the same methods replaces this for deployment (the LangGraph
checkpointer and pgvector need Postgres anyway); the policy service never touches SQL directly.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
import time
from typing import Any, Iterator

from .model import HUMAN, Proposal

GENESIS = "0" * 64
OUTCOMES = ("EXECUTED", "EXECUTION_MISMATCH", "EXECUTION_UNKNOWN")


class StoreUnreachable(RuntimeError):
    pass


class Store:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._conn: sqlite3.Connection | None = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._lock = threading.RLock()
        self._depth = threading.local()
        self._init()

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────
    def _init(self) -> None:
        c = self._c()
        c.execute("CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, created_at REAL, status TEXT, action TEXT, body TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS executions (proposal_id TEXT PRIMARY KEY, ts REAL, effect TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, principal TEXT, proposal_id TEXT, detail TEXT, prev_hash TEXT, hash TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS audit_head (id INTEGER PRIMARY KEY CHECK (id = 1), seq INTEGER, hash TEXT)")
        c.execute("INSERT OR IGNORE INTO audit_head (id, seq, hash) VALUES (1, 0, ?)", (GENESIS,))
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

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Serialise a read-then-write: one process lock, one `BEGIN IMMEDIATE`. Re-entrant — an
        inner call joins the outer transaction."""
        with self._lock:
            depth = getattr(self._depth, "n", 0)
            self._depth.n = depth + 1
            if depth == 0:
                self._c().execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                if depth == 0:
                    self._c().execute("ROLLBACK")
                raise
            else:
                if depth == 0:
                    self._c().execute("COMMIT")
            finally:
                self._depth.n = depth

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

    def record_effect(self, pid: str, effect: Any) -> None:
        """Whatever the executor returned, rendered; a non-JSON effect is recorded by repr, not dropped."""
        self._c().execute("UPDATE executions SET effect = ? WHERE proposal_id = ?",
                          (json.dumps(effect, sort_keys=True, default=repr), pid))

    def execution_claims(self) -> list[str]:
        return [r[0] for r in self._c().execute("SELECT proposal_id FROM executions ORDER BY ts")]

    # ── fuse ─────────────────────────────────────────────────────────────────────────────────
    def fuse_get(self) -> dict:
        row = self._c().execute("SELECT tripped, reason, tripped_at, tripped_by, cleared_at, cleared_by FROM fuse WHERE id = 1").fetchone()
        keys = ("tripped", "reason", "tripped_at", "tripped_by", "cleared_at", "cleared_by")
        return dict(zip(keys, row))

    def fuse_set(self, kind: str, principal: str, detail: dict, ts: float, **fields: Any) -> None:
        """The one way to change fuse state: the new state and its audit row land in one
        transaction, so there is no fuse change without a row that says who and why."""
        with self.transaction():
            cols = ", ".join(f"{k} = ?" for k in fields)
            self._c().execute(f"UPDATE fuse SET {cols} WHERE id = 1", tuple(fields.values()))
            self.audit_append(kind, principal, None, {**detail, "tripped": int(bool(fields.get("tripped", 0)))}, ts=ts)

    # ── audit: append-only hash chain with a head ────────────────────────────────────────────
    def audit_append(self, kind: str, principal: str, proposal_id: str | None, detail: dict, ts: float | None = None) -> str:
        with self.transaction():
            c = self._c()
            prev_seq, prev_hash = c.execute("SELECT seq, hash FROM audit_head WHERE id = 1").fetchone()
            seq = int(prev_seq) + 1
            ts = time.time() if ts is None else ts
            detail_s = json.dumps(detail, sort_keys=True, default=repr)
            h = self._hash(prev_hash, seq, ts, kind, principal, proposal_id, detail_s)
            c.execute(
                "INSERT INTO audit (seq, ts, kind, principal, proposal_id, detail, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (seq, ts, kind, principal, proposal_id, detail_s, prev_hash, h),
            )
            c.execute("UPDATE audit_head SET seq = ?, hash = ? WHERE id = 1", (seq, h))
            return h

    def audit_rows(self) -> list[dict]:
        rows = self._c().execute("SELECT seq, ts, kind, principal, proposal_id, detail, prev_hash, hash FROM audit ORDER BY seq").fetchall()
        keys = ("seq", "ts", "kind", "principal", "proposal_id", "detail", "prev_hash", "hash")
        return [dict(zip(keys, r)) for r in rows]

    def audit_head(self) -> tuple[int, str]:
        """(seq, hash) of the last row. Publish it somewhere the agent cannot write; verify with it."""
        seq, h = self._c().execute("SELECT seq, hash FROM audit_head WHERE id = 1").fetchone()
        return int(seq), h

    def audit_verify(self, anchor: tuple[int, str] | None = None) -> bool:
        """True iff every row hashes to the next, seqs are 1..n with no gap, the chain ends at the
        recorded head, and — if an out-of-band `anchor` (seq, hash) is given — that row is present
        with that hash. Without an anchor, a root that truncates the tail AND rewrites the head is
        not detected; that is why `audit_head()` exists."""
        prev, expected_seq, last = GENESIS, 1, (0, GENESIS)
        anchored = anchor is None
        for r in self.audit_rows():
            # CHECK: audit_seq_contiguous
            if r["seq"] != expected_seq:
                return False
            # ENDCHECK
            # CHECK: audit_link
            if r["prev_hash"] != prev:
                return False
            # ENDCHECK
            # CHECK: audit_row_hash
            if self._hash(prev, r["seq"], r["ts"], r["kind"], r["principal"], r["proposal_id"], r["detail"]) != r["hash"]:
                return False
            # ENDCHECK
            if anchor is not None and r["seq"] == anchor[0]:
                anchored = r["hash"] == anchor[1]
            prev, expected_seq, last = r["hash"], expected_seq + 1, (r["seq"], r["hash"])
        # CHECK: audit_ends_at_head
        if last != self.audit_head():
            return False
        # ENDCHECK
        # CHECK: audit_anchor_present
        if not anchored:
            return False
        # ENDCHECK
        return True

    def audit_anomalies(self) -> list[tuple[str, str]]:
        """Every shape of 'a write happened that the rules did not allow' this layer knows how to
        see after the fact, as (code, id). Empty means none seen — not none possible."""
        rows = self.audit_rows()
        out: list[tuple[str, str]] = []
        proposal_of = {r["proposal_id"]: json.loads(r["detail"]) for r in rows if r["kind"] == "PROPOSAL"}
        auto = {pid for pid, d in proposal_of.items() if d.get("status") == "approved"}
        approved_by_human = {r["proposal_id"] for r in rows if r["kind"] == "DECISION"
                             and json.loads(r["detail"]).get("approve") is True and r["principal"].startswith(HUMAN + ":")}
        for r in rows:
            d = json.loads(r["detail"])
            if r["kind"] in OUTCOMES:
                # CHECK: anomaly_executed_without_approval
                if r["proposal_id"] not in approved_by_human and r["proposal_id"] not in auto:
                    out.append(("executed_without_approval", r["proposal_id"]))
                # ENDCHECK
                # CHECK: anomaly_executed_action_mismatch
                src = proposal_of.get(r["proposal_id"], {})
                if (d.get("action"), d.get("record_id")) != (src.get("action"), src.get("record_id")):
                    out.append(("executed_action_mismatch", r["proposal_id"]))
                # ENDCHECK
            # CHECK: anomaly_decision_not_by_human
            if r["kind"] == "DECISION" and not r["principal"].startswith(HUMAN + ":"):
                out.append(("decision_not_by_human", r["proposal_id"]))
            # ENDCHECK
            # CHECK: anomaly_fuse_cleared_not_by_human
            if r["kind"] == "FUSE_CLEARED" and not r["principal"].startswith(HUMAN + ":"):
                out.append(("fuse_cleared_not_by_human", r["principal"]))
            # ENDCHECK
        attempted = [r["proposal_id"] for r in rows if r["kind"] == "EXECUTION_ATTEMPTED"]
        concluded = {r["proposal_id"] for r in rows if r["kind"] in OUTCOMES}
        # CHECK: anomaly_execution_without_outcome
        out += [("execution_without_outcome", pid) for pid in attempted if pid not in concluded]
        # ENDCHECK
        # CHECK: anomaly_execution_claim_unaudited
        out += [("execution_claim_unaudited", pid) for pid in self.execution_claims() if pid not in set(attempted)]
        # ENDCHECK
        # CHECK: anomaly_fuse_state_mismatch
        fuse_rows = [r for r in rows if r["kind"].startswith("FUSE_") and "tripped" in json.loads(r["detail"])]
        audited = int(json.loads(fuse_rows[-1]["detail"])["tripped"]) if fuse_rows else 0
        if int(bool(self.fuse_get()["tripped"])) != audited:
            out.append(("fuse_state_mismatch", "fuse"))
        # ENDCHECK
        return out

    def audit_orphans(self) -> list[str]:
        """Proposal ids executed without a human's approval or an auto-approval in the chain."""
        return [pid for code, pid in self.audit_anomalies() if code == "executed_without_approval"]

    @staticmethod
    def _hash(prev_hash: str, seq: int, ts: float, kind: str, principal: str, proposal_id: str | None, detail_s: str) -> str:
        payload = json.dumps([prev_hash, seq, ts, kind, principal, proposal_id, detail_s], sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
