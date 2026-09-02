"""The policy service — the boundary the model cannot reach.

The agent calls `propose`. It never calls anything else. A human calls `decide`. The application
calls `execute` with an executor that performs the write and reports what it actually did; the
service compares that report with what was approved and shouts if they differ.

Every branch that can say "no" is wrapped in `# CHECK: <name>` … `# ENDCHECK` markers.
`tests/mutate.py` deletes each block in turn and asserts the test suite goes red: a check whose
removal changes nothing is not a check (the trading system's own lesson, PROVENANCE.md row C1).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Callable

from .fuse import Fuse, SYSTEM_PRINCIPAL, TZ
from .model import (APPROVED, DENIED, EXECUTED, EXECUTED_MISMATCH, HELD, REJECTED,
                    PolicyConfig, Principal, Proposal)
from .store import Store

Executor = Callable[[str, str, dict[str, Any]], dict[str, Any]]
LIVE = (HELD, APPROVED, EXECUTED, EXECUTED_MISMATCH)   # proposals that count against budgets


class Denied(Exception):
    """Raised inside `propose` by a check that says no. Never escapes `propose`."""


class PolicyService:
    def __init__(self, config: PolicyConfig, store: Store, clock: Callable[[], float] = time.time):
        self.config = config
        self.store = store
        self.fuse = Fuse(store)
        self.clock = clock

    # ── helpers ──────────────────────────────────────────────────────────────────────────────
    def _day_start(self, now: float) -> float:
        d = datetime.fromtimestamp(now, TZ)
        return (d - timedelta(hours=d.hour, minutes=d.minute, seconds=d.second, microseconds=d.microsecond)).timestamp()

    def _audit(self, kind: str, by: Principal, p: Proposal | None, detail: dict, now: float) -> None:
        self.store.audit_append(kind, by.id, p.id if p else None, detail, ts=now)

    # ── the agent's only verb ────────────────────────────────────────────────────────────────
    def propose(self, by: Principal, action: str, record_id: str, params: dict[str, Any], evidence: str = "") -> Proposal:
        now = self.clock()
        p = Proposal(id=Proposal.new_id(), principal_id=by.id, action=action, record_id=record_id,
                     params=dict(params), evidence=evidence, status=APPROVED, reason="", created_at=now)
        try:
            self.store.ping()
            # CHECK: fuse_blocks_proposals
            if self.fuse.is_tripped():
                raise Denied("fuse_tripped")
            # ENDCHECK
            spec = self.config.actions.get(action)
            # CHECK: unknown_action
            if spec is None:
                raise Denied("unknown_action")
            # ENDCHECK
            # CHECK: denied_action
            if spec.deny:
                raise Denied("action_denied")
            # ENDCHECK
            # CHECK: field_smuggling
            extra = sorted(set(params) - set(spec.writes))
            if extra:
                raise Denied(f"field_not_permitted:{','.join(extra)}")
            # ENDCHECK
            # CHECK: value_constraint
            for f, allowed in spec.constraints.items():
                if f in params and params[f] not in allowed:
                    raise Denied(f"value_not_permitted:{f}")
            # ENDCHECK
            day = self._day_start(now)
            # CHECK: daily_budget_trips_fuse
            if self.config.daily_writes and self.store.count_proposals_since(day, LIVE) >= self.config.daily_writes:
                self.fuse.trip("budget_exhausted", SYSTEM_PRINCIPAL, now)
                raise Denied("budget_exhausted")
            # ENDCHECK
            # CHECK: action_daily_max
            if spec.daily_max is not None and self.store.count_proposals_since(day, LIVE, action) >= spec.daily_max:
                raise Denied("action_daily_max")
            # ENDCHECK
            # CHECK: approval_required
            if spec.approval == "required":
                p.status = HELD
            # ENDCHECK
        except Denied as e:
            p.status, p.reason = DENIED, str(e)
        except Exception as e:  # noqa: BLE001 — anything unexpected is a NO, never a YES
            # CHECK: fail_closed
            p.status, p.reason = DENIED, f"fail_closed:{type(e).__name__}"
            # ENDCHECK
        try:
            self.store.put_proposal(p)
            self._audit("PROPOSAL", by, p, {"status": p.status, "reason": p.reason, "action": action, "record_id": record_id, "params": p.params}, now)
        except Exception as e:  # noqa: BLE001
            # CHECK: persist_fail_closed
            p.status, p.reason = DENIED, f"fail_closed:persist:{type(e).__name__}"
            # ENDCHECK
        return p

    # ── the human's verb ─────────────────────────────────────────────────────────────────────
    def decide(self, proposal_id: str, approve: bool, by: Principal, note: str = "") -> Proposal:
        now = self.clock()
        p = self.store.get_proposal(proposal_id)
        if p is None:
            raise KeyError(proposal_id)
        # CHECK: decide_human_only
        if not by.is_human():
            self._audit("DECISION_REFUSED", by, p, {"why": "not_human"}, now)
            return p
        # ENDCHECK
        # CHECK: decide_only_held
        if p.status != HELD:
            self._audit("DECISION_REFUSED", by, p, {"why": f"status_{p.status}"}, now)
            return p
        # ENDCHECK
        p.status = APPROVED if approve else REJECTED
        p.decided_by, p.decided_at, p.note = by.id, now, note
        self.store.put_proposal(p)
        self._audit("DECISION", by, p, {"approve": approve, "note": note}, now)
        return p

    # ── the application's verb ───────────────────────────────────────────────────────────────
    def execute(self, proposal_id: str, executor: Executor, by: Principal) -> Proposal:
        """Execute an APPROVED proposal exactly once, with the params that were approved, and
        re-validate what actually happened against them."""
        now = self.clock()
        # CHECK: execute_reads_store
        p = self.store.get_proposal(proposal_id)   # never the caller's object: it may have been edited since approval
        # ENDCHECK
        if p is None:
            raise KeyError(proposal_id)
        # CHECK: execute_only_approved
        if p.status != APPROVED:
            self._audit("EXECUTION_REFUSED", by, p, {"why": f"status_{p.status}"}, now)
            return p
        # ENDCHECK
        # CHECK: fuse_blocks_execution
        if self.fuse.is_tripped():
            self._audit("EXECUTION_REFUSED", by, p, {"why": "fuse_tripped"}, now)
            return p
        # ENDCHECK
        # CHECK: exactly_once
        if not self.store.claim_execution(p.id, now):
            self._audit("EXECUTION_REFUSED", by, p, {"why": "already_executed"}, now)
            return p
        # ENDCHECK
        effect = executor(p.action, p.record_id, dict(p.params))
        self.store.record_effect(p.id, effect)
        applied = effect.get("applied") if isinstance(effect, dict) else None
        # CHECK: revalidate_after_execution
        if applied != p.params:
            p.status = EXECUTED_MISMATCH
            self.store.put_proposal(p)
            self._audit("EXECUTION_MISMATCH", by, p, {"approved": p.params, "applied": applied}, now)
            return p
        # ENDCHECK
        p.status = EXECUTED
        self.store.put_proposal(p)
        self._audit("EXECUTED", by, p, {"applied": applied}, now)
        return p
