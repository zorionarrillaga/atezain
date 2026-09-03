#!/usr/bin/env python3
"""atezain at the command line, over the owner's own outbound drafts (PLAN.md §5, step 7).

    check <draft> passes  →  atezain propose send <draft>      → held: <id>
    the owner pastes the body into his mail client and clicks Enviar HIMSELF
    atezain approve <id> --as human:<who>                      → approved
    atezain record <draft>                                     → executed: the artifact + the row

**This tool never sends anything.** There is no verb here that opens a connection to a mail server.
`send` means *record that this draft went out*; the sending is the owner's hand, and `approve` is
the owner's hand too — an assistant typing it is the failure this whole repo is about.

Identity is the shell's (README §Trust boundary 1): whoever can run this can pass
`--as human:owner`. On the owner's own machine that is the guarantee, and it is stated, not hidden.

Meant to be called from the owner's `bin/venture` by path:
    python3 <atezain>/bin/atezain_cli.py --root <outreach dir> --db <state db> propose send <draft>

`--ledger <PIPELINE.md>` adds his ledger row to what `record` writes; without it the artifact and
this layer's own `pipeline.jsonl` are all that is touched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.executor import make_outreach_executor                                 # noqa: E402
from policy import (AGENT, APPROVED, EXECUTED, HELD, HUMAN, REJECTED, SYSTEM,  # noqa: E402
                    PolicyConfig, PolicyService, Principal, Store)
from records.drafts import Drafts                                                 # noqa: E402

ADAPTER = ROOT / "adapters" / "outreach" / "permissions.toml"
# Neutral defaults on purpose: this file ships in a repository and should not carry one machine's
# layout. Point it at yours with ATEZAIN_OUTREACH_ROOT and ATEZAIN_OUTREACH_DB in your shell — the
# same place the keys live — or with --root and --db.
DEFAULT_ROOT = "./outreach"
DEFAULT_DB = "./var/atezain_outreach.db"
KINDS = {AGENT: AGENT, HUMAN: HUMAN, SYSTEM: SYSTEM}

OK, REFUSED, USAGE = 0, 1, 2


def principal(text: str) -> Principal:
    """`kind:id`, e.g. `human:owner`. The kind is what the layer reads; the id is for the reader."""
    kind, _, who = text.partition(":")
    if kind not in KINDS or not who:
        raise argparse.ArgumentTypeError(f"--as wants kind:id with kind in {sorted(KINDS)}, got {text!r}")
    return Principal(who, kind)


def value(text: str):
    """`k=v`. Only the three JSON words are parsed; everything else stays the string it was, so a
    subject that reads like a number is still a subject."""
    k, _, v = text.partition("=")
    if not k or "=" not in text:
        raise argparse.ArgumentTypeError(f"--param wants k=v, got {text!r}")
    return k, {"true": True, "false": False, "null": None}.get(v, v)


class Cli:
    def __init__(self, a: argparse.Namespace):
        self.drafts = Drafts(a.root, ledger=a.ledger or None)
        self.store = Store(str(Path(a.db).expanduser()))
        self.policy = PolicyService(PolicyConfig.load(a.adapter), self.store)

    # ── reads over the store: the queue is derived from the audit chain, which is the record ──
    def proposals(self) -> list:
        seen, out = set(), []
        for row in self.store.audit_rows():
            pid = row["proposal_id"]
            if row["kind"] != "PROPOSAL" or pid is None or pid in seen:
                continue
            seen.add(pid)
            p = self.store.get_proposal(pid)
            if p is not None:
                out.append(p)
        return out

    def for_draft(self, draft: str, action: str, status: str) -> list:
        return [p for p in self.proposals() if p.record_id == draft and p.action == action and p.status == status]


def line(p) -> str:
    return f"{p.id[:12]}  {p.status:<9} {p.action:<24} {p.record_id}  {json.dumps(p.params, ensure_ascii=False)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="atezain", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=os.environ.get("ATEZAIN_OUTREACH_ROOT", DEFAULT_ROOT))
    ap.add_argument("--db", default=os.environ.get("ATEZAIN_OUTREACH_DB", DEFAULT_DB))
    ap.add_argument("--adapter", default=str(ADAPTER))
    # His ledger, named outright or not at all. Without it a send writes the artifact and this
    # layer's own pipeline row and nothing else; with it, the draft's row in that file is flipped
    # to **SENT** the way `bin/venture record` flips it, and a send whose row is missing refuses.
    ap.add_argument("--ledger", default=os.environ.get("ATEZAIN_OUTREACH_LEDGER", ""),
                    metavar="PIPELINE.md", help="the owner's PIPELINE.md; omitted means no ledger write")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("propose", help="the assistant asks; nothing happens yet")
    p.add_argument("action"); p.add_argument("draft")
    p.add_argument("--param", action="append", type=value, default=[], metavar="k=v")
    p.add_argument("--why", default="", help="the evidence line, recorded in the PROPOSAL row")
    p.add_argument("--as", dest="by", type=principal, default=Principal("assistant", AGENT))

    p = sub.add_parser("queue", help="what is waiting for the owner")
    p.add_argument("--all", action="store_true", help="every proposal, not only the held ones")

    for name, helptext in (("approve", "the owner says yes — after he has clicked Enviar himself"),
                           ("reject", "the owner says no")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("id")
        p.add_argument("--as", dest="by", type=principal, required=True, metavar="human:who")
        p.add_argument("--note", default="")

    p = sub.add_parser("record", help="write the sent artifact for an APPROVED send")
    p.add_argument("draft")
    p.add_argument("--as", dest="by", type=principal, default=Principal("cli", SYSTEM))

    p = sub.add_parser("execute", help="the same, by proposal id")
    p.add_argument("id")
    p.add_argument("--as", dest="by", type=principal, default=Principal("cli", SYSTEM))

    p = sub.add_parser("show", help="what the records say about one draft")
    p.add_argument("draft")

    p = sub.add_parser("audit", help="the chain, its head, and anything anomalous in it")
    p.add_argument("--tail", type=int, default=12)

    sub.add_parser("head", help="the audit head, for publishing out of band on the day card")

    p = sub.add_parser("stop", help="trip the fuse: no proposal is accepted until a human clears it")
    p.add_argument("--reason", default="owner_stop")
    p.add_argument("--as", dest="by", type=principal, default=Principal("owner", SYSTEM))

    p = sub.add_parser("clear", help="clear the fuse — a human only, and never on the day it tripped")
    p.add_argument("--as", dest="by", type=principal, required=True, metavar="human:who")

    a = ap.parse_args(argv)
    c = Cli(a)

    if a.cmd == "propose":
        pr = c.policy.propose(a.by, a.action, a.draft, dict(a.param), evidence=a.why)
        if pr.status == HELD:
            print(f"held: {pr.id}   {a.action} {a.draft} — a human decides this "
                  f"(`atezain approve {pr.id[:12]} --as human:<who>`)")
            return OK
        if pr.status == APPROVED:
            print(f"approved-without-a-human: {pr.id}   {a.action} {a.draft} — "
                  f"the adapter says this one needs no decision; run `atezain execute {pr.id[:12]}`")
            return OK
        print(f"denied: {pr.reason}   {a.action} {a.draft}")
        return REFUSED

    if a.cmd == "queue":
        rows = [p for p in c.proposals() if a.all or p.status == HELD]
        for pr in rows:
            print(line(pr))
        print(f"{len(rows)} proposal(s)" + ("" if a.all else " waiting"))
        return OK

    if a.cmd in ("approve", "reject"):
        matches = [p for p in c.proposals() if p.id.startswith(a.id)]
        if len(matches) != 1:
            print(f"{'no' if not matches else 'more than one'} proposal matches {a.id!r}", file=sys.stderr)
            return REFUSED
        pr = c.policy.decide(matches[0].id, a.cmd == "approve", a.by, note=a.note)
        if pr.status in (APPROVED, REJECTED) and pr.decided_by == a.by.tag:
            print(f"{pr.status}: {pr.id}  by {pr.decided_by}   "
                  f"(identity is your shell — README §Trust boundary 1)")
            return OK
        print(f"refused: {pr.id} is {pr.status}; the decision was not taken (see the audit rows)", file=sys.stderr)
        return REFUSED

    if a.cmd in ("record", "execute"):
        if a.cmd == "record":
            done = c.for_draft(a.draft, "send", EXECUTED)
            if done:
                print(f"refused: {a.draft} was already recorded as sent ({done[-1].id[:12]})", file=sys.stderr)
                return REFUSED
            ready = c.for_draft(a.draft, "send", APPROVED)
            if not ready:
                held = c.for_draft(a.draft, "send", HELD)
                print(f"refused: no approved send for {a.draft}"
                      + (f" — it is held as {held[-1].id[:12]}, waiting for a human" if held else
                         " — propose it first (`atezain propose send <draft> --param to=… --param subject=…`)"),
                      file=sys.stderr)
                return REFUSED
            pid = ready[-1].id
        else:
            matches = [p for p in c.proposals() if p.id.startswith(a.id)]
            if len(matches) != 1:
                print(f"{'no' if not matches else 'more than one'} proposal matches {a.id!r}", file=sys.stderr)
                return REFUSED
            pid = matches[0].id
        pr = c.policy.execute(pid, make_outreach_executor(c.drafts, pid), a.by)
        if pr.status == EXECUTED:
            print(f"executed: {pr.id}  {pr.action} {pr.record_id}")
            return OK
        print(f"not executed: {pr.id} ended {pr.status} — the layer wrote down what it observed", file=sys.stderr)
        return REFUSED

    if a.cmd == "show":
        snap = c.drafts.snapshot(a.draft)
        if snap is None:
            print(f"no such draft under {c.drafts.root}: {a.draft}", file=sys.stderr)
            return REFUSED
        print(json.dumps(snap, ensure_ascii=False, indent=1))
        for r in c.drafts.rows(a.draft):
            print(f"  {r.get('at', '')}  {r.get('event')}  {json.dumps({k: v for k, v in r.items() if k not in ('ts', 'at', 'draft', 'event')}, ensure_ascii=False)}")
        return OK

    if a.cmd == "audit":
        rows = c.store.audit_rows()
        for r in rows[-a.tail:]:
            print(f"{r['seq']:>4}  {r['kind']:<22} {r['principal']:<18} {(r['proposal_id'] or '')[:12]:<12} {r['detail'][:90]}")
        seq, h = c.store.audit_head()
        anomalies = c.store.audit_anomalies()
        print(f"head: seq={seq} hash={h}   verify={c.store.audit_verify()}   rows={len(rows)}")
        for kind, detail in anomalies:
            print(f"ANOMALY  {kind}  {detail}", file=sys.stderr)
        return REFUSED if anomalies or not c.store.audit_verify() else OK

    if a.cmd == "head":
        seq, h = c.store.audit_head()
        print(f"{seq} {h}")
        return OK

    if a.cmd == "stop":
        c.policy.fuse.trip(a.reason, a.by)
        print(f"fuse tripped by {a.by.tag}: {a.reason}. No proposal is accepted until a human clears it, "
              f"and not on the day it tripped.")
        return OK

    if a.cmd == "clear":
        if c.policy.fuse.clear(a.by):
            print(f"fuse cleared by {a.by.tag}")
            return OK
        print("refused: a tripped fuse is cleared by a human, and never on the day it tripped "
              "(the refusal is in the audit chain)", file=sys.stderr)
        return REFUSED

    return USAGE


if __name__ == "__main__":
    raise SystemExit(main())
