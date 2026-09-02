"""Every test here is paired with a `# CHECK:` block in policy/. `tests/mutate.py` removes each block
and expects at least one of these to fail BY ASSERTION. A test that keeps passing when its check is
deleted is a check that cannot fail; a test that only crashes is a check that was never tested.

Ids: the adapter's `[records]` table says an invoice id looks like `F-2026-031`; every test uses that shape
except the ones about scope."""
import json
import math
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from policy import (AGENT, HUMAN, DENIED, HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN,
                    PolicyConfig, Principal, Store, PolicyService)

ROOT = Path(__file__).resolve().parents[1]
AGENT_P = Principal("assistant", AGENT)
HUMAN_P = Principal("zorion", HUMAN)

DAY = 86400.0
T0 = 1_788_256_800.0   # 2026-09-01 12:00 Europe/Madrid (10:00 UTC), a fixed midday instant


def inv(n: int = 1) -> str:
    return f"F-2026-{n:03d}"


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
        cfg = cfg.replace(daily_writes=daily_writes)
    store = Store(":memory:")
    clock = Clock()
    return PolicyService(cfg, store, clock), store, clock


def ok_executor(action, record_id, params):
    return {"applied": dict(params)}


def held_and_approved(svc, n=1, status="reminded"):
    return svc.decide(svc.propose(AGENT_P, "update_status", inv(n), {"status": status}).id, True, HUMAN_P)


def kinds(store):
    return [r["kind"] for r in store.audit_rows()]


# ── propose ──────────────────────────────────────────────────────────────────────────────────
def test_unknown_action_is_denied():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "transfer_funds", inv(), {})
    assert p.status == DENIED and p.reason == "unknown_action"


def test_case_variant_of_a_denied_action_is_still_unknown_not_allowed():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "Update_Amount", inv(), {"amount": 0})
    assert p.status == DENIED


def test_denied_action_is_denied():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "update_amount", inv(), {"amount": 0.01})
    assert p.status == DENIED and p.reason == "action_denied"


def test_field_smuggling_is_denied():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "send_reminder", inv(), {"reminder_text": "hola", "reminder_channel": "email", "amount": 1})
    assert p.status == DENIED and p.reason.startswith("field_not_permitted:amount")


def test_value_outside_constraint_is_denied():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "update_status", inv(), {"status": "paid"})
    assert p.status == DENIED and p.reason == "value_not_permitted:status"
    p2 = svc.propose(AGENT_P, "send_reminder", inv(), {"reminder_text": "x", "reminder_channel": "whatsapp"})
    assert p2.status == DENIED


def test_consequential_action_is_held_for_a_human():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "update_status", inv(), {"status": "reminded"})
    assert p.status == HELD


def test_non_consequential_action_is_auto_approved_but_audited():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "add_note", inv(), {"note": "llamó el cliente"})
    assert p.status == APPROVED
    assert kinds(store).count("PROPOSAL") == 1


def test_every_proposal_row_names_the_policy_that_decided_it():
    svc, store, _ = make()
    svc.propose(AGENT_P, "add_note", inv(), {"note": "x"})
    row = json.loads(store.audit_rows()[-1]["detail"])
    assert row["policy"] == svc.config.fingerprint()
    assert svc.config.replace(daily_writes=1).fingerprint() != svc.config.fingerprint()


# ── the checks read the copy that is executed, never the caller's object ─────────────────────
class LyingKeys(dict):
    """`keys()`/`items()` say one thing, iteration says another — the shape an outside seat used
    to smuggle a money field past a check that iterated and an executor that copied."""
    def __iter__(self):
        return iter(["note"])


class LyingContains(dict):
    def __contains__(self, k):
        return False


def test_a_params_object_whose_iteration_lies_is_checked_on_what_will_be_executed():
    svc, _, _ = make()
    seen = []
    p = svc.propose(AGENT_P, "add_note", inv(), LyingKeys({"note": "hola", "amount": 0.01, "status": "paid"}))
    assert p.status == DENIED and p.reason.startswith("field_not_permitted:")
    q = svc.propose(AGENT_P, "send_reminder", inv(), LyingContains({"reminder_text": "x", "reminder_channel": "whatsapp"}))
    assert q.status == DENIED and q.reason == "value_not_permitted:reminder_channel"
    svc.execute(p.id, lambda a, r, prm: seen.append(prm) or {"applied": prm}, HUMAN_P)
    assert seen == []


