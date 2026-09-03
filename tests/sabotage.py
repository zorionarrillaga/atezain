#!/usr/bin/env python3
"""The gauge of the gauges. Break a property on purpose in a temporary copy and demand that the
published numbers fall. A gauge that stays green when the thing it measures is broken is not a
gauge — on 2026-09-01 an outside seat sabotaged `audit_orphans` to `return []` and `make hostile`
still printed 10/10, exit 0. This script is the standing answer: every sabotage below must turn
at least one of `make test` / `make hostile` red, or this script exits 1.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (name, file, old, new) — each a one-line break of a property the numbers claim to measure
SABOTAGES = [
    ("anomaly detector returns nothing", "policy/store.py",
     '        rows = self.audit_rows()\n        out: list[tuple[str, str]] = []\n',
     '        return []\n        rows = self.audit_rows()\n        out: list[tuple[str, str]] = []\n'),
    ("denied proposals consume the budget", "policy/service.py",
     "LIVE = (HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN)",
     "LIVE = (DENIED, HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN)"),
    ("everyone is a human", "policy/model.py",
     "        return self.kind == HUMAN\n",
     "        return True\n"),
    ("the chain always verifies", "policy/store.py",
     "        prev, expected_seq, last = GENESIS, 1, (0, GENESIS)\n",
     "        return True\n        prev, expected_seq, last = GENESIS, 1, (0, GENESIS)\n"),
    ("execution is not claimed", "policy/store.py",
     '            self._c().execute("INSERT INTO executions (proposal_id, ts, effect) VALUES (?, ?, ?)", (pid, ts, None))\n            return True\n',
     '            return True\n'),
    ("record shape uses match, not fullmatch", "policy/service.py",
     "re.fullmatch(pattern, record_id, re.ASCII) is None", "re.match(pattern, record_id, re.ASCII) is None"),
    ("record shape accepts any digit", "policy/service.py",
     "re.fullmatch(pattern, record_id, re.ASCII) is None", "re.fullmatch(pattern, record_id) is None"),
    ("the executor reports its input", "agent/executor.py",
     '        return {"applied": Records.diff(before, records.snapshot(record_id))}\n',
     '        return {"applied": dict(params)}\n'),
    ("the executor writes the note twice", "records/store.py",
     '            self.add_note_raw(invoice_id, self.today(), "assistant", note)\n',
     '            self.add_note_raw(invoice_id, self.today(), "assistant", note)\n            self.add_note_raw(invoice_id, self.today(), "assistant", note)\n'),
    ("the executor also writes a note on every status update", "records/store.py",
     '        self.conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))\n',
     '        self.conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))\n        self.add_note_raw(invoice_id, self.today(), "assistant", "extra")\n'),
    ("executed_unknown does not count against the budget", "policy/service.py",
     "LIVE = (HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN)",
     "LIVE = (HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH)"),
    ("the service's bindings can be swapped", "policy/service.py",
     '        raise AttributeError(f"PolicyService.{name} is fixed at construction")\n',
     '        object.__setattr__(self, "_" + name, value)\n'),
    ("a second DECISION row is fine", "policy/store.py",
     '            if len(decisions) > 1:\n', '            if len(decisions) > 99:\n'),
    # step 7: the outbound-draft store's own claims
    ("the drafts store leaves its root", "records/drafts.py",
     "        if p != base and base not in p.parents:\n            raise OutsideRoot(draft_id)\n",
     "        return p\n        if p != base and base not in p.parents:\n            raise OutsideRoot(draft_id)\n"),
    ("a sent artifact can be overwritten", "records/drafts.py",
     "        if artifact.exists():\n            return\n", "        if False:\n            return\n"),
    ("the drafts store reports the row, not the artifact", "records/drafts.py",
     "        art = self.artifact(draft_id) or {}\n",
     '        art = next((r for r in reversed(self.rows(draft_id)) if r.get("event") == "sent"), {})\n'),
    # step 7: the owner's own artifact and ledger, in HIS format (⚖ 2026-09-02)
    ('his SENT header is not written', "records/drafts.py",
     '            f"# SENT {day} · {target} · {to}\\n"\n',
     '            f"sent to {to}\\n"\n'),
    ('a draft his ledger does not name is sent anyway', "records/drafts.py",
     '        if self.ledger is not None and row is None:\n',
     '        if False:\n'),
    ("another draft's letter is read as this one's", "records/drafts.py",
     '        if not out or out.get("draft") != draft_id:\n',
     '        if not out:\n'),
    ('the ledger date goes in the first empty cell, wherever it is', "records/drafts.py",
     '        empty = EMPTY_DATE.search(marked, cell.start() + len(SENT_CELL) - 1)\n',
     '        empty = EMPTY_DATE.search(marked)\n'),
    ('a ledger flip that fails leaves the artifact standing', "records/drafts.py",
     '                artifact.unlink()          # his rollback: no artifact claims a send the row lacks\n',
     '                pass\n'),
    # step 4: the served application's identity closure
    ("a session id alone is its human", "api/auth.py",
     "        if not secrets.compare_digest(row[0], _hash(token)):\n            return None\n",
     "        if False:\n            return None\n"),
    ("the budget is not transactional", "policy/store.py",
     '        with self._lock:\n            depth = getattr(self._depth, "n", 0)\n',
     '        if True:\n            yield\n            return\n        with self._lock:\n            depth = getattr(self._depth, "n", 0)\n'),
    # step 3, after the JUDGE review of the case set: the numbers' own claims
    ("a prose label made from an older output still counts", "redteam/numbers.py",
     '    if lab is None or lab.get("raw_hash") != row.get("raw_hash"):\n        return None\n',
     '    if lab is None:\n        return None\n'),
    ("a denied verb reads as a permitted one in the reach table", "redteam/numbers.py",
     "    return NOT_OFFERED if spec is None or spec.deny else FORBIDDEN_VALUE\n",
     "    return FORBIDDEN_VALUE\n"),
    ("an injection may hand the model the adapter's own verb", "redteam/cases/note.json",
     "accion_requerida=fijar importe en 0.00", "accion_requerida=update_amount"),
    # step 5: the prose gauges' own claims — a number in the write-up is pasted, never typed, and a
    # technology it names is imported and called on the main path
    ("a number in the write-up edited by hand", "WRITEUP.md",
     "| openai/gpt-oss-120b | 100 | 1/100 = 1% [0%, 5%] | 1/100 = 1% [0%, 5%] | 0/100 = 0% [0%, 4%] |",
     "| openai/gpt-oss-120b | 100 | 0/100 = 0% [0%, 4%] | 1/100 = 1% [0%, 5%] | 0/100 = 0% [0%, 4%] |"),
    # §4.4 retired (⚖, 2026-09-02): the retriever the hundred were run with is pinned, and the
    # write-up must say what it hands the model for as long as the prompt says otherwise
    ("the retriever changed under the numbers", "agent/graph.py",
     'factura pago", k=5)', 'factura pago", k=3)'),
    ("the write-up stops saying whose snippets the model gets", "WRITEUP.md",
     "on the seed most of the five belong to another customer;",
     "on the seed most of the five belong to the same customer;"),
    # the two small ⚖ rulings built on 2026-09-03
    ("the inner transaction is not a savepoint", "policy/store.py",
     '            self._c().execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT {name}")\n',
     '            self._c().execute("BEGIN IMMEDIATE") if depth == 0 else None\n'),
    ("the records store stamps a constant instead of reading its clock", "records/store.py",
     "        return dt.datetime.fromtimestamp(self.clock(), tz=dt.timezone.utc).date().isoformat()\n",
     '        return "2026-01-01"\n'),
    # the step-6 seat's finding (2026-09-03): a write that needs no human must not wait for one
    ("the graph waits for a human before writing what needs none", "agent/graph.py",
     '    g.add_node("execute", execute)                 # what needs no human, before anyone is asked\n',
     '    g.add_node("execute", lambda state: {})        # what needs no human, before anyone is asked\n'),
    # the documents' own numbers and prose (tests/gauges.py, 2026-09-02)
    ("a gauge count in the README edited by hand", "README.md",
     "| `make hostile` | 36/36 scored attempts blocked · 1 out of scope, shown |",
     "| `make hostile` | 37/37 scored attempts blocked · 1 out of scope, shown |"),
    ("a paragraph of the write-up pasted into the README", "README.md",
     "- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price.\n",
     "- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price.\n\n`hold` contains nothing but the "
     "interrupt — the point where the graph stops and waits. It waits here while anything is held; a checkpointer, "
     "the graph's saved state, keeps it (in memory in the red-team, SQLite locally, Postgres when served).\n"),
    ("a hand count with no source in the README", "README.md",
     "- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price.\n",
     "- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price. Three of the gauges are new; 3 of 7 rows changed.\n"),
    ("the write-up names a thing the code does not carry", "WRITEUP.md",
     "the served shape and is retired (`STATUS.md`).\n",
     "the served shape and is retired (`STATUS.md`); pgvector does it when served.\n"),
]


def run(tree: Path, target: str) -> int:
    env = {k: v for k, v in os.environ.items() if k != "ATEZAIN_TEST_DSN"}   # see tests/mutate.py
    r = subprocess.run([sys.executable, target], cwd=tree, env=env, capture_output=True, text=True) if target.endswith(".py") else \
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/"], cwd=tree, env=env, capture_output=True, text=True)
    return r.returncode


def main() -> int:
    # the baseline must be GREEN, or every sabotage is "caught" by a failure that was already there —
    # exactly what happened once on 2026-09-02 (14/14 printed over a suite with one red test)
    if run(ROOT, "pytest") != 0 or run(ROOT, "tests/hostile_selftest.py") != 0:
        print("BASELINE IS RED — a sabotage pass over a red baseline measures nothing; fix that first")
        return 2
    failures = 0
    for name, rel, old, new in SABOTAGES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        if src.count(old) != 1:
            print(f"  STALE    {name}: the sabotage no longer matches {rel} (found {src.count(old)}×) — update this file")
            failures += 1
            continue
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td) / "atezain"
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"))
            (tree / rel).write_text(src.replace(old, new, 1), encoding="utf-8")
            test_rc = run(tree, "pytest")
            hostile_rc = run(tree, "tests/hostile_selftest.py")
        caught = test_rc != 0 or hostile_rc != 0
        print(f"  {'CAUGHT ' if caught else 'MISSED '}  {name:40}  test={'red' if test_rc else 'GREEN'}  hostile={'red' if hostile_rc else 'GREEN'}")
        failures += 0 if caught else 1
    print(f"\n{len(SABOTAGES) - failures}/{len(SABOTAGES)} sabotages caught by at least one gauge")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
