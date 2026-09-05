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
import hashlib
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


MAX_PARAMS_BYTES = 64 * 1024      # canonical JSON of the params; a note is not a novel
MAX_EVIDENCE_CHARS = 2000


class PolicyService:
    """Its bindings are fixed at construction: `config`, `store`, `clock` and `fuse` are read-only
    properties over slots, so whoever holds the service cannot swap the policy, the clock or the
    fuse under it (an outside seat did all three in one line each on 2026-09-02). Whoever holds
    the STORE is root — README §Trust boundary."""
    __slots__ = ("_config", "_store", "_clock", "_fuse", "_record_reader")

    def __init__(self, config: PolicyConfig, store: Store, clock: Callable[[], float] = time.time,
                 record_reader: Callable[[str], Any] | None = None):
        object.__setattr__(self, "_config", config)
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_clock", clock)
        object.__setattr__(self, "_fuse", Fuse(store, clock))
        # Read-only, and the records', not the model's: `record_constraints` asks it what a record
        # says about itself. Bound at construction like every other binding, so whoever holds the
        # service cannot point it at a record store of their own.
        object.__setattr__(self, "_record_reader", record_reader)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"PolicyService.{name} is fixed at construction")

    config = property(lambda self: self._config)
    store = property(lambda self: self._store)
    clock = property(lambda self: self._clock)
    fuse = property(lambda self: self._fuse)
    record_reader = property(lambda self: self._record_reader)

    # ── helpers ──────────────────────────────────────────────────────────────────────────────
    def _day_start(self, now: float) -> float:
        d = datetime.fromtimestamp(now, TZ)
        return (d - timedelta(hours=d.hour, minutes=d.minute, seconds=d.second, microseconds=d.microsecond)).timestamp()

    def _audit(self, kind: str, by: Principal, p: Proposal | None, detail: dict, now: float) -> None:
        self.store.audit_append(kind, by.tag, p.id if p else None, detail, ts=self.clock())

    # ── the agent's only verb ────────────────────────────────────────────────────────────────
    def propose(self, by: Principal, action: str, record_id: str, params: Any, evidence: str = "", *, idempotency_key: str | None = None) -> Proposal:
        now = self.clock()
        p = Proposal(id=Proposal.new_id(), principal_id=by.tag, action=action, record_id=record_id,
                     params={}, evidence=str(evidence), status=APPROVED, reason="", created_at=now)
        try:
            self.store.ping()
            # the checks, the budget count and the insert are ONE transaction: N concurrent
            # proposals against a budget of K yield at most K that are not denied
            with self.store.transaction():
                fingerprint = None
                if idempotency_key is not None:
                    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
                        raise ValueError("invalid idempotency key")
                    fingerprint = hashlib.sha256(canonical([by.tag, action, record_id, params, evidence]).encode()).hexdigest()
                    prior = self.store.request_get(idempotency_key)
                    if prior:
                        # CHECK: idempotency_payload_matches
                        if prior[0] != fingerprint:
                            raise ValueError("idempotency key reused with different input")
                        # ENDCHECK
                        existing = self.store.get_proposal(prior[1])
                        if existing is None:
                            raise ValueError("idempotency record has no proposal")
                        return existing
                try:
                    self._check(p, action, record_id, params, now)
                except Denied as e:
                    p.status, p.reason = DENIED, str(e)
                self._persist_proposal(p, by, now)
                if idempotency_key is not None and self.store.get_proposal(p.id) is not None:
                    self.store.request_put(idempotency_key, fingerprint, p.id)
                if p.reason == "budget_exhausted":
                    # The fuse trips AFTER the row that spent the last of the budget is written, and
                    # stamps itself when it is written. It used to trip from inside the check, which
                    # put FUSE_TRIPPED in front of the proposal it denied AND with a later clock
                    # reading than the row that follows it — so an ordinary day's budget running out
                    # made the visitor's own chain report `audit_time_not_monotonic` (client
                    # simulation 3, STATUS.md S3-6). Same transaction, so the trip and the row stand
                    # or fall together; the fuse still owns its clock and nobody hands it a time.
                    self.fuse.trip("budget_exhausted", SYSTEM_PRINCIPAL)
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
        canon = canonical({str(k): v for k, v in params.items()})
        # CHECK: params_bounded
        if len(canon.encode("utf-8")) > MAX_PARAMS_BYTES or len(p.evidence) > MAX_EVIDENCE_CHARS:
            raise Denied("params_too_large")
        # ENDCHECK
        p.params = json.loads(canon)
        # CHECK: params_scalar
        # a field is written with ONE value: a string, a number, a boolean or null. A nested
        # object in a field is not a value the adapter declared and is not compared as one
        nested = sorted(k for k, v in p.params.items() if isinstance(v, (dict, list)))
        if nested:
            raise Denied(f"param_not_scalar:{','.join(nested)}")
        # ENDCHECK
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
        # CHECK: record_shape
        # the SHAPE of an id for the record type the action declares — ASCII digits only. This is
        # not ownership: any well-formed id of the type passes; which ids a principal may touch is
        # the authenticated host's to decide (README §Trust boundary, step 4)
        pattern = self.config.records.get(spec.record)
        if pattern is None or not isinstance(record_id, str) or re.fullmatch(pattern, record_id, re.ASCII) is None:
            raise Denied(f"record_shape:{spec.record}")
        # ENDCHECK
        # CHECK: value_of_record
        # A field the adapter BINDS to the record's own value is not the model's to choose: the
        # reminder's address must be the address the record already carries. Three ways to say no,
        # and all three are the same sentence — a value this layer cannot check against the record
        # is a value it does not accept: no reader to ask, a record that carries no such value, or
        # a value that differs from it. The bound source field is one no action declares in
        # `writes` (`test_no_action_writes_a_field_another_action_is_checked_against`), so the
        # agent cannot move the truth it is measured against.
        for f, source in spec.record_constraints.items():
            if f not in p.params:
                continue
            if self.record_reader is None:
                raise Denied(f"no_record_reader:{source}")
            record = self.record_reader(record_id)
            truth = record.get(source) if isinstance(record, Mapping) else None
            if truth is None or truth == "":
                raise Denied(f"record_has_no:{source}")
            if str(p.params[f]) != str(truth):
                raise Denied(f"value_not_of_record:{f}")
        # ENDCHECK
        day = self._day_start(now)
        # CHECK: daily_budget_trips_fuse
        # the trip this denial causes is pulled by `propose`, after this proposal's row is written
        if self.config.daily_writes and self.store.count_proposals_since(day, LIVE) >= self.config.daily_writes:
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
        # CHECK: approval_mode_valid
        if spec.approval not in {"required", "none"}:
            raise Denied("invalid_approval_mode")
        # ENDCHECK

    def _persist_proposal(self, p: Proposal, by: Principal, now: float) -> None:
        try:
            with self.store.transaction():
                self.store.put_proposal(p)
                self._audit("PROPOSAL", by, p, {"status": p.status, "reason": p.reason, "action": p.action,
                                                "record_id": p.record_id, "params": p.params,
                                                "evidence": p.evidence[:MAX_EVIDENCE_CHARS],
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
            try:
                self.store.record_effect(p.id, effect)
                applied = effect.get("applied") if isinstance(effect, Mapping) else None
                same = canonical(applied) == canonical(p.params)
            except Exception as e:  # noqa: BLE001 — an effect that cannot be written down is not a match
                applied = f"unrenderable:{type(e).__name__}"
                self.store.record_effect(p.id, {"unrenderable": type(e).__name__})
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
