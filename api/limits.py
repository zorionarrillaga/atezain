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
