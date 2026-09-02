#!/usr/bin/env python3
"""Hostile self-test: an attacker who holds the same Python objects the application holds tries to
get a write through the policy layer without a human approving it, or to make one that happened
disappear. Every scored attempt must fail; the script prints one line per attempt and exits 1 if
any scored attempt succeeded. An attempt is scored only if it asserts the property it names — an
attempt that cannot fail is not counted (that was this file's own defect on 2026-09-01: one
attempt returned "blocked" unconditionally, and sabotaging the detector still printed 10/10).

Attempts b1–b16 re-author, in this repo's own words, the classes an outside refutation seat used
to get 17 of 19 attempts through the first version of this layer (the seat's script was not
copied: a fix graded by the attacker's own script is co-authorship).

This is NOT the red-team of the model (that is redteam/, with injections planted in records). This
is the red-team of the boundary itself, with no model in the loop.

What this file cannot test, by construction — README §Trust boundary: a caller that mints
`Principal("owner", HUMAN)` IS a human as far as this layer knows. Identity comes from the host.
"""
from __future__ import annotations

import math
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from policy import (AGENT, HUMAN, APPROVED, EXECUTED, EXECUTED_UNKNOWN, HELD, DENIED,  # noqa: E402
                    PolicyConfig, Principal, Store, PolicyService)

AGENT_P = Principal("assistant", AGENT)
HUMAN_P = Principal("owner", HUMAN)
FAKE_HUMAN = Principal("owner", AGENT)           # same id as the human, wrong kind
T0 = 1_788_256_800.0
DAY = 86400.0
INV = "F-2026-031"


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def fresh(daily_writes=None):
    cfg = PolicyConfig.load(ROOT / "adapters" / "invoices-es" / "permissions.toml")
    if daily_writes is not None:
        cfg = cfg.replace(daily_writes=daily_writes)
    return PolicyService(cfg, Store(":memory:"), Clock())


def rec(w):
    """An executor that records what it was asked to write."""
    return lambda a, r, prm: w.append(prm) or {"applied": prm}


SCORED: list[bool] = []


def attempt(name, fn):
    """fn returns a list of breaches (executor writes, or named properties that failed). Empty = blocked."""
    try:
        breaches = fn()
        ok = len(breaches) == 0
        print(f"  {'BLOCKED' if ok else 'BREACH '}  {name}" + ("" if ok else f"  {breaches}"))
    except Exception as e:  # noqa: BLE001 — an exception is a block, not a breach, but is reported
        ok = True
        print(f"  BLOCKED  {name}  (raised {type(e).__name__}: {e})")
    SCORED.append(ok)


