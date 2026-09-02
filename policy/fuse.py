"""The fuse: a trip the agent can pull and cannot clear.

Lineage (PROVENANCE.md rows F1–F2): the trading system's lockdown daemon — a REAL trip cannot be
cleared the same day, and when the guard cannot see the account it blocks rather than allows
("cannot-see = block"). Here: anyone, including the agent and the budget, may trip the fuse; only
a human may clear it, and not on the day it tripped.

The fuse owns its clock. A caller cannot supply the time of a trip — an agent that could trip the
fuse "a hundred years from now" would lock the human out of their own clear path forever.
"""
from __future__ import annotations

import time
from typing import Callable

from .model import Principal, SYSTEM, TZ, local_date  # noqa: F401 — TZ re-exported for the service
from .store import Store


class Fuse:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time):
        self.store = store
        self.clock = clock

    def is_tripped(self) -> bool:
        return bool(self.store.fuse_get()["tripped"])

    def trip(self, reason: str, by: Principal) -> None:
        now = self.clock()
        self.store.fuse_set("FUSE_TRIPPED", by.tag, {"reason": reason}, now, tripped=True, reason=reason)

    def clear(self, by: Principal) -> bool:
        """Return True if cleared. Refusals are audited, never silent."""
        now = self.clock()
        state = self.store.fuse_get()
        if not state["tripped"]:
            return True
        # CHECK: fuse_clear_human_only
        if not by.is_human():
            self.store.audit_append("FUSE_CLEAR_REFUSED", by.tag, None, {"why": "not_human"}, ts=now)
            return False
        # ENDCHECK
        # CHECK: fuse_clear_not_same_day
        # the same-day rule binds trips stamped in the past. A trip stamped in the FUTURE can only
        # have been written by root; it must not lock the human out, so it clears at once and the
        # audit reports it as audit_time_not_monotonic
        if state["tripped_at"] <= now and local_date(now) <= local_date(state["tripped_at"]):
            self.store.audit_append("FUSE_CLEAR_REFUSED", by.tag, None, {"why": "same_day", "tripped_at": state["tripped_at"]}, ts=now)
            return False
        # ENDCHECK
        self.store.fuse_set("FUSE_CLEARED", by.tag, {"reason_was": state["reason"]}, now, tripped=False)
        return True


SYSTEM_PRINCIPAL = Principal("budget", SYSTEM)
