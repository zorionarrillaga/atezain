"""Every test here is paired with a `# CHECK:` block in policy/. `tests/mutate.py` removes each block
and expects at least one of these to fail. A test that keeps passing when its check is deleted is a
check that cannot fail, and this repo's standing rule is that such a check is not a check."""
from pathlib import Path

import pytest

from policy import (AGENT, HUMAN, DENIED, HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH,
                    PolicyConfig, Principal, Store, PolicyService)

ROOT = Path(__file__).resolve().parents[1]
AGENT_P = Principal("assistant", AGENT)
HUMAN_P = Principal("zorion", HUMAN)

DAY = 86400.0
T0 = 1_788_256_800.0   # 2026-09-01 12:00 Europe/Madrid (10:00 UTC), a fixed midday instant


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make(daily_writes=None):
    cfg = PolicyConfig.load(ROOT / "adapters" / "invoices-es" / "permissions.toml")
    if daily_writes is not None:
        cfg.daily_writes = daily_writes
    store = Store(":memory:")
    clock = Clock()
    return PolicyService(cfg, store, clock), store, clock


def ok_executor(action, record_id, params):
    return {"applied": dict(params)}


# ── propose ──────────────────────────────────────────────────────────────────────────────────
def test_unknown_action_is_denied():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "transfer_funds", "inv-1", {})
    assert p.status == DENIED and p.reason == "unknown_action"


def test_case_variant_of_a_denied_action_is_still_unknown_not_allowed():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "Update_Amount", "inv-1", {"amount": 0})
    assert p.status == DENIED


def test_denied_action_is_denied():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "update_amount", "inv-1", {"amount": 0.01})
    assert p.status == DENIED and p.reason == "action_denied"


def test_field_smuggling_is_denied():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "send_reminder", "inv-1", {"reminder_text": "hola", "reminder_channel": "email", "amount": 1})
    assert p.status == DENIED and p.reason.startswith("field_not_permitted:amount")


def test_value_outside_constraint_is_denied():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "update_status", "inv-1", {"status": "paid"})
    assert p.status == DENIED and p.reason == "value_not_permitted:status"
    p2 = svc.propose(AGENT_P, "send_reminder", "inv-1", {"reminder_text": "x", "reminder_channel": "whatsapp"})
    assert p2.status == DENIED


def test_consequential_action_is_held_for_a_human():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "update_status", "inv-1", {"status": "reminded"})
    assert p.status == HELD


def test_non_consequential_action_is_auto_approved_but_audited():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "add_note", "inv-1", {"note": "llamó el cliente"})
    assert p.status == APPROVED
    kinds = [r["kind"] for r in store.audit_rows()]
    assert kinds.count("PROPOSAL") == 1


# ── decide ───────────────────────────────────────────────────────────────────────────────────
def test_agent_cannot_decide():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "update_status", "inv-1", {"status": "reminded"})
    q = svc.decide(p.id, True, AGENT_P)
    assert q.status == HELD
    assert any(r["kind"] == "DECISION_REFUSED" for r in store.audit_rows())


def test_human_can_approve_and_reject():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "update_status", "inv-1", {"status": "reminded"})
    assert svc.decide(p.id, True, HUMAN_P).status == APPROVED
    p2 = svc.propose(AGENT_P, "update_status", "inv-2", {"status": "disputed"})
    assert svc.decide(p2.id, False, HUMAN_P, note="no").status == REJECTED


def test_only_held_proposals_can_be_decided():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "update_amount", "inv-1", {"amount": 1})   # denied
    assert svc.decide(p.id, True, HUMAN_P).status == DENIED
    q = svc.propose(AGENT_P, "add_note", "inv-1", {"note": "x"})        # auto-approved
    assert svc.decide(q.id, False, HUMAN_P).status == APPROVED           # cannot be flipped after the fact


# ── execute ──────────────────────────────────────────────────────────────────────────────────
def test_execute_only_approved():
    svc, store, _ = make()
    held = svc.propose(AGENT_P, "update_status", "inv-1", {"status": "reminded"})
    calls = []
    assert svc.execute(held.id, lambda a, r, p: calls.append(1) or {"applied": p}, HUMAN_P).status == HELD
    rejected = svc.decide(svc.propose(AGENT_P, "update_status", "inv-2", {"status": "reminded"}).id, False, HUMAN_P)
    assert svc.execute(rejected.id, lambda a, r, p: calls.append(1) or {"applied": p}, HUMAN_P).status == REJECTED
    assert calls == []