def main() -> int:
    # ── the original ten ─────────────────────────────────────────────────────────────────────
    def a1():
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
        svc.decide(p.id, True, FAKE_HUMAN)
        svc.execute(p.id, rec(w), AGENT_P)
        return w
    attempt("spoof the human's id with an agent principal", a1)

    def a2():
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
        p.status = APPROVED
        svc.execute(p.id, rec(w), AGENT_P)
        return w
    attempt("flip the held object's status in memory", a2)

    def a3():
        svc = fresh(); w = []
        for name in ("update_amount", "UPDATE_AMOUNT", "update-amount", "update_amount ", "update_amount"):
            p = svc.propose(AGENT_P, name, INV, {"amount": 0})
            svc.execute(p.id, rec(w), AGENT_P)
        return w
    attempt("denied action under five spellings", a3)

    def a4():
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "add_note", INV, {"note": "x", "amount": 0})
        svc.execute(p.id, rec(w), AGENT_P)
        return w
    attempt("smuggle `amount` inside an auto-approved note", a4)

    def a5():
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
        svc.decide(p.id, True, HUMAN_P)
        p.params["status"] = "paid"
        svc.execute(p.id, rec(w), AGENT_P)
        return [x for x in w if x != {"status": "reminded"}]
    attempt("change the params after approval (TOCTOU)", a5)

    def a6():
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
        svc.decide(p.id, True, HUMAN_P)
        for _ in range(5):
            svc.execute(p.id, rec(w), AGENT_P)
        return w[1:]
    attempt("replay one approval five times", a6)

    def a7():
        svc = fresh(); w = []
        svc.fuse.trip("x", HUMAN_P)
        svc.clock.t += 10 * DAY
        svc.fuse.clear(AGENT_P)
        p = svc.propose(AGENT_P, "add_note", INV, {"note": "x"})
        svc.execute(p.id, rec(w), AGENT_P)
        return w
    attempt("clear a tripped fuse as the agent", a7)

    def a8():
        svc = fresh(daily_writes=3); w = []
        for i in range(50):
            svc.propose(AGENT_P, "update_amount", INV, {"amount": 1})
        p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
        svc.execute(p.id, rec(w), AGENT_P)
        # the property: denied proposals cost nothing, so the real one is HELD (not denied, not executed)
        return w + ([] if p.status == HELD else [f"status={p.status}"])
    attempt("spam denied proposals, then a real one is still held for the human", a8)

    def a9():
        svc = fresh(); w = []
        try:
            svc.execute("00000000000000000000000000000000", rec(w), AGENT_P)
        except KeyError:
            pass
        return w
    attempt("execute a proposal id that does not exist", a9)

    def a10():
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
        p.status = APPROVED
        svc.store.put_proposal(p)     # root: this write CANNOT be stopped. It must SHOW.
        svc.execute(p.id, rec(w), AGENT_P)
        return [] if svc.store.audit_orphans() == [p.id] else ["forged approval executed and left no orphan"]
    attempt("forge an approval with store access — it must show as an orphan", a10)

    # ── the seat's classes, re-authored ──────────────────────────────────────────────────────
    class LyingIter(dict):
        def __iter__(self):
            return iter(["note"])

    class LyingContains(dict):
        def __contains__(self, k):
            return False

    def b1():
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "add_note", INV, LyingIter({"note": "hola", "amount": 0.01, "status": "paid"}))
        svc.execute(p.id, rec(w), AGENT_P)
        return w
    attempt("params whose iteration hides the smuggled fields", b1)

    def b2():
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "send_reminder", INV, LyingContains({"reminder_text": "x", "reminder_channel": "whatsapp"}))
        svc.decide(p.id, True, HUMAN_P)
        svc.execute(p.id, rec(w), AGENT_P)
        return w + ([] if p.status == DENIED else [f"status={p.status} reason={p.reason!r}"])
    attempt("params whose `in` lies so the value constraint is skipped", b2)

    def b3():
        svc = fresh()
        for i in range(3):
            svc.propose(AGENT_P, "add_note", INV, {"note": "x"})
        svc.store._c().execute("DELETE FROM audit WHERE seq = (SELECT MAX(seq) FROM audit)")
        return [] if svc.store.audit_verify() is False else ["tail deleted, chain still verifies"]
    attempt("delete the tail row of the audit chain — it must break verification", b3)

    def b4():
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
        svc.decide(p.id, False, HUMAN_P)
        q = svc.store.get_proposal(p.id); q.status = APPROVED; svc.store.put_proposal(q)
        svc.execute(p.id, rec(w), AGENT_P)
        return [] if p.id in svc.store.audit_orphans() else ["human rejected, forged to approved, not an orphan"]
    attempt("forge a human's rejection into an approval — it must show as an orphan", b4)

    def b5():
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "add_note", INV, {"note": "hola"})
        p.action = "send_to_external"; svc.store.put_proposal(p)
        svc.execute(p.id, lambda a, r, prm: w.append(a) or {"applied": prm}, AGENT_P)
        return [] if ("executed_action_mismatch", p.id) in svc.store.audit_anomalies() else ["denied action ran under an approved id, unseen"]
    attempt("swap the action under an auto-approved id — it must show as an anomaly", b5)

    def b6():
        svc = fresh(); side = []

        def writes_then_raises(a, r, prm):
            side.append(prm); raise RuntimeError("timeout after commit")
        p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
        svc.decide(p.id, True, HUMAN_P)
        q = svc.execute(p.id, writes_then_raises, AGENT_P)
        rows = [r["kind"] for r in svc.store.audit_rows() if r["proposal_id"] == p.id]
        bad = []
        if "EXECUTION_ATTEMPTED" not in rows or "EXECUTION_UNKNOWN" not in rows:
            bad.append(f"a real write left rows {rows}")
        if q.status != EXECUTED_UNKNOWN:
            bad.append(f"status={q.status}")
        return bad
    attempt("executor writes and then raises — the write must leave a trace", b6)

    def b7():
        svc = fresh()
        p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
        svc.decide(p.id, True, HUMAN_P)
        q = svc.execute(p.id, lambda a, r, prm: {"applied": prm, "handle": object()}, AGENT_P)
        rows = [r["kind"] for r in svc.store.audit_rows() if r["proposal_id"] == p.id]
        return [] if q.status == EXECUTED and "EXECUTED" in rows else [f"status={q.status} rows={rows}"]
    attempt("executor returns an effect JSON cannot encode — it must still be recorded", b7)

    def b8():
        svc = fresh(); w = []
        svc.fuse.trip("stop", HUMAN_P)
        svc.store.fuse_set("FUSE_CLEARED", AGENT_P.tag, {}, svc.clock(), tripped=0)     # the store primitive, as the agent
        p = svc.propose(AGENT_P, "add_note", INV, {"note": "x"}); svc.execute(p.id, rec(w), AGENT_P)
        seen = ("fuse_cleared_not_by_human", AGENT_P.tag) in svc.store.audit_anomalies()
        return [] if seen else ["fuse cleared through the store by the agent, unseen"]
    attempt("clear the fuse through the store primitive as the agent — it must show", b8)

    def b9():
        svc = fresh()
        svc.fuse.trip("stop", HUMAN_P)
        svc.store._c().execute("UPDATE fuse SET tripped = 0 WHERE id = 1")
        seen = ("fuse_state_mismatch", "fuse") in svc.store.audit_anomalies()
        return [] if seen else ["fuse flipped by raw SQL, state and chain agree"]
    attempt("flip the fuse with raw SQL — state must disagree with the chain", b9)

    def b10():
        svc = fresh()
        try:
            svc.fuse.trip("never clear me", AGENT_P, svc.clock() + 100 * 365 * DAY)
            return ["caller chose the trip time"]
        except TypeError:
            pass
        svc.fuse.trip("x", AGENT_P)
        svc.clock.t += DAY + 1
        return [] if svc.fuse.clear(HUMAN_P) else ["human locked out of the clear path"]
    attempt("trip the fuse 'a hundred years from now' so the human can never clear it", b10)

    class LyingEq(dict):
        def __eq__(self, other):
            return True

    def b11():
        svc = fresh()
        p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
        svc.decide(p.id, True, HUMAN_P)
        q = svc.execute(p.id, lambda a, r, prm: {"applied": LyingEq({"status": "paid"})}, AGENT_P)
        return [] if q.status != EXECUTED else ["a lying __eq__ passed re-validation"]
    attempt("executor reports `applied` with a lying __eq__", b11)

    def b12():
        svc = fresh(); w = []
        for rid in ("customer:9999;DROP TABLE invoices;--", "inv-1", "F-2026-0311", "", None):
            p = svc.propose(AGENT_P, "add_note", rid, {"note": "x"})
            svc.execute(p.id, rec(w), AGENT_P)
        return w
    attempt("write to a record id outside the action's declared scope", b12)

    def b13():
        svc = fresh(); w = []
        spec = svc.config.actions["add_note"]
        svc.config.actions["update_amount"] = spec       # re-spec a deny=true action at runtime
        p = svc.propose(AGENT_P, "update_amount", INV, {"note": "x"})
        svc.execute(p.id, rec(w), AGENT_P)
        return w
    attempt("re-spec a denied action in the live configuration", b13)

    def b14():
        svc = fresh(daily_writes=2); statuses = []; barrier = threading.Barrier(6)

        def go(i):
            barrier.wait()
            statuses.append(svc.propose(AGENT_P, "add_note", INV, {"note": "x"}).status)
        ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
        [t.start() for t in ts]; [t.join() for t in ts]
        n = statuses.count(APPROVED)
        return [] if n <= 2 else [f"{n} of 6 approved against a budget of 2"]
    attempt("six concurrent proposals against a budget of two", b14)

    def b15():
        svc = fresh(daily_writes=3); held = 0
        for i in range(40):
            p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
            if p.status == HELD:
                held += 1
                svc.decide(p.id, False, HUMAN_P)
        return [] if held <= 3 else [f"{held} proposals reached the human against a budget of 3"]
    attempt("refill the budget by getting rejected", b15)

    def b16():
        svc = fresh(); w = []
        for bad in (None, ["note"], 7, "note=x", {"note": math.nan}):
            p = svc.propose(AGENT_P, "add_note", INV, bad)
            svc.execute(p.id, rec(w), AGENT_P)
        rows = sum(1 for r in svc.store.audit_rows() if r["kind"] == "PROPOSAL")
        return w + ([] if rows == 5 else [f"{rows} PROPOSAL rows for 5 malformed proposals"])
    attempt("malformed params — every one denied, none raised, each audited", b16)

    # ── the one this layer cannot see ────────────────────────────────────────────────────────
    svc = fresh(); w = []
    p = svc.propose(AGENT_P, "update_status", INV, {"status": "reminded"})
    svc.decide(p.id, True, Principal("owner", HUMAN))          # minted by the caller
    svc.execute(p.id, rec(w), AGENT_P)
    print(f"  OUT OF SCOPE  mint a HUMAN principal and approve your own proposal  writes={w}  "
          "— identity is the host's (README §Trust boundary); not scored")

    n_ok = sum(SCORED)
    print(f"\n{n_ok}/{len(SCORED)} scored attempts blocked · 1 out of scope, shown")
    return 0 if n_ok == len(SCORED) else 1


if __name__ == "__main__":
    sys.exit(main())