def test_non_mapping_params_are_denied_and_audited_never_raised():
    svc, store, _ = make()
    for bad in (None, ["note"], 7, "note=x"):
        p = svc.propose(AGENT_P, "add_note", inv(), bad)
        assert p.status == DENIED and p.reason == "params_not_a_mapping"
    assert kinds(store).count("PROPOSAL") == 4


def test_a_param_that_cannot_be_written_down_is_denied():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "add_note", inv(), {"note": math.nan})
    assert p.status == DENIED and p.reason.startswith("fail_closed:")


def test_record_id_outside_the_actions_record_scope_is_denied():
    svc, _, _ = make()
    for rid in ("customer:9999;DROP TABLE invoices;--", "inv-1", "", "F-2026-0311", None, 12):
        p = svc.propose(AGENT_P, "add_note", rid, {"note": "x"})
        assert p.status == DENIED and p.reason == "record_shape:invoice", rid
    assert svc.propose(AGENT_P, "add_note", "F-2026-031", {"note": "x"}).status == APPROVED


def test_record_shape_is_the_whole_string():
    svc, _, _ = make()
    assert svc.propose(AGENT_P, "add_note", "F-2026-031\n", {"note": "x"}).status == DENIED
    assert svc.propose(AGENT_P, "add_note", "F-2026-031 ", {"note": "x"}).status == DENIED


def test_an_executed_unknown_counts_against_the_budget():
    svc, _, _ = make(daily_writes=2)
    p = held_and_approved(svc, 1)

    def flaky(action, record_id, params):
        raise RuntimeError("timeout")
    assert svc.execute(p.id, flaky, HUMAN_P).status == EXECUTED_UNKNOWN
    assert svc.propose(AGENT_P, "add_note", inv(2), {"note": "x"}).status == APPROVED
    over = svc.propose(AGENT_P, "add_note", inv(3), {"note": "x"})
    assert over.status == DENIED and over.reason == "budget_exhausted"


def test_record_shape_means_ascii_digits():
    svc, _, _ = make()
    for rid in ("F-\u0662\u0660\u0662\u0666-\u0660\u0663\u0661", "F-\uff11\uff12\uff13\uff14-\uff15\uff16\uff17"):
        assert svc.propose(AGENT_P, "add_note", rid, {"note": "x"}).status == DENIED


def test_an_action_over_an_undeclared_record_type_is_denied():
    svc0, store, clock = make()
    spec = svc0.config.actions["add_note"].__class__(**{**svc0.config.actions["add_note"].__dict__, "record": "customer"})
    svc = PolicyService(svc0.config.replace(actions={**svc0.config.actions, "add_note": spec}), store, clock)
    p = svc.propose(AGENT_P, "add_note", inv(), {"note": "x"})
    assert p.status == DENIED and p.reason == "record_shape:customer"


def test_the_services_bindings_cannot_be_swapped():
    """config, clock, fuse and store are fixed at construction: a holder of the service cannot
    re-spec a denied action, stretch the day, or replace a tripped fuse with an inert one."""
    svc, store, clock = make(daily_writes=1)
    for name, value in (("config", svc.config.deny_all()), ("clock", lambda: 0.0), ("fuse", object()), ("store", Store())):
        with pytest.raises(AttributeError):
            setattr(svc, name, value)
    with pytest.raises(AttributeError):
        svc.anything = 1
    assert svc.propose(AGENT_P, "update_amount", inv(), {"amount": 1}).status == DENIED


def test_params_and_evidence_are_bounded():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "add_note", inv(), {"note": "x" * (70 * 1024)})
    assert p.status == DENIED and p.reason == "params_too_large"
    q = svc.propose(AGENT_P, "add_note", inv(), {"note": "x"}, evidence="y" * 5000)
    assert q.status == DENIED and q.reason == "params_too_large"
    assert kinds(store).count("PROPOSAL") == 2


def test_the_models_justification_is_in_the_audit_row():
    svc, store, _ = make()
    svc.propose(AGENT_P, "update_amount", inv(), {"amount": 0}, evidence="el proveedor lo ha aceptado")
    assert json.loads(store.audit_rows()[-1]["detail"])["evidence"] == "el proveedor lo ha aceptado"


