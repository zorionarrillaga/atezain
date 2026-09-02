"""The policy service — the boundary the model cannot reach.

The agent calls `propose`. It never calls anything else. A human calls `decide`. The application
calls `execute` with an executor that performs the write and reports what it actually did; the
service compares that report with what was approved and shouts if they differ.

Every branch that can say "no" is wrapped in `# CHECK: <name>` … `# ENDCHECK` markers.
`tests/mutate.py` deletes each block in turn and asserts the test suite goes red BY ASSERTION —
a check whose removal changes nothing is not a check, and a check whose removal only crashes the
suite was never tested either (the trading system's own lesson, PROVENANCE.md row C1).

What the checks read: `propose` copies the caller's params through canonical JSON once, and every
check and the executor read THAT copy. A check that reads the caller's object while the executor
reads a copy is two different questions with one answer — an outside seat got three writes
through that gap on 2026-09-01.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Callable

from .fuse import Fuse, SYSTEM_PRINCIPAL, TZ
from .model import (APPROVED, DENIED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN, HELD, REJECTED,
                    PolicyConfig, Principal, Proposal, canonical)
from .store import Store

Executor = Callable[[str, str, dict[str, Any]], dict[str, Any]]

# Proposals that count against the daily budget: everything the policy did not deny. A human's
# REJECTED counts too — a budget the agent refills by getting rejected is not a limit (an outside
# seat put 40 proposals in front of the human in one day against a budget of 3 before this line).
LIVE = (HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN)


class Denied(Exception):
    """Raised inside `propose` by a check that says no. Never escapes `propose`."""


def _render(value: Any) -> Any:
    """A JSON-safe picture of whatever the executor reported, for the audit row. Never raises."""
    try:
        return json.loads(canonical(value))
    except (TypeError, ValueError):
        return repr(value)


class PolicyService:
    def __init__(self, config: PolicyConfig, store: Store, clock: Callable[[], float] = time.time):
        self.config = config
        self.store = store
        self.clock = clock
        self.fuse = Fuse(store, clock)

    # ── helpers ──────────────────────────────────────────────────────────────────────────────
    def _day_start(self, now: float) -> float:
        d = datetime.fromtimestamp(now, TZ)
        return (d - timedelta(hours=d.hour, minutes=d.minute, seconds=d.second, microseconds=d.microsecond)).timestamp()

    def _audit(self, kind: str, by: Principal, p: Proposal | None, detail: dict, now: float) -> None:
        self.store.audit_append(kind, by.tag, p.id if p else None, detail, ts=now)

    # ── the agent's only verb ────────────────────────────────────────────────────────────────
    def propose(self, by: Principal, action: str, record_id: str, params: Any, evidence: str = "") -> Proposal:
        now = self.clock()
        p = Proposal(id=Proposal.new_id(), principal_id=by.tag, action=action, record_id=record_id,
                     params={}, evidence=evidence, status=APPROVED, reason="", created_at=now)
        try:
            self.store.ping()
            # the checks, the budget count and the insert are ONE transaction: N concurrent
            # proposals against a budget of K yield at most K that are not denied
            with self.store.transaction():
                try:
                    self._check(p, action, record_id, params, now)
                except Denied as e:
                    p.status, p.reason = DENIED, str(e)
                self._persist_proposal(p, by, now)
        except Exception as e:  # noqa: BLE001 — anything unexpected is a NO, never a YES
            # CHECK: fail_closed
            p.status, p.reason = DENIED, f"fail_closed:{type(e).__name__}"
            self._persist_proposal(p, by, now)
            # ENDCHECK
        return p

    def _check(self, p: Proposal, action: str, record_id: str, params: Any, now: float) -> None:
        """Every reason to say no, in order. Raises Denied; sets p.status/p.params on the way through."""
        # CHECK: fuse_blocks_proposals
        if self.fuse.is_tripped():
            raise Denied("fuse_tripped")
        # ENDCHECK
        spec = self.config.actions.get(action) if isinstance(action, str) else None
        # CHECK: unknown_action
        if spec is None:
            raise Denied("unknown_action")
        # ENDCHECK
        # CHECK: denied_action
        if spec.deny:
            raise Denied("action_denied")
        # ENDCHECK
        # CHECK: params_shape
        if not isinstance(params, Mapping):
            raise Denied("params_not_a_mapping")
        # ENDCHECK
        # one plain copy, made through canonical JSON; everything below reads p.params
        p.params = json.loads(canonical({str(k): v for k, v in params.items()}))
        # CHECK: field_smuggling
        extra = sorted(set(p.params) - set(spec.writes))
        if extra:
            raise Denied(f"field_not_permitted:{','.join(extra)}")
        # ENDCHECK
        # CHECK: value_constraint
        for f, allowed in spec.constraints.items():
            if f in p.params and p.params[f] not in allowed:
                raise Denied(f"value_not_permitted:{f}")
        # ENDCHECK
        # CHECK: record_scope
        pattern = self.config.records.get(spec.record)
        if pattern is None or not isinstance(record_id, str) or re.fullmatch(pattern, record_id) is None:
            raise Denied(f"record_out_of_scope:{spec.record}")
        # ENDCHECK
        day = self._day_start(now)
        # CHECK: daily_budget_trips_fuse
        if self.config.daily_writes and self.store.count_proposals_since(day, LIVE) >= self.config.daily_writes:
            self.fuse.trip("budget_exhausted", SYSTEM_PRINCIPAL)
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

    def _persist_proposal(self, p: Proposal, by: Principal, now: float) -> None:
        try:
            with self.store.transaction():
                self.store.put_proposal(p)
                self._audit("PROPOSAL", by, p, {"status": p.status, "reason": p.reason, "action": p.action,
                                                "record_id": p.record_id, "params": p.params,
                                                "policy": self.config.fingerprint()}, now)
        except Exception as e:  # noqa: BLE001
            # CHECK: persist_fail_closed
            p.status, p.reason = DENIED, f"fail_closed:persist:{type(e).__name__}"
            # ENDCHECK

    # ── the human's verb ─────────────────────────────────────────────────────────────────────
    def decide(self, proposal_id: str, approve: bool, by: Principal, note: str = "") -> Proposal:
        now = self.clock()
        with self.store.transaction():
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
            p.status = APPROVED if approve is True else REJECTED
            p.decided_by, p.decided_at, p.note = by.tag, now, note
            self.store.put_proposal(p)
            self._audit("DECISION", by, p, {"approve": approve is True, "note": note}, now)
            return p

    # ── the application's verb ───────────────────────────────────────────────────────────────
    def execute(self, proposal_id: str, executor: Executor, by: Principal) -> Proposal:
        """Execute an APPROVED proposal exactly once, with the params that were approved, and
        re-validate what actually happened against them. The proposal is read from the store,
        never taken from the caller: it may have been edited since approval.

        An EXECUTION_ATTEMPTED row is committed BEFORE the executor runs, so a write that happens
        and then raises, or a process that dies mid-write, leaves a row that `audit_anomalies()`
        reports as `execution_without_outcome`. A raise is recorded as EXECUTION_UNKNOWN and the
        proposal ends `executed_unknown` — the write may or may not have happened, and the layer
        says exactly that instead of nothing."""
        now = self.clock()
        with self.store.transaction():
            p = self.store.get_proposal(proposal_id)
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
            ident = {"action": p.action, "record_id": p.record_id}
            # CHECK: execution_attempted_before_write
            self._audit("EXECUTION_ATTEMPTED", by, p, {**ident, "params": p.params}, now)
            # ENDCHECK
        effect, err = None, None
        try:
            effect = executor(p.action, p.record_id, dict(p.params))
        except Exception as e:  # noqa: BLE001
            err = e
        with self.store.transaction():
            # CHECK: executor_failure_is_recorded
            if err is not None:
                p.status = EXECUTED_UNKNOWN
                self.store.put_proposal(p)
                self.store.record_effect(p.id, {"error": f"{type(err).__name__}: {err}"})
                self._audit("EXECUTION_UNKNOWN", by, p, {**ident, "error": type(err).__name__, "message": str(err)[:500]}, now)
                return p
            # ENDCHECK
            self.store.record_effect(p.id, effect)
            applied = effect.get("applied") if isinstance(effect, Mapping) else None
            try:
                same = canonical(applied) == canonical(p.params)
            except (TypeError, ValueError):
                same = False
            # CHECK: revalidate_after_execution
            if not same:
                p.status = EXECUTED_MISMATCH
                self.store.put_proposal(p)
                self._audit("EXECUTION_MISMATCH", by, p, {**ident, "approved": p.params, "applied": _render(applied)}, now)
                return p
            # ENDCHECK
            p.status = EXECUTED
            self.store.put_proposal(p)
            self._audit("EXECUTED", by, p, {**ident, "applied": p.params}, now)
            return p