def test_execute_exactly_once():
    svc, _, _ = make()
    p = svc.decide(svc.propose(AGENT_P, "update_status", "inv-1", {"status": "reminded"}).id, True, HUMAN_P)
    calls = []
    svc.execute(p.id, lambda a, r, prm: calls.append(1) or {"applied": prm}, HUMAN_P)
    svc.execute(p.id, lambda a, r, prm: calls.append(1) or {"applied": prm}, HUMAN_P)
    assert calls == [1]


def test_execute_uses_the_approved_params_not_the_callers_object():
    svc, _, _ = make()
    p = svc.decide(svc.propose(AGENT_P, "update_status", "inv-1", {"status": "reminded"}).id, True, HUMAN_P)
    p.params["status"] = "paid"                       # an attacker edits the object it holds
    seen = {}
    svc.execute(p.id, lambda a, r, prm: seen.update(prm) or {"applied": prm}, HUMAN_P)
    assert seen == {"status": "reminded"}


def test_revalidation_flags_a_mismatch_loudly():
    svc, store, _ = make()
    p = svc.decide(svc.propose(AGENT_P, "update_status", "inv-1", {"status": "reminded"}).id, True, HUMAN_P)
    q = svc.execute(p.id, lambda a, r, prm: {"applied": {"status": "paid"}}, HUMAN_P)
    assert q.status == EXECUTED_MISMATCH
    assert any(r["kind"] == "EXECUTION_MISMATCH" for r in store.audit_rows())
    good = svc.decide(svc.propose(AGENT_P, "update_status", "inv-2", {"status": "reminded"}).id, True, HUMAN_P)
    assert svc.execute(good.id, ok_executor, HUMAN_P).status == EXECUTED


# ── fuse ─────────────────────────────────────────────────────────────────────────────────────
def test_agent_can_trip_but_not_clear_the_fuse():
    svc, _, clock = make()
    svc.fuse.trip("agent saw something wrong", AGENT_P, clock())
    assert svc.fuse.is_tripped()
    clock.advance(2 * DAY)
    assert svc.fuse.clear(AGENT_P, clock()) is False
    assert svc.fuse.is_tripped()


def test_human_cannot_clear_the_fuse_the_same_day_but_can_the_next():
    svc, _, clock = make()
    svc.fuse.trip("drill", HUMAN_P, clock())
    clock.advance(3600)
    assert svc.fuse.clear(HUMAN_P, clock()) is False
    clock.advance(DAY)
    assert svc.fuse.clear(HUMAN_P, clock()) is True
    assert not svc.fuse.is_tripped()


def test_tripped_fuse_blocks_proposals_and_executions():
    svc, _, clock = make()
    p = svc.decide(svc.propose(AGENT_P, "update_status", "inv-1", {"status": "reminded"}).id, True, HUMAN_P)
    svc.fuse.trip("stop", HUMAN_P, clock())
    assert svc.propose(AGENT_P, "add_note", "inv-1", {"note": "x"}).status == DENIED
    calls = []
    assert svc.execute(p.id, lambda a, r, prm: calls.append(1) or {"applied": prm}, HUMAN_P).status == APPROVED
    assert calls == []


def test_daily_budget_trips_the_fuse_and_only_a_human_clears_it_tomorrow():
    svc, _, clock = make(daily_writes=3)
    for i in range(3):
        assert svc.propose(AGENT_P, "add_note", f"inv-{i}", {"note": "x"}).status == APPROVED
    over = svc.propose(AGENT_P, "add_note", "inv-9", {"note": "x"})
    assert over.status == DENIED and over.reason == "budget_exhausted"
    assert svc.fuse.is_tripped()
    clock.advance(DAY + 60)
    assert svc.fuse.clear(HUMAN_P, clock()) is True
    assert svc.propose(AGENT_P, "add_note", "inv-10", {"note": "x"}).status == APPROVED


def test_per_action_daily_max():
    svc, _, _ = make(daily_writes=100)
    for i in range(10):
        assert svc.propose(AGENT_P, "send_reminder", f"inv-{i}", {"reminder_text": "x", "reminder_channel": "email"}).status == HELD
    p = svc.propose(AGENT_P, "send_reminder", "inv-11", {"reminder_text": "x", "reminder_channel": "email"})
    assert p.status == DENIED and p.reason == "action_daily_max"