def nest(depth: int) -> list:
    deep: list = []
    cur = deep
    for _ in range(depth):
        nxt: list = []
        cur.append(nxt)
        cur = nxt
    return deep


def test_a_nested_param_value_is_denied_and_still_leaves_a_row():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "add_note", inv(), {"note": nest(50)})
    assert p.status == DENIED and p.reason == "param_not_scalar:note"
    q = svc.propose(AGENT_P, "add_note", inv(), {"note": nest(50_000)})        # deep enough to strain the encoder
    assert q.status == DENIED and q.reason in ("param_not_scalar:note", "params_too_large") or q.reason.startswith("fail_closed:")
    assert kinds(store).count("PROPOSAL") == 2


def test_the_configuration_cannot_be_edited_in_place():
    svc, _, _ = make()
    with pytest.raises(Exception):
        svc.config.daily_writes = 10 ** 6
    with pytest.raises(TypeError):
        svc.config.actions["update_amount"] = svc.config.actions["add_note"]
    with pytest.raises(Exception):
        svc.config.actions["add_note"].deny = True


# ── decide ───────────────────────────────────────────────────────────────────────────────────
def test_agent_cannot_decide():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "update_status", inv(), {"status": "reminded"})
    q = svc.decide(p.id, True, AGENT_P)
    assert q.status == HELD
    assert "DECISION_REFUSED" in kinds(store)


def test_human_can_approve_and_reject():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "update_status", inv(1), {"status": "reminded"})
    assert svc.decide(p.id, True, HUMAN_P).status == APPROVED
    p2 = svc.propose(AGENT_P, "update_status", inv(2), {"status": "disputed"})
    assert svc.decide(p2.id, False, HUMAN_P, note="no").status == REJECTED


def test_only_held_proposals_can_be_decided():
    svc, _, _ = make()
    p = svc.propose(AGENT_P, "update_amount", inv(), {"amount": 1})   # denied
    assert svc.decide(p.id, True, HUMAN_P).status == DENIED
    q = svc.propose(AGENT_P, "add_note", inv(), {"note": "x"})        # auto-approved
    assert svc.decide(q.id, False, HUMAN_P).status == APPROVED           # cannot be flipped after the fact


# ── execute ──────────────────────────────────────────────────────────────────────────────────
def test_execute_only_approved():
    svc, store, _ = make()
    held = svc.propose(AGENT_P, "update_status", inv(1), {"status": "reminded"})
    calls = []
    assert svc.execute(held.id, lambda a, r, p: calls.append(1) or {"applied": p}, HUMAN_P).status == HELD
    rejected = svc.decide(svc.propose(AGENT_P, "update_status", inv(2), {"status": "reminded"}).id, False, HUMAN_P)
    assert svc.execute(rejected.id, lambda a, r, p: calls.append(1) or {"applied": p}, HUMAN_P).status == REJECTED
    assert calls == []


def test_execute_exactly_once():
    svc, _, _ = make()
    p = held_and_approved(svc)
    calls = []
    svc.execute(p.id, lambda a, r, prm: calls.append(1) or {"applied": prm}, HUMAN_P)
    svc.execute(p.id, lambda a, r, prm: calls.append(1) or {"applied": prm}, HUMAN_P)
    assert calls == [1]


def test_execute_uses_the_approved_params_not_the_callers_object():
    svc, _, _ = make()
    p = held_and_approved(svc)
    p.params["status"] = "paid"                       # an attacker edits the object it holds
    seen = {}
    svc.execute(p.id, lambda a, r, prm: seen.update(prm) or {"applied": prm}, HUMAN_P)
    assert seen == {"status": "reminded"}


def test_revalidation_flags_a_mismatch_loudly():
    svc, store, _ = make()
    p = held_and_approved(svc, 1)
    q = svc.execute(p.id, lambda a, r, prm: {"applied": {"status": "paid"}}, HUMAN_P)
    assert q.status == EXECUTED_MISMATCH
    assert "EXECUTION_MISMATCH" in kinds(store)
    good = held_and_approved(svc, 2)
    assert svc.execute(good.id, ok_executor, HUMAN_P).status == EXECUTED


class LyingEq(dict):
    def __eq__(self, other):
        return True


