#!/usr/bin/env python3
"""The gauge of the gauges. Break a property on purpose in a temporary copy and demand that the
published numbers fall. A gauge that stays green when the thing it measures is broken is not a
gauge — on 2026-09-01 an outside seat sabotaged `audit_orphans` to `return []` and `make hostile`
still printed 10/10, exit 0. This script is the standing answer: every sabotage below must turn
at least one of `make test` / `make hostile` red, or this script exits 1.
"""
from __future__ import annotations

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
     '            self.add_note_raw(invoice_id, "now", "assistant", note)\n',
     '            self.add_note_raw(invoice_id, "now", "assistant", note)\n            self.add_note_raw(invoice_id, "now", "assistant", note)\n'),
    ("the executor also writes a note on every status update", "records/store.py",
     '        self.conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))\n',
     '        self.conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))\n        self.add_note_raw(invoice_id, "now", "assistant", "extra")\n'),
    ("executed_unknown does not count against the budget", "policy/service.py",
     "LIVE = (HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN)",
     "LIVE = (HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH)"),
    ("the service's bindings can be swapped", "policy/service.py",
     '        raise AttributeError(f"PolicyService.{name} is fixed at construction")\n',
     '        object.__setattr__(self, "_" + name, value)\n'),
    ("a second DECISION row is fine", "policy/store.py",
     '            if len(decisions) > 1:\n', '            if len(decisions) > 99:\n'),
    ("the budget is not transactional", "policy/store.py",
     '        with self._lock:\n            depth = getattr(self._depth, "n", 0)\n',
     '        if True:\n            yield\n            return\n        with self._lock:\n            depth = getattr(self._depth, "n", 0)\n'),
]


def run(tree: Path, target: str) -> int:
    r = subprocess.run([sys.executable, target], cwd=tree, capture_output=True, text=True) if target.endswith(".py") else \
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/"], cwd=tree, capture_output=True, text=True)
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
