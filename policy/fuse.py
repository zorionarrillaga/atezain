"""The fuse: a trip the agent can pull and cannot clear.

Lineage (PROVENANCE.md rows F1–F2): the trading system's lockdown daemon — a REAL trip cannot be
cleared the same day, and when the guard cannot see the account it blocks rather than allows
("cannot-see = block"). Here: anyone, including the agent and the budget, may trip the fuse; only
a human may clear it, and not on the day it tripped.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .model import Principal, SYSTEM
from .store import Store

TZ = ZoneInfo("Europe/Madrid")


def local_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).date().isoformat()


class Fuse:
    def __init__(self, store: Store):
        self.store = store

    def is_tripped(self) -> bool:
        return bool(self.store.fuse_get()["tripped"])

    def trip(self, reason: str, by: Principal, now: float) -> None:
        self.store.fuse_set(tripped=1, reason=reason, tripped_at=now, tripped_by=by.id, cleared_at=None, cleared_by=None)
        self.store.audit_append("FUSE_TRIPPED", by.id, None, {"reason": reason}, ts=now)

    def clear(self, by: Principal, now: float) -> bool:
        """Return True if cleared. Refusals are audited, never silent."""
        state = self.store.fuse_get()
        if not state["tripped"]:
            return True
        # CHECK: fuse_clear_human_only
        if not by.is_human():
            self.store.audit_append("FUSE_CLEAR_REFUSED", by.id, None, {"why": "not_human"}, ts=now)
            return False
        # ENDCHECK
        # CHECK: fuse_clear_not_same_day
        if local_date(now) <= local_date(state["tripped_at"]):
            self.store.audit_append("FUSE_CLEAR_REFUSED", by.id, None, {"why": "same_day", "tripped_at": state["tripped_at"]}, ts=now)
            return False
        # ENDCHECK
        self.store.fuse_set(tripped=0, cleared_at=now, cleared_by=by.id)
        self.store.audit_append("FUSE_CLEARED", by.id, None, {"reason_was": state["reason"]}, ts=now)
        return True


SYSTEM_PRINCIPAL = Principal("budget", SYSTEM)