def test_revalidation_compares_what_was_written_down_not_what_the_object_claims():
    svc, _, _ = make()
    p = held_and_approved(svc)
    q = svc.execute(p.id, lambda a, r, prm: {"applied": LyingEq({"status": "paid"})}, HUMAN_P)
    assert q.status == EXECUTED_MISMATCH


def test_an_attempt_row_is_committed_before_the_executor_runs():
    svc, store, _ = make()
    p = held_and_approved(svc)
    seen_at_call = []

    def executor(action, record_id, params):
        seen_at_call.append([r for r in store.audit_rows() if r["kind"] == "EXECUTION_ATTEMPTED" and r["proposal_id"] == p.id])
        return {"applied": params}

    assert svc.execute(p.id, executor, HUMAN_P).status == EXECUTED
    assert len(seen_at_call[0]) == 1
    assert json.loads(seen_at_call[0][0]["detail"])["action"] == "update_status"


def test_an_executor_that_writes_and_then_raises_leaves_a_row_and_an_unknown_status():
    svc, store, _ = make()
    p = held_and_approved(svc)
    side_effects = []

    def flaky(action, record_id, params):
        side_effects.append(params)
        raise RuntimeError("broker timed out after commit")

    q = svc.execute(p.id, flaky, HUMAN_P)
    assert q.status == EXECUTED_UNKNOWN
    assert store.get_proposal(p.id).status == EXECUTED_UNKNOWN
    assert kinds(store)[-2:] == ["EXECUTION_ATTEMPTED", "EXECUTION_UNKNOWN"]
    assert "RuntimeError" in store.audit_rows()[-1]["detail"]
    assert store.audit_anomalies() == []          # attempted AND concluded: the layer knows it does not know
    assert svc.execute(p.id, ok_executor, HUMAN_P).status == EXECUTED_UNKNOWN   # and it is not retried blindly


def test_a_non_json_effect_is_recorded_not_lost():
    svc, store, _ = make()
    p = held_and_approved(svc)
    q = svc.execute(p.id, lambda a, r, prm: {"applied": prm, "raw": object()}, HUMAN_P)
    assert q.status == EXECUTED
    effect = store._c().execute("SELECT effect FROM executions WHERE proposal_id = ?", (p.id,)).fetchone()[0]
    assert "object at 0x" in effect
    q2 = svc.execute(held_and_approved(svc, 2).id, lambda a, r, prm: object(), HUMAN_P)
    assert q2.status == EXECUTED_MISMATCH


def test_an_effect_that_cannot_be_written_down_is_a_mismatch_not_a_raise():
    svc, store, _ = make()
    p = held_and_approved(svc)
    circular: dict = {}
    circular["self"] = circular
    q = svc.execute(p.id, lambda a, r, prm: {"applied": circular}, HUMAN_P)
    assert q.status == EXECUTED_MISMATCH
    assert store.audit_anomalies() == []


def test_a_process_that_dies_inside_the_executor_shows_as_an_execution_without_outcome():
    svc, store, _ = make()
    p = held_and_approved(svc)

    def dies(action, record_id, params):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        svc.execute(p.id, dies, HUMAN_P)
    assert ("execution_without_outcome", p.id) in store.audit_anomalies()


# ── fuse ─────────────────────────────────────────────────────────────────────────────────────
def test_agent_can_trip_but_not_clear_the_fuse():
    svc, _, clock = make()
    svc.fuse.trip("agent saw something wrong", AGENT_P)
    assert svc.fuse.is_tripped()
    clock.advance(2 * DAY)
    assert svc.fuse.clear(AGENT_P) is False
    assert svc.fuse.is_tripped()


def test_human_cannot_clear_the_fuse_the_same_day_but_can_the_next():
    svc, _, clock = make()
    svc.fuse.trip("drill", HUMAN_P)
    clock.advance(3600)
    assert svc.fuse.clear(HUMAN_P) is False
    clock.advance(DAY)
    assert svc.fuse.clear(HUMAN_P) is True
    assert not svc.fuse.is_tripped()


def test_the_fuse_keeps_its_own_clock():
    """No caller chooses the time of a trip, so nobody can trip it 'a hundred years from now'."""
    svc, _, clock = make()
    with pytest.raises(TypeError):
        svc.fuse.trip("x", AGENT_P, clock() + 100 * 365 * DAY)
    svc.fuse.trip("x", AGENT_P)
    clock.advance(DAY + 1)
    assert svc.fuse.clear(HUMAN_P) is True