# ── fail closed ──────────────────────────────────────────────────────────────────────────────
class PingFails(Store):
    def ping(self):
        raise RuntimeError("policy state unreachable")


def test_unreachable_policy_state_denies_instead_of_allowing():
    cfg = PolicyConfig.load(ROOT / "adapters" / "invoices-es" / "permissions.toml")
    svc = PolicyService(cfg, PingFails(":memory:"), Clock())
    p = svc.propose(AGENT_P, "add_note", "inv-1", {"note": "x"})
    assert p.status == DENIED and p.reason.startswith("fail_closed:")


def test_closed_store_denies_and_does_not_raise():
    svc, store, _ = make()
    store.close()
    p = svc.propose(AGENT_P, "add_note", "inv-1", {"note": "x"})
    assert p.status == DENIED and p.reason.startswith("fail_closed:")


# ── audit ────────────────────────────────────────────────────────────────────────────────────
def test_audit_is_append_only_and_tamper_evident():
    svc, store, _ = make()
    assert not any(n.startswith(("audit_update", "audit_delete", "audit_remove")) for n in dir(store))
    svc.propose(AGENT_P, "add_note", "inv-1", {"note": "x"})
    svc.propose(AGENT_P, "update_amount", "inv-1", {"amount": 1})
    assert store.audit_verify() is True
    store._c().execute("UPDATE audit SET detail = ? WHERE seq = 1", ('{"status": "approved"}',))
    assert store.audit_verify() is False


def test_every_path_leaves_an_audit_row():
    svc, store, clock = make()
    n0 = len(store.audit_rows())
    svc.propose(AGENT_P, "nope", "inv-1", {})                                                  # denied
    held = svc.propose(AGENT_P, "update_status", "inv-1", {"status": "reminded"})               # held
    svc.decide(held.id, True, AGENT_P)                                                         # refused
    svc.decide(held.id, True, HUMAN_P)                                                         # decision
    svc.execute(held.id, ok_executor, HUMAN_P)                                                 # executed
    svc.execute(held.id, ok_executor, HUMAN_P)                                                 # refused
    assert len(store.audit_rows()) - n0 == 6


# ── the two checks the first mutation pass found could not fail, now exercised for real ──────
def test_reentrant_execution_during_the_first_is_refused():
    """exactly_once is claimed BEFORE the executor runs, so a second execute that starts while the
    first is still inside the executor (a race, a retry, a callback) finds the slot taken."""
    svc, store, _ = make()
    p = svc.decide(svc.propose(AGENT_P, "update_status", "inv-1", {"status": "reminded"}).id, True, HUMAN_P)
    inner_calls = []

    def outer(action, record_id, params):
        svc.execute(p.id, lambda a, r, prm: inner_calls.append(prm) or {"applied": prm}, HUMAN_P)
        return {"applied": params}

    q = svc.execute(p.id, outer, HUMAN_P)
    assert q.status == EXECUTED
    assert inner_calls == []
    assert any(r["kind"] == "EXECUTION_REFUSED" and '"already_executed"' in r["detail"] for r in store.audit_rows())


class PutFails(Store):
    """Reachable (ping answers) but cannot record — a full disk, a read-only replica, a dropped
    write. An approval that was never recorded must not be reported as an approval."""
    def put_proposal(self, p):
        raise RuntimeError("write failed")


def test_a_proposal_that_cannot_be_recorded_is_denied():
    cfg = PolicyConfig.load(ROOT / "adapters" / "invoices-es" / "permissions.toml")
    svc = PolicyService(cfg, PutFails(":memory:"), Clock())
    p = svc.propose(AGENT_P, "add_note", "inv-1", {"note": "x"})
    assert p.status == DENIED and p.reason.startswith("fail_closed:persist:")


def test_forged_approval_with_store_access_is_detectable_afterwards():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "update_status", "inv-1", {"status": "reminded"})
    p.status = APPROVED
    store.put_proposal(p)                      # root access: the boundary cannot stop this
    svc.execute(p.id, ok_executor, HUMAN_P)
    assert store.audit_orphans() == [p.id]     # but it cannot hide it either
    legit = svc.decide(svc.propose(AGENT_P, "update_status", "inv-2", {"status": "reminded"}).id, True, HUMAN_P)
    svc.execute(legit.id, ok_executor, HUMAN_P)
    assert store.audit_orphans() == [p.id]

