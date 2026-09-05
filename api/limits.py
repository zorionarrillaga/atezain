"""What one visitor may cost (PLAN.md §4.3).

Two different things are limited, for two different reasons:

- **Per IP**: 10 assists a minute and 60 a day, so one visitor cannot occupy the instance.
- **The server's own model key**: a global budget of 800 calls a day. Reaching it trips a SERVER
  fuse that only the owner clears — the same shape as the policy's own fuse, and for the same
  reason: the thing that spends must not be the thing that decides it may keep spending. A visitor
  who brings their own key (`X-Groq-Key`) is not counted against it and is not stopped by it.

In-process counters. One Render free instance is one process, and a restart forgets the minute and
the day — which is why the server fuse is written to disk when a path is given, so a restart does
not silently re-open the tap.
"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from api.storage import Database


class Limits:
    def __init__(self, per_minute: int = 10, per_day: int = 60, model_day: int = 800,
                 state: str | Path | None = None, clock=time.time):
        self.per_minute, self.per_day, self.model_day = per_minute, per_day, model_day
        self.clock, self.state = clock, Path(state) if state else None
        self.hits: dict[str, deque[float]] = {}
        self.model_calls, self.model_day_stamp, self.fuse = 0, self._today(), False
        self._load()

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self.clock()))

    def _load(self) -> None:
        if self.state and self.state.exists():
            try:
                d = json.loads(self.state.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            self.fuse = bool(d.get("fuse"))
            if d.get("day") == self._today():
                self.model_calls = int(d.get("model_calls", 0))

    def _save(self) -> None:
        if self.state:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            self.state.write_text(json.dumps({"fuse": self.fuse, "day": self.model_day_stamp,
                                              "model_calls": self.model_calls}), encoding="utf-8")

    # ── per IP ───────────────────────────────────────────────────────────────────────────────
    def allow(self, ip: str) -> tuple[bool, str]:
        now = self.clock()
        q = self.hits.setdefault(ip, deque())
        while q and now - q[0] > 86400:
            q.popleft()
        if sum(1 for t in q if now - t <= 60) >= self.per_minute:
            return False, "rate_limited_minute"
        if len(q) >= self.per_day:
            return False, "rate_limited_day"
        q.append(now)
        return True, ""

    # ── the server's own key ─────────────────────────────────────────────────────────────────
    def allow_model_call(self, byok: bool) -> tuple[bool, str]:
        """A visitor's own key is never counted and never blocked by the server's budget."""
        if byok:
            return True, ""
        if self.model_day_stamp != self._today():
            self.model_calls, self.model_day_stamp = 0, self._today()
        if self.fuse:
            return False, "server_fuse_tripped"
        if self.model_calls >= self.model_day:
            self.fuse = True
            self._save()
            return False, "model_budget_exhausted"
        self.model_calls += 1
        self._save()
        return True, ""

    def clear_fuse(self, owner_token: str, expected: str | None) -> bool:
        """The owner, and nobody else. `expected` is the deployment's own secret; without one
        configured the fuse cannot be cleared over the network at all."""
        import secrets as _s
        if not expected or not owner_token or not _s.compare_digest(owner_token, expected):
            return False
        self.fuse, self.model_calls = False, 0
        self._save()
        return True


class PersistentLimits:
    """Atomic fixed windows and a durable model fuse, shared by application workers.

    Rate keys are hashes of transport peer addresses, never untrusted forwarding headers.
    No customer content or provider credentials are stored here.
    """
    def __init__(self, path, dsn=None, per_minute=10, per_day=60, model_day=800, clock=time.time):
        self.db, self.clock = Database(path, dsn), clock
        self.per_minute, self.per_day, self.model_day = per_minute, per_day, model_day
        with self.db.transaction("limits") as execute:
            execute("CREATE TABLE IF NOT EXISTS request_windows (key TEXT, period_seconds INTEGER, hits INTEGER, "
                    "expires DOUBLE PRECISION, PRIMARY KEY (key, period_seconds))")
            execute("CREATE TABLE IF NOT EXISTS model_budget (id INTEGER PRIMARY KEY, day TEXT, calls INTEGER, fuse INTEGER)")
            execute("INSERT INTO model_budget VALUES (1, '', 0, 0) ON CONFLICT (id) DO NOTHING")

    def allow(self, peer):
        return self.consume("assist:" + peer, ((60, self.per_minute), (86400, self.per_day)))

    def consume(self, key, windows):
        import hashlib
        now = self.clock()
        key = hashlib.sha256(key.encode()).hexdigest()
        with self.db.transaction("limits") as execute:
            execute("DELETE FROM request_windows WHERE expires <= ?", (now,))
            for seconds, maximum in windows:
                start = int(now // seconds) * seconds
                row = execute("SELECT hits FROM request_windows WHERE key = ? AND period_seconds = ?", (key, seconds)).fetchone()
                if row and row[0] >= maximum:
                    return False, "rate_limited_minute" if seconds == 60 else "rate_limited_day"
            for seconds, maximum in windows:
                expires = (int(now // seconds) + 1) * seconds
                execute("INSERT INTO request_windows VALUES (?,?,1,?) ON CONFLICT (key, period_seconds) "
                        "DO UPDATE SET hits = request_windows.hits + 1", (key, seconds, expires))
        return True, ""

    def _budget(self, execute):
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        old_day, calls, fuse = execute("SELECT day, calls, fuse FROM model_budget WHERE id = 1").fetchone()
        if old_day != day:
            calls = 0
            execute("UPDATE model_budget SET day = ?, calls = 0 WHERE id = 1", (day,))
        return calls, bool(fuse)

    def allow_model_call(self, byok):
        if byok:
            return True, ""
        with self.db.transaction("limits") as execute:
            calls, fuse = self._budget(execute)
            if fuse:
                return False, "server_fuse_tripped"
            if calls >= self.model_day:
                execute("UPDATE model_budget SET fuse = 1 WHERE id = 1")
                return False, "model_budget_exhausted"
            execute("UPDATE model_budget SET calls = calls + 1 WHERE id = 1")
        return True, ""

    @property
    def model_calls(self):
        with self.db.transaction("limits") as execute:
            return self._budget(execute)[0]

    @property
    def fuse(self):
        with self.db.transaction("limits") as execute:
            return self._budget(execute)[1]

    def clear_fuse(self, owner_token, expected):
        import secrets
        if not expected or not owner_token or not secrets.compare_digest(owner_token, expected):
            return False
        with self.db.transaction("limits") as execute:
            execute("UPDATE model_budget SET fuse = 0, calls = 0 WHERE id = 1")
        return True