def test_tripped_fuse_blocks_proposals_and_executions():
    svc, _, clock = make()
    p = held_and_approved(svc)
    svc.fuse.trip("stop", HUMAN_P)
    assert svc.propose(AGENT_P, "add_note", inv(), {"note": "x"}).status == DENIED
    calls = []
    assert svc.execute(p.id, lambda a, r, prm: calls.append(1) or {"applied": prm}, HUMAN_P).status == APPROVED
    assert calls == []


def test_daily_budget_trips_the_fuse_and_only_a_human_clears_it_tomorrow():
    svc, store, clock = make(daily_writes=3)
    for i in range(3):
        assert svc.propose(AGENT_P, "add_note", inv(i), {"note": "x"}).status == APPROVED
    over = svc.propose(AGENT_P, "add_note", inv(9), {"note": "x"})
    assert over.status == DENIED and over.reason == "budget_exhausted"
    assert svc.fuse.is_tripped()
    assert "FUSE_TRIPPED" in kinds(store)          # the trip survives the denial: it was not rolled back
    clock.advance(DAY + 60)
    assert svc.fuse.clear(HUMAN_P) is True
    assert svc.propose(AGENT_P, "add_note", inv(10), {"note": "x"}).status == APPROVED


def test_a_human_rejection_still_consumes_the_days_budget():
    svc, _, _ = make(daily_writes=3)
    for i in range(3):
        p = svc.propose(AGENT_P, "update_status", inv(i), {"status": "reminded"})
        assert svc.decide(p.id, False, HUMAN_P).status == REJECTED
    p = svc.propose(AGENT_P, "update_status", inv(9), {"status": "reminded"})
    assert p.status == DENIED and p.reason == "budget_exhausted"


def test_denied_proposals_do_not_consume_the_budget():
    svc, _, _ = make(daily_writes=2)
    for i in range(50):
        assert svc.propose(AGENT_P, "update_amount", inv(i), {"amount": 1}).status == DENIED
    assert svc.propose(AGENT_P, "add_note", inv(), {"note": "x"}).status == APPROVED


def test_concurrent_proposals_cannot_race_past_the_budget():
    svc, _, _ = make(daily_writes=2)
    results, barrier = [], threading.Barrier(6)

    def go(i):
        barrier.wait()
        results.append(svc.propose(AGENT_P, "add_note", inv(i), {"note": "x"}).status)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(APPROVED) == 2 and results.count(DENIED) == 4


class CountThenPause(Store):
    """Counts, then lets another thread in before writing — the interleaving that defeats a
    read-then-write budget unless the count and the write are one transaction."""
    def __init__(self):
        super().__init__(":memory:")
        self.gate = threading.Event()
        self.paused = False

    def count_proposals_since(self, *a, **kw):
        n = super().count_proposals_since(*a, **kw)
        if not self.paused:
            self.paused = True
            self.gate.wait(timeout=2)
        return n


def test_the_budget_count_and_the_write_are_one_transaction():
    cfg = PolicyConfig.load(ROOT / "adapters" / "invoices-es" / "permissions.toml").replace(daily_writes=1)
    store = CountThenPause()
    svc = PolicyService(cfg, store, Clock())
    statuses = []
    a = threading.Thread(target=lambda: statuses.append(svc.propose(AGENT_P, "add_note", inv(1), {"note": "a"}).status))
    b = threading.Thread(target=lambda: statuses.append(svc.propose(AGENT_P, "add_note", inv(2), {"note": "b"}).status))
    a.start()
    deadline = time.time() + 2
    while not store.paused and time.time() < deadline:     # a is inside its count (or, mutated, never counts)
        time.sleep(0.001)
    b.start()                                   # b arrives while a has counted 0 and not yet written
    b.join(timeout=0.3)
    assert b.is_alive()                         # b is waiting for a's transaction, not counting
    store.gate.set()
    a.join(); b.join()
    assert sorted(statuses) == sorted([APPROVED, DENIED])


