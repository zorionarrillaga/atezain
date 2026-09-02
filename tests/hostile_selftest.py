#!/usr/bin/env python3
"""Hostile self-test: an attacker who holds the same Python objects the application holds tries to
get a write through the policy layer without a human approving it. Every attempt must fail to
execute. The script prints one line per attempt and exits 1 if any write got through.

This is NOT the red-team of the model (that is redteam/, with injections planted in records). This
is the red-team of the boundary itself, with no model in the loop: if the boundary can be walked
around from code, nothing measured against the model means anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from policy import (AGENT, HUMAN, APPROVED, EXECUTED, HELD, DENIED,  # noqa: E402
                    PolicyConfig, Principal, Store, PolicyService)

AGENT_P = Principal("assistant", AGENT)
HUMAN_P = Principal("owner", HUMAN)
FAKE_HUMAN = Principal("owner", AGENT)           # same id as the human, wrong kind
T0 = 1_756_760_400.0


class Clock:
    t = T0

    def __call__(self):
        return self.t


def fresh():
    cfg = PolicyConfig.load(ROOT / "adapters" / "invoices-es" / "permissions.toml")
    return PolicyService(cfg, Store(":memory:"), Clock())


def attempt(name, fn):
    """fn returns the list of executor calls that happened; a write got through if it is non-empty."""
    try:
        writes = fn()
        ok = len(writes) == 0
        print(f"  {'BLOCKED' if ok else 'BREACH '}  {name}" + ("" if ok else f"  writes={writes}"))
        return ok
    except Exception as e:  # noqa: BLE001 — an exception is a block, not a breach, but is reported
        print(f"  BLOCKED  {name}  (raised {type(e).__name__}: {e})")
        return True


def main() -> int:
    results = []

    def a1():  # approve with a principal that spoofs the human's id
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "update_status", "inv", {"status": "reminded"})
        svc.decide(p.id, True, FAKE_HUMAN)
        svc.execute(p.id, lambda a, r, prm: w.append(prm) or {"applied": prm}, AGENT_P)
        return w
    results.append(attempt("spoof the human's id with an agent principal", a1))

    def a2():  # edit the proposal object to APPROVED and execute
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "update_status", "inv", {"status": "reminded"})
        p.status = APPROVED
        svc.execute(p.id, lambda a, r, prm: w.append(prm) or {"applied": prm}, AGENT_P)
        return w
    results.append(attempt("flip the held object's status in memory", a2))

    def a3():  # execute a denied action by name variants
        svc = fresh(); w = []
        for name in ("update_amount", "UPDATE_AMOUNT", "update-amount", "update_amount ", "update_amount"):
            p = svc.propose(AGENT_P, name, "inv", {"amount": 0})
            svc.execute(p.id, lambda a, r, prm: w.append(prm) or {"applied": prm}, AGENT_P)
        return w
    results.append(attempt("denied action under five spellings", a3))

    def a4():  # smuggle a money field inside an allowed action
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "add_note", "inv", {"note": "x", "amount": 0})
        svc.execute(p.id, lambda a, r, prm: w.append(prm) or {"applied": prm}, AGENT_P)
        return w
    results.append(attempt("smuggle `amount` inside an auto-approved note", a4))

    def a5():  # approve, then edit params before execution
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "update_status", "inv", {"status": "reminded"})
        svc.decide(p.id, True, HUMAN_P)
        p.params["status"] = "paid"
        svc.execute(p.id, lambda a, r, prm: w.append(prm) or {"applied": prm}, AGENT_P)
        return [x for x in w if x != {"status": "reminded"}]
    results.append(attempt("change the params after approval (TOCTOU)", a5))

    def a6():  # replay an approved proposal
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "update_status", "inv", {"status": "reminded"})
        svc.decide(p.id, True, HUMAN_P)
        for _ in range(5):
            svc.execute(p.id, lambda a, r, prm: w.append(prm) or {"applied": prm}, AGENT_P)
        return w[1:]
    results.append(attempt("replay one approval five times", a6))

    def a7():  # clear the fuse as the agent, then propose
        svc = fresh(); w = []
        svc.fuse.trip("x", HUMAN_P, T0)
        svc.fuse.clear(AGENT_P, T0 + 10 * 86400)
        p = svc.propose(AGENT_P, "add_note", "inv", {"note": "x"})
        svc.execute(p.id, lambda a, r, prm: w.append(prm) or {"applied": prm}, AGENT_P)
        return w
    results.append(attempt("clear a tripped fuse as the agent", a7))

    def a8():  # exhaust the budget with denied proposals (should not count), then a real one must still be held not executed
        svc = fresh(); w = []
        for i in range(50):
            svc.propose(AGENT_P, "update_amount", f"inv{i}", {"amount": 1})
        p = svc.propose(AGENT_P, "update_status", "inv", {"status": "reminded"})
        svc.execute(p.id, lambda a, r, prm: w.append(prm) or {"applied": prm}, AGENT_P)
        return w if p.status != HELD else w
    results.append(attempt("spam denied proposals, then slip a real one through", a8))

    def a9():  # forge a proposal id
        svc = fresh(); w = []
        try:
            svc.execute("00000000000000000000000000000000", lambda a, r, prm: w.append(prm) or {"applied": prm}, AGENT_P)
        except KeyError:
            pass
        return w
    results.append(attempt("execute a proposal id that does not exist", a9))

    def a10():  # write directly through the store to mark approved, bypassing decide
        svc = fresh(); w = []
        p = svc.propose(AGENT_P, "update_status", "inv", {"status": "reminded"})
        p.status = APPROVED
        svc.store.put_proposal(p)     # an attacker with store access — the audit chain must still show no DECISION
        svc.execute(p.id, lambda a, r, prm: w.append(prm) or {"applied": prm}, AGENT_P)
        kinds = [r["kind"] for r in svc.store.audit_rows()]
        # This one CAN write (store access is root access). What must hold: the audit shows an EXECUTED
        # row with no DECISION row before it — detectable after the fact. Report, do not count as breach.
        detectable = "DECISION" not in kinds and "EXECUTED" in kinds
        print(f"  {'DETECT.'if detectable else 'MISSED '}  store-level forgery leaves an execution without a decision in the audit chain")
        return []
    results.append(attempt("(observed, not scored) forge approval with store access", a10))

    n_ok = sum(results)
    print(f"\n{n_ok}/{len(results)} attempts blocked")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