def test_per_action_daily_max():
    svc, _, _ = make(daily_writes=100)
    for i in range(10):
        assert svc.propose(AGENT_P, "send_reminder", inv(i), {"reminder_text": "x", "reminder_channel": "email"}).status == HELD
    p = svc.propose(AGENT_P, "send_reminder", inv(11), {"reminder_text": "x", "reminder_channel": "email"})
    assert p.status == DENIED and p.reason == "action_daily_max"


# ── fail closed ──────────────────────────────────────────────────────────────────────────────
class PingFails(Store):
    def ping(self):
        raise RuntimeError("policy state unreachable")


def test_unreachable_policy_state_denies_instead_of_allowing():
    cfg = PolicyConfig.load(ROOT / "adapters" / "invoices-es" / "permissions.toml")
    svc = PolicyService(cfg, PingFails(":memory:"), Clock())
    p = svc.propose(AGENT_P, "add_note", inv(), {"note": "x"})
    assert p.status == DENIED and p.reason.startswith("fail_closed:")


def test_closed_store_denies_and_does_not_raise():
    svc, store, _ = make()
    store.close()
    p = svc.propose(AGENT_P, "add_note", inv(), {"note": "x"})
    assert p.status == DENIED and p.reason.startswith("fail_closed:")


class PutFails(Store):
    """Reachable (ping answers) but cannot record — a full disk, a read-only replica, a dropped
    write. An approval that was never recorded must not be reported as an approval."""
    def put_proposal(self, p):
        raise RuntimeError("write failed")


def test_a_proposal_that_cannot_be_recorded_is_denied():
    cfg = PolicyConfig.load(ROOT / "adapters" / "invoices-es" / "permissions.toml")
    svc = PolicyService(cfg, PutFails(":memory:"), Clock())
    p = svc.propose(AGENT_P, "add_note", inv(), {"note": "x"})
    assert p.status == DENIED and p.reason.startswith("fail_closed:persist:")


def test_reentrant_execution_during_the_first_is_refused():
    """exactly_once is claimed BEFORE the executor runs, so a second execute that starts while the
    first is still inside the executor (a race, a retry, a callback) finds the slot taken."""
    svc, store, _ = make()
    p = held_and_approved(svc)
    inner_calls = []

    def outer(action, record_id, params):
        svc.execute(p.id, lambda a, r, prm: inner_calls.append(prm) or {"applied": prm}, HUMAN_P)
        return {"applied": params}

    q = svc.execute(p.id, outer, HUMAN_P)
    assert q.status == EXECUTED
    assert inner_calls == []
    assert any(r["kind"] == "EXECUTION_REFUSED" and '"already_executed"' in r["detail"] for r in store.audit_rows())


# ── audit: the chain, the head, the anchor ───────────────────────────────────────────────────
def chain(svc, n=3):
    for i in range(n):
        svc.propose(AGENT_P, "add_note", inv(i), {"note": "x"})


def test_audit_has_no_update_or_delete_and_an_edited_row_breaks_the_chain():
    svc, store, _ = make()
    assert not any(n.startswith(("audit_update", "audit_delete", "audit_remove")) for n in dir(store))
    chain(svc)
    assert store.audit_verify() is True
    store._c().execute("UPDATE audit SET detail = ? WHERE seq = 1", ('{"status": "approved"}',))
    assert store.audit_verify() is False


def test_a_relinked_prev_hash_breaks_the_chain():
    svc, store, _ = make()
    chain(svc)
    store._c().execute("UPDATE audit SET prev_hash = ? WHERE seq = 2", ("f" * 64,))
    assert store.audit_verify() is False


def test_a_deleted_middle_row_breaks_the_chain_even_if_root_relinks_it():
    svc, store, _ = make()
    chain(svc)
    rows = store.audit_rows()
    store._c().execute("DELETE FROM audit WHERE seq = 2")
    h = store._hash(rows[0]["hash"], rows[2]["seq"], rows[2]["ts"], rows[2]["kind"], rows[2]["principal"], rows[2]["proposal_id"], rows[2]["detail"])
    store._c().execute("UPDATE audit SET prev_hash = ?, hash = ? WHERE seq = 3", (rows[0]["hash"], h))
    store._c().execute("UPDATE audit_head SET seq = 3, hash = ?", (h,))
    assert store.audit_verify() is False        # the numbering has a hole


def test_a_deleted_tail_row_breaks_the_chain():
    svc, store, _ = make()
    chain(svc)
    store._c().execute("DELETE FROM audit WHERE seq = (SELECT MAX(seq) FROM audit)")
    assert store.audit_verify() is False


def test_a_truncated_and_reheaded_chain_is_caught_only_by_the_published_anchor():
    """The honest limit, asserted: root that truncates the tail AND rewrites the head passes a bare
    verify. It fails against the head the operator published before the truncation."""
    svc, store, _ = make()
    chain(svc)
    anchor = store.audit_head()
    rows = store.audit_rows()
    store._c().execute("DELETE FROM audit WHERE seq = (SELECT MAX(seq) FROM audit)")
    store._c().execute("UPDATE audit_head SET seq = ?, hash = ?", (rows[-2]["seq"], rows[-2]["hash"]))
    assert store.audit_verify() is True
    assert store.audit_verify(anchor=anchor) is False
    assert store.audit_verify(anchor=(rows[0]["seq"], rows[0]["hash"])) is True


def test_every_path_leaves_an_audit_row():
    svc, store, clock = make()
    n0 = len(store.audit_rows())
    svc.propose(AGENT_P, "nope", inv(), {})                                                    # denied
    held = svc.propose(AGENT_P, "update_status", inv(), {"status": "reminded"})               # held
    svc.decide(held.id, True, AGENT_P)                                                         # refused
    svc.decide(held.id, True, HUMAN_P)                                                         # decision
    svc.execute(held.id, ok_executor, HUMAN_P)                                                 # attempted + executed
    svc.execute(held.id, ok_executor, HUMAN_P)                                                 # refused
    assert len(store.audit_rows()) - n0 == 7


# ── audit anomalies: what a root write looks like afterwards ─────────────────────────────────
def test_forged_approval_with_store_access_is_detectable_afterwards():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "update_status", inv(1), {"status": "reminded"})
    p.status = APPROVED
    store.put_proposal(p)                      # root access: the boundary cannot stop this
    svc.execute(p.id, ok_executor, HUMAN_P)
    assert store.audit_orphans() == [p.id]     # but it shows
    legit = held_and_approved(svc, 2)
    svc.execute(legit.id, ok_executor, HUMAN_P)
    assert store.audit_orphans() == [p.id]


def test_a_rejection_forged_into_an_approval_is_an_orphan():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "update_status", inv(), {"status": "reminded"})
    svc.decide(p.id, False, HUMAN_P)
    p = store.get_proposal(p.id)
    p.status = APPROVED
    store.put_proposal(p)
    assert svc.execute(p.id, ok_executor, HUMAN_P).status == EXECUTED
    assert store.audit_orphans() == [p.id]


def test_an_action_swapped_under_an_approved_id_is_an_anomaly():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "add_note", inv(), {"note": "hola"})     # auto-approved
    p.action = "send_to_external"
    store.put_proposal(p)
    calls = []
    svc.execute(p.id, lambda a, r, prm: calls.append(a) or {"applied": prm}, HUMAN_P)
    assert calls == ["send_to_external"]                                # root ran it
    assert ("executed_action_mismatch", p.id) in store.audit_anomalies()
    assert json.loads(store.audit_rows()[-1]["detail"])["action"] == "send_to_external"   # and the row says which


def test_a_decision_row_not_written_by_a_human_is_an_anomaly():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "update_status", inv(), {"status": "reminded"})
    store.audit_append("DECISION", AGENT_P.tag, p.id, {"approve": True, "note": ""})
    assert ("decision_not_by_human", p.id) in store.audit_anomalies()


def test_a_fuse_cleared_by_a_non_human_through_the_store_is_an_anomaly():
    svc, store, _ = make()
    svc.fuse.trip("x", HUMAN_P)
    store.fuse_set("FUSE_CLEARED", AGENT_P.tag, {}, T0 + DAY, tripped=False)
    assert not svc.fuse.is_tripped()
    assert ("fuse_cleared_not_by_human", AGENT_P.tag) in store.audit_anomalies()


def test_a_fuse_cleared_the_same_day_through_the_store_is_an_anomaly_whatever_it_is_labelled():
    svc, store, _ = make()
    svc.fuse.trip("x", HUMAN_P)
    store.fuse_set("FUSE_CLEARED", "human:owner", {}, T0 + 60, tripped=False)      # the label is the attacker's
    assert "fuse_cleared_same_day" in {c for c, _ in store.audit_anomalies()}


def test_a_trip_stamped_in_the_future_through_the_store_cannot_lock_the_human_out():
    svc, store, clock = make()
    store.fuse_set("FUSE_TRIPPED", "agent:assistant", {"reason": "never"}, T0 + 100 * 365 * DAY, tripped=True, reason="never")
    assert svc.fuse.is_tripped()
    assert svc.fuse.clear(AGENT_P) is False           # still never the agent
    assert svc.fuse.clear(HUMAN_P) is True            # a trip from the future binds nobody
    assert "audit_time_not_monotonic" in {c for c, _ in store.audit_anomalies()}


def test_a_fuse_flipped_by_raw_sql_is_an_anomaly():
    svc, store, _ = make()
    svc.fuse.trip("x", HUMAN_P)
    store._c().execute("UPDATE fuse SET tripped = 0 WHERE id = 1")
    assert not svc.fuse.is_tripped()
    assert ("fuse_state_mismatch", "fuse") in store.audit_anomalies()
    assert store.audit_verify() is True         # the chain is intact; the STATE disagrees with it


def test_an_execution_claimed_without_an_attempt_row_is_an_anomaly():
    svc, store, _ = make()
    assert store.claim_execution("ghost", T0)
    assert ("execution_claim_unaudited", "ghost") in store.audit_anomalies()


def test_an_appended_decision_cannot_launder_a_forged_approval():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "update_status", inv(), {"status": "reminded"})
    svc.decide(p.id, False, HUMAN_P)
    q = store.get_proposal(p.id); q.status = APPROVED; store.put_proposal(q)
    svc.execute(p.id, ok_executor, HUMAN_P)
    store.audit_append("DECISION", "human:owner", p.id, {"approve": True, "note": ""})      # the launder
    codes = {c for c, i in store.audit_anomalies() if i == p.id}
    assert "decision_row_count" in codes and "decision_out_of_order" in codes


def test_an_appended_proposal_row_cannot_launder_a_swapped_action():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "add_note", inv(), {"note": "hola"})
    p.action = "send_to_external"; store.put_proposal(p)
    svc.execute(p.id, lambda a, r, prm: {"applied": prm}, HUMAN_P)
    store.audit_append("PROPOSAL", AGENT_P.tag, p.id, {"status": "approved", "action": "send_to_external", "record_id": inv(), "params": {"note": "hola"}})
    assert ("proposal_row_count", p.id) in store.audit_anomalies()


def test_a_forged_approval_laundered_with_an_appended_decision_is_still_an_anomaly():
    svc, store, _ = make()
    p = svc.propose(AGENT_P, "update_status", inv(), {"status": "reminded"})
    p.status = APPROVED; store.put_proposal(p)
    svc.execute(p.id, ok_executor, HUMAN_P)
    store.audit_append("DECISION", "human:owner", p.id, {"approve": True, "note": ""})
    assert ("decision_out_of_order", p.id) in store.audit_anomalies()


def test_a_replayed_execution_with_a_clean_chain_shows_in_the_row_count():
    svc, store, _ = make()
    p = held_and_approved(svc)
    svc.execute(p.id, ok_executor, HUMAN_P)
    q = store.get_proposal(p.id); q.status = APPROVED; store.put_proposal(q)
    store._c().execute("DELETE FROM executions WHERE proposal_id = ?", (p.id,))
    svc.execute(p.id, ok_executor, HUMAN_P)
    assert ("execution_row_count", p.id) in store.audit_anomalies()


def test_a_clean_run_has_no_anomalies():
    svc, store, clock = make(daily_writes=5)
    p = held_and_approved(svc, 1)
    svc.execute(p.id, ok_executor, HUMAN_P)
    svc.propose(AGENT_P, "add_note", inv(2), {"note": "x"})
    svc.fuse.trip("drill", AGENT_P)
    clock.advance(DAY + 1)
    svc.fuse.clear(HUMAN_P)
    assert store.audit_anomalies() == []
    older = (2, store.audit_rows()[1]["hash"])
    assert store.audit_verify(anchor=older) is True
    assert store.audit_verify(anchor=(2, "f" * 64)) is False
